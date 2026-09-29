"""
src/trust

Website trust, reputation, and security evaluation service for SmartSearch AI.
Provides multi-signal domain safety evaluation, verified partner matching,
and external threat intelligence integration (Google Safe Browsing, VirusTotal).
"""

from src.trust.models import (
    DomainAnalysis,
    PartnerDetails,
    SecuritySignals,
    TrustAssessment,
    TrustCheckResult,
)
from src.trust.service import WebsiteTrustService

__all__ = [
    "DomainAnalysis",
    "PartnerDetails",
    "SecuritySignals",
    "TrustAssessment",
    "TrustCheckResult",
    "WebsiteTrustService",
]
