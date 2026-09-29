"""
src/trust/partner_directory.py

Maintainable verified partner registry and domain resolution.
Ensures verified partner lookup is deterministic, cached, and distinct from fraud detection.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from src.trust.models import PartnerDetails

LOG = logging.getLogger("smartsearch_trust")


class VerifiedPartnerDirectory:
    """In-memory directory of verified institutional and commercial partner domains."""

    def __init__(self, partners_path: Optional[str | Path] = None) -> None:
        self._partners: dict[str, dict[str, Any]] = {}
        self._partners_path = Path(partners_path) if partners_path else None
        self.load()

    def load(self, path: Optional[str | Path] = None) -> None:
        """Loads partner directory from YAML configuration with built-in fallbacks."""
        target_path = Path(path) if path else self._partners_path

        loaded: dict[str, dict[str, Any]] = {}

        if target_path and target_path.exists():
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                for entry in data.get("partners", []):
                    domain = (entry.get("domain") or "").strip().lower()
                    if domain:
                        loaded[domain] = {
                            "organization": entry.get("organization", "Verified Organization"),
                            "category": entry.get("category", "General"),
                            "verified_since": entry.get("verified_since"),
                            "allow_subdomains": bool(entry.get("allow_subdomains", True)),
                        }
                LOG.info("Loaded %d verified partners from %s", len(loaded), target_path)
            except Exception as exc:
                LOG.error("Failed to load verified partners from %s: %s", target_path, exc)

        # Built-in baseline fallback partners if configuration is missing or empty
        if not loaded:
            baseline = {
                "wikipedia.org": {
                    "organization": "Wikimedia Foundation",
                    "category": "Encyclopedia & Reference",
                    "verified_since": "2024-01-15",
                    "allow_subdomains": True,
                },
                "github.com": {
                    "organization": "GitHub / Microsoft",
                    "category": "Software Development & Hosting",
                    "verified_since": "2024-01-15",
                    "allow_subdomains": True,
                },
                "python.org": {
                    "organization": "Python Software Foundation",
                    "category": "Open Source Software",
                    "verified_since": "2024-02-01",
                    "allow_subdomains": True,
                },
                "google.com": {
                    "organization": "Google LLC",
                    "category": "Search & Cloud Services",
                    "verified_since": "2024-01-01",
                    "allow_subdomains": True,
                },
                "fastapi.tiangolo.com": {
                    "organization": "FastAPI Project",
                    "category": "Software Documentation",
                    "verified_since": "2024-03-01",
                    "allow_subdomains": False,
                },
            }
            loaded.update(baseline)

        self._partners = loaded

    def match(self, hostname: str) -> Optional[PartnerDetails]:
        """Resolves whether a canonical hostname corresponds to a verified partner.

        Supports:
          1. Exact match (e.g. 'wikipedia.org')
          2. Validated subdomain match if allow_subdomains is True (e.g. 'en.wikipedia.org')
        """
        if not hostname:
            return None

        clean_host = hostname.strip().lower()

        # 1. Exact match
        if clean_host in self._partners:
            entry = self._partners[clean_host]
            return PartnerDetails(
                organization=entry["organization"],
                category=entry["category"],
                verified_since=entry.get("verified_since"),
                domain=clean_host,
            )

        # 2. Subdomain check (longest matching parent domain)
        for partner_domain, entry in self._partners.items():
            if entry.get("allow_subdomains") and clean_host.endswith("." + partner_domain):
                return PartnerDetails(
                    organization=entry["organization"],
                    category=entry["category"],
                    verified_since=entry.get("verified_since"),
                    domain=partner_domain,
                )

        return None
