# SmartSearch AI — Chrome / Chromium Extension

> **Google-Style Search Auto-Suggestion & Query Recommendation Powered by Deep Learning**

SmartSearch AI is a browser extension that brings deep-learning-ranked search auto-suggestions to search inputs across the web. Built on Manifest V3, the extension observes search inputs on modern web pages (including dynamic Single-Page Applications like React, Vue, and Angular), debounces keystrokes, and queries a local or remote FastAPI service backed by an SQLite prefix candidate index and an LSTM ranking model.

---

## 1. What SmartSearch AI Does

- **Real-Time Autocomplete:** Detects search boxes across web pages and provides intelligent query suggestions as you type.
- **Deep Learning Ranking:** Instead of simple frequency counts or alphabetical sorting, candidate queries retrieved from a real MS MARCO prefix index are scored and re-ranked using a trained LSTM model.
- **Website Safety & Trust Check:** When opening the extension popup, inspects the active browser tab's domain and displays a compact multi-signal trust assessment (HTTPS, verified partner status, threat intelligence, domain anomalies) without simplistic binary classification.
- **SPA-Friendly Dynamic Detection:** Automatically detects dynamically injected search inputs (e.g., in React, Vue, Angular apps) using a debounced `MutationObserver`.
- **Keyboard & Mouse Navigation:** Seamlessly navigate suggestions using <kbd>↑</kbd> / <kbd>↓</kbd> arrows, press <kbd>Enter</kbd> to select, or click with the mouse.
- **Framework-Aware Value Dispatching:** When selecting a suggestion, native input and change events are dispatched via HTML prototype setters to trigger reactive state updates in modern frontend frameworks.
- **Zero Disruption / Graceful Failure:** If the backend is offline or slow, the extension fails silently without error banners, preserving default browser and webpage search behavior.

---

## 2. Architecture

```text
┌────────────────────────────────────────────────────────┐
│                   BROWSER ENVIRONMENT                  │
│                                                        │
│  User Keystroke in Search Field                        │
│         │                                              │
│         ▼                                              │
│  Content Script (`content.js` + `logic.js`)           │
│    ├── Sensitive Field Exclusion Filter                │
│    │     (Pass, Email, CC, OTP, PIN, CVV ignored)     │
│    ├── Debounce Timer (150–300 ms, default: 200 ms)   │
│    └── AbortController + Request Sequencer             │
│         │                                              │
└─────────┼──────────────────────────────────────────────┘
          │ HTTP GET /api/v1/suggestions?q=<query>&top_k=5
          ▼
┌────────────────────────────────────────────────────────┐
│                   FASTAPI BACKEND                      │
│                                                        │
│  `GET /api/v1/suggestions`                             │
│         │                                              │
│         ├── Prefix Candidate Retrieval (SQLite Index)  │
│         │     (Retrieves matching real query pool)     │
│         │                                              │
│         └── LSTM Ranker (`best_model.pt`)              │
│               (Scores and re-ranks top-K candidates)   │
│         │                                              │
│  Returns JSON:                                         │
│    { "query": "...", "suggestions": [...], "count": 5 }│
└─────────┼──────────────────────────────────────────────┘
          │
          ▼
┌────────────────────────────────────────────────────────┐
│                   BROWSER DROPDOWN                     │
│                                                        │
│  Accessible Dropdown (`content.css`)                  │
│    ├── Positioned beneath active search element        │
│    ├── Prefix match highlighted                        │
│    ├── ARIA listbox & option semantics                │
│    └── Dispatches React/Vue/Angular change events      │
└────────────────────────────────────────────────────────┘
```

---

## 3. Website Safety & Trust Check (Popup)

When you open the extension popup while browsing any website, the extension automatically inspects the active browser tab via `GET /api/v1/site-check`:

- **Compact Visual Card**: Displays the current domain, an explicit assessment state badge (`Low risk`, `Use caution`, `High risk`, `Known threat`, or `Insufficient information`), and signal indicators.
- **Clear Distinction of Concepts**:
  - `🏪 Verified Partner`: Confirmed organization from curated partner database (`configs/verified_partners.yaml`).
  - `✓ No known threat detected`: No hits in threat intelligence feeds (does **not** claim the site is guaranteed safe or official).
  - `ℹ️ Not verified`: Neutral indicator that the site is not in the partner registry. Strictly does **not** mean fraudulent.
- **Privacy Preserving**: Strips paths, queries, and credentials. Only the domain/hostname needed for the security check is inspected.
- **Zero Keystroke Overhead**: Security checks never run on autocomplete typing; checks trigger strictly on popup display or navigation.

---

## 4. How to Start FastAPI

Before using the extension, the SmartSearch AI FastAPI backend must be running.

### Prerequisites
- Python 3.10+ (with project virtual environment activated)
- Model artifacts: `models/lstm/best_model.pt` & `models/lstm/tokenizer.json`
- Index database: `data/indexes/prefix_real/prefix_index.sqlite3`

### Command (from the project directory)

```powershell
# Navigate to the project root
cd c:\Users\JAPNOOR\Downloads\smartsearch_lstm_stage2\project

# Start the FastAPI application with Uvicorn
uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
```

### Verify Server Health
Open a browser or run in PowerShell:
```powershell
curl http://127.0.0.1:8000/health
```
Expected response:
```json
{
  "status": "healthy",
  "model_loaded": true,
  "index_connected": true,
  "device": "cpu"
}
```

---

## 5. How to Load the Extension in Chrome / Chromium

The extension is built with **Manifest V3** and can be loaded directly as an unpacked extension.

1. Open **Google Chrome** (or Microsoft Edge, Brave, Chromium).
2. In the address bar, navigate to:
   ```text
   chrome://extensions/
   ```
3. Enable **Developer mode** using the toggle switch in the top-right corner.
4. Click the **Load unpacked** button in the top-left toolbar.
5. In the file picker dialog, select the `extension/` directory:
   ```text
   c:\Users\JAPNOOR\Downloads\smartsearch_lstm_stage2\project\extension
   ```
6. The **SmartSearch AI — Search Autocomplete** extension will appear in your extensions list.
7. Click the extension puzzle icon in the Chrome toolbar and pin **SmartSearch AI** for quick access.

> **Note:** This extension is distributed as unpacked source code for local development and academic evaluation. It is not published to the Chrome Web Store.

---

## 6. How to Configure the API URL

Click the **SmartSearch AI icon** in the Chrome toolbar to open the popup interface:

1. **Toggle Switch:** Turn suggestions on or off instantly.
2. **Backend Status Banner:** Displays live connectivity to the FastAPI server (`FastAPI Online (CPU)` or `FastAPI Offline`).
3. **API Base URL:** Default is `http://localhost` (Docker/Nginx production gateway). You can change this to any reachable endpoint (e.g., `http://127.0.0.1:8000` for direct FastAPI development or a remote reverse proxy).
4. **Top-K Suggestions:** Number of suggestions to display (1 to 20, default: `5`).
5. **Debounce (ms):** Wait time after typing stops before querying the API (50ms to 1000ms, default: `200ms`).
6. Click **Save Settings** to synchronize across all browser tabs via `chrome.storage.sync`.
7. Click **Options** to view the full settings and diagnostics page.

---

## 7. How Autocomplete Works

1. **Input Detection:**
   - Supported fields: `<input type="search">`, elements with `role="searchbox"`, inputs inside search forms, or inputs with search-related `name`/`id`/`placeholder` tokens (e.g., `q`, `query`, `search`).
   - Dynamically injected inputs are automatically tracked via `MutationObserver`.
2. **Debouncing & Cancellation:**
   - Keystrokes are debounced by default for 200 ms.
   - If the user types a new character before an in-flight request finishes, the previous request is aborted via `AbortController`.
   - A sequential request tracker ensures out-of-order responses can never overwrite newer query suggestions.
3. **Candidate Scoring & Display:**
   - FastAPI retrieves prefix candidates from the SQLite index and evaluates likelihood scores with the trained LSTM.
   - The dropdown renders matching prefix text in bold, hiding internal LSTM floating-point scores from end-users.
4. **Selection & Dispatch:**
   - Selecting a suggestion via <kbd>Enter</kbd> or mouse click populates the field and dispatches native bubbling `input` and `change` events, ensuring frameworks like React update their internal state.

---

## 8. Privacy Behavior & Guarantees

SmartSearch AI enforces strict client-side privacy:

- **Minimal Data Transmission:** Only the characters typed into an active, validated search input are sent to `GET /api/v1/suggestions`.
- **Zero Sensitive Field Access:** The extension strictly ignores:
  - Password fields (`type="password"`, `current-password`, `new-password`)
  - Email fields (`type="email"`)
  - Credit card and payment fields (`cc-number`, `cvv`, `cvc`, `cc-exp`)
  - OTP and 2FA PIN fields (`one-time-code`, `pin`, `otp`, `secret`)
  - Telephone, number, hidden, and file upload fields
- **No Browsing History Collection:** The extension does not request the `history`, `tabs`, `cookies`, or `webNavigation` permissions.
- **No Page Scraping:** DOM content, full page text, and neighboring form fields are never read, analyzed, or uploaded.
- **No Telemetry / Tracking:** No user identifiers or query logs are collected or persisted to external services.

---

## 9. Supported Browsers

SmartSearch AI adheres to the W3C WebExtensions Manifest V3 standard:

| Browser | Compatibility | Notes |
| :--- | :---: | :--- |
| **Google Chrome** | Full (v88+) | Native Manifest V3 support |
| **Microsoft Edge** | Full (v88+) | Chromium-based; supports unpacked loading via `edge://extensions` |
| **Brave** | Full | Chromium-based; identical loading flow |
| **Opera** | Full | Chromium-based; requires Developer Mode |

---

## 10. Local Verification Page

To test all extension features without visiting external sites:

1. Open Chrome.
2. Press <kbd>Ctrl</kbd> + <kbd>O</kbd> (or drag and drop into Chrome) to open:
   ```text
   c:\Users\JAPNOOR\Downloads\smartsearch_lstm_stage2\project\extension\test-page.html
   ```
3. Test scenarios:
   - **Search Input (`type="search"`):** Start typing a query like `"what is"` or `"deep"` to observe live debounced suggestions.
   - **Form Query (`name="q"`):** Observe standard query field autocomplete.
   - **Dynamic Search Input:** Click **"Generate Dynamic Search Input"** to test SPA `MutationObserver` auto-detection.
   - **Password & Email Fields:** Type into these fields to verify that the extension completely ignores sensitive inputs.
   - **Event Logger:** Inspect dispatched `input` and `change` events live at the bottom of the page.

---

## 11. Troubleshooting

### Dropdown does not appear
- Ensure the FastAPI server is running (`curl http://localhost/health` or `curl http://127.0.0.1:8000/health`).
- Open the extension popup from the toolbar and confirm the status banner displays **FastAPI Online**.
- Verify that the input field is a search field (has `type="search"`, `name="q"`, or placeholder with `"search"`).
- Ensure SmartSearch is toggled **ON** in the popup.

### Suggestions are slow or out of sync
- In the popup or options page, adjust the debounce delay (e.g., set to 150 ms or 200 ms).
- Verify host machine CPU load during LSTM PyTorch inference.

### "Could not establish connection. Receiving end does not exist."
- Reload the extension in `chrome://extensions/` by clicking the refresh icon on the SmartSearch card, then refresh the target webpage.

---

## 12. Development Limitations

- **Localhost Bound:** By default, the extension targets `http://localhost`. To access an API on another host, update `host_permissions` in `manifest.json` or configure a custom URL in the Options page.
- **Unpacked Distribution:** Intended for local development, academic defense, and research testing.
- **Isolated CSS:** Dropdown styles are namespaced under `.smartsearch-*` with a maximum `z-index` of `2147483647`, though aggressive `overflow: hidden` on parent containers on certain complex websites may require position tweaking.
