/**
 * extension/background/service-worker.js
 *
 * Manifest V3 Background Service Worker for SmartSearch AI.
 * Handles initial storage setup and background API health verification.
 * Adheres to strict privacy: does not store or process user query history.
 */

const DEFAULT_CONFIG = {
  enabled: true,
  apiBaseUrl: "https://emphatic-tackle-monetary.ngrok-free.dev",
  topK: 5,
  debounceMs: 200,
};

// Initialize default settings on install or update
chrome.runtime.onInstalled.addListener(async (details) => {
  console.log(`[SmartSearch AI] Extension ${details.reason} (v1.0.0)`);
  try {
    const existing = await chrome.storage.sync.get(Object.keys(DEFAULT_CONFIG));
    const merged = { ...DEFAULT_CONFIG, ...existing };
    await chrome.storage.sync.set(merged);
    console.log("[SmartSearch AI] Configuration initialized:", merged);
  } catch (err) {
    console.error("[SmartSearch AI] Error initializing storage:", err);
  }
});

// Message listener for popup/options connectivity checks & content script suggestion fetches
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const action = message && (message.action || message.type);

  if (action === "CHECK_HEALTH") {
    const apiBaseUrl = message.apiBaseUrl || DEFAULT_CONFIG.apiBaseUrl;
    fetch(`${apiBaseUrl}/health`, {
      method: "GET",
      headers: { "ngrok-skip-browser-warning": "true" },
    })
      .then(async (res) => {
        if (!res.ok) {
          sendResponse({ ok: false, status: res.status });
          return;
        }
        const data = await res.json();
        sendResponse({ ok: true, data });
      })
      .catch((err) => {
        sendResponse({ ok: false, error: err.message });
      });
    return true; // Keep message channel open for async response
  }

  if (action === "FETCH_SUGGESTIONS") {
    fetch(message.url, {
      method: "GET",
      headers: {
        Accept: "application/json",
        "ngrok-skip-browser-warning": "true",
      },
    })
      .then(async (res) => {
        if (!res.ok) {
          sendResponse({ ok: false, status: res.status });
          return;
        }
        const data = await res.json();
        sendResponse({ ok: true, data });
      })
      .catch((err) => {
        sendResponse({ ok: false, error: err.message });
      });
    return true; // Keep message channel open for async response
  }

  if (action === "CHECK_SITE_TRUST") {
    const apiBaseUrl = message.apiBaseUrl || DEFAULT_CONFIG.apiBaseUrl;
    const cleanBase = apiBaseUrl.replace(/\/+$/, "");
    const targetUrl = `${cleanBase}/api/v1/site-check?url=${encodeURIComponent(message.url)}`;

    fetch(targetUrl, {
      method: "GET",
      headers: {
        Accept: "application/json",
        "ngrok-skip-browser-warning": "true",
      },
    })
      .then(async (res) => {
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          sendResponse({
            ok: false,
            status: res.status,
            error: errData.detail || `Server error (HTTP ${res.status})`,
          });
          return;
        }
        const data = await res.json();
        sendResponse({ ok: true, data });
      })
      .catch((err) => {
        sendResponse({ ok: false, error: err.message });
      });
    return true; // Keep message channel open for async response
  }
});


