"""
services/whatsapp_client.py
────────────────────────────
Official Meta WhatsApp Cloud API Client.
Handles text, interactive (list / button), and media messages.
"""

import httpx
import logging
from typing import Optional

from core.config import settings

logger = logging.getLogger("autotwin_ai.whatsapp_client")

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


def _auth_headers() -> dict:
    token = settings.whatsapp_token
    if not token:
        raise ValueError("WHATSAPP_CLOUD_TOKEN is not configured.")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _clean_phone(phone: str) -> str:
    return "".join(filter(str.isdigit, phone))


# ──────────────────────────────────────────────────────────────
# Text Message
# ──────────────────────────────────────────────────────────────

async def send_whatsapp_message(to_phone: str, message: str) -> dict:
    """Send a plain text message."""
    if not settings.whatsapp_token or not settings.whatsapp_phone_id:
        logger.warning("WhatsApp credentials not configured. Skipping send.")
        return {"status": "skipped", "reason": "missing_credentials"}

    url = f"{GRAPH_API_BASE}/{settings.whatsapp_phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _clean_phone(to_phone),
        "type": "text",
        "text": {"preview_url": False, "body": message},
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=_auth_headers(), timeout=12.0)
            response.raise_for_status()
            logger.info(f"📤 Text message sent to {_clean_phone(to_phone)}.")
            return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"WhatsApp API HTTP error: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"WhatsApp API connection error: {str(e)}")
        raise


# ──────────────────────────────────────────────────────────────
# Interactive List Message  (up to 10 rows, grouped in sections)
# ──────────────────────────────────────────────────────────────

async def send_interactive_list(
    to_phone: str,
    header: str,
    body: str,
    footer: str,
    button_label: str,
    sections: list,
) -> dict:
    """
    Send an interactive list picker.

    sections format:
        [{"title": "Section Name", "rows": [
            {"id": "row_id", "title": "Row Title", "description": "Optional"}
        ]}]
    """
    if not settings.whatsapp_token or not settings.whatsapp_phone_id:
        logger.warning("WhatsApp credentials not configured. Skipping send.")
        return {"status": "skipped"}

    url = f"{GRAPH_API_BASE}/{settings.whatsapp_phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _clean_phone(to_phone),
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": header},
            "body": {"text": body},
            "footer": {"text": footer},
            "action": {"button": button_label, "sections": sections},
        },
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=_auth_headers(), timeout=12.0)
            response.raise_for_status()
            logger.info(f"📤 Interactive list sent to {_clean_phone(to_phone)}.")
            return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"WhatsApp list API error: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"WhatsApp list connection error: {str(e)}")
        raise


# ──────────────────────────────────────────────────────────────
# Interactive Button Message  (up to 3 quick-reply buttons)
# ──────────────────────────────────────────────────────────────

async def send_interactive_buttons(
    to_phone: str,
    body: str,
    buttons: list,
    header: Optional[str] = None,
    footer: Optional[str] = None,
) -> dict:
    """
    Send an interactive button message.

    buttons format: [{"id": "btn_id", "title": "Button Label"}]
    Maximum 3 buttons. Title max 20 chars.
    """
    if not settings.whatsapp_token or not settings.whatsapp_phone_id:
        logger.warning("WhatsApp credentials not configured. Skipping send.")
        return {"status": "skipped"}

    interactive: dict = {
        "type": "button",
        "body": {"text": body},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
                for b in buttons[:3]
            ]
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    if footer:
        interactive["footer"] = {"text": footer}

    url = f"{GRAPH_API_BASE}/{settings.whatsapp_phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _clean_phone(to_phone),
        "type": "interactive",
        "interactive": interactive,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=_auth_headers(), timeout=12.0)
            response.raise_for_status()
            logger.info(f"📤 Interactive buttons sent to {_clean_phone(to_phone)}.")
            return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"WhatsApp buttons API error: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"WhatsApp buttons connection error: {str(e)}")
        raise


# ──────────────────────────────────────────────────────────────
# Media Download
# ──────────────────────────────────────────────────────────────

async def download_whatsapp_media(media_id: str) -> tuple[bytes, str]:
    """
    Download a media file from WhatsApp Cloud API.
    Returns (file_bytes, mime_type).
    """
    token = settings.whatsapp_token
    if not token:
        raise ValueError("WHATSAPP_CLOUD_TOKEN is not configured.")

    auth_headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Step 1: Resolve media URL
        meta_res = await client.get(f"{GRAPH_API_BASE}/{media_id}", headers=auth_headers)
        meta_res.raise_for_status()
        meta = meta_res.json()
        media_url = meta.get("url")
        mime_type = meta.get("mime_type", "application/octet-stream")

        if not media_url:
            raise ValueError(f"No URL returned for media_id={media_id}")

        # Step 2: Download the binary
        file_res = await client.get(media_url, headers=auth_headers)
        file_res.raise_for_status()

    logger.info(f"📥 Downloaded media {media_id} — {mime_type}, {len(file_res.content)} bytes")
    return file_res.content, mime_type
