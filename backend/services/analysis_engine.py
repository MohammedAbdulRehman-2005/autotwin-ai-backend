import logging
import httpx
from typing import Dict, Any, List

from core.config import settings
from models.database import (
    analysis_check_idempotency,
    analysis_get_extracted_document,
    analysis_get_purchase_order,
    analysis_get_vendor_invoices,
    analysis_get_user_phone,
    analysis_check_duplicate_invoice,
    analysis_save_results,
)

logger = logging.getLogger("autotwin_ai.analysis_engine")

async def generate_whatsapp_message(
    flags: List[str], amount: float, avg: float, confidence: int, status: str
) -> str:
    """Generate dynamic WhatsApp message using Groq LLM based on extracted flags."""
    if not settings.GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not set. Using fallback WhatsApp message generator.")
        return _fallback_message_generator(flags, amount, avg, confidence, status)

    prompt = f"""
    You are AutoTwin AI, an invoice processing assistant. 
    Write a short, professional, and structured WhatsApp status message for an invoice analysis.
    Use emojis. 
    
    Data:
    - Flags: {', '.join(flags) if flags else 'None'}
    - Amount: ₹{amount}
    - Historical Avg: ₹{avg:.2f}
    - Confidence: {confidence}%
    - Status: {status}
    
    Follow this exact format roughly:
    📊 AutoTwin Invoice Analysis
    [Checkmarks for good things. Warning signs for flags like price spike or gst invalid or duplicate]
    💰 Amount: ₹[Amount]
    📈 Avg: ₹[Avg]
    ⚡ Confidence: [Confidence]%
    ✅/❗ Status: [Status]
    """

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.3-70b-versatile", 
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Failed to generate WhatsApp message with Groq: {e}")
        return _fallback_message_generator(flags, amount, avg, confidence, status)

def _fallback_message_generator(flags: List[str], amount: float, avg: float, confidence: int, status: str) -> str:
    msg = "📊 AutoTwin Invoice Analysis\n\n"
    if not flags:
        msg += "✔ No anomalies detected\n"
    else:
        for f in flags:
            if f == "price_spike":
                msg += "⚠ Price spike detected\n"
            elif f == "gst_invalid":
                msg += "⚠ Invalid GST detected\n"
            elif f == "duplicate":
                msg += "⚠ Duplicate invoice detected\n"
    msg += f"💰 Amount: ₹{amount}\n"
    if avg > 0:
        msg += f"📈 Avg: ₹{avg:.2f}\n"
    msg += f"⚡ Confidence: {confidence}%\n"
    msg += f"{'✅' if status == 'auto_approved' else '❗'} Status: {'Auto Approved' if status == 'auto_approved' else 'Needs Review'}\n"
    return msg

async def send_whatsapp_notification(phone_number: str, message: str) -> None:
    if not phone_number:
        logger.warning("No phone number found, skipping WhatsApp notification.")
        return
        
    logger.info(f"📤 Sending WhatsApp notification to {phone_number}...")
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                settings.WHATSAPP_API_URL,
                json={"number": phone_number, "message": message},
                timeout=10.0
            )
            res.raise_for_status()
            logger.info("📤 WhatsApp notification sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send WhatsApp message: {e}")

async def trigger_local_automation(vendor: str, amount: float, gst: float, invoice_id: str, po_number: str) -> None:
    """Fires the webhook to the local Playwright automation agent."""
    import os
    
    automation_url = os.getenv("LOCAL_AUTOMATION_URL")
    if not automation_url:
        logger.error("❌ LOCAL_AUTOMATION_URL is not set in environment. Cannot trigger local automation.")
        return

    logger.info("🚀 Triggering automation...")
    logger.info("📡 Calling ngrok URL...")
    
    payload = {
        "vendor": vendor,
        "amount": amount,
        "gst": gst,
        "invoice_id": invoice_id,
        "po_number": po_number or "N/A"
    }

    try:
         async with httpx.AsyncClient() as client:
             res = await client.post(automation_url, json=payload, timeout=5.0)
             res.raise_for_status()
             logger.info("✅ Successfully triggered local automation via ngrok.")
    except Exception as e:
         logger.error(f"❌ Failed to trigger local automation: {e}")



async def process_invoice_analysis(document_id: str) -> Dict[str, Any]:
    """
    Core brain of AutoTwin AI.
    Validates rules, computes confidence score, decides, saves, and notifies.
    """
    logger.info(f"📥 Received document: {document_id}")
    
    # 6. Idempotency Check
    already_processed = await analysis_check_idempotency(document_id)
    if already_processed:
        logger.info(f"Document {document_id} already processed.")
        return {"status": "already processed", "document_id": document_id}

    # 1. Fetch Data
    doc = await analysis_get_extracted_document(document_id)
    if not doc:
        raise ValueError(f"Extracted document {document_id} not found.")

    user_id = doc.get("user_id", "demo_user")
    po_number = doc.get("po_number")
    vendor = doc.get("vendor", "Unknown")
    amount = float(doc.get("amount", 0.0))
    gst = float(doc.get("gst", 0.0))
    invoice_id = doc.get("invoice_id", "")

    # 2. Fetch Related Data
    po_record = await analysis_get_purchase_order(po_number) if po_number else None
    vendor_invoices = await analysis_get_vendor_invoices(vendor, user_id)
    user_phone = await analysis_get_user_phone(user_id)

    logger.info("🧠 Running analysis...")

    # 3. Validation Engine & 4. Confidence Engine
    score = 0
    flags = []

    # 3.1 PO Matching & 3.2 3-Way Matching
    if po_record:
        # Assuming PO exists -> +30
        score += 30
        
        # Vendor + amount matching
        po_amount = float(po_record.get("amount", 0))
        if po_amount == amount:
            score += 25
        else:
            flags.append("amount_mismatch")
    else:
        flags.append("no_po")

    # 3.3 Historical Analysis
    avg_past_amount = 0.0
    if vendor_invoices:
        total_hist = sum(float(inv.get("amount", 0.0)) for inv in vendor_invoices)
        avg_past_amount = total_hist / len(vendor_invoices)
        
        if amount > 1.5 * avg_past_amount:
            flags.append("price_spike")
        else:
            score += 20
    else:
        # No history, let's say neutral or we give partial score rule says "Else -> +20"
        score += 20

    # 3.4 GST Validation
    if 0 <= gst <= 28:
        score += 15
    else:
        flags.append("gst_invalid")

    # 3.5 Duplicate Detection
    is_dupe = await analysis_check_duplicate_invoice(invoice_id, vendor, amount)
    if is_dupe:
        flags.append("duplicate")
    else:
        score += 10

    # 5. Decision Engine
    logger.info(f"📊 Confidence score: {score}")
    status = "auto_approved" if score >= 80 else "needs_review"

    # 7. Save Results (this also does Step 8: Update document status)
    result_data = {
        "document_id": document_id,
        "user_id": user_id,
        "confidence_score": score,
        "status": status,
        "flags": flags
    }
    await analysis_save_results(result_data)

    # 9. Send WhatsApp Message
    wa_message = await generate_whatsapp_message(flags, amount, avg_past_amount, score, status)
    if user_phone:
        await send_whatsapp_notification(user_phone, wa_message)

    # 10. Trigger Local ERP Automation if auto approved
    if status == "auto_approved":
        await trigger_local_automation(vendor, amount, gst, invoice_id, po_number)

    return result_data
