"""
src/api/routes.py

FastAPI route definitions for SmartSearch AI.
Defines endpoints:
  - GET /health (Liveness probe)
  - GET /readiness (Readiness probe)
  - GET /metrics (Operational metrics)
  - GET /api/v1/version (Metadata & architecture)
  - GET /api/v1/suggestions (Core autocomplete endpoint)
  - GET /api/v1/suggest (Alias autocomplete endpoint with 'limit' support)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.api.config import APIConfig
from src.api.rate_limiter import InMemoryRateLimiter
from src.api.schemas import (
    ErrorResponse,
    HealthResponse,
    MetricsResponse,
    ReadinessResponse,
    SiteCheckResponse,
    SuggestionItem,
    SuggestionsResponse,
    VersionResponse,
)
from src.api.service import SearchService
from src.trust.service import WebsiteTrustService

LOG = logging.getLogger("smartsearch_api")

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependency Helpers
# ---------------------------------------------------------------------------


def get_search_service(request: Request) -> SearchService:
    """Dependency injection helper returning the shared SearchService instance."""
    service = getattr(request.app.state, "search_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SmartSearch suggestion service is not initialized or currently unavailable.",
        )
    return service


def get_api_config(request: Request) -> APIConfig:
    """Dependency injection helper returning the active APIConfig."""
    return getattr(request.app.state, "config", APIConfig.load())


def get_rate_limiter(request: Request) -> InMemoryRateLimiter | None:
    """Dependency injection helper returning the in-memory rate limiter."""
    return getattr(request.app.state, "rate_limiter", None)


def get_client_ip(request: Request) -> str:
    """Extract client IP, taking X-Forwarded-For into account if behind a reverse proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def get_trust_service(request: Request) -> WebsiteTrustService:
    """Dependency injection helper returning the active WebsiteTrustService."""
    service = getattr(request.app.state, "trust_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Website trust service is not initialized or currently unavailable.",
        )
    return service


def check_rate_limit(
    request: Request,
    config: Annotated[APIConfig, Depends(get_api_config)],
    limiter: Annotated[InMemoryRateLimiter | None, Depends(get_rate_limiter)],
) -> None:
    """Dependency enforcing lightweight sliding-window rate limiting across endpoints."""
    if not config.rate_limit_enabled or limiter is None:
        return

    client_ip = get_client_ip(request)
    allowed, retry_after = limiter.check_rate_limit(client_ip)
    if not allowed:
        search_svc = getattr(request.app.state, "search_service", None)
        if search_svc and hasattr(search_svc, "record_rate_limited"):
            search_svc.record_rate_limited()
        LOG.warning("Rate limit exceeded for client %s (retry_after=%ds)", client_ip, retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Try again in {retry_after} second(s).",
            headers={"Retry-After": str(retry_after)},
        )



# ---------------------------------------------------------------------------
# Health, Readiness & Metrics Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Check the basic operational health and component availability of the SmartSearch service.",
    tags=["Monitoring"],
    responses={
        200: {"model": HealthResponse, "description": "Service process is alive."},
        503: {"model": ErrorResponse, "description": "Service components unavailable."},
    },
)
def health_check(
    service: Annotated[SearchService, Depends(get_search_service)],
) -> HealthResponse:
    info = service.health()
    return HealthResponse(**info)


@router.get(
    "/readiness",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description="Verify that the LSTM ranker model weights and SQLite prefix index are fully loaded and ready to serve traffic.",
    tags=["Monitoring"],
    responses={
        200: {"model": ReadinessResponse, "description": "Service is ready to accept inference requests."},
        503: {"model": ErrorResponse, "description": "Service not ready (model or index not connected)."},
    },
)
def readiness_check(
    service: Annotated[SearchService, Depends(get_search_service)],
) -> ReadinessResponse:
    readiness_info = service.readiness()
    if readiness_info["status"] != "ready":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SmartSearch service is not ready to serve traffic.",
        )
    return ReadinessResponse(**readiness_info)


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    summary="Operational metrics",
    description="Retrieve operational performance counters including request counts, latency metrics, and error rates.",
    tags=["Monitoring"],
    responses={
        200: {"model": MetricsResponse, "description": "Service operational metrics."},
    },
)
def get_metrics(
    service: Annotated[SearchService, Depends(get_search_service)],
) -> MetricsResponse:
    return MetricsResponse(**service.get_metrics())


@router.get(
    "/api/v1/version",
    response_model=VersionResponse,
    summary="API and model version",
    description="Retrieve versioning information, model architecture details, and service metadata.",
    tags=["Metadata"],
    responses={
        200: {"model": VersionResponse, "description": "Version metadata."},
    },
)
def get_version() -> VersionResponse:
    return VersionResponse()


# ---------------------------------------------------------------------------
# Suggestions Autocomplete Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/suggestions",
    response_model=SuggestionsResponse,
    summary="Get search suggestions",
    description=(
        "Retrieve ranked autocomplete suggestions for a given partial search query. "
        "Uses SQLite prefix retrieval followed by LSTM neural candidate re-ranking. "
        "Protected by request timeout, input validation, and sliding-window rate limiting."
    ),
    tags=["Autocomplete"],
    responses={
        200: {"model": SuggestionsResponse, "description": "Top-K suggestions retrieved successfully."},
        400: {"model": ErrorResponse, "description": "Invalid input query or top_k parameter."},
        422: {"model": ErrorResponse, "description": "Validation error for missing or malformed parameters."},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded."},
        503: {"model": ErrorResponse, "description": "Underlying ranker model or prefix index unavailable."},
        504: {"model": ErrorResponse, "description": "Inference request timed out."},
    },
)
async def get_suggestions(
    q: Annotated[str, Query(description="User partial search query prefix to autocomplete.", examples=["deep learning"])],
    top_k: Annotated[int, Query(description="Number of suggestions to return (1 to 50).", examples=[5])] = 5,
    service: SearchService = Depends(get_search_service),
    config: APIConfig = Depends(get_api_config),
    _rate_limit: None = Depends(check_rate_limit),
) -> SuggestionsResponse:
    if hasattr(service, "record_request"):
        service.record_request()

    # 1. Validate top_k limits
    if top_k < config.min_top_k:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Parameter 'top_k' must be at least {config.min_top_k}.",
        )
    if top_k > config.max_top_k:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Parameter 'top_k' cannot exceed {config.max_top_k}.",
        )

    # 2. Validate query length
    if len(q) > config.max_query_length:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Query length exceeds the maximum allowed length of {config.max_query_length} characters.",
        )

    # 3. Handle empty / whitespace queries gracefully without error (standard autocomplete UX)
    if not q or not q.strip():
        return SuggestionsResponse(query=q, suggestions=[], count=0, latency_ms=0.0)

    # 4. Privacy-safe query logging
    if config.log_queries:
        LOG.info("Processing suggestion query: %r (top_k=%d)", q, top_k)
    else:
        LOG.debug("Processing suggestion query of length %d (top_k=%d)", len(q), top_k)

    # 5. Execute retrieval and LSTM ranking with timeout protection
    try:
        raw_suggestions, elapsed_ms = await asyncio.wait_for(
            asyncio.to_thread(service.get_suggestions, query=q, top_k=top_k),
            timeout=config.request_timeout_seconds,
        )
    except asyncio.TimeoutError as err:
        LOG.error("Request timed out after %.1fs for query length %d", config.request_timeout_seconds, len(q))
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Suggestion request timed out after {config.request_timeout_seconds} seconds.",
        ) from err
    except HTTPException:
        raise
    except Exception as exc:
        LOG.exception("Unexpected error in ranking pipeline: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while generating suggestions.",
        ) from exc

    # 6. Format response items
    items = [
        SuggestionItem(
            text=item.get("suggestion", item.get("candidate", "")),
            score=item["score"],
        )
        for item in raw_suggestions
    ]

    return SuggestionsResponse(
        query=q,
        suggestions=items,
        count=len(items),
        latency_ms=elapsed_ms,
    )


@router.get(
    "/api/v1/suggest",
    response_model=SuggestionsResponse,
    summary="Get search suggestions (alias)",
    description=(
        "Alias for /api/v1/suggestions supporting both 'limit' and 'top_k' parameters. "
        "Retrieves ranked autocomplete suggestions for a given query prefix."
    ),
    tags=["Autocomplete"],
    responses={
        200: {"model": SuggestionsResponse, "description": "Top-K suggestions retrieved successfully."},
        400: {"model": ErrorResponse, "description": "Invalid input query or limit parameter."},
        422: {"model": ErrorResponse, "description": "Validation error for missing or malformed parameters."},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded."},
        503: {"model": ErrorResponse, "description": "Underlying ranker model or prefix index unavailable."},
        504: {"model": ErrorResponse, "description": "Inference request timed out."},
    },
)
async def get_suggest_alias(
    q: Annotated[str, Query(description="User partial search query prefix to autocomplete.", examples=["machine learning"])],
    limit: Annotated[int | None, Query(description="Number of suggestions to return (1 to 50). Alias for top_k.", examples=[5])] = None,
    top_k: Annotated[int | None, Query(description="Number of suggestions to return (1 to 50).", examples=[5])] = None,
    service: SearchService = Depends(get_search_service),
    config: APIConfig = Depends(get_api_config),
    _rate_limit: None = Depends(check_rate_limit),
) -> SuggestionsResponse:
    effective_k = limit if limit is not None else (top_k if top_k is not None else config.default_top_k)
    return await get_suggestions(q=q, top_k=effective_k, service=service, config=config)


# ---------------------------------------------------------------------------
# Website Trust & Safety Check Endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/site-check",
    response_model=SiteCheckResponse,
    summary="Website trust and safety assessment",
    description=(
        "Evaluates website trust, safety, and reputation signals for a given URL or domain. "
        "Inspects HTTPS/TLS, domain structure, verified partner status, and external threat intelligence. "
        "Strictly privacy-safe: never logs sensitive query parameters or user authentication credentials."
    ),
    tags=["Safety"],
    responses={
        200: {"model": SiteCheckResponse, "description": "Website safety assessment completed successfully."},
        400: {"model": ErrorResponse, "description": "Invalid, empty, or malformed URL parameter."},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded."},
        503: {"model": ErrorResponse, "description": "Trust evaluation service unavailable."},
        504: {"model": ErrorResponse, "description": "Safety evaluation request timed out."},
    },
)
async def check_site(
    url: Annotated[str, Query(description="Target website URL or domain to evaluate.", examples=["https://wikipedia.org"])],
    request: Request,
    trust_service: Annotated[WebsiteTrustService, Depends(get_trust_service)],
    config: Annotated[APIConfig, Depends(get_api_config)],
    _rate_limit: None = Depends(check_rate_limit),
) -> SiteCheckResponse:
    cleaned = (url or "").strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameter 'url' must not be empty.",
        )

    if len(cleaned) > 2048:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL exceeds maximum allowed length of 2048 characters.",
        )

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(trust_service.check_url, cleaned),
            timeout=config.request_timeout_seconds,
        )
    except asyncio.TimeoutError as err:
        LOG.error("Website safety check timed out after %.1fs", config.request_timeout_seconds)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Safety check timed out after {config.request_timeout_seconds} seconds.",
        ) from err
    except HTTPException:
        raise
    except Exception as exc:
        LOG.error("Unexpected error during website trust check: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during website safety evaluation.",
        ) from exc

    return SiteCheckResponse(**result.to_api_dict())

