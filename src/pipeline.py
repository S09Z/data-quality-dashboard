"""Data pipeline: load CSV with Polars, validate with Pandera, summarize.

Design
------
The core logic lives inside ``DataPipeline``, which follows the
**Template Method** pattern::

    pipeline = DataPipeline("data/sales.csv")
    result, summary = pipeline.run()

``_parse_errors`` uses **recursion** (head/tail) to convert Pandera failure
rows into ``ValidationError`` objects.

``_column_summary`` is a ``@staticmethod`` that eliminates the repeated
stats block from the original ``summarize`` function.

Bare module-level functions (``load_csv``, ``validate``, ``summarize``) are
kept as thin **compatibility shims** so existing call-sites and tests require
no changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import polars as pl
from pandera.errors import SchemaErrors

from src.schemas import (
    ColumnSummary,
    SalesSchema,
    SummaryResponse,
    ValidationError,
    ValidationResult,
)

# ---------------------------------------------------------------------------
# DataPipeline — Template Method pattern
# ---------------------------------------------------------------------------


class DataPipeline:
    """Orchestrates the load → validate → summarize pipeline for one file.

    Parameters
    ----------
    path:
        Path to a ``.csv`` or ``.parquet`` file.

    Examples
    --------
    >>> pipeline = DataPipeline("data/sales.csv")
    >>> result, summary = pipeline.run()
    """

    _NUMERIC_COLS: ClassVar[list[str]] = ["quantity", "unit_price"]

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._df: pl.DataFrame | None = None
        self.result: ValidationResult | None = None
        self.summary: SummaryResponse | None = None

    # ------------------------------------------------------------------
    # Template method — fixed algorithm, overridable steps
    # ------------------------------------------------------------------

    def run(self) -> tuple[ValidationResult, SummaryResponse]:
        """Execute the full pipeline and return *(result, summary)*."""
        lf = self._load()
        self._df, self.result = self._validate(lf)
        self.summary = self._summarize(self._df)
        return self.result, self.summary

    # ------------------------------------------------------------------
    # Pipeline steps (overridable in subclasses)
    # ------------------------------------------------------------------

    def _load(self) -> pl.LazyFrame:
        """Scan the source file lazily."""
        if self._path.suffix.lower() == ".parquet":
            return pl.scan_parquet(str(self._path))
        return pl.scan_csv(str(self._path))

    def _validate(self, lf: pl.LazyFrame) -> tuple[pl.DataFrame, ValidationResult]:
        """Collect the LazyFrame and run Pandera validation."""
        df = lf.collect()
        total_rows = len(df)

        failure_cases: pl.DataFrame | None = None
        try:
            SalesSchema.validate(df, lazy=True)
        except SchemaErrors as exc:
            failure_cases = exc.failure_cases

        errors = self._parse_errors(
            failure_cases.to_dicts() if failure_cases is not None else []
        )

        if failure_cases is not None and "index" in failure_cases.columns:
            invalid_rows = failure_cases["index"].drop_nulls().n_unique()
        else:
            invalid_rows = len(errors)

        result = ValidationResult.from_pandera(
            file=str(self._path),
            total_rows=total_rows,
            errors=errors,
            invalid_rows=invalid_rows,
        )
        return df, result

    def _summarize(self, df: pl.DataFrame) -> SummaryResponse:
        """Compute aggregate statistics over the DataFrame."""
        total_rows = len(df)

        # Cast to Float64 so comparisons work even when Pandera coercion left
        # the column as String (e.g. all-null unit_price).
        df_safe = df.with_columns(
            pl.col("quantity").cast(pl.Float64, strict=False),
            pl.col("unit_price").cast(pl.Float64, strict=False),
        )
        df_clean = df_safe.filter(
            pl.col("quantity").is_not_null()
            & pl.col("unit_price").is_not_null()
            & (pl.col("quantity") > 0)
            & (pl.col("unit_price") > 0)
        )
        total_revenue: float = (
            df_clean.select(
                (pl.col("quantity").cast(pl.Float64) * pl.col("unit_price"))
                .sum()
                .alias("rev")
            )["rev"][0]
            or 0.0
        )

        column_summaries = [
            self._column_summary(df_safe, col) for col in self._NUMERIC_COLS
        ]

        regions: dict[str, int] = {}
        if "region" in df.columns:
            region_counts = (
                df.group_by("region").agg(pl.len().alias("count")).sort("region")
            )
            for row in region_counts.to_dicts():
                regions[row["region"]] = row["count"]

        return SummaryResponse(
            file=str(self._path),
            total_rows=total_rows,
            total_revenue=round(total_revenue, 2),
            columns=column_summaries,
            regions=regions,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_errors(
        rows: list[dict],
        acc: list[ValidationError] | None = None,
    ) -> list[ValidationError]:
        """Recursively convert Pandera failure-case rows to ValidationErrors.

        Parameters
        ----------
        rows:
            Remaining rows to process (consumed head-first).
        acc:
            Accumulated results (initialised to ``[]`` on first call).
        """
        if acc is None:
            acc = []
        if not rows:  # base case
            return acc
        head, *tail = rows
        acc.append(
            ValidationError(
                column=head.get("column") or "",
                check=str(head.get("check", "")),
                row_index=head.get("index"),
                failure_case=head.get("failure_case"),
            )
        )
        return DataPipeline._parse_errors(tail, acc)  # recursive case

    @staticmethod
    def _column_summary(df: pl.DataFrame, col: str) -> ColumnSummary:
        """Compute min/max/mean/null_count for *col* in *df*."""
        stats = df.select(
            pl.col(col).min().alias("min"),
            pl.col(col).max().alias("max"),
            pl.col(col).mean().alias("mean"),
            pl.col(col).is_null().sum().alias("null_count"),
        ).row(0)
        return ColumnSummary(
            column=col,
            min=float(stats[0]) if stats[0] is not None else None,
            max=float(stats[1]) if stats[1] is not None else None,
            mean=float(stats[2]) if stats[2] is not None else None,
            null_count=int(stats[3]),
        )

    def __repr__(self) -> str:
        status = (
            "not run"
            if self.result is None
            else ("valid" if self.result.is_valid else "invalid")
        )
        return f"DataPipeline(path={self._path.name!r}, status={status!r})"


# ---------------------------------------------------------------------------
# Compatibility shims — thin wrappers so existing call-sites keep working
# ---------------------------------------------------------------------------


def load_csv(path: str | Path) -> pl.LazyFrame:
    """Scan a CSV or Parquet file lazily with Polars."""
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        return pl.scan_parquet(str(path))
    return pl.scan_csv(str(path))


def validate(
    lf: pl.LazyFrame,
    file: str = "unknown",
) -> tuple[pl.DataFrame, ValidationResult]:
    """Validate a LazyFrame.  Shim that delegates to ``DataPipeline._validate``."""
    pipeline = DataPipeline(file)
    return pipeline._validate(lf)


def summarize(df: pl.DataFrame, file: str = "unknown") -> SummaryResponse:
    """Summarize a DataFrame.  Shim that delegates to ``DataPipeline._summarize``."""
    pipeline = DataPipeline(file)
    return pipeline._summarize(df)
