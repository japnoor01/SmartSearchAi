/**
 * extension/content/logic.js
 *
 * Pure JavaScript business logic for SmartSearch AI.
 * Implements URL construction, sensitive input detection, search input heuristics,
 * response parsing, request sequencing (race condition prevention), and settings validation.
 *
 * Compatible with both Browser (global namespace) and Node.js (CommonJS exports).
 */

(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.SmartSearchLogic = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const DEFAULTS = {
    enabled: true,
    apiBaseUrl: "http://localhost",
    topK: 5,
    debounceMs: 200,
  };

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

  /**
   * Constructs the full API URL for fetching suggestions.
   *
   * @param {string} baseUrl - Base API URL (e.g., http://localhost)
   * @param {string} query - Partial search query
   * @param {number} topK - Number of suggestions requested
   * @returns {string} Fully formed, URL-encoded endpoint URL
   */
  function buildApiUrl(baseUrl, query, topK = 5) {
    const cleanBase = (baseUrl || DEFAULTS.apiBaseUrl).trim().replace(/\/+$/, "");
    const safeTopK = Math.max(1, Math.min(20, parseInt(topK, 10) || DEFAULTS.topK));
    const encodedQuery = encodeURIComponent(query || "");
    return `${cleanBase}/api/v1/suggestions?q=${encodedQuery}&top_k=${safeTopK}`;
  }

  /**
   * Determines whether an input element or attribute set represents a sensitive field.
   *
   * @param {Object} attrs - Element attributes { type, autocomplete, name, id, placeholder, ariaLabel, className }
   * @returns {boolean} True if the input must be ignored for privacy/security
   */
  function isSensitiveInput(attrs) {
    if (!attrs) return false;

    const type = (attrs.type || "text").toLowerCase();
    if (SENSITIVE_TYPES.has(type)) return true;

    const autocomplete = (attrs.autocomplete || "").toLowerCase();
    if (SENSITIVE_AUTOCOMPLETE.has(autocomplete)) return true;

    const attrTokens = [
      attrs.name || "",
      attrs.id || "",
      attrs.placeholder || "",
      attrs.ariaLabel || "",
      attrs.className || "",
    ].join(" ");

    if (SENSITIVE_NAME_REGEX.test(attrTokens)) return true;

    return false;
  }

  /**
   * Determines whether an input element is a search/query field.
   *
   * @param {Object} attrs - Element attributes { type, autocomplete, name, id, placeholder, ariaLabel, className, role, formRole, formAction }
   * @returns {boolean} True if the input is an affirmative search field
   */
  function isSearchField(attrs) {
    if (!attrs) return false;
    if (isSensitiveInput(attrs)) return false;

    const type = (attrs.type || "text").toLowerCase();
    if (type === "search") return true;

    const role = (attrs.role || "").toLowerCase();
    if (role === "searchbox") return true;

    if (attrs.formRole && attrs.formRole.toLowerCase() === "search") return true;
    if (attrs.formAction && attrs.formAction.toLowerCase().includes("search")) return true;

    const attrTokens = [
      attrs.name || "",
      attrs.id || "",
      attrs.placeholder || "",
      attrs.ariaLabel || "",
      attrs.className || "",
    ].join(" ");

    if (SEARCH_NAME_REGEX.test(attrTokens)) return true;

    return false;
  }

  /**
   * Parses and validates raw API response data from FastAPI.
   * Removes duplicates and ensures safe suggestion objects for UI rendering.
   *
   * @param {Object} data - Parsed JSON response from GET /api/v1/suggestions
   * @param {number} maxItems - Maximum items to return
   * @returns {Array<{text: string}>} Cleaned list of suggestion objects
   */
  function parseSuggestionsResponse(data, maxItems = 5) {
    if (!data || typeof data !== "object") return [];
    if (!Array.isArray(data.suggestions)) return [];

    const seen = new Set();
    const result = [];

    for (const item of data.suggestions) {
      let text = "";
      if (typeof item === "string") {
        text = item.trim();
      } else if (item && typeof item === "object") {
        text = (item.text || item.suggestion || "").trim();
      }

      if (text && !seen.has(text.toLowerCase())) {
        seen.add(text.toLowerCase());
        result.push({ text });
        if (result.length >= maxItems) break;
      }
    }

    return result;
  }

  /**
   * Validates user configuration parameters.
   *
   * @param {Object} settings - { apiBaseUrl, topK, debounceMs, enabled }
   * @returns {{ valid: boolean, errors: string[], sanitized: Object }}
   */
  function validateSettings(settings = {}) {
    const errors = [];
    const sanitized = { ...DEFAULTS };

    // 1. API Base URL validation
    const rawUrl = (settings.apiBaseUrl || DEFAULTS.apiBaseUrl).trim();
    try {
      const parsed = new URL(rawUrl);
      if (!["http:", "https:"].includes(parsed.protocol)) {
        errors.push("API URL must start with http:// or https://");
      } else {
        sanitized.apiBaseUrl = `${parsed.protocol}//${parsed.host}${parsed.pathname}`.replace(/\/+$/, "");
      }
    } catch {
      errors.push("Invalid API URL format");
    }

    // 2. Top-K validation
    const topK = parseInt(settings.topK, 10);
    if (isNaN(topK) || topK < 1 || topK > 20) {
      errors.push("Top-K suggestions must be an integer between 1 and 20");
    } else {
      sanitized.topK = topK;
    }

    // 3. Debounce delay validation
    const debounceMs = parseInt(settings.debounceMs, 10);
    if (isNaN(debounceMs) || debounceMs < 50 || debounceMs > 1000) {
      errors.push("Debounce delay must be an integer between 50ms and 1000ms");
    } else {
      sanitized.debounceMs = debounceMs;
    }

    // 4. Enabled flag
    if (settings.enabled !== undefined) {
      sanitized.enabled = Boolean(settings.enabled);
    }

    return {
      valid: errors.length === 0,
      errors,
      sanitized: errors.length === 0 ? sanitized : null,
    };
  }

  /**
   * Request sequencer to track in-flight requests and prevent out-of-order stale overwrites.
   */
  function createRequestTracker() {
    let currentId = 0;

    return {
      next() {
        return ++currentId;
      },
      isLatest(id) {
        return id === currentId;
      },
      current() {
        return currentId;
      },
      reset() {
        currentId = 0;
      }
    };
  }

  /**
   * Escapes HTML special characters to prevent XSS.
   */
  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  /**
   * Splits a suggestion text into matched prefix and suffix for highlighting.
   */
  function highlightPrefix(text, query) {
    const sText = String(text || "");
    const sQuery = String(query || "").trim();

    if (!sQuery) return { matched: "", suffix: sText };

    if (sText.toLowerCase().startsWith(sQuery.toLowerCase())) {
      const matchPart = sText.slice(0, sQuery.length);
      const restPart = sText.slice(sQuery.length);
      return { matched: matchPart, suffix: restPart };
    }

    return { matched: "", suffix: sText };
  }

  /**
   * Formats trust assessment state into label and CSS styling.
   *
   * @param {string} assessment - State from backend: low_risk, caution, high_risk, known_threat, insufficient_information
   * @returns {{ label: string, className: string, icon: string }}
   */
  function formatAssessmentBadge(assessment) {
    switch (assessment) {
      case "low_risk":
        return { label: "Low risk", className: "badge-low-risk", icon: "✓" };
      case "caution":
        return { label: "Use caution", className: "badge-caution", icon: "⚠️" };
      case "high_risk":
        return { label: "High risk", className: "badge-high-risk", icon: "⚠️" };
      case "known_threat":
        return { label: "Known threat", className: "badge-threat", icon: "🛑" };
      case "insufficient_information":
      default:
        return { label: "Insufficient information", className: "badge-insufficient", icon: "ℹ️" };
    }
  }

  /**
   * Formats partner status, ensuring 'Not verified' clearly does not mean fraudulent.
   *
   * @param {boolean} isPartner
   * @param {Object} partnerDetails
   * @returns {{ text: string, isPartner: boolean, icon: string }}
   */
  function formatPartnerBadge(isPartner, partnerDetails) {
    if (isPartner) {
      const org = (partnerDetails && partnerDetails.organization) ? ` (${partnerDetails.organization})` : "";
      return {
        text: `Verified Partner${org}`,
        isPartner: true,
        icon: "🏪",
      };
    }
    return {
      text: "Not verified (does not mean fraudulent)",
      isPartner: false,
      icon: "ℹ️",
    };
  }

  /**
   * Formats threat status.
   *
   * @param {boolean} knownThreat
   * @param {string} threatType
   * @returns {{ text: string, hasThreat: boolean, icon: string }}
   */
  function formatThreatBadge(knownThreat, threatType) {
    if (knownThreat) {
      return {
        text: threatType ? `Threat detected: ${threatType}` : "Known threat detected",
        hasThreat: true,
        icon: "🛑",
      };
    }
    return {
      text: "No known threat detected",
      hasThreat: false,
      icon: "✓",
    };
  }

  return {
    DEFAULTS,
    SENSITIVE_TYPES,
    SENSITIVE_AUTOCOMPLETE,
    buildApiUrl,
    isSensitiveInput,
    isSearchField,
    parseSuggestionsResponse,
    validateSettings,
    createRequestTracker,
    escapeHtml,
    highlightPrefix,
    formatAssessmentBadge,
    formatPartnerBadge,
    formatThreatBadge,
  };
});

