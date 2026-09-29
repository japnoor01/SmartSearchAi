# =============================================================================
# SmartSearch AI -- Production Dockerfile
# =============================================================================
# Multi-stage / optimized slim build for SmartSearch AI FastAPI backend.
# Uses PyTorch CPU wheel to keep image size reasonable (~1 GB instead of >5 GB).
# SQLite prefix index (3.48 GB) is MOUNTED AT RUNTIME via volume, NOT baked into image.

FROM python:3.11-slim AS runtime

# Python and environment settings
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SMARTSEARCH_ENV=production \
    SMARTSEARCH_HOST=0.0.0.0 \
    SMARTSEARCH_PORT=8000 \
    SMARTSEARCH_DEVICE=cpu

WORKDIR /app

# Install minimal OS dependencies for healthcheck probe
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch CPU wheel first to optimize Docker layer caching
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install application runtime dependencies
COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt

# Copy configuration, application code, and ML models
# Note: models/ contains best_model.pt (~13.8 MB) and tokenizer/ (~443 KB)
COPY configs/ ./configs/
COPY src/ ./src/
COPY models/ ./models/

# Create mount point directory for external SQLite index
RUN mkdir -p /app/data/indexes/prefix_real

# Expose internal service port
EXPOSE 8000

# Container healthcheck using deep /readiness endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/readiness || exit 1

# Production startup command (Auto-reload: OFF, Single worker process, Host: 0.0.0.0)
CMD ["python", "-m", "uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
