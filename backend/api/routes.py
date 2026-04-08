"""
api/routes.py
──────────────
AutoTwin AI — Full async API surface.

FIX LOG:
  - _optional_auth moved BEFORE all route definitions (was forward-referenced → NameError)
  - get_invoice deferred import replaced with top-level import
  - All Depends() references resolved at import time
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.security import OAuth2PasswordRequestForm

from core.security import authenticate_user, create_access_token
from models.database import (
    get_dashboard_stats,
    get_invoice,
    get_logs_for_invoice,
    is_demo_mode,
    save_approval,
    update_invoice,
)
from models.supabase_client import upload_invoice_file
from models.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    DashboardResponse,
    LogEntry,
    ProcessInvoiceResponse,
)
from services.orchestrator import Orchestrator

logger = logging.getLogger("autotwin_ai.routes")
router = APIRouter()

# ── Shared singletons (one per worker) ───────────────────────
_orchestrator = Orchestrator()
_memory_graph = _orchestrator.memory_graph  # reuse stateful instance

# ── WebSocket connection registry ─────────────────────────────
_ws_connections: Dict[str, List[WebSocket]] = {}


# ══════════════════════════════════════════════════════════════
# OPTIONAL AUTH HELPER — MUST BE DEFINED BEFORE ANY ROUTE
# THAT USES Depends(_optional_auth)
# ══════════════════════════════════════════════════════════════

async def _optional_auth(request: Request) -> Optional[Dict[str, Any]]:
    """
    Soft JWT auth — returns the user dict if a valid Bearer token is present,
    None otherwise.  Never raises, allowing unauthenticated demo access.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1]
    try:
        from core.security import verify_token, get_user  # deferred to avoid circular import
        payload = verify_token(token)
        return get_user(payload.get("sub", ""))
    except HTTPException:
        return None


# ══════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════

@router.post("/auth/token", tags=["Auth"], summary="Login and receive a JWT")
async def login(form_data: OAuth2PasswordRequestForm = Depends()) -> Dict[str, str]:
    """
    Exchange **username / password** for a Bearer JWT.
    Demo credentials: `demo / demo123`
    """
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(data={"sub": user["username"], "role": user["role"]})
    logger.info("[Routes] /auth/token → user=%s role=%s", user["username"], user["role"])
    return {"access_token": token, "token_type": "bearer"}


# ══════════════════════════════════════════════════════════════
# PROCESS INVOICE
# ══════════════════════════════════════════════════════════════

@router.post(
    "/process-invoice",
    response_model=ProcessInvoiceResponse,
    tags=["Invoice"],
    summary="Process an invoice — file upload or JSON body",
)
async def process_invoice(
    request: Request,
    file: Optional[UploadFile] = File(default=None),
    vendor: Optional[str] = Form(default=None),
    amount: Optional[float] = Form(default=None),
    date: Optional[str] = Form(default=None),
    currency: Optional[str] = Form(default="INR"),
    _user: Optional[Dict[str, Any]] = Depends(_optional_auth),
) -> ProcessInvoiceResponse:
    """
    Accepts **multipart/form-data** (file) *or* **application/json** *or* form fields.

    JSON body shape::

        {"vendor": "TechnoVendor Inc.", "amount": 5000, "date": "2024-01-15", "currency": "INR"}
    """
    invoice_id = str(uuid4())
    file_content: Optional[str] = None
    file_bytes: Optional[bytes] = None
    json_data: Optional[Dict[str, Any]] = None

    content_type = request.headers.get("content-type", "")

    user_id = _user.get("username", "demo_user") if _user else "demo_user"
    file_url: Optional[str] = None

    if file is not None:
        raw_bytes = await file.read()
        file_bytes = raw_bytes
        logger.info("[Routes] /process-invoice | mode=file filename=%s", file.filename)
        # Upload to Supabase Storage (non-blocking — failure won't abort processing)
        try:
            file_url = await upload_invoice_file(
                invoice_id=invoice_id,
                filename=file.filename or f"{invoice_id}.bin",
                file_bytes=raw_bytes,
            )
            if file_url:
                logger.info("[Routes] File stored in Supabase Storage: %s", file_url)
        except Exception as _upload_exc:  # noqa: BLE001
            logger.warning("[Routes] Storage upload failed (non-fatal): %s", _upload_exc)

    elif "application/json" in content_type:
        try:
            body: Dict[str, Any] = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body.")
        json_data = body
        logger.info("[Routes] /process-invoice | mode=json vendor=%s", body.get("vendor"))

    elif vendor or amount:
        json_data = {
            "vendor": vendor or "Unknown Vendor",
            "amount": amount or 0.0,
            "date": date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "currency": currency or "INR",
        }
        logger.info("[Routes] /process-invoice | mode=form vendor=%s", vendor)

    else:
        raise HTTPException(
            status_code=422,
            detail="Supply a file, JSON body, or form fields (vendor/amount/date).",
        )

    result: ProcessInvoiceResponse = await _orchestrator.process_invoice(
        invoice_id=invoice_id,
        file_content=file_content,
        file_bytes=file_bytes,
        json_data=json_data,
        user_id=user_id,
    )

    # Attach Storage URL to the saved invoice if a file was uploaded
    if file_url:
        await update_invoice(invoice_id, {"file_url": file_url}, user_id=user_id)

    await _broadcast_logs(invoice_id, result.logs)
    logger.info(
        "[Routes] /process-invoice done | id=%s decision=%s confidence=%.2f time=%.0fms",
        invoice_id, result.decision, result.confidence, result.processing_time_ms,
    )
    return result


# ══════════════════════════════════════════════════════════════
# APPROVE
# ══════════════════════════════════════════════════════════════

@router.post(
    "/approve",
    response_model=ApprovalResponse,
    tags=["Invoice"],
    summary="Submit human approval / rejection",
)
async def approve_invoice(
    body: ApprovalRequest,
    _user: Optional[Dict[str, Any]] = Depends(_optional_auth),
) -> ApprovalResponse:
    """
    Record a reviewer decision and close the feedback loop:
    confidence is recalculated and the memory graph is updated.
    """
    user_id = _user.get("username", "demo_user") if _user else "demo_user"
    existing = await get_invoice(body.invoice_id, user_id=user_id)
    if existing is None:
        existing = {
            "invoice_id": body.invoice_id,
            "confidence": 0.70,
            "vendor": "unknown",
            "amount": body.updated_amount or 0.0,
        }
        logger.warning("[Routes] /approve: invoice_id %s not found — using defaults", body.invoice_id)

    base_confidence: float = float(existing.get("confidence", 0.70))
    if body.approved:
        updated_confidence = min(1.0, base_confidence + 0.10)
        new_decision = "auto_execute"
        message = "Invoice approved. Confidence updated and memory graph adjusted."
    else:
        updated_confidence = max(0.0, base_confidence - 0.20)
        new_decision = "human_review"
        message = "Invoice rejected. Flagged for re-processing."

    updates: Dict[str, Any] = {
        "approved": body.approved,
        "reviewer_notes": body.reviewer_notes,
        "updated_confidence": updated_confidence,
        "decision": new_decision,
        "status": "approved" if body.approved else "rejected",
    }
    if body.updated_amount is not None:
        updates["amount"] = body.updated_amount

    await update_invoice(body.invoice_id, updates, user_id=user_id)
    await save_approval(
        body.invoice_id,
        {
            "approved": body.approved,
            "reviewer_notes": body.reviewer_notes,
            "updated_confidence": updated_confidence,
            "new_decision": new_decision,
            "user_id": user_id,
        },
        user_id=user_id
    )

    vendor = existing.get("vendor", "unknown")
    _memory_graph.update_vendor_data(
        vendor=vendor,
        amount=body.updated_amount or float(existing.get("amount", 0)),
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        anomaly=not body.approved,
    )
    logger.info(
        "[Routes] /approve | id=%s approved=%s new_confidence=%.2f",
        body.invoice_id, body.approved, updated_confidence,
    )

    return ApprovalResponse(
        invoice_id=body.invoice_id,
        approved=body.approved,
        updated_confidence=round(updated_confidence, 4),
        new_decision=new_decision,
        memory_updated=True,
        message=message,
    )


# ══════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════

_DEMO_DASHBOARD: Dict[str, Any] = {
    "processed": 1247,
    "anomalies": 23,
    "savings": 420000.0,
    "risk_score": 0.32,
    "auto_approved": 1089,
    "human_reviewed": 158,
    "avg_confidence": 0.87,
    "top_vendors": [
        {"vendor": "TechnoVendor Inc.", "count": 312, "total": 1_560_000.0},
        {"vendor": "CloudServe Ltd.", "count": 208, "total": 1_664_000.0},
        {"vendor": "DataPipe Co.", "count": 189, "total": 378_000.0},
        {"vendor": "Infosys", "count": 156, "total": 1_248_000.0},
        {"vendor": "Amazon Web Services", "count": 124, "total": 992_000.0},
    ],
}


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    tags=["Analytics"],
    summary="Aggregate KPI statistics",
)
async def dashboard(
    _user: Optional[Dict[str, Any]] = Depends(_optional_auth),
) -> DashboardResponse:
    """
    Live MongoDB aggregation when connected; falls back to realistic demo values
    when running in demo mode (no DB required).
    """
    user_id = _user.get("username", "demo_user") if _user else "demo_user"
    try:
        db_stats: Dict[str, Any] = await get_dashboard_stats(user_id=user_id)
    except Exception as exc:
        logger.warning("[Routes] /dashboard: DB stats error (%s) — using demo data.", exc)
        db_stats = {}

    def _pick(key: str, demo_val: Any) -> Any:
        live = db_stats.get(key)
        if live is not None and live != 0 and live != [] and live != 0.0:
            return live
        return demo_val

    stats = DashboardResponse(
        processed=_pick("processed", _DEMO_DASHBOARD["processed"]),
        anomalies=_pick("anomalies", _DEMO_DASHBOARD["anomalies"]),
        savings=_pick("savings", _DEMO_DASHBOARD["savings"]),
        risk_score=_pick("risk_score", _DEMO_DASHBOARD["risk_score"]),
        auto_approved=_pick("auto_approved", _DEMO_DASHBOARD["auto_approved"]),
        human_reviewed=_pick("human_reviewed", _DEMO_DASHBOARD["human_reviewed"]),
        avg_confidence=_pick("avg_confidence", _DEMO_DASHBOARD["avg_confidence"]),
        top_vendors=_pick("top_vendors", _DEMO_DASHBOARD["top_vendors"]),
    )
    logger.info("[Routes] /dashboard | demo_mode=%s processed=%d", is_demo_mode(), stats.processed)
    return stats


# ══════════════════════════════════════════════════════════════
# DEMO-RUN  ← Most important endpoint
# ══════════════════════════════════════════════════════════════

@router.post(
    "/demo-run",
    response_model=ProcessInvoiceResponse,
    tags=["Demo"],
    summary="Run a pre-built demo scenario (no auth required)",
)
async def demo_run() -> ProcessInvoiceResponse:
    """
    Runs a fixed invoice through the **full 19-step pipeline**:

    - **Vendor**: TechnoVendor Inc. (known avg ₹5,000)
    - **Amount**: ₹10,000 → **2× historical mean** → price-spike anomaly
    - Demonstrates confidence degradation, anomaly flagging, and decision routing.

    No authentication required. Works with zero configuration (demo mode).
    """
    invoice_id = str(uuid4())
    json_data = {
        "vendor": "TechnoVendor Inc.",
        "amount": 10000.0,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "currency": "INR",
    }
    logger.info("[Routes] /demo-run | invoice_id=%s", invoice_id)

    result: ProcessInvoiceResponse = await _orchestrator.process_invoice(
        invoice_id=invoice_id,
        json_data=json_data,
        user_id="demo_user",
    )
    await _broadcast_logs(invoice_id, result.logs)
    return result


# ══════════════════════════════════════════════════════════════
# LOGS
# ══════════════════════════════════════════════════════════════

@router.get(
    "/logs/{invoice_id}",
    response_model=List[LogEntry],
    tags=["Logs"],
    summary="Retrieve pipeline logs for an invoice",
)
async def get_invoice_logs(
    invoice_id: str,
    _user: Optional[Dict[str, Any]] = Depends(_optional_auth),
) -> List[LogEntry]:
    """Fetch all structured log entries for the given invoice_id."""
    user_id = _user.get("username", "demo_user") if _user else "demo_user"
    raw_logs = await get_logs_for_invoice(invoice_id, user_id=user_id)
    if not raw_logs:
        raise HTTPException(
            status_code=404,
            detail=f"No logs found for invoice_id '{invoice_id}'.",
        )

    entries: List[LogEntry] = []
    for log in raw_logs:
        try:
            entries.append(LogEntry(**log))
        except Exception:
            pass  # skip malformed log entries gracefully

    logger.info("[Routes] /logs/%s → %d entries", invoice_id, len(entries))
    return entries


# ══════════════════════════════════════════════════════════════
# ANALYSIS ENGINE (CORE PROCESSING API)
# ══════════════════════════════════════════════════════════════

from pydantic import BaseModel
class AnalysisRequest(BaseModel):
    document_id: str

@router.post(
    "/process-invoice-analysis",
    tags=["Analysis Engine"],
    summary="Core brain of AutoTwin AI to validate and decide"
)
async def analyze_invoice_endpoint(payload: AnalysisRequest) -> Dict[str, Any]:
    """
    Receives document_id, fetches extracted invoice data, computes confidence score,
    makes decision (auto approve/needs review), saves results, and sends WhatsApp notification.
    """
    try:
        from services.analysis_engine import process_invoice_analysis
        result = await process_invoice_analysis(payload.document_id)
        return result
    except Exception as e:
        logger.error(f"[Routes] Analysis engine failed for document_id {payload.document_id}: {e}")
        from services.analysis_engine import _fallback_message_generator, send_whatsapp_notification
        
        # Safe fallback response
        # Using a dummy user_phone retrieval since exception context might miss it
        # Safest is to just return failure gracefully.
        return {
            "status": "error",
            "message": str(e),
            "document_id": payload.document_id
        }




# ══════════════════════════════════════════════════════════════
# HEALTH
# ══════════════════════════════════════════════════════════════

@router.get("/health", tags=["System"], summary="Service health check")
async def health_check() -> Dict[str, Any]:
    """Returns service status, version, timestamp, and demo_mode flag."""
    from core.config import settings  # deferred to avoid circular import at module init
    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "demo_mode": is_demo_mode(),
    }


# ══════════════════════════════════════════════════════════════
# WEBSOCKET — Real-time log streaming
# ══════════════════════════════════════════════════════════════

@router.websocket("/ws/logs/{invoice_id}")
async def ws_invoice_logs(websocket: WebSocket, invoice_id: str) -> None:
    """
    Stream pipeline logs in real-time for *invoice_id*.

    Connect before or during pipeline execution.  Late connects receive a
    replay of already-emitted logs, then live updates until pipeline completes.

    JavaScript example::

        const ws = new WebSocket("ws://localhost:8000/api/ws/logs/INVOICE_ID");
        ws.onmessage = (e) => console.log(JSON.parse(e.data));
    """
    await websocket.accept()
    logger.info("[WS] Client connected | invoice_id=%s", invoice_id)

    if invoice_id not in _ws_connections:
        _ws_connections[invoice_id] = []
    _ws_connections[invoice_id].append(websocket)

    # Replay any existing logs for late connects
    for log in await get_logs_for_invoice(invoice_id):
        try:
            await websocket.send_text(json.dumps(log, default=str))
        except Exception:
            break

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=20.0)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"event": "ping"}))
    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected | invoice_id=%s", invoice_id)
    finally:
        conns = _ws_connections.get(invoice_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns:
            _ws_connections.pop(invoice_id, None)


# ──────────────────────────────────────────────────────────────
# Internal broadcast helper
# ──────────────────────────────────────────────────────────────

async def _broadcast_logs(invoice_id: str, log_entries: List[LogEntry]) -> None:
    """Push all log entries to connected WebSocket clients, then signal pipeline complete."""
    clients = list(_ws_connections.get(invoice_id, []))
    if not clients:
        return

    for entry in log_entries:
        payload = json.dumps(
            {
                "event": "log",
                "timestamp": entry.timestamp.isoformat(),
                "step": entry.step,
                "message": entry.message,
                "level": entry.level,
                "metadata": entry.metadata or {},
            },
            default=str,
        )
        dead: List[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.remove(ws)

    complete = json.dumps({"event": "pipeline_complete", "invoice_id": invoice_id})
    for ws in clients:
        try:
            await ws.send_text(complete)
        except Exception:
            pass
