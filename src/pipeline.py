"""Data pipeline: load CSV with Polars, validate with Pandera, summarize."""

from __future__ import annotations

from pathlib import Path

import polars as pl
from pandera.errors import SchemaErrors

from src.schemas import (
    ColumnSummary,
    SalesSchema,
    SummaryResponse,
    ValidationError,
    ValidationResult,
)


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
    """
    Collect the LazyFrame, then run Pandera validation.

    Returns the (possibly partial) DataFrame and a ValidationResult.
    Any rows that cause schema errors are reported but the full DataFrame
    is still returned so callers can inspect or summarize it.
    """
    df = lf.collect()
    total_rows = len(df)
    errors: list[ValidationError] = []

    failure_cases = None
    try:
        SalesSchema.validate(df, lazy=True)
    except SchemaErrors as exc:
        failure_cases = exc.failure_cases  # polars DataFrame with columns:
        # failure_case, schema_context, column, check, check_number, index
        for row in failure_cases.to_dicts():
            errors.append(
                ValidationError(
                    column=row.get("column") or "",
                    check=str(row.get("check", "")),
                    row_index=row.get("index"),
                    failure_case=row.get("failure_case"),
                )
            )
        # (errors list is fully populated; row counts computed below)

    if failure_cases is not None and "index" in failure_cases.columns:
        computed_invalid = failure_cases["index"].drop_nulls().n_unique()
    else:
        computed_invalid = len(errors)

    result = ValidationResult(
        file=file,
        total_rows=total_rows,
        valid_rows=max(0, total_rows - computed_invalid),
        invalid_rows=computed_invalid,
        errors=errors,
        is_valid=len(errors) == 0,
    )
    return df, result


def summarize(df: pl.DataFrame, file: str = "unknown") -> SummaryResponse:
    """Compute aggregate statistics over the validated DataFrame."""
    total_rows = len(df)

    # Revenue = quantity * unit_price (only where both are non-null and valid)
    # Cast to Float64 first so numeric comparisons work even when Pandera coercion
    # left the column as String (e.g. all-null unit_price column).
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
    total_revenue = (
        df_clean.select(
            (pl.col("quantity").cast(pl.Float64) * pl.col("unit_price"))
            .sum()
            .alias("rev")
        )["rev"][0]
        or 0.0
    )

    # Numeric column summaries
    numeric_cols = ["quantity", "unit_price"]
    column_summaries: list[ColumnSummary] = []
    for col in numeric_cols:
        stats = df_safe.select(
            pl.col(col).min().alias("min"),
            pl.col(col).max().alias("max"),
            pl.col(col).mean().alias("mean"),
            pl.col(col).is_null().sum().alias("null_count"),
        ).row(0)
        column_summaries.append(
            ColumnSummary(
                column=col,
                min=float(stats[0]) if stats[0] is not None else None,
                max=float(stats[1]) if stats[1] is not None else None,
                mean=float(stats[2]) if stats[2] is not None else None,
                null_count=int(stats[3]),
            )
        )

    # Region distribution
    regions: dict[str, int] = {}
    if "region" in df.columns:
        region_counts = (
            df.group_by("region").agg(pl.len().alias("count")).sort("region")
        )
        for row in region_counts.to_dicts():
            regions[row["region"]] = row["count"]

    return SummaryResponse(
        file=file,
        total_rows=total_rows,
        total_revenue=round(total_revenue, 2),
        columns=column_summaries,
        regions=regions,
    )
