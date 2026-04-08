"""Pydantic v2 response models and Pandera DataFrameModel for sales data."""

from __future__ import annotations

from typing import Any

import pandera.polars as pa
from pandera import Field
from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Pandera schema — validated against Polars DataFrames
# ---------------------------------------------------------------------------


class SalesSchema(pa.DataFrameModel):
    """Schema for the raw sales CSV data."""

    order_id: str = Field(nullable=False)
    product: str = Field(nullable=False)
    quantity: int = Field(gt=0, nullable=False)
    unit_price: float = Field(gt=0.0, nullable=False)
    order_date: str = Field(nullable=False)
    region: str = Field(nullable=False)

    class Config:
        coerce = True


# ---------------------------------------------------------------------------
# Pydantic v2 API response models
# ---------------------------------------------------------------------------


class ValidationError(BaseModel):
    """A single validation failure."""

    model_config = ConfigDict(populate_by_name=True)

    column: str
    check: str
    row_index: int | None = None
    failure_case: Any = None


class ValidationResult(BaseModel):
    """Response from POST /validate."""

    model_config = ConfigDict(populate_by_name=True)

    file: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    errors: list[ValidationError]
    is_valid: bool


class ColumnSummary(BaseModel):
    """Summary statistics for a numeric column."""

    column: str
    min: float | None
    max: float | None
    mean: float | None
    null_count: int


class SummaryResponse(BaseModel):
    """Response from GET /summary."""

    model_config = ConfigDict(populate_by_name=True)

    file: str
    total_rows: int
    total_revenue: float
    columns: list[ColumnSummary]
    regions: dict[str, int]


class SearchResponse(BaseModel):
    """Response from GET /search."""

    query: str
    results: list[str]
    total: int


class HealthResponse(BaseModel):
    """Response from GET /health."""

    status: str
    version: str
