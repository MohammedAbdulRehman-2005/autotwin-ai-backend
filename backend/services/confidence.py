"""
services/confidence.py
───────────────────────
Weighted confidence engine for the AutoTwin AI pipeline.
Combines extraction quality, anomaly pattern score, and vendor history
into a single ConfidenceSchema with full breakdown and natural-language reasoning.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from models.schemas import ConfidenceSchema

logger = logging.getLogger("autotwin_ai.confidence")

# ── Weight constants (must sum to 1.0) ────────────────────────
EXTRACTION_WEIGHT  = 0.4
PATTERN_WEIGHT     = 0.3
HISTORICAL_WEIGHT  = 0.3


class ConfidenceEngine:
    """
    Calculates a composite confidence score from three independent signals:

        confidence = (0.4 × extraction_confidence)
                   + (0.3 × pattern_score)
                   + (0.3 × historical_consistency)
    """

    def calculate(
        self,
        extraction_confidence: float,
        pattern_score: float,
        historical_consistency: float,
    ) -> ConfidenceSchema:
        """
        Args:
            extraction_confidence: 0-1 score from VisionAgent (field quality).
            pattern_score:         0-1 score from AnalyticsAgent (1=clean, 0.2=major anomaly).
            historical_consistency: 0-1 score from MemoryGraph
                                    (1.0=known normal, 0.5=new vendor, 0.3=flagged).
        Returns:
            ConfidenceSchema with score, breakdown, reasoning, and risk_score.
        """
        # Clamp all inputs to [0, 1]
        ext  = max(0.0, min(1.0, extraction_confidence))
        pat  = max(0.0, min(1.0, pattern_score))
        hist = max(0.0, min(1.0, historical_consistency))

        score = (
            EXTRACTION_WEIGHT  * ext
            + PATTERN_WEIGHT   * pat
            + HISTORICAL_WEIGHT * hist
        )
        score = round(score, 4)

        breakdown: Dict[str, Any] = {
            "extraction": {
                "score":  round(ext, 4),
                "weight": EXTRACTION_WEIGHT,
                "contribution": round(EXTRACTION_WEIGHT * ext, 4),
            },
            "pattern": {
                "score":  round(pat, 4),
                "weight": PATTERN_WEIGHT,
                "contribution": round(PATTERN_WEIGHT * pat, 4),
            },
            "historical": {
                "score":  round(hist, 4),
                "weight": HISTORICAL_WEIGHT,
                "contribution": round(HISTORICAL_WEIGHT * hist, 4),
            },
            "total": score,
            "risk_score": round(1.0 - score, 4),
        }

        reasoning = self._build_reasoning(ext, pat, hist, score)

        logger.info(
            "[ConfidenceEngine] score=%.4f extraction=%.2f pattern=%.2f historical=%.2f",
            score, ext, pat, hist,
        )

        return ConfidenceSchema(
            score=score,
            extraction_weight=EXTRACTION_WEIGHT,
            pattern_weight=PATTERN_WEIGHT,
            historical_weight=HISTORICAL_WEIGHT,
            extraction_score=ext,
            pattern_score=pat,
            historical_score=hist,
            breakdown=breakdown,
            reasoning=reasoning,
        )

    # ──────────────────────────────────────────────────────────
    # Natural-language reasoning builder
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_reasoning(ext: float, pat: float, hist: float, score: float) -> str:
        parts: list[str] = []

        # Extraction narrative
        if ext >= 0.90:
            parts.append(f"Extraction was clean ({ext:.2f})")
        elif ext >= 0.75:
            parts.append(f"Extraction had minor gaps ({ext:.2f})")
        else:
            parts.append(f"Extraction quality was poor ({ext:.2f}) — several fields were guessed")

        # Pattern narrative
        if pat >= 0.95:
            parts.append("no anomalies found in pricing patterns")
        elif pat >= 0.60:
            parts.append(f"a minor anomaly was detected in the pricing pattern ({pat:.2f})")
        elif pat >= 0.35:
            parts.append(f"significant anomaly detected in pricing pattern ({pat:.2f}), pulling confidence down")
        else:
            parts.append(f"anomaly detected in pricing pattern ({pat:.2f}) — major deviation flagged")

        # Historical narrative
        if hist >= 0.90:
            parts.append("vendor history is consistent and trusted")
        elif hist >= 0.50:
            parts.append("vendor is new with limited transaction history")
        else:
            parts.append("vendor has been previously risk-flagged")

        # Overall verdict
        if score >= 0.95:
            verdict = "High confidence — pipeline will auto-execute."
        elif score >= 0.70:
            verdict = "Medium confidence — proceeding with a warning."
        else:
            verdict = "Low confidence — routing to human review."

        sentence = "; ".join(parts).capitalize() + ". " + verdict
        return sentence
