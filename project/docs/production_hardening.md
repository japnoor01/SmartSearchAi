# SmartSearch AI — Production Hardening Architecture & Operations Guide

> **Component:** FastAPI Backend Serving, Security & Production Configuration  
> **Status:** Production Hardened & Locally Verified

---

## 1. Architecture Overview

```text
Chrome / Chromium Extension (Client UI only)
                 ↓ (HTTP GET with AbortController & Debounce)
         FastAPI Gateway
                 │
  ┌──────────────┴──────────────────────────┐
  │  - CORS Middleware (Dev vs Prod mode)    │
  │  - Sliding-Window Rate Limiter (IP-based) │
  │  - Request Timeout Protection (5.0s)    │
  │  - Structured Access Logging (No PII)   │
  │  - Standardized JSON Error Formatter    │
  └──────────────┬──────────────────────────┘
                 ↓
      Search Service Orchestrator
         ├── Metrics Collector (Uptime, Latency, Counters)
         ├── Liveness Probe (`GET /health`)
         └── Readiness Probe (`GET /readiness`)
                 ↓
  ┌──────────────────────────────────────────┐
  │  1. Prefix Retrieval Engine              │
  │     SQLite 3.48GB B-Tree (`prefix_real`) │
  │     Sub-2ms candidate generation         │
  ├──────────────────────────────────────────┤
  │  2. Neural LSTM Ranker                   │
  │     PyTorch `PrefixRankingLSTM` (6.35M)  │
  │     Batch `torch.no_grad()` inference    │
  └──────────────────────────────────────────┘
                 ↓
         Top-K Ranked Suggestions
```

---

## 2. Environment Configuration

Configuration is loaded from [`configs/api.yaml`](../configs/api.yaml) with comprehensive environment variable overrides via [`src/api/config.py`](../src/api/config.py).

### Environment Modes: `development` vs `production`

| Setting | Development (`SMARTSEARCH_ENV=development`) | Production (`SMARTSEARCH_ENV=production`) |
| :--- | :--- | :--- |
| **Auto-Reload (`--reload`)** | Configurable via `api.yaml` or env | **Always Disabled** |
| **CORS Origins** | Localhost, 127.0.0.1, dev ports | **Strict explicit allowlist only** |
| **CORS Origin Regex** | `^(chrome-extension://.*\|http://.*\|null)$` | **Disabled (`null`)** |
| **Query Logging** | Configurable (defaults to off) | **Disabled** (never logs raw search strings) |
| **Rate Limiter** | Active (120 req/min default) | Active (configurable req/window) |
| **Request Timeout** | 5.0s | 5.0s |

### Environment Variables Reference

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `SMARTSEARCH_ENV` | `str` | `development` | Environment mode (`development`, `production`, `testing`). |
| `SMARTSEARCH_HOST` | `str` | `127.0.0.1` | Network interface to bind (e.g. `0.0.0.0` or `127.0.0.1`). |
| `SMARTSEARCH_PORT` | `int` | `8000` | HTTP port to listen on. |
| `SMARTSEARCH_RELOAD` | `bool` | `False` | Enable auto-reload (ignored in production). |
| `SMARTSEARCH_REQUEST_TIMEOUT_SECONDS` | `float` | `5.0` | Maximum inference execution duration before HTTP 504. |
| `SMARTSEARCH_MODEL_PATH` | `path` | `models/lstm/best_model.pt` | Path to trained PyTorch LSTM weights. |
| `SMARTSEARCH_TOKENIZER_PATH` | `path` | `models/lstm/tokenizer` | Path to saved character/word tokenizer assets. |
| `SMARTSEARCH_INDEX_PATH` | `path` | `data/indexes/prefix_real/prefix_index.sqlite3` | Path to 3.48GB SQLite prefix index database. |
| `SMARTSEARCH_DEVICE` | `str` | `auto` | Execution device (`auto`, `cpu`, `cuda`). |
| `SMARTSEARCH_CANDIDATE_POOL_SIZE` | `int` | `50` | Number of candidate queries pulled from index before ranking. |
| `SMARTSEARCH_TOP_K` | `int` | `5` | Default number of recommendations returned. |
| `SMARTSEARCH_MAX_QUERY_LENGTH` | `int` | `200` | Maximum allowed query prefix length (HTTP 400 if exceeded). |
| `SMARTSEARCH_RATE_LIMIT_ENABLED` | `bool` | `True` | Enable or disable sliding-window rate limiting. |
| `SMARTSEARCH_RATE_LIMIT_REQUESTS` | `int` | `120` | Maximum queries permitted per client per window. |
| `SMARTSEARCH_RATE_LIMIT_WINDOW_SECONDS`| `float` | `60.0` | Rate limiting sliding window duration in seconds. |
| `SMARTSEARCH_CORS_ORIGINS` | `csv` | (see table) | Comma-separated list of allowed origins. |
| `SMARTSEARCH_CORS_ORIGIN_REGEX` | `str` | (see table) | Regular expression for matching allowed origins. |
| `SMARTSEARCH_LOG_LEVEL` | `str` | `INFO` | Standard Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `SMARTSEARCH_LOG_QUERIES` | `bool` | `False` | Debug-only: logs user query string in console (PII protection). |

---

## 3. Server Startup

### Development Startup (with live reload)
```powershell
cd project
.\start_server.ps1
# Or double-click start_server.bat
```

### Production Startup (hardened execution)
```powershell
cd project
.\start_production_server.ps1
# Or double-click start_production_server.bat
```
Direct production command:
```powershell
$env:SMARTSEARCH_ENV="production"
python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --workers 1
```

---

## 4. API Endpoints & Contract

### 1. `GET /health` (Liveness Probe)
- **Purpose:** Verifies that the FastAPI process is responsive.
- **Response:**
  ```json
  {
    "status": "ok",
    "model_loaded": true,
    "index_connected": true,
    "device": "cpu"
  }
  ```

### 2. `GET /readiness` (Readiness Probe)
- **Purpose:** Deep check confirming model weights and SQLite index connection are initialized and ready to serve traffic.
- **HTTP 200 Response:**
  ```json
  {
    "status": "ready",
    "model_loaded": true,
    "index_connected": true,
    "device": "cpu",
    "uptime_seconds": 154.2
  }
  ```
- **HTTP 503 Response:** If weights or index are not ready.

### 3. `GET /metrics` (Operational Monitoring)
- **Purpose:** Lightweight operational telemetry without requiring Prometheus/Grafana.
- **Response:**
  ```json
  {
    "uptime_seconds": 182.45,
    "total_requests": 48,
    "total_suggestions_served": 42,
    "total_errors": 0,
    "total_rate_limited": 0,
    "avg_latency_ms": 21.34,
    "last_latency_ms": 16.89
  }
  ```

### 4. `GET /api/v1/suggestions` & `GET /api/v1/suggest`
- **Parameters:**
  - `q` (*string*, required): User search prefix (1 to 200 characters).
  - `top_k` (*integer*, optional, default: 5, range: 1–50): Number of ranked suggestions.
  - `limit` (*integer*, optional, alias for `top_k` on `/api/v1/suggest`).
- **Response:**
  ```json
  {
    "query": "deep lea",
    "suggestions": [
      {
        "text": "deep learning enables rapid identification of potent ddr1 kinase inhibitors.",
        "score": -0.4252
      },
      {
        "text": "deep learning dropout",
        "score": -0.5134
      }
    ],
    "count": 2,
    "latency_ms": 18.25
  }
  ```

---

## 5. Security & Rate Limiting

1. **Sliding-Window Rate Limiter:**
   - Implemented in [`src/api/rate_limiter.py`](../src/api/rate_limiter.py).
   - In-memory, thread-safe, zero external dependencies.
   - When exceeded, returns HTTP 429:
     ```json
     {
       "error": "Too Many Requests",
       "detail": "Rate limit exceeded. Try again in 12 second(s).",
       "status_code": 429
     }
     ```
   - Includes standard `Retry-After: <seconds>` HTTP response header.

2. **Stack Trace Isolation:**
   - Handled via Starlette and FastAPI exception handlers in [`src/api/app.py`](../src/api/app.py).
   - Server-side logger captures full tracebacks via `LOG.exception()`.
   - Client response receives only standardized `ErrorResponse` schemas with descriptive error names and sanitized details.
   - Internal filesystem paths and Python line numbers are never returned to clients.

3. **Input Sanitization & Resource Bounds:**
   - Maximum query length: 200 characters (HTTP 400).
   - Query parameter boundary checks: `top_k` bounded between 1 and 50 (HTTP 400).
   - Non-numeric parameters rejected with HTTP 422.
   - Async request timeout: 5.0 seconds (HTTP 504 Gateway Timeout).

4. **Privacy & PII Protection:**
   - `log_queries` defaults to `False` in production.
   - Access logs record HTTP method, path, status code, latency, and client IP without capturing query text in application log files.

---

## 6. Extension Compatibility & Future Deployed HTTPS Support

1. **Decoupled Architecture:** The Chrome Extension contains **zero** ML dependencies; it communicates purely via HTTP REST JSON.
2. **Options & Popup Configuration:** Users can modify the API endpoint via the Extension Options page (`apiBaseUrl`).
3. **Manifest Permissions:** [`extension/manifest.json`](../extension/manifest.json) includes `https://*/*` in `host_permissions` to allow switching from local `http://127.0.0.1:8000` to a deployed HTTPS API without reloading or rebuilding the extension.

---

## 7. Known Production Limitations

1. **Single-Node In-Memory Rate Limiting:** The rate limiter uses an in-memory sliding window per process. If deploying across multiple load-balanced worker processes, an external cache (e.g. Redis) would be required to synchronize limits across nodes.
2. **SQLite Concurrent Write Concurrency:** The prefix index is opened in read-only mode for serving, which supports high concurrent read concurrency. Full database rebuilds must occur offline.
3. **CPU vs CUDA Device Availability:** Automatic hardware detection uses CUDA if PyTorch GPU binaries and drivers are present; otherwise, CPU execution provides ~15–25ms inference latency.
