"""Pytest tests for the data quality pipeline and API."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest
from fastapi.testclient import TestClient

from src.pipeline import load_csv, summarize, validate
from src.schemas import ValidationError, ValidationResult
from src.search import SearchEngine, build_report_from_validation

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent.parent / "data"
SALES_CSV = DATA_DIR / "sales.csv"

VALID_CSV = """\
order_id,product,quantity,unit_price,order_date,region
ORD-A01,Widget A,5,9.99,2024-01-15,North
ORD-A02,Widget B,3,19.99,2024-01-16,South
ORD-A03,Gadget X,10,49.99,2024-01-17,East
"""

INVALID_CSV_NEG_QTY = """\
order_id,product,quantity,unit_price,order_date,region
ORD-B01,Widget A,-1,9.99,2024-01-15,North
"""

INVALID_CSV_NULL_PRICE = """\
order_id,product,quantity,unit_price,order_date,region
ORD-C01,Widget A,5,,2024-01-15,North
"""

INVALID_CSV_ZERO_QTY = """\
order_id,product,quantity,unit_price,order_date,region
ORD-D01,Widget A,0,9.99,2024-01-15,North
"""

# Multi-error CSV: negative qty + null price in separate rows
MULTI_ERROR_CSV = """\
order_id,product,quantity,unit_price,order_date,region
ORD-E01,Widget A,5,9.99,2024-01-15,North
ORD-E02,Widget B,-1,19.99,2024-01-16,South
ORD-E03,Widget C,2,,2024-01-17,East
ORD-E04,Gadget X,0,49.99,2024-01-18,West
"""

# All rows valid, single region
SINGLE_REGION_CSV = """\
order_id,product,quantity,unit_price,order_date,region
ORD-F01,Widget A,2,10.0,2024-02-01,West
ORD-F02,Widget B,4,20.0,2024-02-02,West
"""


def _df_from_csv_str(csv_str: str) -> pl.LazyFrame:
    """Helper: parse an inline CSV string into a Polars LazyFrame."""
    return pl.read_csv(io.StringIO(csv_str)).lazy()


# ---------------------------------------------------------------------------
# Unit tests — pipeline.validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_valid_data_passes(self):
        lf = _df_from_csv_str(VALID_CSV)
        _, result = validate(lf, file="valid.csv")
        assert result.is_valid is True
        assert result.total_rows == 3
        assert result.invalid_rows == 0
        assert result.errors == []

    def test_invalid_quantity_negative(self):
        lf = _df_from_csv_str(INVALID_CSV_NEG_QTY)
        _, result = validate(lf, file="neg_qty.csv")
        assert result.is_valid is False
        assert any("quantity" in e.column for e in result.errors)

    def test_invalid_quantity_zero(self):
        lf = _df_from_csv_str(INVALID_CSV_ZERO_QTY)
        _, result = validate(lf, file="zero_qty.csv")
        assert result.is_valid is False
        assert any("quantity" in e.column for e in result.errors)

    def test_null_price_caught(self):
        lf = _df_from_csv_str(INVALID_CSV_NULL_PRICE)
        _, result = validate(lf, file="null_price.csv")
        assert result.is_valid is False
        assert any("unit_price" in e.column for e in result.errors)

    def test_real_sales_csv_has_errors(self):
        """sales.csv contains intentional bad rows — should fail validation."""
        lf = load_csv(SALES_CSV)
        _, result = validate(lf, file="sales.csv")
        assert result.is_valid is False
        assert result.invalid_rows > 0

    def test_result_file_name_is_set(self):
        lf = _df_from_csv_str(VALID_CSV)
        _, result = validate(lf, file="my_file.csv")
        assert result.file == "my_file.csv"


# ---------------------------------------------------------------------------
# Unit tests — pipeline.summarize
# ---------------------------------------------------------------------------


class TestSummarize:
    def test_summary_totals(self):
        lf = _df_from_csv_str(VALID_CSV)
        df, _ = validate(lf)
        summary = summarize(df)
        # 5*9.99 + 3*19.99 + 10*49.99 = 49.95 + 59.97 + 499.90 = 609.82
        assert abs(summary.total_revenue - 609.82) < 0.01

    def test_summary_row_count(self):
        lf = _df_from_csv_str(VALID_CSV)
        df, _ = validate(lf)
        summary = summarize(df)
        assert summary.total_rows == 3

    def test_summary_regions(self):
        lf = _df_from_csv_str(VALID_CSV)
        df, _ = validate(lf)
        summary = summarize(df)
        assert "North" in summary.regions
        assert "South" in summary.regions
        assert "East" in summary.regions

    def test_summary_columns_present(self):
        lf = _df_from_csv_str(VALID_CSV)
        df, _ = validate(lf)
        summary = summarize(df)
        col_names = [c.column for c in summary.columns]
        assert "quantity" in col_names
        assert "unit_price" in col_names

    def test_summary_excludes_invalid_rows_from_revenue(self):
        """Rows with quantity <= 0 should not contribute to total_revenue."""
        lf = _df_from_csv_str(VALID_CSV + "ORD-X01,Bad,-1,9.99,2024-01-18,West\n")
        df, _ = validate(lf)
        summary = summarize(df)
        # Revenue should match the 3 valid rows only
        assert abs(summary.total_revenue - 609.82) < 0.01


# ---------------------------------------------------------------------------
# Unit tests — search.SearchEngine
# ---------------------------------------------------------------------------


class TestSearchEngine:
    def test_empty_index_returns_empty(self):
        engine = SearchEngine()
        results = engine.query("null values")
        assert results == []

    def test_index_and_query(self):
        engine = SearchEngine()
        engine.index(
            ["Validation FAILED: quantity has negative values.", "All checks passed."]
        )
        results = engine.query("negative quantity", top_k=1)
        assert len(results) >= 1
        assert "negative" in results[0].lower() or "quantity" in results[0].lower()

    def test_count_after_index(self):
        engine = SearchEngine()
        engine.index(["doc one", "doc two", "doc three"])
        assert engine.count() == 3

    def test_reindex_overwrites(self):
        engine = SearchEngine()
        engine.index(["first batch"])
        engine.index(["second batch"])
        assert engine.count() == 1


# ---------------------------------------------------------------------------
# Integration tests — FastAPI endpoints
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    """Create a TestClient with the full FastAPI app."""
    from src.api import app

    with TestClient(app) as c:
        yield c


class TestAPI:
    def test_health_ok(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_validate_default_file(self, client: TestClient):
        resp = client.post("/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_rows" in data
        assert "is_valid" in data

    def test_validate_missing_file(self, client: TestClient):
        resp = client.post("/validate", params={"file_path": "/nonexistent/file.csv"})
        assert resp.status_code == 404

    def test_validate_unsupported_format(self, client: TestClient):
        resp = client.post("/validate", params={"file_path": "/tmp/data.txt"})
        assert resp.status_code in {400, 404}

    def test_summary_available_after_validate(self, client: TestClient):
        # validate first to populate state
        client.post("/validate")
        resp = client.get("/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_revenue" in data
        assert "regions" in data

    def test_search_returns_results(self, client: TestClient):
        client.post("/validate")
        resp = client.get("/search", params={"q": "validation"})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert isinstance(data["results"], list)

    def test_search_missing_query(self, client: TestClient):
        resp = client.get("/search")
        assert resp.status_code == 422  # query param 'q' is required


# ---------------------------------------------------------------------------
# Extra unit tests — pipeline.validate (edge cases & error detail)
# ---------------------------------------------------------------------------


class TestValidateExtra:
    def test_multiple_errors_reported(self):
        """Multi-error CSV: negative qty + null price + zero qty → ≥3 errors."""
        lf = _df_from_csv_str(MULTI_ERROR_CSV)
        _, result = validate(lf, file="multi.csv")
        assert result.is_valid is False
        assert len(result.errors) >= 3

    def test_error_columns_are_named(self):
        """Every error entry must carry a non-empty column name."""
        lf = _df_from_csv_str(MULTI_ERROR_CSV)
        _, result = validate(lf)
        for err in result.errors:
            assert isinstance(err.column, str)
            assert len(err.column) > 0

    def test_error_check_is_populated(self):
        """Every error entry must carry a non-empty check description."""
        lf = _df_from_csv_str(INVALID_CSV_NEG_QTY)
        _, result = validate(lf)
        for err in result.errors:
            assert isinstance(err.check, str)
            assert len(err.check) > 0

    def test_failure_case_value_captured(self):
        """Failure case value (e.g. -1) should be stored on the error."""
        lf = _df_from_csv_str(INVALID_CSV_NEG_QTY)
        _, result = validate(lf)
        qty_errors = [e for e in result.errors if "quantity" in e.column]
        assert len(qty_errors) == 1
        assert str(qty_errors[0].failure_case) == "-1"

    def test_valid_rows_plus_invalid_equals_total(self):
        """valid_rows + invalid_rows must always equal total_rows."""
        lf = _df_from_csv_str(MULTI_ERROR_CSV)
        _, result = validate(lf)
        assert result.valid_rows + result.invalid_rows == result.total_rows

    def test_load_csv_returns_lazyframe(self):
        """load_csv must return a Polars LazyFrame, not a DataFrame."""
        lf = load_csv(SALES_CSV)
        assert isinstance(lf, pl.LazyFrame)

    def test_load_csv_parquet_returns_lazyframe(self, tmp_path):
        """load_csv on a .parquet file must also return a Polars LazyFrame."""
        p = tmp_path / "sales.parquet"
        pl.read_csv(io.StringIO(VALID_CSV)).write_parquet(str(p))
        lf = load_csv(p)
        assert isinstance(lf, pl.LazyFrame)
        df = lf.collect()
        assert len(df) == 3

    def test_validate_returns_dataframe(self):
        """validate() must return an eager pl.DataFrame as the first element."""
        lf = _df_from_csv_str(VALID_CSV)
        df, _ = validate(lf)
        assert isinstance(df, pl.DataFrame)

    def test_pydantic_validation_result_serialises(self):
        """ValidationResult must be JSON-serialisable via model_dump()."""
        lf = _df_from_csv_str(INVALID_CSV_NEG_QTY)
        _, result = validate(lf)
        d = result.model_dump()
        assert d["is_valid"] is False
        assert isinstance(d["errors"], list)


# ---------------------------------------------------------------------------
# Extra unit tests — pipeline.summarize (edge cases)
# ---------------------------------------------------------------------------


class TestSummarizeExtra:
    def test_summary_single_region(self):
        lf = _df_from_csv_str(SINGLE_REGION_CSV)
        df, _ = validate(lf)
        summary = summarize(df)
        assert summary.regions == {"West": 2}

    def test_summary_revenue_single_region(self):
        # 2*10.0 + 4*20.0 = 20 + 80 = 100
        lf = _df_from_csv_str(SINGLE_REGION_CSV)
        df, _ = validate(lf)
        summary = summarize(df)
        assert abs(summary.total_revenue - 100.0) < 0.01

    def test_summary_null_price_excluded_from_revenue(self):
        """Rows with null unit_price must not contribute to total_revenue."""
        lf = _df_from_csv_str(INVALID_CSV_NULL_PRICE)
        df, _ = validate(lf)
        summary = summarize(df)
        assert summary.total_revenue == 0.0

    def test_summary_column_stats_min_max(self):
        lf = _df_from_csv_str(VALID_CSV)
        df, _ = validate(lf)
        summary = summarize(df)
        qty = next(c for c in summary.columns if c.column == "quantity")
        assert qty.min == 3.0
        assert qty.max == 10.0

    def test_summary_null_count_for_clean_data(self):
        lf = _df_from_csv_str(VALID_CSV)
        df, _ = validate(lf)
        summary = summarize(df)
        for col in summary.columns:
            assert col.null_count == 0

    def test_summary_null_count_detected(self):
        """unit_price null in INVALID_CSV_NULL_PRICE should show null_count=1."""
        lf = _df_from_csv_str(INVALID_CSV_NULL_PRICE)
        df, _ = validate(lf)
        summary = summarize(df)
        price_col = next(c for c in summary.columns if c.column == "unit_price")
        assert price_col.null_count == 1

    def test_summary_file_name_propagated(self):
        lf = _df_from_csv_str(VALID_CSV)
        df, _ = validate(lf)
        summary = summarize(df, file="custom.csv")
        assert summary.file == "custom.csv"


# ---------------------------------------------------------------------------
# Extra unit tests — search.build_report_from_validation
# ---------------------------------------------------------------------------


class TestBuildReport:
    def _make_result(
        self, *, is_valid: bool, errors: list[ValidationError]
    ) -> ValidationResult:
        return ValidationResult(
            file="test.csv",
            total_rows=5,
            valid_rows=5 - len(errors),
            invalid_rows=len(errors),
            errors=errors,
            is_valid=is_valid,
        )

    def test_passed_report_contains_passed(self):
        result = self._make_result(is_valid=True, errors=[])
        report = build_report_from_validation(result)
        assert "PASSED" in report
        assert "test.csv" in report

    def test_failed_report_contains_failed(self):
        err = ValidationError(
            column="quantity", check="greater_than(0)", row_index=2, failure_case="-1"
        )
        result = self._make_result(is_valid=False, errors=[err])
        report = build_report_from_validation(result)
        assert "FAILED" in report
        assert "quantity" in report
        assert "greater_than(0)" in report
        assert "row 2" in report
        assert "-1" in report

    def test_report_without_row_index(self):
        err = ValidationError(
            column="product", check="not_nullable", row_index=None, failure_case=None
        )
        result = self._make_result(is_valid=False, errors=[err])
        report = build_report_from_validation(result)
        assert "row" not in report or "row_index" not in report
        assert "product" in report

    def test_report_counts_in_output(self):
        result = self._make_result(is_valid=True, errors=[])
        report = build_report_from_validation(result)
        assert "Total rows: 5" in report
        assert "Valid rows: 5" in report
        assert "Invalid rows: 0" in report


# ---------------------------------------------------------------------------
# Extra unit tests — search.SearchEngine (boundary cases)
# ---------------------------------------------------------------------------


class TestSearchEngineExtra:
    def test_query_top_k_respected(self):
        engine = SearchEngine()
        engine.index(["alpha errors", "beta warnings", "gamma nulls", "delta skipped"])
        results = engine.query("errors warnings nulls", top_k=2)
        assert len(results) <= 2

    def test_query_returns_strings(self):
        engine = SearchEngine()
        engine.index(["some report text"])
        results = engine.query("report", top_k=5)
        assert all(isinstance(r, str) for r in results)

    def test_index_single_doc(self):
        engine = SearchEngine()
        engine.index(["only one document"])
        assert engine.count() == 1
        results = engine.query("document")
        assert len(results) == 1

    def test_index_empty_list(self):
        engine = SearchEngine()
        engine.index([])
        assert engine.count() == 0
        assert engine.query("anything") == []


# ---------------------------------------------------------------------------
# Extra integration tests — FastAPI (missing-coverage branches)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fresh_client():
    """A separate TestClient for tests that need clean shared state."""
    from src.api import app

    with TestClient(app) as c:
        yield c


class TestAPIExtra:
    def test_validate_returns_error_list(self, client: TestClient):
        """POST /validate on sales.csv should return a non-empty errors list."""
        resp = client.post("/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["errors"], list)
        assert len(data["errors"]) > 0

    def test_validate_error_structure(self, client: TestClient):
        """Each error object must have column, check, row_index, failure_case."""
        resp = client.post("/validate")
        data = resp.json()
        for err in data["errors"]:
            assert "column" in err
            assert "check" in err
            assert "row_index" in err
            assert "failure_case" in err

    def test_validate_with_custom_valid_csv(self, client: TestClient, tmp_path: Path):
        """POST /validate on a valid temp CSV file should return is_valid=true."""
        p = tmp_path / "valid.csv"
        p.write_text(VALID_CSV)
        resp = client.post("/validate", params={"file_path": str(p)})
        assert resp.status_code == 200
        assert resp.json()["is_valid"] is True

    def test_validate_with_invalid_csv_temp(self, client: TestClient, tmp_path: Path):
        """POST /validate on a temp CSV with bad rows returns is_valid=false."""
        p = tmp_path / "bad.csv"
        p.write_text(INVALID_CSV_NEG_QTY)
        resp = client.post("/validate", params={"file_path": str(p)})
        assert resp.status_code == 200
        assert resp.json()["is_valid"] is False

    def test_validate_unsupported_extension_existing_file(
        self, client: TestClient, tmp_path: Path
    ):
        """An existing file with .txt extension should return HTTP 400."""
        p = tmp_path / "data.txt"
        p.write_text("some data\n")
        resp = client.post("/validate", params={"file_path": str(p)})
        assert resp.status_code == 400

    def test_summary_after_valid_file(self, client: TestClient, tmp_path: Path):
        """After validating a clean file, summary total_revenue should be correct."""
        p = tmp_path / "valid2.csv"
        p.write_text(VALID_CSV)
        client.post("/validate", params={"file_path": str(p)})
        resp = client.get("/summary")
        assert resp.status_code == 200
        data = resp.json()
        # 5*9.99 + 3*19.99 + 10*49.99
        assert abs(data["total_revenue"] - 609.82) < 0.01

    def test_summary_returns_404_when_no_validate_called(self):
        """With a broken startup (load_csv raises), /summary must return 404."""
        with patch("src.api.load_csv", side_effect=RuntimeError("no disk")):
            from src.api import app as fresh_app

            with TestClient(fresh_app, raise_server_exceptions=False) as c:
                resp = c.get("/summary")
                assert resp.status_code == 404

    def test_search_top_k_param(self, client: TestClient):
        """GET /search with top_k=1 should return at most 1 result."""
        client.post("/validate")
        resp = client.get("/search", params={"q": "validation", "top_k": 1})
        assert resp.status_code == 200
        assert len(resp.json()["results"]) <= 1

    def test_search_response_schema(self, client: TestClient):
        """Search response must include query, results, and total fields."""
        client.post("/validate")
        resp = client.get("/search", params={"q": "error"})
        data = resp.json()
        assert "query" in data
        assert "results" in data
        assert "total" in data
        assert data["query"] == "error"
        assert data["total"] == len(data["results"])

    def test_health_version_field(self, client: TestClient):
        """Health endpoint must return a version string."""
        resp = client.get("/health")
        assert "version" in resp.json()
        assert isinstance(resp.json()["version"], str)

    def test_startup_exception_does_not_crash_app(self, tmp_path: Path):
        """Even if the startup pipeline raises, the app should still be reachable."""
        with patch("src.api.load_csv", side_effect=RuntimeError("disk error")):
            from src.api import app as fresh_app

            with TestClient(fresh_app, raise_server_exceptions=False) as c:
                resp = c.get("/health")
                assert resp.status_code == 200

    def test_validate_returns_500_on_internal_error(self, tmp_path: Path):
        """POST /validate returns HTTP 500 when the pipeline itself crashes."""
        from src.api import app as fresh_app

        p = tmp_path / "ok.csv"
        p.write_text(VALID_CSV)
        with patch("src.api.validate", side_effect=RuntimeError("unexpected crash")):
            with TestClient(fresh_app, raise_server_exceptions=False) as c:
                resp = c.post("/validate", params={"file_path": str(p)})
                assert resp.status_code == 500
                assert "unexpected crash" in resp.json()["detail"]

    # ── Upload (UploadFile) tests ────────────────────────────────────────────

    def test_upload_valid_csv(self, client: TestClient):
        """POST /validate with a valid uploaded CSV should return is_valid=true."""
        resp = client.post(
            "/validate",
            files={"upload": ("valid.csv", VALID_CSV.encode(), "text/csv")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_valid"] is True
        assert data["total_rows"] == 3

    def test_upload_invalid_csv(self, client: TestClient):
        """POST /validate with uploaded CSV containing bad rows returns is_valid=false."""
        resp = client.post(
            "/validate",
            files={"upload": ("bad.csv", INVALID_CSV_NEG_QTY.encode(), "text/csv")},
        )
        assert resp.status_code == 200
        assert resp.json()["is_valid"] is False

    def test_upload_unsupported_extension(self, client: TestClient):
        """Uploading a .txt file should return HTTP 400."""
        resp = client.post(
            "/validate",
            files={"upload": ("data.txt", b"col1,col2\n1,2\n", "text/plain")},
        )
        assert resp.status_code == 400

    def test_upload_takes_precedence_over_file_path(
        self, client: TestClient, tmp_path: Path
    ):
        """When both upload and file_path are given, upload wins."""
        # file_path points at invalid data; upload is valid — result should be valid
        p = tmp_path / "invalid.csv"
        p.write_text(INVALID_CSV_NEG_QTY)
        resp = client.post(
            "/validate",
            params={"file_path": str(p)},
            files={"upload": ("valid.csv", VALID_CSV.encode(), "text/csv")},
        )
        assert resp.status_code == 200
        assert resp.json()["is_valid"] is True

    # ── Parquet tests ────────────────────────────────────────────────────────

    def test_validate_parquet_file(self, client: TestClient, tmp_path: Path):
        """POST /validate on a Parquet file should validate correctly."""
        import polars as pl

        p = tmp_path / "sales.parquet"
        pl.read_csv(io.StringIO(VALID_CSV)).write_parquet(str(p))
        resp = client.post("/validate", params={"file_path": str(p)})
        assert resp.status_code == 200
        assert resp.json()["is_valid"] is True
        assert resp.json()["total_rows"] == 3


# ---------------------------------------------------------------------------
# Settings / config tests
# ---------------------------------------------------------------------------


class TestSettings:
    def test_default_values(self):
        """Settings must expose sensible defaults without any env vars set."""
        from src.config import Settings

        s = Settings()
        assert s.host == "0.0.0.0"
        assert s.port == 8000
        assert s.reload is False
        assert s.log_level == "INFO"
        assert s.log_json is False
        assert s.use_semantic_search is False
        assert s.data_path.name == "sales.csv"

    def test_env_override(self, monkeypatch):
        """Environment variables must override defaults."""
        from src.config import Settings

        monkeypatch.setenv("PORT", "9090")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("LOG_JSON", "true")
        monkeypatch.setenv("USE_SEMANTIC_SEARCH", "false")
        s = Settings()
        assert s.port == 9090
        assert s.log_level == "DEBUG"
        assert s.log_json is True

    def test_data_path_is_path_object(self):
        from src.config import Settings

        s = Settings()
        assert isinstance(s.data_path, Path)

    def test_semantic_model_default(self):
        from src.config import Settings

        s = Settings()
        assert "MiniLM" in s.semantic_model or "minilm" in s.semantic_model.lower()


# ---------------------------------------------------------------------------
# SearchEngine BM25 mode property & new constructor args
# ---------------------------------------------------------------------------


class TestSearchEngineMode:
    def test_default_mode_is_bm25(self):
        engine = SearchEngine()
        assert engine.mode == "bm25"

    def test_explicit_bm25(self):
        engine = SearchEngine(semantic=False)
        assert engine.mode == "bm25"

    def test_semantic_mode_raises_without_package(self, monkeypatch):
        """Without sentence-transformers installed the engine should raise ImportError."""
        import sys

        # Temporarily hide sentence-transformers from imports
        for mod in list(sys.modules):
            if "sentence_transformers" in mod or "sentence-transformers" in mod:
                monkeypatch.delitem(sys.modules, mod, raising=False)

        import haystack.components.embedders as emb_mod

        with patch.object(
            emb_mod,
            "SentenceTransformersDocumentEmbedder",
            side_effect=ImportError("no st"),
        ):
            with pytest.raises((ImportError, Exception)):
                SearchEngine(semantic=True)

    def test_bm25_index_and_query_unchanged(self):
        """Existing BM25 behaviour must be unaffected by constructor change."""
        engine = SearchEngine(semantic=False)
        engine.index(["null values detected", "all checks passed"])
        results = engine.query("null", top_k=1)
        assert len(results) == 1
        assert "null" in results[0].lower()


# ---------------------------------------------------------------------------
# Logging configuration tests
# ---------------------------------------------------------------------------


class TestLoggingConfig:
    def test_plain_text_logging(self):
        """configure_logging with log_json=False must not raise."""
        import logging

        from src.logging_config import configure_logging

        configure_logging(log_level="WARNING", log_json=False)
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_json_logging(self):
        """configure_logging with log_json=True must install a JsonFormatter."""
        import logging

        from src.logging_config import configure_logging

        configure_logging(log_level="INFO", log_json=True)
        root = logging.getLogger()
        assert root.handlers, "root logger must have at least one handler"
        from pythonjsonlogger.json import JsonFormatter

        assert any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)

    def test_log_level_debug(self):
        import logging

        from src.logging_config import configure_logging

        configure_logging(log_level="DEBUG", log_json=False)
        assert logging.getLogger().level == logging.DEBUG

    def test_handlers_replaced_not_duplicated(self):
        """Calling configure_logging twice must not add duplicate handlers."""
        import logging

        from src.logging_config import configure_logging

        configure_logging(log_level="INFO", log_json=False)
        configure_logging(log_level="INFO", log_json=False)
        assert len(logging.getLogger().handlers) == 1

    def test_json_logging_missing_package_raises(self, monkeypatch):
        """configure_logging(log_json=True) raises ImportError if pythonjsonlogger is absent."""
        import sys

        from src.logging_config import configure_logging

        # Simulate missing package by hiding it from sys.modules
        monkeypatch.setitem(sys.modules, "pythonjsonlogger", None)
        monkeypatch.setitem(sys.modules, "pythonjsonlogger.json", None)
        with pytest.raises((ImportError, TypeError)):
            configure_logging(log_level="INFO", log_json=True)
