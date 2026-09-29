# SmartSearch AI — Browser Extension Documentation

> **Manifest V3 Chrome/Chromium Browser Extension Client for SmartSearch AI**

The SmartSearch AI browser extension integrates the deep-learning search suggestion pipeline into real-world browsing environments.

---

## Architecture & Subsystem Separation

```
SmartSearch AI
│
├── Real MS MARCO Preprocessing (`src/data/`)
│     └── 9.2M real web search queries extracted & deduplicated
│
├── Real Prefix Retrieval (`src/retrieval/`)
│     └── SQLite candidate retrieval (50 candidates in < 2ms)
│
├── LSTM Ranking (`src/models/lstm/`)
│     └── PyTorch LSTM scoring candidates by prefix compatibility
│
├── FastAPI Backend (`src/api/`)
│     └── High-performance REST service (`/health`, `/api/v1/suggestions`)
│
└── Browser Extension (`extension/`)
      └── Manifest V3 client with debouncing, accessibility & privacy
```

---

## Subsystem Details

### 1. Retrieval (`src/retrieval/`)
- **Role:** High-speed candidate generation.
- **Engine:** SQLite prefix B-tree index (`data/indexes/prefix_real/prefix_index.sqlite3`).
- **Input:** Partial prefix string `q`.
- **Output:** Top-50 candidate queries ranked initially by historical frequency.
- **Latency:** ~0.5ms – 2.0ms.

### 2. Ranking (`src/models/lstm/`)
- **Role:** Semantic and linguistic re-ranking.
- **Model:** Bidirectional/unidirectional LSTM language model with character+word embedding.
- **Input:** Pairwise prefix + candidate sequences.
- **Output:** Normalized compatibility log-likelihood score $\in [0, 1]$.
- **Execution:** Zero-grad batched PyTorch inference (`eval` mode).

### 3. API (`src/api/`)
- **Role:** Production REST gateway and orchestration.
- **Framework:** FastAPI + Uvicorn + Pydantic v2.
- **Endpoints:**
  - `GET /health` — Service readiness and device status.
  - `GET /api/v1/version` — Semantic version and pipeline details.
  - `GET /api/v1/suggestions?q=<query>&top_k=5` — Ranked suggestion payload.
- **Features:** Input validation, CORS configuration, query truncation, millisecond request duration headers.

### 4. Client / Extension (`extension/`)
- **Role:** End-user interaction and DOM integration.
- **Standard:** Manifest V3 (Chrome, Edge, Brave, Chromium).
- **Core Components:**
  - `manifest.json`: Minimal permissions (`storage`), targeted host permissions (`http://127.0.0.1:8000/*`).
  - `content/logic.js`: Pure JavaScript heuristics for URL building, sensitive input exclusion, candidate parsing, request sequencing, and settings validation.
  - `content/content.js`: DOM observer, event listener manager, debouncer (200ms), and accessible dropdown renderer (`role="listbox"`).
  - `content/content.css`: Namespaced styles (`.smartsearch-*`) with dark-mode support and clean aesthetics.
  - `popup/`: Quick toggle, live backend health check, and parameter controls.
  - `options/`: Full diagnostic and settings management panel.
  - `background/service-worker.js`: Settings lifecycle initialization and async health check messaging.
  - `test-page.html`: Standalone verification harness for search, dynamic, and sensitive inputs.
