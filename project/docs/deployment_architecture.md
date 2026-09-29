# SmartSearch AI: Containerization & Deployment Architecture

This document describes the containerization and production deployment architecture for SmartSearch AI, with a particular focus on container design, handling the 3.48 GB SQLite prefix index, Nginx reverse proxy configuration, and deployment prerequisites.

---

## 1. Local Architecture

In local development, the stack runs directly on the developer host:

```
Chrome/Chromium Browser (SmartSearch Extension)
                     ↓  HTTP (fetch)
            FastAPI Application (:8000)
                     ↓
┌────────────────────┴────────────────────┐
│                                         │
▼                                         ▼
Real SQLite Prefix Index          PyTorch LSTM Ranker
(3.48 GB on local disk)          (best_model.pt + tokenizer)
```

- **Runtime**: Python 3.11+ / PyTorch CPU.
- **Data storage**: `project/data/indexes/prefix_real/prefix_index.sqlite3` accessed directly via Python's standard `sqlite3` driver.
- **Model weights**: Loaded once into memory at application lifespan startup.

---

## 2. Docker Architecture

In containerized deployment, the system is separated into a reverse-proxy tier and an application tier:

```
Internet / Extension Client
           │
           ▼  (Port 80 / 443)
┌──────────────────────────────────────┐
│          smartsearch-nginx           │
│  - Reverse proxy & load balancer     │
│  - Gzip JSON response compression    │
│  - Connection pooling (keepalive)    │
│  - Client body & timeout limits      │
└──────────────────┬───────────────────┘
                   │
                   ▼  (Internal Network :8000)
┌──────────────────────────────────────┐
│          smartsearch-api             │
│  - Python 3.11-slim runtime          │
│  - PyTorch CPU inference engine      │
│  - Sliding-window rate limiter       │
│  - Packaged LSTM model (~13.8 MB)    │
│  - Packaged Tokenizer (~443 KB)      │
└──────────────────┬───────────────────┘
                   │
                   ▼  (Read-Only Bind Mount :ro)
┌──────────────────────────────────────┐
│      Host File System Storage        │
│   prefix_index.sqlite3 (3.48 GB)     │
└──────────────────────────────────────┘
```

---

## 3. Large SQLite Storage Strategy (3.48 GB)

### The Challenge
The real prefix candidate index is **3.48 GB** (`3,480,076,288 bytes`). Copying (`COPY`) this database into the Docker image would:
1. Bloat image size to over 4.5 GB.
2. Severely slow down build, push, and pull times in CI/CD pipelines.
3. Waste storage when deploying multi-container replicas on the same node.

### Strategy Evaluation
- **Option A (Docker Bind Mount / Persistent Volume - CHOSEN FOR CONTAINER STACK)**:
  - Mount host path `./data/indexes/prefix_real:/app/data/indexes/prefix_real:ro`.
  - The container image stays lean (~1.0 GB with PyTorch CPU).
  - The index is mounted read-only (`:ro`), completely eliminating risk of database corruption.
  - OS page cache is shared across reads.
- **Option B (Cloud Persistent Disk / Block Storage)**:
  - In cloud environments (AWS ECS/EKS with EBS, GCP Cloud Run with Cloud Storage volume or GKE with Persistent Disk), attach a dedicated read-only volume containing `prefix_index.sqlite3`.
- **Option C (Initialization Sidecar / S3 Bootstrap)**:
  - An init-container checks if the volume contains `prefix_index.sqlite3`; if missing, it downloads it once from an object storage bucket (S3/GCS) into the persistent volume before the API container boots.

---

## 4. Model and Tokenizer Packaging

Unlike the SQLite index, the neural ranking artifacts are compact:
- **PyTorch LSTM weights (`best_model.pt`)**: **13.76 MB**
- **HuggingFace-compatible Tokenizer (`tokenizer.json`)**: **442.88 KB**
- **Total ML artifacts**: **~14.2 MB**

Because 14.2 MB is less than 1% of the database size, these artifacts are **baked directly into the container image** via `Dockerfile`. This ensures strict immutability between the application ranking code and the model weights.

---

## 5. Nginx Reverse Proxy Role

Nginx acts as the front-facing gateway container (`smartsearch-nginx`):
1. **Request Buffering & Protection**: Restricts payload size (`client_max_body_size 1m`) and enforces proxy connection timeouts (`proxy_connect_timeout 5s; proxy_read_timeout 10s`).
2. **Upstream Keepalive**: Maintains persistent keepalive connections to `smartsearch-api:8000`, reducing per-request TCP handshakes.
3. **Response Compression**: Compresses JSON autocomplete responses with `gzip`, reducing bandwidth for extension users.
4. **Header Forwarding**: Preserves client IP addresses through `X-Forwarded-For` and `X-Real-IP` so FastAPI's sliding-window rate limiter functions accurately.

---

## 6. HTTPS and TLS Requirements

For browser extension security in production:
- Chrome extensions targeting third-party domains strongly require HTTPS.
- **HTTP is used for local Docker verification only.**
- **Production requires HTTPS**:
  - TLS termination occurs at the Nginx layer.
  - Certificates (e.g. Let's Encrypt / Certbot) are mounted to `/etc/nginx/certs/fullchain.pem` and `/etc/nginx/certs/privkey.pem`.
  - A production Nginx configuration template is provided in `project/nginx/conf.d/default.conf`.
  - HTTP traffic (Port 80) must redirect with `301 Moved Permanently` to Port 443.

---

## 7. Environment Variables Reference

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `SMARTSEARCH_ENV` | `production` | Mode (`development`, `production`, `testing`). Disables reload in production. |
| `SMARTSEARCH_HOST` | `0.0.0.0` | Host IP interface to bind inside container. |
| `SMARTSEARCH_PORT` | `8000` | Port to bind inside container. |
| `SMARTSEARCH_INDEX_PATH` | `/app/data/indexes/prefix_real/prefix_index.sqlite3` | Path to SQLite prefix index. |
| `SMARTSEARCH_MODEL_PATH` | `models/lstm/best_model.pt` | Path to PyTorch LSTM model checkpoint. |
| `SMARTSEARCH_TOKENIZER_PATH` | `models/lstm/tokenizer` | Directory containing `tokenizer.json`. |
| `SMARTSEARCH_DEVICE` | `cpu` | Device (`cpu`, `cuda`, or `auto`). |
| `SMARTSEARCH_CORS_ORIGINS` | Explicit domain list | Comma-separated list of allowed origins. |
| `SMARTSEARCH_RATE_LIMIT_ENABLED` | `true` | Enables sliding-window rate limiting. |
| `SMARTSEARCH_RATE_LIMIT_REQUESTS` | `120` | Maximum requests per sliding window. |
| `SMARTSEARCH_RATE_LIMIT_WINDOW_SECONDS` | `60.0` | Duration of rate limiting window in seconds. |
| `SMARTSEARCH_REQUEST_TIMEOUT_SECONDS` | `5.0` | Maximum execution time per query before 504. |
| `SMARTSEARCH_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `SMARTSEARCH_LOG_QUERIES` | `false` | When false, avoids logging user query text for privacy. |

---

## 8. Local Docker Commands

```bash
# 1. Validate Docker Compose configuration
docker compose config

# 2. Build the production container image
docker compose build

# 3. Start the stack (FastAPI backend + Nginx reverse proxy)
docker compose up -d

# 4. Check container health status
docker compose ps

# 5. Tail container logs
docker compose logs -f smartsearch-api

# 6. Verify health and readiness through Nginx
curl -i http://localhost/health
curl -i http://localhost/readiness

# 7. Execute test query
curl -i "http://localhost/api/v1/suggest?q=deep%20lea&limit=5"

# 8. Stop the stack
docker compose down
```

---

## 9. Future Cloud Deployment Requirements

Before deploying to cloud container infrastructure (AWS ECS, GCP Cloud Run, Kubernetes):
1. **TLS / Domain Registration**: Point a DNS A/CNAME record (e.g. `api.smartsearch.domain.com`) to the load balancer / Nginx instance.
2. **SSL Certificate Provisioning**: Obtain a valid SSL/TLS certificate via AWS ACM, Google-managed certificates, or Let's Encrypt.
3. **Volume Provisioning**: Attach a persistent disk containing `prefix_index.sqlite3` (or run the bootstrap script to download it from an S3/GCS bucket on cluster init).
4. **CORS Allowlist**: Set `SMARTSEARCH_CORS_ORIGINS` to include the production frontend domain and Chrome extension IDs.

---

## 10. Resource Requirements

- **Docker Image Size**: ~1.0 GB (Python 3.11-slim + PyTorch CPU + FastAPI dependencies + ML weights).
- **SQLite Index Size**: 3.24 GiB (3,480,076,288 bytes on host filesystem).
- **LSTM Checkpoint Size**: 13.76 MB (14,431,029 bytes).
- **Tokenizer Size**: 442.88 KB (453,511 bytes).
- **Memory Footprint**: ~105 MB to 150 MB RAM per API worker at startup/idle.
- **Compute Recommendation**: Minimum 1 vCPU and 1 GB RAM per worker instance for CPU inference.

---

## 11. Known Limitations

1. **Host-Dependent SQLite Volume**: Container scaling across multiple physical nodes requires an attached cloud volume or distributed key-value store (e.g. Redis/RocksDB).
2. **Single SQLite Process Writing**: The SQLite index is mounted read-only (`:ro`). Live index updating requires rebuilding the index file or deploying an index generation pipeline.
3. **Docker Host WSL Requirement**: On Windows developer machines without WSL2 or Hyper-V active, the Docker Desktop daemon cannot run Linux containers.
