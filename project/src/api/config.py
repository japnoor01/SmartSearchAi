"""
src/api/config.py

Configuration manager for the SmartSearch AI FastAPI backend.
Loads settings from configs/api.yaml with full environment variable override support.
Never uses hardcoded absolute Windows paths.
Separates development and production configuration cleanly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class APIConfig:
    """Production and Development configuration for the SmartSearch FastAPI service."""

    # Environment
    env: str = "development"  # development, production, testing

    # Server settings
    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False
    request_timeout_seconds: float = 5.0

    # Model & tokenizer artifact paths (resolved relative to PROJECT_ROOT)
    checkpoint_path: Path = PROJECT_ROOT / "models" / "lstm" / "best_model.pt"
    tokenizer_dir: Path = PROJECT_ROOT / "models" / "lstm" / "tokenizer"
    device: str = "auto"

    # Prefix retrieval settings
    index_path: Path = PROJECT_ROOT / "data" / "indexes" / "prefix_real" / "prefix_index.sqlite3"
    candidate_pool_size: int = 50

    # Query & ranking limits
    default_top_k: int = 5
    max_top_k: int = 50
    min_top_k: int = 1
    max_query_length: int = 200

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120
    rate_limit_window_seconds: float = 60.0

    # CORS settings
    cors_origins: list[str] = field(
        default_factory=lambda: [
            "http://localhost",
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:8000",
            "http://127.0.0.1",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:8000",
        ]
    )
    cors_origin_regex: str | None = r"^(chrome-extension://[a-zA-Z0-9]+|http://(localhost|127\.0\.0\.1)(:\d+)?|null)$"

    # Logging & Privacy
    logging_level: str = "INFO"
    log_queries: bool = False  # Privacy: whether to log raw queries in server logs

    # Website Trust & Safety settings
    trust_enabled: bool = True
    trust_cache_ttl_seconds: float = 300.0
    trust_cache_max_items: int = 1000
    trust_partners_path: Path = PROJECT_ROOT / "configs" / "verified_partners.yaml"
    safe_browsing_api_key: str = ""
    virustotal_api_key: str = ""

    @property
    def is_production(self) -> bool:
        return self.env.lower() in ("production", "prod")

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> APIConfig:
        """Load APIConfig from YAML file and apply environment variable overrides."""
        cfg_dict: dict[str, Any] = {}
        target_path = Path(config_path) if config_path else PROJECT_ROOT / "configs" / "api.yaml"

        if target_path.exists():
            with open(target_path, "r", encoding="utf-8") as f:
                cfg_dict = yaml.safe_load(f) or {}

        # 0. Environment mode
        env = os.environ.get("SMARTSEARCH_ENV", cfg_dict.get("environment", "development")).lower()

        server_cfg = cfg_dict.get("server", {})
        model_cfg = cfg_dict.get("model", {})
        retrieval_cfg = cfg_dict.get("retrieval", {})
        limits_cfg = cfg_dict.get("limits", {})
        rate_limit_cfg = cfg_dict.get("rate_limit", {})
        cors_cfg = cfg_dict.get("cors", {})
        log_cfg = cfg_dict.get("logging", {})

        def resolve_path(p: str | Path) -> Path:
            path_obj = Path(p)
            return path_obj if path_obj.is_absolute() else PROJECT_ROOT / path_obj

        # 1. Server settings
        host = os.environ.get("SMARTSEARCH_HOST", server_cfg.get("host", "127.0.0.1"))
        port = int(os.environ.get("SMARTSEARCH_PORT", server_cfg.get("port", 8000)))

        # Production strictly disallows reload
        if env in ("production", "prod"):
            reload = False
        else:
            if "SMARTSEARCH_RELOAD" in os.environ:
                reload = os.environ.get("SMARTSEARCH_RELOAD", "").lower() in ("1", "true", "yes")
            else:
                reload = bool(server_cfg.get("reload", False))

        request_timeout_seconds = float(
            os.environ.get(
                "SMARTSEARCH_REQUEST_TIMEOUT_SECONDS",
                server_cfg.get("request_timeout_seconds", 5.0),
            )
        )

        # 2. Model & tokenizer
        ckpt_raw = os.environ.get(
            "SMARTSEARCH_MODEL_PATH",
            model_cfg.get("checkpoint_path", "models/lstm/best_model.pt"),
        )
        tok_raw = os.environ.get(
            "SMARTSEARCH_TOKENIZER_PATH",
            model_cfg.get("tokenizer_dir", "models/lstm/tokenizer"),
        )
        device = os.environ.get("SMARTSEARCH_DEVICE", model_cfg.get("device", "auto"))

        # 3. Retrieval
        idx_raw = os.environ.get(
            "SMARTSEARCH_INDEX_PATH",
            retrieval_cfg.get("index_path", "data/indexes/prefix_real/prefix_index.sqlite3"),
        )
        candidate_pool_size = int(
            os.environ.get(
                "SMARTSEARCH_CANDIDATE_POOL_SIZE",
                retrieval_cfg.get("candidate_pool_size", 50),
            )
        )

        # 4. Limits
        default_top_k = int(os.environ.get("SMARTSEARCH_TOP_K", limits_cfg.get("default_top_k", 5)))
        max_top_k = int(limits_cfg.get("max_top_k", 50))
        min_top_k = int(limits_cfg.get("min_top_k", 1))
        max_query_length = int(
            os.environ.get(
                "SMARTSEARCH_MAX_QUERY_LENGTH",
                limits_cfg.get("max_query_length", 200),
            )
        )

        # 5. Rate limiting
        rate_limit_enabled = os.environ.get(
            "SMARTSEARCH_RATE_LIMIT_ENABLED",
            str(rate_limit_cfg.get("enabled", True)),
        ).lower() in ("1", "true", "yes")
        rate_limit_requests = int(
            os.environ.get(
                "SMARTSEARCH_RATE_LIMIT_REQUESTS",
                rate_limit_cfg.get("max_requests", 120),
            )
        )
        rate_limit_window_seconds = float(
            os.environ.get(
                "SMARTSEARCH_RATE_LIMIT_WINDOW_SECONDS",
                rate_limit_cfg.get("window_seconds", 60.0),
            )
        )

        # 6. CORS (dev vs prod separation)
        cors_env = os.environ.get("SMARTSEARCH_CORS_ORIGINS")
        cors_regex_env = os.environ.get("SMARTSEARCH_CORS_ORIGIN_REGEX")

        if env in ("production", "prod"):
            # Production: strict allowlist only (no extension or null wildcards unless explicitly set)
            if cors_env:
                cors_origins = [o.strip() for o in cors_env.split(",") if o.strip()]
            else:
                cors_origins = cors_cfg.get("production_origins", [])
            cors_origin_regex = (
                cors_regex_env if cors_regex_env is not None else cors_cfg.get("production_origin_regex", None)
            )
        else:
            # Development / Testing: allow local browser, extension, and null file origins
            if cors_env:
                cors_origins = [o.strip() for o in cors_env.split(",") if o.strip()]
            else:
                cors_origins = cors_cfg.get(
                    "development_origins",
                    cors_cfg.get(
                        "allowed_origins",
                        [
                            "http://localhost",
                            "http://localhost:3000",
                            "http://localhost:5173",
                            "http://localhost:8000",
                            "http://127.0.0.1",
                            "http://127.0.0.1:3000",
                            "http://127.0.0.1:5173",
                            "http://127.0.0.1:8000",
                        ],
                    ),
                )
            default_dev_regex = (
                r"^(chrome-extension://[a-zA-Z0-9]+|http://(localhost|127\.0\.0\.1)(:\d+)?|null)$"
            )
            cors_origin_regex = (
                cors_regex_env
                if cors_regex_env is not None
                else cors_cfg.get("development_origin_regex", cors_cfg.get("origin_regex", default_dev_regex))
            )

        # 7. Logging & Privacy
        logging_level = os.environ.get("SMARTSEARCH_LOG_LEVEL", log_cfg.get("level", "INFO"))
        log_queries = os.environ.get(
            "SMARTSEARCH_LOG_QUERIES",
            str(log_cfg.get("log_queries", False)),
        ).lower() in ("1", "true", "yes")

        # 8. Website Trust & Safety settings
        trust_cfg = cfg_dict.get("trust", {})
        trust_enabled = os.environ.get(
            "SMARTSEARCH_TRUST_ENABLED",
            str(trust_cfg.get("enabled", True)),
        ).lower() in ("1", "true", "yes")
        trust_cache_ttl_seconds = float(
            os.environ.get(
                "SMARTSEARCH_TRUST_CACHE_TTL",
                trust_cfg.get("cache_ttl_seconds", 300.0),
            )
        )
        trust_cache_max_items = int(
            os.environ.get(
                "SMARTSEARCH_TRUST_CACHE_MAX_ITEMS",
                trust_cfg.get("cache_max_items", 1000),
            )
        )
        trust_partners_raw = os.environ.get(
            "SMARTSEARCH_PARTNERS_PATH",
            trust_cfg.get("partners_path", "configs/verified_partners.yaml"),
        )
        safe_browsing_api_key = os.environ.get(
            "SMARTSEARCH_SAFE_BROWSING_API_KEY",
            trust_cfg.get("safe_browsing_api_key", ""),
        ).strip()
        virustotal_api_key = os.environ.get(
            "SMARTSEARCH_VIRUSTOTAL_API_KEY",
            trust_cfg.get("virustotal_api_key", ""),
        ).strip()

        return cls(
            env=env,
            host=host,
            port=port,
            reload=reload,
            request_timeout_seconds=request_timeout_seconds,
            checkpoint_path=resolve_path(ckpt_raw),
            tokenizer_dir=resolve_path(tok_raw),
            device=device,
            index_path=resolve_path(idx_raw),
            candidate_pool_size=candidate_pool_size,
            default_top_k=default_top_k,
            max_top_k=max_top_k,
            min_top_k=min_top_k,
            max_query_length=max_query_length,
            rate_limit_enabled=rate_limit_enabled,
            rate_limit_requests=rate_limit_requests,
            rate_limit_window_seconds=rate_limit_window_seconds,
            cors_origins=cors_origins,
            cors_origin_regex=cors_origin_regex,
            logging_level=logging_level,
            log_queries=log_queries,
            trust_enabled=trust_enabled,
            trust_cache_ttl_seconds=trust_cache_ttl_seconds,
            trust_cache_max_items=trust_cache_max_items,
            trust_partners_path=resolve_path(trust_partners_raw),
            safe_browsing_api_key=safe_browsing_api_key,
            virustotal_api_key=virustotal_api_key,
        )

