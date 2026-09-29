"""
src/trust/reputation_client.py

External threat intelligence and reputation provider integrations.
Supports Google Safe Browsing API v4, VirusTotal API v3, and local deterministic threat lists.
Strictly avoids fabricating reputation scores and explicitly surfaces unavailable checks.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import httpx

LOG = logging.getLogger("smartsearch_trust")

# Deterministic local threat domains for validation, compliance tests, and testing
_LOCAL_KNOWN_THREATS = {
    "testsafebrowsing.appspot.com": "MALWARE (Google SafeBrowsing Test)",
    "malware.wicar.org": "MALWARE (WICAR Test Site)",
    "phishing-test.badssl.com": "SOCIAL_ENGINEERING (Phishing Test)",
    "evil-phishing-test.com": "SOCIAL_ENGINEERING (Test Threat)",
    "malicious-domain.test": "MALWARE (Local Test Blocklist)",
    "phishing-brand-update.test": "SOCIAL_ENGINEERING (Brand Phishing)",
}


class ThreatReputationClient:
    """Manages threat intelligence queries across external providers and local blocklists."""

    def __init__(
        self,
        safe_browsing_api_key: Optional[str] = None,
        virustotal_api_key: Optional[str] = None,
        request_timeout: float = 2.0,
        cache_ttl_seconds: float = 300.0,
    ) -> None:
        self.safe_browsing_api_key = (
            safe_browsing_api_key
            if safe_browsing_api_key is not None
            else os.environ.get("SMARTSEARCH_SAFE_BROWSING_API_KEY", "").strip()
        )
        self.virustotal_api_key = (
            virustotal_api_key
            if virustotal_api_key is not None
            else os.environ.get("SMARTSEARCH_VIRUSTOTAL_API_KEY", "").strip()
        )
        self.request_timeout = request_timeout
        self.cache_ttl_seconds = cache_ttl_seconds

        # In-memory TTL cache: {cache_key: (timestamp, result_dict)}
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def check_threat(self, url: str, domain: str) -> dict[str, Any]:
        """Evaluates domain and URL against all configured threat providers.

        Returns a dictionary:
          {
            "known_threat": bool,
            "threat_type": Optional[str],
            "provider_hits": list[str],
            "unavailable_checks": list[str],
            "details": dict[str, Any]
          }
        """
        cache_key = f"{domain}:{url}"
        now = time.time()

        # Check cache
        if cache_key in self._cache:
            ts, cached_res = self._cache[cache_key]
            if now - ts < self.cache_ttl_seconds:
                return cached_res

        known_threat = False
        threat_type: Optional[str] = None
        provider_hits: list[str] = []
        unavailable_checks: list[str] = []
        details: dict[str, Any] = {}

        # 1. Local Known Threat List Check (Deterministic & Offline)
        clean_domain = domain.lower()
        if clean_domain in _LOCAL_KNOWN_THREATS:
            known_threat = True
            threat_type = _LOCAL_KNOWN_THREATS[clean_domain]
            provider_hits.append("local_threat_blocklist")
            details["local_threat_blocklist"] = {"matched": True, "type": threat_type}

        # 2. Google Safe Browsing API v4
        if not self.safe_browsing_api_key:
            unavailable_checks.append("google_safe_browsing")
        else:
            sb_hit, sb_type = self._query_google_safe_browsing(url)
            if sb_hit is None:
                unavailable_checks.append("google_safe_browsing")
            elif sb_hit:
                known_threat = True
                threat_type = threat_type or sb_type
                provider_hits.append("google_safe_browsing")
                details["google_safe_browsing"] = {"matched": True, "type": sb_type}
            else:
                details["google_safe_browsing"] = {"matched": False}

        # 3. VirusTotal API v3
        if not self.virustotal_api_key:
            unavailable_checks.append("virustotal")
        else:
            vt_hit, vt_type = self._query_virustotal(domain)
            if vt_hit is None:
                unavailable_checks.append("virustotal")
            elif vt_hit:
                known_threat = True
                threat_type = threat_type or vt_type
                provider_hits.append("virustotal")
                details["virustotal"] = {"matched": True, "type": vt_type}
            else:
                details["virustotal"] = {"matched": False}

        result = {
            "known_threat": known_threat,
            "threat_type": threat_type,
            "provider_hits": provider_hits,
            "unavailable_checks": unavailable_checks,
            "details": details,
        }

        # Store in cache (limit max size to prevent unbounded memory growth)
        if len(self._cache) > 2000:
            self._cache.clear()
        self._cache[cache_key] = (now, result)

        return result

    def _query_google_safe_browsing(self, target_url: str) -> tuple[Optional[bool], Optional[str]]:
        """Queries Google Safe Browsing Lookup API v4.

        Returns (is_threat, threat_type). If query fails or times out, returns (None, None).
        """
        endpoint = f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={self.safe_browsing_api_key}"
        payload = {
            "client": {"clientId": "smartsearch-ai", "clientVersion": "1.0.0"},
            "threatInfo": {
                "threatTypes": [
                    "MALWARE",
                    "SOCIAL_ENGINEERING",
                    "UNWANTED_SOFTWARE",
                    "POTENTIALLY_HARMFUL_APPLICATION",
                ],
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": [{"url": target_url}],
            },
        }

        try:
            with httpx.Client(timeout=self.request_timeout) as client:
                res = client.post(endpoint, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    matches = data.get("matches", [])
                    if matches:
                        match_type = matches[0].get("threatType", "MALICIOUS")
                        return True, match_type
                    return False, None
                LOG.warning("Google Safe Browsing returned status %d", res.status_code)
                return None, None
        except Exception as exc:
            LOG.warning("Google Safe Browsing query failed: %s", exc)
            return None, None

    def _query_virustotal(self, domain: str) -> tuple[Optional[bool], Optional[str]]:
        """Queries VirusTotal API v3 domain report.

        Returns (is_threat, threat_type). If query fails or times out, returns (None, None).
        """
        endpoint = f"https://www.virustotal.com/api/v3/domains/{domain}"
        headers = {"x-apikey": self.virustotal_api_key}

        try:
            with httpx.Client(timeout=self.request_timeout) as client:
                res = client.get(endpoint, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    stats = (
                        data.get("data", {})
                        .get("attributes", {})
                        .get("last_analysis_stats", {})
                    )
                    malicious_count = stats.get("malicious", 0)
                    suspicious_count = stats.get("suspicious", 0)
                    if malicious_count > 0 or suspicious_count > 1:
                        return True, f"MALICIOUS_DETECTIONS_{malicious_count}"
                    return False, None
                LOG.warning("VirusTotal returned status %d", res.status_code)
                return None, None
        except Exception as exc:
            LOG.warning("VirusTotal query failed: %s", exc)
            return None, None
