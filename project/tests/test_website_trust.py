"""
tests/test_website_trust.py

Comprehensive unit and integration test suite for the SmartSearch AI Website Trust & Safety feature.
Exercises all required specifications:
  - URL normalization & privacy stripping
  - HTTPS detection
  - Domain and apex extraction
  - Verified partner matching (exact and subdomain)
  - Not-verified behavior ('not verified' != 'fraudulent')
  - Known-threat handling (Safe Browsing, VirusTotal, local blocklists)
  - Unavailable external reputation providers
  - Memory-bounded TTL caching
  - Malformed and edge-case URLs
  - Private, local, and loopback addresses
  - Localhost handling
  - IP address literals (public vs private)
  - Subdomain depth and suspicious phishing keywords
  - Unicode / Punycode IDN homograph handling
  - API endpoint GET /api/v1/site-check error handling & rate limiting
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.api.app import create_app
from src.api.config import APIConfig
from src.trust.evaluator import evaluate_trust
from src.trust.models import (
    PartnerDetails,
    TrustAssessment,
)
from src.trust.partner_directory import VerifiedPartnerDirectory
from src.trust.reputation_client import ThreatReputationClient
from src.trust.service import WebsiteTrustService
from src.trust.url_analyzer import normalize_and_analyze_url


# =============================================================================
# 1. URL Normalization & Privacy Tests
# =============================================================================


class TestUrlNormalization:
    """Verifies parsing, scheme defaults, and stripping of sensitive tokens."""

    def test_strip_query_params_and_fragments(self) -> None:
        """Query parameters and fragments must be stripped to prevent logging sensitive tokens."""
        raw = "https://example.com/checkout?session_id=secret123&user=john#payment"
        analysis = normalize_and_analyze_url(raw)
        assert analysis.is_valid is True
        assert analysis.hostname == "example.com"
        assert analysis.normalized_url == "https://example.com/checkout"
        assert "secret123" not in analysis.normalized_url
        assert "payment" not in analysis.normalized_url

    def test_strip_embedded_credentials(self) -> None:
        """Userinfo credentials must be detected as high-risk and stripped from normalized URL."""
        raw = "http://admin:superpassword@banking-portal.com/login"
        analysis = normalize_and_analyze_url(raw)
        assert analysis.is_valid is True
        assert analysis.has_userinfo is True
        assert analysis.hostname == "banking-portal.com"
        assert "superpassword" not in analysis.normalized_url

    def test_bare_domain_defaults_to_https(self) -> None:
        """Input without explicit scheme defaults to HTTPS."""
        analysis = normalize_and_analyze_url("wikipedia.org")
        assert analysis.is_valid is True
        assert analysis.scheme == "https"
        assert analysis.hostname == "wikipedia.org"
        assert analysis.normalized_url == "https://wikipedia.org/"

    def test_explicit_http_scheme(self) -> None:
        """Explicit HTTP scheme is preserved and identified."""
        analysis = normalize_and_analyze_url("http://insecure-site.org/index.html")
        assert analysis.is_valid is True
        assert analysis.scheme == "http"
        assert analysis.hostname == "insecure-site.org"


# =============================================================================
# 2. Domain & Apex Extraction Tests
# =============================================================================


class TestDomainExtraction:
    """Verifies apex domain and multi-level subdomain parsing."""

    def test_standard_apex_and_subdomain(self) -> None:
        analysis = normalize_and_analyze_url("https://en.wikipedia.org/wiki/Deep_learning")
        assert analysis.hostname == "en.wikipedia.org"
        assert analysis.apex_domain == "wikipedia.org"
        assert analysis.subdomain == "en"
        assert analysis.subdomain_depth == 1

    def test_multipart_tld_co_uk(self) -> None:
        analysis = normalize_and_analyze_url("https://news.bbc.co.uk/world")
        assert analysis.hostname == "news.bbc.co.uk"
        assert analysis.apex_domain == "bbc.co.uk"
        assert analysis.subdomain == "news"

    def test_deep_subdomain_nesting(self) -> None:
        analysis = normalize_and_analyze_url("https://a.b.c.d.example.com")
        assert analysis.subdomain_depth == 4
        assert analysis.subdomain == "a.b.c.d"

    def test_suspicious_brand_token_in_subdomain(self) -> None:
        """Subdomain containing brand keyword not in apex triggers suspicious token flag."""
        analysis = normalize_and_analyze_url("https://paypal.verification-portal.net/login")
        assert "paypal" in analysis.suspicious_tokens


# =============================================================================
# 3. Unicode, Punycode & IDN Homograph Tests
# =============================================================================


class TestUnicodeAndPunycode:
    """Verifies internationalized domain names (IDN) and punycode detection."""

    def test_punycode_xn_prefix(self) -> None:
        analysis = normalize_and_analyze_url("https://xn--e1afmkfd.xn--p1ai/")
        assert analysis.is_valid is True
        assert analysis.is_punycode_or_idn is True

    def test_unicode_character_domain(self) -> None:
        """Cyrillic characters spoofing Latin (homograph attack)."""
        # 'а' is Cyrillic small letter a (U+0430)
        cyrillic_apple = "https://аpple.com"
        analysis = normalize_and_analyze_url(cyrillic_apple)
        assert analysis.is_valid is True
        assert analysis.is_punycode_or_idn is True


# =============================================================================
# 4. IP Addresses, Localhost & Private URL Tests
# =============================================================================


class TestIpAndLocalhost:
    """Verifies detection of IPv4, IPv6, localhost, and private networks."""

    def test_public_ipv4_literal(self) -> None:
        analysis = normalize_and_analyze_url("http://93.184.216.34/path")
        assert analysis.is_ip is True
        assert analysis.is_private_or_local is False
        assert analysis.hostname == "93.184.216.34"

    def test_private_ipv4_literal(self) -> None:
        analysis = normalize_and_analyze_url("http://192.168.1.100:8080/")
        assert analysis.is_ip is True
        assert analysis.is_private_or_local is True
        assert analysis.port == 8080

    def test_localhost_ipv4_and_name(self) -> None:
        for target in ("http://localhost:8000", "http://127.0.0.1:8000"):
            analysis = normalize_and_analyze_url(target)
            assert analysis.is_private_or_local is True

    def test_ipv6_loopback(self) -> None:
        analysis = normalize_and_analyze_url("http://[::1]:8080/test")
        assert analysis.is_ip is True
        assert analysis.is_private_or_local is True
        assert analysis.port == 8080


# =============================================================================
# 5. Malformed & Edge-Case URLs
# =============================================================================


class TestMalformedUrls:
    """Verifies graceful handling of invalid syntax without unhandled exceptions."""

    def test_empty_string(self) -> None:
        analysis = normalize_and_analyze_url("")
        assert analysis.is_valid is False
        assert analysis.validation_error is not None

    def test_unsupported_scheme(self) -> None:
        analysis = normalize_and_analyze_url("ftp://ftp.example.com/file.zip")
        assert analysis.is_valid is False
        assert "Unsupported scheme" in (analysis.validation_error or "")

    def test_missing_host(self) -> None:
        analysis = normalize_and_analyze_url("https://")
        assert analysis.is_valid is False

    def test_bare_word_missing_tld(self) -> None:
        analysis = normalize_and_analyze_url("not_a_valid_url")
        assert analysis.is_valid is False
        assert "Invalid domain" in (analysis.validation_error or "")


# =============================================================================
# 6. Verified Partner Directory Tests
# =============================================================================


class TestVerifiedPartners:
    """Verifies partner lookup, subdomains, and the critical 'not verified != fraudulent' rule."""

    def test_exact_partner_match(self) -> None:
        dir_obj = VerifiedPartnerDirectory()
        match = dir_obj.match("wikipedia.org")
        assert match is not None
        assert match.organization == "Wikimedia Foundation"

    def test_subdomain_partner_match(self) -> None:
        dir_obj = VerifiedPartnerDirectory()
        match = dir_obj.match("en.wikipedia.org")
        assert match is not None
        assert match.organization == "Wikimedia Foundation"
        assert match.domain == "wikipedia.org"

    def test_subdomain_disallowed_partner(self) -> None:
        dir_obj = VerifiedPartnerDirectory()
        # fastapi.tiangolo.com has allow_subdomains=False
        match = dir_obj.match("sub.fastapi.tiangolo.com")
        assert match is None

    def test_unknown_domain_is_not_verified(self) -> None:
        dir_obj = VerifiedPartnerDirectory()
        match = dir_obj.match("some-random-legit-blog.org")
        assert match is None


# =============================================================================
# 7. Evaluator State Determinations & Reasoning
# =============================================================================


class TestEvaluatorLogic:
    """Tests the 5 explicit assessment states."""

    def test_verified_partner_with_https_is_low_risk(self) -> None:
        analysis = normalize_and_analyze_url("https://en.wikipedia.org")
        partner = PartnerDetails(organization="Wikimedia Foundation", category="Reference", domain="wikipedia.org")
        rep = {"known_threat": False, "unavailable_checks": ["google_safe_browsing", "virustotal"]}
        assessment, signals, checks = evaluate_trust(analysis, partner, rep)

        assert assessment == TrustAssessment.LOW_RISK
        assert checks["verified_partner"] is True
        assert any("Verified Partner: Wikimedia Foundation" in s for s in signals)

    def test_known_threat_takes_absolute_priority(self) -> None:
        analysis = normalize_and_analyze_url("https://wikipedia.org")
        partner = PartnerDetails(organization="Wikimedia Foundation", category="Reference", domain="wikipedia.org")
        rep = {
            "known_threat": True,
            "threat_type": "MALWARE",
            "provider_hits": ["google_safe_browsing"],
            "unavailable_checks": [],
        }
        assessment, signals, checks = evaluate_trust(analysis, partner, rep)
        assert assessment == TrustAssessment.KNOWN_THREAT
        assert checks["threat_detected"] is True

    def test_public_ip_literal_is_high_risk(self) -> None:
        analysis = normalize_and_analyze_url("http://93.184.216.34/index")
        rep = {"known_threat": False, "unavailable_checks": []}
        assessment, signals, checks = evaluate_trust(analysis, None, rep)
        assert assessment == TrustAssessment.HIGH_RISK
        assert any("Public IP address literal" in s for s in signals)


    def test_unencrypted_http_triggers_caution(self) -> None:
        analysis = normalize_and_analyze_url("http://example.com")
        rep = {"known_threat": False, "unavailable_checks": []}
        assessment, signals, checks = evaluate_trust(analysis, None, rep)
        assert assessment == TrustAssessment.CAUTION
        assert any("Insecure HTTP" in s for s in signals)

    def test_unverified_entity_without_external_reputation_is_insufficient_information(self) -> None:
        """When not in partner directory and external checks are unavailable, do NOT declare 'legit'."""
        analysis = normalize_and_analyze_url("https://some-standard-blog.com")
        rep = {"known_threat": False, "unavailable_checks": ["google_safe_browsing", "virustotal"]}
        assessment, signals, checks = evaluate_trust(analysis, None, rep)

        assert assessment == TrustAssessment.INSUFFICIENT_INFORMATION
        assert checks["verified_partner"] is False
        assert any("not in the verified partner directory (this does not imply fraud)" in s for s in signals)
        assert any("Insufficient independent reputation data" in s for s in signals)

    def test_unverified_entity_with_clean_external_rep_is_low_risk(self) -> None:
        analysis = normalize_and_analyze_url("https://some-clean-domain.com")
        rep = {"known_threat": False, "unavailable_checks": [], "provider_hits": []}
        assessment, signals, checks = evaluate_trust(analysis, None, rep)
        assert assessment == TrustAssessment.LOW_RISK


# =============================================================================
# 8. External Reputation Client & Deterministic Fallbacks
# =============================================================================


class TestReputationClient:
    """Verifies provider hit reporting and handling of missing API keys."""

    def test_unconfigured_api_keys_reported_as_unavailable(self) -> None:
        client = ThreatReputationClient(safe_browsing_api_key="", virustotal_api_key="")
        res = client.check_threat("https://example.com", "example.com")
        assert res["known_threat"] is False
        assert "google_safe_browsing" in res["unavailable_checks"]
        assert "virustotal" in res["unavailable_checks"]

    def test_local_threat_blocklist_detected(self) -> None:
        client = ThreatReputationClient(safe_browsing_api_key="", virustotal_api_key="")
        res = client.check_threat(
            "https://testsafebrowsing.appspot.com/s/malware.html",
            "testsafebrowsing.appspot.com",
        )
        assert res["known_threat"] is True
        assert "local_threat_blocklist" in res["provider_hits"]

    @patch("httpx.Client.post")
    def test_google_safe_browsing_mock_positive(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "matches": [{"threatType": "MALWARE", "platformType": "ANY_PLATFORM"}]
        }
        mock_post.return_value = mock_resp

        client = ThreatReputationClient(safe_browsing_api_key="fake-sb-key")
        res = client.check_threat("https://malicious-test.com", "malicious-test.com")
        assert res["known_threat"] is True
        assert res["threat_type"] == "MALWARE"
        assert "google_safe_browsing" in res["provider_hits"]


# =============================================================================
# 9. WebsiteTrustService Integration & Caching
# =============================================================================


class TestWebsiteTrustService:
    """Verifies end-to-end service orchestration and caching behavior."""

    def test_caching_returns_cached_flag(self) -> None:
        service = WebsiteTrustService(cache_ttl_seconds=60.0)

        # First call: fresh
        res1 = service.check_url("https://wikipedia.org")
        assert res1.cached is False
        assert res1.assessment == TrustAssessment.LOW_RISK

        # Second call: must be served from cache
        res2 = service.check_url("https://wikipedia.org")
        assert res2.cached is True
        assert res2.domain == "wikipedia.org"
        assert res2.assessment == TrustAssessment.LOW_RISK

    def test_service_health(self) -> None:
        service = WebsiteTrustService()
        health = service.health()
        assert health["status"] == "ok"
        assert health["partner_count"] > 0


# =============================================================================
# 10. FastAPI Route GET /api/v1/site-check Tests
# =============================================================================


class TestSiteCheckEndpoint:
    """Verifies the HTTP endpoint contract, error validation, and rate limiting."""

    @pytest.fixture
    def client(self) -> TestClient:
        cfg = APIConfig(
            env="testing",
            rate_limit_enabled=True,
            rate_limit_requests=5,
            rate_limit_window_seconds=60.0,
        )
        trust_svc = WebsiteTrustService()
        # Mock SearchService so app doesn't require 3.5GB SQLite index
        mock_search = MagicMock()
        mock_search.health.return_value = {
            "status": "ok", "model_loaded": True, "index_connected": True, "device": "cpu"
        }

        app = create_app(config=cfg, search_service=mock_search, trust_service=trust_svc)
        return TestClient(app)

    def test_site_check_verified_partner(self, client: TestClient) -> None:
        resp = client.get("/api/v1/site-check?url=https://wikipedia.org")
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "wikipedia.org"
        assert data["verified_partner"] is True
        assert data["partner_details"] is not None
        assert data["partner_details"]["organization"] == "Wikimedia Foundation"
        assert data["assessment"] == "low_risk"
        assert data["known_threat"] is False
        assert "checked_at" in data
        assert isinstance(data["signals"], list)

    def test_site_check_unverified_site(self, client: TestClient) -> None:
        resp = client.get("/api/v1/site-check?url=https://unknown-random-blog.com")
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "unknown-random-blog.com"
        assert data["verified_partner"] is False
        assert data["partner_details"] is None
        assert data["assessment"] in ("insufficient_information", "low_risk")

    def test_site_check_known_threat(self, client: TestClient) -> None:
        resp = client.get("/api/v1/site-check?url=https://testsafebrowsing.appspot.com/malware")
        assert resp.status_code == 200
        data = resp.json()
        assert data["assessment"] == "known_threat"
        assert data["known_threat"] is True

    def test_site_check_missing_url_returns_422_or_400(self, client: TestClient) -> None:
        resp = client.get("/api/v1/site-check")
        assert resp.status_code == 422

    def test_site_check_empty_url_returns_400(self, client: TestClient) -> None:
        resp = client.get("/api/v1/site-check?url=%20")
        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"].lower()

    def test_site_check_excessive_url_length_returns_400(self, client: TestClient) -> None:
        huge_url = "https://example.com/" + ("a" * 2100)
        resp = client.get(f"/api/v1/site-check?url={huge_url}")
        assert resp.status_code == 400
        assert "exceeds" in resp.json()["detail"].lower()

    def test_site_check_rate_limiting_enforcement(self, client: TestClient) -> None:
        """Exceeding 5 requests in window returns 429."""
        for _ in range(5):
            r = client.get("/api/v1/site-check?url=https://wikipedia.org")
            assert r.status_code == 200

        # 6th request must be rejected with 429
        rejected = client.get("/api/v1/site-check?url=https://wikipedia.org")
        assert rejected.status_code == 429
        assert "Rate limit exceeded" in rejected.json()["detail"]
        assert "retry-after" in rejected.headers
