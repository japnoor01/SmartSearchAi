#!/usr/bin/env python3
"""
scripts/generate_synthetic_lstm_queries.py

NOT part of the production pipeline -- a stand-in data source for the LSTM
ranking stage, needed for the same reason documented in
scripts/generate_synthetic_queries.py (used by the earlier prefix-index
stage) and docs/prefix_index.md: this environment does not have the real
9,206,475-row data/processed/msmarco_web_search/train_queries.parquet or
its dev_queries.parquet sibling (produced in a previous session, not
uploaded here), and this container has neither `pyarrow` nor network
access to fetch or regenerate them.

This script reuses the exact same hand-authored topic/unicode/punctuation/
code-like query bank as the prefix-index stage's synthetic generator (so
the "machine lea..." cluster the task's own example is built around is
guaranteed to exist), but adds one thing that generator did not need:
a **query-level train/dev split**, done by hashing each unique
normalized_query (never by row / qid), so that:

  - every qid for a given normalized_query lands entirely in train or
    entirely in dev -- a query can never appear on both sides, which is
    the leakage rule section 2 of the task asks for.
  - the split is deterministic (stable hash + fixed ratio), reproducible
    across runs without persisting a split file.

Output: two JSON Lines files (not Parquet -- see docs/lstm_model.md,
"Data availability", for why: writing real Parquet requires pyarrow or
fastparquet, neither of which is installed, and there is no network
access to install them). Each line: {"qid", "original_query",
"normalized_query"}, matching the schema documented in
configs/retrieval.yaml's `input` section minus the preprocessing-only
columns (language, char_length_*, token_count, normalization_changed)
that the LSTM stage does not need.

    data/synthetic/lstm/train_queries.jsonl
    data/synthetic/lstm/dev_queries.jsonl

Swap this out for real files read from
data/processed/msmarco_web_search/{train,dev}_queries.parquet once they
are available in an environment with pyarrow -- src/models/lstm/
create_training_pairs.py and evaluate.py both accept either a .jsonl or
.parquet path (see their --queries / --dev-queries CLI args).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Iterator

_RNG_SEED = 1337
_DEV_FRACTION = 0.2

# Identical topic bank to scripts/generate_synthetic_queries.py, so the
# retrieval index (already built from that generator) and the LSTM
# training pairs (built from this one) describe overlapping, consistent
# query populations -- create_training_pairs.py retrieves negatives from
# the *existing* prefix index (never rebuilt, per the task's instructions),
# so its candidate space needs to resemble this query population.
_TOPIC_COMPLETIONS = {
    "machine learning": [
        "machine learning",
        "machine learning course",
        "machine learning courses online",
        "machine learning algorithms",
        "machine learning algorithms explained",
        "machine learning projects",
        "machine learning projects for beginners",
        "machine learning tutorial",
        "machine learning tutorial for beginners",
        "machine learning engineer salary",
        "machine learning engineer jobs",
        "machine learning vs deep learning",
        "machine learning models",
        "machine learning interview questions",
        "machine learning roadmap",
        "machine learning python",
        "machine learning with python",
        "machine learning book",
        "machine learning books for beginners",
        "machine learning examples",
    ],
    "machine shop": [
        "machine shop near me",
        "machine shop tools",
        "machine shop equipment for sale",
    ],
    "machinist": [
        "machinist jobs",
        "machinist salary",
        "machinist apprenticeship",
    ],
    "python": [
        "python tutorial",
        "python for beginners",
        "python download",
        "python list comprehension",
        "python dictionary methods",
        "python vs java",
        "python pandas tutorial",
        "python string formatting",
        "python == vs is",
        "python requests library",
        "python install on windows",
    ],
    "data science": [
        "data science course",
        "data science jobs",
        "data science roadmap",
        "data science vs data analytics",
        "data science salary",
        "data structures and algorithms",
        "data scientist interview questions",
    ],
    "deep learning": [
        "deep learning",
        "deep learning specialization",
        "deep learning book",
        "deep learning vs machine learning",
        "deep learning frameworks",
        "deep learning with pytorch",
    ],
    "javascript": [
        "javascript array methods",
        "javascript async await",
        "javascript== vs ===",
        "javascript foreach loop",
    ],
}

_UNICODE_QUERIES = [
    "café near me",
    "München restaurants",
    "日本語 学習 アプリ",
    "北京 天气",
    "москва погода",
    "señor de los cielos",
    "naïve bayes classifier",
    "über uns kontakt",
    "东京 旅游 攻略",
    "παράδειγμα κώδικα python",
]

_PUNCTUATION_QUERIES = [
    "what's the weather today?",
    "c++ vs c#",
    "node.js vs python",
    "AT&T customer service",
    "top 10 movies (2024)",
    "who's who: a-z guide",
    "email@example.com format",
    "50% off coupon code!!",
    "machine learning: a-z",
    "don't stop believin lyrics",
]

_CODE_LIKE_QUERIES = [
    "for (int i = 0; i < n; i++)",
    "SELECT * FROM users WHERE id = 1",
    "git commit -m \"fix bug\"",
    "pip install numpy==1.26.0",
    "df.groupby('col').mean()",
    "const x = () => x + 1;",
    "import pandas as pd",
    "docker run -it ubuntu:latest",
    "npm install --save-dev",
    "machine_learning_model.fit(X_train, y_train)",
]

_FILLER_SUBJECTS = ["best", "top", "how to", "why does", "cheap", "free", "online", "near me", "for kids", "2026"]
_FILLER_OBJECTS = [
    "laptop", "recipe", "hotel", "workout plan", "insurance", "car", "phone plan",
    "guitar lessons", "resume template", "tax software", "web hosting", "vpn service",
    "coffee maker", "running shoes", "language course",
]


def _iter_topic_queries() -> Iterator[str]:
    for completions in _TOPIC_COMPLETIONS.values():
        yield from completions
    yield from _UNICODE_QUERIES
    yield from _PUNCTUATION_QUERIES
    yield from _CODE_LIKE_QUERIES


def _split_bucket(normalized_query: str, dev_fraction: float) -> str:
    """Deterministic query-level split: hash the normalized text (not the
    qid, not the row) so every occurrence of the same query -- however many
    duplicate qids it has -- lands on the same side of the split. This is
    what prevents the leakage case the task calls out: "the same query must
    not accidentally appear as both train-only and dev-only".
    """
    digest = hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "dev" if bucket < dev_fraction else "train"


def synthetic_records(n_filler: int = 1200) -> Iterator[dict]:
    """Yield synthetic (qid, original_query, normalized_query) dicts. See
    module docstring for provenance. Same three-part structure as
    scripts/generate_synthetic_queries.py: hand-authored queries, explicit
    duplicate groups, then random filler for bulk."""
    qid_counter = 0

    def next_qid() -> str:
        nonlocal qid_counter
        qid_counter += 1
        return f"LSTM{qid_counter:08d}"

    for q in _iter_topic_queries():
        yield {"qid": next_qid(), "original_query": q, "normalized_query": q}

    duplicate_groups = ["machine learning tutorial", "python tutorial", "data science course"]
    for base_query in duplicate_groups:
        for _ in range(3):
            yield {"qid": next_qid(), "original_query": base_query, "normalized_query": base_query}

    rng = random.Random(_RNG_SEED)
    for _ in range(n_filler):
        q = f"{rng.choice(_FILLER_SUBJECTS)} {rng.choice(_FILLER_OBJECTS)}"
        yield {"qid": next_qid(), "original_query": q, "normalized_query": q}


def write_split(n_filler: int, dev_fraction: float, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    train_path = out_dir / "train_queries.jsonl"
    dev_path = out_dir / "dev_queries.jsonl"

    stats = {"train_rows": 0, "dev_rows": 0, "train_unique": set(), "dev_unique": set()}
    with open(train_path, "w", encoding="utf-8") as ftrain, open(dev_path, "w", encoding="utf-8") as fdev:
        for record in synthetic_records(n_filler=n_filler):
            bucket = _split_bucket(record["normalized_query"], dev_fraction)
            line = json.dumps(record, ensure_ascii=False)
            if bucket == "dev":
                fdev.write(line + "\n")
                stats["dev_rows"] += 1
                stats["dev_unique"].add(record["normalized_query"])
            else:
                ftrain.write(line + "\n")
                stats["train_rows"] += 1
                stats["train_unique"].add(record["normalized_query"])

    overlap = stats["train_unique"] & stats["dev_unique"]
    assert not overlap, f"Leakage bug: {len(overlap)} normalized queries in both splits: {overlap}"

    return {
        "train_path": str(train_path),
        "dev_path": str(dev_path),
        "train_rows": stats["train_rows"],
        "dev_rows": stats["dev_rows"],
        "train_unique_queries": len(stats["train_unique"]),
        "dev_unique_queries": len(stats["dev_unique"]),
        "overlap_unique_queries": len(overlap),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-filler", type=int, default=1200)
    parser.add_argument("--dev-fraction", type=float, default=_DEV_FRACTION)
    parser.add_argument("--out-dir", type=Path, default=Path("data/synthetic/lstm"))
    args = parser.parse_args()

    summary = write_split(args.n_filler, args.dev_fraction, args.out_dir)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
