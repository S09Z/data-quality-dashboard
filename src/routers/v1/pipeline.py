"""Pipeline routes — v1.

Endpoints
---------
POST /validate          — validate a CSV / Parquet file
GET  /summary           — column statistics for the last validated file
GET  /validate/history  — bounded history ring (newest first)
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile

from src.pipeline import DataPipeline
from src.schemas import (
    HistoryManager,
    HistoryResponse,
    SummaryResponse,
    ValidationResult,
)
from src.search import build_report_from_validation

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pipeline"])

_ALLOWED_SUFFIXES: frozenset[str] = frozenset({".csv", ".parquet"})
HISTORY_MAX_SIZE: int = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cleanup(path: Path | None) -> None:
    """Silently remove a temporary file if it exists."""
    if path and path.exists():
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/validate",
    response_model=ValidationResult,
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "file": "sales.csv",
                        "total_rows": 10,
                        "valid_rows": 7,
                        "invalid_rows": 3,
                        "is_valid": False,
                        "errors": [
                            {
                                "column": "quantity",
                                "check": "greater_than(0)",
                                "row_index": 7,
                                "failure_case": "-1",
                            }
                        ],
                    }
                }
            }
        },
        400: {"description": "Unsupported file type"},
        404: {"description": "File not found"},
        500: {"description": "Internal pipeline error"},
    },
)
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
    - `upload` form field — multipart CSV upload (max ~10 MB).

    If both are given, `upload` takes precedence.
    Falls back to the default `data/sales.csv` when neither is supplied.
    """
    from src.config import settings

    default_data_path = settings.data_path
    tmp_path: Path | None = None

    # ── Resolve the file to validate ────────────────────────────────────────
    if upload is not None:
        # Reject unsupported extensions BEFORE reading any bytes
        suffix = Path(upload.filename or "upload.csv").suffix.lower() or ".csv"
        if suffix not in _ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Uploaded file type '{suffix}' is not supported. "
                    "Use .csv or .parquet."
                ),
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
        path = default_data_path
        label = str(default_data_path)

    # ── Validate path ────────────────────────────────────────────────────────
    if not path.exists():
        _cleanup(tmp_path)
        raise HTTPException(status_code=404, detail=f"File not found: {label}")
    if path.suffix.lower() not in _ALLOWED_SUFFIXES:
        _cleanup(tmp_path)
        raise HTTPException(
            status_code=400,
            detail="Only .csv and .parquet files are supported.",
        )

    # ── Run pipeline ─────────────────────────────────────────────────────────
    try:
        pipeline = DataPipeline(path)
        result, summary = pipeline.run()
        result = result.model_copy(update={"file": label})
        summary = summary.model_copy(update={"file": label})
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

    # ── Append to bounded history ─────────────────────────────────────────────
    manager: HistoryManager = request.app.state.history
    manager.push(result)

    return result


@router.get(
    "/summary",
    response_model=SummaryResponse,
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "file": "sales.csv",
                        "total_rows": 10,
                        "total_revenue": 1024.68,
                        "columns": [
                            {
                                "column": "quantity",
                                "min": 1.0,
                                "max": 10.0,
                                "mean": 4.3,
                                "null_count": 0,
                            },
                            {
                                "column": "unit_price",
                                "min": 9.99,
                                "max": 99.99,
                                "mean": 32.49,
                                "null_count": 1,
                            },
                        ],
                        "regions": {"East": 2, "North": 3, "South": 3, "West": 2},
                    }
                }
            }
        },
        404: {"description": "No summary yet — call POST /v1/validate first"},
    },
)
def get_summary(request: Request) -> SummaryResponse:
    """
    Return aggregate statistics for the last validated file.

    Call POST /v1/validate first to populate the summary.
    """
    summary = request.app.state.last_summary
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail="No summary available yet. Call POST /v1/validate first.",
        )
    return summary


@router.get(
    "/validate/history",
    response_model=HistoryResponse,
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "limit": 50,
                        "results": [
                            {
                                "file": "sales.csv",
                                "total_rows": 10,
                                "valid_rows": 7,
                                "invalid_rows": 3,
                                "is_valid": False,
                                "errors": [],
                            }
                        ],
                    }
                }
            }
        }
    },
)
def get_history(
    request: Request,
    limit: int = Query(
        default=10,
        ge=1,
        le=HISTORY_MAX_SIZE,
        description="Maximum number of past results to return (newest first).",
    ),
) -> HistoryResponse:
    """
    Return the last *limit* validation results in reverse-chronological order.

    Results accumulate across calls to POST /v1/validate (capped at
    the last 50 entries). Resets when the server restarts.
    """
    manager: HistoryManager = request.app.state.history
    sliced = manager.latest(limit)
    return HistoryResponse(total=len(manager), limit=limit, results=sliced)
