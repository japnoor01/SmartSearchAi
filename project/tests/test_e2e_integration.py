"""
tests/test_e2e_integration.py

Comprehensive End-to-End & Production Hardening Test Suite for SmartSearch AI.
Validates:
  1. Service health (liveness probe), readiness probe, and metrics endpoints
  2. Prefix query progression sequence (d -> de -> deep -> deep l -> deep le -> deep lea)
  3. Alias endpoint /api/v1/suggest with limit parameter
  4. Query parameter validations (empty, whitespace, missing, excessive length)
  5. Top-K / limit parameter validations (<1, >50, non-numeric)
  6. Rate limiting enforcement and 429 Retry-After header behavior
  7. CORS handling for Chrome Extension origins, localhost, and null (file://) origins
  8. Production vs Development configuration separation
  9. Error handlers (400, 404, 405, 422, 429) without sensitive stack trace leaks
  10. Request timing diagnostic header (X-Process-Time-Ms)
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from starlette.testclient import TestClient

from src.api.app import create_app
from src.api.config import APIConfig


class TestEndToEndExtensionFastAPIIntegration(unittest.TestCase):
    """E2E Integration tests verifying backend API contract with browser extension."""

    @classmethod
    def setUpClass(cls) -> None:
        """Initialize the real production FastAPI app with real SQLite index and LSTM model."""
        cls.config = APIConfig.load()
        assert cls.config.checkpoint_path.exists(), f"Missing checkpoint: {cls.config.checkpoint_path}"
        assert cls.config.index_path.exists(), f"Missing index: {cls.config.index_path}"
        assert cls.config.tokenizer_dir.exists(), f"Missing tokenizer: {cls.config.tokenizer_dir}"

        cls.app = create_app(config=cls.config)
        cls.client_ctx = TestClient(cls.app)
        cls.client = cls.client_ctx.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        """Cleanly tear down test client and close app lifespan."""
        cls.client_ctx.__exit__(None, None, None)

    # -----------------------------------------------------------------------
    # 1. Health, Readiness & Metrics Checks
    # -----------------------------------------------------------------------

    def test_health_check_operational(self) -> None:
        """Verify /health reports operational state with real model and index connected."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["model_loaded"])
        self.assertTrue(data["index_connected"])
        self.assertIn("cpu", data["device"].lower())

    def test_readiness_probe_operational(self) -> None:
        """Verify /readiness deep probe confirms model and SQLite index readiness."""
        response = self.client.get("/readiness")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ready")
        self.assertTrue(data["model_loaded"])
        self.assertTrue(data["index_connected"])
        self.assertGreaterEqual(data["uptime_seconds"], 0.0)

    def test_metrics_endpoint_reports_counters(self) -> None:
        """Verify /metrics returns operational counters and latency statistics."""
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("uptime_seconds", data)
        self.assertIn("total_requests", data)
        self.assertIn("total_suggestions_served", data)
        self.assertIn("total_errors", data)
        self.assertIn("total_rate_limited", data)
        self.assertIn("avg_latency_ms", data)
        self.assertIn("last_latency_ms", data)

    def test_version_endpoint_metadata(self) -> None:
        """Verify /api/v1/version returns valid service and model metadata."""
        response = self.client.get("/api/v1/version")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("version", data)
        self.assertEqual(data["model_type"], "LSTM Ranker")

    # -----------------------------------------------------------------------
    # 2. Sequential Prefix Typing Progression (d -> de -> deep -> ...)
    # -----------------------------------------------------------------------

    def test_prefix_typing_progression(self) -> None:
        """Verify sequential user typing flow returns coherent, ranked candidates for each prefix."""
        test_sequence = ["d", "de", "deep", "deep l", "deep le", "deep lea"]

        for prefix in test_sequence:
            with self.subTest(prefix=prefix):
                response = self.client.get(f"/api/v1/suggestions?q={prefix}&top_k=5")
                self.assertEqual(response.status_code, 200)
                data = response.json()

                self.assertEqual(data["query"], prefix)
                self.assertIsInstance(data["suggestions"], list)
                self.assertGreaterEqual(data["count"], 0)
                self.assertIsInstance(data["latency_ms"], (int, float))

                if data["count"] > 0:
                    for item in data["suggestions"]:
                        self.assertIn("text", item)
                        self.assertIn("score", item)
                        self.assertIsInstance(item["text"], str)
                        self.assertIsInstance(item["score"], float)
                        clean_prefix = prefix.strip().lower()
                        self.assertTrue(
                            item["text"].lower().startswith(clean_prefix),
                            f"Suggestion '{item['text']}' does not start with prefix '{clean_prefix}'",
                        )

    # -----------------------------------------------------------------------
    # 3. Suggestion Alias Endpoint (/api/v1/suggest with limit parameter)
    # -----------------------------------------------------------------------

    def test_suggest_alias_with_limit(self) -> None:
        """Verify /api/v1/suggest alias works with 'limit' query parameter."""
        response = self.client.get("/api/v1/suggest?q=deep%20learning&limit=3")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["query"], "deep learning")
        self.assertLessEqual(len(data["suggestions"]), 3)
        if len(data["suggestions"]) > 0:
            self.assertEqual(len(data["suggestions"]), 3)
            self.assertIn("deep learning", data["suggestions"][0]["text"].lower())

    def test_suggest_alias_with_top_k(self) -> None:
        """Verify /api/v1/suggest alias also accepts 'top_k' parameter."""
        response = self.client.get("/api/v1/suggest?q=machine%20learning&top_k=2")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertLessEqual(len(data["suggestions"]), 2)

    # -----------------------------------------------------------------------
    # 4. Input Validations (Empty, Whitespace, Excessive Length, Missing)
    # -----------------------------------------------------------------------

    def test_empty_query_returns_empty_list_cleanly(self) -> None:
        """Empty query must return 200 OK with empty suggestions without hitting model."""
        response = self.client.get("/api/v1/suggestions?q=")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["suggestions"], [])
        self.assertEqual(data["count"], 0)

    def test_whitespace_query_returns_empty_list(self) -> None:
        """Whitespace-only query must return 200 OK with empty suggestions."""
        response = self.client.get("/api/v1/suggestions?q=    ")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["suggestions"], [])
        self.assertEqual(data["count"], 0)

    def test_missing_q_parameter_returns_422(self) -> None:
        """Missing required 'q' parameter must return 422 Unprocessable Entity."""
        response = self.client.get("/api/v1/suggestions")
        self.assertEqual(response.status_code, 422)
        data = response.json()
        self.assertIn("detail", data)

    def test_excessive_query_length_returns_400(self) -> None:
        """Query exceeding 200 characters must return 400 Bad Request."""
        long_query = "a" * 201
        response = self.client.get(f"/api/v1/suggestions?q={long_query}")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("exceeds the maximum allowed length", data["detail"])

    # -----------------------------------------------------------------------
    # 5. Top-K / Limit Boundary Validations
    # -----------------------------------------------------------------------

    def test_top_k_below_minimum_returns_400(self) -> None:
        """top_k < 1 must return 400 Bad Request."""
        response = self.client.get("/api/v1/suggestions?q=deep&top_k=0")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("top_k", data["detail"])

    def test_top_k_above_maximum_returns_400(self) -> None:
        """top_k > 50 must return 400 Bad Request."""
        response = self.client.get("/api/v1/suggestions?q=deep&top_k=51")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("top_k", data["detail"])

    def test_top_k_non_numeric_returns_422(self) -> None:
        """Non-numeric top_k must return 422 Unprocessable Entity."""
        response = self.client.get("/api/v1/suggestions?q=deep&top_k=five")
        self.assertEqual(response.status_code, 422)

    # -----------------------------------------------------------------------
    # 6. Rate Limiting Protection (429 & Retry-After)
    # -----------------------------------------------------------------------

    def test_rate_limiting_triggers_429_with_retry_after(self) -> None:
        """Verify that rapid requests exceeding the configured limit trigger HTTP 429."""
        # Create a test app instance with a very low limit of 3 requests per 10s
        limited_config = APIConfig(
            env="testing",
            rate_limit_enabled=True,
            rate_limit_requests=3,
            rate_limit_window_seconds=10.0,
            checkpoint_path=self.config.checkpoint_path,
            tokenizer_dir=self.config.tokenizer_dir,
            index_path=self.config.index_path,
        )
        test_app = create_app(config=limited_config, search_service=self.client.app.state.search_service)

        with TestClient(test_app) as client:
            headers = {"X-Forwarded-For": "198.51.100.42"}

            # Requests 1, 2, 3 should succeed
            for i in range(3):
                res = client.get("/api/v1/suggestions?q=test", headers=headers)
                self.assertEqual(res.status_code, 200, f"Request {i+1} should succeed")

            # Request 4 should be rejected with 429 Too Many Requests
            res4 = client.get("/api/v1/suggestions?q=test", headers=headers)
            self.assertEqual(res4.status_code, 429)
            data4 = res4.json()
            self.assertEqual(data4["error"], "Too Many Requests")
            self.assertIn("Rate limit exceeded", data4["detail"])
            self.assertIn("retry-after", res4.headers)
            retry_after = int(res4.headers["retry-after"])
            self.assertGreater(retry_after, 0)

    # -----------------------------------------------------------------------
    # 7. Production vs Development Configuration Separation
    # -----------------------------------------------------------------------

    def test_production_configuration_separation(self) -> None:
        """Verify production environment strictly disables auto-reload and disallows wildcard origins."""
        old_env = os.environ.get("SMARTSEARCH_ENV")
        try:
            os.environ["SMARTSEARCH_ENV"] = "production"
            prod_config = APIConfig.load()
            self.assertTrue(prod_config.is_production)
            self.assertFalse(prod_config.reload, "Auto-reload must be False in production")
            self.assertIsNone(prod_config.cors_origin_regex, "Default production CORS origin regex must be None")
        finally:
            if old_env is not None:
                os.environ["SMARTSEARCH_ENV"] = old_env
            else:
                os.environ.pop("SMARTSEARCH_ENV", None)

    # -----------------------------------------------------------------------
    # 8. CORS Verification
    # -----------------------------------------------------------------------

    def test_cors_chrome_extension_origin_in_dev(self) -> None:
        """CORS in dev must permit chrome-extension:// origin used by extension popups."""
        headers = {
            "Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
            "Access-Control-Request-Method": "GET",
        }
        response = self.client.options("/api/v1/suggestions?q=test", headers=headers)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
        )

    def test_cors_localhost_origin(self) -> None:
        """CORS must permit localhost and 127.0.0.1 origins."""
        headers = {
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "GET",
        }
        response = self.client.options("/api/v1/suggestions?q=test", headers=headers)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://localhost:8000",
        )

    def test_cors_null_origin_for_local_files(self) -> None:
        """CORS must permit null origin for file:// test pages."""
        headers = {
            "Origin": "null",
            "Access-Control-Request-Method": "GET",
        }
        response = self.client.options("/api/v1/suggestions?q=test", headers=headers)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "null",
        )

    def test_cors_disallows_untrusted_third_party(self) -> None:
        """CORS must reject unauthorized third-party web origins."""
        headers = {
            "Origin": "https://malicious-external-site.com",
            "Access-Control-Request-Method": "GET",
        }
        response = self.client.options("/api/v1/suggestions?q=test", headers=headers)
        self.assertIsNone(response.headers.get("access-control-allow-origin"))

    # -----------------------------------------------------------------------
    # 9. HTTP Methods, 404, 405 & Stack Trace Safety
    # -----------------------------------------------------------------------

    def test_unsupported_method_returns_405(self) -> None:
        """Unsupported POST method to GET suggestion route must return clean 405 without tracebacks."""
        response = self.client.post("/api/v1/suggestions", json={"q": "test"})
        self.assertEqual(response.status_code, 405)
        data = response.json()
        self.assertEqual(data["error"], "Method Not Allowed")
        self.assertNotIn("Traceback", str(data))

    def test_not_found_returns_404(self) -> None:
        """Request to unknown route returns clean 404 without internal server leakage."""
        response = self.client.get("/nonexistent_endpoint_xyz")
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertEqual(data["error"], "Not Found")
        self.assertNotIn("Traceback", str(data))

    # -----------------------------------------------------------------------
    # 10. Request Timing Header
    # -----------------------------------------------------------------------

    def test_process_time_header_present(self) -> None:
        """Every response must contain the X-Process-Time-Ms diagnostic header."""
        response = self.client.get("/api/v1/suggestions?q=python")
        self.assertIn("x-process-time-ms", response.headers)
        process_time = float(response.headers["x-process-time-ms"])
        self.assertGreater(process_time, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
