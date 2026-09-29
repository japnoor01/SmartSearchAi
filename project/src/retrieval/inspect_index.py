#!/usr/bin/env python3
"""
src/retrieval/inspect_index.py

Small verification CLI for a built prefix index -- no pyarrow dependency
(reads only the finished .sqlite3 + its build_report.json), so it works
identically whether pointed at the synthetic demo index or a real one.

Usage:
    python -m src.retrieval.inspect_index --config configs/retrieval.yaml
    python -m src.retrieval.inspect_index --config configs/retrieval.yaml \
        --prefix machine --prefix "machine l" --prefix "machine lea" \
        --prefix python --prefix "deep learning" --prefix "data sc"

With no --prefix given, prints index-level stats only (from the build
report + a live COUNT(*) against the .sqlite3 file, so the "does this
index actually contain real data" question in the task's step 5 can be
answered by eye: compare the printed total against the real corpus's
known row count, 9,206,475).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.retrieval.prefix_index import PrefixIndex, QUERIES_TABLE, HOT_CACHE_TABLE  # noqa: E402


def print_stats(config: dict) -> None:
    out_cfg = config["output"]
    index_dir = Path(out_cfg["index_dir"])
    db_path = index_dir / out_cfg["sqlite_filename"]
    report_path = index_dir / out_cfg["build_report_filename"]

    print(f"index_dir:  {index_dir.resolve()}")
    print(f"db_path:    {db_path.resolve()}")
    if not db_path.exists():
        print("STATUS: NOT BUILT YET. Run: python -m src.retrieval.build_prefix_index --config <this config>")
        return

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    total_queries = conn.execute(f"SELECT COUNT(*) FROM {QUERIES_TABLE}").fetchone()[0]
    hot_cache_rows = conn.execute(f"SELECT COUNT(*) FROM {HOT_CACHE_TABLE}").fetchone()[0]
    conn.close()

    print(f"disk size:  {db_path.stat().st_size:,} bytes ({db_path.stat().st_size / (1024*1024):.1f} MB)")
    print(f"unique normalized queries in `queries` table: {total_queries:,}")
    print(f"rows in `hot_prefix_cache`:                    {hot_cache_rows:,}")

    if report_path.exists():
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        print("\nbuild_report.json:")
        for k, v in report.items():
            if k == "row_error_samples":
                continue
            print(f"  {k}: {v}")
    else:
        print(f"\n(no build report found at {report_path})")


def print_lookups(config: dict, prefixes: list[str], limit: int) -> None:
    index = PrefixIndex.from_config(config)
    try:
        for prefix in prefixes:
            t0 = time.perf_counter()
            results = index.get_candidates(prefix, limit=limit)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            print(f"\nprefix: {prefix!r}  ({len(results)} results, {elapsed_ms:.3f} ms, "
                  f"source={results[0].source if results else 'n/a'})")
            for c in results:
                print(f"  {c.normalized_query!r:60s} popularity={c.popularity:<4d} qid={c.qid}")
            if not results:
                print("  (no matches)")
    finally:
        index.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/retrieval.yaml"))
    parser.add_argument("--prefix", action="append", default=[], help="Repeatable. Defaults to the task's 6 verification prefixes if omitted and --stats-only is not set.")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--stats-only", action="store_true")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    print_stats(config)

    if args.stats_only:
        return

    prefixes = args.prefix or ["machine", "machine l", "machine lea", "python", "deep learning", "data sc"]
    out_cfg = config["output"]
    db_path = Path(out_cfg["index_dir"]) / out_cfg["sqlite_filename"]
    if not db_path.exists():
        return
    print("\n" + "=" * 70)
    print_lookups(config, prefixes, args.limit)


if __name__ == "__main__":
    main()
