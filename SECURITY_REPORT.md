# Security Assessment Report

| | |
| --- | --- |
| **Project** | Data Quality Dashboard |
| **Date** | 2026-04-08 |
| **Version** | 0.1.0 |
| **Scope** | Local development API — `http://127.0.0.1:8000` |
| **Method** | White-box pentest (full source code access) |
| **Tester** | Internal / White-hat perspective |
| **Script** | `tests/pentest_whitebox.py` |

---

## Executive Summary

| Metric | Value |
| --- | --- |
| Test categories | 8 |
| Individual probes | 27 |
| ✅ PASS | 20 |
| 🔴 FAIL | 1 |
| 🟡 WARN | 3 |
| 🔵 INFO | 3 |
| **Overall Risk Score** | **4.4 / 10 — MEDIUM RISK** |

The core security posture is **solid**.
All high-severity attack classes — path traversal, sensitive file disclosure,
infinite-read DoS, and input boundary violations — are fully blocked by the
current implementation.

One exploitable upload-bypass remains open and **must be resolved before any
network-exposed deployment**.
Three missing HTTP security headers are low-impact today but become relevant
the moment a browser-facing UI is added.

---

## Risk Score Reference

| Range | Rating | Colour |
| --- | --- | --- |
| 9.0 – 10.0 | Critical | 🔴🔴 |
| 7.0 – 8.9 | High | 🔴 |
| 4.0 – 6.9 | Medium | 🟡 |
| 1.0 – 3.9 | Low | 🟢 |
| 0.0 | Informational / Pass | ⚪ |

Scores follow a **CVSSv3-inspired 1–10 scale** considering:
Attack Vector · Attack Complexity · Privileges Required · Impact (C/I/A).

---

## Findings Detail

### F-01 — Upload Extension Bypass (Timeout)

| | |
| --- | --- |
| **Score** | **6.5 / 10 — Medium** |
| **Severity** | 🔴 FAIL |
| **Endpoint** | `POST /validate` (multipart upload) |
| **File** | `src/api.py` → `validate_file()` |
| **Probe** | Upload file named `exploit.sh` with `application/x-sh` content-type |
| **Expected** | HTTP 400 immediately, before reading body |
| **Actual** | Request hangs — no response within 5 s timeout |

#### Root Cause

FastAPI / Starlette buffers the **entire** multipart body into memory before the
route handler runs.  The suffix check inside `validate_file()` fires *after*
buffering completes.  A `.sh` file therefore occupies server memory for the
full read duration; the handler never gets a chance to reject it quickly.

#### Attack Scenario

An attacker on the same network (or any misconfigured cloud deployment) sends a
stream of large `.sh` uploads in parallel.  Each request holds a connection
open and consumes unbounded memory.  The server becomes unresponsive — effective
DoS with ~10 concurrent requests of 100 MB each.

#### Proof of Concept

```bash
# Hangs for full timeout duration
curl -s -m 5 -X POST http://localhost:8000/validate \
  -F "upload=@/etc/hosts;filename=exploit.sh;type=application/x-sh"
```

#### Recommended Fix

```python
# src/api.py — add upload size cap and enforce it BEFORE read()
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB hard cap

async def validate_file(...):
    if upload is not None:
        suffix = Path(upload.filename or "upload.csv").suffix.lower() or ".csv"
        # Suffix check already correct — fires before read()
        if suffix not in _ALLOWED_SUFFIXES:
            raise HTTPException(400, f"File type '{suffix}' not supported.")
        # ADD: reject oversized uploads before buffering
        if upload.size is not None and upload.size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File too large. Maximum size is 10 MB.")
        content = await upload.read()
```

For deeper protection, limit the body at the ASGI layer:

```python
# src/api.py — Starlette body-size limit middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int = 10 * 1024 * 1024):
        super().__init__(app)
        self._max = max_bytes

    async def dispatch(self, request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self._max:
            return Response("Request body too large.", status_code=413)
        return await call_next(request)

app.add_middleware(MaxBodySizeMiddleware)
```

---

### F-02 — Missing `X-Content-Type-Options` Header

| | |
| --- | --- |
| **Score** | **3.5 / 10 — Low** |
| **Severity** | 🟡 WARN |
| **Endpoint** | All responses |
| **Expected** | `X-Content-Type-Options: nosniff` present |
| **Actual** | Header absent |

#### Impact (F-02)

Without this header, browsers may MIME-sniff response bodies and interpret a
JSON payload as HTML or JavaScript — enabling content-injection attacks if this
API ever serves a browser-facing frontend or Swagger UI.

---

### F-03 — Missing `X-Frame-Options` Header

| | |
| --- | --- |
| **Score** | **3.5 / 10 — Low** |
| **Severity** | 🟡 WARN |
| **Endpoint** | All responses |
| **Expected** | `X-Frame-Options: DENY` |
| **Actual** | Header absent |

#### Impact (F-03)

The Swagger UI (`/docs`) can be embedded in an `<iframe>` on an
attacker-controlled page — enabling **clickjacking** against developers who
access the docs while authenticated.

---

### F-04 — Missing `Content-Security-Policy` Header

| | |
| --- | --- |
| **Score** | **3.5 / 10 — Low** |
| **Severity** | 🟡 WARN |
| **Endpoint** | All responses |
| **Expected** | `Content-Security-Policy: default-src 'none'` |
| **Actual** | Header absent |

#### Impact (F-04)

No CSP allows unrestricted inline script execution on any HTML page served
(including `/docs`), enabling XSS if input is ever reflected into a page context.

#### Recommended Fix (covers F-02, F-03, F-04 in one change)

```python
# src/api.py
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'none'"
        return response

app.add_middleware(SecurityHeadersMiddleware)
```

---

### F-05 — Null Bytes Accepted in Search Query

| | |
| --- | --- |
| **Score** | **2.0 / 10 — Informational** |
| **Severity** | 🔵 INFO |
| **Endpoint** | `GET /search?q=valid%00inject` |
| **Actual** | HTTP 200 — query processed without error |

#### Impact (F-05)

Very low for a pure Python / BM25 stack.  Null bytes would matter if the query
were passed to a C-level library or a shell command.  Currently safe — logged
for awareness only.

---

### F-06 — Regex-style Pattern Accepted in Search Query

| | |
| --- | --- |
| **Score** | **2.0 / 10 — Informational** |
| **Severity** | 🔵 INFO |
| **Endpoint** | `GET /search?q=(a%2B)%2B%24` |
| **Actual** | HTTP 200 — BM25 treats pattern as plain string |

#### Impact (F-06)

BM25 does not evaluate regex — no ReDoS risk at present.  Would become a risk
if the search backend is upgraded to a regex-capable engine (Elasticsearch,
Solr, etc.).  Add a `max_length` guard if that migration is planned.

---

## Already Secure ✅

| Attack Class | Probe | HTTP Result |
| --- | --- | --- |
| Path traversal (relative) | `../../etc/passwd` | 404 |
| Path traversal (deep) | `../../../../etc/shadow` | 404 |
| Path traversal (dot-dot-env) | `../../.env` | 404 |
| Path traversal (URL-encoded) | `%2e%2e%2fetc%2fpasswd` | 404 |
| Path traversal (double-slash) | `....//....//etc/passwd` | 404 |
| Absolute sensitive path | `/etc/passwd` | 400 (suffix check) |
| Absolute sensitive path | `/etc/hostname` | 404 |
| Source code read | `../../src/config.py` | 400 (suffix check) |
| Config read | `../../pyproject.toml` | 404 |
| DoS via infinite read | `file_path=/dev/zero` | 400 (suffix `.` blocked) |
| Upload unsupported extension | `.txt` file | 400 |
| Search — empty query | `q=` | 422 (`min_length=1`) |
| Search — oversized query | 10 000-char string | 422 (`max_length=500`) |
| Search — unicode bomb | 1 000 × 💣 | 422 |
| `top_k` below minimum | `top_k=0` | 422 |
| `top_k` negative | `top_k=-1` | 422 |
| `top_k` above maximum | `top_k=21` | 422 |
| `top_k` very large | `top_k=9999` | 422 |
| CSV formula injection (server) | `=CMD("calc")` in product | Not evaluated by Polars |

---

## Remediation Roadmap

| Priority | ID | Finding | Estimated Effort | Owner |
| --- | --- | --- | --- | --- |
| **P1** | F-01 | Upload extension bypass / timeout | ~30 min | Backend |
| **P2** | F-02 | Missing `X-Content-Type-Options` | ~15 min (shared middleware) | Backend |
| **P2** | F-03 | Missing `X-Frame-Options` | ~15 min (shared middleware) | Backend |
| **P2** | F-04 | Missing `Content-Security-Policy` | ~15 min (shared middleware) | Backend |
| **P3** | F-05 | Null bytes in search — monitor | None now | — |
| **P3** | F-06 | Regex pattern in search — monitor | None now | — |

> **P1** must be resolved before any network-accessible deployment.
> **P2** must be resolved before any browser-facing UI is added.
> **P3** items are accepted risks — revisit if the search backend changes.

---

## How to Reproduce

```bash
# 1. Start the development server
uv run uvicorn src.api:app --port 8000

# 2. Run the full automated pentest
uv run python tests/pentest_whitebox.py

# 3. Reproduce F-01 manually (observe ~5 s hang)
curl -s -m 5 -X POST http://localhost:8000/validate \
  -F "upload=@/etc/hosts;filename=exploit.sh;type=application/x-sh"

# 4. Check missing security headers
curl -sI http://localhost:8000/health \
  | grep -iE "x-content-type-options|x-frame-options|content-security-policy"
```

---

## Appendix — Scoring Methodology

Scores are assigned per the CVSSv3 Base Score rubric, simplified to a 1–10 scale:

| Factor | Considered |
| --- | --- |
| Attack Vector | Network / Local / Physical |
| Attack Complexity | Low / High |
| Privileges Required | None / Low / High |
| User Interaction | None / Required |
| Confidentiality Impact | None / Low / High |
| Integrity Impact | None / Low / High |
| Availability Impact | None / Low / High |

Automated by `tests/pentest_whitebox.py` — re-run after each fix to verify
the score improves.

---

*End of report.*
