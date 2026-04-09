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

def extract_json(text: str):
    """2. Add safe JSON extraction"""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON found in Gemini response")
    return json.loads(match.group())

def normalize(data: dict):
    """3. Add normalization function"""
    def clean_amount(val):
        if isinstance(val, str):
            return float(val.replace(",", "").replace("$", "").strip() or 0)
        return float(val or 0)

    return {
        "vendor": data.get("vendor"),
        "amount": clean_amount(data.get("amount")),
        "date": data.get("date"),
        "currency": data.get("currency") or "INR"
    }

async def extract_with_gemini(image_bytes: bytes) -> dict:
    """
    Extract invoice data using Gemini Vision.
    Runs asynchronously wrapping the blocking SDK call.
    """
    client = _get_client()

    prompt = """You are an AI system specialized in invoice extraction.

Analyze the image and return ONLY valid JSON:

{
  "vendor": string or null,
  "amount": number or null,
  "date": string or null,
  "currency": string or null
}

Rules:
- No hallucination
- Numbers must be numeric
- Missing fields → null
- Return only JSON"""

    mime_type = "image/jpeg"
    if image_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
        mime_type = "image/png"
    elif image_bytes.startswith(b'%PDF-'):
        mime_type = "application/pdf"

    contents = [
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        prompt
    ]

    def _call_gemini():
        return client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents
        )

    # 4. Add retry logic (2 attempts max)
    logger.info("Gemini extraction started")
    for attempt in range(2):
        try:
            response = await asyncio.wait_for(asyncio.to_thread(_call_gemini), timeout=15)
            logger.info("Gemini response received")
            break
        except Exception as e:
            if attempt == 1:
                logger.error("Gemini failed after retries")
                raise
            logger.warning(f"Gemini retry attempt: {e}")

    # 6. Final flow
    raw_text = response.text.strip()
    data = extract_json(raw_text)
    data = normalize(data)
    
    return data
