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

    @classmethod
    def from_pandera(
        cls,
        *,
        file: str,
        total_rows: int,
        errors: list[ValidationError],
        invalid_rows: int,
    ) -> ValidationResult:
        """Factory: build a ValidationResult from parsed Pandera failure data."""
        return cls(
            file=file,
            total_rows=total_rows,
            valid_rows=max(0, total_rows - invalid_rows),
            invalid_rows=invalid_rows,
            errors=errors,
            is_valid=len(errors) == 0,
        )


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


class HistoryResponse(BaseModel):
    """Response from GET /validate/history."""

    model_config = ConfigDict(populate_by_name=True)

    total: int
    limit: int
    results: list[ValidationResult]


class HealthResponse(BaseModel):
    """Response from GET /health."""

    status: str
    version: str


# ---------------------------------------------------------------------------
# History manager — encapsulates bounded append logic
# ---------------------------------------------------------------------------


class HistoryManager:
    """Bounded FIFO list of ValidationResult objects.

    Keeps the latest *max_size* entries.  Older entries are discarded
    automatically when the cap is exceeded.

    Parameters
    ----------
    max_size:
        Maximum number of entries to retain (default 50).
    """

    def __init__(self, max_size: int = 50) -> None:
        self._items: list[ValidationResult] = []
        self._max_size = max_size

    def push(self, result: ValidationResult) -> None:
        """Append *result* and trim the list to *max_size*."""
        self._items.append(result)
        if len(self._items) > self._max_size:
            del self._items[: len(self._items) - self._max_size]

    def latest(self, limit: int) -> list[ValidationResult]:
        """Return the last *limit* entries in reverse-chronological order."""
        return list(reversed(self._items[-limit:]))

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"HistoryManager(size={len(self)}, max_size={self._max_size})"
