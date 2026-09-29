"""
src/trust/evaluator.py

Multi-signal trust assessment evaluator for SmartSearch AI.
Translates domain structure, partner verification, and threat intelligence into explicit,
auditable assessment states without simplistic 'legit/fake' binary assumptions.
"""

from __future__ import annotations

from typing import Any, Optional

from src.trust.models import (
    DomainAnalysis,
    PartnerDetails,
    TrustAssessment,
)


def evaluate_trust(
    analysis: DomainAnalysis,
    partner: Optional[PartnerDetails],
    reputation: dict[str, Any],
) -> tuple[TrustAssessment, list[str], dict[str, Any]]:
    """Evaluates all collected signals and assigns an explicit TrustAssessment state.

    Returns:
      (assessment, signals, security_checks)
    """
    signals: list[str] = []
    checks: dict[str, Any] = {
        "https": analysis.scheme == "https",
        "domain": analysis.hostname,
        "apex_domain": analysis.apex_domain,
        "is_ip_literal": analysis.is_ip,
        "is_private_or_local": analysis.is_private_or_local,
        "is_punycode_or_idn": analysis.is_punycode_or_idn,
        "has_userinfo": analysis.has_userinfo,
        "port": analysis.port,
        "subdomain_depth": analysis.subdomain_depth,
        "verified_partner": partner is not None,
    }

    # 1. Validation error / Malformed URL
    if not analysis.is_valid:
        signals.append(f"Invalid URL structure: {analysis.validation_error}")
        return TrustAssessment.CAUTION, signals, checks

    # 2. Known Threat Intelligence Match (Priority 1)
    if reputation.get("known_threat"):
        threat_type = reputation.get("threat_type") or "MALICIOUS"
        signals.append(f"Known threat detected: {threat_type}")
        for provider in reputation.get("provider_hits", []):
            signals.append(f"Flagged by threat provider: {provider}")
        checks["threat_detected"] = True
        checks["threat_type"] = threat_type
        return TrustAssessment.KNOWN_THREAT, signals, checks

    checks["threat_detected"] = False

    # 3. Critical Structural Red Flags -> HIGH RISK (Priority 2)
    high_risk_reasons: list[str] = []

    if analysis.is_ip and not analysis.is_private_or_local:
        high_risk_reasons.append(
            "Public IP address literal used instead of a registered domain name (common malware/phishing host)."
        )

    if analysis.has_userinfo:
        high_risk_reasons.append(
            "Embedded credentials syntax (@) in URL authority (common phishing obfuscation)."
        )

    if analysis.is_punycode_or_idn and not partner:
        high_risk_reasons.append(
            "Internationalized / Punycode domain detected (possible homograph character impersonation)."
        )

    if analysis.suspicious_tokens and not partner:
        tokens_str = ", ".join(analysis.suspicious_tokens)
        high_risk_reasons.append(
            f"Suspicious brand or security keywords ({tokens_str}) found in unverified subdomain."
        )

    if analysis.subdomain_depth >= 4 and not partner:
        high_risk_reasons.append(
            f"Deeply nested subdomain depth ({analysis.subdomain_depth} levels) used for obfuscation."
        )

    if high_risk_reasons:
        signals.extend(high_risk_reasons)
        if analysis.scheme == "https":
            signals.append("HTTPS is present, but transport encryption does not negate structural risk.")
        return TrustAssessment.HIGH_RISK, signals, checks

    # 4. Caution Signals (Priority 3)
    caution_reasons: list[str] = []

    if analysis.scheme != "https":
        caution_reasons.append(
            "Insecure HTTP connection (traffic is unencrypted and vulnerable to interception)."
        )

    if analysis.is_private_or_local:
        caution_reasons.append(
            "Private or loopback address (local development/intranet environment)."
        )

    if analysis.port and analysis.port not in (80, 443) and not analysis.is_private_or_local:
        caution_reasons.append(f"Non-standard web port (:{analysis.port}) on public host.")

    # 5. Verified Partner Evaluation (Priority 4)
    if partner is not None:
        signals.append(
            f"Verified Partner: {partner.organization} ({partner.category})."
        )
        if analysis.scheme == "https" and not caution_reasons:
            signals.append("Secure HTTPS connection verified.")
            signals.append("No threat indicators detected.")
            return TrustAssessment.LOW_RISK, signals, checks
        else:
            # Partner accessed via HTTP or non-standard port
            signals.extend(caution_reasons)
            return TrustAssessment.CAUTION, signals, checks

    # 6. Non-Partner Domain Evaluation (Priority 5)
    # Essential rule: "Not verified" MUST NOT mean fraudulent!
    signals.append(
        "Domain is not in the verified partner directory (this does not imply fraud)."
    )

    if caution_reasons:
        signals.extend(caution_reasons)
        return TrustAssessment.CAUTION, signals, checks

    # HTTPS is present, no structural red flags, but not a verified partner
    signals.append("HTTPS transport encryption is enabled.")

    unavailable = reputation.get("unavailable_checks", [])
    has_active_external_rep = (
        "google_safe_browsing" not in unavailable or "virustotal" not in unavailable
    )

    if has_active_external_rep and not reputation.get("known_threat"):
        signals.append("Clean external threat intelligence reputation confirmed.")
        signals.append("Standard domain structure with no anomalous indicators.")
        return TrustAssessment.LOW_RISK, signals, checks

    # If external threat intelligence is unconfigured/unavailable:
    # We report clean local heuristics, but cannot claim verified low_risk!
    signals.append("No active threats detected by local heuristic analysis.")
    if unavailable:
        signals.append(f"External reputation providers not configured: {', '.join(unavailable)}.")
    signals.append(
        "Insufficient independent reputation data to confirm verified low-risk status."
    )
    return TrustAssessment.INSUFFICIENT_INFORMATION, signals, checks
