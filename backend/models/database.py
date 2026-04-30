"""
models/database.py
──────────────────
AutoTwin AI — Async PostgreSQL (Supabase) database layer.

Maps strictly to the predefined Supabase schema with separated invoices and extracted_documents.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from collections import defaultdict
from typing import Any, Dict, List, Optional
import uuid

from core.config import settings

logger = logging.getLogger("autotwin_ai.database")

# ──────────────────────────────────────────────────────────────
# SQLAlchemy async engine
# ──────────────────────────────────────────────────────────────
_engine = None
_pg_available = False

def _build_asyncpg_url(raw: str) -> str:
    if raw.startswith("postgresql+asyncpg://"):
        return raw
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    return raw

if settings.DATABASE_URL:
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool
        _ASYNCPG_URL = _build_asyncpg_url(settings.DATABASE_URL)
        _engine = create_async_engine(
            _ASYNCPG_URL,
            poolclass=NullPool,
            connect_args={
                "command_timeout": 10,
                "statement_cache_size": 0,
                "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4()}__",
            },
        )
        _pg_available = True
        logger.info("PostgreSQL engine initialised → Supabase")
    except Exception as exc:  # noqa: BLE001
        logger.warning("PostgreSQL unavailable (%s) — running IN-MEMORY demo mode.", exc)
else:
    logger.warning("DATABASE_URL not set — running IN-MEMORY demo mode.")

# ──────────────────────────────────────────────────────────────
# In-memory fallback
# ──────────────────────────────────────────────────────────────
_invoices_store: Dict[str, dict] = {}
_vendors_store: Dict[str, List[dict]] = defaultdict(list)
_approvals_store: Dict[str, dict] = {}
_logs_store: Dict[str, List[dict]] = defaultdict(list)

# ──────────────────────────────────────────────────────────────
# Low-level DB access
# ──────────────────────────────────────────────────────────────

async def _execute(sql: str, params: dict | None = None) -> List[dict]:
    if not _pg_available or _engine is None:
        return []
    from sqlalchemy import text
    try:
        async with _engine.begin() as conn:
            result = await conn.execute(text(sql), params or {})
            if result.returns_rows:
                return [dict(r) for r in result.mappings().all()]
            return []
    except Exception as exc:  # noqa: BLE001
        logger.error("DB execute error: %s | sql=%s", exc, sql[:120])
        raise

async def get_db():
    yield _engine if _pg_available else None

# ──────────────────────────────────────────────────────────────
# Invoices & Extracted Documents
# ──────────────────────────────────────────────────────────────

async def get_invoice(invoice_id: str, user_id: str = "demo_user") -> Optional[dict]:
    if _pg_available:
        rows = await _execute(
            """
            SELECT i.*, e.anomaly, e.confidence, e.decision, e.explanation, 
                   e.anomaly_details, e.risk_score, e.confidence_breakdown, e.logs
            FROM invoices i
            LEFT JOIN extracted_documents e ON i.id::text = e.invoice_id AND e.user_id = :user_id
            WHERE i.id = :id AND i.user_id = :user_id
            LIMIT 1
            """,
            {"id": str(invoice_id), "user_id": user_id},
        )
        if rows:
            doc = rows[0]
            # Convert UUIDs back to string for backend consistency
            if "id" in doc and doc["id"]:
                doc["id"] = str(doc["id"])
            
            # Map back to unified schema internally so we don't break existing Python logic
            doc["invoice_id"] = doc["id"]
            return doc
        return None
    return _invoices_store.get(str(invoice_id))

async def save_invoice(data: dict, user_id: str = "demo_user") -> str:
    original_id = data.get("invoice_id")
    # If ID isn't a valid UUID string, forcefully regenerate to avoid Postgres errors
    try:
        uuid.UUID(str(original_id))
        invoice_uuid = str(original_id)
    except:
        invoice_uuid = str(uuid.uuid4())

    now = datetime.now(timezone.utc).isoformat()
    uid = data.get("user_id", user_id)

    if _pg_available and _engine is not None:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker
        
        AsyncSessionLocal = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
        async with AsyncSessionLocal() as session:
            async with session.begin(): # Transaction block
                try:
                    # 1. UPSERT invoices table
                    await session.execute(
                        text("""
                        INSERT INTO invoices (
                            id, user_id, vendor, invoice_no, amount, currency, 
                            status, confidence, category, file_url, created_at
                        ) VALUES (
                            :id, :user_id, :vendor, :invoice_no, :amount, :currency,
                            :status, :confidence, :category, :file_url, now()
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            vendor = EXCLUDED.vendor,
                            invoice_no = EXCLUDED.invoice_no,
                            amount = EXCLUDED.amount,
                            currency = EXCLUDED.currency,
                            status = EXCLUDED.status,
                            confidence = EXCLUDED.confidence,
                            category = EXCLUDED.category,
                            file_url = EXCLUDED.file_url
                        """),
                        {
                            "id": invoice_uuid,
                            "user_id": uid,
                            "vendor": data.get("vendor", "Unknown"),
                            "invoice_no": data.get("invoice_no") or invoice_uuid[:8],
                            "amount": float(data.get("amount", 0.0)),
                            "currency": data.get("currency", "INR"),
                            "status": data.get("status", "pending"),
                            "confidence": float(data.get("confidence", 0)),
                            "category": data.get("category"),
                            "file_url": data.get("file_url")
                        }
                    )

                    # 2. UPSERT extracted_documents
                    # Format JSON fields
                    anomaly_details = json.dumps(data.get("anomaly_details") or {})
                    confidence_breakdown = json.dumps(data.get("confidence_breakdown") or {})
                    logs_json = json.dumps(data.get("logs") or [])

                    await session.execute(
                        text("""
                        INSERT INTO extracted_documents (
                            user_id, invoice_id, vendor, amount, date,
                            anomaly, confidence, status, decision, explanation,
                            anomaly_details, confidence_breakdown, logs,
                            risk_score, processing_time_ms, file_url, created_at
                        ) VALUES (
                            :user_id, :invoice_id, :vendor, :amount, :date,
                            :anomaly, :confidence, :status, :decision, :explanation,
                            CAST(:anomaly_details AS jsonb), CAST(:confidence_breakdown AS jsonb), CAST(:logs AS jsonb),
                            :risk_score, :processing_time_ms, :file_url, now()
                        )
                        """),
                        {
                            "user_id": uid,
                            "invoice_id": invoice_uuid,
                            "vendor": data.get("vendor", "Unknown"),
                            "amount": float(data.get("amount", 0.0)),
                            "date": str(data.get("date", "")),
                            "anomaly": bool(data.get("anomaly", False)),
                            "confidence": float(data.get("confidence", 0)),
                            "status": data.get("status", "processed"),
                            "decision": data.get("decision", "pending"),
                            "explanation": data.get("explanation"),
                            "anomaly_details": anomaly_details,
                            "confidence_breakdown": confidence_breakdown,
                            "logs": logs_json,
                            "risk_score": float(data.get("risk_score", 0.0)),
                            "processing_time_ms": float(data.get("processing_time_ms") or 0.0),
                            "file_url": data.get("file_url")
                        }
                    )
                except Exception as exc:
                    logger.error("Transaction failed during save_invoice: %s", exc)
                    raise
    else:
        _invoices_store[invoice_uuid] = {**data, "invoice_id": invoice_uuid, "created_at": now}

    logger.debug("Invoice saved: %s", invoice_uuid)
    return invoice_uuid

async def update_invoice(invoice_id: str, updates: dict, user_id: str = "demo_user") -> None:
    invoice_uuid = str(invoice_id)
    uid = updates.get("user_id", user_id)
    
    if _pg_available:
        try:
            from sqlalchemy import text
            from sqlalchemy.ext.asyncio import AsyncSession
            from sqlalchemy.orm import sessionmaker
            
            AsyncSessionLocal = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    # Update invoices table if fields match
                    invoices_allowed = {"vendor", "invoice_no", "amount", "currency", "status", "confidence", "category", "file_url"}
                    inv_updates = {k: v for k, v in updates.items() if k in invoices_allowed}
                    if inv_updates:
                        set_clause = ", ".join(f"{k} = :{k}" for k in inv_updates)
                        inv_updates["id"] = invoice_uuid
                        inv_updates["user_id"] = uid
                        await session.execute(
                            text(f"UPDATE invoices SET {set_clause} WHERE id = :id AND user_id = :user_id"),
                            inv_updates
                        )

                    # Update extracted_documents if fields match
                    extracted_allowed = {"anomaly", "confidence", "status", "decision", "explanation", "risk_score", "file_url"}
                    ext_updates = {k: v for k, v in updates.items() if k in extracted_allowed}
                    if ext_updates:
                        ext_set = ", ".join(f"{k} = :{k}" for k in ext_updates)
                        ext_updates["invoice_id"] = invoice_uuid
                        ext_updates["user_id"] = uid
                        await session.execute(
                            text(f"UPDATE extracted_documents SET {ext_set} WHERE invoice_id = :invoice_id AND user_id = :user_id"),
                            ext_updates
                        )
        except Exception as e:
            logger.error("Failed to update invoice %s: %s", invoice_uuid, e)
            raise
    else:
        if invoice_uuid in _invoices_store:
            _invoices_store[invoice_uuid].update({**updates, "updated_at": datetime.now(timezone.utc).isoformat()})

# ──────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────

async def save_log_entry(invoice_id: str, log_entry: dict, user_id: str = "demo_user") -> None:
    invoice_uuid = str(invoice_id)
    uid = log_entry.get("user_id", user_id)

    if _pg_available:
        details_json = json.dumps(log_entry.get("metadata") or {})
        await _execute(
            """
            INSERT INTO agent_logs (invoice_id, user_id, agent, action, result, details, created_at)
            VALUES (:invoice_id, :user_id, :agent, :action, :result, CAST(:details AS jsonb), now())
            """,
            {
                "invoice_id": invoice_uuid,
                "user_id": uid,
                "agent": log_entry.get("step", "system"),
                "action": log_entry.get("message", ""),
                "result": log_entry.get("level", "info"),
                "details": details_json,
            },
        )
    else:
        _logs_store[invoice_uuid].append({**log_entry, "invoice_id": invoice_uuid})

async def get_logs_for_invoice(invoice_id: str, user_id: str = "demo_user") -> List[dict]:
    invoice_uuid = str(invoice_id)
    if _pg_available:
        rows = await _execute(
            "SELECT * FROM agent_logs WHERE invoice_id = :id AND user_id = :uid ORDER BY created_at ASC LIMIT 500",
            {"id": invoice_uuid, "uid": user_id},
        )
        # Map back to old schema for runtime consistency
        mapped = []
        import json
        for r in rows:
            raw_details = r.get("details")
            if isinstance(raw_details, str):
                try:
                    metadata = json.loads(raw_details)
                except Exception:
                    metadata = {}
            elif isinstance(raw_details, dict):
                metadata = raw_details
            else:
                metadata = {}
                
            mapped.append({
                "timestamp": r.get("created_at"),
                "step": r.get("agent"),
                "message": r.get("action"),
                "level": r.get("result"),
                "metadata": metadata
            })
        return mapped
    return list(_logs_store.get(invoice_uuid, []))

# ──────────────────────────────────────────────────────────────
# Vendor History & Transactions
# ──────────────────────────────────────────────────────────────

async def get_vendor_history(vendor_name: str, user_id: str = "demo_user") -> List[dict]:
    if _pg_available:
        return await _execute(
            "SELECT * FROM transactions WHERE vendor = :vendor AND user_id = :uid ORDER BY date DESC LIMIT 100",
            {"vendor": vendor_name, "uid": user_id},
        )
    return list(_vendors_store.get(vendor_name, []))

async def update_vendor_history(vendor_name: str, invoice_data: dict, user_id: str = "demo_user") -> None:
    uid = invoice_data.get("user_id", user_id)
    raw_date = invoice_data.get("date")
    parsed_date = datetime.now(timezone.utc)
    # The transactions table expects a real timestamp. If the string from VisionAgent fails
    # to parse gracefully, we fall back to the current time rather than crash asyncpg.
    if raw_date and isinstance(raw_date, str):
        try:
            # We can try basic ISO parsing first
            parsed_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except ValueError:
            parsed_date = datetime.now(timezone.utc)
            
    if _pg_available:
        await _execute(
            """
            INSERT INTO transactions (user_id, category, amount, vendor, date, anomaly_score, created_at)
            VALUES (:user_id, :category, :amount, :vendor, :date, :anomaly_score, now())
            """,
            {
                "user_id": uid,
                "category": invoice_data.get("category", "General"),
                "amount": float(invoice_data.get("amount", 0)),
                "vendor": vendor_name,
                "date": parsed_date,
                "anomaly_score": float(invoice_data.get("risk_score", 0.0)),
            },
        )
    else:
        _vendors_store[vendor_name].append({**invoice_data, "vendor": vendor_name})

# ──────────────────────────────────────────────────────────────
# Approvals
# ──────────────────────────────────────────────────────────────

async def save_approval(invoice_id: str, approval_data: dict, user_id: str = "demo_user") -> None:
    invoice_uuid = str(invoice_id)
    uid = approval_data.get("user_id", user_id)
    
    if _pg_available:
        status_val = "approved" if approval_data.get("approved") else "rejected"
        await _execute(
            """
            INSERT INTO approvals (invoice_id, user_id, status, notes, resolved_at)
            VALUES (:invoice_id, :user_id, :status, :notes, now())
            """,
            {
                "invoice_id": invoice_uuid,
                "user_id": uid,
                "status": status_val,
                "notes": approval_data.get("reviewer_notes", ""),
            },
        )
        
        # We must also update the invoices + extracted_documents statuses
        await update_invoice(
            invoice_uuid, 
            {"status": status_val, "decision": "human_reviewed"}, 
            user_id=uid
        )
    else:
        _approvals_store[invoice_uuid] = {**approval_data, "invoice_id": invoice_uuid}

async def get_approval(invoice_id: str, user_id: str = "demo_user") -> Optional[dict]:
    invoice_uuid = str(invoice_id)
    if _pg_available:
        rows = await _execute(
            "SELECT * FROM approvals WHERE invoice_id = :id AND user_id = :uid LIMIT 1",
            {"id": invoice_uuid, "uid": user_id},
        )
        return rows[0] if rows else None
    return _approvals_store.get(invoice_uuid)

# ──────────────────────────────────────────────────────────────
# Dashboard
# ──────────────────────────────────────────────────────────────

async def get_dashboard_stats(user_id: str = "demo_user") -> dict:
    if _pg_available:
        agg = await _execute(
            """
            SELECT
                COUNT(*)::int AS processed,
                SUM(CASE WHEN e.anomaly THEN 1 ELSE 0 END)::int AS anomalies,
                SUM(CASE WHEN e.decision='auto_execute' THEN 1 ELSE 0 END)::int AS auto_approved,
                SUM(CASE WHEN e.decision='human_review' THEN 1 ELSE 0 END)::int AS human_reviewed,
                COALESCE(AVG(e.confidence), 0) AS avg_confidence,
                COALESCE(AVG(e.risk_score), 0) AS risk_score
            FROM invoices i
            JOIN extracted_documents e ON i.id::text = e.invoice_id
            WHERE i.user_id = :uid
            """,
            {"uid": user_id}
        )
        base = dict(agg[0]) if agg else {}

        top = await _execute(
            """
            SELECT i.vendor, COUNT(*)::int AS count, COALESCE(SUM(i.amount),0) AS total
            FROM invoices i
            WHERE i.user_id = :uid
            GROUP BY i.vendor
            ORDER BY count DESC
            LIMIT 5
            """,
            {"uid": user_id}
        )
        base["top_vendors"] = [dict(r) for r in top]
        base["savings"] = 0.0 # savings dropped from new schema, fallback to 0
        
        # ensure defaults
        for k in ("processed", "anomalies", "auto_approved", "human_reviewed"):
            base[k] = int(base.get(k) or 0)
        for k in ("risk_score", "avg_confidence"):
            base[k] = float(base.get(k) or 0.0)
            
        return base

    # ── In-memory fallback (stripped for brevity here but handled safely) ──
    records = list(_invoices_store.values())
    return {
        "processed": len(records),
        "anomalies": sum(1 for r in records if r.get("anomaly")),
        "savings": 0.0,
        "risk_score": 0.0,
        "auto_approved": sum(1 for r in records if r.get("decision") == "auto_execute"),
        "human_reviewed": sum(1 for r in records if r.get("decision") == "human_review"),
        "avg_confidence": 0.0,
        "top_vendors": [],
    }

def is_demo_mode() -> bool:
    return not _pg_available

def get_memory_store_snapshot() -> dict:
    return {
        "invoices": dict(_invoices_store),
        "vendors": dict(_vendors_store),
        "approvals": dict(_approvals_store),
        "logs": dict(_logs_store),
    }

# ──────────────────────────────────────────────────────────────
# Core Analysis Engine DB Access
# ──────────────────────────────────────────────────────────────

async def analysis_check_idempotency(document_id: str) -> bool:
    if _pg_available:
        rows = await _execute(
            "SELECT 1 FROM invoice_analysis WHERE document_id = :doc_id LIMIT 1",
            {"doc_id": document_id}
        )
        return len(rows) > 0
    return False

async def analysis_get_extracted_document(document_id: str) -> Optional[dict]:
    if _pg_available:
        rows = await _execute(
            "SELECT * FROM extracted_documents WHERE id = :doc_id LIMIT 1",
            {"doc_id": document_id}
        )
        return rows[0] if rows else None
    return None

async def analysis_get_document_by_invoice_id(invoice_id: str) -> Optional[dict]:
    """Fetch extracted_document by the orchestrator's invoice UUID (invoice_id column)."""
    if _pg_available:
        rows = await _execute(
            "SELECT * FROM extracted_documents WHERE invoice_id = :inv_id ORDER BY created_at DESC LIMIT 1",
            {"inv_id": invoice_id}
        )
        if rows:
            row = rows[0]
            # Expose the document's own primary key as 'document_id'
            row["document_id"] = str(row.get("id", ""))
            return row
    return None

async def analysis_get_purchase_order(po_number: str) -> Optional[dict]:
    if _pg_available:
        rows = await _execute(
            "SELECT * FROM purchase_orders WHERE po_number = :po LIMIT 1",
            {"po": po_number}
        )
        return rows[0] if rows else None
    return None

async def analysis_get_vendor_invoices(vendor: str, user_id: str) -> List[dict]:
    if _pg_available:
        return await _execute(
            "SELECT * FROM invoices WHERE vendor = :vendor AND user_id = :uid",
            {"vendor": vendor, "uid": user_id}
        )
    return []

async def analysis_get_user_phone(user_id: str) -> Optional[str]:
    if _pg_available:
        try:
            import uuid
            uuid.UUID(user_id)
            rows = await _execute(
                "SELECT whatsapp_number FROM users WHERE id = :uid LIMIT 1",
                {"uid": user_id}
            )
            if rows:
                # Normalise — strip non-digits so the API gets a clean E.164 number
                raw = rows[0].get("whatsapp_number") or ""
                digits = "".join(filter(str.isdigit, raw))
                return digits if digits else None
            return None
        except ValueError:
            return None
    return None

async def analysis_check_duplicate_invoice(invoice_id: str, vendor: str, amount: float) -> bool:
    if _pg_available:
        # strict duplicate check
        rows = await _execute(
            "SELECT 1 FROM invoices WHERE invoice_no = :inv_id OR (vendor = :vendor AND amount = :amount) LIMIT 1",
            {"inv_id": invoice_id, "vendor": vendor, "amount": amount}
        )
        return len(rows) > 0
    return False

async def analysis_save_results(data: dict) -> None:
    if _pg_available:
        flags_json = json.dumps(data.get("flags", []))
        await _execute(
            """
            INSERT INTO invoice_analysis (document_id, user_id, confidence_score, status, flags, processed_at)
            VALUES (:doc_id, :uid, :score, :status, CAST(:flags AS jsonb), now())
            """,
            {
                "doc_id": data["document_id"],
                "uid": data["user_id"],
                "score": data["confidence_score"],
                "status": data["status"],
                "flags": flags_json,
            }
        )
        
        # Also update the document status to processed
        await _execute(
            "UPDATE extracted_documents SET status = 'processed' WHERE id = :doc_id",
            {"doc_id": data["document_id"]}
        )

