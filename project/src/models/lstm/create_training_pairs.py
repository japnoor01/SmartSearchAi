#!/usr/bin/env python3
"""
src/models/lstm/create_training_pairs.py

Generate (prefix, candidate_query, label) training/validation examples for
the LSTM ranking model, from a query dataset (task section 1) without ever
materializing the full dataset in RAM (section 13).

Positives
---------
For each query in the source split, a bounded set of prefixes is cut from
its normalized text (`generate_prefixes()` from src/retrieval/prefix_index.py
-- the SAME function the already-built prefix index uses, so a prefix seen
during LSTM training is a prefix the retrieval layer can actually produce
at serve time). Each (prefix, query) pair is emitted as a positive,
label=1.

Negatives
---------
For each positive's prefix, this queries the *existing, already-built*
prefix index (`PrefixIndex.get_candidates`, never rebuilt -- see the
task's explicit "DO NOT rebuild or replace the prefix index") for the
top-N candidates that prefix would actually surface in production, and
samples `negatives_per_positive` of them that are not the positive's own
query, label=0.

**Documented limitation (task section 1: "do NOT assume every other query
is a true negative without documenting the limitation")**: a candidate
returned by the prefix index for the same prefix is, by construction, also
a *plausible* autocomplete completion (it starts with the same text the
user typed). Some of these "negatives" may be nearly as relevant as the
positive (e.g. prefix "machine lea" -> negative "machine learning course"
is a reasonable suggestion, not an irrelevant one). We do not have
click-through or human relevance judgments to distinguish "irrelevant" from
"relevant but not the one specific query we happened to sample as
positive", so every non-target candidate is treated as a *weak* negative:
plausibly less relevant than the exact match, not verified irrelevant. This
is standard practice for autocomplete ranking pretraining (in-batch /
retrieved negatives) but the resulting labels are noisier than a hand
-labeled relevance judgment would be, and evaluation (evaluate.py) should
be read with that in mind.

If the index does not return enough candidates to fill
`negatives_per_positive` (short/rare prefixes), the shortfall is backfilled
with negatives sampled uniformly from *other* queries in the same source
split (never from the other split -- see leakage note below), clearly
tagged `source="random_fallback"` in the output so downstream analysis can
separate index-retrieved negatives from this fallback.

Leakage prevention (task section 2)
------------------------------------
- Train pairs are generated only from the train query file; dev pairs only
  from the dev query file. The caller is responsible for passing disjoint
  files (scripts/generate_synthetic_lstm_queries.py guarantees this by
  construction -- see its docstring).
- Because the *existing* prefix index was built from the full training
    corpus (the index is not split into train/dev; only this LSTM stage
    introduces that split), a
  negative candidate retrieved from it could coincide with a query that
  ended up on the dev side of the LSTM split. When generating **train**
  pairs, any candidate whose normalized_query is in the dev set is
  filtered out before sampling negatives, so dev queries never appear as
  training signal (positive or negative). This filter is skipped when
  generating dev pairs (there, seeing the full index's candidates is
  correct -- it's what evaluate.py's realistic pipeline simulation needs).
- Duplicate normalized queries (same text, multiple qids) are collapsed to
  one row before pair generation, so the same (prefix, query) positive is
  never emitted more than once per duplicate group.

Memory
------
Queries are streamed line-by-line from the source .jsonl (or read via
pyarrow from .parquet, if available and if that's the file extension given
-- see `_iter_queries`). Pairs are written incrementally to the output
.jsonl rather than accumulated in a Python list, so peak memory is
independent of dataset size (task section 13).
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Iterator, MutableMapping

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.retrieval.prefix_index import PrefixIndex, generate_prefixes  # noqa: E402

_ALLOWED_CONTROL_WHITESPACE = frozenset("\t\n\r")


def _contains_disallowed_control(text: str) -> bool:
    """Return whether text contains a non-whitespace ASCII control."""
    return any(
        ord(character) < 32 and character not in _ALLOWED_CONTROL_WHITESPACE
        for character in text
    )


def _iter_queries(path: Path) -> Iterator[dict]:
    """Stream (qid, original_query, normalized_query) records from a
    .jsonl file, or a .parquet file if pyarrow is installed. Never loads
    the full file into a Python list."""
    if path.suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as e:
            raise SystemExit(
                f"{path} is a .parquet file but pyarrow is not installed. "
                "Install it (pip install pyarrow --break-system-packages) or "
                "point --queries at a .jsonl file such as the ones produced by "
                "scripts/generate_synthetic_lstm_queries.py."
            ) from e
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=50_000, columns=["qid", "original_query", "normalized_query"]):
            for row in batch.to_pylist():
                yield row
    else:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


class _QueryStore:
    """Disk-backed deduplicated query store used during pair generation."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            "CREATE TABLE queries (normalized_query TEXT PRIMARY KEY, qid TEXT NOT NULL, "
            "original_query TEXT NOT NULL, duplicate_count INTEGER NOT NULL DEFAULT 0)"
        )
        self.connection.execute("CREATE INDEX queries_qid ON queries(qid)")
        self.rows_seen = 0
        self.invalid_control_rows = 0
        self._pending = 0

    def add(self, row: dict) -> None:
        normalized_query = str(row["normalized_query"])
        self.rows_seen += 1
        if _contains_disallowed_control(normalized_query):
            self.invalid_control_rows += 1
            return
        qid = str(row["qid"])
        original_query = str(row["original_query"])
        insert_cursor = self.connection.execute(
            "INSERT OR IGNORE INTO queries(normalized_query, qid, original_query) VALUES (?, ?, ?)",
            (normalized_query, qid, original_query),
        )
        if insert_cursor.rowcount == 0:
            self.connection.execute(
                "UPDATE queries SET duplicate_count = duplicate_count + 1 WHERE normalized_query = ?",
                (normalized_query,),
            )
        self.connection.execute(
            "UPDATE queries SET qid = ?, original_query = ? "
            "WHERE normalized_query = ? AND ? < qid",
            (qid, original_query, normalized_query, qid),
        )
        self._pending += 1
        if self._pending >= 20_000:
            self.connection.commit()
            self._pending = 0

    def finish(self) -> None:
        self.connection.commit()

    @property
    def unique_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM queries").fetchone()[0])

    def contains(self, normalized_query: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM queries WHERE normalized_query = ? LIMIT 1", (normalized_query,)
        ).fetchone() is not None

    def iter_rows(self) -> Iterator[dict]:
        cursor = self.connection.execute(
            "SELECT qid, original_query, normalized_query, duplicate_count "
            "FROM queries ORDER BY normalized_query"
        )
        for qid, original_query, normalized_query, duplicate_count in cursor:
            yield {
                "qid": qid,
                "original_query": original_query,
                "normalized_query": normalized_query,
                "duplicate_count": duplicate_count,
            }

    def close(self) -> None:
        self.connection.close()


def _stage_queries(path: Path, store_path: Path) -> _QueryStore:
    store = _QueryStore(store_path)
    for row in _iter_queries(path):
        store.add(row)
    store.finish()
    return store


def _build_fallback_reservoir(
    store: _QueryStore,
    size: int,
    exclude: set[str],
    exclude_store: _QueryStore | None,
    seed: int,
) -> list[str]:
    """Keep a deterministic bounded sample for rare-prefix shortfalls."""
    rng = random.Random(seed ^ 0x5EED5EED)
    reservoir: list[str] = []
    eligible_seen = 0
    for row in store.iter_rows():
        normalized_query = row["normalized_query"]
        if normalized_query in exclude or (exclude_store is not None and exclude_store.contains(normalized_query)):
            continue
        eligible_seen += 1
        if len(reservoir) < size:
            reservoir.append(normalized_query)
        else:
            replacement = rng.randrange(eligible_seen)
            if replacement < size:
                reservoir[replacement] = normalized_query
    return reservoir


def generate_pairs_for_split(
    queries_path: Path,
    index: PrefixIndex,
    *,
    min_prefix_length: int,
    max_prefix_length: int,
    max_prefixes_per_query: int,
    include_whitespace_prefixes: bool,
    negatives_per_positive: int,
    candidate_pool_size: int,
    exclude_normalized_queries: set[str] | None,
    seed: int,
    exclude_queries_path: Path | None = None,
    stats: MutableMapping[str, int | float] | None = None,
) -> Iterator[dict]:
    """Yield {"prefix", "candidate", "label", "source"} dicts for one
    split. `exclude_normalized_queries`, when given, drops any retrieved
    candidate whose normalized_query is in that set before negatives are
    sampled -- used to keep dev queries out of train negatives."""
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory(prefix="smartsearch_lstm_pairs_") as temp_dir:
        query_store = _stage_queries(queries_path, Path(temp_dir) / "queries.sqlite3")
        exclude = set(exclude_normalized_queries or ())
        if exclude_queries_path is not None:
            exclude_store = _stage_queries(exclude_queries_path, Path(temp_dir) / "exclude.sqlite3")
        else:
            exclude_store = None

        fallback_reservoir = _build_fallback_reservoir(
            query_store,
            max(candidate_pool_size, negatives_per_positive * 8),
            exclude,
            exclude_store,
            seed,
        )
        if stats is not None:
            stats["source_queries_processed"] = query_store.rows_seen
            stats["invalid_control_rows_skipped"] = query_store.invalid_control_rows
            stats["unique_source_queries"] = query_store.unique_count
            stats["duplicate_source_rows"] = query_store.rows_seen - query_store.unique_count
            stats["fallback_reservoir_size"] = len(fallback_reservoir)

        try:
            for row in query_store.iter_rows():
                normalized_query = row["normalized_query"]
                prefixes = generate_prefixes(
                    normalized_query,
                    min_prefix_length=min_prefix_length,
                    max_prefix_length=max_prefix_length,
                    include_whitespace_prefixes=include_whitespace_prefixes,
                    max_prefixes_per_query=max_prefixes_per_query,
                )
                if stats is not None and not prefixes:
                    stats["queries_without_generated_prefixes"] = stats.get("queries_without_generated_prefixes", 0) + 1
                for prefix in prefixes:
                    if _contains_disallowed_control(prefix):
                        if stats is not None:
                            stats["invalid_control_prefixes_skipped"] = stats.get("invalid_control_prefixes_skipped", 0) + 1
                        continue
                    if stats is not None:
                        stats["prefixes_generated"] = stats.get("prefixes_generated", 0) + 1
                    yield {"prefix": prefix, "candidate": normalized_query, "label": 1, "source": "positive"}

                    candidates = index.get_candidates(prefix, limit=candidate_pool_size)
                    pool = [
                        c.normalized_query
                        for c in candidates
                        if c.normalized_query != normalized_query
                        and not _contains_disallowed_control(c.normalized_query)
                        and c.normalized_query not in exclude
                        and (exclude_store is None or not exclude_store.contains(c.normalized_query))
                    ]
                    if stats is not None:
                        stats["candidate_pool_queries"] = stats.get("candidate_pool_queries", 0) + len(pool)
                        stats["candidate_pool_count"] = stats.get("candidate_pool_count", 0) + 1
                        pool_size = len(pool)
                        stats["candidate_pool_min"] = min(stats.get("candidate_pool_min", pool_size), pool_size)
                        stats["candidate_pool_max"] = max(stats.get("candidate_pool_max", pool_size), pool_size)
                        if not pool:
                            stats["prefixes_without_index_negatives"] = stats.get("prefixes_without_index_negatives", 0) + 1
                    rng.shuffle(pool)
                    chosen = pool[:negatives_per_positive]
                    for neg in chosen:
                        yield {"prefix": prefix, "candidate": neg, "label": 0, "source": "index_retrieved"}

                    fallback_pool = [
                        q for q in fallback_reservoir
                        if q != normalized_query
                        and q not in chosen
                        and not _contains_disallowed_control(q)
                    ]
                    rng.shuffle(fallback_pool)
                    for neg in fallback_pool[: negatives_per_positive - len(chosen)]:
                        yield {"prefix": prefix, "candidate": neg, "label": 0, "source": "random_fallback"}
                    if stats is not None and len(chosen) < negatives_per_positive:
                        stats["negative_shortfalls"] = stats.get("negative_shortfalls", 0) + 1
        finally:
            query_store.close()
            if exclude_store is not None:
                exclude_store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/lstm.yaml"))
    parser.add_argument("--split", choices=["train", "dev"], required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Override the configured JSONL output path without changing the config.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Override the JSON report path without changing the config.",
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    data_cfg = cfg["data"]
    pairs_cfg = cfg["training_pairs"]
    retrieval_cfg_path = Path(cfg["retrieval"]["config_path"])
    with open(retrieval_cfg_path, "r", encoding="utf-8") as f:
        retrieval_cfg = yaml.safe_load(f)

    queries_path = Path(data_cfg["train_queries"] if args.split == "train" else data_cfg["dev_queries"])
    out_path = args.output or Path(
        pairs_cfg["train_pairs_path"] if args.split == "train" else pairs_cfg["dev_pairs_path"]
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    label_counts: Counter = Counter()
    source_counts: Counter = Counter()
    generation_stats: dict[str, int | float] = {}

    with PrefixIndex.from_config(retrieval_cfg) as index, open(out_path, "w", encoding="utf-8") as f:
        for pair in generate_pairs_for_split(
            queries_path,
            index,
            min_prefix_length=pairs_cfg["min_prefix_length"],
            max_prefix_length=pairs_cfg["max_prefix_length"],
            max_prefixes_per_query=pairs_cfg["max_prefixes_per_query"],
            include_whitespace_prefixes=pairs_cfg["include_whitespace_prefixes"],
            negatives_per_positive=pairs_cfg["negatives_per_positive"],
            candidate_pool_size=pairs_cfg["candidate_pool_size"],
            exclude_normalized_queries=None,
            exclude_queries_path=Path(data_cfg["dev_queries"]) if args.split == "train" else None,
            seed=cfg["seed"],
            stats=generation_stats,
        ):
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")
            label_counts[pair["label"]] += 1
            source_counts[pair["source"]] += 1

    elapsed = time.time() - t0

    report = {
        "split": args.split,
        "queries_path": str(queries_path),
        "output_path": str(out_path),
        "positive_pairs": label_counts.get(1, 0),
        "negative_pairs": label_counts.get(0, 0),
        "negatives_by_source": {k: v for k, v in source_counts.items() if k != "positive"},
        "total_pairs": sum(label_counts.values()),
        **generation_stats,
        "generation_seconds": round(elapsed, 3),
        "leakage_filter_applied": args.split == "train",
    }
    pool_count = generation_stats.get("candidate_pool_count", 0)
    report["candidate_pool_average"] = round(
        generation_stats.get("candidate_pool_queries", 0) / pool_count, 3
    ) if pool_count else 0.0
    report_path = args.report or out_path.parent / f"{args.split}_pairs_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
