/**
 * extension/tests/test_extension_logic.js
 *
 * Unit tests for SmartSearch AI Browser Extension logic.
 * Tests pure functions:
 *   - API URL construction & query encoding
 *   - Top-K parameter bounds & sanitization
 *   - Sensitive input rejection (privacy & security)
 *   - Search input detection heuristics
 *   - API response parsing & duplicate deduplication
 *   - Stale request sequencing & race-condition cancellation
 *   - Debounce timing behavior
 *   - User settings validation (URLs, ranges, toggles)
 *
 * Execution:
 *   node extension/tests/test_extension_logic.js
 */

const assert = require("node:assert");
const logic = require("../content/logic.js");

let totalTests = 0;
let passedTests = 0;
let failedTests = 0;

function runTest(name, fn) {
  totalTests++;
  try {
    fn();
    passedTests++;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failedTests++;
    console.error(`  ✗ ${name}`);
    console.error(`    Error: ${err.message}`);
  }
}

async function runAsyncTest(name, fn) {
  totalTests++;
  try {
    await fn();
    passedTests++;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failedTests++;
    console.error(`  ✗ ${name}`);
    console.error(`    Error: ${err.message}`);
  }
}

console.log("==================================================");
console.log("SMARTSEARCH AI EXTENSION — PURE LOGIC UNIT TESTS");
console.log("==================================================");

// ---------------------------------------------------------------------------
// 1. API URL Construction & Query Encoding
// ---------------------------------------------------------------------------
console.log("\n[Suite 1] API URL Construction & Query Encoding");

runTest("buildApiUrl with default baseUrl (http://localhost)", () => {
  assert.strictEqual(logic.DEFAULTS.apiBaseUrl, "http://localhost");
  const url = logic.buildApiUrl(null, "machine learning", 5);
  assert.strictEqual(
    url,
    "http://localhost/api/v1/suggestions?q=machine%20learning&top_k=5"
  );
});

runTest("buildApiUrl with explicit http://localhost base", () => {
  const url = logic.buildApiUrl("http://localhost", "machine learning", 5);
  assert.strictEqual(
    url,
    "http://localhost/api/v1/suggestions?q=machine%20learning&top_k=5"
  );
});

runTest("buildApiUrl handles trailing slashes on http://localhost cleanly", () => {
  const url = logic.buildApiUrl("http://localhost///", "neural networks", 5);
  assert.strictEqual(
    url,
    "http://localhost/api/v1/suggestions?q=neural%20networks&top_k=5"
  );
});

runTest("buildApiUrl preserves custom/dev base URLs (e.g., http://127.0.0.1:8000)", () => {
  const url = logic.buildApiUrl("http://127.0.0.1:8000", "machine learning", 5);
  assert.strictEqual(
    url,
    "http://127.0.0.1:8000/api/v1/suggestions?q=machine%20learning&top_k=5"
  );
});

runTest("buildApiUrl encodes special URL characters properly", () => {
  const url = logic.buildApiUrl("http://localhost", "c++ & python?", 3);
  assert.strictEqual(
    url,
    "http://localhost/api/v1/suggestions?q=c%2B%2B%20%26%20python%3F&top_k=3"
  );
});

runTest("buildApiUrl handles non-ASCII / Unicode queries", () => {
  const url = logic.buildApiUrl("http://localhost", "über cool", 5);
  assert.strictEqual(
    url,
    "http://localhost/api/v1/suggestions?q=%C3%BCber%20cool&top_k=5"
  );
});

// ---------------------------------------------------------------------------
// 2. Top-K Boundary Enforcement
// ---------------------------------------------------------------------------
console.log("\n[Suite 2] Top-K Boundary Enforcement");

runTest("Clamps negative top_k to 1", () => {
  const url = logic.buildApiUrl("http://127.0.0.1:8000", "ai", -5);
  assert.strictEqual(url, "http://127.0.0.1:8000/api/v1/suggestions?q=ai&top_k=1");
});

runTest("Clamps excessive top_k to 20", () => {
  const url = logic.buildApiUrl("http://127.0.0.1:8000", "ai", 100);
  assert.strictEqual(url, "http://127.0.0.1:8000/api/v1/suggestions?q=ai&top_k=20");
});

runTest("Defaults non-numeric top_k to 5", () => {
  const url = logic.buildApiUrl("http://127.0.0.1:8000", "ai", "invalid");
  assert.strictEqual(url, "http://127.0.0.1:8000/api/v1/suggestions?q=ai&top_k=5");
});

// ---------------------------------------------------------------------------
// 3. Sensitive Field Exclusion (Privacy & Security)
// ---------------------------------------------------------------------------
console.log("\n[Suite 3] Sensitive Input Rejection (Privacy)");

runTest("Rejects password type input", () => {
  assert.strictEqual(logic.isSensitiveInput({ type: "password" }), true);
});

runTest("Rejects email type input", () => {
  assert.strictEqual(logic.isSensitiveInput({ type: "email" }), true);
});

runTest("Rejects tel and number type inputs", () => {
  assert.strictEqual(logic.isSensitiveInput({ type: "tel" }), true);
  assert.strictEqual(logic.isSensitiveInput({ type: "number" }), true);
});

runTest("Rejects hidden, file, and submit inputs", () => {
  assert.strictEqual(logic.isSensitiveInput({ type: "hidden" }), true);
  assert.strictEqual(logic.isSensitiveInput({ type: "file" }), true);
  assert.strictEqual(logic.isSensitiveInput({ type: "submit" }), true);
});

runTest("Rejects sensitive autocomplete markers (e.g., cc-number, current-password)", () => {
  assert.strictEqual(logic.isSensitiveInput({ type: "text", autocomplete: "cc-number" }), true);
  assert.strictEqual(logic.isSensitiveInput({ type: "text", autocomplete: "current-password" }), true);
  assert.strictEqual(logic.isSensitiveInput({ type: "text", autocomplete: "one-time-code" }), true);
});

runTest("Rejects sensitive token in name, id, or placeholder (e.g., card_pin, cvv)", () => {
  assert.strictEqual(logic.isSensitiveInput({ type: "text", name: "user_password" }), true);
  assert.strictEqual(logic.isSensitiveInput({ type: "text", id: "cvv_code" }), true);
  assert.strictEqual(logic.isSensitiveInput({ type: "text", placeholder: "Enter credit card" }), true);
  assert.strictEqual(logic.isSensitiveInput({ type: "text", ariaLabel: "Enter OTP code" }), true);
});

runTest("Permits standard non-sensitive inputs", () => {
  assert.strictEqual(
    logic.isSensitiveInput({ type: "text", name: "search_query", placeholder: "Search docs..." }),
    false
  );
});

// ---------------------------------------------------------------------------
// 4. Search Input Detection Heuristics
// ---------------------------------------------------------------------------
console.log("\n[Suite 4] Search Input Detection Heuristics");

runTest("Identifies explicit type='search' input", () => {
  assert.strictEqual(logic.isSearchField({ type: "search" }), true);
});

runTest("Identifies role='searchbox' input", () => {
  assert.strictEqual(logic.isSearchField({ type: "text", role: "searchbox" }), true);
});

runTest("Identifies standard query parameter names: 'q', 'query', 'search'", () => {
  assert.strictEqual(logic.isSearchField({ type: "text", name: "q" }), true);
  assert.strictEqual(logic.isSearchField({ type: "text", name: "search_term" }), true);
  assert.strictEqual(logic.isSearchField({ type: "text", id: "main-query-input" }), true);
  assert.strictEqual(logic.isSearchField({ type: "text", placeholder: "Search articles..." }), true);
});

runTest("Identifies input inside a search form", () => {
  assert.strictEqual(
    logic.isSearchField({ type: "text", formRole: "search" }),
    true
  );
  assert.strictEqual(
    logic.isSearchField({ type: "text", formAction: "/site/search.php" }),
    true
  );
});

runTest("Never marks sensitive input as search field even if named 'search_password'", () => {
  assert.strictEqual(
    logic.isSearchField({ type: "password", name: "search_password" }),
    false
  );
});

runTest("Ignores generic unrelated text inputs", () => {
  assert.strictEqual(
    logic.isSearchField({ type: "text", name: "firstName", placeholder: "First name" }),
    false
  );
});

// ---------------------------------------------------------------------------
// 5. API Response Parsing & Duplicate Removal
// ---------------------------------------------------------------------------
console.log("\n[Suite 5] API Response Parsing & Deduplication");

runTest("Parses standard FastAPI response structure", () => {
  const apiResponse = {
    query: "deep",
    suggestions: [
      { text: "deep learning", score: 0.95 },
      { text: "deep space", score: 0.82 },
      { text: "deep dish pizza", score: 0.75 }
    ],
    count: 3
  };

  const parsed = logic.parseSuggestionsResponse(apiResponse, 5);
  assert.strictEqual(parsed.length, 3);
  assert.strictEqual(parsed[0].text, "deep learning");
  assert.strictEqual(parsed[1].text, "deep space");
  assert.strictEqual(parsed[2].text, "deep dish pizza");
  // Verification: Score is stripped from client view model
  assert.strictEqual(parsed[0].score, undefined);
});

runTest("Handles array of raw strings gracefully", () => {
  const rawResponse = {
    query: "test",
    suggestions: ["test driven development", "test automation"]
  };

  const parsed = logic.parseSuggestionsResponse(rawResponse, 5);
  assert.strictEqual(parsed.length, 2);
  assert.strictEqual(parsed[0].text, "test driven development");
  assert.strictEqual(parsed[1].text, "test automation");
});

runTest("Removes case-insensitive duplicate suggestions", () => {
  const duplicateResponse = {
    query: "python",
    suggestions: [
      { text: "python tutorial" },
      { text: "Python Tutorial" },
      { text: "python 3" },
      { text: "PYTHON 3" }
    ]
  };

  const parsed = logic.parseSuggestionsResponse(duplicateResponse, 5);
  assert.strictEqual(parsed.length, 2);
  assert.strictEqual(parsed[0].text, "python tutorial");
  assert.strictEqual(parsed[1].text, "python 3");
});

runTest("Handles null, empty, or malformed responses safely", () => {
  assert.deepStrictEqual(logic.parseSuggestionsResponse(null), []);
  assert.deepStrictEqual(logic.parseSuggestionsResponse({}), []);
  assert.deepStrictEqual(logic.parseSuggestionsResponse({ suggestions: "not an array" }), []);
  assert.deepStrictEqual(logic.parseSuggestionsResponse({ suggestions: [] }), []);
});

// ---------------------------------------------------------------------------
// 6. Stale Request Handling (Race Condition Prevention)
// ---------------------------------------------------------------------------
console.log("\n[Suite 6] Stale Request Handling & Sequencing");

runTest("Sequencer generates incremental IDs and validates latest", () => {
  const tracker = logic.createRequestTracker();
  assert.strictEqual(tracker.current(), 0);

  const req1 = tracker.next(); // 1
  assert.strictEqual(req1, 1);
  assert.strictEqual(tracker.isLatest(req1), true);

  const req2 = tracker.next(); // 2
  assert.strictEqual(req2, 2);
  assert.strictEqual(tracker.isLatest(req1), false); // Req 1 is now stale!
  assert.strictEqual(tracker.isLatest(req2), true);  // Req 2 is latest
});

runTest("Out-of-order simulated response resolution is rejected", () => {
  const tracker = logic.createRequestTracker();

  const reqA = tracker.next(); // User typed "dee"
  const reqB = tracker.next(); // User typed "deep"

  // Suppose response for reqB arrives first:
  assert.strictEqual(tracker.isLatest(reqB), true);

  // Later, slow response for reqA arrives:
  assert.strictEqual(tracker.isLatest(reqA), false); // Must be discarded!
});

// ---------------------------------------------------------------------------
// 7. Debounce Timing Simulation
// ---------------------------------------------------------------------------
console.log("\n[Suite 7] Debounce Timing Simulation");

async function testDebounceExecution() {
  let callCount = 0;
  let lastVal = "";

  function createDebouncer(fn, delayMs) {
    let timer = null;
    return (val) => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        fn(val);
      }, delayMs);
    };
  }

  const debounced = createDebouncer((val) => {
    callCount++;
    lastVal = val;
  }, 50);

  // Rapid bursts of keystrokes:
  debounced("d");
  debounced("de");
  debounced("dee");
  debounced("deep");

  // Immediate check: nothing should have executed yet
  assert.strictEqual(callCount, 0);

  // Wait 80ms for debounce timer to fire once
  await new Promise((resolve) => setTimeout(resolve, 80));

  assert.strictEqual(callCount, 1);
  assert.strictEqual(lastVal, "deep");
}

// ---------------------------------------------------------------------------
// 8. Settings Validation
// ---------------------------------------------------------------------------
console.log("\n[Suite 8] Settings Validation & Sanitization");

runTest("Validates and defaults apiBaseUrl to http://localhost when unspecified", () => {
  assert.strictEqual(logic.DEFAULTS.apiBaseUrl, "http://localhost");
  const res = logic.validateSettings({
    topK: 5,
    debounceMs: 200,
    enabled: true,
  });
  assert.strictEqual(res.valid, true);
  assert.strictEqual(res.errors.length, 0);
  assert.strictEqual(res.sanitized.apiBaseUrl, "http://localhost");
  assert.strictEqual(res.sanitized.topK, 5);
  assert.strictEqual(res.sanitized.debounceMs, 200);
});

runTest("Validates valid default configuration with http://localhost", () => {
  const res = logic.validateSettings({
    apiBaseUrl: "http://localhost",
    topK: 5,
    debounceMs: 200,
    enabled: true,
  });

  assert.strictEqual(res.valid, true);
  assert.strictEqual(res.errors.length, 0);
  assert.strictEqual(res.sanitized.apiBaseUrl, "http://localhost");
  assert.strictEqual(res.sanitized.topK, 5);
  assert.strictEqual(res.sanitized.debounceMs, 200);
});

runTest("Validates and preserves configurable custom endpoints (e.g., http://127.0.0.1:8000)", () => {
  const res = logic.validateSettings({
    apiBaseUrl: "http://127.0.0.1:8000",
    topK: 10,
    debounceMs: 300,
    enabled: true,
  });

  assert.strictEqual(res.valid, true);
  assert.strictEqual(res.errors.length, 0);
  assert.strictEqual(res.sanitized.apiBaseUrl, "http://127.0.0.1:8000");
  assert.strictEqual(res.sanitized.topK, 10);
  assert.strictEqual(res.sanitized.debounceMs, 300);
});

runTest("Validates and preserves remote HTTPS configurable endpoint", () => {
  const res = logic.validateSettings({
    apiBaseUrl: "https://search.example.com/api",
    topK: 5,
    debounceMs: 200,
  });

  assert.strictEqual(res.valid, true);
  assert.strictEqual(res.sanitized.apiBaseUrl, "https://search.example.com/api");
});

runTest("Rejects invalid URL schema (ftp://)", () => {
  const res = logic.validateSettings({
    apiBaseUrl: "ftp://127.0.0.1:8000",
    topK: 5,
    debounceMs: 200,
  });

  assert.strictEqual(res.valid, false);
  assert.strictEqual(res.errors.some((e) => e.includes("http://")), true);
});

runTest("Rejects non-URL string", () => {
  const res = logic.validateSettings({
    apiBaseUrl: "not-a-valid-url",
  });

  assert.strictEqual(res.valid, false);
});

runTest("Rejects out-of-range topK (e.g. 0 or 50)", () => {
  const resZero = logic.validateSettings({ topK: 0 });
  assert.strictEqual(resZero.valid, false);

  const resTooHigh = logic.validateSettings({ topK: 50 });
  assert.strictEqual(resTooHigh.valid, false);
});

runTest("Rejects out-of-range debounceMs (e.g. 10ms or 5000ms)", () => {
  const resTooLow = logic.validateSettings({ debounceMs: 10 });
  assert.strictEqual(resTooLow.valid, false);

  const resTooHigh = logic.validateSettings({ debounceMs: 5000 });
  assert.strictEqual(resTooHigh.valid, false);
});

// ---------------------------------------------------------------------------
// 9. Prefix Match Highlighting & HTML Escaping
// ---------------------------------------------------------------------------
console.log("\n[Suite 9] Prefix Match & HTML Escaping");

runTest("highlightPrefix splits matched prefix and remaining suffix correctly", () => {
  const res = logic.highlightPrefix("machine learning algorithms", "machine learn");
  assert.strictEqual(res.matched, "machine learn");
  assert.strictEqual(res.suffix, "ing algorithms");
});

runTest("highlightPrefix handles non-matching prefix safely", () => {
  const res = logic.highlightPrefix("deep learning", "nlp");
  assert.strictEqual(res.matched, "");
  assert.strictEqual(res.suffix, "deep learning");
});

runTest("escapeHtml sanitizes HTML tags and dangerous characters", () => {
  const unsafe = `<script>alert("xss")</script> & 'safe'`;
  const escaped = logic.escapeHtml(unsafe);
  assert.strictEqual(
    escaped,
    `&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt; &amp; &#039;safe&#039;`
  );
});

// ---------------------------------------------------------------------------
// 10. Website Trust & Safety Formatting
// ---------------------------------------------------------------------------
console.log("\n[Suite 10] Website Trust & Safety Formatting");

runTest("formatAssessmentBadge correctly maps all 5 assessment states", () => {
  const low = logic.formatAssessmentBadge("low_risk");
  assert.strictEqual(low.label, "Low risk");
  assert.strictEqual(low.className, "badge-low-risk");

  const caution = logic.formatAssessmentBadge("caution");
  assert.strictEqual(caution.label, "Use caution");
  assert.strictEqual(caution.className, "badge-caution");

  const high = logic.formatAssessmentBadge("high_risk");
  assert.strictEqual(high.label, "High risk");
  assert.strictEqual(high.className, "badge-high-risk");

  const threat = logic.formatAssessmentBadge("known_threat");
  assert.strictEqual(threat.label, "Known threat");
  assert.strictEqual(threat.className, "badge-threat");

  const insuff = logic.formatAssessmentBadge("insufficient_information");
  assert.strictEqual(insuff.label, "Insufficient information");
  assert.strictEqual(insuff.className, "badge-insufficient");

  const fallback = logic.formatAssessmentBadge("unknown_state");
  assert.strictEqual(fallback.label, "Insufficient information");
  assert.strictEqual(fallback.className, "badge-insufficient");
});

runTest("formatPartnerBadge clearly distinguishes verified from unverified without accusing", () => {
  const verified = logic.formatPartnerBadge(true, { organization: "Mozilla Foundation" });
  assert.strictEqual(verified.isPartner, true);
  assert.strictEqual(verified.text, "Verified Partner (Mozilla Foundation)");
  assert.strictEqual(verified.icon, "🏪");

  const verifiedNoOrg = logic.formatPartnerBadge(true, null);
  assert.strictEqual(verifiedNoOrg.isPartner, true);
  assert.strictEqual(verifiedNoOrg.text, "Verified Partner");

  const unverified = logic.formatPartnerBadge(false, null);
  assert.strictEqual(unverified.isPartner, false);
  assert.strictEqual(unverified.text, "Not verified (does not mean fraudulent)");
  assert.strictEqual(unverified.icon, "ℹ️");
});

runTest("formatThreatBadge cleanly distinguishes known threat from no threat detected", () => {
  const noThreat = logic.formatThreatBadge(false, null);
  assert.strictEqual(noThreat.hasThreat, false);
  assert.strictEqual(noThreat.text, "No known threat detected");
  assert.strictEqual(noThreat.icon, "✓");

  const threatWithDetail = logic.formatThreatBadge(true, "PHISHING");
  assert.strictEqual(threatWithDetail.hasThreat, true);
  assert.strictEqual(threatWithDetail.text, "Threat detected: PHISHING");
  assert.strictEqual(threatWithDetail.icon, "🛑");

  const threatNoDetail = logic.formatThreatBadge(true, null);
  assert.strictEqual(threatNoDetail.hasThreat, true);
  assert.strictEqual(threatNoDetail.text, "Known threat detected");
});

// Run async test and report final results
(async () => {
  await runAsyncTest("Debounce collapses rapid keystrokes into single execution", testDebounceExecution);

  console.log("\n==================================================");
  console.log(`TOTAL TESTS: ${totalTests}`);
  console.log(`PASSED:      ${passedTests}`);
  console.log(`FAILED:      ${failedTests}`);
  console.log("==================================================");

  if (failedTests > 0) {
    process.exit(1);
  }
})();
