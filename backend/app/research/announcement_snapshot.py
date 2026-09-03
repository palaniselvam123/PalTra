"""Point-in-time fidelity for the NSE corporate-announcement archive.

**The question this exists to answer.** The feasibility probe established that
NSE's announcement API carries a real second-precision dissemination timestamp
(`exchdisstime`), an ISIN on every record, and a unique document id (`seq_id`).
It could not establish one thing: whether a record retrieved today still says
the same thing tomorrow. NSE serves the archive as its *current state*. There is
no `as_of` parameter, and `old_new` / `bflag` / `csvName` are null on every
record observed, so the API exposes no amendment flag of its own. A single
retrieval therefore cannot distinguish an append-only archive from one that
silently rewrites history.

That distinction is not academic. If `exchdisstime` for a June record can be
edited in August, then every event study built on it is measuring a timestamp
the market never saw, and no amount of statistical care downstream repairs it.

**The only honest method is temporal.** Freeze a retrieval, hash it, never touch
it again, retrieve the same window later, and diff. This module is the frozen
side of that: it turns a set of raw API records into a byte-deterministic
artefact, refuses to overwrite one that already exists, and compares two of them
into a *new* artefact rather than updating either.

**Why nothing here touches the network or the research database.** Fidelity
evidence is worthless if the thing producing it can also mutate. Retrieval lives
in `announcement_snapshot_capture`; this module is pure functions over dicts, so
its behaviour is fully pinned by tests. Snapshots are written outside the
repository and outside `research_data/` — they are evidence about a third party,
not project data, and must not become something a later backfill can rewrite.

**Why `seq_id` is the identity and not the version.** It is unique per filing
and stable across the two independent retrieval paths already compared. It is
*not* a version counter: NSE supersedes a filing by publishing a new one with a
new `seq_id`, so "new id" means "new document", never "old document edited".
Conflating the two would report every routine follow-up filing as a fidelity
failure. The taxonomy below keeps them apart.

Nothing in this module reads a price, a return, or an outcome.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import stat
from pathlib import Path

# Every field the API returns is tracked. A fidelity check has no business
# deciding in advance which mutations would be acceptable, so the comparison
# watches all of them and the taxonomy sorts out what a change means.
TRACKED_FIELDS = (
    "an_dt",
    "attFileSize",
    "attchmntFile",
    "attchmntText",
    "bflag",
    "csvName",
    "desc",
    "difference",
    "dt",
    "exchdisstime",
    "fileSize",
    "hasXbrl",
    "old_new",
    "orgid",
    "seq_id",
    "smIndustry",
    "sm_isin",
    "sm_name",
    "sort_date",
    "symbol",
)

IDENTITY_FIELD = "seq_id"

# Which kind of failure a changed field represents. A timestamp edit and a
# headline correction are both "the archive changed", but only one of them
# invalidates an event study, so they are never pooled.
TIMESTAMP_FIELDS = frozenset({"an_dt", "exchdisstime", "sort_date", "dt", "difference"})
IDENTITY_FIELDS = frozenset({"symbol", "sm_isin", "sm_name"})
CATEGORY_FIELDS = frozenset({"desc"})
DOCUMENT_FIELDS = frozenset({"attchmntFile", "fileSize", "attFileSize", "hasXbrl"})
# Headline text is the field previously observed to differ solely because one
# HTTP client collapsed whitespace or decoded HTML entities. It is never
# silently treated as harmless; `is_client_artifact` has to prove it first.
TEXT_FIELDS = frozenset({"attchmntText"})

# Attachment fingerprints, split by whether they actually describe the bytes.
# `nsearchives.nseindia.com` serves from several replicas whose stored mtimes
# differ by up to a couple of seconds, and its ETag is the weak form
# `W/"<bytes>-<mtime_ms>"`, so ETag and Last-Modified move between two
# retrievals of a file that never changed. Treating them as content would
# report a mutation on essentially every document. Length and SHA-256 are the
# only comparators that mean anything here.
DOC_CONTENT_FIELDS = ("doc_length", "doc_sha256")
DOC_ADVISORY_FIELDS = ("doc_etag", "last_modified")

# Section 7 taxonomy. A/B are benign archive growth; C-F are mutations of
# something already published; G is anything the rules cannot place, which is
# reported rather than absorbed.
A_NEW_RECORD = "A_expected_new_record"
B_NEW_FILING_SAME_EVENT = "B_new_filing_same_event"
C_CONTENT_MODIFIED = "C_historical_content_modified"
D_DOCUMENT_REPLACED = "D_document_replaced"
E_TIMESTAMP_MODIFIED = "E_timestamp_modified"
F_IDENTITY_MODIFIED = "F_identity_modified"
G_UNCLEAR = "G_unclear"
CATEGORY_MUTATION = "E_category_mutation"
H_CLIENT_ARTIFACT = "H_client_retrieval_artifact"

# A record whose dissemination time predates the baseline retrieval but which
# was absent from the baseline. Not a mutation of an existing row, but a
# point-in-time hazard in its own right: the archive gained history after the
# fact, so "what was knowable at time T" is not reconstructible from a snapshot
# taken at T. Reported under G, named separately so it cannot hide there.
G_LATE_ARRIVING = "G_late_arriving_historical_record"
G_REMOVED = "G_removed_from_archive"

MUTATION_CLASSES = frozenset(
    {
        C_CONTENT_MODIFIED,
        CATEGORY_MUTATION,
        D_DOCUMENT_REPLACED,
        E_TIMESTAMP_MODIFIED,
        F_IDENTITY_MODIFIED,
    }
)

# Report labels matching the re-check taxonomy. Internal class names stay
# stable so existing tests and artefacts remain readable.
REPORT_LABEL = {
    A_NEW_RECORD: "A_NEW_RECORD",
    G_REMOVED: "B_REMOVED_RECORD",
    E_TIMESTAMP_MODIFIED: "C_TIMESTAMP_MUTATION",
    F_IDENTITY_MODIFIED: "D_IDENTITY_MUTATION",
    CATEGORY_MUTATION: "E_CATEGORY_MUTATION",
    D_DOCUMENT_REPLACED: "F_DOCUMENT_REFERENCE_MUTATION",
    C_CONTENT_MODIFIED: "C_CONTENT_OTHER",
    H_CLIENT_ARTIFACT: "H_CLIENT_RETRIEVAL_ARTIFACT",
    B_NEW_FILING_SAME_EVENT: "J_EXPECTED_NEW_SUPERSEDING_FILING",
    G_LATE_ARRIVING: "K_LATE_ARRIVING_HISTORICAL_RECORD",
    G_UNCLEAR: "L_UNRESOLVED",
}

SNAPSHOT_KIND = "nse_corporate_announcements"
SNAPSHOT_FORMAT_VERSION = 1

# Announcement stamps carry no zone and are IST by convention; `retrieved_at` is
# UTC. Comparing the two without converting would shift every boundary by 5h30m
# and misclassify a whole evening of new filings as late-arriving history.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


# ---- canonicalisation and hashing ---------------------------------------


def _normalise(value):
    """Collapse the API's several spellings of "absent" onto ``None``.

    The endpoint returns JSON ``null`` for an empty field, but the same field
    arrives as the four-character string ``"None"`` when the response has been
    round-tripped through a text tool. Without this, re-reading a snapshot
    through a different path would manufacture a difference in every null
    field and drown a real edit in noise.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text == "" or text in ("None", "null"):
            return None
        return text
    return value


def canonical_record(raw: dict) -> dict:
    """The tracked subset of one API record, normalised and key-ordered.

    Untracked keys are dropped so that a field NSE adds later cannot silently
    change every digest and mask a genuine edit behind a schema change.
    """
    return {field: _normalise(raw.get(field)) for field in TRACKED_FIELDS}


def canonical_bytes(obj) -> bytes:
    """Byte-deterministic JSON: sorted keys, fixed separators, ASCII-escaped.

    ``ensure_ascii`` matters more than it looks. Company names carry rupee
    signs and typographic dashes, and escaping them means the digest does not
    depend on the filesystem or console encoding of whoever ran the capture.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def record_digest(raw: dict) -> str:
    return sha256_hex(canonical_bytes(canonical_record(raw)))


def records_digest(records: list[dict]) -> str:
    """Digest of the record set, independent of retrieval order.

    Sorting by ``seq_id`` before hashing is what makes the digest a statement
    about *content*. The API returns newest-first, and a per-symbol sweep
    returns a different order again, so an order-sensitive digest would report
    a difference every time the query strategy changed while the archive stood
    still.
    """
    canon = sorted((canonical_record(r) for r in records), key=lambda r: str(r[IDENTITY_FIELD]))
    return sha256_hex(canonical_bytes(canon))


# ---- snapshot construction ----------------------------------------------


def build_snapshot(
    records: list[dict],
    queries: list[dict],
    retrieved_at: dt.datetime,
    universe: list[str],
    window: tuple[str, str],
    documents: dict | None = None,
    notes: str = "",
) -> dict:
    """Assemble the artefact. Pure: the caller supplies retrieval facts.

    `queries` carries one entry per HTTP request — url, params, status, byte
    count and response digest — so the snapshot documents how it was obtained
    and not merely what it contains.
    """
    seen: dict[str, dict] = {}
    duplicate_ids = 0
    for raw in records:
        rec = canonical_record(raw)
        key = str(rec[IDENTITY_FIELD])
        if key in seen:
            duplicate_ids += 1
            # Identical re-delivery across overlapping queries is expected and
            # harmless; a genuine conflict under one id is not, and must not be
            # silently resolved by last-write-wins.
            if seen[key] != rec:
                raise ValueError(f"conflicting records share {IDENTITY_FIELD}={key}")
            continue
        seen[key] = rec

    ordered = [seen[k] for k in sorted(seen)]
    return {
        "kind": SNAPSHOT_KIND,
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "retrieved_at": retrieved_at.astimezone(dt.timezone.utc).isoformat(),
        "window": {"start": window[0], "end": window[1]},
        "universe_size": len(universe),
        "universe": sorted(universe),
        "record_count": len(ordered),
        "duplicate_deliveries": duplicate_ids,
        "records_sha256": records_digest(ordered),
        "tracked_fields": list(TRACKED_FIELDS),
        "queries": queries,
        "documents": documents or {},
        "notes": notes,
        "records": ordered,
    }


def write_once(path: Path | str, payload: dict) -> dict:
    """Write a JSON artefact exactly once, then make the file read-only.

    Refuses to touch an existing path. The whole method depends on an artefact
    being the thing it was when it was written, so "overwrite" is never the
    right operation — a later retrieval produces a new file and a comparison
    artefact, never an update in place.
    """
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"artefact already exists and must not be overwritten: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = canonical_bytes(payload)
    # Exclusive create closes the gap between the existence check and the write.
    with open(path, "xb") as fh:
        fh.write(body)
    os.chmod(path, stat.S_IREAD)
    return {"path": str(path), "bytes": len(body), "file_sha256": sha256_hex(body)}


def write_snapshot(path: Path | str, snapshot: dict) -> dict:
    """Freeze a retrieval, with the snapshot-specific provenance echoed back."""
    written = write_once(path, snapshot)
    written.update(
        {
            "records_sha256": snapshot["records_sha256"],
            "record_count": snapshot["record_count"],
            "retrieved_at": snapshot["retrieved_at"],
        }
    )
    return written


def read_snapshot(path: Path | str) -> dict:
    data = Path(path).read_bytes()
    snapshot = json.loads(data.decode("ascii"))
    recomputed = records_digest(snapshot["records"])
    if recomputed != snapshot["records_sha256"]:
        raise ValueError(
            f"snapshot {path} failed its own integrity check: "
            f"stored {snapshot['records_sha256']}, recomputed {recomputed}"
        )
    return snapshot


def file_fingerprint(path: Path | str) -> dict:
    data = Path(path).read_bytes()
    return {"path": str(path), "bytes": len(data), "file_sha256": sha256_hex(data)}


# ---- structural point-in-time diagnostic --------------------------------


def sequence_monotonicity(records: list[dict]) -> dict:
    """Does `seq_id` order agree with dissemination order?

    This is the one point-in-time test a *single* snapshot can support. In an
    append-only archive where ids are allocated at publication, sorting by
    `seq_id` should reproduce chronological order. A record carrying a low id
    and a late timestamp was either backdated or allocated its id somewhere
    other than at publication — both of which mean the id cannot be read as
    "when this became public", and the second reading is a genuine hazard.

    A violation is not proof of tampering; NSE may run several allocation pools
    (per filing category, say). It does establish that `seq_id` ordering cannot
    substitute for the timestamp, which is worth knowing before anyone leans
    on it.
    """
    pairs = []
    for rec in records:
        sid, stamp = rec.get("seq_id"), rec.get("exchdisstime")
        parsed = parse_nse_datetime(stamp) if stamp else None
        if sid is None or parsed is None:
            continue
        try:
            pairs.append((int(sid), parsed, rec))
        except (TypeError, ValueError):
            continue
    pairs.sort(key=lambda p: p[0])

    violations = []
    worst = dt.timedelta(0)
    for prev, cur in zip(pairs, pairs[1:]):
        if cur[1] < prev[1]:
            gap = prev[1] - cur[1]
            worst = max(worst, gap)
            violations.append(
                {
                    "seq_id": cur[2].get("seq_id"),
                    "exchdisstime": cur[2].get("exchdisstime"),
                    "desc": cur[2].get("desc"),
                    "symbol": cur[2].get("symbol"),
                    "behind_by_seconds": int(gap.total_seconds()),
                    "previous_seq_id": prev[2].get("seq_id"),
                    "previous_exchdisstime": prev[2].get("exchdisstime"),
                }
            )
    return {
        "comparable_records": len(pairs),
        "violations": len(violations),
        "violation_rate": (len(violations) / len(pairs)) if pairs else 0.0,
        "max_backwards_gap_seconds": int(worst.total_seconds()),
        "examples": violations[:10],
        "categories": _count(v["desc"] for v in violations),
    }


def parse_nse_datetime(text: str) -> dt.datetime | None:
    """`07-Jun-2026 22:54:43` -> naive datetime. IST by convention.

    The payload declares no timezone. The probe established IST empirically
    from the hour-of-day distribution, so the value is left naive rather than
    stamped with a zone the source never asserted.
    """
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(str(text).strip(), fmt)
        except (ValueError, AttributeError, TypeError):
            continue
    return None


def _count(values) -> dict:
    out: dict[str, int] = {}
    for v in values:
        key = str(v)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# ---- comparison ----------------------------------------------------------


def classify_change(changed_fields: set[str]) -> str:
    """Most severe applicable class for a record that exists in both snapshots.

    Ordered by what each implies for research. An identity change means the
    event may now belong to a different company; a timestamp change means the
    event moved in time; a category change means the event was re-typed; a
    document change means the content behind a stable id was swapped.
    Headline-only text is ordinary content unless `is_client_artifact` proves
    otherwise — that proof happens after this function returns.
    """
    if changed_fields & IDENTITY_FIELDS:
        return F_IDENTITY_MODIFIED
    if changed_fields & TIMESTAMP_FIELDS:
        return E_TIMESTAMP_MODIFIED
    if changed_fields & CATEGORY_FIELDS:
        return CATEGORY_MUTATION
    if changed_fields & DOCUMENT_FIELDS:
        return D_DOCUMENT_REPLACED
    return C_CONTENT_MODIFIED


def _normalise_text(value) -> str:
    """Collapse whitespace and decode HTML entities for client-artifact tests.

    Only used to *prove* a difference is an encoding artefact. A residual
    difference after this normalisation is a genuine content change.
    """
    if value is None:
        return ""
    return " ".join(html.unescape(str(value)).split())


def is_client_artifact(before: dict, after: dict, changed_fields: set[str]) -> bool:
    """True only when every changed field is text and the texts match after
    whitespace collapse and HTML-entity decoding.

    Proven on the earlier cross-path retrieval: one HTTP client collapsed
    double spaces and decoded `&#64258;` to a ligature. That is not an NSE
    edit. Anything that still differs after normalisation is not attributed
    here — it stays a content mutation.
    """
    if not changed_fields or not changed_fields <= TEXT_FIELDS:
        return False
    return all(_normalise_text(before.get(f)) == _normalise_text(after.get(f))
               for f in changed_fields)


def _event_key(rec: dict) -> tuple:
    """Symbol + category + calendar day: the coarse identity of an *event*.

    Used only to tell a superseding filing (class B) from a genuinely new one
    (class A). Deliberately blunt — it exists to avoid over-reporting, not to
    deduplicate an event population.
    """
    stamp = parse_nse_datetime(rec.get("exchdisstime") or "")
    return (rec.get("symbol"), rec.get("desc"), stamp.date().isoformat() if stamp else None)


def compare_snapshots(baseline: dict, current: dict) -> dict:
    """Diff two snapshots into a report. Neither input is modified.

    Returns the full A-G breakdown plus the verdict inputs. The caller decides
    GO / CONDITIONAL GO / NO-GO; this function does not, because the threshold
    is a research-policy question and not a property of the data.
    """
    base_by_id = {str(r[IDENTITY_FIELD]): r for r in baseline["records"]}
    cur_by_id = {str(r[IDENTITY_FIELD]): r for r in current["records"]}
    base_retrieved = (
        dt.datetime.fromisoformat(baseline["retrieved_at"]).astimezone(IST).replace(tzinfo=None)
    )
    base_events = {_event_key(r) for r in baseline["records"]}

    changed: list[dict] = []
    for key in sorted(set(base_by_id) & set(cur_by_id)):
        b, c = base_by_id[key], cur_by_id[key]
        fields = {f for f in TRACKED_FIELDS if b.get(f) != c.get(f)}
        if not fields:
            continue
        cls = classify_change(fields)
        if is_client_artifact(b, c, fields):
            cls = H_CLIENT_ARTIFACT
        changed.append(
            {
                "seq_id": key,
                "class": cls,
                "report_label": REPORT_LABEL.get(cls, cls),
                "changed_fields": sorted(fields),
                "before": {f: b.get(f) for f in sorted(fields)},
                "after": {f: c.get(f) for f in sorted(fields)},
                "symbol": b.get("symbol"),
                "exchdisstime": b.get("exchdisstime"),
            }
        )

    added: list[dict] = []
    for key in sorted(set(cur_by_id) - set(base_by_id)):
        rec = cur_by_id[key]
        stamp = parse_nse_datetime(rec.get("exchdisstime") or "")
        if stamp is not None and stamp > base_retrieved:
            cls = A_NEW_RECORD
        elif _event_key(rec) in base_events:
            cls = B_NEW_FILING_SAME_EVENT
        elif stamp is not None:
            cls = G_LATE_ARRIVING
        else:
            cls = G_UNCLEAR
        added.append(
            {
                "seq_id": key,
                "class": cls,
                "report_label": REPORT_LABEL.get(cls, cls),
                "symbol": rec.get("symbol"),
                "desc": rec.get("desc"),
                "exchdisstime": rec.get("exchdisstime"),
            }
        )

    removed = [
        {
            "seq_id": key,
            "class": G_REMOVED,
            "report_label": REPORT_LABEL[G_REMOVED],
            "symbol": base_by_id[key].get("symbol"),
            "desc": base_by_id[key].get("desc"),
            "exchdisstime": base_by_id[key].get("exchdisstime"),
        }
        for key in sorted(set(base_by_id) - set(cur_by_id))
    ]

    by_class = _count(item["class"] for item in changed + added + removed)
    mutations = sum(1 for item in changed if item["class"] in MUTATION_CLASSES)
    return {
        "kind": "nse_corporate_announcements_comparison",
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "compared_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "baseline": {
            "retrieved_at": baseline["retrieved_at"],
            "record_count": baseline["record_count"],
            "records_sha256": baseline["records_sha256"],
        },
        "current": {
            "retrieved_at": current["retrieved_at"],
            "record_count": current["record_count"],
            "records_sha256": current["records_sha256"],
        },
        "elapsed_seconds": int(
            (
                dt.datetime.fromisoformat(current["retrieved_at"])
                - dt.datetime.fromisoformat(baseline["retrieved_at"])
            ).total_seconds()
        ),
        "identical": not (changed or added or removed),
        "counts": {
            "unchanged": len(set(base_by_id) & set(cur_by_id)) - len(changed),
            "changed": len(changed),
            "added": len(added),
            "removed": len(removed),
            "historical_mutations": mutations,
        },
        "by_class": by_class,
        "changed_records": changed,
        "added_records": added,
        "removed_records": removed,
    }


CLASSIFICATION_KEYS = (
    "identical",
    "counts",
    "by_class",
    "changed_records",
    "added_records",
    "removed_records",
    "elapsed_seconds",
)


def classification_payload(report: dict) -> dict:
    """The part of a comparison that must be deterministic.

    `compared_at` is wall-clock and is excluded: two runs of the same inputs
    must classify the same differences even if they happen a second apart.
    """
    return {k: report[k] for k in CLASSIFICATION_KEYS}


def classification_digest(report: dict) -> str:
    return sha256_hex(canonical_bytes(classification_payload(report)))


def compare_documents(baseline: dict, current: dict) -> dict:
    """Did any fingerprinted attachment change behind a stable `seq_id`?

    Only documents sampled in *both* snapshots can be compared; the sample is
    deterministic by `seq_id`, so in practice that is all of them. Advisory
    metadata is reported but never counted as a change — see the note on
    `DOC_ADVISORY_FIELDS` for why it moves on its own.
    """
    b = (baseline.get("documents") or {}).get("entries", {})
    c = (current.get("documents") or {}).get("entries", {})
    shared = sorted(set(b) & set(c))

    content_changes, advisory_changes = [], []
    for key in shared:
        for field in DOC_CONTENT_FIELDS:
            if b[key].get(field) is not None and b[key].get(field) != c[key].get(field):
                content_changes.append(
                    {"seq_id": key, "field": field,
                     "before": b[key].get(field), "after": c[key].get(field)}
                )
        for field in DOC_ADVISORY_FIELDS:
            if b[key].get(field) is not None and b[key].get(field) != c[key].get(field):
                advisory_changes.append(
                    {"seq_id": key, "field": field,
                     "before": b[key].get(field), "after": c[key].get(field)}
                )

    return {
        "compared": len(shared),
        "content_hashes_compared": sum(
            1 for k in shared if b[k].get("doc_sha256") and c[k].get("doc_sha256")
        ),
        "content_changed": len(content_changes),
        "content_changes": content_changes,
        "advisory_changed": len(advisory_changes),
        "advisory_changes": advisory_changes[:20],
        "advisory_note": (
            "ETag and Last-Modified drift between replicas of an unchanged file and "
            "are not evidence of mutation. Only doc_length and doc_sha256 are."
        ),
    }


def write_comparison(path: Path | str, report: dict) -> dict:
    """Comparisons are artefacts too: write-once, read-only, never in place."""
    written = write_once(path, report)
    written.update({"identical": report["identical"], "counts": report["counts"]})
    return written


__all__ = [
    "A_NEW_RECORD",
    "B_NEW_FILING_SAME_EVENT",
    "C_CONTENT_MODIFIED",
    "CATEGORY_FIELDS",
    "CATEGORY_MUTATION",
    "H_CLIENT_ARTIFACT",
    "REPORT_LABEL",
    "DOC_ADVISORY_FIELDS",
    "DOC_CONTENT_FIELDS",
    "DOCUMENT_FIELDS",
    "D_DOCUMENT_REPLACED",
    "E_TIMESTAMP_MODIFIED",
    "F_IDENTITY_MODIFIED",
    "G_LATE_ARRIVING",
    "G_REMOVED",
    "G_UNCLEAR",
    "IDENTITY_FIELD",
    "IDENTITY_FIELDS",
    "MUTATION_CLASSES",
    "SNAPSHOT_KIND",
    "TIMESTAMP_FIELDS",
    "TRACKED_FIELDS",
    "build_snapshot",
    "canonical_bytes",
    "canonical_record",
    "classification_digest",
    "classification_payload",
    "classify_change",
    "is_client_artifact",
    "compare_documents",
    "compare_snapshots",
    "file_fingerprint",
    "parse_nse_datetime",
    "record_digest",
    "records_digest",
    "read_snapshot",
    "sequence_monotonicity",
    "sha256_hex",
    "write_comparison",
    "write_once",
    "write_snapshot",
]
