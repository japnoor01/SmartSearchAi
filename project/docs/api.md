# SmartSearch AI — FastAPI Backend Documentation

Production-quality FastAPI REST service wrapping the SmartSearch candidate retrieval and LSTM neural re-ranking pipeline.

```
Browser Extension / Web Client
              ↓
GET /api/v1/suggestions?q=deep%20learning&top_k=5
              ↓
FastAPI Application (lifespan, CORS, timing, validation)
              ↓
SearchService (in-memory singleton)
              ↓
SmartSearchRanker
   ├── PrefixIndex (SQLite B-tree range scan / hot cache)
   └── LSTMRanker (PyTorch eval mode, zero-gradient, batch scoring)
              ↓
JSON Response
```

> **Research & Engineering Disclaimer:**
> This project is an independent search-autocomplete research/engineering system inspired by modern search suggestion workflows. It does not reproduce Google's proprietary internal implementation.

---

## 1. Installation & Dependencies

The FastAPI backend requires the following Python packages (specified in [`requirements.txt`](file:///c:/Users/JAPNOOR/Downloads/smartsearch_lstm_stage2/project/requirements.txt)):

```bash
pip install "fastapi>=0.115.0" "uvicorn[standard]>=0.30.0" "pydantic>=2.9.0" "httpx>=0.28.0"
```

---

## 2. Configuration & Environment Variables

All settings can be configured via [`configs/api.yaml`](file:///c:/Users/JAPNOOR/Downloads/smartsearch_lstm_stage2/project/configs/api.yaml) or overridden using environment variables without hardcoding absolute paths.

| Setting | Environment Variable | Default Value | Description |
|---|---|---|---|
| Server Host | `SMARTSEARCH_HOST` | `127.0.0.1` | Network interface to bind. |
| Server Port | `SMARTSEARCH_PORT` | `8000` | Port to listen on. |
| Model Path | `SMARTSEARCH_MODEL_PATH` | `models/lstm/best_model.pt` | Path to trained PyTorch LSTM weights. |
| Tokenizer Dir | `SMARTSEARCH_TOKENIZER_PATH` | `models/lstm/tokenizer` | Path to fitted hybrid vocabulary. |
| Prefix Index | `SMARTSEARCH_INDEX_PATH` | `data/indexes/prefix_real/prefix_index.sqlite3` | SQLite prefix index database. |
| Candidate Pool | `SMARTSEARCH_CANDIDATE_POOL_SIZE` | `50` | Number of candidate queries pulled from index before LSTM ranking. |
| Default Top-K | `SMARTSEARCH_TOP_K` | `5` | Default suggestion count if omitted by client. |
| Max Query Length | `SMARTSEARCH_MAX_QUERY_LENGTH` | `200` | Maximum character length accepted for query parameter `q`. |
| Device | `SMARTSEARCH_DEVICE` | `auto` | Execution device (`auto`, `cpu`, or `cuda`). |
| CORS Origins | `SMARTSEARCH_CORS_ORIGINS` | `http://localhost,http://localhost:3000,...` | Comma-separated list of allowed origins. |

---

## 3. Starting the Server

Start the local server using `uvicorn`:

```powershell
uvicorn src.api.app:app --reload --host 127.0.0.1 --port 8000
```

Or using the module entry point:

```powershell
python -m src.api.app
```

Once started, the interactive documentation interfaces are automatically available at:
- **Swagger UI:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc:** [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)
- **OpenAPI Schema:** [http://127.0.0.1:8000/openapi.json](http://127.0.0.1:8000/openapi.json)

---

## 4. API Endpoints

### A. Health Check
- **Endpoint:** `GET /health`
- **Description:** Verifies service health, loaded components, and inference device.
- **Example Response:**
  ```json
  {
    "status": "ok",
    "model_loaded": true,
    "index_connected": true,
    "device": "cpu"
  }
  ```

### B. Version Metadata
- **Endpoint:** `GET /api/v1/version`
- **Description:** Returns service release version, API version, and model architecture.
- **Example Response:**
  ```json
  {
    "name": "SmartSearch AI API",
    "version": "1.0.0",
    "api_version": "v1",
    "model_type": "LSTM Ranker",
    "description": "Production search auto-suggestion and query recommendation service powered by SQLite prefix retrieval and LSTM neural re-ranking."
  }
  ```

### C. Autocomplete Suggestions
- **Endpoint:** `GET /api/v1/suggestions`
- **Parameters:**
  - `q` (*string, required*): The user's partial query prefix. Maximum 200 characters.
  - `top_k` (*integer, optional, default: 5*): Number of suggestions to return (minimum 1, maximum 50).
- **Behavior:**
  - Empty (`q=""`) or whitespace-only (`q="   "`) queries return `200 OK` with an empty `suggestions` array and `count: 0`.
  - Prefix candidates are retrieved from the SQLite B-tree index, deduplicated, verified against the prefix, and re-ranked with the LSTM model.
  - Returns raw ranking logits as output by the model (higher is better).
- **Example Request:**
  ```http
  GET /api/v1/suggestions?q=deep%20learning&top_k=2 HTTP/1.1
  Host: 127.0.0.1:8000
  ```
- **Example Response (`200 OK`):**
  ```json
  {
    "query": "deep learning",
    "suggestions": [
      {
        "text": "deep learning enables rapid identification of potent ddr1 kinase inhibitors.",
        "score": -0.3671
      },
      {
        "text": "deep learning dropout",
        "score": -0.4165
      }
    ],
    "count": 2,
    "latency_ms": 24.5
  }
  ```

---

## 5. Error Handling & Status Codes

All errors return clean, standardized JSON without leaking internal filesystem paths or Python stack traces:

```json
{
  "error": "Bad Request",
  "detail": "Parameter 'top_k' cannot exceed 50.",
  "status_code": 400
}
```

| HTTP Status | Category | Example Scenarios |
|---|---|---|
| `200 OK` | Success | Valid suggestions returned; or empty query (`[]`). |
| `400 Bad Request` | Client Error | `top_k < 1`, `top_k > 50`, `len(q) > 200`. |
| `422 Unprocessable Entity` | Validation Error | Missing `q` parameter; non-integer `top_k` (e.g. `top_k=abc`). |
| `500 Internal Server Error` | Server Error | Unhandled runtime exception (logged securely on server). |
| `503 Service Unavailable` | Service Error | Model weights or SQLite prefix index not initialized. |

---

## 6. Website Trust / Safety Check Endpoint

### `GET /api/v1/site-check`

Inspects a destination URL/domain, evaluates multi-signal risk factors, checks verified partner directories and threat intelligence, and produces an explicit safety assessment without simplistic binary categorization.

#### Query Parameters
- `url` (string, required): Destination website URL or domain name (maximum 2048 characters).

#### Example Request
```http
GET /api/v1/site-check?url=https://github.com HTTP/1.1
Host: localhost:8000
```

#### Example Response
```json
{
  "url": "https://github.com/",
  "domain": "github.com",
  "assessment": "low_risk",
  "verified_partner": true,
  "partner_details": {
    "organization": "GitHub / Microsoft",
    "category": "Software Development & Hosting",
    "verified_since": "2024-01-15",
    "domain": "github.com"
  },
  "known_threat": false,
  "threat_type": null,
  "security_checks": {
    "https": true,
    "domain": "github.com",
    "apex_domain": "github.com",
    "is_ip_literal": false,
    "is_private_or_local": false,
    "is_punycode_or_idn": false,
    "has_userinfo": false,
    "port": null,
    "subdomain_depth": 0,
    "verified_partner": true,
    "threat_detected": false
  },
  "unavailable_checks": [
    "google_safe_browsing",
    "virustotal"
  ],
  "signals": [
    "Verified Partner: GitHub / Microsoft (Software Development & Hosting).",
    "Secure HTTPS connection verified.",
    "No threat indicators detected."
  ],
  "checked_at": "2026-09-29T10:20:44+00:00",
  "cached": true,
  "latency_ms": 0.11
}
```

For full details, signal weights, and state determination logic, see [`docs/website_trust.md`](website_trust.md).

---

## 7. Performance & Lifecycle Optimization

1. **Singleton Model Loading:** The LSTM checkpoint, tokenizer, and SQLite read-only connection are initialized **once** during FastAPI application startup (`lifespan`).
2. **Zero Autograd Overhead:** Model forward passes use `torch.inference_mode()` with dropout disabled (`eval()` mode).
3. **No Parquet Scans:** Serving queries touch only the indexed SQLite B-tree, never scanning 9.2M Parquet rows.
4. **Mini-Batch Chunking:** Candidate pools are chunked into mini-batches (`batch_size=64`) to prevent GPU VRAM spikes on large candidate pools.
5. **Response Timing:** Every response includes an `X-Process-Time-Ms` header measuring roundtrip handler execution.

---

## 8. Security & Privacy Guarantees

- **No Data Persistence:** The API does not store, write, or log queries to disk or database.
- **Zero Sensitive Attributes:** The API accepts only the partial query string. It does not accept or track cookies, authentication tokens, passwords, page contents, or browsing history.
- **Controlled CORS:** Configurable allowed origins prevent arbitrary untrusted cross-origin access while permitting local development and authorized frontends.

---

## 9. Browser Extension Integration Readiness

In the upcoming development stage, the browser extension will simply issue asynchronous GET requests on input keystrokes:

```javascript
async function fetchSuggestions(partialQuery, topK = 5) {
  if (!partialQuery.trim()) return [];
  const url = `http://127.0.0.1:8000/api/v1/suggestions?q=${encodeURIComponent(partialQuery)}&top_k=${topK}`;
  const response = await fetch(url);
  if (!response.ok) return [];
  const data = await response.json();
  return data.suggestions.map(s => s.text);
}
```
