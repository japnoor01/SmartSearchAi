#!/usr/bin/env python3
"""
src/retrieval/benchmark_prefix_index.py

Latency benchmark for the built prefix index.

Usage:
    python -m src.retrieval.benchmark_prefix_index --config configs/retrieval.yaml

For each of a fixed set of benchmark prefixes, runs a warm-up pass followed
by >= 100 timed calls to PrefixIndex.get_candidates(), then reports p50,
p95, p99, and average latency (milliseconds) per prefix plus an overall
summary. Results are written to reports/retrieval/prefix_index_benchmark.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import time
from pathlib import Path

import yaml

from src.retrieval.prefix_index import PrefixIndex

LOG = logging.getLogger("benchmark_prefix_index")

BENCHMARK_PREFIXES = [
    "m",
    "ma",
    "mac",
    "machine",
    "machine l",
    "machine lea",
    "python",
    "data",
    "deep",
    "xyznonexistent",
]

WARMUP_ITERATIONS = 20
TIMED_ITERATIONS = 200  # >= 100 per requirement, with headroom for stable percentiles
LIMIT = 100


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def benchmark_prefix(index: PrefixIndex, prefix: str) -> dict:
    for _ in range(WARMUP_ITERATIONS):
        index.get_candidates(prefix, limit=LIMIT)

    latencies_ms: list[float] = []
    for _ in range(TIMED_ITERATIONS):
        t0 = time.perf_counter()
        results = index.get_candidates(prefix, limit=LIMIT)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    latencies_ms.sort()
    return {
        "prefix": prefix,
        "iterations": TIMED_ITERATIONS,
        "result_count_last_run": len(results),
        "p50_ms": round(_percentile(latencies_ms, 50), 4),
        "p95_ms": round(_percentile(latencies_ms, 95), 4),
        "p99_ms": round(_percentile(latencies_ms, 99), 4),
        "avg_ms": round(statistics.mean(latencies_ms), 4),
        "min_ms": round(latencies_ms[0], 4),
        "max_ms": round(latencies_ms[-1], 4),
    }


def run_benchmark(index: PrefixIndex, prefixes: list[str] = BENCHMARK_PREFIXES) -> dict:
    per_prefix = [benchmark_prefix(index, p) for p in prefixes]
    all_p50 = [r["p50_ms"] for r in per_prefix]
    all_p95 = [r["p95_ms"] for r in per_prefix]
    all_p99 = [r["p99_ms"] for r in per_prefix]
    all_avg = [r["avg_ms"] for r in per_prefix]
    return {
        "limit_per_query": LIMIT,
        "warmup_iterations": WARMUP_ITERATIONS,
        "timed_iterations": TIMED_ITERATIONS,
        "per_prefix": per_prefix,
        "overall": {
            "p50_ms": round(statistics.mean(all_p50), 4),
            "p95_ms": round(statistics.mean(all_p95), 4),
            "p99_ms": round(statistics.mean(all_p99), 4),
            "avg_ms": round(statistics.mean(all_avg), 4),
            "worst_p99_ms": round(max(all_p99), 4),
            "worst_p99_prefix": per_prefix[all_p99.index(max(all_p99))]["prefix"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the prefix index's lookup latency.")
    parser.add_argument("--config", type=Path, default=Path("configs/retrieval.yaml"))
    parser.add_argument("--output", type=Path, default=None, help="Override the report output path.")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    index = PrefixIndex.from_config(config)
    try:
        LOG.info("running benchmark (%d prefixes, %d warm-up + %d timed iterations each)...",
                  len(BENCHMARK_PREFIXES), WARMUP_ITERATIONS, TIMED_ITERATIONS)
        results = run_benchmark(index)
    finally:
        index.close()

    output_path = args.output or Path("reports/retrieval/prefix_index_benchmark.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    LOG.info("overall p50=%.3fms p95=%.3fms p99=%.3fms avg=%.3fms",
              results["overall"]["p50_ms"], results["overall"]["p95_ms"],
              results["overall"]["p99_ms"], results["overall"]["avg_ms"])
    LOG.info("benchmark report written to %s", output_path)


if __name__ == "__main__":
    main()
