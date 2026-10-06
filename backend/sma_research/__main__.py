"""python -m sma_research {download,analyze} ..."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sma_research", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="On the Fly machine: past candles + saved settings into --out")
    d.add_argument("--out", required=True, type=Path)
    d.add_argument("--days", type=int, default=90, help="calendar days of history (default 90)")
    d.add_argument("--top", type=int, default=60, help="most liquid F&O stocks to keep (default 60)")
    d.add_argument("--symbols", default="", help="comma-separated stocks instead of the F&O ranking")
    d.add_argument("--force", action="store_true", help="run even during market hours")
    a = sub.add_parser("analyze", help="Anywhere: replay the downloaded candles and write the report")
    a.add_argument("--data", required=True, type=Path, help="the download step's --out")
    a.add_argument("--out", required=True, type=Path)
    a.add_argument("--jobs", type=int, default=2)
    a.add_argument("--dev-share", type=float, default=0.6, help="earlier share of days used to choose thresholds")
    args = ap.parse_args(argv)
    # The engine logs every entry decision at INFO; only research progress is wanted here.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s", stream=sys.stdout)
    logging.getLogger("sma_research").setLevel(logging.INFO)

    if args.cmd == "download":
        from sma_research.data import download, market_hours

        if market_hours() and not args.force:
            print("Refusing to download during market hours (09:00-15:45 IST): the live bot needs the machine.")
            return 2
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
        meta = asyncio.run(download(args.out, days=args.days, top=args.top, symbols=symbols))
        print(f"Downloaded {len(meta['symbols'])} stocks, {meta['start']} to {meta['end']}, into {args.out}")
        return 0

    from sma_research.run import run

    report = run(args.data, args.out, args.jobs, args.dev_share)
    print(report.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
