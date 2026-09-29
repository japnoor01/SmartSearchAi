"""
src/api/app.py

FastAPI application factory and server entry point for SmartSearch AI.
Manages the application lifecycle (lifespan), CORS middleware, standardized error
handling, request timing, structured logging, and routing.
"""

from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Ensure repository root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.api.config import APIConfig
from src.api.rate_limiter import InMemoryRateLimiter
from src.api.routes import router
from src.api.schemas import ErrorResponse
from src.api.service import SearchService
from src.trust.service import WebsiteTrustService

LOG = logging.getLogger("smartsearch_api")


def create_app(
    config: APIConfig | None = None,
    search_service: SearchService | None = None,
    trust_service: WebsiteTrustService | None = None,
) -> FastAPI:
    """Application factory for the SmartSearch FastAPI service.

    Allows injecting a pre-configured SearchService and WebsiteTrustService for
    testing (e.g. with mock in-memory indexes) or instantiating production resources via lifespan.
    """
    cfg = config or APIConfig.load()

    # Configure root logging
    logging.basicConfig(
        level=getattr(logging, cfg.logging_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.config = cfg

        # 1. Initialize Website Trust & Safety Service
        if getattr(app.state, "trust_service", None) is not None:
            LOG.info("Using pre-configured WebsiteTrustService.")
        elif trust_service is not None:
            app.state.trust_service = trust_service
        elif cfg.trust_enabled:
            LOG.info("Initializing WebsiteTrustService...")
            app.state.trust_service = WebsiteTrustService(
                partners_path=cfg.trust_partners_path,
                safe_browsing_api_key=cfg.safe_browsing_api_key,
                virustotal_api_key=cfg.virustotal_api_key,
                cache_ttl_seconds=cfg.trust_cache_ttl_seconds,
                cache_max_items=cfg.trust_cache_max_items,
            )

        # 2. Initialize Search Service (Model & Index)
        if getattr(app.state, "search_service", None) is not None:
            # Reusing injected service (e.g. test fixture)
            LOG.info("Using pre-configured SearchService.")

        else:
            # Production startup: verify required artifacts exist before loading
            LOG.info("Starting SmartSearch AI backend service (env=%s)...", cfg.env)
            if not cfg.checkpoint_path.exists():
                error_msg = f"Model checkpoint not found: {cfg.checkpoint_path}"
                LOG.error(error_msg)
                raise FileNotFoundError(error_msg)

            if not cfg.tokenizer_dir.exists():
                error_msg = f"Tokenizer directory not found: {cfg.tokenizer_dir}"
                LOG.error(error_msg)
                raise FileNotFoundError(error_msg)

            if not cfg.index_path.exists():
                error_msg = f"Prefix index database not found: {cfg.index_path}"
                LOG.error(error_msg)
                raise FileNotFoundError(error_msg)

            LOG.info("Loading model and prefix index into memory...")
            t0 = time.perf_counter()
            app.state.search_service = SearchService(config=cfg)
            elapsed = time.perf_counter() - t0
            LOG.info("SmartSearch service ready in %.2f seconds.", elapsed)

        yield

        # Shutdown: close resources cleanly if instantiated by this app instance
        LOG.info("Shutting down SmartSearch service...")
        if search_service is None and hasattr(app.state, "search_service") and app.state.search_service:
            app.state.search_service.close()
        LOG.info("SmartSearch service stopped cleanly.")

    app = FastAPI(
        title="SmartSearch AI API",
        description=(
            "Production search auto-suggestion and query recommendation service "
            "powered by SQLite prefix retrieval and LSTM neural re-ranking."
        ),
        version="1.0.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.state.config = cfg
    app.state.rate_limiter = InMemoryRateLimiter(
        max_requests=cfg.rate_limit_requests,
        window_seconds=cfg.rate_limit_window_seconds,
    )
    if search_service is not None:
        app.state.search_service = search_service
    if trust_service is not None:
        app.state.trust_service = trust_service

    # -----------------------------------------------------------------------
    # CORS Configuration (dev vs prod separated)
    # -----------------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_origin_regex=cfg.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    # -----------------------------------------------------------------------
    # Request Timing and Structured Logging Middleware
    # -----------------------------------------------------------------------
    @app.middleware("http")
    async def request_logging_and_timing_middleware(request: Request, call_next):
        start_time = time.perf_counter()
        response = await call_next(request)
        process_time_ms = (time.perf_counter() - start_time) * 1000.0
        response.headers["X-Process-Time-Ms"] = f"{process_time_ms:.2f}"

        # Structured request logging without exposing sensitive query parameters
        client_ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "-")
        )
        LOG.info(
            "%s %s -> %d (%.2fms) [client=%s]",
            request.method,
            request.url.path,
            response.status_code,
            process_time_ms,
            client_ip,
        )
        return response

    # -----------------------------------------------------------------------
    # Standardized Error Handlers (No internal paths or stack traces exposed)
    # -----------------------------------------------------------------------
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        error_msg = errors[0]["msg"] if errors else "Invalid request parameters."
        loc = " -> ".join(str(l) for l in errors[0]["loc"]) if errors else "query"
        detail = f"{error_msg} (field: {loc})"

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error="Unprocessable Entity",
                detail=detail,
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            ).model_dump(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        error_name = "Error"
        if exc.status_code == status.HTTP_400_BAD_REQUEST:
            error_name = "Bad Request"
        elif exc.status_code == status.HTTP_404_NOT_FOUND:
            error_name = "Not Found"
        elif exc.status_code == status.HTTP_405_METHOD_NOT_ALLOWED:
            error_name = "Method Not Allowed"
        elif exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            error_name = "Too Many Requests"
        elif exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
            error_name = "Service Unavailable"
        elif exc.status_code == status.HTTP_504_GATEWAY_TIMEOUT:
            error_name = "Gateway Timeout"

        return JSONResponse(
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
            content=ErrorResponse(
                error=error_name,
                detail=str(exc.detail),
                status_code=exc.status_code,
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        LOG.exception("Unhandled server exception: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error="Internal Server Error",
                detail="An unexpected server error occurred while processing the request.",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            ).model_dump(),
        )

    # Mount endpoints
    app.include_router(router)

    return app


# Default singleton app for ASGI servers (e.g. uvicorn src.api.app:app)
app = create_app()


def main() -> None:
    """CLI launcher for local development or production execution."""
    import uvicorn

    cfg = APIConfig.load()
    mode = "PRODUCTION" if cfg.is_production else "DEVELOPMENT"
    print(f"Starting SmartSearch AI server [{mode}] on http://{cfg.host}:{cfg.port}")
    if cfg.is_production:
        print("  * Auto-reload: DISABLED (Production safe)")
        print(f"  * Rate limiting: {cfg.rate_limit_requests} req / {cfg.rate_limit_window_seconds}s")
        print("  * Query logging: DISABLED")

    uvicorn.run(
        "src.api.app:app",
        host=cfg.host,
        port=cfg.port,
        reload=cfg.reload and not cfg.is_production,
    )


if __name__ == "__main__":
    main()
