"""
services/whatsapp_client.py
────────────────────────────
Official Meta WhatsApp Cloud API Client.
Handles sending messages.
"""

import httpx
import logging
from core.config import settings

logger = logging.getLogger("autotwin_ai.whatsapp_client")

def get_whatsapp_headers() -> dict:
    if not settings.WHATSAPP_CLOUD_TOKEN:
        raise ValueError("WHATSAPP_CLOUD_TOKEN is not configured.")
    return {
        "Authorization": f"Bearer {settings.WHATSAPP_CLOUD_TOKEN}",
        "Content-Type": "application/json",
    }

async def send_whatsapp_message(to_phone: str, message: str) -> dict:
    """
    Sends a text message using the Official WhatsApp Cloud API.
    to_phone must be a valid phone number with country code (e.g., 919876543210).
    """
    if not settings.WHATSAPP_CLOUD_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        logger.warning("WhatsApp Cloud API credentials not fully configured. Skipping send.")
        return {"status": "skipped", "reason": "missing_credentials"}

    # Strip any non-numeric characters from the phone number
    clean_phone = "".join(filter(str.isdigit, to_phone))

    url = f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": clean_phone,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                headers=get_whatsapp_headers(),
                timeout=12.0
            )
            response.raise_for_status()
            logger.info(f"📤 WhatsApp message successfully sent to {clean_phone}.")
            return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"WhatsApp API HTTP error: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"WhatsApp API connection error: {str(e)}")
        raise
