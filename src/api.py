"""FastAPI application exposing data quality endpoints."""

from __future__ import annotations

import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile

from src.config import settings
from src.logging_config import configure_logging
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
# Constants derived from settings
# ---------------------------------------------------------------------------

DEFAULT_DATA_PATH = settings.data_path

_ALLOWED_SUFFIXES = {".csv", ".parquet"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise per-app state and run the startup pipeline."""
    configure_logging(log_level=settings.log_level, log_json=settings.log_json)
    # Attach fresh state to the app instance (no module-level globals)
    app.state.engine = SearchEngine(
        semantic=settings.use_semantic_search,
        model=settings.semantic_model,
    )
    app.state.last_result: ValidationResult | None = None
    app.state.last_summary: SummaryResponse | None = None

    logger.info("Starting up — running initial validation pipeline…")
    try:
        lf = load_csv(DEFAULT_DATA_PATH)
        df, result = validate(lf, file=str(DEFAULT_DATA_PATH))
        summary = summarize(df, file=str(DEFAULT_DATA_PATH))

        app.state.last_result = result
        app.state.last_summary = summary

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
async def validate_file(
    request: Request,
    file_path: str | None = Query(
        default=None,
        description="Server-side path to a CSV or Parquet file to validate.",
    ),
    upload: UploadFile | None = File(
        default=None,
        description="CSV file uploaded directly (multipart/form-data).",
    ),
) -> ValidationResult:
    """
    Validate a CSV / Parquet file with Polars + Pandera.

    **Two ways to provide data:**
    - `file_path` query param — absolute path to a server-side file.
    - `upload` form field — multipart CSV upload (max ~50 MB).

    If both are given, `upload` takes precedence.
    Falls back to the default `data/sales.csv` when neither is supplied.
    """
    # ── Resolve the file to validate ────────────────────────────────────────
    tmp_path: Path | None = None

    if upload is not None:
        # Write the uploaded bytes to a temp file so Polars can scan it
        suffix = Path(upload.filename or "upload.csv").suffix.lower() or ".csv"
        if suffix not in _ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded file type '{suffix}' is not supported. Use .csv or .parquet.",
            )
        content = await upload.read()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        path = tmp_path
        label = upload.filename or "upload"
    elif file_path is not None:
        path = Path(file_path)
        label = file_path
    else:
        path = DEFAULT_DATA_PATH
        label = str(DEFAULT_DATA_PATH)

    # ── Validate path ────────────────────────────────────────────────────────
    if not path.exists():
        _cleanup(tmp_path)
        raise HTTPException(status_code=404, detail=f"File not found: {label}")
    if path.suffix.lower() not in _ALLOWED_SUFFIXES:
        _cleanup(tmp_path)
        raise HTTPException(
            status_code=400, detail="Only .csv and .parquet files are supported."
        )

    # ── Run pipeline ─────────────────────────────────────────────────────────
    try:
        lf = load_csv(path)
        df, result = validate(lf, file=label)
        summary = summarize(df, file=label)
    except Exception as exc:
        _cleanup(tmp_path)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        _cleanup(tmp_path)

    # ── Update app state & re-index search ───────────────────────────────────
    request.app.state.last_result = result
    request.app.state.last_summary = summary
    report_text = build_report_from_validation(result)
    request.app.state.engine.index([report_text])

    return result


@app.get("/summary", response_model=SummaryResponse, tags=["pipeline"])
def get_summary(request: Request) -> SummaryResponse:
    """
    Return aggregate statistics for the last validated file.

    Call POST /validate first to populate the summary.
    """
    summary = request.app.state.last_summary
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail="No summary available yet. Call POST /validate first.",
        )
    return summary


@app.get("/search", response_model=SearchResponse, tags=["search"])
def search(
    request: Request,
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
    results = request.app.state.engine.query(text=q, top_k=top_k)
    return SearchResponse(query=q, results=results, total=len(results))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cleanup(path: Path | None) -> None:
    """Silently remove a temporary file if it exists."""
    if path and path.exists():
        path.unlink(missing_ok=True)
