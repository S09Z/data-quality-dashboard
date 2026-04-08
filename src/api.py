"""FastAPI application — app factory and lifespan only.

All route handlers live under src/routers/v1/ and are mounted at /v1.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.config import settings
from src.logging_config import configure_logging
from src.pipeline import DataPipeline
from src.routers.v1 import router as v1_router
from src.routers.v1.pipeline import HISTORY_MAX_SIZE
from src.schemas import HistoryManager, SummaryResponse, ValidationResult
from src.search import SearchEngine, build_report_from_validation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise per-app state and run the startup pipeline."""
    configure_logging(log_level=settings.log_level, log_json=settings.log_json)

    app.state.engine = SearchEngine(
        semantic=settings.use_semantic_search,
        model=settings.semantic_model,
    )
    app.state.last_result: ValidationResult | None = None
    app.state.last_summary: SummaryResponse | None = None
    app.state.history = HistoryManager(max_size=HISTORY_MAX_SIZE)

    logger.info("Starting up — running initial validation pipeline…")
    try:
        pipeline = DataPipeline(settings.data_path)
        result, summary = pipeline.run()

        app.state.last_result = result
        app.state.last_summary = summary
        app.state.history.push(result)

        report_text = build_report_from_validation(result)
        app.state.engine.index([report_text])
        logger.info(
            "Startup pipeline complete. valid=%d invalid=%d",
            result.valid_rows,
            result.invalid_rows,
        )
    except Exception:
        logger.exception("Startup pipeline failed — API will still start.")

    yield
    logger.info("Shutting down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Data Quality Dashboard",
    description=(
        "Validate sales data, explore summaries, and search quality reports.\n\n"
        "All endpoints are versioned under **/v1**."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(v1_router, prefix="/v1")
