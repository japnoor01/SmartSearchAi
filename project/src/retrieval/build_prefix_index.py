#!/usr/bin/env python3
"""
src/retrieval/build_prefix_index.py

Build script for the prefix candidate-generation index.

Usage:
    python -m src.retrieval.build_prefix_index --config configs/retrieval.yaml

Reads data/processed/msmarco_web_search/train_queries.parquet in bounded
Arrow record batches (never materializing the full 9.2M-row file as a
Python list), streams rows into src/retrieval/index_builder.py's
build_index_from_records(), and writes:

    <output.index_dir>/<output.sqlite_filename>   -- the SQLite index
    <output.index_dir>/<output.build_report_filename> -- this run's stats

This is the only file in src/retrieval/ that depends on pyarrow -- see
index_builder.py's module docstring for why the parquet-reading I/O layer
is kept separate from the indexing logic. `pyarrow` is imported lazily,
INSIDE `iter_parquet_rows()`, not at module import time -- a prior version
of this file imported it at the top and turned a missing `pyarrow` into a
module-level `SystemExit`, which meant simply `import
src.retrieval.build_prefix_index` (e.g. from a test, to exercise the CLI
argument parsing or the demo-index safety guard below, neither of which
touch Parquet) would abort the whole process in any environment without
pyarrow installed -- `pytest.importorskip("pyarrow")` can't catch a
`SystemExit`. Deferring the import fixes that: this module always imports
cleanly; only calling `iter_parquet_rows()` (i.e. actually building the
real index) requires pyarrow.

Safety guard: `data/indexes/prefix/` is this project's existing, already-
built SYNTHETIC/DEMO index (see docs/prefix_index.md) and must not be
silently overwritten by a real-data build. `configs/retrieval.yaml`'s
default `output.index_dir` now points at `data/indexes/prefix_real/`
instead, but as a second line of defense this script also refuses to
write into the known demo directory unless `--force` is passed explicitly
-- see `_guard_against_overwriting_demo_index()`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Iterator

import yaml

# Allow running as `python -m src.retrieval.build_prefix_index` from the
# project root without an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.retrieval.index_builder import build_index_from_records  # noqa: E402

LOG = logging.getLogger("build_prefix_index")

REQUIRED_COLUMNS = ["qid", "original_query", "normalized_query"]

# The permanent, already-built synthetic/demo index location (see
# docs/prefix_index.md). Never the default `output.index_dir` for a real
# build -- see _guard_against_overwriting_demo_index().
KNOWN_SYNTHETIC_DEMO_INDEX_DIR = Path("data/indexes/prefix")


def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def iter_parquet_rows(parquet_path: Path, batch_size: int) -> Iterator[dict]:
    """Stream rows out of a parquet file as plain dicts, in bounded Arrow
    record batches. Memory usage is O(batch_size), never O(file size) --
    this is how a 540MB / 9.2M-row file gets processed without ever being
    fully resident as Python objects.

    Raises SystemExit with an actionable message if pyarrow is not
    installed -- deferred to here (not module import time) so the rest of
    this module (CLI parsing, the demo-index safety guard) can be
    imported and unit-tested in environments without pyarrow.
    """
    try:
        import pyarrow.parquet as pq
    except ImportError as e:
        raise SystemExit(
            "pyarrow is required to read train_queries.parquet "
            "(pip install pyarrow --break-system-packages, or "
            "pip install -r requirements.txt). "
            "src/retrieval/index_builder.py itself has no pyarrow dependency, "
            "so tests and other input adapters are unaffected by this."
        ) from e

    pf = pq.ParquetFile(str(parquet_path))
    schema_names = set(pf.schema_arrow.names)
    missing = [c for c in REQUIRED_COLUMNS if c not in schema_names]
    if missing:
        raise ValueError(
            f"{parquet_path} is missing required column(s) {missing}. "
            f"Found columns: {sorted(schema_names)}"
        )
    for batch in pf.iter_batches(batch_size=batch_size, columns=REQUIRED_COLUMNS):
        # to_pylist() materializes only this one batch, not the whole file.
        for row in batch.to_pylist():
            yield row


def _guard_against_overwriting_demo_index(index_dir: Path, force: bool) -> None:
    """Refuse to build into data/indexes/prefix/ (the permanent synthetic
    demo index) unless --force is explicitly passed. Pure path comparison
    -- no pyarrow, no I/O beyond resolve() -- so this is fully unit-
    testable without pyarrow installed."""
    if force:
        return
    if index_dir.resolve() == KNOWN_SYNTHETIC_DEMO_INDEX_DIR.resolve():
        raise SystemExit(
            f"Refusing to build into {index_dir} -- this is the project's existing "
            "synthetic/demo index (see docs/prefix_index.md) and must not be "
            "silently overwritten. Point --index-dir (or configs/*.yaml -> "
            "output.index_dir) at a different location, such as "
            "data/indexes/prefix_real, or pass --force if you really mean to "
            "replace the demo index."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the SmartSearch AI prefix candidate index.")
    parser.add_argument("--config", type=Path, default=Path("configs/retrieval.yaml"))
    parser.add_argument(
        "--train-queries",
        type=Path,
        default=None,
        help="Override input.train_queries from the config file.",
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=None,
        help="Override output.index_dir from the config file (where the .sqlite3 + build report are written).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow writing into data/indexes/prefix/ (the existing synthetic/demo index). Off by default.",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    logging.basicConfig(
        level=getattr(logging, config.get("logging", {}).get("level", "INFO")),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    train_path = args.train_queries or Path(config["input"]["train_queries"])
    if not train_path.exists():
        raise SystemExit(
            f"Input parquet not found: {train_path}\n"
            f"This build script expects the output of the preprocessing stage "
            f"(src/data/preprocess_queries.py). Raw TSVs are never read directly here."
        )

    out_cfg = config["output"]
    index_dir = args.index_dir or Path(out_cfg["index_dir"])
    _guard_against_overwriting_demo_index(index_dir, force=args.force)
    db_path = index_dir / out_cfg["sqlite_filename"]
    report_path = index_dir / out_cfg["build_report_filename"]

    batch_size = config.get("processing", {}).get("batch_size", 50_000)

    LOG.info("building prefix index from %s -> %s", train_path, db_path)
    t0 = time.time()

    rows = iter_parquet_rows(train_path, batch_size=batch_size)
    stats = build_index_from_records(rows, db_path, config)

    elapsed = time.time() - t0
    LOG.info("build complete in %.1fs", elapsed)
    LOG.info("records processed:            %d", stats.records_processed)
    LOG.info("unique normalized queries:    %d", stats.unique_normalized_queries)
    LOG.info("duplicate normalized queries: %d", stats.duplicate_normalized_queries)
    LOG.info("prefixes generated (hot cache staging): %d", stats.prefixes_generated)
    LOG.info("hot cache rows:                %d", stats.hot_cache_rows)
    LOG.info("index size:                    %.1f MB", stats.index_size_bytes / (1024 * 1024))
    if stats.errors_skipped:
        LOG.warning("rows skipped due to errors: %d (see report for samples)", stats.errors_skipped)

    is_real_data = "processed" in train_path.parts and "msmarco_web_search" in train_path.parts
    report = {
        "note": (
            "REAL data build (MS MARCO Web Search train split)"
            if is_real_data
            else f"Custom-source build from {train_path} -- verify data provenance manually."
        ),
        "config_path": str(args.config),
        "train_queries_path": str(train_path),
        "db_path": str(db_path),
        **stats.as_dict(),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    LOG.info("build report written to %s", report_path)


if __name__ == "__main__":
    main()
