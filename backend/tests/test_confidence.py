"""
tests/test_confidence.py
─────────────────────────
Unit tests for ConfidenceEngine and DecisionEngine.

Run with:
    pytest tests/test_confidence.py -v
"""

from __future__ import annotations

import sys
import os

# Ensure the backend root is on the path when running from the tests/ dir
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from models.schemas import AnomalyResult, ConfidenceSchema
from services.confidence import ConfidenceEngine, EXTRACTION_WEIGHT, PATTERN_WEIGHT, HISTORICAL_WEIGHT
from services.decision import DecisionEngine, HIGH_CONFIDENCE, MEDIUM_CONFIDENCE


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def engine() -> ConfidenceEngine:
    return ConfidenceEngine()


@pytest.fixture
def decision_engine() -> DecisionEngine:
    return DecisionEngine()


def _no_anomaly() -> AnomalyResult:
    return AnomalyResult(is_anomaly=False, explanation="No anomaly.")


def _anomaly(anomaly_type: str = "price_spike", dev: float = 80.0) -> AnomalyResult:
    result = AnomalyResult(
        is_anomaly=True,
        anomaly_type=anomaly_type,
        deviation_percentage=dev,
        explanation="Test anomaly.",
    )
    return result


# ══════════════════════════════════════════════════════════════
# ConfidenceEngine tests
# ══════════════════════════════════════════════════════════════

class TestConfidenceEngine:

    def test_high_confidence(self, engine: ConfidenceEngine) -> None:
        """All perfect inputs → score must exceed HIGH_CONFIDENCE threshold."""
        result = engine.calculate(
            extraction_confidence=0.95,
            pattern_score=1.0,
            historical_consistency=1.0,
        )
        assert isinstance(result, ConfidenceSchema)
        assert result.score > 0.95, (
            f"Expected score > 0.95, got {result.score}"
        )

    def test_low_confidence(self, engine: ConfidenceEngine) -> None:
        """All poor inputs → score must fall well below 0.50."""
        result = engine.calculate(
            extraction_confidence=0.55,
            pattern_score=0.2,
            historical_consistency=0.3,
        )
        assert result.score < 0.50, (
            f"Expected score < 0.50, got {result.score}"
        )

    def test_confidence_formula(self, engine: ConfidenceEngine) -> None:
        """Verify score matches the exact weighted formula."""
        ext, pat, hist = 0.80, 0.70, 0.60
        result = engine.calculate(
            extraction_confidence=ext,
            pattern_score=pat,
            historical_consistency=hist,
        )
        expected = round(
            (EXTRACTION_WEIGHT  * ext)
            + (PATTERN_WEIGHT   * pat)
            + (HISTORICAL_WEIGHT * hist),
            4,
        )
        assert result.score == expected, (
            f"Formula mismatch: expected {expected}, got {result.score}"
        )

    def test_breakdown_keys_present(self, engine: ConfidenceEngine) -> None:
        """Breakdown dict must contain all required component keys."""
        result = engine.calculate(0.90, 0.85, 0.80)
        required_keys = {"extraction", "pattern", "historical", "total", "risk_score"}
        assert required_keys.issubset(result.breakdown.keys()), (
            f"Missing breakdown keys: {required_keys - result.breakdown.keys()}"
        )

    def test_risk_score_is_complement(self, engine: ConfidenceEngine) -> None:
        """risk_score in breakdown should equal 1 - confidence score."""
        result = engine.calculate(0.80, 0.75, 0.70)
        expected_risk = round(1.0 - result.score, 4)
        assert result.breakdown["risk_score"] == expected_risk

    def test_inputs_clamped_above_one(self, engine: ConfidenceEngine) -> None:
        """Inputs exceeding 1.0 must be clamped — score should remain ≤ 1.0."""
        result = engine.calculate(1.5, 1.5, 1.5)
        assert result.score <= 1.0

    def test_inputs_clamped_below_zero(self, engine: ConfidenceEngine) -> None:
        """Negative inputs must be clamped — score should remain ≥ 0.0."""
        result = engine.calculate(-0.5, -1.0, -0.2)
        assert result.score >= 0.0

    def test_reasoning_is_non_empty_string(self, engine: ConfidenceEngine) -> None:
        """Reasoning must be a non-empty string."""
        result = engine.calculate(0.92, 1.0, 1.0)
        assert isinstance(result.reasoning, str)
        assert len(result.reasoning) > 10

    def test_weights_sum_to_one(self) -> None:
        """Module-level weights must sum to exactly 1.0."""
        total = round(EXTRACTION_WEIGHT + PATTERN_WEIGHT + HISTORICAL_WEIGHT, 10)
        assert total == 1.0, f"Weights sum to {total}, expected 1.0"


# ══════════════════════════════════════════════════════════════
# DecisionEngine tests
# ══════════════════════════════════════════════════════════════

class TestDecisionEngine:

    def _make_confidence(self, score: float) -> ConfidenceSchema:
        engine = ConfidenceEngine()
        # Back-calculate inputs that yield approximately *score*
        # Using equal-weight shortcut: each component = score (since weights sum to 1)
        return engine.calculate(score, score, score)

    def test_decision_auto_execute(self, decision_engine: DecisionEngine) -> None:
        """confidence > HIGH_CONFIDENCE + no anomaly → auto_execute."""
        conf = self._make_confidence(0.97)
        result = decision_engine.decide(conf, _no_anomaly())
        assert result.decision == "auto_execute"
        assert result.requires_human is False
        assert result.risk_level == "low"

    def test_decision_warn(self, decision_engine: DecisionEngine) -> None:
        """MEDIUM < confidence ≤ HIGH and no anomaly → warn."""
        conf = self._make_confidence(0.80)
        result = decision_engine.decide(conf, _no_anomaly())
        assert result.decision == "warn"
        assert result.requires_human is False
        assert result.risk_level == "medium"

    def test_decision_human_review(self, decision_engine: DecisionEngine) -> None:
        """confidence < MEDIUM_CONFIDENCE → human_review."""
        conf = self._make_confidence(0.50)
        result = decision_engine.decide(conf, _no_anomaly())
        assert result.decision == "human_review"
        assert result.requires_human is True
        assert result.risk_level == "high"

    def test_anomaly_escalates_risk_to_high(self, decision_engine: DecisionEngine) -> None:
        """An anomaly on a warn-level confidence → risk bumped to 'high'."""
        conf = self._make_confidence(0.80)   # would normally be medium
        result = decision_engine.decide(conf, _anomaly("price_spike"))
        assert result.risk_level == "high"

    def test_auto_execute_blocked_by_anomaly(self, decision_engine: DecisionEngine) -> None:
        """
        Even a perfect confidence score with an anomaly must NOT produce
        auto_execute — it should downgrade to warn.
        """
        conf = self._make_confidence(0.97)
        result = decision_engine.decide(conf, _anomaly("duplicate"))
        # With anomaly present, decision cannot be auto_execute
        assert result.decision != "auto_execute"

    def test_explanation_is_non_empty(self, decision_engine: DecisionEngine) -> None:
        """Explanation field must always be a non-empty string."""
        for score in (0.30, 0.75, 0.97):
            conf = self._make_confidence(score)
            result = decision_engine.decide(conf, _no_anomaly())
            assert isinstance(result.explanation, str) and len(result.explanation) > 20

    def test_action_is_non_empty(self, decision_engine: DecisionEngine) -> None:
        """Action field must never be empty."""
        conf = self._make_confidence(0.72)
        result = decision_engine.decide(conf, _no_anomaly())
        assert isinstance(result.action, str) and len(result.action) > 5
