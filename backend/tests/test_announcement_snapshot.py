"""Point-in-time fidelity tooling for the NSE announcement archive.

These pin the properties the fidelity argument rests on: a digest that depends
on content and nothing else, a baseline that cannot be rewritten, a record
identity that survives re-retrieval, and a comparison that separates archive
growth from archive mutation. If any of these slip, a later "no changes
detected" result would mean nothing.

Nothing here touches the network, the research database, or any price.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import stat

import pytest

from app.research.announcement_snapshot import (
    A_NEW_RECORD, B_NEW_FILING_SAME_EVENT, C_CONTENT_MODIFIED,
    CATEGORY_MUTATION, D_DOCUMENT_REPLACED, E_TIMESTAMP_MODIFIED,
    F_IDENTITY_MODIFIED, G_LATE_ARRIVING, G_REMOVED, H_CLIENT_ARTIFACT,
    TRACKED_FIELDS, build_snapshot, canonical_bytes, canonical_record,
    classification_digest, compare_documents, compare_snapshots, file_fingerprint,
    is_client_artifact, parse_nse_datetime, read_snapshot, record_digest,
    records_digest, sequence_monotonicity, write_comparison, write_snapshot,
)

RESEARCH = pathlib.Path(__file__).resolve().parents[1] / "app" / "research"
UTC = dt.timezone.utc


def _rec(seq_id, symbol="TATASTEEL", stamp="04-Jun-2026 10:28:25", desc="General Updates", **over):
    base = {
        "an_dt": stamp,
        "attFileSize": "847.10 KB",
        "attchmntFile": f"https://nsearchives.nseindia.com/corporate/{seq_id}.pdf",
        "attchmntText": "Company has informed the Exchange about something",
        "bflag": None,
        "csvName": None,
        "desc": desc,
        "difference": "00:00:01",
        "dt": "04062026102824",
        "exchdisstime": stamp,
        "fileSize": "847.10 KB",
        "hasXbrl": True,
        "old_new": None,
        "orgid": None,
        "seq_id": str(seq_id),
        "smIndustry": "Steel And Steel Products",
        "sm_isin": "INE081A01012",
        "sm_name": "Tata Steel Limited",
        "sort_date": "2026-06-04 10:28:24",
        "symbol": symbol,
    }
    base.update(over)
    return base


def _snap(records, retrieved="2026-08-31T09:00:00+00:00", **over):
    snap = build_snapshot(
        records=records,
        queries=[{"url": "https://example.invalid", "params": {}, "status": 200}],
        retrieved_at=dt.datetime.fromisoformat(retrieved),
        universe=["TATASTEEL", "RELIANCE"],
        window=("2026-06-01", "2026-07-23"),
    )
    snap.update(over)
    return snap


# ---- deterministic hashing ----------------------------------------------


def test_digest_ignores_key_order_and_untracked_fields():
    a = _rec(1)
    b = {k: a[k] for k in reversed(list(a))}
    b["someFieldNseAddedLater"] = "noise"
    assert record_digest(a) == record_digest(b)


def test_digest_treats_null_spellings_as_identical():
    """A JSON null and the string "None" are the same absent field.

    Round-tripping a response through a text tool turns one into the other; if
    the digest disagreed, every null field would read as an edit.
    """
    assert record_digest(_rec(1, bflag=None)) == record_digest(_rec(1, bflag="None"))
    assert record_digest(_rec(1, orgid=None)) == record_digest(_rec(1, orgid="  "))


def test_digest_changes_when_a_tracked_field_changes():
    for field, value in [
        ("exchdisstime", "04-Jun-2026 10:28:26"),
        ("sm_isin", "INE002A01018"),
        ("symbol", "RELIANCE"),
        ("desc", "Acquisition"),
        ("attchmntFile", "https://nsearchives.nseindia.com/corporate/other.pdf"),
    ]:
        assert record_digest(_rec(1)) != record_digest(_rec(1, **{field: value})), field


def test_records_digest_is_order_independent():
    recs = [_rec(3), _rec(1), _rec(2)]
    assert records_digest(recs) == records_digest(list(reversed(recs)))


def test_canonical_bytes_are_ascii_and_stable():
    """Company names carry non-ASCII; the digest must not depend on encoding."""
    payload = canonical_bytes(canonical_record(_rec(1, sm_name="Bajaj \u20b9 Finance \u2014 Ltd")))
    payload.decode("ascii")  # raises if a raw non-ASCII byte leaked through
    assert payload == canonical_bytes(canonical_record(_rec(1, sm_name="Bajaj \u20b9 Finance \u2014 Ltd")))


def test_canonical_record_keeps_exactly_the_tracked_fields():
    assert set(canonical_record(_rec(1))) == set(TRACKED_FIELDS)


# ---- stable record identity ---------------------------------------------


def test_identical_duplicate_deliveries_collapse():
    """Overlapping queries re-deliver the same filing; that is not a conflict."""
    snap = _snap([_rec(1), _rec(1), _rec(2)])
    assert snap["record_count"] == 2
    assert snap["duplicate_deliveries"] == 1


def test_conflicting_records_under_one_seq_id_raise():
    with pytest.raises(ValueError, match="conflicting records"):
        _snap([_rec(1), _rec(1, exchdisstime="04-Jun-2026 11:00:00")])


def test_snapshot_records_are_sorted_by_identity():
    snap = _snap([_rec(30), _rec(10), _rec(20)])
    assert [r["seq_id"] for r in snap["records"]] == ["10", "20", "30"]


# ---- immutability and no accidental overwrite ---------------------------


def test_baseline_is_write_once_and_read_only(tmp_path):
    path = tmp_path / "baseline.json"
    written = write_snapshot(path, _snap([_rec(1)]))

    assert written["record_count"] == 1
    assert len(written["file_sha256"]) == 64
    assert not (os.stat(path).st_mode & stat.S_IWRITE), "baseline must be read-only"

    with pytest.raises(FileExistsError):
        write_snapshot(path, _snap([_rec(2)]))
    with pytest.raises(PermissionError):
        open(path, "wb").close()

    # The refused second write must not have altered the first.
    assert [r["seq_id"] for r in read_snapshot(path)["records"]] == ["1"]
    os.chmod(path, stat.S_IWRITE)  # so tmp_path teardown can remove it


def test_read_snapshot_rejects_a_tampered_file(tmp_path):
    path = tmp_path / "baseline.json"
    write_snapshot(path, _snap([_rec(1)]))
    os.chmod(path, stat.S_IWRITE)

    snap = json.loads(path.read_bytes())
    snap["records"][0]["exchdisstime"] = "04-Jun-2026 23:59:59"
    path.write_bytes(canonical_bytes(snap))

    with pytest.raises(ValueError, match="integrity check"):
        read_snapshot(path)


def test_comparison_writes_a_new_artefact_and_cannot_update_the_baseline(tmp_path):
    """A comparison is a new file. It is not an in-place edit of the baseline."""
    baseline_path = tmp_path / "baseline.json"
    written = write_snapshot(baseline_path, _snap([_rec(1)]))
    digest_before = written["file_sha256"]

    report = compare_snapshots(_snap([_rec(1)]), _snap([_rec(1), _rec(2)]))
    comparison_path = tmp_path / "comparison.json"
    write_comparison(comparison_path, report)

    assert comparison_path.exists()
    assert comparison_path.resolve() != baseline_path.resolve()
    with pytest.raises(FileExistsError):
        write_comparison(comparison_path, report)
    with pytest.raises(FileExistsError):
        write_snapshot(baseline_path, _snap([_rec(99)]))
    assert file_fingerprint(baseline_path)["file_sha256"] == digest_before
    os.chmod(baseline_path, stat.S_IWRITE)
    os.chmod(comparison_path, stat.S_IWRITE)


def test_snapshot_round_trips_byte_for_byte(tmp_path):
    snap = _snap([_rec(1), _rec(2)])
    path = tmp_path / "b.json"
    write_snapshot(path, snap)
    reloaded = read_snapshot(path)
    assert reloaded["records_sha256"] == snap["records_sha256"]
    assert reloaded["records"] == snap["records"]
    os.chmod(path, stat.S_IWRITE)


# ---- change detection ----------------------------------------------------


def test_no_change_reports_identical():
    report = compare_snapshots(_snap([_rec(1), _rec(2)]), _snap([_rec(1), _rec(2)]))
    assert report["identical"] is True
    assert report["counts"]["changed"] == 0
    assert report["counts"]["historical_mutations"] == 0


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("exchdisstime", "04-Jun-2026 14:00:00", E_TIMESTAMP_MODIFIED),
        ("an_dt", "04-Jun-2026 14:00:00", E_TIMESTAMP_MODIFIED),
        ("sm_isin", "INE002A01018", F_IDENTITY_MODIFIED),
        ("symbol", "RELIANCE", F_IDENTITY_MODIFIED),
        ("attchmntFile", "https://nsearchives.nseindia.com/corporate/swapped.pdf", D_DOCUMENT_REPLACED),
        ("desc", "Acquisition", CATEGORY_MUTATION),
        ("attchmntText", "different headline", C_CONTENT_MODIFIED),
    ],
)
def test_each_mutation_class_is_detected(field, value, expected):
    report = compare_snapshots(_snap([_rec(1)]), _snap([_rec(1, **{field: value})]))
    assert report["identical"] is False
    assert report["counts"]["historical_mutations"] == 1
    change = report["changed_records"][0]
    assert change["class"] == expected
    assert field in change["changed_fields"]
    assert change["before"][field] != change["after"][field]


def test_identity_change_outranks_a_simultaneous_timestamp_change():
    """Worst applicable class wins: a record that moved company matters most."""
    report = compare_snapshots(
        _snap([_rec(1)]),
        _snap([_rec(1, symbol="RELIANCE", exchdisstime="04-Jun-2026 14:00:00")]),
    )
    assert report["changed_records"][0]["class"] == F_IDENTITY_MODIFIED


def test_new_record_after_baseline_is_benign_growth():
    report = compare_snapshots(
        _snap([_rec(1)], retrieved="2026-06-10T09:00:00+00:00"),
        _snap([_rec(1), _rec(99, stamp="20-Jul-2026 10:00:00", desc="Acquisition")]),
    )
    assert report["counts"]["historical_mutations"] == 0
    assert report["added_records"][0]["class"] == A_NEW_RECORD


def test_superseding_filing_for_a_known_event_is_class_b():
    """A new seq_id for the same symbol, category and day supersedes; it is not
    evidence that the earlier record was edited."""
    report = compare_snapshots(
        _snap([_rec(1)], retrieved="2026-08-31T09:00:00+00:00"),
        _snap([_rec(1), _rec(2)]),
    )
    assert report["added_records"][0]["class"] == B_NEW_FILING_SAME_EVENT


def test_late_arriving_historical_record_is_flagged():
    """A record disseminated before the baseline but absent from it means the
    archive gained history after the fact — a point-in-time hazard."""
    report = compare_snapshots(
        _snap([_rec(1)], retrieved="2026-08-31T09:00:00+00:00"),
        _snap([_rec(1), _rec(500, symbol="RELIANCE", stamp="02-Jun-2026 11:00:00", desc="Dividend")]),
    )
    assert report["added_records"][0]["class"] == G_LATE_ARRIVING
    assert report["added_records"][0]["report_label"] == "K_LATE_ARRIVING_HISTORICAL_RECORD"


def test_removed_record_is_reported_not_ignored():
    report = compare_snapshots(_snap([_rec(1), _rec(2)]), _snap([_rec(1)]))
    assert report["counts"]["removed"] == 1
    assert report["removed_records"][0]["class"] == G_REMOVED
    assert report["removed_records"][0]["report_label"] == "B_REMOVED_RECORD"


def test_classification_is_deterministic_across_repeated_calls():
    """Wall-clock `compared_at` may move; the verdict must not."""
    base = _snap([_rec(1), _rec(2)])
    cur = _snap([_rec(1, desc="Acquisition"), _rec(2),
                 _rec(99, stamp="02-Jun-2026 11:00:00", desc="Dividend", symbol="RELIANCE")])
    a, b = compare_snapshots(base, cur), compare_snapshots(base, cur)
    assert classification_digest(a) == classification_digest(b)
    assert a["counts"] == b["counts"]
    assert a["changed_records"] == b["changed_records"]
    assert a["added_records"] == b["added_records"]


def test_comparison_does_not_mutate_either_input():
    base, cur = _snap([_rec(1)]), _snap([_rec(1, desc="Acquisition")])
    before = (canonical_bytes(base), canonical_bytes(cur))
    compare_snapshots(base, cur)
    assert (canonical_bytes(base), canonical_bytes(cur)) == before


def test_category_change_outranks_headline_text():
    report = compare_snapshots(
        _snap([_rec(1)]),
        _snap([_rec(1, desc="Acquisition", attchmntText="different headline")]),
    )
    assert report["changed_records"][0]["class"] == CATEGORY_MUTATION
    assert report["changed_records"][0]["report_label"] == "E_CATEGORY_MUTATION"


def test_whitespace_only_headline_is_a_client_artifact_not_a_mutation():
    """Double-space collapse was observed in a different HTTP client.
    Same seq_id, same timestamps: not an NSE edit."""
    before = _rec(1, attchmntText="Company has informed the Exchange about something")
    after = _rec(1, attchmntText="Company has  informed the Exchange about something")
    assert is_client_artifact(
        {"attchmntText": before["attchmntText"]},
        {"attchmntText": after["attchmntText"]},
        {"attchmntText"},
    )
    report = compare_snapshots(_snap([before]), _snap([after]))
    assert report["changed_records"][0]["class"] == H_CLIENT_ARTIFACT
    assert report["counts"]["historical_mutations"] == 0


def test_html_entity_vs_ligature_is_a_client_artifact():
    before = _rec(1, attchmntText="temporarilydiscontinues &#64258;ights")
    after = _rec(1, attchmntText="temporarilydiscontinues \ufb02ights")
    report = compare_snapshots(_snap([before]), _snap([after]))
    assert report["changed_records"][0]["class"] == H_CLIENT_ARTIFACT
    assert report["counts"]["historical_mutations"] == 0


def test_real_headline_edit_is_not_a_client_artifact():
    report = compare_snapshots(
        _snap([_rec(1, attchmntText="Allotment of 100 Shares")]),
        _snap([_rec(1, attchmntText="Allotment of 200 Shares")]),
    )
    assert report["changed_records"][0]["class"] == C_CONTENT_MODIFIED
    assert report["counts"]["historical_mutations"] == 1


# ---- attachment comparison -----------------------------------------------


def _with_docs(entries):
    snap = _snap([_rec(1)])
    snap["documents"] = {"entries": entries}
    return snap


def test_etag_and_last_modified_drift_is_not_document_mutation():
    """NSE's archive serves from replicas with mtimes a second or two apart,
    and its ETag embeds that mtime. Counting it as a change would report a
    mutation on nearly every attachment."""
    before = {"1": {"doc_length": "199623", "doc_sha256": "a" * 64,
                    "doc_etag": 'W/"199623-1780331170424"',
                    "last_modified": "Tue, 02 Jun 2026 15:29:59 GMT"}}
    after = {"1": {"doc_length": "199623", "doc_sha256": "a" * 64,
                   "doc_etag": 'W/"199623-1780331170892"',
                   "last_modified": "Tue, 02 Jun 2026 15:30:00 GMT"}}
    result = compare_documents(_with_docs(before), _with_docs(after))
    assert result["content_changed"] == 0
    assert result["advisory_changed"] == 2
    assert result["content_hashes_compared"] == 1


def test_document_content_change_is_reported():
    before = {"1": {"doc_length": "199623", "doc_sha256": "a" * 64}}
    after = {"1": {"doc_length": "200000", "doc_sha256": "b" * 64}}
    result = compare_documents(_with_docs(before), _with_docs(after))
    assert result["content_changed"] == 2
    assert {c["field"] for c in result["content_changes"]} == {"doc_length", "doc_sha256"}


def test_documents_absent_from_one_snapshot_are_not_compared():
    result = compare_documents(_with_docs({"1": {"doc_sha256": "a" * 64}}), _with_docs({}))
    assert result["compared"] == 0
    assert result["content_changed"] == 0


# ---- structural diagnostic ----------------------------------------------


def test_sequence_monotonicity_flags_a_backwards_record():
    ok = [
        _rec(100, stamp="01-Jun-2026 10:00:00"),
        _rec(200, stamp="02-Jun-2026 10:00:00"),
        _rec(300, stamp="03-Jun-2026 10:00:00"),
    ]
    assert sequence_monotonicity([canonical_record(r) for r in ok])["violations"] == 0

    backwards = ok + [_rec(250, stamp="01-Jun-2026 09:00:00", desc="Credit Rating")]
    result = sequence_monotonicity([canonical_record(r) for r in backwards])
    assert result["violations"] >= 1
    assert result["max_backwards_gap_seconds"] > 0


def test_parse_nse_datetime_handles_both_observed_formats():
    assert parse_nse_datetime("07-Jun-2026 22:54:43") == dt.datetime(2026, 6, 7, 22, 54, 43)
    assert parse_nse_datetime("2026-06-07 22:54:43") == dt.datetime(2026, 6, 7, 22, 54, 43)
    assert parse_nse_datetime("not a date") is None


# ---- no secret leakage ---------------------------------------------------


def test_snapshot_payload_carries_no_credentials(tmp_path):
    """The artefact records how it was fetched, never what authenticated it."""
    snap = _snap([_rec(1)])
    snap["queries"] = [
        {"url": "https://www.nseindia.com/api/corporate-announcements",
         "params": {"index": "equities", "symbol": "TATASTEEL"},
         "status": 200, "response_sha256": "ab" * 32}
    ]
    path = tmp_path / "b.json"
    write_snapshot(path, snap)
    blob = path.read_bytes().decode("ascii").lower()
    os.chmod(path, stat.S_IWRITE)

    for banned in ("cookie", "set-cookie", "authorization", "password", "secret",
                   "api_key", "apikey", "access_token", "bearer", "groww"):
        assert banned not in blob, f"snapshot leaked {banned!r}"


def test_env_values_do_not_appear_in_capture_source():
    """Guard against a credential being pasted into the capture module."""
    env = pathlib.Path(__file__).resolve().parents[1] / ".env"
    source = (RESEARCH / "announcement_snapshot_capture.py").read_text(encoding="utf-8")
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            value = line.split("=", 1)[1].strip().strip("'\"")
            if len(value) >= 8:
                assert value not in source, "an .env value appears in the capture module"
    lowered = source.lower()
    for banned in ("password", "secret=", "api_key", "apikey", "access_token", "bearer "):
        assert banned not in lowered, f"capture module mentions {banned!r}"


# ---- research fences -----------------------------------------------------


def test_snapshot_tooling_registers_no_hypothesis_and_reads_no_outcome():
    """This line of work is a data-integrity check. It must not be able to
    create H006, touch the registry, or read a forward return."""
    for name in ("announcement_snapshot.py", "announcement_snapshot_capture.py"):
        source = (RESEARCH / name).read_text(encoding="utf-8")
        for banned in ("H006", "hypothesis_registry", "register_hypothesis",
                       "record_result", "HOLDOUT", "hold_out_", "future_move",
                       "forward_return"):
            assert banned not in source, f"{name} references {banned!r}"


def test_capture_window_is_development_only_and_never_loads_price():
    """The live compare command must not be able to wander into validation
    or hold-out, and must not load candles at all."""
    from app.research.announcement_snapshot_capture import (
        VERIFY_DAYS, WINDOW_END, WINDOW_ISO, WINDOW_START,
    )
    from app.research.tradeability import DEV_END, DEV_START, VAL_START

    assert WINDOW_ISO == (DEV_START.isoformat(), DEV_END.isoformat())
    assert WINDOW_START == DEV_START.strftime("%d-%m-%Y")
    assert WINDOW_END == DEV_END.strftime("%d-%m-%Y")
    for day in VERIFY_DAYS:
        parsed = dt.datetime.strptime(day, "%d-%m-%Y").date()
        assert DEV_START <= parsed <= DEV_END
        assert parsed < VAL_START

    source = (RESEARCH / "announcement_snapshot_capture.py").read_text(encoding="utf-8")
    assert "store.read" not in source
    assert ".read(" not in source
    assert "VAL_START" not in source
    assert "VAL_END" not in source
    assert "2026-07-24" not in source
    assert "24-07-2026" not in source
    assert "2026-08-" not in source
    assert source.count("ResearchStore().symbols") == 1
