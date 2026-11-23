# SmartSearch AI — Comprehensive Project Audit Report

**Audit Date:** November 2026  
**Project:** SmartSearch AI (Search Auto-Suggestion & Website Trust Platform)  
**Workspace:** `c:\Users\JAPNOOR\Downloads\smartsearch_lstm_stage2`  
**Git Branch:** `main` (origin/main)  
**Total Non-Venv Files Scanned:** 224 files  
**Total Disk Footprint:** 5,802.00 MB (5.67 GB)

---

## 1. Executive Summary

A comprehensive, non-destructive audit of the SmartSearch AI codebase was conducted across all source files, configurations, datasets, binary executables, test suites, Docker assets, and browser extension files.

Every file was evaluated against four rigorous safety criteria:
1. **Production Runtime Necessity:** Does the FastAPI backend, Nginx proxy, Docker deployment, or browser extension depend on this file?
2. **Reproducibility & Retraining:** Is this file required to rebuild the prefix index, retrain or evaluate the LSTM neural ranker, or verify MS MARCO data processing?
3. **Automated Testing & CI:** Is this file imported by the Python unit/integration test suite or Node.js extension test suite?
4. **Safety Guards & Architecture Documentation:** Does this file serve as a benchmark baseline, safety guard target, or core operational guide?

### Key Audit Findings:
- **Zero Critical Regressions:** All 183 Python test cases (including real-database integration tests) and all 43 Node.js extension unit tests pass with 100% success.
- **Immediate Safe Recovery:** **726.62 MB** can be reclaimed immediately by removing obsolete scratch files (including a 724.74 MB compressed index upload artifact) and clearing stale runtime logs/PIDs without touching any production code.
- **Secondary Safe Recovery (Pending Approval):** An additional **516.95 MB** can be recovered by removing a redundant root-level copy of the MS MARCO dataset (`data/`), whose canonical copy lives inside `project/data/processed/msmarco_web_search/`.
- **Public Tunnel Clarification:** The project actively supports two tunnel architectures:
  1. **Ngrok Tunnel (`project/ngrok.exe`, 31.73 MB):** Actively used by `run_background.ps1` and `start_ngrok.ps1` to serve the permanent public domain `https://emphatic-tackle-monetary.ngrok-free.dev`, which is configured as the active backend endpoint in the Chrome extension.
  2. **Cloudflare Tunnel (`cloudflared` container):** Runs inside Docker via `docker compose --profile tunnel up -d cloudflared` as documented in `docs/cloudflare_tunnel.md` and `docker-compose.yml` (no local `.exe` required).

---

## 2. Complete Project Directory Structure & Footprint

```text
c:\Users\JAPNOOR\Downloads\smartsearch_lstm_stage2\
├── .git/                                   # Git repository metadata
├── .gitignore                              # Root Git ignore rules
├── .pytest_cache/                          # Ephemeral pytest cache (removable)
├── .venv/                                  # Python 3.13 virtual environment
├── check_pairs.py                          # 724 B: One-off diagnostic script (removable)
├── README.md                               # 10.0 KB: Top-level comprehensive documentation
├── data/                                   # 516.95 MB: Exact duplicate of project/data/ (candidate for removal)
│   └── processed/msmarco_web_search/
│       ├── train_queries.parquet           # 516.30 MB (Identical to project/data copy)
│       ├── dev_queries.parquet             # 0.65 MB (Identical to project/data copy)
│       └── preprocessing_stats.json        # 5.9 KB (Identical to project/data copy)
│
└── project/                                # Canonical application root (4,557 MB total)
    ├── .dockerignore                       # Docker build context exclusions
    ├── .env                                # Local secrets & active tunnel credentials
    ├── .env.example                        # Production environment template
    ├── .gitignore                          # Project-level Git ignore rules
    ├── Dockerfile                          # Multi-stage production container definition
    ├── docker-compose.yml                  # Compose definition (FastAPI + Nginx + Cloudflare + Ngrok)
    ├── README.md                           # Core project documentation
    ├── requirements.txt                    # Development & training dependencies
    ├── requirements-runtime.txt            # Minimal container serving dependencies
    ├── ngrok.exe                           # 31.73 MB: Active tunnel binary for background supervisor
    ├── smartsearch-edge.zip                # 27.4 KB: Pre-zipped extension package (removable)
    │
    ├── configs/                            # System configurations
    │   ├── api.yaml                        # FastAPI serving & rate limit config
    │   ├── lstm.yaml                       # LSTM model architecture & hyperparameters
    │   ├── lstm_real.yaml                  # Real-data training config (points to train_pairs_clean.jsonl)
    │   ├── retrieval.yaml                  # Prefix retrieval tuning & benchmark config
    │   ├── retrieval_real.yaml             # Real MS MARCO prefix index configuration
    │   └── verified_partners.yaml          # Website trust verified partner domain directory
    │
    ├── data/                               # Application datasets and indices (4,510.94 MB)
    │   ├── indexes/
    │   │   ├── prefix/                     # 0.82 MB: Synthetic demo index (guard target & benchmark fixture)
    │   │   │   ├── prefix_index.sqlite3    # 844 KB: SQLite prefix test fixture
    │   │   │   ├── build_report.json       # Build metadata
    │   │   │   └── validation_examples.json# Test validation queries
    │   │   └── prefix_real/                # 3,480.08 MB (3.48 GB): Production prefix index (DO NOT TOUCH)
    │   │       ├── prefix_index.sqlite3    # 3.48 GB: 20M+ prefix entries from MS MARCO
    │   │       └── build_report.json       # Build statistics
    │   ├── processed/
    │   │   ├── lstm/
    │   │   │   ├── train_pairs.jsonl       # 320.49 MB: Raw pair extraction (3.2M pairs)
    │   │   │   ├── train_pairs_clean.jsonl # 317.38 MB: Sanitized training pairs (control chars stripped)
    │   │   │   ├── train_pairs_clean_report.json # Sanitization metrics report
    │   │   │   ├── dev_pairs.jsonl         # 35.43 MB: Validation pairs
    │   │   │   └── dev_pairs_report.json   # Validation pair generation metrics
    │   │   └── msmarco_web_search/         # Canonical preprocessed MS MARCO dataset (516.95 MB)
    │   │       ├── train_queries.parquet   # 516.30 MB: 9.2M preprocessed queries
    │   │       ├── dev_queries.parquet     # 0.65 MB: 9,253 dev queries
    │   │       └── preprocessing_stats.json# Cleaning & deduplication stats
    │   └── synthetic/                      # 1.05 MB: Offline test fixtures and generation seeds
    │       └── lstm/
    │           ├── train_queries.jsonl     # 110 KB
    │           ├── train_pairs.jsonl       # 690 KB
    │           ├── dev_queries.jsonl       # 56 KB
    │           └── dev_pairs.jsonl         # 160 KB
    │
    ├── docs/                               # 8 Comprehensive engineering architecture guides
    │   ├── api.md                          # REST API schema and contract
    │   ├── cloudflare_tunnel.md            # Cloudflare Quick Tunnel operations guide
    │   ├── deployment_architecture.md      # Full production topology & edge networking
    │   ├── extension.md                    # Manifest V3 extension developer guide
    │   ├── lstm_model.md                   # LSTM neural architecture & training documentation
    │   ├── prefix_index.md                 # SQLite candidate generation design & performance
    │   ├── production_hardening.md         # Rate limiting, timeouts, logging & CORS
    │   └── website_trust.md                # Multi-signal trust and safety evaluation engine
    │
    ├── extension/                          # Chrome/Chromium Manifest V3 browser extension
    │   ├── manifest.json                   # Extension manifest V3
    │   ├── test-page.html                  # Local interactive test suite page
    │   ├── background/service-worker.js    # Background service worker & omnibox handler
    │   ├── content/                        # Injected autocomplete dropdown & DOM observer
    │   │   ├── content.js                  # Dynamic input observer and UI rendering
    │   │   ├── content.css                 # Autocomplete dropdown styling
    │   │   └── logic.js                    # Pure business logic (debouncing, parsing, escaping)
    │   ├── icons/                          # Extension icons (16, 32, 48, 128 px)
    │   ├── options/                        # Extension configuration options page
    │   ├── popup/                          # Extension toolbar popup (status & trust card)
    │   └── tests/                          # Automated Node.js extension test suite
    │       ├── test_extension_logic.js     # 43 pure unit tests (Node.js)
    │       └── test_e2e_extension_api.js   # Live integration test against API
    │
    ├── logs/                               # 1.88 MB: Stale runtime logs (safe to rotate/truncate)
    │   ├── fastapi.log                     # 509 KB
    │   ├── fastapi_err.log                 # 833 KB
    │   ├── ngrok.log                       # 604 KB
    │   ├── supervisor.log                  # 23 KB
    │   └── watchdog.pid                    # 6 B: Stale process ID
    │
    ├── models/                             # Trained neural weights & tokenizer (14.20 MB)
    │   └── lstm/
    │       ├── best_model.pt               # 13.76 MB: PyTorch checkpoint (6.35M parameters)
    │       └── tokenizer/                  # Hybrid word/character tokenizer
    │           ├── tokenizer.json          # 443 KB: Tokenizer vocab & state
    │           ├── config.json             # Architecture config
    │           └── special_tokens_map.json # Special token IDs
    │
    ├── nginx/                              # Nginx reverse proxy configuration
    │   └── conf.d/default.conf             # Upstream routing, rate limits, gzip & security headers
    │
    ├── reports/                            # Machine-readable benchmarks and model evaluation outputs
    │   ├── models/                         # LSTM evaluation and baseline comparison reports
    │   └── retrieval/                      # Retrieval latency and throughput benchmarks
    │
    ├── scratch/                            # 724.74 MB: Temporary development scratch (REMOVABLE)
    │   ├── prefix_index.sqlite3.gz         # 724.74 MB: One-off Hugging Face upload artifact
    │   ├── crash_fastapi.ps1               # 251 B: Debug helper script
    │   ├── err.log                         # 0 B: Empty log
    │   ├── make_app_py.py                  # 2.4 KB: HF Space app generator
    │   ├── new_app.py                      # 3.9 KB: HF Space deployment script
    │   └── upload_index_hf.py              # 809 B: HF upload script
    │
    ├── scripts/                            # Operational utility and lifecycle scripts
    │   ├── fit_tokenizer.py                # Re-trains tokenizer from text corpus
    │   ├── generate_synthetic_queries.py   # Test fixture generator (imported by tests)
    │   ├── generate_synthetic_lstm_queries.py # LSTM fixture generator (imported by tests)
    │   ├── sanitize_training_pairs.py      # Cleans control characters from pairs
    │   ├── install_startup_shortcut.ps1/bat# Windows startup persistence
    │   ├── register_task.ps1/bat           # Windows Task Scheduler supervisor registration
    │   ├── remove_startup_shortcut.ps1/bat # Removes startup shortcut
    │   ├── unregister_task.ps1/bat         # Unregisters background task
    │   └── test_endpoints.ps1/bat          # Live endpoint verification script
    │
    ├── src/                                # Production application source code
    │   ├── api/                            # FastAPI app, config, routes, schemas, rate limiter
    │   ├── models/                         # LSTM model, tokenizer, training, inference
    │   ├── retrieval/                      # SQLite PrefixIndex, IndexBuilder, benchmark
    │   └── trust/                          # Website trust engine, URL analyzer, reputation
    │
    ├── tests/                              # Comprehensive Python test suite (12 test modules)
    │   ├── test_api.py                     # API routing, status codes, schemas, timing
    │   ├── test_build_prefix_index.py      # SQLite index builder & safety guard tests
    │   ├── test_create_training_pairs.py   # Pair generation heuristics & filters
    │   ├── test_e2e_integration.py         # End-to-end simulated client workflows
    │   ├── test_lstm_inference.py          # Neural ranker scoring, batching, real index test
    │   ├── test_lstm_model.py              # Model architecture, loss, gradient propagation
    │   ├── test_prefix_index.py            # Candidate retrieval, prefix normalization
    │   ├── test_sanitize_training_pairs.py # Control character sanitization tests
    │   ├── test_tokenizer_streaming.py     # Tokenizer serialization & streaming tests
    │   ├── test_website_trust.py           # 40 tests: trust states, partners, blocklist
    │   └── _minimal_pytest_runner.py       # 5.4 KB: Legacy shim (retained for reference)
    │
    └── Lifecycle & Service Management Scripts:
        ├── run_background.ps1 / .bat / .vbs# Production supervisor with auto-restart watchdog
        ├── start_ngrok.ps1 / .bat          # Interactive Ngrok public HTTPS launcher
        ├── start_tunnel.ps1 / .bat         # Cloudflare Quick Tunnel container launcher
        ├── start_production_server.ps1/.bat# Direct production Uvicorn launcher
        ├── start_server.ps1 / .bat         # Development server launcher
        ├── status_services.ps1 / .bat      # Health probe & process status reporter
        └── stop_services.ps1 / .bat        # Graceful process terminator
```

---

## 3. Storage Breakdown by Category

| Category | Path | Size | Status | Action |
| :--- | :--- | :--- | :--- | :--- |
| **Production SQLite Index** | `project/data/indexes/prefix_real/` | 3,480.08 MB | Essential | **RETAIN** (Mount read-only) |
| **Scratch Upload Gzip** | `project/scratch/prefix_index.sqlite3.gz` | 724.74 MB | Obsolete | **SAFE TO DELETE** |
| **Duplicate MS MARCO Dataset** | `data/processed/msmarco_web_search/` | 516.95 MB | Redundant copy | **RECOMMEND DELETION** (Canonical in `project/`) |
| **Canonical MS MARCO Dataset** | `project/data/processed/msmarco_web_search/` | 516.95 MB | Essential | **RETAIN** (Original training corpus) |
| **Cleaned LSTM Training Pairs** | `project/data/processed/lstm/train_pairs_clean.jsonl` | 317.38 MB | Essential | **RETAIN** (Model training input) |
| **Raw LSTM Training Pairs** | `project/data/processed/lstm/train_pairs.jsonl` | 320.49 MB | Intermediate | **RETAIN** (Reproducibility baseline) |
| **Validation Pairs** | `project/data/processed/lstm/dev_pairs.jsonl` | 35.43 MB | Essential | **RETAIN** (Model evaluation input) |
| **Ngrok Tunnel Binary** | `project/ngrok.exe` | 31.73 MB | Active | **RETAIN** (Extension live backend) |
| **Trained LSTM Model** | `project/models/lstm/best_model.pt` | 13.76 MB | Essential | **RETAIN** (Production weights) |
| **Stale Logs & PIDs** | `project/logs/` | 1.88 MB | Ephemeral | **SAFE TO CLEAN** |
| **Synthetic Test Data** | `project/data/synthetic/` | 1.05 MB | Test Fixtures | **RETAIN** (Used by test runners) |
| **Synthetic Demo Index** | `project/data/indexes/prefix/` | 0.82 MB | Test Fixture | **RETAIN** (Guard target in tests) |
| **Extension Edge Zip** | `project/smartsearch-edge.zip` | 0.03 MB | Redundant | **SAFE TO DELETE** |
| **Root Diagnostic Script** | `check_pairs.py` | < 0.01 MB | Obsolete | **SAFE TO DELETE** |
| **Scratch Development Scripts** | `project/scratch/` (scripts & err.log) | < 0.01 MB | Obsolete | **SAFE TO DELETE** |
| **Python Bytecode Caches** | `**/__pycache__/`, `.pytest_cache/` | ~0.70 MB | Cache | **SAFE TO DELETE** |

---

## 4. Detailed Component Categorization

### 4.1 Tier 1: Core Components That Must Never Be Deleted
1. **Real Prefix Retrieval Index (`project/data/indexes/prefix_real/prefix_index.sqlite3`):**  
   3.48 GB SQLite B-Tree index storing 20,496,878 prefix entries with pre-computed query frequencies. Enables sub-2ms candidate retrieval.
2. **Canonical MS MARCO Preprocessed Dataset (`project/data/processed/msmarco_web_search/`):**  
   Contains 9,206,475 cleaned web queries in `train_queries.parquet` (516.30 MB) and 9,253 validation queries in `dev_queries.parquet` (0.65 MB). Needed for reproducibility and dataset provenance.
3. **Trained LSTM Neural Weights (`project/models/lstm/best_model.pt`):**  
   Trained 6.35M parameter PyTorch neural ranker checkpoint with character and word hybrid embeddings.
4. **Tokenizer Vocabulary (`project/models/lstm/tokenizer/`):**  
   Vocabulary mapping and config for sequence encoding during autocomplete inference.
5. **FastAPI Application & Modules (`project/src/`):**  
   All services, routes, schemas, rate limiting, and business logic.
6. **Website Trust and Safety Engine (`project/src/trust/` & `project/configs/verified_partners.yaml`):**  
   Deterministic reputation, partner directory, and multi-signal safety analysis.
7. **Manifest V3 Browser Extension (`project/extension/`):**  
   Complete Chrome/Chromium extension with DOM observer, accessible dropdown UI, and popup trust card.
8. **Deployment & Container Orchestration (`Dockerfile`, `docker-compose.yml`, `project/nginx/`):**  
   Container build instructions, volume mounts, and Nginx reverse proxy configuration.
9. **Environment Configurations (`project/.env`, `project/.env.example`, `project/configs/`):**  
   Template and production environment settings.
10. **Automated Test Suites (`project/tests/`, `project/extension/tests/`):**  
    183 Python tests and 43 Node.js tests providing comprehensive regression protection.

### 4.2 Tier 2: Files Proven Unnecessary (Safe Immediate Cleanup)
1. **`project/scratch/prefix_index.sqlite3.gz` (724.74 MB):**  
   An intermediate compressed copy created solely to upload the index to Hugging Face Datasets (`japnoor69/smartsearch-index`). The uncompressed production index is permanently mounted from `project/data/indexes/prefix_real/`. No runtime or test code references this archive.
2. **`project/scratch/` Development Scripts & Empty Logs (7.4 KB):**  
   `crash_fastapi.ps1`, `err.log`, `make_app_py.py`, `new_app.py`, and `upload_index_hf.py`. These were temporary scratch experiments during Hugging Face deployment and local debugging.
3. **`check_pairs.py` at workspace root (724 B):**  
   A 20-line diagnostic script written during early debugging to count control characters in raw pairs. Its functionality is permanently formalized in `project/scripts/sanitize_training_pairs.py` and `project/tests/test_sanitize_training_pairs.py`.
4. **`project/smartsearch-edge.zip` (27.4 KB):**  
   A pre-packaged archive of `project/extension/`. The source files remain fully intact and can be zipped anytime.
5. **Stale Runtime Logs and PID Files in `project/logs/` (1.88 MB):**  
   `fastapi.log`, `fastapi_err.log`, `ngrok.log`, `supervisor.log`, and `watchdog.pid`. These represent stale execution outputs from earlier runs. The supervisor automatically recreates and rotates these logs upon startup.
6. **Python Bytecode & Cache Folders (`__pycache__`, `.pytest_cache`):**  
   Located across root, `project/src/`, `project/tests/`, and `project/scripts/`. Transient compiler artifacts.

### 4.3 Tier 3: Ambiguous or Redundant Files (Candidate for User Approval)
1. **Root-Level `data/` Duplicate Directory (516.95 MB):**  
   - Files: `data/processed/msmarco_web_search/train_queries.parquet` (516.30 MB), `dev_queries.parquet` (0.65 MB), `preprocessing_stats.json` (5.9 KB).
   - Finding: These files are byte-for-byte identical to the canonical files inside `project/data/processed/msmarco_web_search/`.
   - Reason for root existence: Created when the initial preprocessing script was invoked from the repository root before the `project/` directory was established as the primary context.
   - Recommendation: Remove `data/` at the root level to recover **516.95 MB** of duplicate storage, while keeping the canonical copy in `project/data/`.
2. **Raw Training Pairs (`project/data/processed/lstm/train_pairs.jsonl`, 320.49 MB):**  
   - Contains 3,217,031 raw training pairs generated from the MS MARCO corpus before control-character stripping.
   - The production training configuration (`configs/lstm_real.yaml`) points to `train_pairs_clean.jsonl` (317.38 MB, 3,216,663 sanitized pairs).
   - Recommendation: Retain `train_pairs.jsonl` because it represents the raw generated dataset prior to filtering, ensuring full academic and developmental reproducibility.
3. **Synthetic Demo Index (`project/data/indexes/prefix/`, 0.82 MB):**  
   - Contains a small 844 KB SQLite index built from 234 synthetic queries.
   - Finding: `src/retrieval/build_prefix_index.py` contains explicit hardcoded safety guards that refuse to overwrite this path without `--force`, and `tests/test_build_prefix_index.py` specifically tests this guard.
   - Recommendation: Retain this 0.82 MB index to ensure safety guard test suites continue passing without modification.
4. **Legacy Runner Shim (`project/tests/_minimal_pytest_runner.py`, 5.4 KB):**  
   - Created for environments without `pytest` or `torch` installed.
   - Recommendation: Retain as an offline test fallback or flag for optional removal.

---

## 5. Potential Disk Space Recovery Summary

| Cleanup Scope | Items Included | Space Recovered |
| :--- | :--- | :--- |
| **Immediate Safe Cleanup** | `project/scratch/prefix_index.sqlite3.gz` (724.74 MB), scratch scripts (7.4 KB), `check_pairs.py` (724 B), `smartsearch-edge.zip` (27.4 KB), stale logs in `project/logs/` (1.88 MB), all `__pycache__` & `.pytest_cache` (~0.7 MB) | **~727.35 MB** |
| **Secondary Cleanup (Upon Approval)** | Root duplicate `data/` directory (516.95 MB) | **+516.95 MB** |
| **Total Reclaimable Disk Space** | | **~1,244.30 MB (1.21 GB)** |

---

## 6. Dependency & Configuration Health Check

1. **Production vs. Development Dependencies:**
   - `requirements-runtime.txt`: Minimal, container-optimized set (`fastapi`, `uvicorn`, `pydantic`, `httpx`, `pyyaml`). PyTorch CPU is installed separately via Docker layer caching.
   - `requirements.txt`: Includes full development tools (`pyarrow`, `pytest`, `torch`).
   - Separation is strictly maintained.
2. **Container Build Context (`.dockerignore`):**
   - Correctly excludes `data/`, `extension/`, `tests/`, `scratch/`, `.git/`, `.venv/`, and logs.
   - Ensures the 3.48 GB SQLite index is never baked into the container image, but rather volume mounted read-only at runtime.
3. **Source Control Rules (`.gitignore`):**
   - Both root and project `.gitignore` properly exclude databases (`*.sqlite3`), raw data, `.env`, logs, and checkpoints.
4. **Public Tunnel & Extension Alignment:**
   - Manifest V3 Chrome extension uses `https://emphatic-tackle-monetary.ngrok-free.dev` with `ngrok-skip-browser-warning` headers.
   - Ngrok binary and background supervisor scripts are retained to keep this extension connection operational.
   - Cloudflare Quick Tunnel remains ready via Docker Compose (`docker compose --profile tunnel up -d cloudflared`).
