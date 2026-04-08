# CLAUDE.md

> Context file for AI coding assistants (Claude, Copilot, Cursor, etc.).
> Read this before writing any code. Keep it updated as the project evolves.

---

## 1. Project Overview

**Data Quality Dashboard** is a FastAPI microservice for validating CSV / Parquet
data files and surfacing quality issues as JSON.

Primary users: data engineers and analytics teams who need automated, auditable
validation of ingested files before downstream processing.

Optimize for:

- Correctness — validation results must be deterministic and trustworthy
- Clarity — error messages must tell the caller exactly what row/column/check failed
- Security — all file input is untrusted; validate type and size before reading bytes
- Observability — structured JSON logs, health check, bounded history ring

Avoid over-engineering. Prefer clarity over cleverness.
Do not add abstractions for single-use cases.

---

## 2. Tech Stack

- **Python 3.12** (required — uses modern type syntax)
- **FastAPI** ≥ 0.100 with async route handlers
- **Polars** ≥ 1.0 — all dataframe operations (do not mix with pandas)
- **Pandera** ≥ 0.21 (polars backend) — schema declaration and validation
- **Pydantic v2** + **pydantic-settings** — response models and `.env` config
- **Haystack AI** ≥ 2.0 — BM25 / semantic search over validation reports
- **Uvicorn** — ASGI server (run via `main.py` or `uv run uvicorn src.api:app`)
- **python-json-logger** — structured JSON log output
- **uv** — package manager (never use `pip install` directly)
- **Ruff** — linter and formatter (replaces black + isort + flake8)
- **pytest** + **pytest-asyncio** + **httpx** — test runner and async HTTP client
- **nginx** 1.27-alpine — TLS termination and reverse proxy (Docker only)
- **Docker** + **docker compose** v2 — containerisation

Do not introduce:

- `pandas` or `numpy` — use Polars exclusively
- `SQLAlchemy` / `asyncpg` without a matching `DB_*` env var implementation
- `Redux`, `Celery`, or any task queue — out of scope for this service
- Any CSS / frontend framework — this is a pure API service

---

## 3. Architecture

```
nginx (:443 / :80)
  └─► FastAPI app (:8000)          ← internal Docker network, never public
        ├── lifespan startup        DataPipeline.run() on DEFAULT_DATA_PATH
        ├── POST /validate          DataPipeline.run() → ValidationResult
        ├── GET  /summary           last SummaryResponse from app.state
        ├── GET  /search?q=…        SearchEngine.query()
        └── GET  /history           HistoryManager.latest()
```

**Design patterns — do not remove or replace these without discussion:**

- **Template Method** — `DataPipeline._load → _validate → _summarize`
- **Strategy** — `SearchEngine` delegates to `_BM25Strategy` or `_SemanticStrategy`
- **Factory classmethod** — `ValidationResult.from_pandera()`
- **Bounded ring** — `HistoryManager(max_size=N)` for in-memory history
- **Recursive helper** — `DataPipeline._parse_errors(rows, acc=None)`
- **App-state singleton** — all mutable state lives in `app.state.*`; no module-level globals in `api.py`

---

## 4. Coding Conventions

- `from __future__ import annotations` at the top of every `src/` module
- Type-annotate all function signatures — avoid `Any` unless truly unavoidable
- Named exports only — no wildcard `import *`
- `async/await` for all route handlers; sync helpers stay sync
- Keep functions under 40 lines; extract helpers when logic grows
- Descriptive names — no abbreviations (`qty` → `quantity`, `df` is acceptable for Polars frames)
- No dead code, no commented-out blocks
- Add comments only when intent is non-obvious (the *why*, not the *what*)
- Raise `HTTPException` with a descriptive `detail` string — never let raw exceptions reach the client
- Use `model_copy(update={...})` for modified Pydantic models — treat them as immutable
- Use `Path` objects, not raw strings, for all filesystem paths

**Imports order (enforced by Ruff `I` rule):**

1. `__future__`
2. stdlib
3. third-party
4. local `src.*`

---

## 5. UI & Design System

This project has **no frontend** — it is a pure JSON API.

API response design rules:

- All responses conform to the Pydantic models in `src/schemas.py`
- Error responses use FastAPI's default `{"detail": "..."}` shape — do not change this
- Field names use `snake_case` throughout
- Monetary values (e.g. `total_revenue`) use `float` rounded to 2 decimal places
- Timestamps use ISO-8601 strings — never raw `datetime` objects in JSON
- Do not add undocumented fields to response models without updating the schema

---

## 6. Content & Copy Guidance

- Error `detail` strings: tell the caller **what to do**, not just what went wrong
  - ✅ `"Uploaded file type '.sh' is not supported. Use .csv or .parquet."`
  - ❌ `"Invalid file type"`
- Log messages: structured key=value style when `LOG_JSON=false`; full JSON when `true`
- Validation error messages mirror Pandera check names exactly — do not paraphrase
- `.env.example` comments: one sentence per variable explaining its effect and valid values
- `CLAUDE.md` must stay accurate — update it whenever architecture, patterns, or tooling change

---

## 7. Testing & Quality Bar

Before marking any task complete:

- run lint: `uv run ruff check src/ tests/ main.py`
- run format check: `uv run ruff format --check src/ tests/ main.py`
- run tests: `uv run pytest --cov=src --cov-report=term-missing`
- coverage must not drop below **80 %**

Rules:

- Unit tests required for: pipeline logic, schema validation, history ring, search engine, all API routes
- Group related tests in `class Test*` inside `tests/test_pipeline.py`
- HTTP client for route tests: `httpx.AsyncClient` with `httpx.ASGITransport(app=app)`
- Patch at the **import location**, not the definition location
  - ✅ `patch("src.api.DataPipeline.run")`
  - ❌ `patch("src.pipeline.DataPipeline.run")`
- Use `tmp_path` or `monkeypatch` — never touch the real filesystem in unit tests
- Inject `HistoryManager` state via `manager._items = [...]` directly in test setup
- Security probes live in `tests/pentest_whitebox.py` — run against a live server, not the test client

---

## 8. File Placement Rules

```
src/api.py              Route handlers and lifespan only — no business logic
src/pipeline.py         DataPipeline class + compatibility shim functions
src/schemas.py          Pydantic response models, Pandera schema, HistoryManager
src/search.py           SearchEngine + Strategy classes + build_report_from_validation
src/config.py           Settings singleton — add new env vars here first
src/logging_config.py   configure_logging() helper only

tests/test_pipeline.py      All pytest tests
tests/pentest_whitebox.py   Security scanner (run manually against live server)

nginx/nginx.conf        Reverse proxy config — rate limits, TLS, headers
Dockerfile              Multi-stage build — edit only to change base image or deps
docker-compose.yml      Service orchestration — app + nginx
.env.example            Canonical list of all supported env vars with comments
scripts/gen_certs.sh    One-off cert generation for local TLS
```

Rules:

- New business logic → `src/pipeline.py` or a new `src/<feature>.py` module
- New API route → `src/api.py` only
- New config knob → add to `Settings` in `src/config.py` **and** document in `.env.example`
- Do not create a new module for single-function use — add it to the closest existing module
- Module filename must match the primary class or concept it exports

---

## 9. Safe-Change Rules

- Do not rename or remove any existing API route (`/validate`, `/summary`, `/search`, `/history`, `/health`)
- Do not change the suffix-check order in `validate_file` — the check **must** run before `await upload.read()`
- Do not modify `nginx/nginx.conf` security headers without noting it in the PR description
- Do not modify the Pandera `SalesSchema` columns without updating all affected tests
- Do not change `HistoryManager.push` / `latest` signatures — used in both `api.py` and tests
- Do not change `DataPipeline.run()` return type — it must remain `tuple[ValidationResult, SummaryResponse]`
- Flag major architectural changes (new pattern, new dependency, new service) **before** implementing — describe the change and wait for approval

---

## 10. Commands

```bash
# Dependencies
uv sync --frozen --all-extras             # install prod + dev deps
uv add <package>                          # add a new dependency
uv add --dev <package>                    # add a dev-only dependency

# Development
uv run uvicorn src.api:app --reload       # dev server on localhost:8000
uv run python main.py                     # prod-style server (no reload)

# Lint & format
uv run ruff check src/ tests/ main.py           # lint (check only)
uv run ruff check src/ tests/ main.py --fix     # lint + auto-fix
uv run ruff format src/ tests/ main.py          # format

# Tests
uv run pytest --cov=src --cov-report=term-missing   # with coverage
uv run pytest -x                                     # stop on first failure
uv run pytest -k "TestDataPipeline"                  # run one class

# Security scan (requires live server)
uv run uvicorn src.api:app --port 8000 &
uv run python tests/pentest_whitebox.py
pkill -f "uvicorn src.api:app"

# Docker
bash scripts/gen_certs.sh                         # generate self-signed cert (first time only)
docker compose up --build -d                      # build + start app and nginx
curl -k https://localhost/health                  # smoke-test
docker compose logs -f app                        # tail app logs
docker compose exec nginx nginx -s reload         # reload nginx config (no restart)
docker compose down                               # stop and remove containers
```

---

## 11. Security Rules

- Never commit `.env`, `nginx/certs/*.key`, or `nginx/certs/*.crt` — all are git-ignored
- `.env.example` is the only env file allowed in version control — must contain placeholder values only, no real secrets
- Never hardcode secrets, API keys, or passwords in source code
- Never log sensitive data:
  - no logging of file contents or raw upload bytes
  - no logging of full request bodies
  - no logging of internal stack traces to the HTTP response
- File upload handling:
  - suffix check **must** run before `await upload.read()` — never buffer untrusted bytes first
  - accepted extensions: `.csv` and `.parquet` only — reject everything else with `HTTP 400`
  - `MAX_UPLOAD_BYTES` is the application-level size guard (defence-in-depth behind nginx `client_max_body_size`)
- Path traversal: never pass user-supplied strings directly to `Path()` without validation
- All user input validated via Pydantic models before any processing
- `app.state.*` is the only place for mutable per-app state — no module-level globals in `api.py`
- nginx security headers (`X-Content-Type-Options`, `X-Frame-Options`, `CSP`, `HSTS`) must remain enabled — do not remove or weaken them
- TLS minimum version: TLSv1.2 — do not lower this in `nginx.conf`
- Rate limiting zones (`global`, `validate`) must remain active — do not remove `limit_req` directives