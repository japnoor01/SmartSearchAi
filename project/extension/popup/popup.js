/**
 * extension/popup/popup.js
 *
 * Interactive logic for SmartSearch AI extension popup.
 * Manages user preferences, storage synchronization, backend health status,
 * and the Website Trust & Safety Check for the active browser tab.
 */

document.addEventListener("DOMContentLoaded", () => {
  const toggleEnabled = document.getElementById("toggle-enabled");
  const statusBanner = document.getElementById("status-banner");
  const statusText = document.getElementById("status-text");
  const apiUrlInput = document.getElementById("api-url");
  const topKInput = document.getElementById("top-k");
  const debounceInput = document.getElementById("debounce-ms");
  const settingsForm = document.getElementById("settings-form");
  const optionsBtn = document.getElementById("options-btn");
  const saveFeedback = document.getElementById("save-feedback");

  // Trust Card UI Elements
  const trustBadge = document.getElementById("trust-badge");
  const trustDomainVal = document.getElementById("trust-domain-val");
  const trustSignalsList = document.getElementById("trust-signals-list");
  const trustNotice = document.getElementById("trust-notice");

  const DEFAULT_SETTINGS = {
    enabled: true,
    apiBaseUrl: "http://localhost",
    topK: 5,
    debounceMs: 200,
  };

  // 1. Load saved settings & run initial checks
  chrome.storage.sync.get(DEFAULT_SETTINGS, (settings) => {
    toggleEnabled.checked = settings.enabled;
    apiUrlInput.value = settings.apiBaseUrl;
    topKInput.value = settings.topK;
    debounceInput.value = settings.debounceMs;

    checkBackendHealth(settings.apiBaseUrl);
    checkActiveTabTrust(settings.apiBaseUrl);
  });

  // 2. Health check against FastAPI backend
  async function checkBackendHealth(baseUrl) {
    statusBanner.className = "status-banner status-loading";
    statusText.textContent = "Connecting to FastAPI...";

    const cleanUrl = (baseUrl || DEFAULT_SETTINGS.apiBaseUrl).replace(/\/+$/, "");
    try {
      const response = await fetch(`${cleanUrl}/health`, { method: "GET" });
      if (response.ok) {
        const data = await response.json();
        statusBanner.className = "status-banner status-online";
        statusText.textContent = `FastAPI Online (${(data.device || "CPU").toUpperCase()})`;
      } else {
        statusBanner.className = "status-banner status-offline";
        statusText.textContent = `Backend Error (HTTP ${response.status})`;
      }
    } catch (err) {
      statusBanner.className = "status-banner status-offline";
      statusText.textContent = "FastAPI Offline (Not Reachable)";
    }
  }

  // 3. Website Trust & Safety Check for Active Tab
  function checkActiveTabTrust(baseUrl) {
    if (!chrome.tabs || !chrome.tabs.query) {
      setTrustUnavailable("Tab inspection unavailable in this context");
      return;
    }

    setTrustLoading();

    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (!tabs || tabs.length === 0 || !tabs[0].url) {
        setTrustUnavailable("No active web page detected.");
        return;
      }

      const activeUrl = tabs[0].url;

      // Handle internal or non-inspectable browser schemes
      if (
        activeUrl.startsWith("chrome://") ||
        activeUrl.startsWith("chrome-extension://") ||
        activeUrl.startsWith("edge://") ||
        activeUrl.startsWith("about:") ||
        activeUrl.startsWith("devtools://") ||
        activeUrl.startsWith("view-source:")
      ) {
        setInternalPageUI(activeUrl);
        return;
      }

      let parsedHost = "";
      try {
        parsedHost = new URL(activeUrl).hostname;
      } catch (e) {
        parsedHost = "Unknown domain";
      }

      if (trustDomainVal) {
        trustDomainVal.textContent = parsedHost || "Current site";
      }
      setTrustLoading();

      // Send to background service worker which handles CORS & endpoint routing
      chrome.runtime.sendMessage(
        {
          type: "CHECK_SITE_TRUST",
          url: activeUrl,
          apiBaseUrl: baseUrl,
        },
        (response) => {
          if (chrome.runtime.lastError) {
            setTrustUnavailable(chrome.runtime.lastError.message || "Background worker error");
            return;
          }

          if (!response || response.error) {
            setTrustUnavailable((response && response.error) || "Check failed");
            return;
          }

          renderTrustAssessment(response.data);
        }
      );
    });
  }

  function setTrustLoading() {
    if (trustBadge) {
      trustBadge.className = "trust-badge badge-loading";
      trustBadge.textContent = "Checking...";
    }
    if (trustNotice) {
      trustNotice.style.display = "none";
    }
  }

  function setInternalPageUI(url) {
    if (trustBadge) {
      trustBadge.className = "trust-badge badge-insufficient";
      trustBadge.textContent = "Internal Page";
    }
    if (trustDomainVal) {
      try {
        const parsed = new URL(url);
        trustDomainVal.textContent = parsed.protocol + "//" + (parsed.hostname || "browser");
      } catch (_) {
        trustDomainVal.textContent = "Browser page";
      }
    }

    if (trustSignalsList) {
      trustSignalsList.innerHTML = `
        <div class="trust-signal-item signal-info">
          <span class="signal-bullet">ℹ️</span>
          <span class="signal-text">Browser internal page — website safety checks do not apply.</span>
        </div>
      `;
    }
    if (trustNotice) {
      trustNotice.style.display = "none";
    }
  }

  function setTrustUnavailable(reason) {
    if (trustBadge) {
      trustBadge.className = "trust-badge badge-insufficient";
      trustBadge.textContent = "Unavailable";
    }
    if (trustSignalsList) {
      trustSignalsList.innerHTML = `
        <div class="trust-signal-item signal-info">
          <span class="signal-bullet">ℹ️</span>
          <span class="signal-text">Site check service unavailable: ${escapeHtml(reason)}</span>
        </div>
      `;
    }
    if (trustNotice) {
      trustNotice.style.display = "none";
    }
  }

  function renderTrustAssessment(data) {
    if (!data) return;

    const Logic = (typeof window !== "undefined" && window.SmartSearchLogic) || null;

    // 1. Assessment badge
    const badgeInfo = Logic && Logic.formatAssessmentBadge
      ? Logic.formatAssessmentBadge(data.assessment)
      : { label: data.assessment || "Unknown", className: "badge-insufficient", icon: "ℹ️" };

    if (trustBadge) {
      trustBadge.className = `trust-badge ${badgeInfo.className}`;
      trustBadge.textContent = badgeInfo.label;
    }
    if (trustDomainVal) {
      trustDomainVal.textContent = data.domain || "Current site";
    }

    // 2. Format individual signal rows
    const signalsHtml = [];

    // HTTPS Signal
    const isHttps = data.security_signals && data.security_signals.https_enabled;
    if (isHttps) {
      signalsHtml.push(`
        <div class="trust-signal-item signal-success">
          <span class="signal-bullet">✓</span>
          <span class="signal-text">HTTPS enabled</span>
        </div>
      `);
    } else {
      signalsHtml.push(`
        <div class="trust-signal-item signal-warning">
          <span class="signal-bullet">⚠️</span>
          <span class="signal-text">Insecure connection (HTTP / No TLS)</span>
        </div>
      `);
    }

    // Threat Status Signal
    const threatInfo = Logic && Logic.formatThreatBadge
      ? Logic.formatThreatBadge(data.known_threat, data.security_signals && data.security_signals.threat_type)
      : {
          text: data.known_threat ? "Known threat detected" : "No known threat detected",
          hasThreat: data.known_threat,
          icon: data.known_threat ? "🛑" : "✓",
        };

    signalsHtml.push(`
      <div class="trust-signal-item ${threatInfo.hasThreat ? "signal-danger" : "signal-success"}">
        <span class="signal-bullet">${threatInfo.icon}</span>
        <span class="signal-text">${escapeHtml(threatInfo.text)}</span>
      </div>
    `);

    // Partner Status Signal
    const partnerInfo = Logic && Logic.formatPartnerBadge
      ? Logic.formatPartnerBadge(data.verified_partner, data.partner_details)
      : {
          text: data.verified_partner ? "Verified Partner" : "Not verified (does not mean fraudulent)",
          isPartner: data.verified_partner,
          icon: data.verified_partner ? "🏪" : "ℹ️",
        };

    signalsHtml.push(`
      <div class="trust-signal-item ${partnerInfo.isPartner ? "signal-success" : "signal-info"}">
        <span class="signal-bullet">${partnerInfo.icon}</span>
        <span class="signal-text">${escapeHtml(partnerInfo.text)}</span>
      </div>
    `);

    // Detailed reasons or warnings
    if (Array.isArray(data.reasons) && data.reasons.length > 0) {
      for (const reason of data.reasons) {
        if (
          reason.includes("Verified partner:") ||
          reason === "Verified partner" ||
          reason === "Unencrypted HTTP" ||
          reason.includes("Known threat detected")
        ) {
          continue;
        }
        signalsHtml.push(`
          <div class="trust-signal-item signal-warning">
            <span class="signal-bullet">⚠️</span>
            <span class="signal-text">${escapeHtml(reason)}</span>
          </div>
        `);
      }
    }

    if (trustSignalsList) {
      trustSignalsList.innerHTML = signalsHtml.join("");
    }

    // Notice Box for Context / Disclaimers / Unavailable checks
    const notices = [];
    if (data.assessment === "insufficient_information") {
      notices.push("No reputation data found. Exercise standard caution.");
    } else if (data.assessment === "low_risk" && !data.verified_partner) {
      notices.push("No known threat detected does not guarantee a site is official.");
    }

    if (Array.isArray(data.unavailable_checks) && data.unavailable_checks.length > 0) {
      notices.push(`Unavailable checks: ${data.unavailable_checks.join(", ")}`);
    }

    if (trustNotice) {
      if (notices.length > 0) {
        trustNotice.textContent = notices.join(" • ");
        trustNotice.style.display = "block";
      } else {
        trustNotice.style.display = "none";
      }
    }
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // 4. Quick toggle enable/disable
  toggleEnabled.addEventListener("change", () => {
    const isEnabled = toggleEnabled.checked;
    chrome.storage.sync.set({ enabled: isEnabled }, () => {
      showFeedback(isEnabled ? "SmartSearch enabled" : "SmartSearch disabled");
    });
  });

  // 5. Save form settings
  settingsForm.addEventListener("submit", (e) => {
    e.preventDefault();

    const Logic = (typeof window !== "undefined" && window.SmartSearchLogic) || null;

    if (Logic) {
      const validation = Logic.validateSettings({
        apiBaseUrl: apiUrlInput.value,
        topK: topKInput.value,
        debounceMs: debounceInput.value,
        enabled: toggleEnabled.checked,
      });

      if (!validation.valid) {
        showFeedback(validation.errors[0] || "Invalid settings");
        return;
      }

      chrome.storage.sync.set(validation.sanitized, () => {
        showFeedback("Settings saved!");
        checkBackendHealth(validation.sanitized.apiBaseUrl);
        checkActiveTabTrust(validation.sanitized.apiBaseUrl);
      });
      return;
    }

    let rawUrl = apiUrlInput.value.trim() || DEFAULT_SETTINGS.apiBaseUrl;
    rawUrl = rawUrl.replace(/\/+$/, "");

    const topK = Math.max(1, Math.min(20, parseInt(topKInput.value, 10) || 5));
    const debounceMs = Math.max(50, Math.min(1000, parseInt(debounceInput.value, 10) || 200));

    const updated = {
      apiBaseUrl: rawUrl,
      topK: topK,
      debounceMs: debounceMs,
    };

    chrome.storage.sync.set(updated, () => {
      showFeedback("Settings saved!");
      checkBackendHealth(rawUrl);
      checkActiveTabTrust(rawUrl);
    });
  });

  // 6. Open full options page
  optionsBtn.addEventListener("click", () => {
    if (chrome.runtime.openOptionsPage) {
      chrome.runtime.openOptionsPage();
    } else {
      window.open(chrome.runtime.getURL("options/options.html"));
    }
  });

  function showFeedback(msg) {
    saveFeedback.textContent = msg;
    saveFeedback.style.display = "block";
    setTimeout(() => {
      saveFeedback.style.display = "none";
    }, 2000);
  }
});
