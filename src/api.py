"""FastAPI application exposing data quality endpoints."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from src.pipeline import load_csv, summarize, validate
from src.schemas import (
    HealthResponse,
    SearchResponse,
    SummaryResponse,
    ValidationResult,
)
from src.search import SearchEngine, build_report_from_validation

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default data path — can be overridden via environment variable DATA_PATH
# ---------------------------------------------------------------------------

DEFAULT_DATA_PATH = Path(__file__).parent.parent / "data" / "sales.csv"

# Module-level state shared across requests
_engine: SearchEngine = SearchEngine()
_last_result: ValidationResult | None = None
_last_summary: SummaryResponse | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run the pipeline once at startup to pre-populate search index."""
    global _last_result, _last_summary

    logger.info("Starting up — running initial validation pipeline…")
    try:
        lf = load_csv(DEFAULT_DATA_PATH)
        df, result = validate(lf, file=str(DEFAULT_DATA_PATH))
        summary = summarize(df, file=str(DEFAULT_DATA_PATH))

        _last_result = result
        _last_summary = summary

        report_text = build_report_from_validation(result)
        _engine.index([report_text])
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
    description="Validate sales data, explore summaries, and search quality reports.",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    """Liveness check."""
    return HealthResponse(status="ok", version="0.1.0")


@app.post("/validate", response_model=ValidationResult, tags=["pipeline"])
def validate_file(
    file_path: str = Query(
        default=str(DEFAULT_DATA_PATH),
        description="Absolute or relative path to the CSV file to validate.",
    ),
) -> ValidationResult:
    """
    Load and validate a CSV file with Polars + Pandera.

    Returns a detailed ValidationResult with any schema/data errors found.
    """
    global _last_result, _last_summary

    path = Path(file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    if path.suffix.lower() not in {".csv", ".parquet"}:
        raise HTTPException(
            status_code=400, detail="Only .csv and .parquet files are supported."
        )

    try:
        lf = load_csv(path)
        df, result = validate(lf, file=str(path))
        summary = summarize(df, file=str(path))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Update shared state & re-index search
    _last_result = result
    _last_summary = summary
    report_text = build_report_from_validation(result)
    _engine.index([report_text])

    return result


@app.get("/summary", response_model=SummaryResponse, tags=["pipeline"])
def get_summary() -> SummaryResponse:
    """
    Return aggregate statistics for the last validated file.

    Call POST /validate first to populate the summary.
    """
    if _last_summary is None:
        raise HTTPException(
            status_code=404,
            detail="No summary available yet. Call POST /validate first.",
        )
    return _last_summary


@app.get("/search", response_model=SearchResponse, tags=["search"])
def search(
    q: str = Query(
        ..., min_length=1, description="Search query over validation reports."
    ),
    top_k: int = Query(
        default=5, ge=1, le=20, description="Number of results to return."
    ),
) -> SearchResponse:
    """
    BM25 keyword search over indexed data quality reports.

    Returns matching report snippets ranked by relevance.
    """
    results = _engine.query(text=q, top_k=top_k)
    return SearchResponse(query=q, results=results, total=len(results))
