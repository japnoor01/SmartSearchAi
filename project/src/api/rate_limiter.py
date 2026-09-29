"""
src/api/rate_limiter.py

Lightweight, in-memory sliding-window rate limiter for SmartSearch AI.
Protects search suggestion endpoints against accidental client request floods,
rapid typing bursts without debouncing, and automated scraping.

Zero external dependencies (no Redis required).
Thread-safe and suitable for single-instance production and local development.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Deque

LOG = logging.getLogger("smartsearch_api.rate_limiter")


class InMemoryRateLimiter:
    """Thread-safe sliding-window rate limiter keyed by client identifier (e.g. IP)."""

    def __init__(
        self,
        max_requests: int = 120,
        window_seconds: float = 60.0,
        cleanup_interval_seconds: float = 300.0,
    ) -> None:
        self.max_requests = max(1, max_requests)
        self.window_seconds = max(1.0, float(window_seconds))
        self.cleanup_interval_seconds = float(cleanup_interval_seconds)

        self._lock = threading.Lock()
        self._history: dict[str, Deque[float]] = collections.defaultdict(collections.deque)
        self._last_cleanup = time.monotonic()

    def check_rate_limit(self, client_id: str) -> tuple[bool, int]:
        """Check whether the client is allowed to proceed under the current rate limit.

        Args:
            client_id: Unique client key, typically IP address.

        Returns:
            Tuple of (is_allowed: bool, retry_after_seconds: int).
            If is_allowed is True, retry_after_seconds is 0.
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            # Periodic house-keeping to prevent memory leak from stale IPs
            if now - self._last_cleanup > self.cleanup_interval_seconds:
                self._purge_stale_entries(cutoff)
                self._last_cleanup = now

            timestamps = self._history[client_id]

            # Drop timestamps outside the sliding window
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) < self.max_requests:
                timestamps.append(now)
                return True, 0

            # Rate limit exceeded: calculate wait time until oldest entry expires
            oldest = timestamps[0]
            retry_after = max(1, int(oldest + self.window_seconds - now + 0.99))
            return False, retry_after

    def _purge_stale_entries(self, cutoff: float) -> None:
        """Removes client queues that have no activity within the window."""
        stale_keys = [
            key for key, timestamps in self._history.items()
            if not timestamps or timestamps[-1] <= cutoff
        ]
        for key in stale_keys:
            del self._history[key]

    def reset(self) -> None:
        """Reset all rate limiting state (primarily for test isolation)."""
        with self._lock:
            self._history.clear()
            self._last_cleanup = time.monotonic()
