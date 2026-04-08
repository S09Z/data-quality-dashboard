# Data Quality Dashboard — Implementation Report

**Project:** `data-quality-dashboard`
**Date:** April 8, 2026
**Branch:** `main`
**Total source lines:** 1,499 (excl. `.venv`)

---

## 1. Project Overview

Pipeline ที่รับไฟล์ข้อมูล Sales → Validate → เปิด API ให้ Query → ค้นหาด้วย Semantic Search → ครบ loop

```
data-quality-dashboard/
├── data/sales.csv             # Sample data (10 rows, 3 bad rows intentional)
├── src/
│   ├── config.py              # NEW — pydantic-settings AppConfig
│   ├── logging_config.py      # NEW — structured JSON logging
│   ├── schemas.py             # Pandera DataFrameModel + Pydantic v2 models
│   ├── pipeline.py            # Polars load/transform + Pandera validation
│   ├── search.py              # Haystack BM25 + Semantic search
│   └── api.py                 # FastAPI REST endpoints
├── tests/test_pipeline.py     # 76 pytest tests
├── main.py                    # Uvicorn entry point
└── pyproject.toml             # uv + Ruff config
```

**Tech Stack:**

| Layer | Library | Version |
|-------|---------|---------|
| Data loading | Polars | 1.39.3 |
| Validation | Pandera[polars] | 0.30.1 |
| Schema / Response | Pydantic v2 | 2.12.5 |
| Configuration | pydantic-settings | — |
| API | FastAPI | 0.135.3 |
| Search | haystack-ai | 2.27.0 |
| Logging | python-json-logger | 4.1.0 |
| Test | Pytest + pytest-cov | 9.0.3 / 7.1.0 |
| Lint/Format | Ruff | 0.15.9 |
| Runtime | uv + Python 3.12 | — |

---

## 2. Implementation Phases

### Phase 1 — Project Scaffolding

#### Before
```
data-quality-dashboard/   ← empty directory
```

#### After
```
data-quality-dashboard/
├── .venv/                 (uv-managed, Python 3.12.13)
├── .gitignore
├── .vscode/settings.json  (Ruff as default formatter, Pylint disabled)
├── pyproject.toml
└── main.py                (stub)
```

**คำสั่งที่ใช้:**
```bash
uv init --python 3.12 --no-readme
uv python install 3.12
uv sync --extra dev
```

**ผลกระทบ:**
- ได้ virtual environment ที่ locked ทุก dependency (`uv.lock`)
- ไม่ต้องใช้ `pip` หรือ `venv` เอง — uv จัดการทั้งหมด
- VS Code ชี้ interpreter ไปที่ `.venv/bin/python` อัตโนมัติ

---

### Phase 2 — Core Files

#### 2a. `data/sales.csv`

**Before:** ไม่มีไฟล์

**After:** 10 rows, 6 columns (`order_id, product, quantity, unit_price, order_date, region`) โดยมี 3 bad rows ตั้งใจ:

| Row | ปัญหา |
|-----|--------|
| ORD-008 | `quantity = -1` (ต้อง > 0) |
| ORD-009 | `unit_price = null` (ห้าม null) |
| ORD-010 | `quantity = 0` (ต้อง > 0) |

**ผลกระทบ:** ใช้ทดสอบ validation pipeline ได้ตรงตาม real-world scenario

---

#### 2b. `src/schemas.py`

**Before:** ไม่มีไฟล์

**After:** กำหนด schema ทั้งหมดในที่เดียว

```python
# Pandera — ตรวจ Polars DataFrame
class SalesSchema(pa.DataFrameModel):
    order_id:   str
    product:    str
    quantity:   int   = Field(gt=0)       # ต้องมากกว่า 0
    unit_price: float = Field(gt=0.0)     # ต้องมากกว่า 0 และ ไม่ null
    order_date: str
    region:     str

# Pydantic v2 — Response Models
class ValidationResult(BaseModel): ...   # POST /validate
class SummaryResponse(BaseModel): ...    # GET /summary
class SearchResponse(BaseModel): ...     # GET /search
class HealthResponse(BaseModel): ...     # GET /health
```

**ผลกระทบ:** schema เดียวใช้ทั้ง validation และ API response — ไม่มี duplication

---

#### 2c. `src/pipeline.py`

**Before:** ไม่มีไฟล์

**After (Phase 2):**
```python
def load_csv(path) → pl.LazyFrame:     # scan_csv เท่านั้น
def validate(lf)  → (DataFrame, ValidationResult)
def summarize(df) → SummaryResponse
```

**After (Phase 3 — Parquet fix):**
```python
def load_csv(path) → pl.LazyFrame:
    path = Path(path)
    if path.suffix.lower() == ".parquet":   # ← เพิ่ม branch นี้
        return pl.scan_parquet(str(path))
    return pl.scan_csv(str(path))
```

| Before | After |
|--------|-------|
| `.parquet` → crash ใน Polars | `.parquet` → `scan_parquet` ถูกต้อง |
| รองรับเฉพาะ CSV | รองรับ CSV + Parquet |

**Bug ที่พบและแก้:** `summarize()` crash เมื่อ `unit_price` เป็น null ทั้ง column เพราะ Polars infer เป็น `String` แทน `Float64`

```python
# Before (crash)
df.filter(pl.col("unit_price") > 0)   # ComputeError: cannot compare String with int

# After (แก้แล้ว)
df_safe = df.with_columns(
    pl.col("unit_price").cast(pl.Float64, strict=False)
)
df_safe.filter(pl.col("unit_price") > 0)   # ✓
```

---

#### 2d. `src/search.py`

**Before (Phase 2):** BM25 เท่านั้น, constructor ไม่มี argument

```python
class SearchEngine:
    def __init__(self) -> None:
        self._store = InMemoryDocumentStore()
        self._retriever = InMemoryBM25Retriever(...)
```

**After (Phase 3 — Semantic mode):**
```python
class SearchEngine:
    def __init__(
        self,
        semantic: bool = False,         # ← toggle BM25 / Semantic
        model: str = "all-MiniLM-L6-v2"
    ) -> None: ...

    @property
    def mode(self) -> str: ...          # "bm25" | "semantic"
```

| Feature | BM25 (default) | Semantic (`USE_SEMANTIC_SEARCH=true`) |
|---------|---------------|---------------------------------------|
| Startup cost | ทันที | download model ครั้งแรก (~100 MB) |
| ความแม่นยำ | keyword matching | semantic similarity |
| dependency | haystack-ai | + sentence-transformers |
| ใช้ GPU | ไม่ | optional |

---

#### 2e. `src/api.py`

**Before (Phase 2) — Global mutable state:**
```python
# Module-level globals — ปัญหาใหญ่
_engine: SearchEngine = SearchEngine()
_last_result: ValidationResult | None = None
_last_summary: SummaryResponse | None = None

async def lifespan(app):
    global _last_result, _last_summary   # ← anti-pattern
    ...
```

**After (Phase 3 — `app.state`):**
```python
async def lifespan(app):
    app.state.engine = SearchEngine(...)   # ← per-app, isolatable
    app.state.last_result = None
    app.state.last_summary = None
    ...

def get_summary(request: Request):
    summary = request.app.state.last_summary   # ← ดึงจาก app.state
```

**ผลกระทบ:**

| ด้าน | Before | After |
|------|--------|-------|
| Test isolation | ต้อง monkey-patch module globals | ใช้ `TestClient` แยก instance ได้ |
| Concurrent safety | shared mutable state | ผูกกับ app instance |
| FastAPI best practice | ❌ | ✅ |

---

**Before (Phase 2) — POST /validate รับแค่ file path:**
```python
def validate_file(
    file_path: str = Query(default=...)   # server-side path เท่านั้น
) -> ValidationResult: ...
```

**After (Phase 3 — UploadFile):**
```python
async def validate_file(
    request: Request,
    file_path: str | None = Query(default=None),     # server path (optional)
    upload: UploadFile | None = File(default=None),  # ← upload CSV/Parquet
) -> ValidationResult: ...
```

**Priority order:** `upload` > `file_path` > `DEFAULT_DATA_PATH`

---

**Before (Phase 2) — Hardcoded config:**
```python
DEFAULT_DATA_PATH = Path(__file__).parent.parent / "data" / "sales.csv"
# host, port, log_level ถูก hardcode ใน main.py
```

**After (Phase 3 — `src/config.py`):**
```python
class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    data_path: Path = ...
    log_level: str = "INFO"
    log_json: bool = False
    use_semantic_search: bool = False
    semantic_model: str = "all-MiniLM-L6-v2"

settings = Settings()   # อ่านจาก env vars หรือ .env อัตโนมัติ
```

**วิธีใช้:**
```bash
# เปลี่ยน port + เปิด JSON logging
PORT=9090 LOG_JSON=true uv run python main.py

# เปิด semantic search
USE_SEMANTIC_SEARCH=true uv run python main.py

# ชี้ data file อื่น
DATA_PATH=/data/q1_sales.parquet uv run python main.py
```

---

**Before (Phase 2) — ไม่มี structured logging:**
```python
# ไม่มี logging configuration เลย — print หรือ default unconfigured logger
```

**After (Phase 3 — `src/logging_config.py`):**
```
# LOG_JSON=false (default — human-readable)
2026-04-08T14:30:00 | INFO     | src.api | Startup pipeline complete. valid=7 invalid=3

# LOG_JSON=true (structured)
{"timestamp": "2026-04-08T14:30:00", "level": "INFO", "name": "src.api",
 "message": "Startup pipeline complete. valid=7 invalid=3"}
```

---

## 3. Test Coverage ก่อน/หลัง

| Phase | Tests | Coverage |
|-------|-------|----------|
| Phase 2 (initial) | 22 | 97% |
| + error detail tests | 57 | 100% |
| + high priority (upload, parquet, app.state) | 63 | 100% |
| + medium priority (config, logging, semantic) | **76** | **95%*** |

> *95% เพราะ 13 lines ที่เหลือเป็น live semantic search path (ต้อง download model) — ไม่ run ใน unit test suite โดยเจตนา

### Test classes ทั้งหมด

| Class | Tests | Coverage area |
|-------|-------|---------------|
| `TestValidate` | 6 | pipeline.validate — happy/error paths |
| `TestSummarize` | 5 | pipeline.summarize — totals, regions, revenue |
| `TestSearchEngine` | 4 | SearchEngine BM25 basic |
| `TestAPI` | 7 | FastAPI endpoints — basic integration |
| `TestValidateExtra` | 9 | validate — edge cases, types, serialisation, Parquet |
| `TestSummarizeExtra` | 7 | summarize — null handling, stats, null_count |
| `TestBuildReport` | 4 | build_report_from_validation — report format |
| `TestSearchEngineExtra` | 4 | SearchEngine — top_k, types, empty |
| `TestAPIExtra` | 14 | API — upload, 400/404/500, app.state, mocks |
| `TestSettings` | 4 | config.Settings — defaults, env override |
| `TestSearchEngineMode` | 4 | SearchEngine — mode property, semantic error |
| `TestLoggingConfig` | 5 | logging_config — plain/JSON/level/idempotent |

---

## 4. API Endpoints Summary

| Method | Path | Input | Output | Status |
|--------|------|-------|--------|--------|
| `GET` | `/health` | — | `{"status":"ok","version":"0.1.0"}` | ✅ |
| `POST` | `/validate` | `file_path` query **or** `upload` form | `ValidationResult` | ✅ |
| `GET` | `/summary` | — | `SummaryResponse` | ✅ |
| `GET` | `/search?q=...&top_k=5` | query string | `SearchResponse` | ✅ |

Swagger UI: `http://localhost:8000/docs`

---

## 5. Remaining Backlog (Low Priority)

| # | Item | Effort |
|---|------|--------|
| 1 | `[project.scripts]` CLI entry `dqd` ใน pyproject.toml | S |
| 2 | `.pre-commit-config.yaml` รัน Ruff ก่อน commit | S |
| 3 | `GET /validate/history` เก็บ validation results ล่าสุด N รายการ | M |
| 4 | OpenAPI `openapi_extra` examples บน endpoints | S |
| 5 | Rate limiting / API key auth | M |
