# Prefix Candidate-Generation Index

Design doc for `src/retrieval/prefix_index.py`, `index_builder.py`, and
`build_prefix_index.py` -- the real-time autocomplete candidate generator
for SmartSearchAI, built on top of the 9,206,475-row
`data/processed/msmarco_web_search/train_queries.parquet` produced by the
preprocessing stage.

**A note on this document's data, up front, since it affects everything
below:** this container has neither `pyarrow` installed nor network access.
The real `train_queries.parquet` (541,379,312 bytes, 9,206,475 rows) and
`dev_queries.parquet` (9,253 rows) now exist on the user's own machine
(produced by the preprocessing stage in their environment) but were never
uploaded here -- by design; see "Two index locations" below. Every number
in this doc that says "synthetic" comes from a small stand-in corpus
(`scripts/generate_synthetic_queries.py`, ~20k rows, 234 unique queries)
generated specifically to exercise every edge case in the spec (Unicode,
punctuation, code-like queries, duplicate normalized queries, a dense
"machine lea..." cluster). The build/query/benchmark code itself is the
real, production implementation -- only the input data used *in this
session* is a stand-in. See "Validation" and the final report for exactly
what was and wasn't run against real data, and "Rebuilding against the
real dataset" below for the exact commands the user runs locally, where
both `pyarrow` and the real files are actually available.

## Two index locations

This project now deliberately keeps **two separate, independently
buildable indexes**, never the same directory:

| | Location | Config | Built from | Status |
|---|---|---|---|---|
| Synthetic/demo | `data/indexes/prefix/` | (built via `build_index_from_records()` directly, no config-driven Parquet read) | `scripts/generate_synthetic_queries.py`, ~20k rows | Already built, permanent, used by `tests/test_prefix_index.py` and this doc's "Validation" section. **Never overwritten by a real-data build.** |
| Real / production | `data/indexes/prefix_real/` | `configs/retrieval.yaml` (`output.index_dir`) | `data/processed/msmarco_web_search/train_queries.parquet`, 9,206,475 rows | Not yet built in any environment this session had access to -- see "Rebuilding against the real dataset" for the exact commands to build and verify it on the user's machine. |

`configs/retrieval.yaml`'s `output.index_dir` was changed from
`data/indexes/prefix` to `data/indexes/prefix_real` specifically so that
running the build script with its default config can never collide with
the demo index. As a second, independent line of defense,
`src/retrieval/build_prefix_index.py::main()` also calls
`_guard_against_overwriting_demo_index()`, which raises `SystemExit`
before doing any work if the resolved output directory is
`data/indexes/prefix/` and `--force` was not passed -- so even a
misconfigured or hand-edited config pointed back at the demo directory
gets refused rather than silently overwriting it. Both are exercised for
real in `tests/test_build_prefix_index.py` (`test_guard_*`,
`test_main_refuses_demo_dir_without_force`,
`test_default_config_output_dir_is_not_the_demo_dir`).

---

## A. Index architecture chosen and why

**Chosen: a single SQLite database file with two tables, not a materialized
"one row per prefix" table.**

### The core idea

`queries` is a `WITHOUT ROWID` table whose primary key is
`(match_key, normalized_query)`, where `match_key` is the case-folded,
whitespace-normalized form of `normalized_query` used for matching. A
`WITHOUT ROWID` table in SQLite is physically stored in primary-key order
-- so `queries` is, on disk, one sorted array of queries clustered by
`match_key`.

That single property means **any prefix, of any length, is served by one
index seek plus a short forward scan**:

```sql
SELECT ... FROM queries
WHERE match_key >= :prefix AND match_key < :upper_bound
ORDER BY popularity DESC, normalized_query ASC
LIMIT :limit
```

`:upper_bound` is `:prefix` with its last character's Unicode code point
incremented by one (`src/retrieval/prefix_index.py::_range_upper_bound`).
This is not a heuristic -- it's correct for arbitrary Unicode prefixes
because UTF-8 byte order is guaranteed to equal Unicode code point order,
and SQLite's default `BINARY` collation compares `TEXT` as UTF-8 bytes.

This means the system does **not** need to materialize a row per
`(query, prefix_length)` pair to answer prefix queries correctly. A naive
"generate every prefix of every query and index it" design would, at 9.2M
queries and an average query length in the teens, produce on the order of
100M+ rows just to index prefixes -- most of that structure buys nothing
over a plain sorted index, because a B-tree seek is already O(log n)
regardless of prefix length.

### Where the required "prefix generation" component actually earns its
keep

The spec (section 2) asks for a configurable `generate_prefixes()` with
min/max length, whitespace handling, and a per-query cap. That function
exists (`src/retrieval/prefix_index.py::generate_prefixes`) and is used for
exactly the part of this system where materializing prefixes *does* pay
for itself: **`hot_prefix_cache`**, a small, pre-ranked top-K table for the
shortest prefixes (default: length <= `hot_prefix_cache.max_length` = 6).

Why short prefixes specifically: a single character like `"m"` can match a
huge fraction of the corpus. Ranking those matches live, on every
keystroke, under real QPS, is wasted work when the top 50 results for
`"m"` don't change between index builds. So at build time, every
deduplicated query's bounded set of short prefixes (via `generate_prefixes`,
respecting `min_prefix_length`, `include_whitespace_prefixes`, and
`max_prefixes_per_query`) is generated once, ranked once per prefix with a
SQL window function (`ROW_NUMBER() OVER (PARTITION BY prefix ORDER BY
popularity DESC, normalized_query ASC)`), and only the top `top_k` (default
50) rows per prefix survive into `hot_prefix_cache`. That table stays small
and bounded no matter how large the corpus is, because it's capped per
prefix, not per query.

Longer or colder prefixes -- the vast majority of what a user actually
types by the time they've typed `"machine lea"` -- skip the cache and go
straight to the range scan, which is already fast (see benchmark below)
because it's a single indexed seek.

**Correctness never depends on `hot_prefix_cache.max_length`.** It only
depends on the range scan, which always works. The cache is purely a
latency/CPU optimization for the highest-QPS keys, and `PrefixIndex`
detects when the cache can't be trusted to be complete (it hit its own
`top_k` cap and the caller asked for more results than that) and
transparently falls back to the range scan in that case
(`get_candidates`, the `cache_is_complete` check).

### Why SQLite over the alternatives considered

| Option | Verdict |
|---|---|
| **SQLite (chosen)** | Single file, zero extra services, a real B-tree on disk, `WITHOUT ROWID` clustering gives prefix range scans for free, trivial to ship/version/copy. Sub-millisecond point/range lookups at this scale (see benchmark). |
| Python dict/trie in memory | Violates "do not create an unnecessarily huge in-memory Python dictionary" outright at 9.2M rows; also not persisted -- every process restart pays the full build cost. |
| DuckDB | Excellent for analytical batch queries over Parquet, but its query-serving story for many small point/range lookups from a live process is less mature than SQLite's, and it adds a dependency with no benefit here since we don't need SQL analytics at serve time, just indexed lookups. |
| Flat sorted array + `bisect` (memory-mapped) | Would work and would be even faster, but reimplements what a B-tree index already gives us, with none of SQLite's crash-safety, tooling, or easy secondary indexing (`idx_queries_popularity`) for free. |
| Marisa-trie / other compressed trie structures | Very memory-efficient and fast, but adds a third-party dependency not in the existing project's `requirements.txt` (which only lists `pyarrow`, `pyyaml`, `requests`, `pytest`), and reimplementing dedup/popularity/ranking on top of a trie is more code for a marginal gain over SQLite at this scale. |

Given the five stated trade-off dimensions (build speed, disk size, lookup
latency, implementation simplicity, scalability), SQLite with a clustered
sorted table plus a small hot-prefix cache is the best balance: it needs no
new infrastructure, degrades gracefully (every prefix is still served
correctly even without the cache), and the numbers below show it comfortably
meets real-time latency requirements.

### Two-phase, streamed build (bounded memory throughout)

1. **Ingest** (`index_builder.ingest_records`): stream rows out of
   `train_queries.parquet` in bounded Arrow record batches
   (`processing.batch_size`, default 50,000 -- see
   `build_prefix_index.py::iter_parquet_rows`), UPSERT each into `queries`
   in batches of `processing.sqlite_commit_batch_size` (default 20,000)
   with a `COMMIT` after each batch. Memory use is `O(batch_size)`, never
   `O(9.2M)`.
2. **Hot-cache build** (`index_builder.build_hot_prefix_cache`): stream the
   *already-deduplicated* `queries` table (not the raw 9.2M rows) through
   `generate_prefixes()`, capped at `hot_prefix_cache.max_length`, into a
   temporary staging table, rank with one window-function query, keep the
   top K, drop the staging table.

Neither phase ever calls `.to_pylist()` / `fetchall()` on the full dataset.

---

## B–H. Numbers

Two separate answers here, because of the data-availability note at the top
of this document.

### From the *real* preprocessing run (from `preprocessing_stats.json`,
uploaded alongside this task -- this index was not yet built against it in
this environment)

- Records to index (train split): **9,206,475**
- Duplicate normalized queries already measured by preprocessing:
  **3,843** (see section 5 below for how the index handles them)
- `train_queries.parquet` size on disk: **541,379,312 bytes** (~516 MB)

### From the *synthetic* demo build actually run in this environment

```
records_processed:            20,093
unique_normalized_queries:    234
duplicate_normalized_queries: 19,859
prefixes_generated (hot-cache staging): 1,403
hot_cache_rows:                1,403
ingest time:                   0.112 s
hot-cache build time:          0.010 s
total build time:              0.126 s
index size on disk:            864,256 bytes (~844 KB)
```

(The synthetic filler generator draws from only 150 distinct
subject/object combinations to keep the demo corpus small and fast to
inspect by hand, so it produces far more duplicates than the real corpus
would at this record count -- see `scripts/generate_synthetic_queries.py`.
The dedup, popularity, and hot-cache machinery being exercised is identical
to what would run against the real file.)

### Lookup latency (synthetic index, 200 timed iterations per prefix after
20-iteration warm-up, `limit=100`; full breakdown in
`reports/retrieval/prefix_index_benchmark.json`)

| Prefix | p50 (ms) | p95 (ms) | p99 (ms) | avg (ms) |
|---|---|---|---|---|
| `m` | 0.232 | 0.492 | 0.570 | 0.267 |
| `ma` | 0.229 | 0.585 | 0.780 | 0.263 |
| `mac` | 0.281 | 1.083 | 1.280 | 0.382 |
| `machine` | 0.210 | 0.572 | 0.937 | 0.251 |
| `machine l` | 0.253 | 0.731 | 1.173 | 0.323 |
| `machine lea` | 0.245 | 0.853 | 1.082 | 0.327 |
| `python` | 0.110 | 0.556 | 0.924 | 0.184 |
| `data` | 0.087 | 0.852 | 1.639 | 0.216 |
| `deep` | 0.073 | 0.264 | 0.359 | 0.100 |
| `xyznonexistent` | 0.030 | 0.128 | 0.171 | 0.042 |
| **Overall (mean across prefixes)** | **0.175** | **0.612** | **0.891** | **0.236** |

All well under a millisecond, comfortably inside real-time autocomplete
budgets (typical targets are single-digit milliseconds server-side). At
9.2M rows the range-scan path's cost is still dominated by an index seek
(`O(log n)`, so ~24 comparisons at 9.2M rows vs. ~18 at 234 rows) plus a
`LIMIT`-bounded forward scan -- i.e. latency should stay in the same
sub-millisecond-to-low-single-digit-millisecond regime at full scale,
though this should be re-measured once the real file can be built (see
"Rebuilding against the real dataset" below).

### Memory

Peak resident memory during the synthetic build was not separately
profiled (no `pyarrow`/`memory_profiler` available in this sandbox), but by
construction it is bounded by `max(batch_size, sqlite_commit_batch_size)`
row-dicts in flight at any time (50,000 / 20,000 by default) -- never the
full row count. SQLite itself keeps its page cache small by default and
does not require the database to fit in memory.

---

## 4. Popularity / frequency signal

The corpus has no click-through, impression, or search-frequency data --
only query text and a `qid`. We do not invent one. `configs/retrieval.yaml
-> popularity.mode` offers exactly two honest options:

- **`"neutral"`**: every query gets `popularity = 1`. Ranking then falls
  back entirely to the deterministic `normalized_query ASC` tie-break.
- **`"duplicate_count"` (default)**: `popularity = 1 + duplicate_count`,
  where `duplicate_count` is the number of *other* raw rows (distinct
  `qid`s) whose `normalized_query` is byte-identical to this one. This is
  a real, derived-from-data count -- not fabricated -- but it is
  explicitly **not** a measure of how often a query was actually searched
  by a user; it only reflects how many times the exact same normalized
  text happened to appear as a distinct row in this corpus. It's a weak
  signal used only because it's the one piece of repetition information
  genuinely present in the data, and it's what separates `"machine
  learning tutorial"` (popularity 4 in the synthetic demo, because it
  happens to appear under 4 different qids) from a query that appears
  exactly once.

The schema keeps this swappable: `queries.popularity` is a plain integer
column, computed once at the end of the ingest phase
(`index_builder.finalize_popularity`). Plugging in a real signal later (log
-derived click/impression counts, e.g.) means writing new values into that
column and re-running `ANALYZE` -- no schema or query-path change required.

---

## 5. Duplicate normalized queries

The preprocessing stats report **3,843** duplicate normalized queries in
the train split (rows where two or more distinct `qid`s normalize to
exactly the same text).

**Decision: collapse each duplicate group into a single `queries` row for
retrieval purposes, but never delete any `qid`.**

- The canonical `qid` / `original_query` shown for that row is the
  **lexicographically smallest `qid`** among the group -- deterministic
  and reproducible across rebuilds (no dependency on file row order).
- Every other `qid` in the group is preserved in a comma-separated
  `duplicate_qids` column on that same row, so full traceability back to
  every original raw row is always available.
- `duplicate_count` records how many *other* rows collapsed into this one,
  and (by default) directly drives the row's `popularity` -- see section 4.

This is implemented as a single-pass, streaming `UPSERT ... ON CONFLICT`
(`index_builder._UPSERT_QUERY_SQL`) -- no separate dedup pass or in-memory
set of seen queries is needed, which matters at 9.2M rows.

Requirement 6 additionally requires that `get_candidates` never return
duplicate `normalized_query` values. Because `normalized_query` is part of
the table's primary key, that's structurally guaranteed -- it isn't
possible for the query result to contain the same `normalized_query`
twice.

---

## 9. Benchmark

Run with:

```bash
python -m src.retrieval.benchmark_prefix_index --config configs/retrieval.yaml
```

Methodology: for each of the 10 required prefixes (`m`, `ma`, `mac`,
`machine`, `machine l`, `machine lea`, `python`, `data`, `deep`,
`xyznonexistent`), 20 warm-up calls followed by 200 timed calls to
`get_candidates(prefix, limit=100)`. p50/p95/p99/avg are computed from the
200 timed samples (linear-interpolation percentile). Full results:
`reports/retrieval/prefix_index_benchmark.json` (numbers reproduced in
section B–H above).

---

## Validation (section 10 of the task)

Run against the synthetic demo index built in this environment (`data sc`,
`deep learning`, etc. below are typed exactly as a user would type them --
lowercase input matches regardless of the corpus's stored casing because
`matching.case_sensitive` defaults to `false`):

```
prefix: 'machine'
  machine learning tutorial            popularity=4  source=range_scan
  machine learning                     popularity=1  source=range_scan
  machine learning algorithms          popularity=1  source=range_scan
  machine learning algorithms explained popularity=1 source=range_scan
  machine learning book                popularity=1  source=range_scan

prefix: 'machine l'
  (identical top-5 to 'machine' above -- no non-"learning" "machine l..."
   query outranks them in this corpus)

prefix: 'machine lea'
  (identical top-5 again)

prefix: 'python'
  python tutorial                      popularity=4  source=hot_cache
  python == vs is                      popularity=1  source=hot_cache
  python dictionary methods            popularity=1  source=hot_cache
  python download                      popularity=1  source=hot_cache
  python for beginners                 popularity=1  source=hot_cache

prefix: 'deep learning'
  deep learning                        popularity=1  source=range_scan
  deep learning book                   popularity=1  source=range_scan
  deep learning frameworks             popularity=1  source=range_scan
  deep learning specialization         popularity=1  source=range_scan
  deep learning vs machine learning    popularity=1  source=range_scan

prefix: 'data sc'
  data science course                  popularity=4  source=range_scan
  data science jobs                    popularity=1  source=range_scan
  data science roadmap                 popularity=1  source=range_scan
  data science salary                  popularity=1  source=range_scan
  data science vs data analytics       popularity=1  source=range_scan
```

Full machine-readable output: `data/indexes/prefix/validation_examples.json`.

**These are suggestions generated from our own synthetic (and, once rebuilt
against the real file, MS MARCO Web Search-derived) query corpus. They are
not Google's or any other search engine's autocomplete suggestions.**

Note `python` was served from `hot_cache` (6 characters, at the
`hot_prefix_cache.max_length` boundary) while everything longer was served
from `range_scan` -- exactly the intended split described in section A.

---

## Rebuilding against the real dataset

This document's build/benchmark numbers above came from the synthetic
corpus because this sandbox lacks `pyarrow` and network access. The real
`train_queries.parquet` / `dev_queries.parquet` now exist on the user's
own machine, at (their project root)
`data/processed/msmarco_web_search/{train,dev}_queries.parquet` -- this
build has NOT been run in any environment this session had access to, and
nothing below is a fabricated result. This is the exact, complete sequence
to run **locally**, from the project root
(`C:\Users\JAPNOOR\Downloads\smartsearch_lstm_stage\project` for the
current user):

```bash
# 0. One-time: install pyarrow if not already present.
pip install pyarrow --break-system-packages   # or: pip install -r requirements.txt

# 1. Build the REAL index (writes to data/indexes/prefix_real/ -- see
#    "Two index locations" above; the existing data/indexes/prefix/ demo
#    index is untouched by this command, and this command will refuse to
#    run at all if configs/retrieval.yaml's output.index_dir is ever
#    pointed back at data/indexes/prefix/ without --force).
python -m src.retrieval.build_prefix_index --config configs/retrieval.yaml

# 2. Verify: stats + the task's 6 required example lookups
#    (machine, machine l, machine lea, python, deep learning, data sc).
python -m src.retrieval.inspect_index --config configs/retrieval.yaml

# 3. Benchmark (written to an explicitly-named file so it never collides
#    with the existing synthetic benchmark report at
#    reports/retrieval/prefix_index_benchmark_synthetic_demo.json).
python -m src.retrieval.benchmark_prefix_index --config configs/retrieval.yaml --output reports/retrieval/prefix_index_benchmark_real.json

# 4. Run the test suite (indexing-core tests need no pyarrow; the new
#    Parquet-path tests in test_build_prefix_index.py will now actually
#    run, not skip, since pyarrow is installed).
python -m pytest tests/test_prefix_index.py tests/test_build_prefix_index.py -v
```

No further code changes should be required -- `build_prefix_index.py`
already points at `configs/retrieval.yaml -> input.train_queries`, which
is set to that exact real path, and `output.index_dir`, which is set to
`data/indexes/prefix_real` (not the demo directory).
`tests/test_prefix_index.py` does not depend on the real file at all (it
builds its own tiny index from `scripts/generate_synthetic_queries.py`);
`tests/test_build_prefix_index.py`'s CLI/guard tests also don't, and its
Parquet-reading tests will only actually execute (rather than skip) once
`pyarrow` is installed -- see that file's module docstring.

**Corrected staging-table size estimate** (an earlier version of this doc
under-estimated this): at ~9.2M unique queries and the default
`hot_prefix_cache.max_length: 6`, phase 2's temporary `_prefix_raw_staging`
table can reach on the order of **tens of millions of rows** (up to
`unique_queries x 6`, since the large majority of real natural-language
queries are at least 6 characters long and so generate the full 6
prefixes) before the window-function query collapses it down to the much
smaller final `hot_prefix_cache` table (bounded by `#distinct prefixes of
length <= 6` x `top_k`, typically far fewer than 9.2M since many queries
share the same short prefixes). This is a temporary build-time disk/CPU
cost only -- the staging table is dropped immediately after -- but it
means phase 2 is the slower of the two phases at full scale, and
`index_builder.py` now logs progress every 500,000 queries processed
during it (previously silent) so a long-running real build doesn't look
identical to a hung one. `hot_prefix_cache.max_length` and `.top_k` are
the two knobs to lower if build time or index size need to shrink.
