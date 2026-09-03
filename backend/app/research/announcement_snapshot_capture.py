"""Retrieve the NSE announcement archive and freeze it as evidence.

Companion to `announcement_snapshot`, which holds the pure logic. This module
owns everything impure: HTTP, the clock, and the filesystem.

**Not an ingester.** It writes JSON artefacts to a directory *outside* the
repository and outside `research_data/`, creates no tables, touches no research
candle, and is never imported by the live application. It exists to answer one
data-integrity question and then stop. Nothing downstream reads its output
automatically; a human reads the comparison report.

**Why per-symbol queries rather than date ranges.** A date-range sweep of all
NSE equities over the development window is on the order of 21,000 records, and
the endpoint returns them newest-first in one response — large, and awkward to
verify as complete. Per-symbol queries for the whole window return a few dozen
records each, so completeness is self-evident, and the universe filter happens
at the source instead of in a post-filter that could silently drop a renamed
symbol. Date-range queries are still issued for a handful of days and diffed
against the per-symbol result, which is the completeness cross-check the design
asks for, run in the direction that can actually fail.

Usage, from `backend/`:

    python -m app.research.announcement_snapshot_capture baseline
    python -m app.research.announcement_snapshot_capture compare <baseline.json>
    python -m app.research.announcement_snapshot_capture diff --dry-run <baseline.json> <recheck.json>

No outcome, return, or price is read anywhere in this module.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import requests

from app.research.announcement_snapshot import (
    build_snapshot, compare_documents, compare_snapshots, file_fingerprint,
    parse_nse_datetime, read_snapshot, sequence_monotonicity, sha256_hex,
    write_comparison, write_snapshot,
)
from app.research.store import ResearchStore

# Outside the repository on purpose: this is evidence about a third-party
# archive, not project data, and must not sit anywhere a backfill or a research
# script could rewrite it.
ARTEFACT_ROOT = Path(__file__).resolve().parents[4] / "research_artefacts" / "announcements"

BASE = "https://www.nseindia.com"
API = f"{BASE}/api/corporate-announcements"
WARMUP = f"{BASE}/companies-listing/corporate-filings-announcements"

WINDOW_START = "01-06-2026"
WINDOW_END = "23-07-2026"
WINDOW_ISO = ("2026-06-01", "2026-07-23")

# Days re-queried by date range to confirm the per-symbol sweep missed nothing.
VERIFY_DAYS = ("16-06-2026", "01-07-2026", "22-07-2026")

REQUEST_SPACING_S = 0.4
DOC_HEAD_SAMPLE = 40
DOC_HASH_SAMPLE = 15
DOC_HASH_MAX_BYTES = 1_500_000

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def make_session() -> requests.Session:
    """A cookie-warmed session. No credentials of any kind are involved."""
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": WARMUP,
        }
    )
    s.get(WARMUP, timeout=30)
    return s


def fetch(session: requests.Session, params: dict, retries: int = 3) -> tuple[list[dict], dict]:
    """One API call plus the provenance record that documents it.

    The returned metadata carries the response digest, so the artefact can show
    that its records are the ones the endpoint actually sent rather than a
    post-processed view of them.
    """
    last_error = None
    for attempt in range(retries):
        try:
            resp = session.get(API, params=params, timeout=60)
            if resp.status_code == 200:
                body = resp.content
                data = resp.json()
                if isinstance(data, dict):
                    data = data.get("data", [])
                return data, {
                    "url": API,
                    "params": dict(params),
                    "status": resp.status_code,
                    "bytes": len(body),
                    "response_sha256": sha256_hex(body),
                    "record_count": len(data),
                    "attempt": attempt + 1,
                }
            last_error = f"HTTP {resp.status_code}"
        except Exception as exc:  # network flake, JSON error
            last_error = repr(exc)
        # NSE drops the session cookie periodically; a fresh warm-up is the
        # documented remedy and costs one request.
        time.sleep(1.5 * (attempt + 1))
        session.get(WARMUP, timeout=30)
    return [], {
        "url": API,
        "params": dict(params),
        "status": "FAILED",
        "error": last_error,
        "bytes": 0,
        "response_sha256": None,
        "record_count": 0,
        "attempt": retries,
    }


def sample_documents(session: requests.Session, records: list[dict]) -> dict:
    """Fingerprint a deterministic sample of attachment PDFs.

    Archiving every attachment would run to gigabytes for one window, so the
    sample is fixed by `seq_id` order rather than chosen at random: both the
    baseline and any later run fingerprint the *same* documents, which is the
    only way the comparison means anything.

    `HEAD` gives length, ETag and Last-Modified for free. A smaller subset is
    downloaded in full for a true content hash, because a server is free to
    change a body without changing the metadata it advertises.
    """
    with_docs = sorted(
        (r for r in records if (r.get("attchmntFile") or "").startswith("http")),
        key=lambda r: str(r.get("seq_id")),
    )
    if not with_docs:
        return {"attempted": 0, "note": "no attachment URLs present"}

    step = max(1, len(with_docs) // DOC_HEAD_SAMPLE)
    sample = with_docs[::step][:DOC_HEAD_SAMPLE]
    out: dict[str, dict] = {}
    hashed = 0
    for rec in sample:
        url = rec["attchmntFile"]
        entry: dict = {"seq_id": rec.get("seq_id"), "url": url}
        try:
            head = session.head(url, timeout=30, allow_redirects=True)
            entry["status"] = head.status_code
            entry["doc_length"] = head.headers.get("Content-Length")
            entry["doc_etag"] = head.headers.get("ETag")
            entry["last_modified"] = head.headers.get("Last-Modified")
        except Exception as exc:
            entry["status"] = "HEAD_FAILED"
            entry["error"] = repr(exc)
        length = entry.get("doc_length")
        small = length is not None and length.isdigit() and int(length) <= DOC_HASH_MAX_BYTES
        if hashed < DOC_HASH_SAMPLE and small and entry.get("status") == 200:
            try:
                body = session.get(url, timeout=90).content
                entry["doc_sha256"] = sha256_hex(body)
                entry["doc_bytes"] = len(body)
                hashed += 1
            except Exception as exc:
                entry["doc_sha256"] = None
                entry["error"] = repr(exc)
        time.sleep(REQUEST_SPACING_S)
        out[str(rec.get("seq_id"))] = entry

    return {
        "attempted": len(sample),
        "content_hashed": hashed,
        "head_sample_target": DOC_HEAD_SAMPLE,
        "hash_sample_target": DOC_HASH_SAMPLE,
        "hash_size_limit_bytes": DOC_HASH_MAX_BYTES,
        "limitation": (
            "Attachments are not archived. A deterministic sample is fingerprinted by "
            "HTTP metadata, and a subset under the size limit by full content hash. "
            "Documents outside the sample cannot be compared for content mutation."
        ),
        "entries": out,
    }


def sweep(session: requests.Session, symbols: list[str], verbose: bool = True):
    """Per-symbol sweep of the window, plus the date-range cross-check."""
    records: list[dict] = []
    queries: list[dict] = []
    per_symbol: dict[str, int] = {}
    failures: list[str] = []

    for i, sym in enumerate(symbols, 1):
        params = {
            "index": "equities",
            "symbol": sym,
            "from_date": WINDOW_START,
            "to_date": WINDOW_END,
        }
        data, meta = fetch(session, params)
        queries.append(meta)
        if meta["status"] == "FAILED":
            failures.append(sym)
        records.extend(data)
        per_symbol[sym] = len(data)
        if verbose and (i % 25 == 0 or i == len(symbols)):
            print(f"  {i}/{len(symbols)} symbols, {len(records)} records", flush=True)
        time.sleep(REQUEST_SPACING_S)

    universe = set(symbols)
    verification = []
    for day in VERIFY_DAYS:
        params = {"index": "equities", "from_date": day, "to_date": day}
        data, meta = fetch(session, params)
        queries.append(meta)
        from_range = {
            str(r["seq_id"]) for r in data if (r.get("symbol") or "").strip() in universe
        }
        target = dt.datetime.strptime(day, "%d-%m-%Y").date()
        from_sweep = set()
        for r in records:
            stamp = parse_nse_datetime(r.get("exchdisstime") or "")
            if stamp and stamp.date() == target:
                from_sweep.add(str(r["seq_id"]))
        verification.append(
            {
                "day": day,
                "date_range_universe_records": len(from_range),
                "per_symbol_sweep_records": len(from_sweep),
                "agree": from_range == from_sweep,
                "only_in_date_range": sorted(from_range - from_sweep),
                "only_in_sweep": sorted(from_sweep - from_range),
            }
        )
        time.sleep(REQUEST_SPACING_S)

    return records, queries, per_symbol, failures, verification


def capture(label: str, verbose: bool = True) -> tuple[Path, dict]:
    symbols = ResearchStore().symbols("5m")
    if verbose:
        print(f"universe: {len(symbols)} symbols; window {WINDOW_ISO[0]} .. {WINDOW_ISO[1]}")
    session = make_session()
    retrieved_at = dt.datetime.now(dt.timezone.utc)

    records, queries, per_symbol, failures, verification = sweep(session, symbols, verbose)
    if verbose:
        print(f"  fingerprinting document sample ...", flush=True)
    documents = sample_documents(session, records)

    snapshot = build_snapshot(
        records=records,
        queries=queries,
        retrieved_at=retrieved_at,
        universe=symbols,
        window=WINDOW_ISO,
        documents=documents,
        notes=(
            "Point-in-time fidelity baseline for the NSE corporate-announcement "
            "archive. Development window only. No validation or hold-out data, no "
            "price, return or outcome of any kind is read or stored here."
        ),
    )
    snapshot["per_symbol_counts"] = dict(sorted(per_symbol.items()))
    snapshot["failed_symbols"] = failures
    snapshot["completeness_verification"] = verification
    snapshot["sequence_monotonicity"] = sequence_monotonicity(snapshot["records"])

    stamp = retrieved_at.strftime("%Y%m%dT%H%M%SZ")
    path = ARTEFACT_ROOT / f"{label}_{stamp}.json"
    written = write_snapshot(path, snapshot)
    return path, written


def diff_files(baseline_path: Path, current_path: Path, *, dry_run: bool = False) -> Path | dict:
    """Diff two frozen snapshots.

    Default: write a new read-only comparison artefact. `dry_run=True` prints
    and returns the report without touching the filesystem — for tooling
    validation during a waiting period, when another live re-query would add
    nothing.
    """
    baseline = read_snapshot(baseline_path)
    current = read_snapshot(current_path)
    report = compare_snapshots(baseline, current)
    report["baseline_file"] = file_fingerprint(baseline_path)
    report["current_file"] = file_fingerprint(current_path)
    report["baseline_sequence_monotonicity"] = baseline.get("sequence_monotonicity")
    report["current_sequence_monotonicity"] = current.get("sequence_monotonicity")
    report["document_comparison"] = compare_documents(baseline, current)

    print("\n=== COMPARISON ===")
    if dry_run:
        print("  mode: DRY-RUN (no artefact written)")
    print(f"  baseline: {baseline['retrieved_at']}  ({baseline['record_count']} records)")
    print(f"  current:  {current['retrieved_at']}  ({current['record_count']} records)")
    print(f"  elapsed: {report['elapsed_seconds']}s")
    print(f"  records digest equal: "
          f"{baseline['records_sha256'] == current['records_sha256']}")
    print(f"  identical: {report['identical']}")
    for k, v in report["counts"].items():
        print(f"    {k}: {v}")
    print(f"  by class: {report['by_class'] or '{}'}")
    dc = report["document_comparison"]
    print(f"  documents compared: {dc['compared']} "
          f"(content-hashed both times: {dc['content_hashes_compared']})")
    print(f"    content changed:  {dc['content_changed']}")
    print(f"    advisory drift:   {dc['advisory_changed']} (ETag / Last-Modified, not mutation)")
    for item in dc["content_changes"][:5]:
        print(f"     CONTENT CHANGE {item}")

    if dry_run:
        return report

    out = ARTEFACT_ROOT / f"comparison_{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.json"
    write_comparison(out, report)
    print(f"  written: {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("baseline", help="retrieve and freeze a new baseline")
    c = sub.add_parser("compare", help="retrieve again and diff against a baseline")
    c.add_argument("baseline", type=Path)
    d = sub.add_parser("diff", help="diff two snapshots already on disk")
    d.add_argument("baseline", type=Path)
    d.add_argument("current", type=Path)
    d.add_argument("--dry-run", action="store_true",
                   help="classify without writing a comparison artefact")
    args = ap.parse_args(argv)

    if args.cmd == "baseline":
        _, written = capture("baseline")
        print("\n=== BASELINE WRITTEN (read-only) ===")
        for k, v in written.items():
            print(f"  {k}: {v}")
        return 0

    if args.cmd == "diff":
        diff_files(args.baseline, args.current, dry_run=args.dry_run)
        return 0

    print(f"baseline: {args.baseline}")
    path, _ = capture("recheck")
    diff_files(args.baseline, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
