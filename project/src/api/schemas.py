"""
src/api/schemas.py

Pydantic schemas for the SmartSearch AI API request validation and response serialization.
Strict, stable schemas ensuring full compatibility with browser extensions and client apps.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class SuggestionItem(BaseModel):
    """A single ranked autocomplete search suggestion."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        ...,
        description="The recommended full query suggestion text.",
        examples=["deep learning tutorial"],
    )
    score: float = Field(
        ...,
        description="Raw ranking logit score from the trained LSTM model (higher is better).",
        examples=[-0.3671],
    )


class SuggestionsResponse(BaseModel):
    """Top-level response payload for autocomplete search endpoints."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        description="The user partial query prefix that was submitted.",
        examples=["deep learning"],
    )
    suggestions: list[SuggestionItem] = Field(
        default_factory=list,
        description="List of top-K suggestions ordered by descending ranking score.",
    )
    count: int = Field(
        ...,
        description="Number of suggestions returned in this response.",
        examples=[5],
    )
    latency_ms: Optional[float] = Field(
        default=None,
        description="End-to-end pipeline latency in milliseconds.",
        examples=[21.8],
    )


class HealthResponse(BaseModel):
    """Service liveness probe response."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    status: str = Field("ok", description="Overall liveness status.", examples=["ok"])
    model_loaded: bool = Field(True, description="Whether the neural LSTM ranker is loaded in memory.")
    index_connected: bool = Field(True, description="Whether the SQLite prefix index is connected.")
    device: str = Field("cpu", description="Compute device used for inference (cpu or cuda).")


class ReadinessResponse(BaseModel):
    """Service readiness probe response verifying all backing resources are available."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    status: str = Field("ready", description="Readiness status ('ready' or 'not_ready').", examples=["ready"])
    model_loaded: bool = Field(True, description="Whether the LSTM model weights are loaded and active.")
    index_connected: bool = Field(True, description="Whether the SQLite prefix index B-tree is connected.")
    device: str = Field("cpu", description="Compute device used for inference.")
    uptime_seconds: float = Field(..., description="Server process uptime in seconds.", examples=[142.5])


class MetricsResponse(BaseModel):
    """Operational monitoring metrics for the SmartSearch service."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    uptime_seconds: float = Field(..., description="Total elapsed process uptime in seconds.")
    total_requests: int = Field(..., description="Total incoming HTTP requests processed.")
    total_suggestions_served: int = Field(..., description="Total suggestion responses returned.")
    total_errors: int = Field(..., description="Total server-side 5xx exceptions encountered.")
    total_rate_limited: int = Field(..., description="Total requests rejected by rate limiting (429).")
    avg_latency_ms: float = Field(..., description="Average pipeline processing latency in milliseconds.")
    last_latency_ms: float = Field(..., description="Latency of the most recently processed suggestion query.")


class VersionResponse(BaseModel):
    """API and model versioning metadata."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    name: str = Field("SmartSearch AI API", description="Project service name.")
    version: str = Field("1.0.0", description="Semantic service release version.")
    api_version: str = Field("v1", description="API route specification version.")
    model_type: str = Field("LSTM Ranker", description="Underlying model architecture.")
    description: str = Field(
        "Production search auto-suggestion and query recommendation service powered by SQLite prefix retrieval and LSTM neural re-ranking.",
        description="Service description.",
    )


class ErrorResponse(BaseModel):
    """Standardized API error response (avoids leaking stack traces or internal paths)."""

    model_config = ConfigDict(extra="forbid")

    error: str = Field(..., description="Error category name.", examples=["Bad Request", "Too Many Requests"])
    detail: str = Field(..., description="Human-readable explanation of what went wrong.", examples=["Query length exceeds maximum allowed length of 200 characters."])
    status_code: int = Field(..., description="HTTP status code.", examples=[400])


class PartnerDetails(BaseModel):
    """Metadata for an explicitly verified partner domain."""

    model_config = ConfigDict(extra="forbid")

    organization: str = Field(..., description="Legal or operating name of the verified organization.")
    category: str = Field(..., description="Organization or website industry category.")
    verified_since: Optional[str] = Field(None, description="ISO date when partnership was verified.")
    domain: str = Field("", description="Canonical partner root domain.")


class SiteCheckResponse(BaseModel):
    """Structured website trust, safety, and reputation assessment payload."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., description="Normalized URL evaluated by the trust engine.", examples=["https://wikipedia.org"])
    domain: str = Field(..., description="Canonical hostname extracted from URL.", examples=["wikipedia.org"])
    assessment: str = Field(
        ...,
        description="Explicit assessment state: low_risk, caution, high_risk, known_threat, or insufficient_information.",
        examples=["low_risk"],
    )
    verified_partner: bool = Field(
        ...,
        description="Whether domain is in verified partner registry. 'false' does NOT mean fraudulent.",
    )
    partner_details: Optional[PartnerDetails] = Field(
        default=None,
        description="Metadata for verified partners (null if not verified).",
    )
    known_threat: bool = Field(
        ...,
        description="Whether active malware, phishing, or threat intelligence hits were detected.",
    )
    threat_type: Optional[str] = Field(
        default=None,
        description="Categorization of detected threat if known_threat is true.",
    )
    security_checks: dict[str, Any] = Field(
        default_factory=dict,
        description="Detailed breakdown of evaluated security checks.",
        examples=[{"https": True, "is_ip_literal": False, "subdomain_depth": 0}],
    )
    unavailable_checks: list[str] = Field(
        default_factory=list,
        description="Security providers or reputation queries that could not be evaluated.",
        examples=[["google_safe_browsing", "virustotal"]],
    )
    signals: list[str] = Field(
        default_factory=list,
        description="Human-readable rationales and findings supporting the assessment.",
        examples=[["HTTPS transport encryption enabled", "Verified Partner: Wikimedia Foundation"]],
    )
    checked_at: str = Field(
        ...,
        description="ISO 8601 UTC timestamp of safety check execution.",
        examples=["2026-09-29T10:00:00Z"],
    )
    cached: bool = Field(
        default=False,
        description="Whether response was served from in-memory TTL cache.",
    )
    latency_ms: Optional[float] = Field(
        default=None,
        description="Evaluation latency in milliseconds.",
        examples=[3.5],
    )

