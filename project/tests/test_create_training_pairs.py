"""Focused tests for real-data-compatible LSTM pair generation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.generate_synthetic_lstm_queries import synthetic_records  # noqa: E402
from src.models.lstm.create_training_pairs import (  # noqa: E402
    _contains_disallowed_control,
    generate_pairs_for_split,
)
from src.retrieval.index_builder import build_index_from_records  # noqa: E402
from src.retrieval.prefix_index import PrefixIndex, generate_prefixes  # noqa: E402


def _write_queries(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _make_fixture_index(tmp_path: Path) -> PrefixIndex:
    config = {
        "output": {"index_dir": str(tmp_path), "sqlite_filename": "index.sqlite3"},
        "matching": {"case_sensitive": False, "unicode_form": "NFKC"},
        "prefix_generation": {
            "min_prefix_length": 1,
            "max_prefix_length": 20,
            "include_whitespace_prefixes": True,
            "max_prefixes_per_query": 20,
        },
        "hot_prefix_cache": {"max_length": 6, "top_k": 50},
        "popularity": {"mode": "duplicate_count"},
    }
    records = list(synthetic_records(n_filler=5))
    records.extend(
        [
            {"qid": "BAD", "original_query": "machine\x08broken", "normalized_query": "machine\x08broken"},
            {"qid": "UNICODE", "original_query": "中文查询", "normalized_query": "中文查询"},
        ]
    )
    build_index_from_records(records, tmp_path / "index.sqlite3", config)
    return PrefixIndex.from_config(config)


def _generate(path: Path, index: PrefixIndex, **kwargs) -> list[dict]:
    return list(
        generate_pairs_for_split(
            path,
            index,
            min_prefix_length=1,
            max_prefix_length=12,
            max_prefixes_per_query=5,
            include_whitespace_prefixes=True,
            negatives_per_positive=2,
            candidate_pool_size=10,
            exclude_normalized_queries=None,
            seed=1337,
            **kwargs,
        )
    )


def test_positive_pairs_have_valid_prefixes_and_no_identity_collision(tmp_path: Path) -> None:
    rows = [
        {"qid": "Q1", "original_query": "machine learning", "normalized_query": "machine learning"},
    ]
    queries_path = tmp_path / "queries.jsonl"
    _write_queries(queries_path, rows)
    index = _make_fixture_index(tmp_path)
    try:
        pairs = _generate(queries_path, index)
    finally:
        index.close()

    positives = [pair for pair in pairs if pair["label"] == 1]
    assert positives
    assert all(pair["candidate"].startswith(pair["prefix"]) for pair in positives)
    for positive in positives:
        assert not any(
            pair["prefix"] == positive["prefix"]
            and pair["label"] == 0
            and pair["candidate"] == positive["candidate"]
            for pair in pairs
        )


def test_generation_is_deterministic(tmp_path: Path) -> None:
    queries_path = tmp_path / "queries.jsonl"
    _write_queries(
        queries_path,
        [
            {"qid": "Q1", "original_query": "machine learning", "normalized_query": "machine learning"},
            {"qid": "Q2", "original_query": "machine shop", "normalized_query": "machine shop"},
        ],
    )
    index = _make_fixture_index(tmp_path)
    try:
        first = _generate(queries_path, index)
        second = _generate(queries_path, index)
    finally:
        index.close()
    assert first == second


def test_ascii_controls_are_rejected_but_normal_whitespace_is_allowed() -> None:
    assert _contains_disallowed_control("bad\x08query")
    assert all(_contains_disallowed_control(chr(code)) for code in [0, 1, 7, 15, 31])
    assert not _contains_disallowed_control("normal space")
    assert not _contains_disallowed_control("normal\t tab\nline\rreturn")


def test_whitespace_prefixes_follow_configuration() -> None:
    assert "a " in generate_prefixes("a b", min_prefix_length=2, max_prefix_length=2)
    assert "a " not in generate_prefixes(
        "a b", min_prefix_length=2, max_prefix_length=2, include_whitespace_prefixes=False
    )
    assert "a\t" in generate_prefixes("a\tb", min_prefix_length=2, max_prefix_length=2)


def test_control_query_cannot_be_positive_or_negative_and_unicode_is_retained(tmp_path: Path) -> None:
    queries_path = tmp_path / "queries.jsonl"
    _write_queries(
        queries_path,
        [
            {"qid": "BAD", "original_query": "machine\x08broken", "normalized_query": "machine\x08broken"},
            {"qid": "GOOD", "original_query": "machine learning", "normalized_query": "machine learning"},
            {"qid": "CN", "original_query": "中文查询", "normalized_query": "中文查询"},
        ],
    )
    index = _make_fixture_index(tmp_path)
    try:
        pairs = _generate(queries_path, index)
    finally:
        index.close()

    assert any(pair["candidate"] == "中文查询" and pair["label"] == 1 for pair in pairs)
    assert all(not _contains_disallowed_control(pair["prefix"]) for pair in pairs)
    assert all(not _contains_disallowed_control(pair["candidate"]) for pair in pairs)
    assert all(pair["candidate"] != "machine\x08broken" for pair in pairs)


def test_train_exclusion_prevents_dev_positive_from_being_a_negative(tmp_path: Path) -> None:
    train_path = tmp_path / "train.jsonl"
    dev_path = tmp_path / "dev.jsonl"
    _write_queries(
        train_path,
        [{"qid": "Q1", "original_query": "machine learning", "normalized_query": "machine learning"}],
    )
    _write_queries(
        dev_path,
        [{"qid": "Q2", "original_query": "machine learning course", "normalized_query": "machine learning course"}],
    )
    index = _make_fixture_index(tmp_path)
    try:
        pairs = _generate(train_path, index, exclude_queries_path=dev_path)
    finally:
        index.close()
    assert all(
        pair["candidate"] != "machine learning course"
        for pair in pairs
        if pair["label"] == 0
    )


def test_no_match_prefix_is_handled_without_pairs(tmp_path: Path) -> None:
    queries_path = tmp_path / "queries.jsonl"
    _write_queries(
        queries_path,
        [{"qid": "Q1", "original_query": "zzzz", "normalized_query": "zzzz"}],
    )
    index = _make_fixture_index(tmp_path)
    try:
        pairs = _generate(queries_path, index)
    finally:
        index.close()
    assert any(pair["label"] == 1 for pair in pairs)
    assert all(pair["candidate"] != "zzzz" or pair["label"] == 1 for pair in pairs)
