"""
src/retrieval/prefix_index.py

Query-time prefix candidate-generation index for the SmartSearch AI
autocomplete engine.

This module is intentionally free of any Parquet/Arrow dependency -- it only
needs the Python standard library (sqlite3, unicodedata, re) so that it can
be imported, unit-tested, and used to serve traffic without pulling in the
(larger, build-time-only) Parquet reading stack. See build_prefix_index.py
for the index build pipeline and index_builder.py for the shared
build-time indexing core.

Architecture summary (full rationale in docs/prefix_index.md):

  - The index is a single SQLite database file on disk. SQLite gives us a
    durable, memory-mapped-friendly B-tree index without loading 9.2M rows
    into a Python object graph, with zero extra infrastructure to run.

  - `queries` is a WITHOUT ROWID table keyed on (match_key, normalized_query)
    -- i.e. physically clustered/sorted by the case-folded matching key.
    ANY prefix, of ANY length, can be served correctly and efficiently from
    this table alone via a range scan:

        WHERE match_key >= :prefix AND match_key < :upper_bound

    where :upper_bound is :prefix with its last character's code point
    incremented by one. Because UTF-8 byte order equals Unicode code point
    order, this range scan is correct for arbitrary Unicode prefixes and
    only ever needs one index seek plus a small forward scan -- no need to
    materialize a separate row per prefix length.

  - `hot_prefix_cache` is a small, pre-ranked top-K table for the shortest,
    highest-QPS prefixes only (configurable, default length <= 6). It
    exists purely as a latency/CPU optimization for the hottest keys (a
    single-character prefix like "m" can match millions of rows; ranking
    those live on every keystroke would be wasteful when the top 50 never
    change between builds). Every other prefix is served by the range scan
    above, which is why this cache is allowed to be small and bounded.

  - Candidates are always deduplicated by normalized_query (it is the
    primary-key component), and ordering is always deterministic:
    popularity DESC, normalized_query ASC.
"""

from __future__ import annotations

import logging
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("prefix_index")

# ---------------------------------------------------------------------------
# Schema (shared by index_builder.py, which creates these tables)
# ---------------------------------------------------------------------------

QUERIES_TABLE = "queries"
HOT_CACHE_TABLE = "hot_prefix_cache"
META_TABLE = "index_meta"

CREATE_QUERIES_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {QUERIES_TABLE} (
    match_key         TEXT NOT NULL,
    normalized_query  TEXT NOT NULL,
    qid               TEXT NOT NULL,
    original_query    TEXT NOT NULL,
    duplicate_qids    TEXT NOT NULL DEFAULT '',
    duplicate_count   INTEGER NOT NULL DEFAULT 0,
    popularity        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (match_key, normalized_query)
) WITHOUT ROWID;
"""

CREATE_HOT_CACHE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {HOT_CACHE_TABLE} (
    prefix            TEXT NOT NULL,
    rank              INTEGER NOT NULL,
    match_key         TEXT NOT NULL,
    normalized_query  TEXT NOT NULL,
    qid               TEXT NOT NULL,
    original_query    TEXT NOT NULL,
    duplicate_count   INTEGER NOT NULL,
    popularity        INTEGER NOT NULL,
    PRIMARY KEY (prefix, rank)
) WITHOUT ROWID;
"""

CREATE_META_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {META_TABLE} (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Candidate record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One autocomplete candidate returned by PrefixIndex.get_candidates()."""

    qid: str
    original_query: str
    normalized_query: str
    popularity: int
    duplicate_count: int
    matched_prefix: str
    prefix_match_length: int
    source: str  # "hot_cache" or "range_scan" -- useful for debugging/metrics

    def to_dict(self) -> dict:
        return {
            "qid": self.qid,
            "original_query": self.original_query,
            "normalized_query": self.normalized_query,
            "popularity": self.popularity,
            "duplicate_count": self.duplicate_count,
            "matched_prefix": self.matched_prefix,
            "prefix_match_length": self.prefix_match_length,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Normalization -- MUST mirror src/data/preprocess_queries.py's
# normalize_query() for stored text, with one deliberate difference: an
# in-progress user prefix's *trailing* whitespace is meaningful (it marks
# "start of the next word") and must not be trimmed away.
# ---------------------------------------------------------------------------

import re as _re

_WHITESPACE_RUN_RE = _re.compile(r"\s+", _re.UNICODE)


def normalize_prefix(text: str, case_sensitive: bool = False, unicode_form: str = "NFKC") -> str:
    """Normalize a user-typed, possibly-incomplete prefix for matching.

    Mirrors preprocess_queries.normalize_query()'s Unicode-normalization and
    internal-whitespace-collapsing behavior so that a typed prefix and the
    indexed normalized_query it should match are directly comparable.
    Differs from that function in one place, deliberately: only *leading*
    whitespace is stripped. Trailing whitespace is preserved (collapsed to
    at most one space) because "machine " is a distinct, meaningful prefix
    from "machine" in an autocomplete context -- it means the user has
    finished typing a word.
    """
    if not text:
        return ""
    normalized = unicodedata.normalize(unicode_form, text)
    normalized = normalized.lstrip()
    normalized = _WHITESPACE_RUN_RE.sub(" ", normalized)
    if not case_sensitive:
        normalized = normalized.casefold()
    return normalized


def generate_prefixes(
    match_key: str,
    min_prefix_length: int = 1,
    max_prefix_length: int = 20,
    include_whitespace_prefixes: bool = True,
    max_prefixes_per_query: int = 20,
) -> list[str]:
    """Generate the bounded set of character prefixes materialized for one
    query's match_key at index-build time.

    Controlled entirely by the four parameters below (see
    configs/retrieval.yaml -> prefix_generation for the defaults and the
    rationale for each):

      - min_prefix_length / max_prefix_length: the inclusive range of
        prefix lengths considered. Never produces a prefix shorter than
        min_prefix_length or longer than max_prefix_length (or the full
        match_key, whichever is shorter).
      - include_whitespace_prefixes: if False, a prefix ending exactly on a
        whitespace character (e.g. "machine " for "machine learning") is
        skipped -- some deployments prefer to only suggest mid-word.
      - max_prefixes_per_query: hard cap on the number of prefixes
        returned, protecting against pathologically long queries (this
        corpus has normalized queries up to 1,520 characters) generating
        excessive prefix counts.

    Returns prefixes in increasing length order. Note this function
    operates on whatever string it is given (typically match_key, the
    case-folded matching form) -- it does not itself normalize text.
    """
    if not match_key or min_prefix_length < 1 or max_prefix_length < min_prefix_length:
        return []

    upper = min(max_prefix_length, len(match_key))
    prefixes: list[str] = []
    for length in range(min_prefix_length, upper + 1):
        prefix = match_key[:length]
        if not include_whitespace_prefixes and prefix[-1].isspace():
            continue
        prefixes.append(prefix)
        if len(prefixes) >= max_prefixes_per_query:
            break
    return prefixes


def _range_upper_bound(prefix: str) -> Optional[str]:
    """Compute the exclusive upper bound for a BINARY-collation range scan
    that matches every string starting with `prefix`.

    Works by incrementing the code point of the last character. This is
    correct (not just a heuristic) because UTF-8 byte-wise ordering is
    guaranteed to match Unicode code point ordering, and SQLite's default
    TEXT collation (BINARY) compares TEXT values as their UTF-8 encoded
    bytes. Returns None in the (astronomically rare) case where the last
    character is already the maximum Unicode code point (U+10FFFF) and no
    single-character increment is possible; callers fall back to a
    LIKE-based scan in that case.
    """
    if not prefix:
        return None
    last_cp = ord(prefix[-1])
    if last_cp >= 0x10FFFF:
        return None
    return prefix[:-1] + chr(last_cp + 1)


# ---------------------------------------------------------------------------
# Query-time index
# ---------------------------------------------------------------------------


class PrefixIndex:
    """Read-only handle to a built prefix index (a SQLite database file
    produced by build_prefix_index.py / index_builder.build_index_from_records).

    Thread-safety: a PrefixIndex opens its SQLite connection with
    check_same_thread=False and the database itself is opened read-only, so
    a single instance can safely be shared across threads for read-only
    querying, which is the only operation this class exposes.
    """

    def __init__(
        self,
        db_path: str | Path,
        case_sensitive: bool = False,
        unicode_form: str = "NFKC",
        hot_cache_max_length: int = 6,
        hot_cache_top_k: int = 50,
    ) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Prefix index not found at {self.db_path}. "
                f"Build it first with: python -m src.retrieval.build_prefix_index --config configs/retrieval.yaml"
            )
        self.case_sensitive = case_sensitive
        self.unicode_form = unicode_form
        self.hot_cache_max_length = hot_cache_max_length
        self.hot_cache_top_k = hot_cache_top_k

        uri = f"file:{self.db_path}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA query_only = TRUE;")

    @classmethod
    def from_config(cls, config: dict) -> "PrefixIndex":
        """Build a PrefixIndex from a parsed configs/retrieval.yaml dict."""
        out_cfg = config["output"]
        db_path = Path(out_cfg["index_dir"]) / out_cfg["sqlite_filename"]
        match_cfg = config.get("matching", {})
        cache_cfg = config.get("hot_prefix_cache", {})
        return cls(
            db_path=db_path,
            case_sensitive=match_cfg.get("case_sensitive", False),
            unicode_form=match_cfg.get("unicode_form", "NFKC"),
            hot_cache_max_length=cache_cfg.get("max_length", 6),
            hot_cache_top_k=cache_cfg.get("top_k", 50),
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PrefixIndex":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- internal helpers ----------------------------------------------

    def _rows_from_hot_cache(self, normalized: str, limit: int) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            f"""
            SELECT match_key, normalized_query, qid, original_query,
                   duplicate_count, popularity
            FROM {HOT_CACHE_TABLE}
            WHERE prefix = ?
            ORDER BY rank ASC
            LIMIT ?
            """,
            (normalized, limit),
        )
        return cur.fetchall()

    def _rows_from_range_scan(self, normalized: str, limit: int) -> list[sqlite3.Row]:
        upper = _range_upper_bound(normalized)
        if upper is not None:
            cur = self._conn.execute(
                f"""
                SELECT match_key, normalized_query, qid, original_query,
                       duplicate_count, popularity
                FROM {QUERIES_TABLE}
                WHERE match_key >= ? AND match_key < ?
                ORDER BY popularity DESC, normalized_query ASC
                LIMIT ?
                """,
                (normalized, upper, limit),
            )
        else:
            # Extremely rare edge case: prefix ends in U+10FFFF, no upper
            # bound expressible as a single-character increment.
            cur = self._conn.execute(
                f"""
                SELECT match_key, normalized_query, qid, original_query,
                       duplicate_count, popularity
                FROM {QUERIES_TABLE}
                WHERE match_key >= ? AND substr(match_key, 1, ?) = ?
                ORDER BY popularity DESC, normalized_query ASC
                LIMIT ?
                """,
                (normalized, len(normalized), normalized, limit),
            )
        return cur.fetchall()

    def _rows_for_empty_prefix(self, limit: int) -> list[sqlite3.Row]:
        """Every string has the empty string as a prefix. We treat an
        (post-normalization) empty typed prefix as "show globally popular
        queries" rather than an error -- a reasonable, documented default
        for a blank autocomplete box."""
        cur = self._conn.execute(
            f"""
            SELECT match_key, normalized_query, qid, original_query,
                   duplicate_count, popularity
            FROM {QUERIES_TABLE}
            ORDER BY popularity DESC, normalized_query ASC
            LIMIT ?
            """,
            (limit,),
        )
        return cur.fetchall()

    @staticmethod
    def _rows_to_candidates(rows: list[sqlite3.Row], matched_prefix: str, source: str) -> list[Candidate]:
        return [
            Candidate(
                qid=row["qid"],
                original_query=row["original_query"],
                normalized_query=row["normalized_query"],
                popularity=row["popularity"],
                duplicate_count=row["duplicate_count"],
                matched_prefix=matched_prefix,
                prefix_match_length=len(matched_prefix),
                source=source,
            )
            for row in rows
        ]

    # -- public API -------------------------------------------------------

    def get_candidates(self, prefix: str, limit: int = 100) -> list[Candidate]:
        """Return up to `limit` candidate queries whose normalized text
        starts with `prefix` (after applying the same normalization used at
        index-build time), ranked by popularity (deterministic tie-break:
        normalized_query ascending). Never returns duplicate
        normalized_query values. Returns fewer than `limit` when fewer
        candidates exist; returns [] on no matches or on invalid input.
        """
        if limit <= 0:
            return []
        if prefix is None:
            return []

        normalized = normalize_prefix(prefix, self.case_sensitive, self.unicode_form)

        if normalized == "":
            rows = self._rows_for_empty_prefix(limit)
            return self._rows_to_candidates(rows, matched_prefix="", source="range_scan")

        use_cache = len(normalized) <= self.hot_cache_max_length
        if use_cache:
            cache_limit = max(limit, 1)
            rows = self._rows_from_hot_cache(normalized, cache_limit)
            # The cache is only a *complete* answer when it did not hit its
            # own top_k cap -- otherwise there may be lower-ranked real
            # matches beyond what was cached that the caller's `limit`
            # still needs.
            cache_is_complete = len(rows) < self.hot_cache_top_k
            if cache_is_complete or len(rows) >= limit:
                return self._rows_to_candidates(rows[:limit], matched_prefix=normalized, source="hot_cache")
            # Fall through to the authoritative range scan.

        rows = self._rows_from_range_scan(normalized, limit)
        return self._rows_to_candidates(rows, matched_prefix=normalized, source="range_scan")

    def get_top_candidates(self, prefix: str, limit: int = 5) -> list[Candidate]:
        """Convenience wrapper around get_candidates() for the baseline
        autocomplete API's default "show me the top few" use case."""
        return self.get_candidates(prefix, limit=limit)
