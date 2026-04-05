"""
services/decision.py
─────────────────────
Decision engine for AutoTwin AI.
Maps a ConfidenceSchema + AnomalyResult to a final DecisionSchema,
including the action taken, status, and human-readable explanation.
"""

from __future__ import annotations

import logging

from models.schemas import AnomalyResult, ConfidenceSchema, DecisionSchema

logger = logging.getLogger("autotwin_ai.decision")

# ── Confidence thresholds ─────────────────────────────────────
HIGH_CONFIDENCE  = 0.95   # auto-execute
MEDIUM_CONFIDENCE = 0.70  # warn but proceed


class DecisionEngine:
    """
    Produces a DecisionSchema from a confidence score and anomaly signal.

    Decision matrix
    ───────────────
    confidence > 0.95  → auto_execute   | risk: low
    confidence > 0.70  → warn           | risk: medium
    else               → human_review   | risk: high

    Anomalies always raise the risk level at least to 'medium'.
    """

    def decide(
        self,
        confidence_schema: ConfidenceSchema,
        anomaly_result: AnomalyResult,
    ) -> DecisionSchema:
        score   = confidence_schema.score
        anomaly = anomaly_result.is_anomaly

        # ── Core decision logic ────────────────────────────────
        if score > HIGH_CONFIDENCE and not anomaly:
            decision      = "auto_execute"
            requires_human = False
            risk_level    = "low"
            status        = "approved"
        elif score > MEDIUM_CONFIDENCE:
            decision      = "warn"
            requires_human = False
            # Anomaly bumps medium → medium/high
            risk_level    = "high" if anomaly else "medium"
            status        = "processed_with_warning"
        else:
            decision      = "human_review"
            requires_human = True
            risk_level    = "high"
            status        = "needs_review"

        # ── Action string ──────────────────────────────────────
        action = self._build_action(decision, anomaly_result)

        # ── Explanation ────────────────────────────────────────
        explanation = self._build_explanation(
            decision=decision,
            score=score,
            anomaly=anomaly,
            anomaly_result=anomaly_result,
            risk_level=risk_level,
        )

        schema = DecisionSchema(
            decision=decision,
            action=action,
            explanation=explanation,
            requires_human=requires_human,
            risk_level=risk_level,
            status=status,
        )

        logger.info(
            "[DecisionEngine] decision=%s risk=%s requires_human=%s score=%.4f",
            decision, risk_level, requires_human, score,
        )
        return schema

    # ──────────────────────────────────────────────────────────
    # Action builder
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_action(decision: str, anomaly_result: AnomalyResult) -> str:
        base_actions = {
            "auto_execute": "Invoice automatically approved and submitted to ERP system.",
            "warn":         "Invoice processed and logged; finance team notified via email.",
            "human_review": "Invoice held in review queue; awaiting manual approval.",
        }
        base = base_actions.get(decision, "Action undetermined.")

        # Append anomaly-specific suffix
        if anomaly_result.is_anomaly and anomaly_result.anomaly_type:
            suffixes = {
                "price_spike":      " Pricing alert raised for finance review.",
                "duplicate":        " Duplicate flagged; original invoice cross-referenced.",
                "unusual_vendor":   " Vendor verification request sent to procurement.",
            }
            base += suffixes.get(anomaly_result.anomaly_type, "")
        return base

    # ──────────────────────────────────────────────────────────
    # Explanation builder
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_explanation(
        decision: str,
        score: float,
        anomaly: bool,
        anomaly_result: AnomalyResult,
        risk_level: str,
    ) -> str:
        score_pct = f"{score:.0%}"

        base_explanations = {
            "auto_execute": (
                f"Confidence score of {score_pct} exceeds the {HIGH_CONFIDENCE:.0%} "
                f"auto-execution threshold with no anomalies detected. "
                f"The invoice has been autonomously approved."
            ),
            "warn": (
                f"Confidence score of {score_pct} is within the acceptable range "
                f"({MEDIUM_CONFIDENCE:.0%}–{HIGH_CONFIDENCE:.0%}) but does not meet "
                f"the auto-execution bar. Invoice has been processed with a warning flag."
            ),
            "human_review": (
                f"Confidence score of {score_pct} is below the {MEDIUM_CONFIDENCE:.0%} "
                f"threshold required for autonomous processing. "
                f"Manual review is required before this invoice can be approved."
            ),
        }
        explanation = base_explanations.get(decision, f"Decision: {decision}.")

        if anomaly and anomaly_result.anomaly_type:
            anomaly_context = {
                "price_spike": (
                    f" Additionally, a price spike was detected "
                    f"({anomaly_result.deviation_percentage or 0:.1f}% above historical average): "
                    f"{anomaly_result.explanation}"
                ),
                "duplicate": (
                    f" A potential duplicate invoice was also identified: "
                    f"{anomaly_result.explanation}"
                ),
                "unusual_vendor": (
                    f" The vendor appears unusual or unrecognised: "
                    f"{anomaly_result.explanation}"
                ),
            }
            explanation += anomaly_context.get(anomaly_result.anomaly_type, "")

        explanation += f" Overall risk level: {risk_level.upper()}."
        return explanation
