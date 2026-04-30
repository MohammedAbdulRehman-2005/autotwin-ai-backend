"""
services/agents/vision_agent.py
────────────────────────────────
Extracts structured invoice data from raw file content or a pre-parsed JSON dict.
Simulates OCR / NLP extraction with confidence scoring.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from models.schemas import ExtractionResult

logger = logging.getLogger("autotwin_ai.vision_agent")

# ── Regex patterns ────────────────────────────────────────────
_VENDOR_PATTERNS = [
    re.compile(r"(?:vendor|supplier|from|bill\s+to)[:\s]+([A-Za-z0-9 &.,'-]{3,60})", re.I),
    re.compile(r"^([A-Z][A-Za-z0-9 &.,'-]{2,50})\s*\n", re.M),
]
_AMOUNT_PATTERNS = [
    re.compile(r"(?:total|amount|grand\s+total|payable)[:\s₹$€£]*([0-9,]+(?:\.[0-9]{1,2})?)", re.I),
    re.compile(r"[₹$€£]\s*([0-9,]+(?:\.[0-9]{1,2})?)"),
]
_DATE_PATTERNS = [
    re.compile(r"(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})"),
    re.compile(r"(\d{4}-\d{2}-\d{2})"),
    re.compile(r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s,]+\d{4})", re.I),
]
_CURRENCY_MAP = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP"}


class VisionAgent:
    """
    Extracts invoice fields from file content (text / OCR output) or
    a pre-validated JSON dict (demo / API mode).
    """

    async def extract(
        self,
        file_content: Optional[str] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> ExtractionResult:
        """
        Main entry point.

        Priority: json_data > file_content
        Falls back to synthetic defaults if neither yields all fields.
        """
        logger.info("[VisionAgent] Starting extraction | mode=%s",
                    "json" if json_data else "file" if file_content else "fallback")

        await asyncio.sleep(0.5)  # Simulate processing delay

        if json_data:
            return await self._extract_from_json(json_data)
        if file_content:
            return await self._extract_from_text(file_content)

        logger.warning("[VisionAgent] No input supplied — returning low-confidence fallback.")
        return self._fallback_result()

    # ──────────────────────────────────────────────────────────
    # JSON path (demo / API submit)
    # ──────────────────────────────────────────────────────────

    async def _extract_from_json(self, data: Dict[str, Any]) -> ExtractionResult:
        logger.info("[VisionAgent] Extracting from JSON dict | keys=%s", list(data.keys()))

        vendor = str(data.get("vendor") or "").strip()
        raw_amount = data.get("amount") or data.get("total")
        date = str(data.get("date") or "").strip()
        currency = str(data.get("currency") or "INR").strip().upper()

        try:
            amount = float(raw_amount) if raw_amount is not None else 0.0
        except (ValueError, TypeError):
            amount = 0.0

        date = self._normalise_date(date)

        missing = sum([not vendor, amount == 0.0, not date])
        confidence = self._score_from_missing(missing)

        # Build line items if present
        raw_items = data.get("line_items")
        line_items = None
        if raw_items and isinstance(raw_items, list):
            from models.schemas import LineItem
            line_items = [
                LineItem(
                    description=str(item.get("description") or ""),
                    quantity=float(item["quantity"]) if item.get("quantity") is not None else None,
                    unit_price=float(item["unit_price"]) if item.get("unit_price") is not None else None,
                    amount=float(item["amount"]) if item.get("amount") is not None else None,
                )
                for item in raw_items if isinstance(item, dict)
            ]

        logger.info(
            "[VisionAgent] JSON extraction done | vendor=%r amount=%s date=%s conf=%.2f",
            vendor, amount, date, confidence,
        )
        return ExtractionResult(
            vendor=vendor or "Unknown Vendor",
            company=data.get("company") or None,
            invoice_no=data.get("invoice_no") or None,
            amount=amount,
            date=date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            due_date=data.get("due_date") or None,
            payment_terms=data.get("payment_terms") or None,
            subtotal=float(data["subtotal"]) if data.get("subtotal") else None,
            gst_rate=float(data["gst_rate"]) if data.get("gst_rate") else None,
            gst_amount=float(data["gst_amount"]) if data.get("gst_amount") else None,
            currency=currency,
            seller_gstin=data.get("seller_gstin") or None,
            buyer_gstin=data.get("buyer_gstin") or None,
            line_items=line_items,
            notes=data.get("notes") or None,
            extraction_confidence=confidence,
        )

    # ──────────────────────────────────────────────────────────
    # Text / OCR path
    # ──────────────────────────────────────────────────────────

    async def _extract_from_text(self, text: str) -> ExtractionResult:
        logger.info("[VisionAgent] Extracting from text | length=%d chars", len(text))

        vendor = self._extract_vendor(text)
        amount, currency = self._extract_amount(text)
        date = self._extract_date(text)

        logger.debug("[VisionAgent] Raw extractions | vendor=%r amount=%s date=%s", vendor, amount, date)

        missing = sum([not vendor, amount == 0.0, not date])
        confidence = self._score_from_missing(missing)

        vendor = vendor or "Unknown Vendor"
        date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

        logger.info(
            "[VisionAgent] Text extraction done | vendor=%r amount=%s date=%s conf=%.2f",
            vendor, amount, date, confidence,
        )
        return ExtractionResult(
            vendor=vendor,
            amount=amount,
            date=date,
            extraction_confidence=confidence,
            currency=currency,
        )

    # ──────────────────────────────────────────────────────────
    # Field extractors
    # ──────────────────────────────────────────────────────────

    def _extract_vendor(self, text: str) -> Optional[str]:
        for pattern in _VENDOR_PATTERNS:
            match = pattern.search(text)
            if match:
                candidate = match.group(1).strip().rstrip(".,")
                if len(candidate) >= 3:
                    logger.debug("[VisionAgent] Vendor matched by pattern: %r", candidate)
                    return candidate
        return None

    def _extract_amount(self, text: str) -> tuple[float, str]:
        currency = "INR"
        # Detect currency symbol
        for symbol, code in _CURRENCY_MAP.items():
            if symbol in text:
                currency = code
                break

        for pattern in _AMOUNT_PATTERNS:
            match = pattern.search(text)
            if match:
                raw = match.group(1).replace(",", "")
                try:
                    amount = float(raw)
                    logger.debug("[VisionAgent] Amount matched: %s %s", currency, amount)
                    return amount, currency
                except ValueError:
                    continue
        return 0.0, currency

    def _extract_date(self, text: str) -> Optional[str]:
        for pattern in _DATE_PATTERNS:
            match = pattern.search(text)
            if match:
                raw = match.group(1)
                normalised = self._normalise_date(raw)
                if normalised:
                    logger.debug("[VisionAgent] Date matched: %r → %s", raw, normalised)
                    return normalised
        return None

    # ──────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_date(raw: str) -> Optional[str]:
        """Try multiple format parsers; return ISO YYYY-MM-DD or None."""
        formats = [
            "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
            "%d/%m/%y", "%d-%m-%y",
            "%d %B %Y", "%d %b %Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return raw if raw else None

    @staticmethod
    def _score_from_missing(missing_fields: int) -> float:
        """
        Confidence heuristic:
          0 missing  → 0.92  (all fields found cleanly)
          1 missing  → 0.75  (some fields absent)
          2+ missing → 0.55  (mostly guessed)
        """
        scores = {0: 0.92, 1: 0.75}
        return scores.get(missing_fields, 0.55)

    @staticmethod
    def _fallback_result() -> ExtractionResult:
        return ExtractionResult(
            vendor="Unknown Vendor",
            amount=0.0,
            date=datetime.utcnow().strftime("%Y-%m-%d"),
            extraction_confidence=0.55,
            currency="INR",
        )
