"""
tests/test_build_prefix_index.py

Tests for src/retrieval/build_prefix_index.py -- the real-Parquet-input
build entrypoint, as distinct from tests/test_prefix_index.py (which tests
the pyarrow-free indexing core via in-memory records).

Two groups, split deliberately so this file is useful in ANY environment:

1. CLI / safety-guard logic that never touches Parquet -- config loading,
   the missing-input-file check, and _guard_against_overwriting_demo_index
   (which protects data/indexes/prefix/, the permanent synthetic/demo
   index, from being silently overwritten by a real-data build). These
   run and pass with no pyarrow installed, because build_prefix_index.py
   imports pyarrow lazily inside iter_parquet_rows() specifically so this
   is possible -- see that function's docstring.

2. Parquet-reading tests (iter_parquet_rows batching, its required-column
   check, and a full small end-to-end build FROM a real .parquet file)
   -- gated behind `pytest.importorskip("pyarrow")`, so they skip cleanly
   (not silently, not fabricated-passing) wherever pyarrow isn't
   installed, and run for real the moment it is -- including on the
   user's local machine, where the actual 9.2M-row build happens.

Run with:
    python -m pytest tests/test_build_prefix_index.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.retrieval.build_prefix_index as bpi  # noqa: E402
from src.retrieval.prefix_index import PrefixIndex  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Group 1: CLI / safety-guard logic -- no pyarrow required.
# ---------------------------------------------------------------------------


def test_module_imports_without_pyarrow():
    # If this test file collected at all, the import at the top already
    # succeeded without pyarrow needing to be installed -- this assertion
    # just makes that guarantee explicit and named.
    assert hasattr(bpi, "iter_parquet_rows")
    assert hasattr(bpi, "main")


def test_guard_blocks_the_known_synthetic_demo_dir():
    with pytest.raises(SystemExit, match="synthetic/demo index"):
        bpi._guard_against_overwriting_demo_index(Path("data/indexes/prefix"), force=False)


def test_guard_blocks_demo_dir_via_relative_dotted_path():
    # Same physical directory, spelled differently -- .resolve() must
    # still catch it.
    with pytest.raises(SystemExit):
        bpi._guard_against_overwriting_demo_index(Path("data/indexes/../indexes/prefix"), force=False)


def test_guard_allows_a_different_directory():
    # Should not raise.
    bpi._guard_against_overwriting_demo_index(Path("data/indexes/prefix_real"), force=False)


def test_guard_allows_demo_dir_with_force():
    # Should not raise.
    bpi._guard_against_overwriting_demo_index(Path("data/indexes/prefix"), force=True)


def test_default_config_output_dir_is_not_the_demo_dir():
    """Regression guard for the config itself: configs/retrieval.yaml's
    default output.index_dir must not equal the permanent demo index
    directory, or a plain `python -m src.retrieval.build_prefix_index
    --config configs/retrieval.yaml` (no flags) would need --force just to
    run, which would be a confusing default."""
    with open(PROJECT_ROOT / "configs" / "retrieval.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    configured_dir = Path(config["output"]["index_dir"])
    assert configured_dir.resolve() != bpi.KNOWN_SYNTHETIC_DEMO_INDEX_DIR.resolve()


def test_default_config_input_points_at_real_msmarco_path():
    with open(PROJECT_ROOT / "configs" / "retrieval.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    train_path = Path(config["input"]["train_queries"])
    assert "processed" in train_path.parts and "msmarco_web_search" in train_path.parts
    assert train_path.name == "train_queries.parquet"


def test_main_missing_input_file_raises_clear_systemexit(tmp_path, monkeypatch):
    """This must fail on the missing-file check, BEFORE iter_parquet_rows
    is ever called -- so it must not require pyarrow."""
    config_path = tmp_path / "retrieval.yaml"
    missing_parquet = tmp_path / "does_not_exist.parquet"
    config_path.write_text(
        yaml.safe_dump(
            {
                "input": {"train_queries": str(missing_parquet)},
                "output": {
                    "index_dir": str(tmp_path / "out"),
                    "sqlite_filename": "idx.sqlite3",
                    "build_report_filename": "report.json",
                },
                "processing": {"batch_size": 1000, "sqlite_commit_batch_size": 1000},
                "matching": {"case_sensitive": False, "unicode_form": "NFKC"},
                "prefix_generation": {
                    "min_prefix_length": 1, "max_prefix_length": 20,
                    "include_whitespace_prefixes": True, "max_prefixes_per_query": 20,
                },
                "hot_prefix_cache": {"max_length": 6, "top_k": 50},
                "popularity": {"mode": "duplicate_count"},
                "logging": {"level": "INFO"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["build_prefix_index", "--config", str(config_path)])
    with pytest.raises(SystemExit, match="not found"):
        bpi.main()


def test_main_refuses_demo_dir_without_force(tmp_path, monkeypatch):
    """Wire-level check that main() actually calls the guard: point a
    config's output.index_dir straight at the demo directory and confirm
    main() exits via the guard's message, not by way of ever calling
    iter_parquet_rows (so, again, no pyarrow needed) -- the input file
    existence check must pass first, so give it a real (empty-ish) file."""
    fake_input = tmp_path / "train_queries.parquet"
    fake_input.write_bytes(b"not a real parquet file, just needs to exist")
    config_path = tmp_path / "retrieval.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "input": {"train_queries": str(fake_input)},
                "output": {
                    "index_dir": str(bpi.KNOWN_SYNTHETIC_DEMO_INDEX_DIR),
                    "sqlite_filename": "idx.sqlite3",
                    "build_report_filename": "report.json",
                },
                "processing": {"batch_size": 1000, "sqlite_commit_batch_size": 1000},
                "matching": {"case_sensitive": False, "unicode_form": "NFKC"},
                "prefix_generation": {
                    "min_prefix_length": 1, "max_prefix_length": 20,
                    "include_whitespace_prefixes": True, "max_prefixes_per_query": 20,
                },
                "hot_prefix_cache": {"max_length": 6, "top_k": 50},
                "popularity": {"mode": "duplicate_count"},
                "logging": {"level": "INFO"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["build_prefix_index", "--config", str(config_path)])
    with pytest.raises(SystemExit, match="synthetic/demo index"):
        bpi.main()


# ---------------------------------------------------------------------------
# Group 2: real Parquet-reading tests -- require pyarrow, skip cleanly
# without it (see module docstring). NOT run in this sandbox (no pyarrow,
# no network to install it); WILL run for real on the user's machine.
# ---------------------------------------------------------------------------


def _write_tiny_parquet(path: Path, rows: list[dict]) -> None:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    table = pa.table(
        {
            "qid": [r["qid"] for r in rows],
            "original_query": [r["original_query"] for r in rows],
            "normalized_query": [r["normalized_query"] for r in rows],
        }
    )
    pq.write_table(table, path)


def test_iter_parquet_rows_reads_all_rows_across_small_batches(tmp_path):
    pytest.importorskip("pyarrow")
    rows = [
        {"qid": f"Q{i:03d}", "original_query": f"query {i}", "normalized_query": f"query {i}"}
        for i in range(7)
    ]
    parquet_path = tmp_path / "tiny.parquet"
    _write_tiny_parquet(parquet_path, rows)

    # batch_size smaller than the row count exercises the multi-batch path.
    read_back = list(bpi.iter_parquet_rows(parquet_path, batch_size=2))
    assert len(read_back) == 7
    assert {r["qid"] for r in read_back} == {r["qid"] for r in rows}
    assert all(set(r.keys()) == {"qid", "original_query", "normalized_query"} for r in read_back)


def test_iter_parquet_rows_missing_required_column_raises(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    table = pa.table({"qid": ["Q1"], "original_query": ["x"]})  # missing normalized_query
    parquet_path = tmp_path / "bad.parquet"
    pq.write_table(table, parquet_path)
    with pytest.raises(ValueError, match="missing required column"):
        list(bpi.iter_parquet_rows(parquet_path, batch_size=100))


def test_end_to_end_build_from_small_real_parquet_file(tmp_path):
    """The parquet-input equivalent of tests/test_prefix_index.py's
    record-input tests: build a real (if tiny) .sqlite3 index FROM an
    actual .parquet file via the same code path build_prefix_index.py
    uses for the 9.2M-row real corpus, then check index creation, prefix
    lookup, a duplicate-normalized-query group, and a no-match prefix."""
    pytest.importorskip("pyarrow")
    rows = [
        {"qid": "Q001", "original_query": "machine learning", "normalized_query": "machine learning"},
        {"qid": "Q002", "original_query": "machine learning course", "normalized_query": "machine learning course"},
        {"qid": "Q003", "original_query": "machine learning tutorial", "normalized_query": "machine learning tutorial"},
        {"qid": "Q004", "original_query": "Machine Learning Tutorial", "normalized_query": "machine learning tutorial"},
        {"qid": "Q005", "original_query": "python tutorial", "normalized_query": "python tutorial"},
    ]
    parquet_path = tmp_path / "train_queries.parquet"
    _write_tiny_parquet(parquet_path, rows)

    with open(PROJECT_ROOT / "configs" / "retrieval.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    db_path = tmp_path / "test_real_index.sqlite3"
    from src.retrieval.index_builder import build_index_from_records

    parsed_rows = bpi.iter_parquet_rows(parquet_path, batch_size=2)
    stats = build_index_from_records(parsed_rows, db_path, config)

    assert stats.records_processed == 5
    assert stats.unique_normalized_queries == 4  # the Q003/Q004 duplicate group collapses
    assert stats.duplicate_normalized_queries == 1
    assert db_path.exists()

    index = PrefixIndex.from_config(
        {**config, "output": {**config["output"], "index_dir": str(db_path.parent), "sqlite_filename": db_path.name}}
    )
    try:
        results = index.get_candidates("machine lea", limit=10)
        queries = {c.normalized_query for c in results}
        assert queries == {"machine learning", "machine learning course", "machine learning tutorial"}

        dup = [c for c in index.get_candidates("machine learning tutorial", limit=10) if c.normalized_query == "machine learning tutorial"]
        assert len(dup) == 1
        assert dup[0].qid == "Q003"  # lexicographically smaller than Q004
        assert dup[0].duplicate_count == 1

        assert index.get_candidates("xyznonexistent", limit=10) == []
    finally:
        index.close()


def test_determinism_across_two_builds_from_the_same_parquet_file(tmp_path):
    pytest.importorskip("pyarrow")
    rows = [
        {"qid": f"Q{i:03d}", "original_query": f"topic {i} query", "normalized_query": f"topic {i} query"}
        for i in range(20)
    ]
    parquet_path = tmp_path / "train_queries.parquet"
    _write_tiny_parquet(parquet_path, rows)

    with open(PROJECT_ROOT / "configs" / "retrieval.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    from src.retrieval.index_builder import build_index_from_records

    db_path_a = tmp_path / "a.sqlite3"
    db_path_b = tmp_path / "b.sqlite3"
    build_index_from_records(bpi.iter_parquet_rows(parquet_path, batch_size=3), db_path_a, config)
    build_index_from_records(bpi.iter_parquet_rows(parquet_path, batch_size=7), db_path_b, config)

    index_a = PrefixIndex.from_config({**config, "output": {**config["output"], "index_dir": str(tmp_path), "sqlite_filename": "a.sqlite3"}})
    index_b = PrefixIndex.from_config({**config, "output": {**config["output"], "index_dir": str(tmp_path), "sqlite_filename": "b.sqlite3"}})
    try:
        for prefix in ["topic", "topic 1", "topic 1 query", "nomatch"]:
            a_results = [c.normalized_query for c in index_a.get_candidates(prefix, limit=50)]
            b_results = [c.normalized_query for c in index_b.get_candidates(prefix, limit=50)]
            assert a_results == b_results, f"non-deterministic results for prefix {prefix!r}"
    finally:
        index_a.close()
        index_b.close()
