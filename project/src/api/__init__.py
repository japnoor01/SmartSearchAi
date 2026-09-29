"""
src/api/__init__.py

FastAPI backend service for SmartSearch AI.
"""

from src.api.app import app, create_app
from src.api.config import APIConfig
from src.api.service import SearchService

__all__ = ["app", "create_app", "APIConfig", "SearchService"]
