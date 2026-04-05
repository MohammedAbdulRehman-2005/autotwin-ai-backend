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
    2. Attempt MongoDB connection (graceful fail → demo mode)
    3. Warm up MemoryGraph with demo vendor data

    Shutdown
    ────────
    4. Close MongoDB Motor client cleanly
    """

    # ── STARTUP ────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("  %s v%s — starting up", settings.APP_NAME, settings.APP_VERSION)
    logger.info("  DEBUG=%s", settings.DEBUG)
    logger.info("=" * 60)

    # 1. MongoDB connection probe
    try:
        from models.database import _client, _motor_available  # noqa: PLC0415
        if _motor_available and _client is not None:
            await _client.admin.command("ping")
            logger.info("[Startup] ✅ MongoDB connected → %s", settings.MONGODB_URL)
        else:
            logger.warning("[Startup] ⚠️  MongoDB unavailable — running in IN-MEMORY demo mode.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Startup] ⚠️  MongoDB ping failed (%s) — demo mode active.", exc)

    # 2. Warm up MemoryGraph
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
        from models.database import _client  # noqa: PLC0415
        if _client is not None:
            _client.close()
            logger.info("[Shutdown] ✅ MongoDB Motor client closed.")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Shutdown] Motor client close skipped: %s", exc)
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
        "http://127.0.0.1:3000",
        "*",                           # open for demo; restrict in production
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
