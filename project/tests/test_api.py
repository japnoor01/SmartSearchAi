"""
tests/test_api.py

Unit and integration tests for the SmartSearch AI FastAPI backend.
Tests endpoints:
  - GET /health
  - GET /api/v1/version
  - GET /api/v1/suggestions
  - Input validations (missing q, empty q, whitespace q, invalid top_k, excessive query length)
  - Error responses and headers (CORS, timing)
  - Missing artifact startup failure behavior
  - Real model & index integration test (guarded by RUN_REAL_INDEX_TESTS=1)

Unit tests use an in-memory mock SearchService and DO NOT depend on the 3.48GB SQLite index.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


class _Skipped(Exception):
    pass


class _RaisesContext:
    def __init__(self, expected_exception, match=None):
        self.expected_exception = expected_exception
        self.match = match

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            raise AssertionError(f"DID NOT RAISE {self.expected_exception}")
        if not issubclass(exc_type, self.expected_exception):
            return False
        if self.match and self.match not in str(exc_val):
            raise AssertionError(f"Exception {exc_val!r} did not match {self.match!r}")
        return True


try:
    import pytest
except ImportError:
    import types
    pytest = types.ModuleType("pytest")
    pytest.fixture = lambda *a, **kw: (lambda fn: fn)
    pytest.importorskip = lambda name: pytest if name == "pytest" else __import__(name)
    pytest.skip = lambda reason="": (_ for _ in ()).throw(_Skipped(reason))
    pytest.raises = _RaisesContext
    sys.modules["pytest"] = pytest

from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.app import create_app
from src.api.config import APIConfig
from src.api.schemas import HealthResponse, SuggestionsResponse, VersionResponse


# ---------------------------------------------------------------------------
# Mock Search Service for Fast, Isolated Unit Testing
# ---------------------------------------------------------------------------


class MockSearchService:
    """In-memory mock of SearchService for unit tests without index/model files."""

    def __init__(self, canned_data: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.canned_data = canned_data or {
            "deep": [
                {"suggestion": "deep learning", "score": -0.3671},
                {"suggestion": "deep learning tutorial", "score": -0.4165},
                {"suggestion": "deep learning projects", "score": -0.5394},
            ],
            "deep learning": [
                {"suggestion": "deep learning enables rapid identification", "score": -0.3671},
                {"suggestion": "deep learning dropout", "score": -0.4165},
            ],
            "empty_result": [],
        }
        self.call_count = 0

    def get_suggestions(self, query: str, top_k: int) -> tuple[list[dict[str, Any]], float]:
        self.call_count += 1
        if not query or not query.strip():
            return [], 0.0

        matches = self.canned_data.get(query.lower().strip(), [])
        return matches[:top_k], 12.34

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "model_loaded": True,
            "index_connected": True,
            "device": "cpu",
        }

    def close(self) -> None:
        pass


def _get_client() -> TestClient:
    """Helper creating a test client bound to MockSearchService."""
    mock_service = MockSearchService()
    test_config = APIConfig(
        max_query_length=200,
        min_top_k=1,
        max_top_k=50,
        default_top_k=5,
    )
    app = create_app(config=test_config, search_service=mock_service)
    return TestClient(app)


@pytest.fixture
def mock_client() -> TestClient:
    """Fixture providing a TestClient configured with MockSearchService."""
    client = _get_client()
    yield client


# ---------------------------------------------------------------------------
# Health & Version Endpoint Tests
# ---------------------------------------------------------------------------


def test_health_endpoint(mock_client: TestClient | None = None) -> None:
    """Verify GET /health returns 200 and conforms to HealthResponse schema."""
    client = mock_client or _get_client()
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["model_loaded"] is True
    assert data["index_connected"] is True
    assert "device" in data
    # Validate against Pydantic schema
    HealthResponse(**data)


def test_version_endpoint(mock_client: TestClient | None = None) -> None:
    """Verify GET /api/v1/version returns 200 and metadata schema."""
    client = mock_client or _get_client()
    response = client.get("/api/v1/version")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0.0"
    assert data["api_version"] == "v1"
    assert data["model_type"] == "LSTM Ranker"
    assert "description" in data
    # Validate against Pydantic schema
    VersionResponse(**data)


# ---------------------------------------------------------------------------
# Suggestions Endpoint Tests
# ---------------------------------------------------------------------------


def test_suggestions_valid_query(mock_client: TestClient | None = None) -> None:
    """Verify GET /api/v1/suggestions returns ranked suggestions with 200."""
    client = mock_client or _get_client()
    response = client.get("/api/v1/suggestions?q=deep&top_k=2")
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "deep"
    assert data["count"] == 2
    assert len(data["suggestions"]) == 2
    assert data["suggestions"][0]["text"] == "deep learning"
    assert isinstance(data["suggestions"][0]["score"], float)
    assert data["latency_ms"] is not None
    # Validate against Pydantic schema
    parsed = SuggestionsResponse(**data)
    assert parsed.count == 2


def test_suggestions_empty_query(mock_client: TestClient | None = None) -> None:
    """Verify empty query parameter q='' returns empty suggestions without error."""
    client = mock_client or _get_client()
    response = client.get("/api/v1/suggestions?q=")
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == ""
    assert data["count"] == 0
    assert data["suggestions"] == []


def test_suggestions_whitespace_query(mock_client: TestClient | None = None) -> None:
    """Verify whitespace-only query parameter returns empty suggestions without error."""
    client = mock_client or _get_client()
    response = client.get("/api/v1/suggestions?q=%20%20%20")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 0
    assert data["suggestions"] == []


def test_suggestions_no_candidates(mock_client: TestClient | None = None) -> None:
    """Verify query yielding no index matches returns empty suggestions with count=0."""
    client = mock_client or _get_client()
    response = client.get("/api/v1/suggestions?q=nonexistent_query_xyz")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 0
    assert data["suggestions"] == []


def test_suggestions_missing_q_parameter(mock_client: TestClient | None = None) -> None:
    """Verify missing required parameter q returns 422 Unprocessable Entity."""
    client = mock_client or _get_client()
    response = client.get("/api/v1/suggestions")
    assert response.status_code == 422
    data = response.json()
    assert data["error"] == "Unprocessable Entity"
    assert "field: query -> q" in data["detail"] or "missing" in data["detail"].lower()


def test_suggestions_invalid_top_k_below_min(mock_client: TestClient | None = None) -> None:
    """Verify top_k < 1 returns 400 Bad Request."""
    client = mock_client or _get_client()
    response = client.get("/api/v1/suggestions?q=deep&top_k=0")
    assert response.status_code == 400
    data = response.json()
    assert data["error"] == "Bad Request"
    assert "top_k" in data["detail"]
    assert "at least 1" in data["detail"]


def test_suggestions_excessive_top_k(mock_client: TestClient | None = None) -> None:
    """Verify top_k > max_top_k returns 400 Bad Request."""
    client = mock_client or _get_client()
    response = client.get("/api/v1/suggestions?q=deep&top_k=100")
    assert response.status_code == 400
    data = response.json()
    assert data["error"] == "Bad Request"
    assert "cannot exceed 50" in data["detail"]


def test_suggestions_malformed_top_k_string(mock_client: TestClient | None = None) -> None:
    """Verify non-integer top_k returns 422 Unprocessable Entity."""
    client = mock_client or _get_client()
    response = client.get("/api/v1/suggestions?q=deep&top_k=invalid_number")
    assert response.status_code == 422
    data = response.json()
    assert data["error"] == "Unprocessable Entity"


def test_suggestions_excessive_query_length(mock_client: TestClient | None = None) -> None:
    """Verify query exceeding max_query_length returns 400 Bad Request."""
    client = mock_client or _get_client()
    long_query = "a" * 250
    response = client.get(f"/api/v1/suggestions?q={long_query}")
    assert response.status_code == 400
    data = response.json()
    assert data["error"] == "Bad Request"
    assert "maximum allowed length" in data["detail"]


# ---------------------------------------------------------------------------
# Headers, Timing & CORS Tests
# ---------------------------------------------------------------------------


def test_process_time_header(mock_client: TestClient | None = None) -> None:
    """Verify response includes X-Process-Time-Ms header."""
    client = mock_client or _get_client()
    response = client.get("/health")
    assert "X-Process-Time-Ms" in response.headers
    time_val = float(response.headers["X-Process-Time-Ms"])
    assert time_val >= 0.0


def test_cors_headers(mock_client: TestClient | None = None) -> None:
    """Verify CORS preflight and headers allow configured origins."""
    client = mock_client or _get_client()
    headers = {"Origin": "http://localhost:3000"}
    response = client.get("/api/v1/suggestions?q=deep", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


# ---------------------------------------------------------------------------
# Startup & Artifact Validation
# ---------------------------------------------------------------------------


def test_startup_fails_on_missing_checkpoint(tmp_path: Path) -> None:
    """Verify application lifespan fails clearly when model checkpoint is missing."""
    invalid_config = APIConfig(
        checkpoint_path=tmp_path / "nonexistent_model.pt",
        tokenizer_dir=tmp_path / "tokenizer",
        index_path=tmp_path / "index.sqlite3",
    )
    app = create_app(config=invalid_config)
    with pytest.raises(FileNotFoundError, match="checkpoint not found"):
        with TestClient(app):
            pass


# ---------------------------------------------------------------------------
# Integration Test: Real Model & SQLite Index (RUN_REAL_INDEX_TESTS=1)
# ---------------------------------------------------------------------------


def test_real_api_integration() -> None:
    """Full integration test using live checkpoint and real 3.48GB SQLite index.

    Guarded by RUN_REAL_INDEX_TESTS=1. Run locally with:
        $env:RUN_REAL_INDEX_TESTS="1"; python tests/_minimal_pytest_runner.py
    """
    if os.environ.get("RUN_REAL_INDEX_TESTS") != "1":
        pytest.skip("Real API integration test skipped (set RUN_REAL_INDEX_TESTS=1 to run)")

    real_config = APIConfig.load()
    if not real_config.checkpoint_path.exists() or not real_config.index_path.exists():
        pytest.skip("Real artifacts missing, skipping integration test.")

    app = create_app(config=real_config)
    with TestClient(app) as client:
        # 1. Health check on real components
        health_res = client.get("/health")
        assert health_res.status_code == 200
        assert health_res.json()["status"] == "ok"

        # 2. Suggestions endpoint on real MS MARCO data
        res = client.get("/api/v1/suggestions?q=deep%20learning&top_k=3")
        assert res.status_code == 200
        data = res.json()
        assert data["count"] > 0
        assert data["query"] == "deep learning"
        assert all(isinstance(s["score"], float) for s in data["suggestions"])
        assert any("deep learning" in s["text"].lower() for s in data["suggestions"])


if __name__ == "__main__":
    import tempfile
    import traceback

    passed, skipped, failed = 0, 0, 0
    print("Running SmartSearch AI API test suite...")
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                params = fn.__code__.co_varnames[: fn.__code__.co_argcount]
                kwargs = {}
                if "tmp_path" in params:
                    kwargs["tmp_path"] = Path(tempfile.mkdtemp())
                fn(**kwargs)
                print(f"  ok   {name}")
                passed += 1
            except Exception as e:
                if "Skip" in type(e).__name__:
                    print(f"  skip {name} -- {e}")
                    skipped += 1
                else:
                    print(f"  FAIL {name}")
                    traceback.print_exc()
                    failed += 1

    print(f"\nResult: {passed} passed, {skipped} skipped, {failed} failed")
    sys.exit(1 if failed else 0)

