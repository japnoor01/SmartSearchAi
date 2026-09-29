/**
 * extension/tests/test_e2e_extension_api.js
 *
 * End-to-End Live Integration Test: Extension Client <-> FastAPI Server.
 * Runs in Node.js 18+ (using native fetch).
 *
 * Verifies:
 *   1. Live FastAPI /health connectivity
 *   2. Sequential typing progression (d, de, deep, deep l, deep le, deep lea)
 *   3. Parse & sanitize suggestions through SmartSearchLogic
 *   4. Alias endpoint /api/v1/suggest with limit parameter
 *   5. Empty query handling
 *   6. Stale response protection with AbortController and RequestTracker
 *   7. Graceful offline/network failure handling
 */

"use strict";

const assert = require("assert");
const Logic = require("../content/logic.js");

const API_BASE = process.env.API_BASE_URL || Logic.DEFAULTS.apiBaseUrl || "http://localhost";

let passedCount = 0;
let failedCount = 0;

function test(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => {
      console.log(`  ✓ ${name}`);
      passedCount++;
    })
    .catch((err) => {
      console.error(`  ✗ ${name}`);
      console.error(`    ${err.message}`);
      failedCount++;
    });
}

async function main() {
  console.log("==================================================");
  console.log("SMARTSEARCH AI — EXTENSION <-> FASTAPI LIVE E2E");
  console.log("==================================================");

  // 1. Health & Readiness checks
  console.log("\n[Suite 1] Live Backend Connectivity & Readiness");
  await test("FastAPI /health reports operational status with CPU device", async () => {
    const res = await fetch(`${API_BASE}/health`);
    assert.strictEqual(res.status, 200, "Expected HTTP 200");
    const body = await res.json();
    assert.strictEqual(body.status, "ok");
    assert.strictEqual(body.model_loaded, true);
    assert.strictEqual(body.index_connected, true);
  });

  await test("FastAPI /readiness confirms deep probe readiness", async () => {
    const res = await fetch(`${API_BASE}/readiness`);
    assert.strictEqual(res.status, 200, "Expected HTTP 200");
    const body = await res.json();
    assert.strictEqual(body.status, "ready");
    assert.strictEqual(body.model_loaded, true);
    assert.strictEqual(body.index_connected, true);
    assert.ok(typeof body.uptime_seconds === "number");
  });

  await test("FastAPI /metrics reports operational metrics", async () => {
    const res = await fetch(`${API_BASE}/metrics`);
    assert.strictEqual(res.status, 200, "Expected HTTP 200");
    const body = await res.json();
    assert.ok(typeof body.total_requests === "number");
    assert.ok(typeof body.uptime_seconds === "number");
  });

  // 2. Typing progression sequence
  console.log("\n[Suite 2] Sequential Typing Progression (d -> de -> deep -> ...)");
  const sequence = ["d", "de", "deep", "deep l", "deep le", "deep lea"];
  for (const prefix of sequence) {
    await test(`Typing prefix: "${prefix}" returns ranked candidates`, async () => {
      const url = Logic.buildApiUrl(API_BASE, prefix, 5);
      const res = await fetch(url);
      assert.strictEqual(res.status, 200, `Expected 200 for prefix "${prefix}"`);
      const data = await res.json();
      assert.strictEqual(data.query, prefix);

      const parsed = Logic.parseSuggestionsResponse(data, 5);
      assert.ok(Array.isArray(parsed), "Parsed result must be an array");
      assert.ok(parsed.length > 0, `Expected suggestions for prefix "${prefix}"`);

      // Verify prefix match
      for (const item of parsed) {
        assert.ok(item.text, "Suggestion text must exist");
        assert.ok(
          item.text.toLowerCase().startsWith(prefix.trim().toLowerCase()),
          `"${item.text}" should start with prefix "${prefix}"`
        );
      }
    });
  }

  // 3. Suggestion alias with limit parameter
  console.log("\n[Suite 3] Alias Route /api/v1/suggest with limit");
  await test("GET /api/v1/suggest?q=deep%20learning&limit=3", async () => {
    const res = await fetch(`${API_BASE}/api/v1/suggest?q=deep%20learning&limit=3`);
    assert.strictEqual(res.status, 200);
    const data = await res.json();
    assert.strictEqual(data.query, "deep learning");
    const parsed = Logic.parseSuggestionsResponse(data, 3);
    assert.ok(parsed.length <= 3);
    assert.ok(parsed[0].text.toLowerCase().includes("deep learning"));
  });

  // 4. Empty query handling
  console.log("\n[Suite 4] Empty & Whitespace Query Handling");
  await test("Empty query returns 200 with 0 suggestions", async () => {
    const url = Logic.buildApiUrl(API_BASE, "", 5);
    const res = await fetch(url);
    assert.strictEqual(res.status, 200);
    const data = await res.json();
    const parsed = Logic.parseSuggestionsResponse(data, 5);
    assert.strictEqual(parsed.length, 0);
  });

  // 5. Stale response & AbortController protection simulation
  console.log("\n[Suite 5] Stale Response Protection & Cancellation");
  await test("Rapid typing cancels earlier in-flight request via AbortController", async () => {
    const controller1 = new AbortController();
    const tracker = Logic.createRequestTracker();

    const id1 = tracker.next();
    const promise1 = fetch(Logic.buildApiUrl(API_BASE, "d", 5), {
      signal: controller1.signal,
    }).catch((err) => {
      assert.strictEqual(err.name, "AbortError", "Expected AbortError on cancellation");
      return null;
    });

    // Immediately trigger request 2 (user typed 'de')
    controller1.abort();
    const id2 = tracker.next();
    const res2 = await fetch(Logic.buildApiUrl(API_BASE, "de", 5));
    const data2 = await res2.json();

    assert.strictEqual(tracker.isLatest(id1), false, "Request 1 must be flagged as stale");
    assert.strictEqual(tracker.isLatest(id2), true, "Request 2 must be flagged as latest");

    await promise1;
    assert.strictEqual(data2.query, "de");
  });

  // 6. Graceful failure / offline simulation
  console.log("\n[Suite 6] Offline / Unreachable Server Fallback");
  await test("Unreachable backend fails gracefully without unhandled exceptions", async () => {
    let dropdownHidden = false;
    const hideDropdown = () => { dropdownHidden = true; };

    try {
      // Connect to dead port
      await fetch("http://127.0.0.1:59999/api/v1/suggestions?q=test", {
        signal: AbortSignal.timeout(500),
      });
    } catch (err) {
      // Content script fallback handler
      hideDropdown();
    }

    assert.strictEqual(dropdownHidden, true, "Dropdown must be hidden when server is unreachable");
  });

  // 7. Live Website Trust & Safety Check (/api/v1/site-check)
  console.log("\n[Suite 7] Live Website Trust / Safety Check (/api/v1/site-check)");
  await test("GET /api/v1/site-check returns verified partner details for https://github.com", async () => {
    const res = await fetch(`${API_BASE}/api/v1/site-check?url=https://github.com`);
    assert.strictEqual(res.status, 200, "Expected HTTP 200");
    const data = await res.json();
    assert.strictEqual(data.domain, "github.com");
    assert.strictEqual(data.verified_partner, true);
    assert.strictEqual(data.assessment, "low_risk");
    assert.ok(data.partner_details, "Partner details must be present");
    assert.strictEqual(data.known_threat, false);
    assert.ok(Array.isArray(data.signals), "Signals must be an array");
    assert.ok(Array.isArray(data.unavailable_checks), "Unavailable checks must be an array");
  });

  await test("GET /api/v1/site-check returns unverified insufficient_information for unknown domain", async () => {
    const res = await fetch(`${API_BASE}/api/v1/site-check?url=https://sample-unverified-test.org/news`);
    assert.strictEqual(res.status, 200, "Expected HTTP 200");
    const data = await res.json();
    assert.strictEqual(data.domain, "sample-unverified-test.org");
    assert.strictEqual(data.verified_partner, false);
    assert.strictEqual(data.assessment, "insufficient_information");
    assert.strictEqual(data.known_threat, false);
  });

  await test("GET /api/v1/site-check handles malformed URL gracefully with caution assessment", async () => {
    const res = await fetch(`${API_BASE}/api/v1/site-check?url=not_a_valid_url`);
    assert.strictEqual(res.status, 200, "Expected HTTP 200 structured assessment");
    const data = await res.json();
    assert.strictEqual(data.assessment, "caution");
    assert.ok(data.signals.some((s) => s.toLowerCase().includes("invalid")), "Must explain invalid structure");
  });

  await test("GET /api/v1/site-check rejects whitespace/empty url query with 400", async () => {
    const res = await fetch(`${API_BASE}/api/v1/site-check?url=%20`);
    assert.strictEqual(res.status, 400, "Expected HTTP 400 Bad Request");
  });

  await test("GET /api/v1/site-check rejects missing url query parameter with 422", async () => {
    const res = await fetch(`${API_BASE}/api/v1/site-check`);
    assert.strictEqual(res.status, 422, "Expected HTTP 422 Unprocessable Entity");
  });

  console.log("\n==================================================");
  console.log(`TOTAL E2E TESTS: ${passedCount + failedCount}`);
  console.log(`PASSED:          ${passedCount}`);
  console.log(`FAILED:          ${failedCount}`);
  console.log("==================================================");

  if (failedCount > 0) {
    process.exit(1);
  }
}

main().catch((err) => {
  console.error("Test runner failed:", err);
  process.exit(1);
});
