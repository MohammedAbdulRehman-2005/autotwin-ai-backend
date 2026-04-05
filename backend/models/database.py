"""
models/database.py
──────────────────
Async MongoDB (Motor) database layer with a transparent in-memory fallback.
If Motor cannot connect within CONNECT_TIMEOUT_MS, every function silently
falls back to module-level dicts so the demo can run without a live DB.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.config import settings

logger = logging.getLogger("autotwin_ai.database")

# ──────────────────────────────────────────────────────────────
# Motor client – imported lazily so the app boots even without
# the package installed (demo / test environments).
# ──────────────────────────────────────────────────────────────
_motor_available = False
_client = None
_db = None

CONNECT_TIMEOUT_MS = 3_000   # 3 s – fast-fail for demo mode

try:
    from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase  # type: ignore

    _client = AsyncIOMotorClient(
        settings.MONGODB_URL,
        serverSelectionTimeoutMS=CONNECT_TIMEOUT_MS,
    )
    _db = _client[settings.DATABASE_NAME]
    _motor_available = True
    logger.info("Motor client initialised → %s / %s", settings.MONGODB_URL, settings.DATABASE_NAME)
except Exception as exc:  # noqa: BLE001
    logger.warning("Motor unavailable (%s) – running in IN-MEMORY demo mode.", exc)


# ──────────────────────────────────────────────────────────────
# In-memory fallback stores
# ──────────────────────────────────────────────────────────────
_invoices_store: Dict[str, dict] = {}
_vendors_store: Dict[str, List[dict]] = defaultdict(list)
_approvals_store: Dict[str, dict] = {}
_logs_store: Dict[str, List[dict]] = defaultdict(list)


# ══════════════════════════════════════════════════════════════
# Collection helpers
# ══════════════════════════════════════════════════════════════

def _invoices_col():
    """Return the Motor invoices collection or None in fallback mode."""
    return _db["invoices"] if _motor_available and _db is not None else None


def _vendors_col():
    return _db["vendors"] if _motor_available and _db is not None else None


def _approvals_col():
    return _db["approvals"] if _motor_available and _db is not None else None


def _logs_col():
    return _db["logs"] if _motor_available and _db is not None else None


# ══════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════

async def get_db():
    """
    Yield the Motor database handle.
    Falls back to None (handled by every caller) when not connected.
    Intended for use as a FastAPI dependency.
    """
    yield _db if _motor_available else None


# ──────────────────────────────────────────────────────────
# Invoice CRUD
# ──────────────────────────────────────────────────────────

async def get_invoice(invoice_id: str) -> Optional[dict]:
    """Fetch a single invoice by its ID."""
    col = _invoices_col()
    if col is not None:
        doc = await col.find_one({"invoice_id": invoice_id}, {"_id": 0})
        return doc
    return _invoices_store.get(invoice_id)


async def save_invoice(data: dict) -> str:
    """
    Persist a new invoice document.
    Returns the invoice_id on success.
    """
    invoice_id: str = data["invoice_id"]
    col = _invoices_col()
    if col is not None:
        await col.update_one(
            {"invoice_id": invoice_id},
            {"$set": {**data, "created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
    else:
        _invoices_store[invoice_id] = {**data, "created_at": datetime.now(timezone.utc).isoformat()}
    logger.debug("Invoice saved: %s", invoice_id)
    return invoice_id


async def update_invoice(invoice_id: str, updates: dict) -> None:
    """Merge *updates* into an existing invoice document."""
    col = _invoices_col()
    if col is not None:
        await col.update_one(
            {"invoice_id": invoice_id},
            {"$set": {**updates, "updated_at": datetime.now(timezone.utc)}},
        )
    else:
        if invoice_id in _invoices_store:
            _invoices_store[invoice_id].update(
                {**updates, "updated_at": datetime.now(timezone.utc).isoformat()}
            )
        else:
            logger.warning("update_invoice: %s not found in memory store.", invoice_id)
    logger.debug("Invoice updated: %s | fields: %s", invoice_id, list(updates.keys()))


# ──────────────────────────────────────────────────────────
# Vendor history
# ──────────────────────────────────────────────────────────

async def get_vendor_history(vendor_name: str) -> List[dict]:
    """Return all historical invoice records for *vendor_name*."""
    col = _vendors_col()
    if col is not None:
        cursor = col.find({"vendor": vendor_name}, {"_id": 0}).sort("date", -1).limit(100)
        return await cursor.to_list(length=100)
    return list(_vendors_store.get(vendor_name, []))


async def update_vendor_history(vendor_name: str, invoice_data: dict) -> None:
    """Append *invoice_data* to the vendor's history."""
    col = _vendors_col()
    record = {**invoice_data, "vendor": vendor_name, "recorded_at": datetime.now(timezone.utc).isoformat()}
    if col is not None:
        await col.insert_one(record)
    else:
        _vendors_store[vendor_name].append(record)
    logger.debug("Vendor history updated: %s", vendor_name)


# ──────────────────────────────────────────────────────────
# Dashboard stats
# ──────────────────────────────────────────────────────────

async def get_dashboard_stats() -> dict:
    """
    Aggregate KPIs from stored invoices.
    Works in both MongoDB and in-memory modes.
    """
    col = _invoices_col()

    if col is not None:
        pipeline = [
            {
                "$group": {
                    "_id": None,
                    "processed": {"$sum": 1},
                    "anomalies": {"$sum": {"$cond": ["$anomaly", 1, 0]}},
                    "savings": {"$sum": {"$ifNull": ["$savings", 0]}},
                    "auto_approved": {
                        "$sum": {"$cond": [{"$eq": ["$decision", "auto_execute"]}, 1, 0]}
                    },
                    "human_reviewed": {
                        "$sum": {
                            "$cond": [{"$eq": ["$decision", "human_review"]}, 1, 0]
                        }
                    },
                    "avg_confidence": {"$avg": "$confidence"},
                    "avg_risk": {"$avg": "$risk_score"},
                }
            }
        ]
        result = await col.aggregate(pipeline).to_list(length=1)
        base: dict = result[0] if result else {}
        base.pop("_id", None)

        # top vendors
        vendor_pipeline = [
            {"$group": {"_id": "$vendor", "count": {"$sum": 1}, "total": {"$sum": "$amount"}}},
            {"$sort": {"count": -1}},
            {"$limit": 5},
            {"$project": {"_id": 0, "vendor": "$_id", "count": 1, "total": 1}},
        ]
        top_vendors = await col.aggregate(vendor_pipeline).to_list(length=5)
        base["top_vendors"] = top_vendors
        return _fill_dashboard_defaults(base)

    # ── In-memory aggregation ──
    records = list(_invoices_store.values())
    if not records:
        return _fill_dashboard_defaults({})

    processed = len(records)
    anomalies = sum(1 for r in records if r.get("anomaly"))
    savings = sum(r.get("savings", 0.0) for r in records)
    auto_approved = sum(1 for r in records if r.get("decision") == "auto_execute")
    human_reviewed = sum(1 for r in records if r.get("decision") == "human_review")
    confidences = [r["confidence"] for r in records if "confidence" in r]
    risks = [r["risk_score"] for r in records if "risk_score" in r]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    avg_risk = sum(risks) / len(risks) if risks else 0.0

    # top vendors
    vendor_counts: Dict[str, Dict[str, Any]] = {}
    for r in records:
        v = r.get("vendor", "unknown")
        if v not in vendor_counts:
            vendor_counts[v] = {"vendor": v, "count": 0, "total": 0.0}
        vendor_counts[v]["count"] += 1
        vendor_counts[v]["total"] += r.get("amount", 0.0)
    top_vendors = sorted(vendor_counts.values(), key=lambda x: x["count"], reverse=True)[:5]

    return {
        "processed": processed,
        "anomalies": anomalies,
        "savings": round(savings, 2),
        "risk_score": round(avg_risk, 4),
        "auto_approved": auto_approved,
        "human_reviewed": human_reviewed,
        "avg_confidence": round(avg_confidence, 4),
        "top_vendors": top_vendors,
    }


def _fill_dashboard_defaults(data: dict) -> dict:
    """Ensure all dashboard keys are present with safe defaults."""
    defaults = {
        "processed": 0,
        "anomalies": 0,
        "savings": 0.0,
        "risk_score": 0.0,
        "auto_approved": 0,
        "human_reviewed": 0,
        "avg_confidence": 0.0,
        "top_vendors": [],
    }
    defaults.update(data)
    return defaults


# ──────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────

async def save_log_entry(invoice_id: str, log_entry: dict) -> None:
    """Append a structured log entry for the given invoice."""
    col = _logs_col()
    record = {**log_entry, "invoice_id": invoice_id}
    if col is not None:
        await col.insert_one(record)
    else:
        _logs_store[invoice_id].append(record)
    logger.debug("Log saved for invoice %s | step=%s", invoice_id, log_entry.get("step"))


async def get_logs_for_invoice(invoice_id: str) -> List[dict]:
    """Retrieve all log entries for a given invoice."""
    col = _logs_col()
    if col is not None:
        cursor = col.find({"invoice_id": invoice_id}, {"_id": 0}).sort("timestamp", 1)
        return await cursor.to_list(length=500)
    return list(_logs_store.get(invoice_id, []))


# ──────────────────────────────────────────────────────────
# Approval records
# ──────────────────────────────────────────────────────────

async def save_approval(invoice_id: str, approval_data: dict) -> None:
    """Persist an approval decision."""
    col = _approvals_col()
    record = {**approval_data, "invoice_id": invoice_id, "approved_at": datetime.now(timezone.utc).isoformat()}
    if col is not None:
        await col.update_one(
            {"invoice_id": invoice_id},
            {"$set": record},
            upsert=True,
        )
    else:
        _approvals_store[invoice_id] = record
    logger.debug("Approval saved: %s | approved=%s", invoice_id, approval_data.get("approved"))


async def get_approval(invoice_id: str) -> Optional[dict]:
    """Retrieve the approval record for an invoice."""
    col = _approvals_col()
    if col is not None:
        return await col.find_one({"invoice_id": invoice_id}, {"_id": 0})
    return _approvals_store.get(invoice_id)


# ──────────────────────────────────────────────────────────
# Utility
# ──────────────────────────────────────────────────────────

def is_demo_mode() -> bool:
    """Returns True when the system is operating without a live MongoDB."""
    return not _motor_available


def get_memory_store_snapshot() -> dict:
    """
    Debug helper – returns a snapshot of all in-memory stores.
    Should NOT be exposed via a public API endpoint in production.
    """
    return {
        "invoices": dict(_invoices_store),
        "vendors": dict(_vendors_store),
        "approvals": dict(_approvals_store),
        "logs": dict(_logs_store),
    }
