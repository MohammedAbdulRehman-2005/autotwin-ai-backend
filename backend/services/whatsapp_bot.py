"""
services/whatsapp_bot.py
────────────────────────
Handles incoming WhatsApp messages, detects intent, queries Supabase,
and generates personalized AI responses using Groq.
"""
import re
import json
import logging
from datetime import datetime, timezone
import httpx

from core.config import settings
from models.supabase_client import get_supabase_client
from services.whatsapp_client import send_whatsapp_message

logger = logging.getLogger("autotwin_ai.whatsapp_bot")

# ──────────────────────────────────────────────────────────────
# Intent Detection
# ──────────────────────────────────────────────────────────────

def detect_intent(text: str) -> str:
    t = text.lower().strip()

    if t in ['hi', 'hello', 'hey', 'namaste', 'hii', 'yo']:
        return 'menu'

    if t == '1' or re.search(r'invoice\s*(summary|status|count|list|today)', t):
        return 'invoice_summary'
    if t == '2' or re.search(r'payment\s*(status|done|made|completed|list)|paid|transactions', t):
        return 'payment_status'
    if t == '3' or re.search(r'daily\s*report|full report|overview|summary', t):
        return 'daily_report'
    if t == '4' or re.search(r'anomal|fraud|suspicious|flagged|risk', t):
        return 'anomaly_details'
    if t == '5' or re.search(r'pending|review|waiting|needs.review|human review', t):
        return 'pending_review'
    if re.search(r'cash\s*flow|cashflow|money flow|inflow|outflow|spend|expenditure|expense', t):
        return 'cash_flow'

    # Fallback
    if re.search(r'invoice|bill|document|vendor|processed', t):
        return 'invoice_summary'
    if re.search(r'payment|pay|transaction|transfer|amount paid', t):
        return 'payment_status'
    if re.search(r'anomal|warning|alert|flag', t):
        return 'anomaly_details'
    if re.search(r'pending|review|check|approve', t):
        return 'pending_review'

    return 'unknown'

MENU = """👋 Welcome to *AutoTwin AI*

What would you like to know?

1️⃣ Invoice Summary
2️⃣ Payment Status
3️⃣ Daily Report
4️⃣ Anomaly Details
5️⃣ Pending Reviews

💬 Or just ask naturally — e.g. "What's the cash flow today?"""

# ──────────────────────────────────────────────────────────────
# Data Fetchers (Supabase)
# ──────────────────────────────────────────────────────────────

def today_iso() -> str:
    # Basic start of day
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

async def fetch_invoice_summary() -> dict:
    supabase = get_supabase_client()
    try:
        res = supabase.table("extracted_documents").select(
            "id, vendor, amount, anomaly, decision, confidence, created_at"
        ).gte("created_at", today_iso()).order("created_at", desc=True).execute()
        
        docs = res.data or []
        total = len(docs)
        anomalies = sum(1 for d in docs if d.get("anomaly"))
        auto_approved = sum(1 for d in docs if d.get("decision") == "auto_execute")
        pending = sum(1 for d in docs if d.get("decision") == "human_review")
        
        conf_sum = sum(d.get("confidence", 0) for d in docs)
        avg_conf = (conf_sum / total * 100) if total > 0 else 0

        vendor_map = {}
        for d in docs:
            v = d.get("vendor", "Unknown")
            vendor_map[v] = vendor_map.get(v, 0) + 1
            
        top_vendors = [f"{v} ({c})" for v, c in sorted(vendor_map.items(), key=lambda item: item[1], reverse=True)[:3]]

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
            "vendor, amount, confidence, explanation, anomaly_details, created_at"
        ).eq("anomaly", True).gte("created_at", today_iso()).order("created_at", desc=True).limit(10).execute()
        
        docs = res.data or []
        anomalies = []
        for d in docs:
            conf = d.get("confidence") or 0
            anomalies.append({
                "vendor": d.get("vendor"),
                "amount": d.get("amount"),
                "confidence": f"{conf * 100:.0f}%",
                "reason": d.get("explanation") or "No explanation provided"
            })
            
        return {
            "intent": "anomaly_details",
            "count": len(docs),
            "anomalies": anomalies
        }
    except Exception as e:
        logger.error(f"fetch_anomaly_details error: {e}")
        return {}

async def fetch_cash_flow_data() -> dict:
    supabase = get_supabase_client()
    try:
        res = supabase.table("transactions").select(
            "amount, vendor, category, date, anomaly_score"
        ).gte("created_at", today_iso()).order("date", desc=True).execute()
        
        txns = res.data or []
        total_inflow = sum(t.get("amount") or 0 for t in txns)
        
        by_category = {}
        for t in txns:
            cat = t.get("category") or "Other"
            by_category[cat] = by_category.get(cat, 0) + (t.get("amount") or 0)
            
        top_categories = [f"{c}: ₹{a:.0f}" for c, a in sorted(by_category.items(), key=lambda x: x[1], reverse=True)[:5]]
        
        avg_txn = (total_inflow / len(txns)) if txns else 0
        
        return {
            "intent": "cash_flow",
            "total_transactions": len(txns),
            "total_amount_inr": f"{total_inflow:.2f}",
            "avg_transaction": f"{avg_txn:.2f}",
            "category_breakdown": top_categories
        }
    except Exception as e:
        logger.error(f"fetch_cash_flow_data error: {e}")
        return {}

async def fetch_payment_status() -> dict:
    supabase = get_supabase_client()
    try:
        res_txns = supabase.table("transactions").select(
            "vendor, amount, date, category"
        ).gte("created_at", today_iso()).order("date", desc=True).limit(10).execute()
        txns = res_txns.data or []
        
        res_appr = supabase.table("approvals").select(
            "invoice_id, status, notes, resolved_at"
        ).gte("created_at", today_iso()).execute()
        approvals = res_appr.data or []
        
        total_paid = sum(t.get("amount") or 0 for t in txns)
        
        recent = [{"vendor": t.get("vendor"), "amount": f"₹{t.get('amount')}", "category": t.get("category")} for t in txns[:5]]
        
        return {
            "intent": "payment_status",
            "payments_done": len(txns),
            "total_paid_inr": f"{total_paid:.2f}",
            "recent_payments": recent,
            "approvals_today": len(approvals),
            "approved_count": sum(1 for a in approvals if a.get("status") == "approved"),
            "rejected_count": sum(1 for a in approvals if a.get("status") == "rejected")
        }
    except Exception as e:
        logger.error(f"fetch_payment_status error: {e}")
        return {}

async def fetch_pending_review() -> dict:
    supabase = get_supabase_client()
    try:
        res = supabase.table("extracted_documents").select(
            "vendor, amount, confidence, explanation, created_at"
        ).eq("decision", "human_review").order("created_at", desc=True).limit(10).execute()
        docs = res.data or []
        
        items = []
        for d in docs:
            conf = d.get("confidence") or 0
            items.append({
                "vendor": d.get("vendor"),
                "amount": f"₹{d.get('amount')}",
                "confidence": f"{conf * 100:.0f}%",
                "reason": d.get("explanation") or "Manual check required"
            })
            
        return {
            "intent": "pending_review",
            "count": len(docs),
            "items": items
        }
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
        "cash_flow": {"total_transactions": cash.get("total_transactions", 0), "total_amount_inr": cash.get("total_amount_inr", 0)},
        "payments": {"done": pay.get("payments_done", 0), "total_paid": pay.get("total_paid_inr", 0), "approvals": pay.get("approvals_today", 0)}
    }

# ──────────────────────────────────────────────────────────────
# AI Generation
# ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are AutoTwin AI, an intelligent invoice & finance assistant.
Rules:
- ALWAYS answer based STRICTLY on the provided business data below.
- NEVER give generic stats — personalize the response to the exact question asked.
- Use emojis selectively (not on every line).
- Keep the response concise and WhatsApp-friendly (no markdown headers, use bullet points).
- If a value is 0 or null, say "None today" — do NOT fabricate data.
- Detect the user's language and reply in the SAME language."""

async def generate_personalised_response(data: dict, user_message: str) -> str:
    if not settings.GROQ_API_KEY:
        return f"⚠️ AI engine (Groq) unavailable. Raw data:\n{json.dumps(data, indent=2)}"
        
    prompt = f"""User asked: "{user_message}"

Business data (use ONLY this):
{json.dumps(data, indent=2)}

Respond in a personalised, helpful way strictly based on the data above."""

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
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 400,
                },
                timeout=15.0
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Groq Chat failed: {e}")
        return f"⚠️ AI engine temporarily unavailable. Raw data:\n{json.dumps(data, indent=2)}"

# ──────────────────────────────────────────────────────────────
# Main Message Handler
# ──────────────────────────────────────────────────────────────

async def handle_incoming_message(sender_phone: str, text: str) -> None:
    """
    Main entry point for incoming text messages from WhatsApp Cloud API.
    """
    intent = detect_intent(text)
    logger.info(f"📥 WhatsApp Message from {sender_phone}: '{text}' -> Intent: {intent}")

    if intent == 'menu' or intent == 'unknown':
        await send_whatsapp_message(sender_phone, MENU)
        return

    loading_msgs = {
        "invoice_summary": "📄 Fetching invoice details...",
        "payment_status": "💰 Checking payment records...",
        "daily_report": "📊 Building your daily report...",
        "anomaly_details": "🔍 Scanning anomalies...",
        "pending_review": "🕒 Fetching pending reviews...",
        "cash_flow": "💸 Analysing cash flow..."
    }
    
    await send_whatsapp_message(sender_phone, loading_msgs.get(intent, "⏳ Processing..."))

    data = {}
    if intent == "invoice_summary":
        data = await fetch_invoice_summary()
    elif intent == "payment_status":
        data = await fetch_payment_status()
    elif intent == "daily_report":
        data = await fetch_daily_report()
    elif intent == "anomaly_details":
        data = await fetch_anomaly_details()
    elif intent == "pending_review":
        data = await fetch_pending_review()
    elif intent == "cash_flow":
        data = await fetch_cash_flow_data()
    else:
        data = await fetch_invoice_summary()

    response_text = await generate_personalised_response(data, text)
    await send_whatsapp_message(sender_phone, response_text)
