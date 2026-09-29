"""
src/trust/service.py

High-level Website Trust and Safety service for SmartSearch AI.
Coordinates URL decomposition, verified partner matching, threat intelligence,
and multi-signal safety evaluation with memory-bounded TTL caching.
"""

from __future__ import annotations

import datetime
import logging
import time
from pathlib import Path
from typing import Any, Optional

from src.trust.evaluator import evaluate_trust
from src.trust.models import (
    PartnerDetails,
    TrustAssessment,
    TrustCheckResult,
)
from src.trust.partner_directory import VerifiedPartnerDirectory
from src.trust.reputation_client import ThreatReputationClient
from src.trust.url_analyzer import normalize_and_analyze_url

LOG = logging.getLogger("smartsearch_trust")


class WebsiteTrustService:
    """Production service for evaluating website security and trust signals."""

    def __init__(
        self,
        partners_path: Optional[str | Path] = None,
        safe_browsing_api_key: Optional[str] = None,
        virustotal_api_key: Optional[str] = None,
        cache_ttl_seconds: float = 300.0,
        cache_max_items: int = 1000,
    ) -> None:
        self.partner_directory = VerifiedPartnerDirectory(partners_path)
        self.reputation_client = ThreatReputationClient(
            safe_browsing_api_key=safe_browsing_api_key,
            virustotal_api_key=virustotal_api_key,
            cache_ttl_seconds=cache_ttl_seconds,
        )
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_max_items = cache_max_items

        # In-memory evaluation cache: {cache_key: (timestamp, TrustCheckResult)}
        self._cache: dict[str, tuple[float, TrustCheckResult]] = {}

    def check_url(self, raw_url: str) -> TrustCheckResult:
        """Evaluates an input URL or domain string across all safety and trust signals.

        Privacy guarantee:
          - Does not log raw query strings or authentication credentials.
          - Strips sensitive tokens before normalization and caching.
        """
        t0 = time.perf_counter()

        # 1. Normalize and analyze URL structure
        analysis = normalize_and_analyze_url(raw_url)

        now_ts = time.time()
        iso_now = (
            datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )

        # Handle validation error immediately
        if not analysis.is_valid:
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            assessment, signals, checks = evaluate_trust(
                analysis, partner=None, reputation={"known_threat": False, "unavailable_checks": []}
            )
            return TrustCheckResult(
                url=raw_url,
                domain=analysis.hostname or "unknown",
                assessment=assessment,
                verified_partner=False,
                known_threat=False,
                security_checks=checks,
                unavailable_checks=[],
                signals=signals,
                checked_at=iso_now,
                cached=False,
                latency_ms=elapsed_ms,
            )

        # 2. Check TTL Cache
        cache_key = f"{analysis.scheme}://{analysis.hostname}:{analysis.port or ''}"
        if cache_key in self._cache:
            created_ts, cached_result = self._cache[cache_key]
            if now_ts - created_ts < self.cache_ttl_seconds:
                elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                # Return cached copy marked as cached
                return TrustCheckResult(
                    url=cached_result.url,
                    domain=cached_result.domain,
                    assessment=cached_result.assessment,
                    verified_partner=cached_result.verified_partner,
                    partner_details=cached_result.partner_details,
                    known_threat=cached_result.known_threat,
                    threat_type=cached_result.threat_type,
                    security_checks=cached_result.security_checks,
                    unavailable_checks=cached_result.unavailable_checks,
                    signals=cached_result.signals,
                    checked_at=cached_result.checked_at,
                    cached=True,
                    latency_ms=elapsed_ms,
                )

        # 3. Verified Partner Lookup
        partner = self.partner_directory.match(analysis.hostname)

        # 4. Threat Reputation Check
        reputation = self.reputation_client.check_threat(
            url=analysis.normalized_url, domain=analysis.hostname
        )

        # 5. Evaluate Multi-Signal Assessment
        assessment, signals, checks = evaluate_trust(analysis, partner, reputation)

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        result = TrustCheckResult(
            url=analysis.normalized_url,
            domain=analysis.hostname,
            assessment=assessment,
            verified_partner=partner is not None,
            partner_details=partner,
            known_threat=reputation.get("known_threat", False),
            threat_type=reputation.get("threat_type"),
            security_checks=checks,
            unavailable_checks=reputation.get("unavailable_checks", []),
            signals=signals,
            checked_at=iso_now,
            cached=False,
            latency_ms=elapsed_ms,
        )

        # 6. Store in cache with bounded size
        if len(self._cache) >= self.cache_max_items:
            # Simple eviction of oldest quarter of cache
            keys_to_remove = list(self._cache.keys())[: max(1, self.cache_max_items // 4)]
            for k in keys_to_remove:
                self._cache.pop(k, None)

        self._cache[cache_key] = (now_ts, result)

        # Privacy-conscious structured logging: only log domain and outcome, never raw query params
        LOG.info(
            "Website safety check: domain=%s assessment=%s partner=%s threat=%s (%.2fms)",
            analysis.hostname,
            assessment.value,
            partner is not None,
            result.known_threat,
            elapsed_ms,
        )

        return result

    def health(self) -> dict[str, Any]:
        """Component diagnostic health report."""
        return {
            "status": "ok",
            "partner_count": len(self.partner_directory._partners),
            "cached_evaluations": len(self._cache),
            "safe_browsing_configured": bool(self.reputation_client.safe_browsing_api_key),
            "virustotal_configured": bool(self.reputation_client.virustotal_api_key),
        }
