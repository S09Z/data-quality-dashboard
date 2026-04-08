# =============================================================================
# Makefile — Data Quality Dashboard
# =============================================================================
# Usage:
#   make            → show this help
#   make dev        → start local dev server (hot-reload, no Docker)
#   make up         → build & start full stack (app + nginx) in Docker
#   make down       → stop and remove containers
#   make test       → run pytest with coverage
#   make lint       → ruff check + format check
#   make fix        → ruff auto-fix + format
#
# Prerequisites: uv, Docker, docker compose v2
# =============================================================================

.DEFAULT_GOAL := help

# ── Variables ─────────────────────────────────────────────────────────────────
UV        := uv run
SRCS      := src/ tests/ main.py
CERTS_DIR := nginx/certs
CERT      := $(CERTS_DIR)/server.crt

# ── Help ──────────────────────────────────────────────────────────────────────
.PHONY: help
help:
	@echo ""
	@echo "  Data Quality Dashboard"
	@echo ""
	@echo "  Local development"
	@echo "  ─────────────────────────────────────────────────────"
	@echo "  make install      Install all dependencies (prod + dev)"
	@echo "  make dev          Start dev server with hot-reload on :8000"
	@echo "  make run          Start prod-style server (no reload)"
	@echo ""
	@echo "  Docker"
	@echo "  ─────────────────────────────────────────────────────"
	@echo "  make up           Build + start app and nginx (https://localhost)"
	@echo "  make up-detach    Same as up, but run in background"
	@echo "  make down         Stop and remove containers"
	@echo "  make logs         Tail logs for all services"
	@echo "  make logs-app     Tail app service logs only"
	@echo "  make nginx-reload Reload nginx config without restart"
	@echo "  make smoke        Smoke-test the running stack (curl /health)"
	@echo ""
	@echo "  Code quality"
	@echo "  ─────────────────────────────────────────────────────"
	@echo "  make lint         Ruff lint + format check (no changes)"
	@echo "  make fix          Ruff auto-fix + format (writes files)"
	@echo "  make test         Run pytest with coverage (fail < 80%)"
	@echo "  make test-fast    Run pytest, stop on first failure"
	@echo "  make pentest      Run white-box security scanner (needs live server)"
	@echo ""
	@echo "  Utilities"
	@echo "  ─────────────────────────────────────────────────────"
	@echo "  make certs        Generate self-signed TLS certs for local Docker"
	@echo "  make clean        Remove .venv, __pycache__, .coverage, .pytest_cache"
	@echo ""

# =============================================================================
# Local development
# =============================================================================

.PHONY: install
install:
	uv sync --frozen --all-extras

.PHONY: dev
dev: install
	$(UV) uvicorn src.api:app --reload --host 0.0.0.0 --port 8000

.PHONY: run
run: install
	$(UV) python main.py

# =============================================================================
# Docker
# =============================================================================

.PHONY: certs
certs:
	@if [ ! -f "$(CERT)" ]; then \
		echo "→ Generating self-signed TLS certificates…"; \
		bash scripts/gen_certs.sh; \
	else \
		echo "→ Certificates already exist at $(CERTS_DIR)/ — skipping."; \
	fi

.PHONY: up
up: certs
	docker compose up --build

.PHONY: up-detach
up-detach: certs
	docker compose up --build -d
	@echo ""
	@echo "  Stack is running. Try:"
	@echo "    make smoke     → curl -k https://localhost/health"
	@echo "    make logs      → tail all logs"
	@echo "    make down      → stop"
	@echo ""

.PHONY: down
down:
	docker compose down

.PHONY: logs
logs:
	docker compose logs -f

.PHONY: logs-app
logs-app:
	docker compose logs -f app

.PHONY: nginx-reload
nginx-reload:
	docker compose exec nginx nginx -s reload

.PHONY: smoke
smoke:
	@echo "→ GET https://localhost/health"
	@curl -sk https://localhost/health | python3 -m json.tool

# =============================================================================
# Code quality
# =============================================================================

.PHONY: lint
lint:
	$(UV) ruff check $(SRCS)
	$(UV) ruff format --check $(SRCS)

.PHONY: fix
fix:
	$(UV) ruff check $(SRCS) --fix
	$(UV) ruff format $(SRCS)

.PHONY: test
test: install
	$(UV) pytest tests/ \
		--cov=src \
		--cov-report=term-missing \
		--cov-fail-under=80 \
		-v

.PHONY: test-fast
test-fast: install
	$(UV) pytest tests/ -x -q

.PHONY: pentest
pentest:
	@echo "→ Starting server in background…"
	$(UV) uvicorn src.api:app --port 8000 --log-level warning &
	@sleep 3
	$(UV) python tests/pentest_whitebox.py; \
		pkill -f "uvicorn src.api:app" || true

# =============================================================================
# Utilities
# =============================================================================

.PHONY: clean
clean:
	rm -rf .venv __pycache__ src/__pycache__ tests/__pycache__ \
		.coverage coverage.xml .pytest_cache .ruff_cache \
		$(shell find . -type d -name '__pycache__' 2>/dev/null)
	@echo "→ Clean complete."
