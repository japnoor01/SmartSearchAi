/**
 * extension/content/content.js
 *
 * Content Script for SmartSearch AI.
 * Autonomously detects search inputs on modern web pages (including dynamic SPAs),
 * debounces API calls to FastAPI, manages in-flight request lifecycle,
 * and displays an accessible autocomplete dropdown with keyboard & mouse navigation.
 *
 * Privacy & Security:
 *   - Strictly ignores password, email, credit card, OTP, and sensitive inputs.
 *   - Sends ONLY the partial query to the configured local/remote FastAPI server.
 *   - Never inspects or transmits page contents, cookies, or user credentials.
 */

(() => {
  // Global singleton configuration (defaults overridden from chrome.storage)
  const config = {
    enabled: true,
    apiBaseUrl: "http://localhost",
    topK: 5,
    debounceMs: 200,
  };

  // State
  let activeInput = null;
  let dropdownEl = null;
  let currentItems = [];
  let selectedIndex = -1;
  let debounceTimer = null;
  let currentAbortController = null;
  let currentRequestId = 0;

  // ---------------------------------------------------------------------------
  // 1. Configuration & Storage Sync
  // ---------------------------------------------------------------------------

  function loadConfiguration() {
    if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.sync) {
      chrome.storage.sync.get(["enabled", "apiBaseUrl", "topK", "debounceMs"], (items) => {
        if (items.enabled !== undefined) config.enabled = items.enabled;
        if (items.apiBaseUrl) config.apiBaseUrl = items.apiBaseUrl.replace(/\/+$/, "");
        if (items.topK) config.topK = parseInt(items.topK, 10) || 5;
        if (items.debounceMs) config.debounceMs = parseInt(items.debounceMs, 10) || 200;
      });

      chrome.storage.onChanged.addListener((changes, areaName) => {
        if (areaName === "sync") {
          if (changes.enabled) config.enabled = changes.enabled.newValue;
          if (changes.apiBaseUrl) config.apiBaseUrl = changes.apiBaseUrl.newValue.replace(/\/+$/, "");
          if (changes.topK) config.topK = parseInt(changes.topK.newValue, 10) || 5;
          if (changes.debounceMs) config.debounceMs = parseInt(changes.debounceMs.newValue, 10) || 200;
          if (!config.enabled) hideDropdown();
        }
      });
    }
  }

  // ---------------------------------------------------------------------------
  // 2. Sensitive Field Exclusion & Search Input Detection
  // ---------------------------------------------------------------------------

  const Logic = (typeof window !== "undefined" && window.SmartSearchLogic) || null;

  const SENSITIVE_TYPES = new Set([
    "password", "email", "tel", "number", "hidden",
    "file", "submit", "button", "reset", "checkbox", "radio"
  ]);

  const SENSITIVE_AUTOCOMPLETE = new Set([
    "current-password", "new-password", "cc-number", "cc-csc",
    "cc-exp", "cc-type", "one-time-code", "bday", "transaction-amount"
  ]);

  const SENSITIVE_NAME_REGEX = /pass|pwd|password|secret|credit|card|cvv|cvc|token|auth|otp|pin|ssn|tax|security|bank/i;
  const SEARCH_NAME_REGEX = /search|query|\bq\b|suggest|autocomplete|lookup|find/i;

  function isSearchInput(element) {
    if (!element || !(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement)) {
      return false;
    }

    const form = element.closest("form");
    const attrs = {
      type: element.getAttribute("type") || "text",
      autocomplete: element.getAttribute("autocomplete") || "",
      name: element.name || "",
      id: element.id || "",
      placeholder: element.getAttribute("placeholder") || "",
      ariaLabel: element.getAttribute("aria-label") || "",
      className: element.className || "",
      role: element.getAttribute("role") || "",
      formRole: form ? (form.getAttribute("role") || "") : "",
      formAction: form ? (form.getAttribute("action") || "") : "",
    };

    if (Logic) {
      return Logic.isSearchField(attrs);
    }

    const type = attrs.type.toLowerCase();

    // 1. Strict rejection of sensitive types
    if (SENSITIVE_TYPES.has(type)) return false;

    // 2. Strict rejection of sensitive autocomplete markers
    if (SENSITIVE_AUTOCOMPLETE.has(attrs.autocomplete.toLowerCase())) return false;

    // 3. Strict rejection of sensitive attribute tokens
    const attrTokens = [attrs.name, attrs.id, attrs.placeholder, attrs.ariaLabel, attrs.className].join(" ");
    if (SENSITIVE_NAME_REGEX.test(attrTokens)) return false;

    // 4. Affirmative search detection
    if (type === "search" || attrs.role === "searchbox") return true;
    if (attrs.formRole === "search" || attrs.formAction.includes("search")) return true;
    if (SEARCH_NAME_REGEX.test(attrTokens)) return true;

    return false;
  }

  // ---------------------------------------------------------------------------
  // 3. Dropdown UI Construction & Positioning
  // ---------------------------------------------------------------------------

  function getOrCreateDropdown() {
    if (!dropdownEl) {
      dropdownEl = document.createElement("div");
      dropdownEl.id = "smartsearch-dropdown-menu";
      dropdownEl.className = "smartsearch-dropdown";
      dropdownEl.setAttribute("role", "listbox");
      dropdownEl.setAttribute("aria-label", "Search suggestions");
      dropdownEl.style.display = "none";
      document.body.appendChild(dropdownEl);
    }
    return dropdownEl;
  }

  function positionDropdown(input) {
    if (!dropdownEl || !input) return;

    const rect = input.getBoundingClientRect();
    const scrollX = window.scrollX || window.pageXOffset || 0;
    const scrollY = window.scrollY || window.pageYOffset || 0;

    dropdownEl.style.top = `${rect.bottom + scrollY + 4}px`;
    dropdownEl.style.left = `${rect.left + scrollX}px`;
    dropdownEl.style.width = `${Math.max(rect.width, 240)}px`;
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function renderDropdown(input, suggestions, query) {
    const dropdown = getOrCreateDropdown();
    dropdown.innerHTML = "";
    currentItems = suggestions;
    selectedIndex = -1;

    if (!suggestions || suggestions.length === 0) {
      hideDropdown();
      return;
    }

    const queryLower = query.toLowerCase();

    suggestions.forEach((item, index) => {
      const text = item.text || item.suggestion || "";
      const textLower = text.toLowerCase();

      // Bold matching prefix portion
      let displayHtml = escapeHtml(text);
      if (textLower.startsWith(queryLower) && queryLower.length > 0) {
        const matchPart = escapeHtml(text.slice(0, query.length));
        const restPart = escapeHtml(text.slice(query.length));
        displayHtml = `<span class="smartsearch-match">${matchPart}</span>${restPart}`;
      }

      const row = document.createElement("div");
      row.className = "smartsearch-item";
      row.id = `smartsearch-opt-${index}`;
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", "false");
      row.dataset.index = String(index);
      row.dataset.text = text;

      // Magnifying glass icon SVG
      const iconHtml = `
        <span class="smartsearch-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
        </span>
      `;

      row.innerHTML = `${iconHtml}<span class="smartsearch-text">${displayHtml}</span>`;

      // Mouse events
      row.addEventListener("mouseenter", () => {
        setSelectedIndex(index);
      });

      row.addEventListener("mousedown", (e) => {
        // Prevent blur on the input from closing before selection completes
        e.preventDefault();
        applySuggestion(text, input);
      });

      dropdown.appendChild(row);
    });

    // Add subtle footer branding
    const footer = document.createElement("div");
    footer.className = "smartsearch-footer";
    footer.innerHTML = `
      <span class="smartsearch-footer-badge">
        <svg viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z"/>
        </svg>
        SmartSearch AI
      </span>
      <span class="smartsearch-footer-hint">↑↓ navigate · ↵ select · esc</span>
    `;
    dropdown.appendChild(footer);

    positionDropdown(input);
    dropdown.style.display = "block";
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-controls", "smartsearch-dropdown-menu");
  }

  function hideDropdown() {
    if (dropdownEl) {
      dropdownEl.style.display = "none";
      dropdownEl.innerHTML = "";
    }
    if (activeInput) {
      activeInput.setAttribute("aria-expanded", "false");
      activeInput.removeAttribute("aria-activedescendant");
    }
    currentItems = [];
    selectedIndex = -1;
  }

  function setSelectedIndex(index) {
    if (!dropdownEl) return;
    const items = dropdownEl.querySelectorAll(".smartsearch-item");

    items.forEach((el, i) => {
      if (i === index) {
        el.classList.add("smartsearch-item-selected");
        el.setAttribute("aria-selected", "true");
        if (activeInput) {
          activeInput.setAttribute("aria-activedescendant", el.id);
        }
        el.scrollIntoView({ block: "nearest" });
      } else {
        el.classList.remove("smartsearch-item-selected");
        el.setAttribute("aria-selected", "false");
      }
    });

    selectedIndex = index;
  }

  // ---------------------------------------------------------------------------
  // 4. Input Value Application (React / Vue / Angular Compatible)
  // ---------------------------------------------------------------------------

  function applySuggestion(text, input) {
    if (!input) return;

    // Use native prototype setter to bypass React/Vue synthetic value overrides
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value"
    )?.set;

    if (nativeSetter) {
      nativeSetter.call(input, text);
    } else {
      input.value = text;
    }

    // Dispatch native bubbling input and change events
    input.dispatchEvent(new Event("input", { bubbles: true, cancelable: true }));
    input.dispatchEvent(new Event("change", { bubbles: true, cancelable: true }));

    hideDropdown();
    input.focus();
  }

  // ---------------------------------------------------------------------------
  // 5. Debounced API Client & Race Condition Avoidance
  // ---------------------------------------------------------------------------

  async function fetchSuggestions(query, targetInput) {
    if (!config.enabled) return;

    // Cancel in-flight stale request
    if (currentAbortController) {
      currentAbortController.abort();
    }
    currentAbortController = new AbortController();
    const requestId = ++currentRequestId;

    const url = Logic
      ? Logic.buildApiUrl(config.apiBaseUrl, query, config.topK)
      : `${config.apiBaseUrl}/api/v1/suggestions?q=${encodeURIComponent(query)}&top_k=${config.topK}`;

    try {
      let data = null;

      // 1. Attempt fetch through background service worker (bypasses webpage CORS, file:// origin, & Brave Shields)
      if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
        try {
          const resp = await new Promise((resolve) => {
            chrome.runtime.sendMessage(
              { action: "FETCH_SUGGESTIONS", url: url },
              (res) => {
                if (chrome.runtime.lastError || !res || !res.ok) {
                  resolve(null);
                } else {
                  resolve(res.data);
                }
              }
            );
          });
          if (resp) {
            data = resp;
          }
        } catch {
          // If background worker inactive, fall through to direct fetch
        }
      }

      // Guard against out-of-order stale responses
      if (requestId !== currentRequestId) {
        return;
      }

      // 2. Fallback to direct fetch if background worker is unavailable (e.g. standalone tests)
      if (!data) {
        const response = await fetch(url, {
          method: "GET",
          signal: currentAbortController.signal,
          headers: {
            Accept: "application/json",
            "ngrok-skip-browser-warning": "true",
          },
        });

        if (!response.ok) {
          hideDropdown();
          return;
        }

        data = await response.json();

        // Guard against out-of-order stale responses
        if (requestId !== currentRequestId) {
          return;
        }
      }

      const suggestions = Logic
        ? Logic.parseSuggestionsResponse(data, config.topK)
        : (data && Array.isArray(data.suggestions) ? data.suggestions : []);

      if (suggestions && suggestions.length > 0) {
        renderDropdown(targetInput, suggestions, query);
      } else {
        hideDropdown();
      }
    } catch (err) {
      if (err.name === "AbortError") {
        // Clean expected cancellation, ignore
        return;
      }
      // Fail silently on server offline / error without disrupting user
      hideDropdown();
    }
  }


  function handleInputEvent(event) {
    const input = event.target;
    activeInput = input;

    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }

    const query = (input.value || "").trim();
    if (!query || !config.enabled) {
      hideDropdown();
      return;
    }

    debounceTimer = setTimeout(() => {
      fetchSuggestions(query, input);
    }, config.debounceMs);
  }

  // ---------------------------------------------------------------------------
  // 6. Keyboard Navigation Handler
  // ---------------------------------------------------------------------------

  function handleKeydownEvent(event) {
    const input = event.target;

    // If dropdown is hidden, allow normal key behavior
    if (!dropdownEl || dropdownEl.style.display === "none" || currentItems.length === 0) {
      return;
    }

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setSelectedIndex((selectedIndex + 1) % currentItems.length);
        break;

      case "ArrowUp":
        event.preventDefault();
        setSelectedIndex((selectedIndex - 1 + currentItems.length) % currentItems.length);
        break;

      case "Enter":
        if (selectedIndex >= 0 && selectedIndex < currentItems.length) {
          event.preventDefault();
          const selectedText = currentItems[selectedIndex].text || currentItems[selectedIndex].suggestion || "";
          applySuggestion(selectedText, input);
        }
        // If nothing selected, allow default form submit!
        break;

      case "Escape":
        event.preventDefault();
        hideDropdown();
        break;

      case "Tab":
        // Close dropdown on tab away
        hideDropdown();
        break;
    }
  }

  // ---------------------------------------------------------------------------
  // 7. Event Listener Attachment & DOM Observers
  // ---------------------------------------------------------------------------

  function attachListeners(input) {
    if (!input || input.dataset.smartsearchAttached === "true") return;

    input.dataset.smartsearchAttached = "true";

    input.addEventListener("input", handleInputEvent);
    input.addEventListener("keydown", handleKeydownEvent);

    input.addEventListener("focus", (e) => {
      activeInput = e.target;
      const val = (e.target.value || "").trim();
      if (val && config.enabled) {
        fetchSuggestions(val, e.target);
      }
    });

    input.addEventListener("blur", () => {
      // Small timeout allows mousedown on dropdown item to fire before blur hides it
      setTimeout(() => {
        if (document.activeElement !== input) {
          hideDropdown();
        }
      }, 200);
    });
  }

  function scanInputs(root = document) {
    const candidates = root.querySelectorAll("input, textarea");
    candidates.forEach((el) => {
      if (isSearchInput(el)) {
        attachListeners(el);
      }
    });
  }

  // Monitor DOM for dynamic inputs (React / Vue / SPAs)
  let mutationDebounce = null;
  const observer = new MutationObserver(() => {
    if (mutationDebounce) clearTimeout(mutationDebounce);
    mutationDebounce = setTimeout(() => {
      scanInputs(document.body);
    }, 60);
  });

  // Reposition dropdown on window resize & scroll
  window.addEventListener("resize", () => {
    if (activeInput && dropdownEl && dropdownEl.style.display !== "none") {
      positionDropdown(activeInput);
    }
  });

  window.addEventListener("scroll", () => {
    if (activeInput && dropdownEl && dropdownEl.style.display !== "none") {
      positionDropdown(activeInput);
    }
  }, { passive: true });

  // Hide on click outside
  document.addEventListener("pointerdown", (e) => {
    if (dropdownEl && dropdownEl.style.display !== "none") {
      if (!dropdownEl.contains(e.target) && e.target !== activeInput) {
        hideDropdown();
      }
    }
  });

  // ---------------------------------------------------------------------------
  // 8. Initialization
  // ---------------------------------------------------------------------------

  loadConfiguration();
  scanInputs(document);

  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true });
  } else {
    document.addEventListener("DOMContentLoaded", () => {
      scanInputs(document);
      observer.observe(document.body, { childList: true, subtree: true });
    });
  }
})();
