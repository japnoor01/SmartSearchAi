"""
src/trust/url_analyzer.py

Robust, privacy-conscious URL and domain parsing, normalization, and heuristic decomposition.
Never leaks query parameters or user authentication credentials into logs.
"""

from __future__ import annotations

import ipaddress
import re
import urllib.parse
from typing import Optional

from src.trust.models import DomainAnalysis

# Multi-part public suffixes commonly used in registrable domain calculations
_COMMON_TWO_PART_TLDS = frozenset([
    "co.uk", "org.uk", "gov.uk", "ac.uk",
    "com.au", "net.au", "org.au", "edu.au",
    "co.nz", "net.nz", "org.nz",
    "co.jp", "ne.jp", "or.jp",
    "com.br", "org.br",
    "co.in", "net.in", "org.in", "gen.in",
    "com.sg", "edu.sg",
])

# Sensitive brand tokens commonly abused in subdomain phishing
_SUSPICIOUS_PHISHING_TOKENS = frozenset([
    "paypal", "apple", "google", "microsoft", "amazon", "netflix",
    "login", "verify", "secure", "account", "banking", "update", "signin",
    "wallet", "crypto", "security", "support",
])

# Standard web ports that do not require caution flags
_STANDARD_PORTS = frozenset([80, 443])


def normalize_and_analyze_url(raw_url: str) -> DomainAnalysis:
    """Parses, normalizes, and inspects an input URL or domain string.

    Privacy Guarantee:
      - Strips all query parameters, fragments, and userinfo credentials.
      - Returns a sanitized canonical URL for logging and analysis.
    """
    cleaned = (raw_url or "").strip()
    if not cleaned:
        return DomainAnalysis(
            raw_input=raw_url,
            normalized_url="",
            scheme="",
            hostname="",
            apex_domain="",
            subdomain="",
            subdomain_depth=0,
            port=None,
            is_ip=False,
            is_private_or_local=False,
            is_punycode_or_idn=False,
            has_userinfo=False,
            validation_error="Empty or whitespace URL provided.",
        )

    # 1. Scheme normalization (prepend https:// if bare domain or host)
    has_explicit_scheme = "://" in cleaned
    if not has_explicit_scheme:
        # Check if user typed something like "localhost:8000" or "example.com/path"
        working_url = "https://" + cleaned
    else:
        working_url = cleaned

    try:
        parsed = urllib.parse.urlsplit(working_url)
    except Exception as exc:
        return DomainAnalysis(
            raw_input=raw_url,
            normalized_url="",
            scheme="",
            hostname="",
            apex_domain="",
            subdomain="",
            subdomain_depth=0,
            port=None,
            is_ip=False,
            is_private_or_local=False,
            is_punycode_or_idn=False,
            has_userinfo=False,
            validation_error=f"Malformed URL syntax: {exc}",
        )

    scheme = parsed.scheme.lower() if parsed.scheme else "https"
    if scheme not in ("http", "https"):
        return DomainAnalysis(
            raw_input=raw_url,
            normalized_url="",
            scheme=scheme,
            hostname="",
            apex_domain="",
            subdomain="",
            subdomain_depth=0,
            port=None,
            is_ip=False,
            is_private_or_local=False,
            is_punycode_or_idn=False,
            has_userinfo=False,
            validation_error=f"Unsupported scheme '{scheme}'. Only HTTP and HTTPS are permitted.",
        )

    # Check for embedded credentials (e.g. http://user:pass@bank.com)
    has_userinfo = bool(parsed.username or parsed.password)

    # Extract netloc and host
    netloc = parsed.netloc or ""
    # Strip userinfo if present
    if "@" in netloc:
        netloc = netloc.split("@")[-1]

    # Extract host and port
    host_raw = netloc
    port: Optional[int] = None
    if ":" in netloc:
        # IPv6 addresses use [::1]:port notation
        if netloc.startswith("["):
            bracket_close = netloc.find("]")
            if bracket_close != -1:
                host_raw = netloc[1:bracket_close]
                remainder = netloc[bracket_close + 1:]
                if remainder.startswith(":"):
                    try:
                        port = int(remainder[1:])
                    except ValueError:
                        pass
        else:
            parts = netloc.split(":")
            if len(parts) == 2:
                host_raw = parts[0]
                try:
                    port = int(parts[1])
                except ValueError:
                    pass

    # Clean host (strip trailing dot, lowercase)
    hostname = host_raw.rstrip(".").lower()
    if not hostname:
        return DomainAnalysis(
            raw_input=raw_url,
            normalized_url="",
            scheme=scheme,
            hostname="",
            apex_domain="",
            subdomain="",
            subdomain_depth=0,
            port=port,
            is_ip=False,
            is_private_or_local=False,
            is_punycode_or_idn=False,
            has_userinfo=has_userinfo,
            validation_error="Missing hostname in URL.",
        )

    # 2. IP Address & Local/Private Evaluation
    is_ip = False
    is_private_or_local = False

    # Check for localhost names
    if hostname in ("localhost", "ip6-localhost", "ip6-loopback") or hostname.endswith(
        (".local", ".localhost", ".internal", ".lan", ".test", ".example", ".invalid")
    ):
        is_private_or_local = True

    try:
        ip_obj = ipaddress.ip_address(hostname)
        is_ip = True
        is_private_or_local = (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_reserved
            or ip_obj.is_link_local
            or ip_obj.is_unspecified
        )
    except ValueError:
        is_ip = False

    # 3. Unicode / Punycode / IDN evaluation
    is_punycode_or_idn = False
    try:
        # Test if punycode encoding changes it or starts with xn--
        encoded_host = hostname.encode("idna").decode("ascii")
        if "xn--" in encoded_host or any(ord(c) > 127 for c in hostname):
            is_punycode_or_idn = True
            hostname = encoded_host  # Use standard IDNA ASCII for uniform matching
    except Exception:
        is_punycode_or_idn = any(ord(c) > 127 for c in hostname)

    # Ensure hostname is either an IP, a recognized local host, or has a valid domain dot
    if not is_ip and not is_private_or_local and "." not in hostname:
        return DomainAnalysis(
            raw_input=raw_url,
            normalized_url="",
            scheme=scheme,
            hostname=hostname,
            apex_domain=hostname,
            subdomain="",
            subdomain_depth=0,
            port=port,
            is_ip=False,
            is_private_or_local=False,
            is_punycode_or_idn=is_punycode_or_idn,
            has_userinfo=has_userinfo,
            validation_error=f"Invalid domain '{hostname}': Hostname must contain a top-level domain, an IP address, or localhost.",
        )

    # 4. Apex domain and subdomain calculation
    apex_domain = hostname
    subdomain = ""
    subdomain_depth = 0

    if not is_ip:
        labels = hostname.split(".")
        if len(labels) >= 2:
            two_part = ".".join(labels[-2:])
            three_part = ".".join(labels[-3:]) if len(labels) >= 3 else ""

            if two_part in _COMMON_TWO_PART_TLDS and len(labels) >= 3:
                apex_domain = three_part
                subdomain_labels = labels[:-3]
            else:
                apex_domain = two_part
                subdomain_labels = labels[:-2]

            subdomain = ".".join(subdomain_labels)
            subdomain_depth = len(subdomain_labels)

    # 5. Suspicious Token Heuristics (e.g. brand in subdomain)
    suspicious_tokens: list[str] = []
    if subdomain:
        subdomain_lower = subdomain.lower()
        for token in _SUSPICIOUS_PHISHING_TOKENS:
            if token in subdomain_lower and token not in apex_domain:
                suspicious_tokens.append(token)

    # 6. Construct Privacy-Normalized Canonical URL
    # Strips query params, fragments, and credentials
    path = parsed.path or "/"
    port_part = f":{port}" if port and port not in (80, 443) else ""
    normalized_url = f"{scheme}://{hostname}{port_part}{path}"

    return DomainAnalysis(
        raw_input=raw_url,
        normalized_url=normalized_url,
        scheme=scheme,
        hostname=hostname,
        apex_domain=apex_domain,
        subdomain=subdomain,
        subdomain_depth=subdomain_depth,
        port=port,
        is_ip=is_ip,
        is_private_or_local=is_private_or_local,
        is_punycode_or_idn=is_punycode_or_idn,
        has_userinfo=has_userinfo,
        suspicious_tokens=suspicious_tokens,
        validation_error=None,
    )
