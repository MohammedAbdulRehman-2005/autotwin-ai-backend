import os
import json
import re
import asyncio
from google import genai
from google.genai import types
import logging

logger = logging.getLogger("autotwin_ai.gemini_client")


def _get_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set.")
    return genai.Client(api_key=api_key)


def extract_json(text: str) -> dict:
    """Safely pull the first JSON object out of a Gemini response."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON found in Gemini response")
    return json.loads(match.group())


def _clean_amount(val) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    cleaned = re.sub(r"[^\d.]", "", str(val))
    return float(cleaned) if cleaned else 0.0


def normalize_rich(data: dict) -> dict:
    """Normalize all extracted fields into consistent Python types."""
    return {
        "vendor":        str(data.get("vendor") or "Unknown Vendor").strip(),
        "company":       str(data.get("company") or "").strip(),
        "invoice_no":    str(data.get("invoice_no") or "").strip(),
        "amount":        _clean_amount(data.get("total") or data.get("amount")),
        "subtotal":      _clean_amount(data.get("subtotal")),
        "gst_amount":    _clean_amount(data.get("gst_amount")),
        "gst_rate":      float(data.get("gst_rate") or 0.0),
        "date":          str(data.get("date") or "").strip(),
        "due_date":      str(data.get("due_date") or "").strip(),
        "payment_terms": str(data.get("payment_terms") or "").strip(),
        "currency":      str(data.get("currency") or "INR").strip().upper(),
        "line_items":    data.get("line_items") or [],
        "notes":         str(data.get("notes") or "").strip(),
    }


async def extract_with_gemini(image_bytes: bytes) -> dict:
    """
    Extract rich, structured invoice data using Gemini Vision.

    Returns a normalized dict with all important invoice fields:
    vendor, company, invoice_no, date, due_date, payment_terms,
    subtotal, gst_rate, gst_amount, total, currency, line_items, notes.
    """
    client = _get_client()

    prompt = """You are a specialized invoice data extraction AI.

Analyze the invoice image and return ONLY valid JSON with these exact fields:

{
  "vendor": "supplier/seller company name",
  "company": "buyer/bill-to company name",
  "invoice_no": "invoice number or ID",
  "date": "invoice date in YYYY-MM-DD format",
  "due_date": "payment due date in YYYY-MM-DD or null",
  "payment_terms": "e.g. Net 30, Immediate, or null",
  "subtotal": numeric amount before tax,
  "gst_rate": GST percentage as a number (e.g. 18 for 18%),
  "gst_amount": numeric GST tax amount,
  "total": total invoice amount including all taxes,
  "currency": "currency code e.g. INR, USD",
  "line_items": [
    {"description": "item name", "quantity": number, "unit_price": number, "amount": number}
  ],
  "notes": "any important notes, payment instructions, or special remarks"
}

Strict Rules:
- All numeric values MUST be numbers, NOT strings
- Missing fields must be null
- Return ONLY the JSON object — no explanation, no markdown
- Do NOT hallucinate — extract only what is clearly visible"""

    mime_type = "image/jpeg"
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        mime_type = "image/png"
    elif image_bytes[:4] == b'%PDF':
        mime_type = "application/pdf"

    contents = [
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        prompt,
    ]

    def _call_gemini():
        return client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents,
        )

    logger.info("Gemini rich extraction started")
    last_exc = None
    for attempt in range(2):
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(_call_gemini), timeout=20
            )
            logger.info("Gemini response received")
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            logger.warning(f"Gemini attempt {attempt + 1} failed: {exc}")

    if last_exc:
        logger.error("Gemini failed after retries")
        raise last_exc

    raw_text = response.text.strip()
    data = extract_json(raw_text)
    normalized = normalize_rich(data)

    logger.info(
        "Extraction complete: vendor=%s total=%.2f gst=%.1f%%",
        normalized["vendor"], normalized["amount"], normalized["gst_rate"],
    )
    return normalized
