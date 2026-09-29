"""
src/api/service.py

Service layer bridging the FastAPI web framework with the underlying
SmartSearch ML pipeline (SmartSearchRanker and PrefixIndex).
Ensures models are loaded once and shared cleanly across incoming requests.
Includes performance tracking metrics and health/readiness inspection.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from src.api.config import APIConfig
from src.models.lstm.inference import SmartSearchRanker

LOG = logging.getLogger("smartsearch_api")


class SearchService:
    """Thread-safe search suggestion service wrapping SmartSearchRanker."""

    def __init__(
        self,
        ranker: SmartSearchRanker | None = None,
        config: APIConfig | None = None,
    ) -> None:
        self.config = config or APIConfig.load()
        self.start_time = time.time()
        self._lock = threading.Lock()

        # Operational metrics counters
        self.total_requests = 0
        self.total_suggestions_served = 0
        self.total_errors = 0
        self.total_rate_limited = 0
        self.total_latency_ms = 0.0
        self.last_latency_ms = 0.0

        if ranker is not None:
            self.ranker = ranker
        else:
            LOG.info(
                "Initializing SmartSearchRanker (checkpoint: %s, index: %s)",
                self.config.checkpoint_path,
                self.config.index_path,
            )
            self.ranker = SmartSearchRanker(
                index_path=self.config.index_path,
                checkpoint_path=self.config.checkpoint_path,
                tokenizer_dir=self.config.tokenizer_dir,
                device=self.config.device,
                candidate_pool_size=self.config.candidate_pool_size,
                default_top_k=self.config.default_top_k,
            )

    def get_suggestions(
        self,
        query: str,
        top_k: int,
    ) -> tuple[list[dict[str, Any]], float]:
        """Fetch and rank autocomplete suggestions for a given query prefix.

        Returns:
            Tuple of (suggestions_list, latency_in_ms).
        """
        # Return empty suggestions for empty or whitespace-only inputs without hitting the index
        if not query or not query.strip():
            return [], 0.0

        t0 = time.perf_counter()
        try:
            results = self.ranker.suggest(
                prefix=query,
                top_k=top_k,
                candidate_pool_size=self.config.candidate_pool_size,
            )
        except Exception:
            with self._lock:
                self.total_errors += 1
            raise

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        rounded_ms = round(elapsed_ms, 2)

        with self._lock:
            self.total_suggestions_served += 1
            self.total_latency_ms += elapsed_ms
            self.last_latency_ms = rounded_ms

        return results, rounded_ms

    def record_request(self) -> None:
        """Increment overall incoming request counter."""
        with self._lock:
            self.total_requests += 1

    def record_rate_limited(self) -> None:
        """Increment rate-limit rejection counter."""
        with self._lock:
            self.total_rate_limited += 1

    def health(self) -> dict[str, Any]:
        """Check status of ranker model and prefix index (liveness probe)."""
        model_loaded = hasattr(self.ranker, "ranker") and self.ranker.ranker.model is not None
        index_connected = hasattr(self.ranker, "index") and self.ranker.index is not None
        device_name = str(getattr(self.ranker.ranker, "device", "cpu"))

        return {
            "status": "ok",
            "model_loaded": model_loaded,
            "index_connected": index_connected,
            "device": device_name,
        }

    def readiness(self) -> dict[str, Any]:
        """Check operational readiness of backing ML model and SQLite index."""
        health_info = self.health()
        is_ready = bool(health_info["model_loaded"] and health_info["index_connected"])
        uptime = round(time.time() - self.start_time, 2)

        return {
            "status": "ready" if is_ready else "not_ready",
            "model_loaded": health_info["model_loaded"],
            "index_connected": health_info["index_connected"],
            "device": health_info["device"],
            "uptime_seconds": uptime,
        }

    def get_metrics(self) -> dict[str, Any]:
        """Retrieve operational service performance metrics."""
        with self._lock:
            uptime = round(time.time() - self.start_time, 2)
            avg_latency = (
                round(self.total_latency_ms / self.total_suggestions_served, 2)
                if self.total_suggestions_served > 0
                else 0.0
            )

            return {
                "uptime_seconds": uptime,
                "total_requests": self.total_requests,
                "total_suggestions_served": self.total_suggestions_served,
                "total_errors": self.total_errors,
                "total_rate_limited": self.total_rate_limited,
                "avg_latency_ms": avg_latency,
                "last_latency_ms": self.last_latency_ms,
            }

    def close(self) -> None:
        """Close backing index resources."""
        if hasattr(self.ranker, "close"):
            self.ranker.close()
