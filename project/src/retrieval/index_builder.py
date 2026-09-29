"""
src/retrieval/index_builder.py

Build-time core for the prefix candidate-generation index. Deliberately
free of any Parquet/Arrow dependency: it consumes a plain iterable of row
dicts (qid, original_query, normalized_query) and writes a SQLite index.

build_prefix_index.py is a thin adapter on top of this module that streams
rows out of train_queries.parquet via pyarrow. Separating I/O from indexing
logic this way means:

  1. The indexing logic (dedup, popularity, prefix generation, hot-cache
     ranking) can be unit-tested with small in-memory record lists, with no
     Parquet file and no pyarrow installation required.
  2. The same core could be pointed at a different input format later
     (CSV, JSON Lines, a database cursor, ...) without touching this file.

Two-phase build, both streamed / batched to keep memory flat:

  Phase 1 (ingest): stream input rows -> UPSERT into `queries`, keyed on
    (match_key, normalized_query). Duplicate normalized queries (same
    match_key + normalized_query, different qid) are folded into a single
    row: the lexicographically smallest qid becomes the canonical
    `qid`/`original_query` for that row (deterministic, reproducible), and
    every other qid is appended to `duplicate_qids` so no qid is silently
    dropped -- see docs/prefix_index.md section 5 for the rationale.

  Phase 2 (hot-cache build): stream the now-deduplicated `queries` table
    (NOT the original 9.2M raw rows) through generate_prefixes(), capped at
    hot_prefix_cache.max_length, into a temporary raw-candidates table, then
    use a single SQL window-function query to keep only the top_k rows per
    prefix (by popularity, then normalized_query) in `hot_prefix_cache`. The
    temporary table is dropped immediately after.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from src.retrieval.prefix_index import (
    CREATE_HOT_CACHE_TABLE_SQL,
    CREATE_META_TABLE_SQL,
    CREATE_QUERIES_TABLE_SQL,
    HOT_CACHE_TABLE,
    META_TABLE,
    QUERIES_TABLE,
    generate_prefixes,
    normalize_prefix,
)

LOG = logging.getLogger("index_builder")

_RAW_PREFIX_TABLE = "_prefix_raw_staging"
_PROGRESS_LOG_EVERY = 500_000


@dataclass
class BuildStats:
    """Everything the build script needs to print/report at the end. Filled
    in as the build progresses so a partial run still has useful numbers."""

    records_processed: int = 0
    unique_normalized_queries: int = 0
    duplicate_normalized_queries: int = 0
    prefixes_generated: int = 0
    hot_cache_rows: int = 0
    ingest_seconds: float = 0.0
    hot_cache_seconds: float = 0.0
    total_seconds: float = 0.0
    index_size_bytes: int = 0
    errors_skipped: int = 0
    row_error_samples: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "records_processed": self.records_processed,
            "unique_normalized_queries": self.unique_normalized_queries,
            "duplicate_normalized_queries": self.duplicate_normalized_queries,
            "prefixes_generated": self.prefixes_generated,
            "hot_cache_rows": self.hot_cache_rows,
            "ingest_seconds": round(self.ingest_seconds, 3),
            "hot_cache_build_seconds": round(self.hot_cache_seconds, 3),
            "total_build_seconds": round(self.total_seconds, 3),
            "index_size_bytes": self.index_size_bytes,
            "errors_skipped": self.errors_skipped,
            "row_error_samples": self.row_error_samples[:10],
        }


def _open_write_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    # Build-time pragmas: durability is irrelevant for an index we can
    # always rebuild from the Parquet source of truth, so trade it for
    # speed. WAL is unnecessary for a single-writer batch build.
    conn.execute("PRAGMA synchronous = OFF;")
    conn.execute("PRAGMA journal_mode = MEMORY;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_QUERIES_TABLE_SQL)
    conn.execute(CREATE_HOT_CACHE_TABLE_SQL)
    conn.execute(CREATE_META_TABLE_SQL)
    conn.commit()


_UPSERT_QUERY_SQL = f"""
INSERT INTO {QUERIES_TABLE}
    (match_key, normalized_query, qid, original_query, duplicate_qids, duplicate_count, popularity)
VALUES
    (:match_key, :normalized_query, :qid, :original_query, '', 0, 1)
ON CONFLICT(match_key, normalized_query) DO UPDATE SET
    duplicate_count = duplicate_count + 1,
    qid = CASE WHEN excluded.qid < {QUERIES_TABLE}.qid THEN excluded.qid ELSE {QUERIES_TABLE}.qid END,
    original_query = CASE WHEN excluded.qid < {QUERIES_TABLE}.qid THEN excluded.original_query ELSE {QUERIES_TABLE}.original_query END,
    duplicate_qids = CASE
        WHEN {QUERIES_TABLE}.duplicate_qids = '' THEN
            CASE WHEN excluded.qid < {QUERIES_TABLE}.qid THEN {QUERIES_TABLE}.qid ELSE excluded.qid END
        ELSE {QUERIES_TABLE}.duplicate_qids || ',' ||
            CASE WHEN excluded.qid < {QUERIES_TABLE}.qid THEN {QUERIES_TABLE}.qid ELSE excluded.qid END
    END
;
"""


def ingest_records(
    conn: sqlite3.Connection,
    records: Iterable[dict],
    *,
    case_sensitive: bool,
    unicode_form: str,
    commit_batch_size: int,
    stats: BuildStats,
) -> None:
    """Phase 1: stream `records` (dicts with qid/original_query/
    normalized_query keys) into the `queries` table, deduplicating on
    (match_key, normalized_query) as described in the module docstring.
    """
    t0 = time.time()
    batch: list[dict] = []
    cur = conn.cursor()

    def flush() -> None:
        if not batch:
            return
        cur.executemany(_UPSERT_QUERY_SQL, batch)
        conn.commit()
        batch.clear()

    for row in records:
        try:
            qid = str(row["qid"])
            original_query = str(row["original_query"])
            normalized_query = str(row["normalized_query"])
            if normalized_query == "":
                raise ValueError("empty normalized_query")
            match_key = normalize_prefix(normalized_query, case_sensitive, unicode_form)
            if match_key == "":
                raise ValueError("normalized_query collapsed to empty match_key")
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: one bad row must not kill a 9.2M-row build
            stats.errors_skipped += 1
            if len(stats.row_error_samples) < 10:
                stats.row_error_samples.append(f"{row!r}: {exc}")
            continue

        batch.append(
            {
                "match_key": match_key,
                "normalized_query": normalized_query,
                "qid": qid,
                "original_query": original_query,
            }
        )
        stats.records_processed += 1
        if len(batch) >= commit_batch_size:
            flush()
            LOG.info("ingested %d records...", stats.records_processed)

    flush()

    row = conn.execute(f"SELECT COUNT(*) FROM {QUERIES_TABLE}").fetchone()
    stats.unique_normalized_queries = row[0]
    stats.duplicate_normalized_queries = stats.records_processed - stats.unique_normalized_queries
    stats.ingest_seconds = time.time() - t0


def _iter_deduped_queries(conn: sqlite3.Connection) -> Iterator[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        f"SELECT match_key, normalized_query, qid, original_query, duplicate_count, popularity FROM {QUERIES_TABLE}"
    )
    while True:
        rows = cur.fetchmany(10_000)
        if not rows:
            break
        for r in rows:
            yield r


def finalize_popularity(conn: sqlite3.Connection, mode: str) -> None:
    """Compute the final `popularity` column from `duplicate_count`
    according to configs/retrieval.yaml -> popularity.mode. See
    docs/prefix_index.md section 4 for why these are the only two options.
    """
    if mode == "duplicate_count":
        conn.execute(f"UPDATE {QUERIES_TABLE} SET popularity = 1 + duplicate_count;")
    elif mode == "neutral":
        conn.execute(f"UPDATE {QUERIES_TABLE} SET popularity = 1;")
    else:
        raise ValueError(f"Unknown popularity.mode: {mode!r} (expected 'neutral' or 'duplicate_count')")
    conn.commit()


def build_hot_prefix_cache(
    conn: sqlite3.Connection,
    *,
    min_prefix_length: int,
    hot_cache_max_length: int,
    include_whitespace_prefixes: bool,
    max_prefixes_per_query: int,
    top_k: int,
    commit_batch_size: int,
    stats: BuildStats,
) -> None:
    """Phase 2: generate bounded prefixes for every deduplicated query
    (via generate_prefixes) up to hot_cache_max_length, stage them, then
    keep only the top_k rows per prefix using a window function.
    """
    t0 = time.time()
    conn.execute(f"DROP TABLE IF EXISTS {_RAW_PREFIX_TABLE};")
    conn.execute(
        f"""
        CREATE TABLE {_RAW_PREFIX_TABLE} (
            prefix TEXT NOT NULL,
            match_key TEXT NOT NULL,
            normalized_query TEXT NOT NULL,
            qid TEXT NOT NULL,
            original_query TEXT NOT NULL,
            duplicate_count INTEGER NOT NULL,
            popularity INTEGER NOT NULL
        );
        """
    )
    conn.commit()

    insert_sql = f"""
        INSERT INTO {_RAW_PREFIX_TABLE}
            (prefix, match_key, normalized_query, qid, original_query, duplicate_count, popularity)
        VALUES (:prefix, :match_key, :normalized_query, :qid, :original_query, :duplicate_count, :popularity)
    """
    batch: list[dict] = []
    cur = conn.cursor()

    def flush() -> None:
        if not batch:
            return
        cur.executemany(insert_sql, batch)
        conn.commit()
        batch.clear()

    effective_max_len = min(hot_cache_max_length, 10 ** 9)  # (readability no-op; documents intent explicitly)
    # At real-corpus scale (9.2M unique queries), this phase can generate
    # tens of millions of staging rows (up to hot_cache_max_length prefixes
    # per query) before the window-function query below collapses them down
    # to the much smaller final hot_prefix_cache table -- see
    # docs/prefix_index.md, "Rebuilding against the real dataset", for the
    # corrected order-of-magnitude estimate. With no progress output at all,
    # a long-running real build looks identical to a hung one, so log every
    # _PROGRESS_LOG_EVERY source queries processed (not staging rows, since
    # that count is far less useful to watch).
    queries_processed = 0
    for row in _iter_deduped_queries(conn):
        prefixes = generate_prefixes(
            row["match_key"],
            min_prefix_length=min_prefix_length,
            max_prefix_length=effective_max_len,
            include_whitespace_prefixes=include_whitespace_prefixes,
            max_prefixes_per_query=max_prefixes_per_query,
        )
        for p in prefixes:
            batch.append(
                {
                    "prefix": p,
                    "match_key": row["match_key"],
                    "normalized_query": row["normalized_query"],
                    "qid": row["qid"],
                    "original_query": row["original_query"],
                    "duplicate_count": row["duplicate_count"],
                    "popularity": row["popularity"],
                }
            )
            stats.prefixes_generated += 1
        queries_processed += 1
        if queries_processed % _PROGRESS_LOG_EVERY == 0:
            LOG.info(
                "hot-cache staging: %d queries processed, %d staging rows so far...",
                queries_processed, stats.prefixes_generated,
            )
        if len(batch) >= commit_batch_size:
            flush()

    flush()
    LOG.info(
        "hot-cache staging complete: %d queries, %d staging rows. "
        "Building staging index and ranking top_k=%d per prefix (single SQL "
        "statement -- no further progress output until it completes)...",
        queries_processed, stats.prefixes_generated, top_k,
    )
    conn.execute(f"CREATE INDEX _prefix_raw_idx ON {_RAW_PREFIX_TABLE}(prefix, popularity DESC, normalized_query ASC);")
    conn.commit()

    conn.execute(
        f"""
        INSERT INTO {HOT_CACHE_TABLE}
            (prefix, rank, match_key, normalized_query, qid, original_query, duplicate_count, popularity)
        SELECT prefix, rnk, match_key, normalized_query, qid, original_query, duplicate_count, popularity
        FROM (
            SELECT
                prefix, match_key, normalized_query, qid, original_query, duplicate_count, popularity,
                ROW_NUMBER() OVER (
                    PARTITION BY prefix
                    ORDER BY popularity DESC, normalized_query ASC
                ) AS rnk
            FROM {_RAW_PREFIX_TABLE}
        )
        WHERE rnk <= ?;
        """,
        (top_k,),
    )
    conn.commit()

    conn.execute(f"DROP TABLE {_RAW_PREFIX_TABLE};")
    conn.commit()

    row = conn.execute(f"SELECT COUNT(*) FROM {HOT_CACHE_TABLE}").fetchone()
    stats.hot_cache_rows = row[0]
    stats.hot_cache_seconds = time.time() - t0


def build_index_from_records(
    records: Iterable[dict],
    db_path: str | Path,
    config: dict,
) -> BuildStats:
    """End-to-end build: ingest -> finalize popularity -> hot cache ->
    indexes -> ANALYZE. This is the function both build_prefix_index.py
    (real Parquet input) and the test suite (synthetic in-memory input)
    call -- see the module docstring for why it is split out this way.
    """
    t_start = time.time()
    db_path = Path(db_path)
    stats = BuildStats()

    match_cfg = config.get("matching", {})
    prefix_cfg = config.get("prefix_generation", {})
    cache_cfg = config.get("hot_prefix_cache", {})
    pop_cfg = config.get("popularity", {})
    proc_cfg = config.get("processing", {})

    conn = _open_write_connection(db_path)
    try:
        _create_schema(conn)

        ingest_records(
            conn,
            records,
            case_sensitive=match_cfg.get("case_sensitive", False),
            unicode_form=match_cfg.get("unicode_form", "NFKC"),
            commit_batch_size=proc_cfg.get("sqlite_commit_batch_size", 20_000),
            stats=stats,
        )

        finalize_popularity(conn, pop_cfg.get("mode", "duplicate_count"))

        build_hot_prefix_cache(
            conn,
            min_prefix_length=prefix_cfg.get("min_prefix_length", 1),
            hot_cache_max_length=cache_cfg.get("max_length", 6),
            include_whitespace_prefixes=prefix_cfg.get("include_whitespace_prefixes", True),
            max_prefixes_per_query=prefix_cfg.get("max_prefixes_per_query", 20),
            top_k=cache_cfg.get("top_k", 50),
            commit_batch_size=proc_cfg.get("sqlite_commit_batch_size", 20_000),
            stats=stats,
        )

        LOG.info("creating secondary index on queries(popularity DESC)...")
        conn.execute(f"CREATE INDEX idx_queries_popularity ON {QUERIES_TABLE}(popularity DESC, normalized_query ASC);")
        conn.commit()

        conn.execute(
            f"INSERT OR REPLACE INTO {META_TABLE} (key, value) VALUES ('build_stats', ?)",
            (str(stats.as_dict()),),
        )
        conn.commit()

        LOG.info("running ANALYZE...")
        conn.execute("ANALYZE;")
        conn.commit()
        conn.execute("VACUUM;")
    finally:
        conn.close()

    stats.index_size_bytes = db_path.stat().st_size
    stats.total_seconds = time.time() - t_start
    return stats
