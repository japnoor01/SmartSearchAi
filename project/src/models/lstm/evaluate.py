#!/usr/bin/env python3
"""
src/models/lstm/evaluate.py

Evaluate autocomplete ranking quality by simulating the REAL pipeline
(task section 8):

    dev query -> prefixes -> existing prefix index -> candidate pool
        -> ranker (baseline ordering, or the LSTM) -> top-K
        -> Precision@1, Precision@K, Recall@K, MRR@K, Hit Rate@K

Two modes, selected with --mode:

  --mode baseline   Ranks the candidate pool using the prefix index's own
                     existing deterministic order (popularity DESC,
                     normalized_query ASC -- i.e. "no ranking model at
                     all"). Needs no PyTorch. *** This mode WAS run in this
                     session -- see reports/models/lstm_baseline_comparison.json
                     for the real output. ***

  --mode lstm       Re-ranks the same candidate pools with a trained
                     LSTMRanker checkpoint (src/models/lstm/inference.py).
                     Requires PyTorch and a checkpoint from train.py.
                     *** NOT run in this session -- PyTorch is not
                     installed here. See docs/lstm_model.md. ***

Both modes never re-query the target query's own text as a "candidate you
already know is right" -- ranking is always computed over exactly the
candidate pool the live prefix index would actually return for that
prefix, which is the whole point of "simulate the real pipeline" rather
than evaluating on isolated (prefix, candidate) pairs in a vacuum.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.retrieval.prefix_index import PrefixIndex, generate_prefixes  # noqa: E402


def _iter_queries(path: Path, max_queries: int | None = None):
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(path)
        count = 0
        for batch in pf.iter_batches(batch_size=50_000, columns=["qid", "original_query", "normalized_query"]):
            for row in batch.to_pylist():
                yield row
                count += 1
                if max_queries and count >= max_queries:
                    return
    else:
        with open(path, "r", encoding="utf-8") as f:
            count = 0
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
                    count += 1
                    if max_queries and count >= max_queries:
                        return


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def evaluate_split(
    mode: str,
    dev_queries_path: Path,
    index: PrefixIndex,
    *,
    pairs_cfg: dict,
    top_k: int,
    ranker=None,
    max_queries: int | None = None,
) -> dict:
    """Returns metrics + per-group latency samples. `ranker`
    (src.models.lstm.inference.LSTMRanker) is required and used when
    mode == "lstm"; ignored for "baseline"."""
    p_at_1, p_at_k, r_at_k, mrr_at_k, hit_at_k = [], [], [], [], []
    latencies_ms = []
    n_prefixes_evaluated = 0
    n_prefixes_target_missing_from_index = 0

    for row in _iter_queries(dev_queries_path, max_queries=max_queries):
        target = row["normalized_query"]
        prefixes = generate_prefixes(
            target,
            min_prefix_length=pairs_cfg["min_prefix_length"],
            max_prefix_length=pairs_cfg["max_prefix_length"],
            include_whitespace_prefixes=pairs_cfg["include_whitespace_prefixes"],
            max_prefixes_per_query=pairs_cfg["max_prefixes_per_query"],
        )
        for prefix in prefixes:
            candidates = index.get_candidates(prefix, limit=pairs_cfg["candidate_pool_size"])
            candidate_texts = [c.normalized_query for c in candidates]
            if target not in candidate_texts:
                # The prefix index (built from its own synthetic corpus --
                # see docs/prefix_index.md) does not contain this exact
                # dev query. Documented, not silently dropped: cannot
                # evaluate ranking quality for a prefix whose correct
                # answer isn't even in the retrieval layer's candidate
                # pool -- that's a retrieval-recall gap, not something an
                # ranking-only re-ranker can fix.
                n_prefixes_target_missing_from_index += 1
                continue

            t0 = time.perf_counter()
            if mode == "baseline":
                ranked_texts = candidate_texts  # index's own order already applied
            else:
                ranked = ranker.rank_candidates(prefix, candidate_texts, top_k=len(candidate_texts))
                ranked_texts = [r["candidate"] for r in ranked]
            latencies_ms.append((time.perf_counter() - t0) * 1000)

            topk = ranked_texts[:top_k]
            n_prefixes_evaluated += 1
            p_at_1.append(1.0 if ranked_texts[:1] == [target] else 0.0)
            p_at_k.append(sum(1 for c in topk if c == target) / len(topk) if topk else 0.0)
            r_at_k.append(1.0 if target in topk else 0.0)  # exactly one relevant item per query here
            hit_at_k.append(1.0 if target in topk else 0.0)
            rr = 0.0
            for rank, c in enumerate(topk, start=1):
                if c == target:
                    rr = 1.0 / rank
                    break
            mrr_at_k.append(rr)

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "mode": mode,
        "n_prefixes_evaluated": n_prefixes_evaluated,
        "n_prefixes_target_missing_from_index": n_prefixes_target_missing_from_index,
        "precision@1": round(_mean(p_at_1), 4),
        f"precision@{top_k}": round(_mean(p_at_k), 4),
        f"recall@{top_k}": round(_mean(r_at_k), 4),
        f"mrr@{top_k}": round(_mean(mrr_at_k), 4),
        f"hit_rate@{top_k}": round(_mean(hit_at_k), 4),
        "latency_ms": {
            "avg": round(statistics.mean(latencies_ms), 4) if latencies_ms else None,
            "p50": round(_percentile(latencies_ms, 0.50), 4) if latencies_ms else None,
            "p95": round(_percentile(latencies_ms, 0.95), 4) if latencies_ms else None,
            "n_samples": len(latencies_ms),
        },
    }


def evaluate_pairs_split(
    mode: str,
    pairs_path: Path,
    *,
    top_k: int,
    ranker=None,
    max_groups: int | None = None,
) -> dict:
    """Evaluate ranking metrics on grouped prefix candidate pools from a pairs JSONL file."""
    p_at_1, p_at_k, r_at_k, mrr_at_k, hit_at_k = [], [], [], [], []
    latencies_ms = []
    n_groups = 0

    current_prefix: str | None = None
    current_group: list[dict] = []

    def _eval_group(group: list[dict]) -> None:
        nonlocal n_groups
        targets = [g["candidate"] for g in group if g["label"] == 1]
        if not targets:
            return
        target = targets[0]
        candidate_texts = [g["candidate"] for g in group]
        prefix = group[0]["prefix"]

        t0 = time.perf_counter()
        if mode == "baseline":
            ranked_texts = candidate_texts
        else:
            ranked = ranker.rank_candidates(prefix, candidate_texts, top_k=len(candidate_texts))
            ranked_texts = [r["candidate"] for r in ranked]
        latencies_ms.append((time.perf_counter() - t0) * 1000)

        topk = ranked_texts[:top_k]
        p_at_1.append(1.0 if ranked_texts[:1] == [target] else 0.0)
        p_at_k.append(sum(1 for c in topk if c in targets) / len(topk) if topk else 0.0)
        r_at_k.append(1.0 if any(c in targets for c in topk) else 0.0)
        hit_at_k.append(1.0 if any(c in targets for c in topk) else 0.0)
        rr = 0.0
        for rank, c in enumerate(topk, start=1):
            if c in targets:
                rr = 1.0 / rank
                break
        mrr_at_k.append(rr)
        n_groups += 1

    with open(pairs_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            prefix = row["prefix"]
            if current_prefix is None:
                current_prefix = prefix
                current_group = [row]
            elif prefix == current_prefix:
                current_group.append(row)
            else:
                _eval_group(current_group)
                if max_groups is not None and n_groups >= max_groups:
                    current_group = []
                    break
                current_prefix = prefix
                current_group = [row]

    if current_group and (max_groups is None or n_groups < max_groups):
        _eval_group(current_group)

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "mode": mode,
        "n_prefixes_evaluated": n_groups,
        "precision@1": round(_mean(p_at_1), 4),
        f"precision@{top_k}": round(_mean(p_at_k), 4),
        f"recall@{top_k}": round(_mean(r_at_k), 4),
        f"mrr@{top_k}": round(_mean(mrr_at_k), 4),
        f"hit_rate@{top_k}": round(_mean(hit_at_k), 4),
        "latency_ms": {
            "avg": round(statistics.mean(latencies_ms), 4) if latencies_ms else None,
            "p50": round(_percentile(latencies_ms, 0.50), 4) if latencies_ms else None,
            "p95": round(_percentile(latencies_ms, 0.95), 4) if latencies_ms else None,
            "n_samples": len(latencies_ms),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/lstm.yaml"))
    parser.add_argument("--mode", choices=["baseline", "lstm"], required=True)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--tokenizer-dir", type=Path, default=None)
    parser.add_argument("--pairs", type=Path, default=None, help="Evaluate directly on a pairs JSONL file.")
    parser.add_argument("--max-queries", type=int, default=None, help="Limit number of dev queries evaluated.")
    parser.add_argument("--max-groups", type=int, default=None, help="Limit number of prefix groups evaluated.")
    parser.add_argument("--output", type=Path, default=None, help="Output JSON report path.")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    ranker = None
    if args.mode == "lstm":
        from src.models.lstm.inference import LSTMRanker

        checkpoint = args.checkpoint or Path(cfg["train"]["checkpoint_dir"]) / cfg["train"]["best_checkpoint_name"]
        tokenizer_dir = args.tokenizer_dir or Path(cfg["tokenizer"]["save_dir"])
        device = cfg["train"].get("device", "auto")
        ranker = LSTMRanker(checkpoint, tokenizer_dir, device=device)

    if args.pairs:
        result = evaluate_pairs_split(
            args.mode,
            args.pairs,
            top_k=cfg["evaluate"]["top_k"],
            ranker=ranker,
            max_groups=args.max_groups,
        )
    else:
        with open(cfg["retrieval"]["config_path"], "r", encoding="utf-8") as f:
            retrieval_cfg = yaml.safe_load(f)
        index = PrefixIndex.from_config(retrieval_cfg)
        result = evaluate_split(
            args.mode,
            Path(cfg["data"]["dev_queries"]),
            index,
            pairs_cfg=cfg["training_pairs"],
            top_k=cfg["evaluate"]["top_k"],
            ranker=ranker,
            max_queries=args.max_queries,
        )
        index.close()

    report_path = args.output or Path(cfg["evaluate"]["report_path"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
