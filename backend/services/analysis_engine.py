"""
services/analysis_engine.py
────────────────────────────
Core brain of AutoTwin AI.

Steps:
1. Idempotency check
2. Fetch document from extracted_documents
3. Fetch PO, vendor history, user phone
4. Run 5 validation rules → confidence score + flags
5. Decide: auto_approved (>=80) or needs_review
6. Save results to invoice_analysis + update doc status
7. Send personalized WhatsApp message
   - If needs_review: append APPROVE/REJECT prompt
"""
import logging
import httpx
from typing import Dict, Any, List, Optional

from core.config import settings
from models.database import (
    analysis_check_idempotency,
    analysis_get_extracted_document,
    analysis_get_document_by_invoice_id,
    analysis_get_purchase_order,
    analysis_get_vendor_invoices,
    analysis_get_user_phone,
    analysis_check_duplicate_invoice,
    analysis_save_results,
)

logger = logging.getLogger("autotwin_ai.analysis_engine")

# ──────────────────────────────────────────────────────────────
# WhatsApp Message Generator (Groq LLM + fallback)
# ──────────────────────────────────────────────────────────────

async def generate_whatsapp_message(
    flags: List[str],
    amount: float,
    avg: float,
    confidence: int,
    status: str,
    doc: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate a rich, dynamic WhatsApp summary using Groq LLM."""
    doc = doc or {}
    vendor       = doc.get("vendor", "Unknown")
    company      = doc.get("company", "")
    invoice_no   = doc.get("invoice_no", "")
    gst_rate     = doc.get("gst_rate", 0.0)
    gst_amount   = doc.get("gst_amount", 0.0)
    subtotal     = doc.get("subtotal", 0.0)
    due_date     = doc.get("due_date", "")
    payment_terms = doc.get("payment_terms", "")
    notes        = doc.get("notes", "")

    if not settings.GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not set — using fallback message.")
        return _fallback_message(flags, amount, avg, confidence, status, doc)

    prompt = f"""You are AutoTwin AI, an invoice analysis assistant. 
Write a concise, professional WhatsApp message summarizing this invoice analysis.
Use emojis. Be specific to the actual data — no generic text.

Invoice Data:
- Vendor: {vendor}
- Company (buyer): {company or 'N/A'}
- Invoice No: {invoice_no or 'N/A'}
- Subtotal: ₹{subtotal}
- GST: {gst_rate}% = ₹{gst_amount}
- Total: ₹{amount}
- Historical Avg: ₹{avg:.2f}
- Due Date: {due_date or 'N/A'}
- Payment Terms: {payment_terms or 'N/A'}
- Notes: {notes or 'None'}
- Flags: {', '.join(flags) if flags else 'None'}
- Confidence Score: {confidence}%
- Decision: {status}

Format (follow roughly):
📄 AutoTwin Invoice Alert
[vendor + invoice no]
[key financial breakdown with GST]
[any flags as warnings]
⚡ Confidence: X%
[Status line]

Keep it under 220 words. WhatsApp-friendly (no markdown headers)."""

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 350,
                },
                timeout=12.0,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Groq message generation failed: {e}")
        return _fallback_message(flags, amount, avg, confidence, status, doc)


def _fallback_message(
    flags: List[str],
    amount: float,
    avg: float,
    confidence: int,
    status: str,
    doc: Dict[str, Any],
) -> str:
    vendor     = doc.get("vendor", "Unknown")
    invoice_no = doc.get("invoice_no", "")
    gst_rate   = doc.get("gst_rate", 0.0)
    gst_amount = doc.get("gst_amount", 0.0)
    subtotal   = doc.get("subtotal", 0.0)
    due_date   = doc.get("due_date", "")

    lines = ["📄 *AutoTwin Invoice Alert*\n"]
    lines.append(f"🏢 Vendor: {vendor}" + (f" | Invoice #{invoice_no}" if invoice_no else ""))
    if subtotal:
        lines.append(f"💵 Subtotal: ₹{subtotal:,.2f}")
    if gst_rate:
        lines.append(f"🧾 GST ({gst_rate}%): ₹{gst_amount:,.2f}")
    lines.append(f"💰 Total: ₹{amount:,.2f}")
    if avg > 0:
        diff_pct = ((amount - avg) / avg * 100) if avg else 0
        trend = f"(+{diff_pct:.1f}% vs avg)" if diff_pct > 0 else f"({diff_pct:.1f}% vs avg)"
        lines.append(f"📈 Historical Avg: ₹{avg:,.2f} {trend}")
    if due_date:
        lines.append(f"📅 Due: {due_date}")

    lines.append("")
    for flag in flags:
        if flag == "price_spike":
            lines.append("⚠️ Price spike detected (>1.5× avg)")
        elif flag == "gst_invalid":
            lines.append("⚠️ Invalid GST rate (outside 0-28%)")
        elif flag == "duplicate":
            lines.append("⚠️ Possible duplicate invoice")
        elif flag == "no_po":
            lines.append("⚠️ No matching PO found")
        elif flag == "amount_mismatch":
            lines.append("⚠️ Amount does not match PO")
    if not flags:
        lines.append("✅ All checks passed")

    lines.append("")
    lines.append(f"⚡ Confidence: {confidence}%")
    status_icon = "✅" if status == "auto_approved" else "❗"
    status_text = "Auto Approved" if status == "auto_approved" else "Needs Review"
    lines.append(f"{status_icon} Status: *{status_text}*")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# WhatsApp Dispatcher
# ──────────────────────────────────────────────────────────────

async def send_whatsapp_notification(phone_number: str, message: str) -> None:
    from services.whatsapp_client import send_whatsapp_message
    if not phone_number:
        logger.warning("No phone number — skipping WhatsApp notification.")
        return
    logger.info(f"📤 Preparing to send WhatsApp to {phone_number}...")
    try:
        await send_whatsapp_message(phone_number, message)
    except Exception as e:
        logger.error(f"WhatsApp dispatch failed: {e}")


# ──────────────────────────────────────────────────────────────
# Core Analysis Pipeline
# ──────────────────────────────────────────────────────────────

async def process_invoice_analysis(document_id: str) -> Dict[str, Any]:
    """
    Main pipeline: validates invoice, scores confidence, decides, saves, notifies.
    document_id = extracted_documents.id (UUID primary key)
    """
    logger.info(f"📥 Received document: {document_id}")

    # Step 1: Idempotency — skip if already processed
    if await analysis_check_idempotency(document_id):
        logger.info(f"Document {document_id} already processed — skipping.")
        return {"status": "already_processed", "document_id": document_id}

    # Step 2: Fetch the extracted document
    doc = await analysis_get_extracted_document(document_id)
    if not doc:
        raise ValueError(f"extracted_documents row not found for id={document_id}")

    user_id      = doc.get("user_id", "demo_user")
    po_number    = doc.get("po_number")
    vendor       = doc.get("vendor", "Unknown")
    amount       = float(doc.get("amount", 0.0))
    gst          = float(doc.get("gst") or doc.get("gst_rate") or 0.0)
    invoice_id   = doc.get("invoice_id", "")

    # Step 3: Fetch related data
    po_record      = await analysis_get_purchase_order(po_number) if po_number else None
    vendor_invoices = await analysis_get_vendor_invoices(vendor, user_id)
    user_phone     = await analysis_get_user_phone(user_id)

    logger.info("🧠 Running validation engine...")

    score = 0
    flags: List[str] = []

    # 3.1 PO Matching (+30)
    # 3.2 3-Way Matching (+25)
    if po_record:
        score += 30
        po_amount = float(po_record.get("amount", 0))
        if po_amount == amount:
            score += 25
        else:
            flags.append("amount_mismatch")
    else:
        flags.append("no_po")

    # 3.3 Historical Analysis (+20)
    avg_past = 0.0
    if vendor_invoices:
        avg_past = sum(float(inv.get("amount", 0.0)) for inv in vendor_invoices) / len(vendor_invoices)
        if amount > 1.5 * avg_past:
            flags.append("price_spike")
        else:
            score += 20
    else:
        score += 20  # No history → neutral

    # 3.4 GST Validation (+15)
    if 0 <= gst <= 28:
        score += 15
    else:
        flags.append("gst_invalid")

    # 3.5 Duplicate Detection (+10)
    if await analysis_check_duplicate_invoice(invoice_id, vendor, amount):
        flags.append("duplicate")
    else:
        score += 10

    logger.info(f"📊 Confidence score: {score} | Flags: {flags}")

    # Step 5: Decision
    status = "auto_approved" if score >= 80 else "needs_review"

    # Step 6: Save results + update doc status
    result = {
        "document_id": document_id,
        "user_id": user_id,
        "confidence_score": score,
        "status": status,
        "flags": flags,
    }
    await analysis_save_results(result)

    # Step 7: WhatsApp notification
    wa_msg = await generate_whatsapp_message(
        flags=flags,
        amount=amount,
        avg=avg_past,
        confidence=score,
        status=status,
        doc=doc,
    )

    final_phone = user_phone or getattr(settings, "WHATSAPP_DEFAULT_NUMBER", "")

    if final_phone:
        await send_whatsapp_notification(final_phone, wa_msg)

        # For review-required invoices, follow up with interactive Approve/Reject buttons
        if status == "needs_review":
            try:
                from services.whatsapp_client import send_interactive_buttons
                vendor_name = doc.get("vendor", "Unknown Vendor")
                await send_interactive_buttons(
                    to_phone=final_phone,
                    header="Action Required",
                    body=(
                        f"Vendor: {vendor_name}\n"
                        f"Amount: ₹{amount:,.2f}\n"
                        f"Confidence: {score}%\n\n"
                        "Please review this invoice and take action."
                    ),
                    footer=f"Invoice ID: {(invoice_id or document_id)[:8]}…",
                    buttons=[
                        {"id": f"approve:{invoice_id or document_id}", "title": "Approve"},
                        {"id": f"reject:{invoice_id or document_id}", "title": "Reject"},
                    ],
                )
            except Exception as btn_exc:
                logger.warning(f"Could not send approval buttons (non-fatal): {btn_exc}")
    else:
        logger.warning(f"⚠️ No phone number resolved for user {user_id}. WhatsApp notification skipped.")

    return result


async def process_invoice_analysis_by_invoice_id(invoice_id: str) -> Dict[str, Any]:
    """
    Alternate entry point called by the orchestrator's finalizer_node.
    Looks up the extracted_document by its invoice_id column (orchestrator UUID),
    then delegates to process_invoice_analysis with the document's own primary key.
    """
    logger.info(f"📥 Auto-triggered analysis for invoice_id={invoice_id}")
    doc = await analysis_get_document_by_invoice_id(invoice_id)
    if not doc:
        logger.warning(f"No extracted_document found for invoice_id={invoice_id} — skipping analysis.")
        return {"status": "not_found", "invoice_id": invoice_id}

    document_id = doc.get("document_id") or str(doc.get("id", ""))
    if not document_id:
        logger.warning("Could not resolve document primary key — skipping analysis.")
        return {"status": "no_document_id", "invoice_id": invoice_id}

    return await process_invoice_analysis(document_id)
