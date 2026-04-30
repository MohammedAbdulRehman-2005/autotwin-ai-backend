"""
api/whatsapp_routes.py
───────────────────────
Webhook handler for Meta's WhatsApp Cloud API.

Handles:
- Text messages        → intent routing / AI chat
- Interactive replies  → list_reply (menu) and button_reply (approve/reject)
- Image / document     → invoice intake pipeline
"""

import asyncio
import logging
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response

from core.config import settings
from services.whatsapp_bot import handle_incoming_message, handle_whatsapp_invoice
from services.whatsapp_client import send_whatsapp_message, send_interactive_buttons

logger = logging.getLogger("autotwin_ai.whatsapp_routes")
router = APIRouter()


# ──────────────────────────────────────────────────────────────
# Webhook Verification
# ──────────────────────────────────────────────────────────────

@router.get("/webhook/whatsapp")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("WhatsApp webhook verified successfully.")
        return int(hub_challenge)
    logger.warning("WhatsApp webhook verification failed.")
    raise HTTPException(status_code=403, detail="Verification failed")


# ──────────────────────────────────────────────────────────────
# Incoming Webhook
# ──────────────────────────────────────────────────────────────

@router.post("/webhook/whatsapp")
async def receive_webhook(request: Request):
    """
    Receives incoming messages from WhatsApp Cloud API.
    Responds with 200 immediately; all processing is fire-and-forget.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    entries = body.get("entry", [])
    for entry in entries:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                asyncio.create_task(_dispatch_message(msg))

    return Response(content="EVENT_RECEIVED", status_code=200)


# ──────────────────────────────────────────────────────────────
# Message Dispatcher
# ──────────────────────────────────────────────────────────────

async def _dispatch_message(msg: dict) -> None:
    """Route a single WhatsApp message to the correct handler."""
    sender = msg.get("from")
    if not sender:
        return

    msg_type = msg.get("type")

    # ── Interactive reply (list or button) ──────────────────
    if msg_type == "interactive":
        interactive = msg.get("interactive", {})
        itype = interactive.get("type")

        if itype == "list_reply":
            # User selected a menu option
            row_id = interactive.get("list_reply", {}).get("id", "")
            logger.info(f"List reply from {sender}: {row_id}")
            asyncio.create_task(handle_incoming_message(sender, row_id))

        elif itype == "button_reply":
            # User tapped Approve / Reject button
            btn_id = interactive.get("button_reply", {}).get("id", "")
            logger.info(f"Button reply from {sender}: {btn_id}")
            asyncio.create_task(_handle_button_reply(sender, btn_id))

        return

    # ── Text message ─────────────────────────────────────────
    if msg_type == "text":
        text_body = msg.get("text", {}).get("body", "").strip()
        if not text_body:
            return

        logger.info(f"Text message from {sender}: {text_body}")

        upper = text_body.upper()
        # Legacy text-based APPROVE/REJECT still supported
        match = re.match(r"^(APPROVE|REJECT)(?:\s+([\w-]+))?$", upper)
        if match:
            action = match.group(1)
            doc_id = match.group(2)
            asyncio.create_task(_handle_approval(sender, action == "APPROVE", doc_id))
        else:
            asyncio.create_task(handle_incoming_message(sender, text_body))
        return

    # ── Image ────────────────────────────────────────────────
    if msg_type == "image":
        image = msg.get("image", {})
        media_id = image.get("id")
        mime_type = image.get("mime_type", "image/jpeg")
        caption = image.get("caption", "")
        if media_id:
            logger.info(f"Image from {sender}: media_id={media_id}, caption={caption!r}")
            asyncio.create_task(
                handle_whatsapp_invoice(sender, media_id, mime_type, caption or None)
            )
        return

    # ── Document (PDF) ───────────────────────────────────────
    if msg_type == "document":
        document = msg.get("document", {})
        media_id = document.get("id")
        mime_type = document.get("mime_type", "application/pdf")
        caption = document.get("caption", "")
        filename = document.get("filename", "")
        if media_id:
            logger.info(f"Document from {sender}: {filename} media_id={media_id}")
            asyncio.create_task(
                handle_whatsapp_invoice(sender, media_id, mime_type, caption or None)
            )
        return

    logger.debug(f"Unhandled message type '{msg_type}' from {sender}")


# ──────────────────────────────────────────────────────────────
# Button Reply: Approve / Reject
# ──────────────────────────────────────────────────────────────

async def _handle_button_reply(sender: str, btn_id: str) -> None:
    """
    Handle button_reply from an approval request.
    Expected btn_id format: "approve:<doc_id>" or "reject:<doc_id>"
    """
    if ":" not in btn_id:
        await send_whatsapp_message(sender, "Unrecognised button. Please use the menu.")
        return

    action_raw, doc_id = btn_id.split(":", 1)
    approved = action_raw.lower() == "approve"
    await _handle_approval(sender, approved, doc_id)


async def _handle_approval(sender: str, approved: bool, doc_id: Optional[str]) -> None:
    """Core approval/rejection logic."""
    from models.supabase_client import get_supabase_client
    from api.routes import approve_invoice
    from models.schemas import ApprovalRequest

    action_label = "Approve" if approved else "Reject"

    if not doc_id or doc_id.upper() in {"ALL", "LATEST"}:
        await send_whatsapp_message(sender, "Looking up the latest pending invoice…")
        supabase = get_supabase_client()
        res = (
            supabase.table("extracted_documents")
            .select("id")
            .eq("decision", "human_review")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        pending = res.data or []
        if not pending:
            await send_whatsapp_message(sender, "No pending invoices found requiring review.")
            return
        doc_id = pending[0].get("id")

    logger.info(f"Approval action: {action_label} for doc_id={doc_id} by {sender}")

    try:
        req = ApprovalRequest(
            invoice_id=doc_id,
            approved=approved,
            reviewer_notes=f"WhatsApp {action_label.lower()} by {sender}",
        )
        result = await approve_invoice(req, _user=None)

        if approved:
            body = (
                f"Invoice approved successfully.\n\n"
                f"Updated Confidence: {result.updated_confidence * 100:.0f}%\n"
                f"{result.message}"
            )
        else:
            body = (
                f"Invoice rejected and flagged for re-processing.\n\n"
                f"{result.message}"
            )

        await send_interactive_buttons(
            to_phone=sender,
            header="AutoTwin AI",
            body=body,
            footer=f"Invoice ID: {doc_id[:8]}…",
            buttons=[{"id": "menu", "title": "Back to Menu"}],
        )

    except Exception as err:
        logger.error(f"Approval failed: {err}")
        await send_whatsapp_message(
            sender,
            f"Could not process your {action_label} request. Error: {str(err)}",
        )
