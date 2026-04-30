"""
services/whatsapp_bot.py
────────────────────────
Handles incoming WhatsApp messages: intent routing, data fetching,
AI response generation, and WhatsApp invoice intake (image/document).
"""

import logging
import json
import re
from datetime import datetime, timezone
from typing import Optional
import httpx

from core.config import settings
from models.supabase_client import get_supabase_client
from models.database import get_user_id_by_phone
from services.whatsapp_client import (
    send_whatsapp_message,
    send_interactive_list,
    send_interactive_buttons,
    download_whatsapp_media,
)
from services.category_classifier import classify_invoice_category

logger = logging.getLogger("autotwin_ai.whatsapp_bot")


# ──────────────────────────────────────────────────────────────
# Menu
# ──────────────────────────────────────────────────────────────

async def send_menu(to_phone: str) -> None:
    """Send the main menu as an interactive list."""
    await send_interactive_list(
        to_phone=to_phone,
        header="AutoTwin AI",
        body="What would you like to check? Select a report below.",
        footer="You can also send an invoice image to process it instantly.",
        button_label="View Reports",
        sections=[
            {
                "title": "Financial Reports",
                "rows": [
                    {
                        "id": "invoice_summary",
                        "title": "Invoice Summary",
                        "description": "Today's invoices, anomalies & confidence",
                    },
                    {
                        "id": "payment_status",
                        "title": "Payment Status",
                        "description": "Recent payments and approvals",
                    },
                    {
                        "id": "daily_report",
                        "title": "Daily Report",
                        "description": "Full end-of-day financial overview",
                    },
                ],
            },
            {
                "title": "Risk & Reviews",
                "rows": [
                    {
                        "id": "anomaly_details",
                        "title": "Anomaly Details",
                        "description": "Flagged or suspicious invoices",
                    },
                    {
                        "id": "pending_review",
                        "title": "Pending Reviews",
                        "description": "Invoices awaiting your approval",
                    },
                    {
                        "id": "cash_flow",
                        "title": "Cash Flow",
                        "description": "Spend breakdown by category",
                    },
                ],
            },
        ],
    )


# ──────────────────────────────────────────────────────────────
# Intent Detection
# ──────────────────────────────────────────────────────────────

# IDs that arrive verbatim from interactive list/button replies
_DIRECT_INTENTS = {
    "invoice_summary", "payment_status", "daily_report",
    "anomaly_details", "pending_review", "cash_flow",
}


def detect_intent(text: str) -> str:
    t = text.lower().strip()

    if t in {"hi", "hello", "hey", "namaste", "hii", "yo", "start", "menu"}:
        return "menu"

    if t in _DIRECT_INTENTS:
        return t

    if re.search(r"invoice\s*(summary|status|count|list|today)", t):
        return "invoice_summary"
    if re.search(r"payment\s*(status|done|made|completed|list)|paid|transactions", t):
        return "payment_status"
    if re.search(r"daily\s*report|full report|overview", t):
        return "daily_report"
    if re.search(r"anomal|fraud|suspicious|flagged|risk", t):
        return "anomaly_details"
    if re.search(r"pending|review|waiting|needs.review|human review", t):
        return "pending_review"
    if re.search(r"cash\s*flow|cashflow|money flow|spend|expenditure|expense", t):
        return "cash_flow"

    # Loose fallbacks
    if re.search(r"invoice|bill|document|vendor|processed", t):
        return "invoice_summary"
    if re.search(r"payment|pay|transaction|transfer", t):
        return "payment_status"
    if re.search(r"anomal|warning|alert|flag", t):
        return "anomaly_details"
    if re.search(r"pending|review|check|approve", t):
        return "pending_review"

    return "unknown"


# ──────────────────────────────────────────────────────────────
# Data Fetchers
# ──────────────────────────────────────────────────────────────

def _today_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


async def fetch_invoice_summary() -> dict:
    supabase = get_supabase_client()
    try:
        res = supabase.table("extracted_documents").select(
            "id, vendor, amount, anomaly, decision, confidence, category, created_at"
        ).gte("created_at", _today_iso()).order("created_at", desc=True).execute()

        docs = res.data or []
        total = len(docs)
        anomalies = sum(1 for d in docs if d.get("anomaly"))
        auto_approved = sum(1 for d in docs if d.get("decision") == "auto_execute")
        pending = sum(1 for d in docs if d.get("decision") == "human_review")
        avg_conf = (sum(d.get("confidence", 0) for d in docs) / total * 100) if total else 0

        vendor_map: dict = {}
        for d in docs:
            v = d.get("vendor", "Unknown")
            vendor_map[v] = vendor_map.get(v, 0) + 1
        top_vendors = [
            f"{v} ({c})"
            for v, c in sorted(vendor_map.items(), key=lambda x: x[1], reverse=True)[:3]
        ]

        return {
            "intent": "invoice_summary",
            "total_invoices": total,
            "anomalies": anomalies,
            "auto_approved": auto_approved,
            "pending_review": pending,
            "avg_confidence_pct": f"{avg_conf:.1f}",
            "top_vendors": top_vendors,
        }
    except Exception as e:
        logger.error(f"fetch_invoice_summary error: {e}")
        return {}


async def fetch_anomaly_details() -> dict:
    supabase = get_supabase_client()
    try:
        res = supabase.table("extracted_documents").select(
            "vendor, amount, confidence, explanation, created_at"
        ).eq("anomaly", True).gte("created_at", _today_iso()).order("created_at", desc=True).limit(10).execute()

        docs = res.data or []
        anomalies = [
            {
                "vendor": d.get("vendor"),
                "amount": d.get("amount"),
                "confidence": f"{(d.get('confidence') or 0) * 100:.0f}%",
                "reason": d.get("explanation") or "No explanation provided",
            }
            for d in docs
        ]
        return {"intent": "anomaly_details", "count": len(docs), "anomalies": anomalies}
    except Exception as e:
        logger.error(f"fetch_anomaly_details error: {e}")
        return {}


async def fetch_cash_flow_data() -> dict:
    supabase = get_supabase_client()
    try:
        res = supabase.table("transactions").select(
            "amount, vendor, category, date"
        ).gte("created_at", _today_iso()).order("date", desc=True).execute()

        txns = res.data or []
        total = sum(t.get("amount") or 0 for t in txns)
        by_category: dict = {}
        for t in txns:
            cat = t.get("category") or "Other"
            by_category[cat] = by_category.get(cat, 0) + (t.get("amount") or 0)

        top_cats = [
            f"{c}: ₹{a:,.0f}"
            for c, a in sorted(by_category.items(), key=lambda x: x[1], reverse=True)[:5]
        ]
        return {
            "intent": "cash_flow",
            "total_transactions": len(txns),
            "total_amount_inr": f"{total:,.2f}",
            "avg_transaction": f"{total / len(txns):,.2f}" if txns else "0.00",
            "category_breakdown": top_cats,
        }
    except Exception as e:
        logger.error(f"fetch_cash_flow_data error: {e}")
        return {}


async def fetch_payment_status() -> dict:
    supabase = get_supabase_client()
    try:
        res_txns = supabase.table("transactions").select(
            "vendor, amount, date, category"
        ).gte("created_at", _today_iso()).order("date", desc=True).limit(10).execute()
        txns = res_txns.data or []

        res_appr = supabase.table("approvals").select(
            "status"
        ).gte("created_at", _today_iso()).execute()
        approvals = res_appr.data or []

        total_paid = sum(t.get("amount") or 0 for t in txns)
        recent = [
            {"vendor": t.get("vendor"), "amount": f"₹{t.get('amount'):,.2f}", "category": t.get("category")}
            for t in txns[:5]
        ]
        return {
            "intent": "payment_status",
            "payments_done": len(txns),
            "total_paid_inr": f"{total_paid:,.2f}",
            "recent_payments": recent,
            "approvals_today": len(approvals),
            "approved_count": sum(1 for a in approvals if a.get("status") == "approved"),
            "rejected_count": sum(1 for a in approvals if a.get("status") == "rejected"),
        }
    except Exception as e:
        logger.error(f"fetch_payment_status error: {e}")
        return {}


async def fetch_pending_review() -> dict:
    supabase = get_supabase_client()
    try:
        res = supabase.table("extracted_documents").select(
            "id, vendor, amount, confidence, explanation, created_at"
        ).eq("decision", "human_review").order("created_at", desc=True).limit(10).execute()
        docs = res.data or []

        items = [
            {
                "id": d.get("id"),
                "vendor": d.get("vendor"),
                "amount": f"₹{d.get('amount'):,.2f}",
                "confidence": f"{(d.get('confidence') or 0) * 100:.0f}%",
                "reason": d.get("explanation") or "Manual check required",
            }
            for d in docs
        ]
        return {"intent": "pending_review", "count": len(docs), "items": items}
    except Exception as e:
        logger.error(f"fetch_pending_review error: {e}")
        return {}


async def fetch_daily_report() -> dict:
    inv = await fetch_invoice_summary()
    anomaly = await fetch_anomaly_details()
    cash = await fetch_cash_flow_data()
    pay = await fetch_payment_status()
    return {
        "intent": "daily_report",
        "invoice_summary": inv,
        "anomaly_summary": {"count": anomaly.get("count", 0)},
        "cash_flow": {
            "total_transactions": cash.get("total_transactions", 0),
            "total_amount_inr": cash.get("total_amount_inr", 0),
        },
        "payments": {
            "done": pay.get("payments_done", 0),
            "total_paid": pay.get("total_paid_inr", 0),
            "approvals": pay.get("approvals_today", 0),
        },
    }


# ──────────────────────────────────────────────────────────────
# AI Response Generation
# ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are AutoTwin AI, an intelligent invoice and finance assistant.
Rules:
- Answer based STRICTLY on the business data provided. Never fabricate numbers.
- Keep responses concise and WhatsApp-friendly (plain text, bullet points where helpful).
- If a value is 0 or missing, say "None today" rather than inventing data.
- Use emojis selectively — not on every line.
- Match the user's language."""


async def generate_ai_response(data: dict, user_message: str) -> str:
    if not settings.GROQ_API_KEY:
        return f"AI engine unavailable. Raw data:\n{json.dumps(data, indent=2)}"

    prompt = (
        f'User asked: "{user_message}"\n\n'
        f"Business data (use ONLY this):\n{json.dumps(data, indent=2)}\n\n"
        "Respond helpfully and concisely based on the data above."
    )

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 400,
                },
                timeout=15.0,
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Groq response generation failed: {e}")
        return f"AI engine temporarily unavailable.\n{json.dumps(data, indent=2)}"


# ──────────────────────────────────────────────────────────────
# Approval Button Helper
# ──────────────────────────────────────────────────────────────

async def send_approval_buttons(
    to_phone: str,
    doc_id: str,
    vendor: str,
    amount: float,
    confidence: float,
    explanation: str,
) -> None:
    """Send a professional invoice review request with Approve / Reject buttons."""
    body = (
        f"*Invoice Review Required*\n\n"
        f"Vendor: {vendor}\n"
        f"Amount: ₹{amount:,.2f}\n"
        f"Confidence: {confidence * 100:.0f}%\n\n"
        f"Reason: {explanation}\n\n"
        "Please review and take action below."
    )
    await send_interactive_buttons(
        to_phone=to_phone,
        header="Action Required",
        body=body,
        footer=f"Invoice ID: {doc_id[:8]}…",
        buttons=[
            {"id": f"approve:{doc_id}", "title": "Approve"},
            {"id": f"reject:{doc_id}", "title": "Reject"},
        ],
    )


# ──────────────────────────────────────────────────────────────
# WhatsApp Invoice Intake
# ──────────────────────────────────────────────────────────────

_MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


async def handle_whatsapp_invoice(
    sender_phone: str,
    media_id: str,
    mime_type: str,
    caption: Optional[str],
) -> None:
    """
    Process an invoice image or PDF sent via WhatsApp.
    1. Download media from Meta
    2. Upload to Supabase Storage
    3. Run orchestrator pipeline (OCR → analysis → confidence → decision)
    4. Classify category (from message hint or AI)
    5. Save to invoices + extracted_documents   tables
    6. Send confirmation; if human_review needed, send Approve/Reject buttons
    """
    await send_whatsapp_message(sender_phone, "Received your invoice. Processing now…")

    try:
        # Resolve sender phone → actual user account
        real_user_id = await get_user_id_by_phone(sender_phone) or sender_phone
        logger.info("WhatsApp invoice from %s → resolved user_id=%s", sender_phone, real_user_id)

        # 1. Download media
        file_bytes, detected_mime = await download_whatsapp_media(media_id)
        ext = _MIME_TO_EXT.get(mime_type or detected_mime, "jpg")

        # 2. Upload to Supabase Storage
        supabase = get_supabase_client()
        storage_path = f"whatsapp/{real_user_id}/{media_id}.{ext}"
        supabase.storage.from_("invoices").upload(
            path=storage_path,
            file=file_bytes,
            file_options={"content-type": mime_type or detected_mime},
        )
        file_url = supabase.storage.from_("invoices").get_public_url(storage_path)

        # 3. Orchestrator pipeline
        from services.orchestrator import Orchestrator
        orchestrator = Orchestrator()
        result = await orchestrator.process_invoice(
            file_bytes=file_bytes,
            user_id=real_user_id,
        )

        # 4. Classify category
        category = await classify_invoice_category(
            vendor=result.vendor,
            amount=result.amount,
            message_hint=caption,
        )

        # 5. Save to invoices table (linked to the real user account)
        from uuid import uuid4
        invoice_id = str(uuid4())
        supabase.table("invoices").insert({
            "id": invoice_id,
            "user_id": real_user_id,
            "vendor": result.vendor,
            "invoice_no": result.invoice_no or result.invoice_id,
            "amount": result.amount,
            "currency": "INR",
            "status": result.status,
            "confidence": result.confidence,
            "category": category,
            "file_url": file_url,
        }).execute()

        # Update extracted_documents with category + file_url
        supabase.table("extracted_documents").update({
            "category": category,
            "file_url": file_url,
            "user_id": real_user_id,
        }).eq("invoice_id", result.invoice_id).execute()

        # 6. Send confirmation
        status_icon = (
            "✅" if result.decision == "auto_execute"
            else "⚠️" if result.decision == "warn"
            else "👁"
        )
        msg = (
            f"{status_icon} *Invoice Processed*\n\n"
            f"Vendor: *{result.vendor}*\n"
            f"Amount: *₹{result.amount:,.2f}*\n"
            f"Category: *{category}*\n"
            f"Date: {result.date}\n"
            f"Confidence: {result.confidence * 100:.0f}%\n"
            f"Status: {result.status.replace('_', ' ').title()}\n\n"
            f"{result.explanation}"
        )
        await send_whatsapp_message(sender_phone, msg)

        if result.decision == "human_review":
            await send_approval_buttons(
                to_phone=sender_phone,
                doc_id=result.invoice_id,
                vendor=result.vendor,
                amount=result.amount,
                confidence=result.confidence,
                explanation=result.explanation,
            )

    except Exception as e:
        logger.error(f"WhatsApp invoice processing failed: {e}", exc_info=True)
        await send_whatsapp_message(
            sender_phone,
            "Unable to process the invoice. Please ensure it is a clear image or PDF and try again.",
        )


# ──────────────────────────────────────────────────────────────
# Main Text Message Handler
# ──────────────────────────────────────────────────────────────

_LOADING_MSGS = {
    "invoice_summary": "Fetching invoice details…",
    "payment_status": "Checking payment records…",
    "daily_report": "Building your daily report…",
    "anomaly_details": "Scanning for anomalies…",
    "pending_review": "Fetching pending reviews…",
    "cash_flow": "Analysing cash flow…",
}


async def handle_incoming_message(sender_phone: str, text: str) -> None:
    """Entry point for incoming text messages."""
    intent = detect_intent(text)
    logger.info(f"📥 WhatsApp from {sender_phone}: '{text}' → intent={intent}")

    if intent in {"menu", "unknown"}:
        await send_menu(sender_phone)
        return

    await send_whatsapp_message(sender_phone, _LOADING_MSGS.get(intent, "Processing…"))

    fetchers = {
        "invoice_summary": fetch_invoice_summary,
        "payment_status": fetch_payment_status,
        "daily_report": fetch_daily_report,
        "anomaly_details": fetch_anomaly_details,
        "pending_review": fetch_pending_review,
        "cash_flow": fetch_cash_flow_data,
    }
    data = await fetchers.get(intent, fetch_invoice_summary)()

    response = await generate_ai_response(data, text)
    await send_whatsapp_message(sender_phone, response)
