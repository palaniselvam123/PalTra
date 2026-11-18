#!/usr/bin/env python3
"""Share the backend test files out into N parts of about the same size (CI runs the parts on N runners at once).

Usage (from backend/): python ../scripts/test_parts.py N PART  -> prints PART's test files (PART is 1..N).
The biggest files go first, each into the part with the least so far, so every runner gets the same files every time.
"""
from __future__ import annotations

import sys
from pathlib import Path


def parts(files: list[Path], n: int) -> list[list[str]]:
    load = [0] * n
    out: list[list[str]] = [[] for _ in range(n)]
    for path in sorted(files, key=lambda p: (-p.stat().st_size, p.name)):
        i = min(range(n), key=lambda k: (load[k], k))
        out[i].append(str(path))
        load[i] += path.stat().st_size
    return out


def main() -> None:
    n, part = int(sys.argv[1]), int(sys.argv[2])
    files = sorted(Path("tests").glob("test_*.py"))
    print(" ".join(sorted(parts(files, n)[part - 1])))


if __name__ == "__main__":
    main()
