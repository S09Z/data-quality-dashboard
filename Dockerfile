# =============================================================================
# Stage 1 — builder
# Install all Python dependencies using uv into an isolated prefix.
# This stage is never shipped; only the installed packages are copied out.
# =============================================================================
FROM python:3.12-slim AS builder

# Install uv (fast pip replacement) via the official installer
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Copy dependency manifests first so Docker can cache the install layer
COPY pyproject.toml uv.lock ./

# Sync production dependencies only (no dev/test extras) into /build/.venv
RUN uv sync --frozen --no-dev --no-editable


# =============================================================================
# Stage 2 — runtime
# Minimal image: no build tools, no test deps, non-root user.
# =============================================================================
FROM python:3.12-slim AS runtime

# ── Security: run as non-root ────────────────────────────────────────────────
RUN groupadd --gid 1001 appgroup \
 && useradd  --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# ── Copy venv from builder ───────────────────────────────────────────────────
COPY --from=builder /build/.venv /app/.venv

# Put the venv on PATH so `python` resolves to the venv interpreter
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # pydantic-settings: all config via env vars inside the container
    HOST=0.0.0.0 \
    PORT=8000 \
    RELOAD=false \
    LOG_JSON=true \
    LOG_LEVEL=INFO

# ── Copy application source ──────────────────────────────────────────────────
COPY src/     ./src/
COPY main.py  ./
COPY data/    ./data/

# ── Ownership ────────────────────────────────────────────────────────────────
RUN chown -R appuser:appgroup /app

USER appuser

# ── Expose internal port (nginx proxies to this) ────────────────────────────
EXPOSE 8000

# ── Health check (Docker / compose can poll this) ───────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# ── Entrypoint ───────────────────────────────────────────────────────────────
CMD ["python", "-m", "uvicorn", "src.api:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--no-access-log"]
