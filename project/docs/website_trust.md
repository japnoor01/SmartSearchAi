# SmartSearch AI — Website Trust & Safety Check System

Comprehensive architecture and operations guide for the SmartSearch Website Trust & Safety Check module.

```
Browser Extension (active tab URL)
                ↓
    Nginx Reverse Proxy (:80)
                ↓
    FastAPI Router (:8000)
    GET /api/v1/site-check?url=<target_url>
                ↓
    Rate Limiter (InMemoryRateLimiter, Shared IP window)
                ↓
    WebsiteTrustService (Bounded TTL Cache)
         ├── URL Analyzer (privacy normalizer, domain extractor, IDN/punycode, IP/local check)
         ├── Verified Partner Directory (registry in configs/verified_partners.yaml)
         ├── Reputation Client
         │    ├── Google Safe Browsing API v4 (lookup API, env: SMARTSEARCH_SAFE_BROWSING_API_KEY)
         │    ├── VirusTotal API v3 (domain reports, env: SMARTSEARCH_VIRUSTOTAL_API_KEY)
         │    └── Local Known Threat Blocklist (test datasets & heuristics)
         └── Multi-Signal Evaluator
                ↓
    Explicit Assessment State:
    [low_risk | caution | high_risk | known_threat | insufficient_information]
                ↓
    Structured JSON Response
```

---

## 1. Core Engineering Principles

1. **Explicit Assessment States**: Avoid simplistic "legit vs. fake" or "safe vs. dangerous" binary categorizations. Sites are evaluated into five auditable states.
2. **"No Known Threat Detected" ≠ "Legitimate"**: The absence of negative intelligence in threat databases does not prove that an unknown or new site is trustworthy.
3. **HTTPS Does Not Guarantee Authenticity**: HTTPS only guarantees transport layer encryption between the client and server. Phishing and scam sites frequently deploy valid free TLS certificates (e.g., Let's Encrypt).
4. **"Not Verified" ≠ "Fraudulent"**: The vast majority of legitimate websites on the internet will not be listed in any partner registry. "Not verified" is purely informational.
5. **No Fabricated Intelligence**: If external reputation providers (Google Safe Browsing, VirusTotal) are unconfigured or fail to respond, the system explicitly reports them in `unavailable_checks` and downgrades unverified domains to `insufficient_information`.
6. **Privacy First**: Sensitive URL components (query strings, authentication credentials, URL fragments) are stripped immediately before analysis or caching. No private browsing data or form inputs are ever inspected.

---

## 2. Explicit Assessment States

Every inspected URL is categorized into one of five states:

| Assessment State | Definition & Determination Criteria | Extension UI Badge |
|---|---|---|
| `known_threat` | Confirmed match in Google Safe Browsing, VirusTotal positive detections, or local known threat blocklist (malware, phishing, social engineering). | 🛑 **Known threat** |
| `high_risk` | Objective high-risk structural indicators: public IP address literal used instead of domain, embedded credentials in URL, severe homograph/punycode spoofing. | ⚠️ **High risk** |
| `caution` | Measurable security deficits: unencrypted plain HTTP transport, private/loopback network targets, excessive subdomain nesting, or suspicious brand token in non-partner subdomain. | ⚠️ **Use caution** |
| `low_risk` | Explicitly verified partner with HTTPS, OR domain with positive external reputation confirmation and zero risk signals. | ✓ **Low risk** |
| `insufficient_information` | Valid HTTPS domain with no known threat hits, but domain is unverified and external reputation sources are unavailable or lack domain history. | ℹ️ **Insufficient info** |

---

## 3. Trust Signals Evaluated

The service synthesizes the following signals:

1. **Transport Encryption (HTTPS)**:
   - Evaluates whether connection uses TLS (`https://`) or unencrypted plaintext (`http://`).
   - Flag: `security_checks.https`.
2. **Domain & Host Structure**:
   - Extraction of apex domain and subdomain using two-part TLD awareness (e.g., `.co.uk`, `.com.au`, `.org.in`).
   - Flag: `security_checks.subdomain_depth`, `security_checks.apex_domain`.
3. **IP Address Literals**:
   - Detects IPv4 and IPv6 literals used as hostnames (e.g., `http://93.184.216.34:8080`). Public IP hosts trigger `high_risk`.
4. **Local / Private Network Targets**:
   - Flags loopback (`127.0.0.1`, `::1`), private ranges (`10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12`), and local domain suffixes (`.local`, `.localhost`, `.internal`, `.lan`). Triggers `caution`.
5. **Punycode & IDN Homograph Attacks**:
   - Analyzes internationalized domain names (IDN) with Cyrillic or mixed-script characters spoofing Latin brands (e.g., `xn--...`).
6. **URL Credentials**:
   - Detects embedded userinfo (e.g., `https://user:pass@legit-site.com`).
7. **Verified Partner Match**:
   - Direct verification against curated partner directory in [`configs/verified_partners.yaml`](file:///c:/Users/JAPNOOR/Downloads/smartsearch_lstm_stage2/project/configs/verified_partners.yaml).
8. **Threat Intelligence Feeds**:
   - Google Safe Browsing API v4 & VirusTotal v3 detection checks.

---

## 4. Verified Partner Directory

The partner directory is managed declaratively in [`configs/verified_partners.yaml`](file:///c:/Users/JAPNOOR/Downloads/smartsearch_lstm_stage2/project/configs/verified_partners.yaml).

### Configuration Schema
```yaml
partners:
  - domain: "wikipedia.org"
    organization: "Wikimedia Foundation"
    category: "Non-Profit Knowledge Repository"
    verified_since: "2024-01-01"
    subdomains_included: true
    notes: "Official Wikimedia Foundation domains"
```

### Partner Matching Rules
- If `subdomains_included: true`, requests for `en.wikipedia.org` or `upload.wikimedia.org` match the partner entry.
- If a partner domain is verified:
  - `verified_partner = true`
  - `partner_details = { organization, category, verified_since, domain }`
  - When combined with HTTPS and zero threat detections, assessment is `low_risk`.
- If a domain is NOT present:
  - `verified_partner = false`
  - `partner_details = null`
  - "Not verified" does **not** imply fraud, illegitimacy, or risk.

---

## 5. External Security & Reputation Providers

The service supports modular integrations with major external threat intelligence providers.

### Supported Providers
1. **Google Safe Browsing v4 (Lookup API)**:
   - Endpoint: `https://safebrowsing.googleapis.com/v4/threatMatches:find`
   - Threats monitored: `MALWARE`, `SOCIAL_ENGINEERING`, `UNWANTED_SOFTWARE`, `POTENTIALLY_HARMFUL_APPLICATION`.
2. **VirusTotal API v3**:
   - Endpoint: `https://www.virustotal.com/api/v3/domains/{domain}`
   - Evaluates engine detection statistics (`malicious`, `suspicious`).
3. **Local Test Blocklist**:
   - Built-in test malicious domains (e.g., `testsafebrowsing.appspot.com`, `malware.testing.example.org`) for test automation and mock verification.

### Environment Variable Configuration
API keys are loaded exclusively from environment variables or `.env` file (never hard-coded):

```bash
# Google Safe Browsing v4 API Key
SMARTSEARCH_SAFE_BROWSING_API_KEY=AIzaSy...

# VirusTotal v3 API Key
SMARTSEARCH_VIRUSTOTAL_API_KEY=a1b2c3...

# Optional: Master switch for website trust service
SMARTSEARCH_TRUST_ENABLED=true

# Cache TTL for domain check results (seconds)
SMARTSEARCH_TRUST_CACHE_TTL_SECONDS=3600
```

### Unconfigured Provider Handling
If API keys are omitted or empty:
- The system continues operating smoothly using structural heuristics, local blocklists, and verified partner registry.
- Missing providers are reported in `unavailable_checks`:
  ```json
  "unavailable_checks": ["google_safe_browsing", "virustotal"]
  ```
- Unverified domains without external data evaluate to `insufficient_information`. No fake positive or negative ratings are ever produced.

---

## 6. Privacy & Data Handling

1. **Path and Query Stripping**:
   - All URL paths, queries, and fragments are stripped before analysis:
     - Input: `https://mybank.com/account/summary?token=secret123&user=456#pane`
     - Evaluated & Logged: `https://mybank.com/`
2. **No User Content Inspection**:
   - The browser extension only queries the hostname and scheme of the active browser tab when opened.
   - Form fields, passwords, cookies, authorization tokens, and DOM contents are never read or transmitted.
3. **Production Logs**:
   - Server logs never output query parameters or user identifiers. Only normalized apex/host domains are referenced at `DEBUG` log level.

---

## 7. Caching & Performance

1. **In-Memory TTL Cache**:
   - Every evaluated domain is cached in memory with a default TTL of 3600 seconds (1 hour).
   - Subsequent checks for identical domains return within `< 0.2ms` with `"cached": true`.
   - Thread-safe, bounded eviction policy prevents memory bloat.
2. **Rate Limiting**:
   - Reuses the shared production rate limiter (`InMemoryRateLimiter`).
   - Protects against brute-force URL scanning or abuse.
3. **Browser Extension Performance**:
   - The extension does **not** run site checks on keystroke or typing.
   - Checks execute on popup display using the active tab URL.

---

## 8. REST API Reference

### `GET /api/v1/site-check`

Inspects a website domain and returns a structured safety assessment.

#### Request Parameters
| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `url` | string | Yes | — | Complete or bare URL/domain to evaluate (max 2048 chars). |

#### Response Schema (`200 OK`)
```json
{
  "url": "https://github.com/",
  "domain": "github.com",
  "assessment": "low_risk",
  "verified_partner": true,
  "partner_details": {
    "organization": "GitHub / Microsoft",
    "category": "Software Development & Hosting",
    "verified_since": "2024-01-15",
    "domain": "github.com"
  },
  "known_threat": false,
  "threat_type": null,
  "security_checks": {
    "https": true,
    "domain": "github.com",
    "apex_domain": "github.com",
    "is_ip_literal": false,
    "is_private_or_local": false,
    "is_punycode_or_idn": false,
    "has_userinfo": false,
    "port": null,
    "subdomain_depth": 0,
    "verified_partner": true,
    "threat_detected": false
  },
  "unavailable_checks": [
    "google_safe_browsing",
    "virustotal"
  ],
  "signals": [
    "Verified Partner: GitHub / Microsoft (Software Development & Hosting).",
    "Secure HTTPS connection verified.",
    "No threat indicators detected."
  ],
  "checked_at": "2026-09-29T10:20:44+00:00",
  "cached": true,
  "latency_ms": 0.11
}
```

#### Error Codes
- `400 Bad Request`: Empty or whitespace URL provided, or URL exceeds 2048 characters.
- `422 Unprocessable Entity`: Missing `url` query parameter.
- `429 Too Many Requests`: Client exceeded rate limit window.
- `504 Gateway Timeout`: Safety check timed out.

---

## 9. Browser Extension Behavior

1. **Active Tab Inspection**:
   - The extension requests the minimum permission required: `"activeTab"`.
   - On popup open, `chrome.tabs.query({ active: true, currentWindow: true })` retrieves the URL of the tab the user is viewing.
2. **Internal Page Handling**:
   - For `chrome://`, `edge://`, `about:`, or extension pages, the UI displays `Internal Page` with an informational notice: *"Browser internal page — website safety checks do not apply."*
3. **Visual Distinction of Trust**:
   - **Verified Partner**: Highlighted in green with store icon (`🏪 Verified Partner (Organization)`).
   - **No Known Threat**: Clear checkmark (`✓ No known threat detected`).
   - **Not Verified**: Neutral informational notice (`ℹ️ Not verified (does not mean fraudulent)`).
   - **Insufficient Information**: Displays standard caution disclaimer rather than false certification.
