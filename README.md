# SmartSearch AI — Search Auto-Suggestion & Website Trust Platform

> **Production AI & Browser Intelligence Platform**  
> An end-to-end search auto-suggestion and website trust evaluation platform powered by MS MARCO real search data, an SQLite prefix candidate retrieval engine, a trained PyTorch LSTM neural ranker, a high-performance FastAPI service, Docker containerization, and a Manifest V3 Chrome extension.

---

## 1. System Architecture

```text
Chrome / Chromium Browser Extension (Manifest V3)
       │
       ├── Autocomplete Dropdown (debounced query typing)
       └── Website Safety / Trust Card (active tab inspection)
                │
                ▼
        Nginx Reverse Proxy (:80)
                │
                ▼
        FastAPI Production Backend (:8000)
       ┌────────┴──────────────────────────┐
       │                                   │
[Feature 1: Autocomplete]          [Feature 2: Website Trust]
       │                                   │
SQLite Prefix Index (20M+ entries)  URL Analyzer (privacy normalizer)
       │                                   │
PyTorch LSTM Neural Ranker          Verified Partner Registry
       │                                   │
Candidate Re-ranking & Scores       Threat Intelligence (GSB & VT)
                                           │
                                    Multi-Signal Evaluator
                                           │
                                    5 Explicit Assessment States
```

---

## 2. Core Capabilities

### Feature 1: Neural Search Autocomplete
- **Real-Time Autocomplete:** Detects search boxes across web pages and provides intelligent query suggestions as you type.
- **Deep Learning Ranking:** Instead of simple frequency counts or alphabetical sorting, candidate queries retrieved from an SQLite prefix index are scored and re-ranked using a trained character/word hybrid LSTM model.
- **SPA-Friendly Dynamic Detection:** Automatically detects dynamically injected search inputs (e.g., in React, Vue, Angular apps) using a debounced `MutationObserver`.
- **Keyboard & Mouse Navigation:** Seamlessly navigate suggestions using <kbd>↑</kbd> / <kbd>↓</kbd> arrows, press <kbd>Enter</kbd> to select, or click with the mouse.
- **Framework-Aware Value Dispatching:** When selecting a suggestion, native input and change events are dispatched via HTML prototype setters to trigger reactive state updates in modern frontend frameworks.
- **Zero Disruption / Graceful Failure:** If the backend is offline or slow, the extension fails silently without error banners, preserving default browser and webpage search behavior.

### Feature 2: Website Trust & Safety Check
- **Active Tab Inspection:** When opening the extension popup while browsing any website, the extension inspects the active browser tab's domain and displays a compact multi-signal trust assessment.
- **5 Explicit Assessment States:** Replaces naive binary "legit/fake" classifiers with 5 auditable states:
  - 🛑 `known_threat`: Confirmed hit in threat intelligence databases (Google Safe Browsing, VirusTotal, or local blocklists).
  - ⚠️ `high_risk`: Public IP address literal used as host, embedded credentials, severe homograph/punycode spoofing.
  - ⚠️ `caution`: Insecure HTTP connection (unencrypted), loopback/private network target, suspicious brand token in subdomain.
  - ✓ `low_risk`: Verified partner with HTTPS, OR positive external reputation confirmation with clean signals.
  - ℹ️ `insufficient_information`: Valid HTTPS domain with no threat hits, but unverified and without external reputation.
- **Verified Partner Directory:** Maintained in [`project/configs/verified_partners.yaml`](project/configs/verified_partners.yaml). "Not verified" strictly does **not** mean fraudulent.
- **Multi-Signal Evaluation:** Synthesizes HTTPS encryption, domain registration structure, apex extraction, IP literal detection, IDN/punycode spoofing, and external threat intelligence.
- **Zero Keystroke Overhead:** Website safety checks never execute on autocomplete keystrokes; checks trigger strictly when the popup opens.
- **Privacy Guarantees:** Strips paths, query parameters, and fragments. Only the domain/hostname needed for the security check is inspected or logged.

---

## 3. Directory Layout

```text
smartsearch_lstm_stage2/
├── README.md                          # Repository documentation
├── .gitignore                         # Git exclusion rules
├── project/
│   ├── configs/                       # Configuration files
│   │   ├── api.yaml                   # FastAPI server & trust settings
│   │   ├── verified_partners.yaml     # Partner directory registry
│   │   ├── lstm.yaml                  # LSTM model architecture
│   │   └── retrieval.yaml             # SQLite index settings
│   ├── docker-compose.yml             # Multi-container Docker deployment (API + Nginx)
│   ├── Dockerfile                     # Production container image
│   ├── docs/                          # Detailed technical documentation
│   │   ├── api.md                     # REST API reference
│   │   ├── website_trust.md           # Website trust architecture & states
│   │   ├── lstm_model.md              # Neural ranking model details
│   │   ├── prefix_index.md            # Retrieval index benchmarks
│   │   └── deployment_architecture.md # Production architecture
│   ├── extension/                     # Chrome / Chromium Manifest V3 extension
│   │   ├── manifest.json              # Extension manifest (least-privilege permissions)
│   │   ├── background/                # Service worker background scripts
│   │   ├── content/                   # Content scripts & DOM observers
│   │   ├── popup/                     # Popup interface with Website Safety Card
│   │   ├── options/                   # Options & diagnostics page
│   │   └── test-page.html             # Local verification test harness
│   ├── nginx/                         # Nginx reverse proxy configuration
│   ├── src/                           # Python source code
│   │   ├── api/                       # FastAPI application & routes
│   │   ├── models/lstm/               # PyTorch LSTM inference & training
│   │   ├── retrieval/                 # SQLite prefix index retrieval
│   │   └── trust/                     # Website trust & safety service
│   └── tests/                         # Pytest test suites (77 passing tests)
```

---

## 4. Quickstart Guide

### Option A: Run with Docker Compose (Recommended)

```powershell
cd project

# Start FastAPI and Nginx reverse proxy
docker compose up -d

# Verify health
curl.exe http://localhost/health
```

### Option B: Run Locally with Python

```powershell
cd project

# Install dependencies
pip install -r requirements.txt

# Start FastAPI server
uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
```

### Load the Extension in Chrome / Chromium

1. Open **Google Chrome** (or Edge / Brave).
2. Navigate to `chrome://extensions/`.
3. Enable **Developer mode** (top-right toggle).
4. Click **Load unpacked** (top-left button).
5. Select the `project/extension` folder.
6. Pin **SmartSearch AI** to your browser toolbar.

---

## 5. REST API Reference

### 1. `GET /api/v1/suggestions?q=<query>&top_k=5`
Retrieves neural-ranked autocomplete search suggestions for a partial query prefix.

### 2. `GET /api/v1/site-check?url=<target_url>`
Evaluates website safety, transport security, partner verification, and threat intelligence.

**Sample Response:**
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

---

## 6. Testing & Validation

### Backend Unit & Integration Tests (pytest)
```powershell
python -m pytest tests/test_website_trust.py tests/test_api.py tests/test_e2e_integration.py
```
**Results:** `77 passed, 1 skipped` (40 website trust tests, 14 API tests, 23 E2E tests).

### Extension Pure-Logic Unit Tests (Node.js)
```powershell
node extension/tests/test_extension_logic.js
```
**Results:** `43 passed, 0 failed` across 10 test suites.

### Extension Live E2E Integration Tests (Node.js <-> FastAPI)
```powershell
node extension/tests/test_e2e_extension_api.js
```
**Results:** `18 passed, 0 failed` verifying live HTTP connectivity, LSTM typing progression, and site safety checks.
