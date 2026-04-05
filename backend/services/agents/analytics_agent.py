"""
services/agents/analytics_agent.py
────────────────────────────────────
Anomaly detection engine: price-spike, duplicate invoice, and unusual-vendor detection.
Produces a pattern_score (0-1) and a human-readable explanation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from models.schemas import AnomalyResult, ExtractionResult

logger = logging.getLogger("autotwin_ai.analytics_agent")

# ── Tunable thresholds ────────────────────────────────────────
PRICE_SPIKE_MULTIPLIER = 1.5       # current > avg * 1.5  → spike
DUPLICATE_WINDOW_DAYS  = 7         # same vendor+amount within N days
MIN_HISTORY_FOR_SPIKE  = 2         # need at least N records to flag spike

# Known 'trusted' vendors loaded at startup (extendable via DB)
_KNOWN_VENDORS: set[str] = {
    "tata consultancy services",
    "infosys",
    "wipro",
    "hcl technologies",
    "accenture",
    "ibm india",
    "amazon web services",
    "microsoft",
    "google cloud",
    "oracle",
}


class AnalyticsAgent:
    """
    Receives an ExtractionResult plus historical invoice records for the
    same vendor, and returns an AnomalyResult with a pattern_score.
    """

    async def analyze(
        self,
        extraction: ExtractionResult,
        vendor_history: List[dict],
    ) -> AnomalyResult:
        """
        Run all anomaly detectors and return the most severe finding.
        pattern_score is attached as a custom attribute on the returned model
        so ConfidenceManager can consume it without schema changes.
        """
        logger.info(
            "[AnalyticsAgent] Analysing vendor=%r amount=%.2f history_records=%d",
            extraction.vendor, extraction.amount, len(vendor_history),
        )

        vendor_lower = extraction.vendor.strip().lower()

        # ── 1. Duplicate detection ──────────────────────────────
        is_dup = self.detect_duplicate(
            vendor=extraction.vendor,
            amount=extraction.amount,
            date=extraction.date,
            recent_invoices=vendor_history,
        )
        if is_dup:
            explanation = (
                f"Duplicate invoice detected: {extraction.vendor} submitted "
                f"₹{extraction.amount:,.2f} again within {DUPLICATE_WINDOW_DAYS} days."
            )
            logger.warning("[AnalyticsAgent] DUPLICATE flagged | %s", explanation)
            result = AnomalyResult(
                is_anomaly=True,
                anomaly_type="duplicate",
                deviation_percentage=0.0,
                explanation=explanation,
            )
            result.__dict__["pattern_score"] = 0.3  # moderately severe
            return result

        # ── 2. Price-spike detection ────────────────────────────
        historical_amounts = [
            float(r["amount"]) for r in vendor_history if "amount" in r
        ]
        is_spike, deviation = self.detect_price_spike(
            current_amount=extraction.amount,
            historical_amounts=historical_amounts,
        )
        if is_spike:
            avg = sum(historical_amounts) / len(historical_amounts)
            explanation = (
                f"Price spike detected for {extraction.vendor}: "
                f"₹{extraction.amount:,.2f} is {deviation:.1f}% above the "
                f"historical average of ₹{avg:,.2f}."
            )
            if deviation > 100:
                explanation = (
                    f"Vendor price increased {deviation/100:.1f}x compared to "
                    f"historical average (avg ₹{avg:,.2f} → now ₹{extraction.amount:,.2f})."
                )
            logger.warning("[AnalyticsAgent] PRICE SPIKE flagged | deviation=%.1f%%", deviation)
            result = AnomalyResult(
                is_anomaly=True,
                anomaly_type="price_spike",
                deviation_percentage=round(deviation, 2),
                explanation=explanation,
            )
            result.__dict__["pattern_score"] = self._spike_to_pattern_score(deviation)
            return result

        # ── 3. Unusual vendor detection ─────────────────────────
        is_unusual = self.detect_unusual_vendor(
            vendor=vendor_lower,
            known_vendors=_KNOWN_VENDORS,
        )
        if is_unusual and not vendor_history:
            # Only flag if there's zero history AND not in known list
            explanation = (
                f"Unusual vendor: '{extraction.vendor}' has no prior transaction "
                f"history and is not in the approved vendor registry."
            )
            logger.warning("[AnalyticsAgent] UNUSUAL VENDOR flagged | %s", extraction.vendor)
            result = AnomalyResult(
                is_anomaly=True,
                anomaly_type="unusual_vendor",
                deviation_percentage=None,
                explanation=explanation,
            )
            result.__dict__["pattern_score"] = 0.6  # minor — needs review
            return result

        # ── 4. No anomaly ───────────────────────────────────────
        logger.info("[AnalyticsAgent] No anomaly detected for %r", extraction.vendor)
        result = AnomalyResult(
            is_anomaly=False,
            anomaly_type=None,
            deviation_percentage=None,
            explanation=(
                f"No anomalies detected. {extraction.vendor}'s invoice of "
                f"₹{extraction.amount:,.2f} is consistent with historical data."
            ),
        )
        result.__dict__["pattern_score"] = 1.0  # clean
        return result

    # ──────────────────────────────────────────────────────────
    # Detectors
    # ──────────────────────────────────────────────────────────

    def detect_price_spike(
        self,
        current_amount: float,
        historical_amounts: List[float],
    ) -> Tuple[bool, float]:
        """
        Returns (is_spike, deviation_percentage).
        Requires at least MIN_HISTORY_FOR_SPIKE records.
        """
        if len(historical_amounts) < MIN_HISTORY_FOR_SPIKE:
            logger.debug(
                "[AnalyticsAgent] Insufficient history (%d records) for spike check.",
                len(historical_amounts),
            )
            return False, 0.0

        avg = sum(historical_amounts) / len(historical_amounts)
        if avg == 0:
            return False, 0.0

        threshold = avg * PRICE_SPIKE_MULTIPLIER
        deviation = ((current_amount - avg) / avg) * 100

        logger.debug(
            "[AnalyticsAgent] Price check | current=%.2f avg=%.2f threshold=%.2f dev=%.1f%%",
            current_amount, avg, threshold, deviation,
        )
        return current_amount > threshold, deviation

    def detect_duplicate(
        self,
        vendor: str,
        amount: float,
        date: str,
        recent_invoices: List[dict],
    ) -> bool:
        """
        Returns True if any recent invoice has same vendor + same amount
        within DUPLICATE_WINDOW_DAYS of *date*.
        """
        try:
            invoice_date = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            logger.debug("[AnalyticsAgent] Cannot parse date %r for duplicate check.", date)
            return False

        window_start = invoice_date - timedelta(days=DUPLICATE_WINDOW_DAYS)

        for record in recent_invoices:
            try:
                rec_date = datetime.strptime(str(record.get("date", "")), "%Y-%m-%d")
            except ValueError:
                continue

            same_vendor = record.get("vendor", "").strip().lower() == vendor.strip().lower()
            same_amount = abs(float(record.get("amount", -1)) - amount) < 0.01
            in_window   = window_start <= rec_date <= invoice_date

            if same_vendor and same_amount and in_window:
                logger.debug(
                    "[AnalyticsAgent] Duplicate found | record_date=%s amount=%.2f",
                    rec_date.date(), amount,
                )
                return True
        return False

    def detect_unusual_vendor(
        self,
        vendor: str,
        known_vendors: set[str],
    ) -> bool:
        """Returns True if vendor is NOT in the known-vendors set."""
        return vendor.lower() not in known_vendors

    # ──────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _spike_to_pattern_score(deviation_pct: float) -> float:
        """
        Map deviation percentage to a pattern_score (0–1):
          0%   deviation  → 1.0  (no anomaly)
          50%  deviation  → 0.6  (minor spike)
          100% deviation  → 0.4  (significant spike)
          >100% deviation → 0.2  (major spike / >2× average)
        """
        if deviation_pct <= 0:
            return 1.0
        if deviation_pct < 50:
            return 0.6
        if deviation_pct < 100:
            return 0.4
        return 0.2
