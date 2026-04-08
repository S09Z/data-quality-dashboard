# Data Quality Dashboard

A lightweight FastAPI service that validates CSV / Parquet files with **Polars + Pandera**, surfaces column statistics, and lets you search past validation reports via BM25 or semantic search.

---

## Features

- **Validate** — upload a file or point to a server-side path; get row-level error details back as JSON
- **Summarise** — column min / max / mean / null-count + total revenue aggregation
- **Search** — full-text BM25 or SentenceTransformers semantic search over validation history
- **History** — bounded ring of the last 50 validation runs
- **Hardened** — nginx reverse proxy with TLS, rate limiting, and security headers

---

## Quick start

```bash
# 1. Clone & install (requires Python 3.12+ and uv)
git clone https://github.com/S09Z/data-quality-dashboard.git
cd data-quality-dashboard
uv sync --frozen --all-extras

# 2. Copy env config
cp .env.example .env          # edit as needed

# 3. Run
uv run uvicorn src.api:app --reload
```

API docs → <http://localhost:8000/docs>

---

## Docker

```bash
# Copy certs for local TLS (self-signed)
bash scripts/gen_certs.sh

# Build & start app + nginx
docker compose up --build -d

# Smoke-test
curl -k https://localhost/health
```

The app is never exposed directly — all traffic goes through **nginx on :443** (HTTP :80 redirects to HTTPS).

---

## API endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Liveness check |
| `POST` | `/validate` | Validate a file (`upload` form field or `file_path` query param) |
| `GET` | `/summary` | Column statistics for the last validated file |
| `GET` | `/search?q=…` | Search validation history |
| `GET` | `/history` | Last N validation results |

---

## Project layout

```text
src/
├── api.py          # FastAPI app & endpoints
├── pipeline.py     # DataPipeline class (load → validate → summarise)
├── schemas.py      # Pydantic models + HistoryManager
├── search.py       # SearchEngine with BM25 / semantic strategy
├── config.py       # pydantic-settings (reads .env)
└── logging_config.py
tests/              # pytest suite (92 tests, ≥ 80 % coverage)
nginx/nginx.conf    # Hardened reverse proxy config
Dockerfile          # 2-stage build (uv builder → non-root runtime)
docker-compose.yml  # app + nginx, internal backend network
.env.example        # All config knobs documented
```

---

## Development

```bash
# Lint & format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Tests with coverage
uv run pytest --cov=src --cov-report=term-missing

# Pre-commit hooks
pre-commit install
```

CI runs automatically on every push / PR via **GitHub Actions** (lint → test → Docker build).

---

## Configuration

All settings are environment variables (see `.env.example`):

| Variable | Default | Description |
| --- | --- | --- |
| `PORT` | `8000` | Uvicorn bind port |
| `DATA_PATH` | `data/sales.csv` | Default file validated on startup |
| `USE_SEMANTIC_SEARCH` | `false` | Switch BM25 → SentenceTransformers |
| `LOG_JSON` | `false` | Emit structured JSON logs |
| `HISTORY_MAX_SIZE` | `50` | Validation history ring size |
| `DB_*` | — | PostgreSQL connection (future persistence layer) |

---

## Security

See [`SECURITY_REPORT.md`](SECURITY_REPORT.md) for the full white-box pentest findings and remediation roadmap.
