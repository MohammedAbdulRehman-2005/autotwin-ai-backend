"""
services/memory.py
───────────────────
In-memory vendor knowledge graph for AutoTwin AI.
Tracks transaction history, price trends, risk flags, and historical
consistency scores without any external DB dependency.
Pre-seeded with demo vendors for out-of-the-box testing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autotwin_ai.memory")

# ── Historical consistency thresholds ────────────────────────
PRICE_VARIANCE_TOLERANCE = 0.30   # 30 % deviation is still "consistent"
MIN_TRANSACTIONS_FOR_TRUST = 3    # vendor needs ≥ 3 transactions to be "trusted"

# ── Pre-seeded demo vendors ───────────────────────────────────
_DEMO_SEED: Dict[str, Dict[str, Any]] = {
    "TechnoVendor Inc.": {
        "avg_price": 5000.0,
        "transaction_count": 12,
        "last_seen": "2024-03-01",
        "risk_flags": [],
        "price_trend": [4800.0, 5100.0, 5000.0, 4900.0, 5000.0],
    },
    "CloudServe Ltd.": {
        "avg_price": 8000.0,
        "transaction_count": 8,
        "last_seen": "2024-02-20",
        "risk_flags": [],
        "price_trend": [7800.0, 8200.0, 8000.0, 7950.0, 8100.0],
    },
    "DataPipe Co.": {
        "avg_price": 2000.0,
        "transaction_count": 20,
        "last_seen": "2024-03-10",
        "risk_flags": [],
        "price_trend": [1950.0, 2050.0, 2000.0, 1980.0, 2020.0],
    },
}


class MemoryGraph:
    """
    In-memory vendor knowledge graph.
    Stores per-vendor price statistics, risk flags, and price-trend lists.
    """

    def __init__(self) -> None:
        # Deep-copy seed so tests start fresh
        self._graph: Dict[str, Dict[str, Any]] = {
            vendor: dict(data) for vendor, data in _DEMO_SEED.items()
        }

    # ──────────────────────────────────────────────────────────
    # Read
    # ──────────────────────────────────────────────────────────

    def get_vendor_history(self, vendor: str) -> Dict[str, Any]:
        """
        Returns the vendor profile dict.

        Shape::
            {
                "avg_price":          float,
                "transaction_count":  int,
                "last_seen":          str (ISO date),
                "risk_flags":         List[str],
                "price_trend":        List[float],
                "known":              bool,
            }
        """
        normalised = self._normalise(vendor)
        record = self._find(normalised)
        if record:
            logger.debug("[MemoryGraph] Vendor found: %r | txns=%d", vendor, record["transaction_count"])
            return {**record, "known": True}

        logger.debug("[MemoryGraph] Vendor not found: %r — returning empty profile", vendor)
        return {
            "avg_price": 0.0,
            "transaction_count": 0,
            "last_seen": "",
            "risk_flags": [],
            "price_trend": [],
            "known": False,
        }

    def get_all_vendors(self) -> List[Dict[str, Any]]:
        """Return a list of all vendor profiles with their names."""
        return [{"vendor": name, **data} for name, data in self._graph.items()]

    # ──────────────────────────────────────────────────────────
    # Write
    # ──────────────────────────────────────────────────────────

    def update_vendor_data(
        self,
        vendor: str,
        amount: float,
        date: str,
        anomaly: bool,
    ) -> None:
        """
        Update (or create) a vendor entry after a processed invoice.
        Recalculates running average and appends to price trend.
        """
        normalised = self._normalise(vendor)
        existing   = self._find(normalised)

        if existing:
            key = self._find_key(normalised)
            count = existing["transaction_count"]
            new_avg = ((existing["avg_price"] * count) + amount) / (count + 1)
            trend   = existing["price_trend"][-9:] + [amount]   # keep last 10

            flags: List[str] = list(existing.get("risk_flags", []))
            if anomaly and "anomaly" not in flags:
                flags.append("anomaly")

            self._graph[key].update({
                "avg_price": round(new_avg, 2),
                "transaction_count": count + 1,
                "last_seen": date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "risk_flags": flags,
                "price_trend": trend,
            })
            logger.info(
                "[MemoryGraph] Updated vendor %r | new_avg=%.2f txns=%d anomaly=%s",
                vendor, new_avg, count + 1, anomaly,
            )
        else:
            # First time we see this vendor
            self._graph[vendor] = {
                "avg_price": round(amount, 2),
                "transaction_count": 1,
                "last_seen": date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "risk_flags": ["anomaly"] if anomaly else [],
                "price_trend": [amount],
            }
            logger.info("[MemoryGraph] New vendor registered: %r | amount=%.2f", vendor, amount)

    # ──────────────────────────────────────────────────────────
    # Consistency scoring
    # ──────────────────────────────────────────────────────────

    def calculate_historical_consistency(self, vendor: str, amount: float) -> float:
        """
        Returns a [0, 1] consistency score based on vendor history:

          1.0 — known vendor, amount within tolerance, no risk flags
          0.8 — known vendor, amount within tolerance, minor flag
          0.5 — new vendor (< MIN_TRANSACTIONS_FOR_TRUST)
          0.3 — vendor has been risk-flagged previously
        """
        normalised = self._normalise(vendor)
        record = self._find(normalised)

        if not record:
            logger.debug("[MemoryGraph] Consistency: new vendor %r → 0.5", vendor)
            return 0.5

        risk_flags = record.get("risk_flags", [])
        if risk_flags:
            logger.debug("[MemoryGraph] Consistency: vendor %r has flags %s → 0.3", vendor, risk_flags)
            return 0.3

        count = record["transaction_count"]
        if count < MIN_TRANSACTIONS_FOR_TRUST:
            logger.debug(
                "[MemoryGraph] Consistency: vendor %r low history (%d txns) → 0.5",
                vendor, count,
            )
            return 0.5

        avg = record["avg_price"]
        if avg > 0:
            deviation = abs(amount - avg) / avg
            in_tolerance = deviation <= PRICE_VARIANCE_TOLERANCE
        else:
            in_tolerance = True

        score = 1.0 if in_tolerance else 0.7
        logger.debug(
            "[MemoryGraph] Consistency: vendor %r avg=%.2f current=%.2f → %.1f",
            vendor, avg, amount, score,
        )
        return score

    # ──────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _normalise(vendor: str) -> str:
        return vendor.strip().lower()

    def _find(self, normalised: str) -> Optional[Dict[str, Any]]:
        """Case-insensitive key lookup."""
        for key, value in self._graph.items():
            if key.strip().lower() == normalised:
                return value
        return None

    def _find_key(self, normalised: str) -> Optional[str]:
        """Return the original-case key for a normalised vendor string."""
        for key in self._graph:
            if key.strip().lower() == normalised:
                return key
        return None
