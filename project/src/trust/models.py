"""
src/trust/models.py

Domain models and assessment enumerations for the website trust and safety system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TrustAssessment(str, Enum):
    """Explicit, non-simplistic website safety assessment states.

    Rules:
      - LOW_RISK: Verified partners or clean domains with valid HTTPS and no threat indicators.
      - CAUTION: Unencrypted HTTP, unusual ports, local/private addresses, or minor heuristic flags.
      - HIGH_RISK: Critical structural or identity anomalies (public IP literals, userinfo phishing, homographs).
      - KNOWN_THREAT: Active match in threat databases (Safe Browsing, VirusTotal, blocklists).
      - INSUFFICIENT_INFORMATION: Unverified entity without external reputation verification;
        neither confirmed safe nor confirmed malicious.
    """

    LOW_RISK = "low_risk"
    CAUTION = "caution"
    HIGH_RISK = "high_risk"
    KNOWN_THREAT = "known_threat"
    INSUFFICIENT_INFORMATION = "insufficient_information"


@dataclass(frozen=True)
class PartnerDetails:
    """Metadata for an explicitly verified partner domain."""

    organization: str
    category: str
    verified_since: Optional[str] = None
    domain: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "organization": self.organization,
            "category": self.category,
            "verified_since": self.verified_since,
            "domain": self.domain,
        }


@dataclass
class DomainAnalysis:
    """Structural decomposition of a target URL/domain."""

    raw_input: str
    normalized_url: str
    scheme: str
    hostname: str
    apex_domain: str
    subdomain: str
    subdomain_depth: int
    port: Optional[int]
    is_ip: bool
    is_private_or_local: bool
    is_punycode_or_idn: bool
    has_userinfo: bool
    suspicious_tokens: list[str] = field(default_factory=list)
    validation_error: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.validation_error is None and bool(self.hostname)


@dataclass
class SecuritySignals:
    """Consolidated findings across all evaluated security dimensions."""

    https_enabled: bool
    verified_partner: bool
    known_threat: bool
    threat_type: Optional[str] = None
    is_ip_literal: bool = False
    is_private_or_local: bool = False
    is_punycode: bool = False
    has_userinfo: bool = False
    suspicious_port: bool = False
    suspicious_subdomains: bool = False
    reputation_checked: bool = False
    reputation_clean: bool = False
    unavailable_checks: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass
class TrustCheckResult:
    """Final structured outcome of a website trust evaluation."""

    url: str
    domain: str
    assessment: TrustAssessment
    verified_partner: bool
    known_threat: bool
    security_checks: dict[str, Any]
    unavailable_checks: list[str]
    signals: list[str]
    checked_at: str
    partner_details: Optional[PartnerDetails] = None
    threat_type: Optional[str] = None
    cached: bool = False
    latency_ms: Optional[float] = None

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "domain": self.domain,
            "assessment": self.assessment.value,
            "verified_partner": self.verified_partner,
            "partner_details": self.partner_details.to_dict() if self.partner_details else None,
            "known_threat": self.known_threat,
            "threat_type": self.threat_type,
            "security_checks": self.security_checks,
            "unavailable_checks": self.unavailable_checks,
            "signals": self.signals,
            "checked_at": self.checked_at,
            "cached": self.cached,
            "latency_ms": self.latency_ms,
        }
