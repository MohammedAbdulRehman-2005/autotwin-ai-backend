"""
main.py
────────
AutoTwin AI — Application entry point.

Wires together:
  - FastAPI app with full OpenAPI metadata
  - CORS middleware (Vercel frontend + localhost)
  - Lifespan context (startup / shutdown hooks)
  - API router mounted at /api
  - Root redirect → /docs
  - Supabase PostgreSQL + Storage integration
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from api.routes import router
from core.config import settings

# ── Logging configuration ─────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("autotwin_ai.main")

# ══════════════════════════════════════════════════════════════
# Custom OpenAPI tag metadata
# ══════════════════════════════════════════════════════════════
TAGS_METADATA = [
    {
        "name": "Auth",
        "description": "OAuth2 JWT authentication. Use **demo / demo123** for quick access.",
    },
    {
        "name": "Invoice",
        "description": (
            "Core invoice intelligence pipeline. Submit via file upload, JSON body, "
            "or form fields. Returns confidence scores, anomaly detection, and AI decisions."
        ),
    },
    {
        "name": "Analytics",
        "description": "Aggregate KPI dashboard — processed invoices, anomalies, savings, risk scores.",
    },
    {
        "name": "Demo",
        "description": (
            "One-click demo endpoint. Runs a pre-built price-spike scenario "
            "(TechnoVendor Inc. @ ₹10,000 vs ₹5,000 historical avg) through the full pipeline."
        ),
    },
    {
        "name": "Logs",
        "description": "Retrieve structured pipeline logs per invoice. Also available via WebSocket.",
    },
    {
        "name": "System",
        "description": "Health checks and system diagnostics.",
    },
]

# ══════════════════════════════════════════════════════════════
# Lifespan — startup & shutdown hooks
# ══════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Async context manager executed once per worker process.

    Startup
    ───────
    1. Log banner
    2. Probe Supabase PostgreSQL connection
    3. Bootstrap Supabase Storage bucket
    4. Warm up MemoryGraph with demo vendor data

    Shutdown
    ────────
    5. Dispose SQLAlchemy async engine pool cleanly
    """

    # ── STARTUP ────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("  %s v%s — starting up", settings.APP_NAME, settings.APP_VERSION)
    logger.info("  DEBUG=%s", settings.DEBUG)
    logger.info("=" * 60)

    # 1. Supabase PostgreSQL connection probe
    try:
        from models.database import _engine, _pg_available  # noqa: PLC0415
        if _pg_available and _engine is not None:
            async with _engine.connect() as conn:
                from sqlalchemy import text
                await conn.execute(text("SELECT 1"))
            logger.info("[Startup] ✅ Supabase PostgreSQL connected.")
        else:
            logger.warning("[Startup] ⚠️  DATABASE_URL not set — running in IN-MEMORY demo mode.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Startup] ⚠️  PostgreSQL probe failed (%s) — demo mode active.", exc)

    # 2. Bootstrap Supabase Storage bucket
    try:
        from models.supabase_client import ensure_bucket_exists, is_storage_available  # noqa: PLC0415
        if is_storage_available():
            await ensure_bucket_exists()
            logger.info("[Startup] ✅ Supabase Storage ready (bucket='%s').", settings.SUPABASE_STORAGE_BUCKET)
        else:
            logger.warning("[Startup] ⚠️  Supabase Storage unavailable — file uploads disabled.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Startup] Storage bootstrap error: %s", exc)

    # 3. Warm up MemoryGraph
    try:
        from services.memory import MemoryGraph  # noqa: PLC0415
        mg = MemoryGraph()
        vendors = mg.get_all_vendors()
        logger.info("[Startup] ✅ MemoryGraph warm — %d demo vendor(s) pre-loaded.", len(vendors))
        for v in vendors:
            logger.debug(
                "  └─ %-30s avg=₹%-10.0f txns=%d",
                v["vendor"], v["avg_price"], v["transaction_count"],
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("[Startup] MemoryGraph init error: %s", exc)

    logger.info("[Startup] 🚀 %s is ready to serve.", settings.APP_NAME)

    # ── Hand control to the app ────────────────────────────────
    yield

    # ── SHUTDOWN ───────────────────────────────────────────────
    logger.info("[Shutdown] Gracefully shutting down %s…", settings.APP_NAME)
    try:
        from models.database import _engine  # noqa: PLC0415
        if _engine is not None:
            await _engine.dispose()
            logger.info("[Shutdown] ✅ SQLAlchemy engine pool disposed.")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Shutdown] Engine dispose skipped: %s", exc)
    logger.info("[Shutdown] 👋 Goodbye.")


# ══════════════════════════════════════════════════════════════
# FastAPI application
# ══════════════════════════════════════════════════════════════

app = FastAPI(
    title=settings.APP_NAME,
    description="""
## AutoTwin AI — Confidence-Aware, Self-Healing Financial Intelligence System

A production-ready AI backend that processes invoices through a multi-agent pipeline:

| Agent | Role |
|---|---|
| 🔍 **VisionAgent** | OCR / NLP field extraction with confidence scoring |
| 📊 **AnalyticsAgent** | Anomaly detection: price spikes, duplicates, unusual vendors |
| 🧠 **ConfidenceEngine** | Weighted tri-signal confidence: extraction × pattern × history |
| ⚖️ **DecisionEngine** | Auto-execute / warn / human-review routing |
| 🌐 **BrowserAgent** | Self-healing RPA with DOM-failure retry |
| 🔄 **ReflectionAgent** | Meta-cognitive self-improvement loop |

### Demo Access
- **Swagger UI**: [/docs](/docs)
- **Login**: `demo / demo123`
- **No-auth demo**: `POST /api/demo-run`
- **Frontend**: [https://autotwin-one.vercel.app](https://autotwin-one.vercel.app)
""",
    version=settings.APP_VERSION,
    openapi_tags=TAGS_METADATA,
    servers=[
        {"url": "https://awake-comfort-production-4bf3.up.railway.app", "description": "Production Server"},
        {"url": "http://localhost:8000", "description": "Local Development"}
    ],
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ══════════════════════════════════════════════════════════════
# CORS Middleware
# ══════════════════════════════════════════════════════════════

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://autotwin-one.vercel.app",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Invoice-ID", "X-Processing-Time"],
)

# ══════════════════════════════════════════════════════════════
# Router
# ══════════════════════════════════════════════════════════════

app.include_router(router, prefix="/api")

# ══════════════════════════════════════════════════════════════
# Root-level aliases — frontend calls these WITHOUT /api prefix
# ══════════════════════════════════════════════════════════════

from fastapi import Request as _Request
from fastapi.responses import JSONResponse as _JSONResponse

@app.post("/process-invoice-analysis", include_in_schema=False)
async def root_process_invoice_analysis(_req: _Request) -> _JSONResponse:
    """Alias: frontend calls /process-invoice-analysis (no /api prefix)."""
    try:
        body = await _req.json()
        from services.analysis_engine import process_invoice_analysis
        result = await process_invoice_analysis(body.get("document_id", ""))
        return _JSONResponse(content=result)
    except Exception as exc:
        logger.error("[RootAlias] /process-invoice-analysis error: %s", exc)
        return _JSONResponse(status_code=500, content={"error": str(exc)})

@app.post("/process-invoice", include_in_schema=False)
async def root_process_invoice(_req: _Request) -> _JSONResponse:
    """Alias: forward /process-invoice (no /api prefix) → /api/process-invoice."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/process-invoice", status_code=307)

# ══════════════════════════════════════════════════════════════
# Root
# ══════════════════════════════════════════════════════════════

@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Redirect bare root to the interactive API docs."""
    return RedirectResponse(url="/docs")


# ══════════════════════════════════════════════════════════════
# Dev entrypoint
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="debug" if settings.DEBUG else "info",
    )
