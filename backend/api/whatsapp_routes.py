"""
api/whatsapp_routes.py
───────────────────────
Handles incoming Webhooks from Meta's Official WhatsApp Cloud API.
"""
import logging
import re
from fastapi import APIRouter, Request, Query, HTTPException, Response
from pydantic import BaseModel
from typing import Dict, Any

from core.config import settings
from services.whatsapp_bot import handle_incoming_message
from models.database import get_invoice
from services.whatsapp_client import send_whatsapp_message
import httpx  # For calling our own local /api/approve endpoint without duplicating logic

logger = logging.getLogger("autotwin_ai.whatsapp_routes")
router = APIRouter()

@router.get("/webhook/whatsapp")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    """
    Webhook verification required by Meta.
    """
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("WhatsApp webhook verified successfully.")
        return int(hub_challenge)
    
    logger.warning("WhatsApp webhook verification failed.")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook/whatsapp")
async def receive_webhook(request: Request):
    """
    Receives incoming WhatsApp messages from users.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Meta sends an array of entries
    entries = body.get("entry", [])
    for entry in entries:
        changes = entry.get("changes", [])
        for change in changes:
            value = change.get("value", {})
            messages = value.get("messages", [])
            for msg in messages:
                if msg.get("type") == "text":
                    sender_phone = msg.get("from")
                    text_body = msg.get("text", {}).get("body", "").strip()
                    
                    if not sender_phone or not text_body:
                        continue
                        
                    logger.info(f"Incoming WhatsApp message from {sender_phone}: {text_body}")
                    
                    # 1. Check if it's an APPROVE/REJECT command
                    upper_text = text_body.upper()
                    match = re.match(r"^(APPROVE|REJECT)(?:\s+([\w-]+))?$", upper_text)
                    if match:
                        action = match.group(1)
                        doc_id = match.group(2)
                        await handle_approval_action(sender_phone, action, doc_id, request.url.components.netloc)
                    else:
                        # 2. General AI Chat / Intent routing
                        import asyncio
                        # Fire and forget to not block webhook response (Meta requires 200 OK fast)
                        asyncio.create_task(handle_incoming_message(sender_phone, text_body))

    return Response(content="EVENT_RECEIVED", status_code=200)

async def handle_approval_action(sender_phone: str, action: str, doc_id: str, host: str):
    from models.supabase_client import get_supabase_client
    approved = (action == "APPROVE")
    
    if not doc_id or doc_id in ["ALL", "LATEST"]:
        # Find latest pending document
        await send_whatsapp_message(sender_phone, "🔍 Looking up the latest pending invoice...")
        supabase = get_supabase_client()
        res = supabase.table("extracted_documents").select("id").eq("decision", "human_review").order("created_at", desc=True).limit(1).execute()
        
        pending = res.data or []
        if not pending:
            await send_whatsapp_message(sender_phone, "⚠️ No pending invoices found requiring review.")
            return
        doc_id = pending[0].get("id")

    logger.info(f"📝 Approval reply: {action} for document_id={doc_id}")
    try:
        # Call the existing /approve route via HTTP to ensure all logic (memory graph, local automation) fires.
        # Alternatively, we could import approve_invoice from routes, but this ensures a clean context.
        # For simplicity and reliability in a webhook, we'll make a self-HTTP call if we know our own URL,
        # or we can just import the route handler. 
        from api.routes import approve_invoice
        from models.schemas import ApprovalRequest
        
        req = ApprovalRequest(
            invoice_id=doc_id,
            approved=approved,
            reviewer_notes=f"WhatsApp {action.lower()} by {sender_phone}"
        )
        
        # We pass None for _user to trigger demo_user fallback which is fine for webhook
        result = await approve_invoice(req, _user=None)
        
        if approved:
            msg = f"✅ Invoice *{doc_id}* has been *Approved*\n📊 New confidence: {result.updated_confidence * 100:.0f}%\n{result.message}"
        else:
            msg = f"❌ Invoice *{doc_id}* has been *Rejected*\n📌 Flagged for re-processing.\n{result.message}"
            
        await send_whatsapp_message(sender_phone, msg)
        
    except Exception as err:
        logger.error(f"Approval action failed: {err}")
        await send_whatsapp_message(sender_phone, f"⚠️ Could not process your {action} request.\nError: {str(err)}")
