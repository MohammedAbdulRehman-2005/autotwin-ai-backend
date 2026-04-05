"""
services/agents/reflection_agent.py
─────────────────────────────────────
Meta-cognitive reflection layer.
Analyses completed pipeline execution, identifies failure patterns,
proposes corrective strategies, and stores them for future runs.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autotwin_ai.reflection_agent")

# ── Reflection thresholds ─────────────────────────────────────
LOW_CONFIDENCE_THRESHOLD  = 0.70
HIGH_RETRY_THRESHOLD      = 2
SPIKE_DEVIATION_THRESHOLD = 50.0   # %


class ReflectionAgent:
    """
    Analyses a completed pipeline result, generates improvement suggestions,
    estimates a confidence_impact, and persists strategies in memory for
    future pipeline runs.
    """

    def __init__(self) -> None:
        # Persistent strategy store: { failure_pattern: [strategy_str, ...] }
        self._strategy_memory: Dict[str, List[str]] = defaultdict(list)
        self._reflection_history: List[dict] = []

    async def reflect(self, pipeline_result: dict) -> dict:
        """
        Entry point.

        Args:
            pipeline_result: The full dict returned by the orchestrator, expected keys:
                confidence, retry_count, anomaly, anomaly_details, decision,
                vendor, amount, logs, processing_time_ms, risk_score.

        Returns:
            {
                "improvement_suggestions": List[str],
                "confidence_impact":       float,
                "failure_patterns":        List[str],
                "stored_strategies":       List[str],
                "reflection_summary":      str,
            }
        """
        logger.info("[ReflectionAgent] Starting reflection for invoice_id=%s",
                    pipeline_result.get("invoice_id", "unknown"))

        failure_patterns: List[str] = []
        suggestions: List[str]      = []
        confidence_impact: float    = 0.0

        confidence    = float(pipeline_result.get("confidence", 1.0))
        retry_count   = int(pipeline_result.get("retry_count", 0))
        anomaly       = bool(pipeline_result.get("anomaly", False))
        anomaly_det   = pipeline_result.get("anomaly_details") or {}
        decision      = str(pipeline_result.get("decision", ""))
        risk_score    = float(pipeline_result.get("risk_score", 0.0))
        proc_time_ms  = float(pipeline_result.get("processing_time_ms", 0.0))
        vendor        = str(pipeline_result.get("vendor", "unknown"))

        # ── Failure pattern: Low confidence ────────────────────
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            pattern = "low_extraction_confidence"
            failure_patterns.append(pattern)
            suggestion = (
                f"Confidence score {confidence:.2f} is below threshold "
                f"{LOW_CONFIDENCE_THRESHOLD}. Consider enriching the OCR pipeline "
                f"with a higher-resolution scan or a secondary NLP pass."
            )
            suggestions.append(suggestion)
            self._store_strategy(pattern, suggestion)
            confidence_impact += 0.08  # reflection adds ~8 % confidence
            logger.warning("[ReflectionAgent] Low confidence detected: %.2f", confidence)

        # ── Failure pattern: High retry count ──────────────────
        if retry_count >= HIGH_RETRY_THRESHOLD:
            pattern = "high_browser_retries"
            failure_patterns.append(pattern)
            suggestion = (
                f"Browser agent required {retry_count} retries. The ERP/sheet DOM "
                f"may have changed. Recommend updating CSS selectors or switching "
                f"to an API-based integration for '{vendor}'."
            )
            suggestions.append(suggestion)
            self._store_strategy(pattern, suggestion)
            confidence_impact += 0.05
            logger.warning("[ReflectionAgent] High retry count: %d", retry_count)

        # ── Failure pattern: Price spike anomaly ───────────────
        if anomaly and isinstance(anomaly_det, dict):
            anomaly_type = anomaly_det.get("anomaly_type") or ""
            deviation    = float(anomaly_det.get("deviation_percentage") or 0.0)

            if anomaly_type == "price_spike":
                pattern = "price_spike_anomaly"
                failure_patterns.append(pattern)
                if deviation > SPIKE_DEVIATION_THRESHOLD:
                    suggestion = (
                        f"Major price spike of {deviation:.1f}% detected for {vendor}. "
                        f"Recommend adding this vendor to the enhanced-monitoring list "
                        f"and requiring dual approval for invoices above their 90-day avg."
                    )
                else:
                    suggestion = (
                        f"Minor price deviation of {deviation:.1f}% for {vendor}. "
                        f"Update historical baseline to include this quarter's data."
                    )
                suggestions.append(suggestion)
                self._store_strategy(pattern, suggestion)
                confidence_impact += 0.03
                logger.info("[ReflectionAgent] Price spike reflected: %.1f%%", deviation)

            elif anomaly_type == "duplicate":
                pattern = "duplicate_invoice"
                failure_patterns.append(pattern)
                suggestion = (
                    f"Duplicate invoice detected for {vendor}. "
                    f"Recommend implementing invoice-hash fingerprinting at ingestion "
                    f"to block duplicates before they reach the pipeline."
                )
                suggestions.append(suggestion)
                self._store_strategy(pattern, suggestion)
                confidence_impact += 0.04
                logger.info("[ReflectionAgent] Duplicate invoice reflected.")

            elif anomaly_type == "unusual_vendor":
                pattern = "unusual_vendor"
                failure_patterns.append(pattern)
                suggestion = (
                    f"Unusual vendor '{vendor}' flagged. "
                    f"If this vendor is legitimate, add them to the approved registry "
                    f"to suppress future alerts and improve confidence scoring."
                )
                suggestions.append(suggestion)
                self._store_strategy(pattern, suggestion)
                confidence_impact += 0.04
                logger.info("[ReflectionAgent] Unusual vendor reflected: %s", vendor)

        # ── Failure pattern: Human review decision ─────────────
        if decision == "human_review":
            pattern = "human_review_required"
            if pattern not in failure_patterns:
                failure_patterns.append(pattern)
            suggestion = (
                "Pipeline routed to human review. After approval, the decision "
                "should be fed back into the historical model to improve future "
                "autonomous approvals for similar invoices."
            )
            suggestions.append(suggestion)
            self._store_strategy(pattern, suggestion)
            confidence_impact += 0.02

        # ── Failure pattern: Slow processing ──────────────────
        if proc_time_ms > 5000:
            pattern = "slow_processing"
            failure_patterns.append(pattern)
            suggestion = (
                f"Processing time {proc_time_ms:.0f}ms exceeds 5 s. "
                f"Consider parallelising vision extraction and analytics steps, "
                f"or adding a Redis cache layer for repeat vendors."
            )
            suggestions.append(suggestion)
            self._store_strategy(pattern, suggestion)
            confidence_impact += 0.01
            logger.info("[ReflectionAgent] Slow processing reflected: %.0f ms", proc_time_ms)

        # ── No failures found ──────────────────────────────────
        if not failure_patterns:
            suggestions.append(
                "Pipeline executed cleanly with no failure patterns. "
                "Continue monitoring and consider raising the HIGH_CONFIDENCE_THRESHOLD."
            )

        # ── Build summary ──────────────────────────────────────
        confidence_impact = round(min(confidence_impact, 0.20), 4)  # cap at 20 % boost
        reflection_summary = self._build_summary(
            failure_patterns, suggestions, confidence_impact, vendor
        )

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "invoice_id": pipeline_result.get("invoice_id"),
            "vendor": vendor,
            "failure_patterns": failure_patterns,
            "suggestions": suggestions,
            "confidence_impact": confidence_impact,
        }
        self._reflection_history.append(record)

        stored_strategies = self._get_all_strategies()
        logger.info(
            "[ReflectionAgent] Reflection complete | patterns=%s impact=+%.2f",
            failure_patterns, confidence_impact,
        )

        return {
            "improvement_suggestions": suggestions,
            "confidence_impact": confidence_impact,
            "failure_patterns": failure_patterns,
            "stored_strategies": stored_strategies,
            "reflection_summary": reflection_summary,
        }

    # ──────────────────────────────────────────────────────────
    # Strategy memory
    # ──────────────────────────────────────────────────────────

    def _store_strategy(self, pattern: str, strategy: str) -> None:
        """Persist a strategy string for a given failure pattern."""
        if strategy not in self._strategy_memory[pattern]:
            self._strategy_memory[pattern].append(strategy)
            logger.debug("[ReflectionAgent] Strategy stored | pattern=%s", pattern)

    def _get_all_strategies(self) -> List[str]:
        """Return a flat list of all stored strategies across all patterns."""
        flat: List[str] = []
        for strategies in self._strategy_memory.values():
            flat.extend(strategies)
        return flat

    def get_strategies_for_pattern(self, pattern: str) -> List[str]:
        """Public accessor — retrieve stored strategies for a specific failure pattern."""
        return list(self._strategy_memory.get(pattern, []))

    def get_reflection_history(self) -> List[dict]:
        """Return the full reflection history for this agent instance."""
        return list(self._reflection_history)

    # ──────────────────────────────────────────────────────────
    # Summary builder
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_summary(
        patterns: List[str],
        suggestions: List[str],
        impact: float,
        vendor: str,
    ) -> str:
        if not patterns:
            return (
                f"Reflection for {vendor}: Pipeline executed without issues. "
                f"No corrective action required."
            )
        pattern_str = ", ".join(patterns)
        return (
            f"Reflection for {vendor}: Detected {len(patterns)} failure pattern(s) "
            f"[{pattern_str}]. Generated {len(suggestions)} improvement suggestion(s). "
            f"Estimated confidence boost from self-healing: +{impact*100:.1f}%."
        )
