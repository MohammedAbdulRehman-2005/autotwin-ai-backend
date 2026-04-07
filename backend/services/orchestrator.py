"""
services/orchestrator.py
─────────────────────────
AutoTwin AI — Main Pipeline Brain.

Orchestrates the full invoice intelligence pipeline in 19 steps:
    VisionAgent → MemoryGraph → AnalyticsAgent → ConfidenceEngine
    → DecisionEngine → BrowserAgent (conditional) → ReflectionAgent
    → Database persistence → ProcessInvoiceResponse
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from models.schemas import (
    AnomalyResult,
    ConfidenceSchema,
    DecisionSchema,
    ExtractionResult,
    LogEntry,
    ProcessInvoiceResponse,
)
from services.agents.analytics_agent import AnalyticsAgent
from services.agents.browser_agent import BrowserAgent
from services.agents.reflection_agent import ReflectionAgent
from services.agents.vision_agent import VisionAgent
from services.confidence import ConfidenceEngine
from services.decision import DecisionEngine
from services.logger import PipelineLogger
from services.memory import MemoryGraph
from services.gemini_client import extract_with_gemini

logger = logging.getLogger("autotwin_ai.orchestrator")


class Orchestrator:
    """
    Singleton-style service that owns all sub-service instances.
    The MemoryGraph is intentionally stateful (persists across requests).
    """

    def __init__(self) -> None:
        self.vision_agent      = VisionAgent()
        self.analytics_agent   = AnalyticsAgent()
        self.browser_agent     = BrowserAgent()
        self.reflection_agent  = ReflectionAgent()
        self.confidence_engine = ConfidenceEngine()
        self.decision_engine   = DecisionEngine()
        self.memory_graph      = MemoryGraph()          # shared state ✓
        logger.info("[Orchestrator] All services initialised.")

    # ══════════════════════════════════════════════════════════
    # Public entry point
    # ══════════════════════════════════════════════════════════

    async def process_invoice(
        self,
        invoice_id: Optional[str] = None,
        file_content: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        json_data: Optional[Dict[str, Any]] = None,
        user_id: str = "demo_user",
    ) -> ProcessInvoiceResponse:
        """
        Execute the full 19-step AutoTwin AI pipeline.

        Args:
            invoice_id:   Optional caller-supplied ID; auto-generated if absent.
            file_content: Raw text / OCR output from an uploaded file.
            json_data:    Pre-parsed invoice dict (API / demo submit).

        Returns:
            ProcessInvoiceResponse — the complete result including logs.
        """
        invoice_id = invoice_id or str(uuid4())
        pipeline_logger = PipelineLogger(invoice_id, user_id=user_id)
        start_ms = time.monotonic() * 1000

        # ── Step 1 & 2 ─────────────────────────────────────────
        pipeline_logger.log(
            "init", "Invoice received, starting pipeline.", "info",
            {"invoice_id": invoice_id},
        )

        # ══════════════════════════════
        # Step 3 — Vision extraction
        # ══════════════════════════════
        if file_bytes:
            for attempt in range(2):
                try:
                    pipeline_logger.log("gemini", "Gemini extraction started", "info")
        
                    gemini_data = await extract_with_gemini(file_bytes)
        
                    def clean_amount(val):
                        if isinstance(val, str):
                            return float(val.replace(",", "").replace("$", "").strip() or 0)
                        return float(val or 0)
        
                    amount = clean_amount(gemini_data.get("amount"))
        
                    current_date = datetime.now().strftime("%Y-%m-%d")
        
                    date = self.vision_agent._normalise_date(
                        gemini_data.get("date") or ""
                    ) or current_date
        
                    confidence = 0.9 if gemini_data.get("vendor") and amount > 0 else 0.7
        
                    extraction = ExtractionResult(
                        vendor=gemini_data.get("vendor") or "Unknown Vendor",
                        amount=amount,
                        date=date,
                        extraction_confidence=confidence,
                        currency=gemini_data.get("currency") or "INR"
                    )
        
                    pipeline_logger.log(
                        "gemini",
                        "Gemini extraction success",
                        "success",
                        {"vendor": extraction.vendor, "amount": extraction.amount}
                    )
                    break
        
                except Exception as e:
                    if attempt == 0:
                        pipeline_logger.log("gemini", f"Retrying Gemini: {str(e)}", "warning")
                        continue
        
                    if "429" in str(e):
                        reason = "rate_limit"
                    elif "timeout" in str(e).lower() or "TimeoutError" in str(type(e)):
                        reason = "timeout"
                    else:
                        reason = "unknown"
                        
                    pipeline_logger.log(
                        "gemini",
                        f"Gemini failed ({reason}) → fallback triggered: {str(e)}",
                        "warning"
                    )
        
                    extraction = await self.vision_agent.extract(
                        file_content="fallback",
                        json_data=None
                    )
        else:
            pipeline_logger.log("extraction", "Running VisionAgent extraction…", "info")
            extraction = await self.vision_agent.extract(
                file_content=file_content,
                json_data=json_data,
            )

        # ── Step 4 ─────────────────────────────────────────────
        pipeline_logger.log(
            "extraction",
            f"Vision agent extracted: vendor={extraction.vendor!r}, "
            f"amount={extraction.amount:,.2f} {extraction.currency}, "
            f"date={extraction.date}, confidence={extraction.extraction_confidence:.2f}",
            "success",
            {
                "vendor": extraction.vendor,
                "amount": extraction.amount,
                "date": extraction.date,
                "extraction_confidence": extraction.extraction_confidence,
            },
        )

        # ══════════════════════════════
        # Step 5 — Vendor memory lookup
        # ══════════════════════════════
        vendor_profile = self.memory_graph.get_vendor_history(extraction.vendor)

        # ── Step 6 ─────────────────────────────────────────────
        txn_count = vendor_profile.get("transaction_count", 0)
        pipeline_logger.log(
            "memory",
            f"Memory graph loaded vendor history: {txn_count} transaction(s) found "
            f"for '{extraction.vendor}'.",
            "info",
            {"vendor_profile": vendor_profile},
        )

        # ── Build vendor_history list for AnalyticsAgent ───────
        vendor_history: List[dict] = [
            {"vendor": extraction.vendor, "amount": p, "date": ""}
            for p in vendor_profile.get("price_trend", [])
        ]

        # ══════════════════════════════
        # Step 7 — Anomaly analysis
        # ══════════════════════════════
        pipeline_logger.log("anomaly_check", "Running AnalyticsAgent anomaly detection…", "info")
        anomaly_result: AnomalyResult = await self.analytics_agent.analyze(
            extraction=extraction,
            vendor_history=vendor_history,
        )

        # ── Step 8 ─────────────────────────────────────────────
        if anomaly_result.is_anomaly:
            pipeline_logger.log(
                "anomaly_check",
                f"⚠️ Anomaly detected: {anomaly_result.anomaly_type} — "
                f"{anomaly_result.explanation}",
                "warning",
                {
                    "anomaly_type":         anomaly_result.anomaly_type,
                    "deviation_percentage": anomaly_result.deviation_percentage,
                },
            )
        else:
            pipeline_logger.log(
                "anomaly_check",
                f"No anomalies detected for {extraction.vendor!r}.",
                "success",
            )

        # ══════════════════════════════
        # Step 9 — Historical consistency
        # ══════════════════════════════
        historical_consistency = self.memory_graph.calculate_historical_consistency(
            vendor=extraction.vendor,
            amount=extraction.amount,
        )

        # ── pattern_score is stored as a dynamic attribute on the anomaly result
        pattern_score: float = anomaly_result.__dict__.get("pattern_score", 1.0)

        # ══════════════════════════════
        # Step 10 — Confidence calculation
        # ══════════════════════════════
        confidence_schema: ConfidenceSchema = self.confidence_engine.calculate(
            extraction_confidence=extraction.extraction_confidence,
            pattern_score=pattern_score,
            historical_consistency=historical_consistency,
        )

        # ── Step 11 ────────────────────────────────────────────
        pipeline_logger.log(
            "confidence",
            f"Confidence calculated: {confidence_schema.score:.0%} — "
            f"{confidence_schema.reasoning}",
            "info",
            {"breakdown": confidence_schema.breakdown},
        )

        # ══════════════════════════════
        # Step 12 — Decision
        # ══════════════════════════════
        decision_schema: DecisionSchema = self.decision_engine.decide(
            confidence_schema=confidence_schema,
            anomaly_result=anomaly_result,
        )

        # ── Step 13 ────────────────────────────────────────────
        pipeline_logger.log(
            "decision",
            f"Decision: {decision_schema.decision} — {decision_schema.explanation}",
            "success" if decision_schema.decision == "auto_execute" else "warning",
            {
                "decision":      decision_schema.decision,
                "risk_level":    decision_schema.risk_level,
                "requires_human": decision_schema.requires_human,
            },
        )

        # ══════════════════════════════
        # Step 14 — Browser automation (auto-execute only)
        # ══════════════════════════════
        retry_count = 0
        browser_logs: List[str] = []

        if decision_schema.decision == "auto_execute":
            pipeline_logger.log(
                "browser_agent",
                "Auto-execute triggered — submitting invoice to ERP via BrowserAgent.",
                "info",
            )
            browser_result = await self.browser_agent.run_task(
                "submit_to_erp",
                {
                    "invoice_id": invoice_id,
                    "vendor":     extraction.vendor,
                    "amount":     extraction.amount,
                    "date":       extraction.date,
                },
            )
            retry_count  = browser_result.get("retry_count", 0)
            browser_logs = browser_result.get("logs", [])

            if browser_result.get("success"):
                pipeline_logger.log(
                    "browser_agent",
                    f"ERP submission successful (retries={retry_count}).",
                    "success",
                    {"erp_result": browser_result.get("result")},
                )
            else:
                pipeline_logger.log(
                    "browser_agent",
                    f"ERP submission FAILED after {retry_count} retries.",
                    "error",
                    {"browser_logs": browser_logs},
                )

        # ══════════════════════════════
        # Step 15 — Reflection (on retries)
        # ══════════════════════════════
        reflection_data: Optional[dict] = None
        if retry_count > 0:
            pipeline_logger.log(
                "reflection",
                f"BrowserAgent had {retry_count} retry/retries — triggering ReflectionAgent.",
                "info",
            )
            reflection_data = await self.reflection_agent.reflect(
                pipeline_result={
                    "invoice_id":        invoice_id,
                    "vendor":            extraction.vendor,
                    "amount":            extraction.amount,
                    "confidence":        confidence_schema.score,
                    "retry_count":       retry_count,
                    "anomaly":           anomaly_result.is_anomaly,
                    "anomaly_details":   anomaly_result.model_dump() if anomaly_result.is_anomaly else {},
                    "decision":          decision_schema.decision,
                    "risk_score":        confidence_schema.breakdown.get("risk_score", 0.0),
                    "processing_time_ms": (time.monotonic() * 1000) - start_ms,
                }
            )
            pipeline_logger.log(
                "reflection",
                f"Reflection complete | confidence_impact=+{reflection_data.get('confidence_impact', 0):.2f} "
                f"| suggestions={len(reflection_data.get('improvement_suggestions', []))}",
                "info",
                {"reflection": reflection_data},
            )

        # ══════════════════════════════
        # Step 16 — Update memory
        # ══════════════════════════════
        self.memory_graph.update_vendor_data(
            vendor=extraction.vendor,
            amount=extraction.amount,
            date=extraction.date,
            anomaly=anomaly_result.is_anomaly,
        )
        pipeline_logger.log(
            "memory",
            f"Vendor memory updated for '{extraction.vendor}'.",
            "info",
        )

        # ══════════════════════════════
        # Step 17 — Persist to DB
        # ══════════════════════════════
        status = decision_schema.status or decision_schema.decision
        risk_score: float = round(confidence_schema.breakdown.get("risk_score", 1 - confidence_schema.score), 4)

        invoice_doc = {
            "invoice_id":  invoice_id,
            "vendor":      extraction.vendor,
            "amount":      extraction.amount,
            "date":        extraction.date,
            "currency":    extraction.currency,
            "anomaly":     anomaly_result.is_anomaly,
            "confidence":  confidence_schema.score,
            "risk_score":  risk_score,
            "decision":    decision_schema.decision,
            "status":      status,
        }
        try:
            from models.database import save_invoice, update_vendor_history
            await save_invoice(invoice_doc, user_id=user_id)
            await update_vendor_history(extraction.vendor, invoice_doc, user_id=user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Orchestrator] DB save skipped: %s", exc)

        # ══════════════════════════════
        # Step 18 — Final log
        # ══════════════════════════════
        elapsed_ms = round((time.monotonic() * 1000) - start_ms, 2)
        pipeline_logger.log(
            "complete",
            f"🏁 Pipeline complete in {elapsed_ms}ms | "
            f"decision={decision_schema.decision} | "
            f"confidence={confidence_schema.score:.0%} | "
            f"risk={risk_score:.2f}",
            "success",
            {"processing_time_ms": elapsed_ms},
        )

        # ══════════════════════════════
        # Step 19 — Build response
        # ══════════════════════════════
        log_entries: List[LogEntry] = pipeline_logger.get_logs()

        return ProcessInvoiceResponse(
            invoice_id=invoice_id,
            vendor=extraction.vendor,
            amount=extraction.amount,
            date=extraction.date,
            anomaly=anomaly_result.is_anomaly,
            confidence=confidence_schema.score,
            status=status,
            decision=decision_schema.decision,
            explanation=decision_schema.explanation,
            anomaly_details=anomaly_result if anomaly_result.is_anomaly else None,
            confidence_breakdown=confidence_schema,
            logs=log_entries,
            retry_count=retry_count,
            processing_time_ms=elapsed_ms,
            risk_score=risk_score,
        )
