#!/usr/bin/env python3
"""
scripts/generate_synthetic_queries.py

NOT part of the production pipeline. This environment does not have the
real data/processed/msmarco_web_search/train_queries.parquet file (it was
produced in a different session and is 540MB -- too large to upload), and
this container has neither pyarrow installed nor network access to fetch
it. To still build, test, benchmark, and validate the actual prefix index
end-to-end, this script generates a small synthetic query corpus that
matches the real schema (qid, original_query, normalized_query, ...) and
deliberately exercises every edge case the spec calls out: duplicate
normalized queries, Unicode, punctuation, code-like queries, and a dense
cluster of "machine lea..." queries so get_candidates("machine lea") has a
realistic, checkable answer.

Swap this out for the real iter_parquet_rows() in build_prefix_index.py
once train_queries.parquet is available in the environment -- nothing else
in the pipeline changes (see docs/prefix_index.md, "Validation" section).
"""

from __future__ import annotations

import random
from typing import Iterator

_RNG_SEED = 1337

# A realistic-ish set of head terms with rich completions, so prefix
# lookups against short/hot prefixes ("m", "ma", "machine l", "python",
# "data", "deep") have many real candidates, mirroring how these prefixes
# would behave against the real 9.2M-query corpus.
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

# Deliberately duplicate normalized queries (same normalized text, distinct
# qids) so the dedup path (requirement 5 / test 13) has real cases to
# exercise. These simulate what the real corpus's 3,843 duplicate rows
# look like -- e.g. the same query logged under different raw ids.
_DUPLICATE_GROUPS = [
    "machine learning tutorial",
    "python tutorial",
    "data science course",
]

_DUPLICATES_PER_GROUP = 3


def _iter_topic_queries() -> Iterator[str]:
    for completions in _TOPIC_COMPLETIONS.values():
        yield from completions
    yield from _UNICODE_QUERIES
    yield from _PUNCTUATION_QUERIES
    yield from _CODE_LIKE_QUERIES


def synthetic_records(n_filler: int = 2000) -> Iterator[dict]:
    """Yield synthetic (qid, original_query, normalized_query) dicts.

    Order:
      1. All hand-authored topic/unicode/punctuation/code queries (unique
         qids), so their presence in the index is guaranteed for tests.
      2. Explicit duplicate groups: the same normalized_query re-emitted
         under multiple distinct qids, to exercise dedup.
      3. `n_filler` random low-signal filler queries, purely to give the
         index a bit of realistic bulk / mixed popularity distribution for
         the benchmark.

    normalized_query mirrors what src/data/preprocess_queries.py would
    produce (NFKC + collapsed/trimmed whitespace); original_query is left
    byte-identical here since none of these synthetic strings need the
    case/whitespace changes preprocessing would normalize away.
    """
    qid_counter = 0

    def next_qid() -> str:
        nonlocal qid_counter
        qid_counter += 1
        return f"SYN{qid_counter:08d}"

    for q in _iter_topic_queries():
        yield {"qid": next_qid(), "original_query": q, "normalized_query": q}

    for base_query in _DUPLICATE_GROUPS:
        for _ in range(_DUPLICATES_PER_GROUP):
            yield {"qid": next_qid(), "original_query": base_query, "normalized_query": base_query}

    rng = random.Random(_RNG_SEED)
    subjects = ["best", "top", "how to", "why does", "cheap", "free", "online", "near me", "for kids", "2026"]
    objects = [
        "laptop",
        "recipe",
        "hotel",
        "workout plan",
        "insurance",
        "car",
        "phone plan",
        "guitar lessons",
        "resume template",
        "tax software",
        "web hosting",
        "vpn service",
        "coffee maker",
        "running shoes",
        "language course",
    ]
    for _ in range(n_filler):
        q = f"{rng.choice(subjects)} {rng.choice(objects)}"
        yield {"qid": next_qid(), "original_query": q, "normalized_query": q}
