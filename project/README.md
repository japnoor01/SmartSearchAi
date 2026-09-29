# SmartSearch AI — Search Auto-Suggestion & Query Recommendation System

> **Deep Learning / NLP Capstone Project**
> An end-to-end, production-grade search auto-suggestion pipeline powered by MS MARCO real search data, SQLite prefix candidate retrieval, a trained PyTorch LSTM neural ranker, a high-performance FastAPI service, and a Manifest V3 Chrome extension.

---

## System Architecture

```text
SmartSearch AI
│
├── Real MS MARCO Preprocessing
│     ├── Source: 9,206,475 real web queries (train) + 9,253 queries (dev)
│     ├── Pipeline: Cleaning, control character stripping, deduplication
│     └── Artifacts: Parquet datasets and 3.2M verified training pairs
│
├── Real Prefix Retrieval
│     ├── Engine: SQLite B-tree index (3.48 GB, 20M+ prefix entries)
│     ├── Latency: Sub-2ms candidate lookup
│     └── Output: Top-50 candidate queries per partial prefix
│
├── LSTM Ranking
│     ├── Architecture: Character + Word Hybrid LSTM Language Model
│     ├── Model: PyTorch checkpoint (`best_model.pt`, 6.35M parameters)
│     └── Inference: Batched, zero-grad candidate likelihood re-ranking
│
├── FastAPI Backend
│     ├── Framework: FastAPI + Uvicorn + Pydantic v2
│     ├── Endpoints: GET /health, GET /api/v1/version, GET /api/v1/suggestions
│     └── Verification: 14 passing unit tests + 2 live integration tests
│
└── Browser Extension
      ├── Standard: Chrome / Chromium Manifest V3
      ├── Client: Debounced content script, DOM observer for dynamic SPAs
      ├── UI: Accessible autocomplete dropdown (<kbd>↑</kbd><kbd>↓</kbd><kbd>↵</kbd><kbd>esc</kbd>)
      └── Verification: 35 passing pure-logic unit tests in Node.js
```

---

## Component Separation

### 1. Retrieval (`src/retrieval/`)
- **Responsibility:** High-speed candidate generation.
- **Engine:** SQLite prefix B-tree index (`data/indexes/prefix_real/prefix_index.sqlite3`).
- **Input:** Partial user prefix `q` (e.g., `"deep lear"`).
- **Output:** Up to 50 raw candidate queries that start with the prefix, sorted by historical search frequency.
- **Performance:** Sub-2 millisecond lookup, bypassing linear full-corpus scans.

### 2. Ranking (`src/models/lstm/`)
- **Responsibility:** Neural scoring and linguistic re-ranking.
- **Model:** PyTorch LSTM ranker (`models/lstm/best_model.pt`) with vocabulary from `models/lstm/tokenizer/`.
- **Input:** Candidate query set + partial prefix.
- **Output:** Sorted suggestions by LSTM conditional log-likelihood scores.
- **Execution:** Batched evaluation mode with zero gradients (`torch.no_grad()`).

### 3. API (`src/api/`)
- **Responsibility:** Production REST service and integration orchestrator.
- **Framework:** FastAPI with async lifespan management and CORS support.
- **Endpoints:**
  - `GET /health` — Service readiness, model loading state, device type.
  - `GET /api/v1/version` — Pipeline metadata, model architecture, vocabulary size.
  - `GET /api/v1/suggestions?q=<query>&top_k=5` — Ranked query suggestions.
- **Configuration:** Centrally configured via [`configs/api.yaml`](configs/api.yaml) and environment variables.

### 4. Client / Extension (`extension/`)
- **Responsibility:** Real-world browser user experience and DOM integration.
- **Standard:** Manifest V3 (compatible with Chrome, Edge, Brave, and Opera).
- **Features:**
  - Dynamic input detection for Single-Page Applications (React, Vue, Angular) via `MutationObserver`.
  - Configurable debounce delay (default: 200ms) with `AbortController` cancellation to eliminate race conditions.
  - Strict privacy filter: ignores passwords, emails, credit cards, OTP, and sensitive attributes.
  - Accessible dropdown with ARIA attributes and keyboard navigation.
  - Extension Popup and Options management UI with live backend connectivity testing.

---

## Quickstart Guide

### Step 1: Start the FastAPI Backend

Open a PowerShell terminal in the project directory:

```powershell
cd c:\Users\JAPNOOR\Downloads\smartsearch_lstm_stage2\project

# Start the server with Uvicorn
uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
```

Verify backend health:
```powershell
curl http://127.0.0.1:8000/health
```

Expected output:
```json
{"status":"healthy","model_loaded":true,"index_connected":true,"device":"cpu"}
```

### Step 2: Load the Extension in Chrome

1. Open **Google Chrome** (or Chromium / Edge).
2. Navigate to:
   ```text
   chrome://extensions/
   ```
3. Enable **Developer mode** (toggle in the top-right corner).
4. Click **Load unpacked** (top-left button).
5. Select the `extension/` folder:
   ```text
   c:\Users\JAPNOOR\Downloads\smartsearch_lstm_stage2\project\extension
   ```
6. The **SmartSearch AI — Search Autocomplete** card will appear with status active.

### Step 3: Test on the Local Verification Page

1. Open the included test harness in Chrome:
   ```text
   c:\Users\JAPNOOR\Downloads\smartsearch_lstm_stage2\project\extension\test-page.html
   ```
2. Type `"machine lear"` or `"deep"` in the search box.
3. Observe the debounced suggestions dropdown powered by the real LSTM model.
4. Verify that the password and email inputs do NOT trigger autocomplete.
5. Click **"Generate Dynamic Search Input"** to verify SPA dynamic element detection.

---

## Test Suites & Validation

### Python Backend Tests
Run the comprehensive backend test runner:
```powershell
python tests/_minimal_pytest_runner.py
```
**Results:** 80 passed, 0 failed (14 API unit tests, 64 ML/retrieval unit tests, 2 live integration tests).

### Extension Pure-Logic Tests
Run the standalone Node.js test runner:
```powershell
node extension/tests/test_extension_logic.js
```
**Results:** 35 passed, 0 failed across 9 test suites:
- URL construction & Unicode query encoding (4 tests)
- Top-K boundary enforcement (3 tests)
- Sensitive field exclusion (7 tests)
- Search input detection heuristics (6 tests)
- API response parsing & deduplication (4 tests)
- Stale request sequencing & race-condition cancellation (2 tests)
- Debounce timing simulation (1 test)
- Settings validation & sanitization (5 tests)
- Prefix match & HTML escaping (3 tests)

---

## Privacy & Security Guarantees

- **No History or Page Content Collection:** The extension never inspects DOM text outside the active search input and never requests browsing history, tab tracking, or cookies.
- **Sensitive Inputs Excluded:** Automatically bypasses password, credit card, CVV, OTP, email, telephone, and authentication fields.
- **Scoped Host Permissions:** Manifest permissions are restricted strictly to `http://127.0.0.1:8000/*` and `http://localhost:8000/*`.
