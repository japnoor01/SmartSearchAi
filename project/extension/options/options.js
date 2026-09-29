/**
 * extension/options/options.js
 *
 * Controller for the SmartSearch AI settings and options page.
 */

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("options-form");
  const apiUrlInput = document.getElementById("api-url");
  const topKInput = document.getElementById("top-k");
  const debounceInput = document.getElementById("debounce-ms");
  const enableToggle = document.getElementById("enable-toggle");
  const testConnBtn = document.getElementById("test-conn-btn");
  const connResult = document.getElementById("conn-result");
  const resetBtn = document.getElementById("reset-btn");
  const saveMsg = document.getElementById("save-msg");

  const DEFAULTS = {
    enabled: true,
    apiBaseUrl: "http://localhost",
    topK: 5,
    debounceMs: 200,
  };

  // 1. Load saved preferences
  chrome.storage.sync.get(DEFAULTS, (settings) => {
    enableToggle.checked = settings.enabled;
    apiUrlInput.value = settings.apiBaseUrl;
    topKInput.value = settings.topK;
    debounceInput.value = settings.debounceMs;
  });

  // 2. Test Connection
  testConnBtn.addEventListener("click", async () => {
    let url = apiUrlInput.value.trim();
    if (!url) url = DEFAULTS.apiBaseUrl;
    url = url.replace(/\/+$/, "");

    connResult.style.display = "block";
    connResult.className = "conn-result";
    connResult.textContent = `Connecting to ${url}/health...`;

    const t0 = performance.now();
    try {
      const response = await fetch(`${url}/health`, { method: "GET" });
      const elapsed = Math.round(performance.now() - t0);

      if (response.ok) {
        const data = await response.json();
        connResult.className = "conn-result success";
        connResult.textContent = `✓ Connected successfully (${elapsed}ms)! Model: LSTM Ranker (${data.device.toUpperCase()}), Status: ${data.status}.`;
      } else {
        connResult.className = "conn-result error";
        connResult.textContent = `✗ Server responded with HTTP ${response.status}: ${response.statusText}.`;
      }
    } catch (err) {
      connResult.className = "conn-result error";
      connResult.textContent = `✗ Connection failed: ${err.message}. Ensure the FastAPI server is running on ${url}.`;
    }
  });

  // 3. Save Changes
  form.addEventListener("submit", (e) => {
    e.preventDefault();

    const Logic = (typeof window !== "undefined" && window.SmartSearchLogic) || null;

    if (Logic) {
      const validation = Logic.validateSettings({
        apiBaseUrl: apiUrlInput.value,
        topK: topKInput.value,
        debounceMs: debounceInput.value,
        enabled: enableToggle.checked,
      });

      if (!validation.valid) {
        alert("Validation error:\n" + validation.errors.join("\n"));
        return;
      }

      chrome.storage.sync.set(validation.sanitized, () => {
        saveMsg.style.display = "inline";
        setTimeout(() => {
          saveMsg.style.display = "none";
        }, 2500);
      });
      return;
    }

    let rawUrl = apiUrlInput.value.trim() || DEFAULTS.apiBaseUrl;
    try {
      const parsed = new URL(rawUrl);
      if (!["http:", "https:"].includes(parsed.protocol)) {
        throw new Error("Protocol must be http or https");
      }
      rawUrl = `${parsed.protocol}//${parsed.host}`;
    } catch {
      alert("Please provide a valid HTTP or HTTPS API URL.");
      return;
    }

    const topK = Math.max(1, Math.min(20, parseInt(topKInput.value, 10) || 5));
    const debounceMs = Math.max(50, Math.min(1000, parseInt(debounceInput.value, 10) || 200));
    const enabled = enableToggle.checked;

    const newSettings = {
      enabled,
      apiBaseUrl: rawUrl,
      topK,
      debounceMs,
    };

    chrome.storage.sync.set(newSettings, () => {
      saveMsg.style.display = "inline";
      setTimeout(() => {
        saveMsg.style.display = "none";
      }, 2500);
    });
  });

  // 4. Reset Defaults
  resetBtn.addEventListener("click", () => {
    if (confirm("Reset all settings to production defaults?")) {
      chrome.storage.sync.set(DEFAULTS, () => {
        enableToggle.checked = DEFAULTS.enabled;
        apiUrlInput.value = DEFAULTS.apiBaseUrl;
        topKInput.value = DEFAULTS.topK;
        debounceInput.value = DEFAULTS.debounceMs;
        connResult.style.display = "none";

        saveMsg.textContent = "Reset to defaults!";
        saveMsg.style.display = "inline";
        setTimeout(() => {
          saveMsg.style.display = "none";
          saveMsg.textContent = "Changes saved successfully!";
        }, 2500);
      });
    }
  });
});
