"""
tests/test_prefix_index.py

Builds a small SQLite index from the synthetic corpus (see
scripts/generate_synthetic_queries.py) once per test session via
index_builder.build_index_from_records() -- no pyarrow / Parquet file is
needed to run these tests, since index_builder.py's core is pure stdlib.

Run with:
    python -m pytest tests/test_prefix_index.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.generate_synthetic_queries import synthetic_records  # noqa: E402
from src.retrieval.index_builder import build_index_from_records  # noqa: E402
from src.retrieval.prefix_index import PrefixIndex, generate_prefixes, normalize_prefix  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def config() -> dict:
    with open(PROJECT_ROOT / "configs" / "retrieval.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def built_index(tmp_path_factory, config) -> PrefixIndex:
    db_path = tmp_path_factory.mktemp("prefix_index_test") / "test_index.sqlite3"
    records = list(synthetic_records(n_filler=500))
    build_index_from_records(records, db_path, config)
    index = PrefixIndex.from_config(
        {**config, "output": {**config["output"], "index_dir": str(db_path.parent), "sqlite_filename": db_path.name}}
    )
    yield index
    index.close()


# ---------------------------------------------------------------------------
# 1. Exact prefix
# ---------------------------------------------------------------------------


def test_exact_prefix_machine(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("machine", limit=100)
    assert len(results) > 0
    for c in results:
        assert c.normalized_query.lower().startswith("machine")


# ---------------------------------------------------------------------------
# 2. Longer prefix
# ---------------------------------------------------------------------------


def test_longer_prefix_machine_lea(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("machine lea", limit=100)
    assert len(results) > 0
    queries = {c.normalized_query for c in results}
    assert "machine learning" in queries
    assert "machine learning tutorial" in queries
    for c in results:
        assert c.normalized_query.lower().startswith("machine lea")


# ---------------------------------------------------------------------------
# 3. Prefix with (trailing) whitespace
# ---------------------------------------------------------------------------


def test_prefix_with_trailing_whitespace(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("machine l", limit=100)
    assert len(results) > 0
    queries = {c.normalized_query for c in results}
    # "machine learning..." queries and "machine learning tutorial" should
    # be present; "machine shop..." must NOT match "machine l".
    assert any(q.startswith("machine l") for q in queries)
    assert not any(q.startswith("machine s") for q in queries)


# ---------------------------------------------------------------------------
# 4. Case behavior
# ---------------------------------------------------------------------------


def test_case_insensitive_by_default(built_index: PrefixIndex) -> None:
    lower = built_index.get_candidates("machine learning", limit=100)
    upper = built_index.get_candidates("MACHINE LEARNING", limit=100)
    mixed = built_index.get_candidates("MaChInE LeArNiNg", limit=100)
    assert {c.normalized_query for c in lower} == {c.normalized_query for c in upper}
    assert {c.normalized_query for c in lower} == {c.normalized_query for c in mixed}
    assert len(lower) > 0


def test_normalize_prefix_case_folds_by_default() -> None:
    assert normalize_prefix("MACHINE") == normalize_prefix("machine") == "machine"


def test_normalize_prefix_case_sensitive_mode() -> None:
    assert normalize_prefix("MACHINE", case_sensitive=True) == "MACHINE"
    assert normalize_prefix("machine", case_sensitive=True) == "machine"


# ---------------------------------------------------------------------------
# 5. Unicode queries
# ---------------------------------------------------------------------------


def test_unicode_prefix_japanese(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("日本語", limit=10)
    assert len(results) > 0
    assert any(c.normalized_query.startswith("日本語") for c in results)


def test_unicode_prefix_cyrillic(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("москва", limit=10)
    assert len(results) > 0


def test_unicode_prefix_accented_latin(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("café", limit=10)
    assert len(results) > 0
    assert any(c.normalized_query.startswith("café") for c in results)


# ---------------------------------------------------------------------------
# 6. Punctuation
# ---------------------------------------------------------------------------


def test_punctuation_prefix(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("c++", limit=10)
    assert len(results) > 0
    assert any("c++" in c.normalized_query for c in results)


def test_punctuation_prefix_question_mark(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("what's the weather", limit=10)
    assert len(results) > 0


# ---------------------------------------------------------------------------
# 7. Code-like queries
# ---------------------------------------------------------------------------


def test_code_like_prefix_sql(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("SELECT * FROM", limit=10)
    assert len(results) > 0


def test_code_like_prefix_python_import(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("import pandas", limit=10)
    assert len(results) > 0


# ---------------------------------------------------------------------------
# 8. Empty prefix
# ---------------------------------------------------------------------------


def test_empty_prefix_returns_globally_popular(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("", limit=10)
    assert len(results) == 10
    # Deterministic: popularity DESC, normalized_query ASC -- so two calls agree.
    results_again = built_index.get_candidates("", limit=10)
    assert [c.normalized_query for c in results] == [c.normalized_query for c in results_again]


# ---------------------------------------------------------------------------
# 9. Prefix with no matches
# ---------------------------------------------------------------------------


def test_no_matches(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("xyznonexistentprefixzzz", limit=100)
    assert results == []


# ---------------------------------------------------------------------------
# 10 / 11 / 12. limit handling
# ---------------------------------------------------------------------------


def test_limit_1(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("machine", limit=1)
    assert len(results) == 1


def test_limit_5(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("machine", limit=5)
    assert len(results) == 5


def test_limit_100(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("machine", limit=100)
    assert len(results) <= 100
    assert len(results) > 5  # the synthetic corpus has more than 5 "machine..." queries


def test_limit_exceeds_available_returns_fewer(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("machine learning tutorial", limit=100)
    # This exact normalized query (plus its duplicate-qid group) is a single
    # unique normalized_query -- there cannot be 100 distinct matches.
    assert 0 < len(results) < 100


# ---------------------------------------------------------------------------
# 13. No duplicate normalized queries in results
# ---------------------------------------------------------------------------


def test_no_duplicate_normalized_queries_in_results(built_index: PrefixIndex) -> None:
    results = built_index.get_candidates("machine learning tutorial", limit=100)
    normalized = [c.normalized_query for c in results]
    assert len(normalized) == len(set(normalized))


def test_dedup_group_collapses_to_one_candidate(built_index: PrefixIndex) -> None:
    # "machine learning tutorial", "python tutorial", "data science course"
    # were each emitted under 3 distinct synthetic qids (see
    # scripts/generate_synthetic_queries.py::_DUPLICATE_GROUPS) plus once
    # more as a hand-authored topic query = 4 raw rows sharing one
    # normalized_query. get_candidates must surface it exactly once, with
    # duplicate_count reflecting the collapsed rows.
    results = built_index.get_candidates("python tutorial", limit=100)
    matches = [c for c in results if c.normalized_query == "python tutorial"]
    assert len(matches) == 1
    assert matches[0].duplicate_count == 3


# ---------------------------------------------------------------------------
# 14. Deterministic ordering
# ---------------------------------------------------------------------------


def test_deterministic_ordering(built_index: PrefixIndex) -> None:
    first = built_index.get_candidates("machine lea", limit=50)
    second = built_index.get_candidates("machine lea", limit=50)
    assert [c.normalized_query for c in first] == [c.normalized_query for c in second]
    # Explicitly verify the popularity DESC, normalized_query ASC contract.
    for a, b in zip(first, first[1:]):
        assert a.popularity > b.popularity or (
            a.popularity == b.popularity and a.normalized_query <= b.normalized_query
        )


# ---------------------------------------------------------------------------
# 15. qid / original_query traceability
# ---------------------------------------------------------------------------


def test_qid_and_original_query_traceable(built_index: PrefixIndex) -> None:
    # NOTE: "machine learning tutorial" is itself a prefix of the distinct
    # query "machine learning tutorial for beginners", so both legitimately
    # match -- traceability is checked on the exact-match row.
    results = built_index.get_candidates("machine learning tutorial", limit=10)
    matches = [c for c in results if c.normalized_query == "machine learning tutorial"]
    assert len(matches) == 1
    c = matches[0]
    assert c.qid.startswith("SYN")
    assert c.original_query == "machine learning tutorial"
    assert c.normalized_query == "machine learning tutorial"


def test_get_top_candidates_is_get_candidates_with_default_limit(built_index: PrefixIndex) -> None:
    top = built_index.get_top_candidates("machine", limit=5)
    full = built_index.get_candidates("machine", limit=5)
    assert [c.normalized_query for c in top] == [c.normalized_query for c in full]


# ---------------------------------------------------------------------------
# generate_prefixes() unit tests (pure function, no index needed)
# ---------------------------------------------------------------------------


def test_generate_prefixes_respects_min_max_length() -> None:
    prefixes = generate_prefixes("machine", min_prefix_length=2, max_prefix_length=4, max_prefixes_per_query=100)
    assert prefixes == ["ma", "mac", "mach"]


def test_generate_prefixes_respects_max_prefixes_per_query() -> None:
    prefixes = generate_prefixes("machine learning", min_prefix_length=1, max_prefix_length=20, max_prefixes_per_query=3)
    assert prefixes == ["m", "ma", "mac"]


def test_generate_prefixes_whitespace_toggle() -> None:
    with_ws = generate_prefixes(
        "machine learning", min_prefix_length=7, max_prefix_length=8,
        include_whitespace_prefixes=True, max_prefixes_per_query=100,
    )
    without_ws = generate_prefixes(
        "machine learning", min_prefix_length=7, max_prefix_length=8,
        include_whitespace_prefixes=False, max_prefixes_per_query=100,
    )
    assert "machine " in with_ws
    assert "machine " not in without_ws
    assert "machine" in without_ws


def test_generate_prefixes_empty_input() -> None:
    assert generate_prefixes("") == []


def test_generate_prefixes_never_exceeds_query_length() -> None:
    prefixes = generate_prefixes("hi", min_prefix_length=1, max_prefix_length=20, max_prefixes_per_query=100)
    assert prefixes == ["h", "hi"]


# ---------------------------------------------------------------------------
# normalize_prefix() unit tests
# ---------------------------------------------------------------------------


def test_normalize_prefix_preserves_trailing_space() -> None:
    assert normalize_prefix("machine ") == "machine "


def test_normalize_prefix_strips_leading_space() -> None:
    assert normalize_prefix("  machine") == "machine"


def test_normalize_prefix_collapses_internal_whitespace() -> None:
    assert normalize_prefix("machine    learning") == "machine learning"


def test_normalize_prefix_unicode_nfkc() -> None:
    # Full-width Latin "ｍａｃｈｉｎｅ" (U+FF4D...) should fold to ASCII
    # "machine" under NFKC, matching preprocessing's normalization.
    assert normalize_prefix("ｍａｃｈｉｎｅ") == "machine"
