"""
services/orchestrator.py
─────────────────────────
AutoTwin AI — Autonomous Graph Orchestrator (LangGraph - State-Driven & Self-Healing)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict
from uuid import uuid4

from langgraph.graph import StateGraph, END

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

MAX_GRAPH_RETRIES = 3

class AgentState(TypedDict, total=False):
    invoice_id: str
    raw_input: dict
    extraction_result: dict
    vendor_history: dict
    similar_cases: list
    anomaly_result: dict
    confidence_score: float
    decision: str
    execution_result: dict
    browser_logs: list
    failure_reason: str
    retry_strategy: str
    retry_count: int
    current_node: str
    next_node: str
    error_flag: bool
    terminate: bool
    memory_update: dict
    trace: list
    
    # Internal context bindings
    logger: Any
    confidence_breakdown: dict
    decision_explanation: str

class Orchestrator:
    def __init__(self) -> None:
        self.vision_agent      = VisionAgent()
        self.analytics_agent   = AnalyticsAgent()
        self.browser_agent     = BrowserAgent()
        self.reflection_agent  = ReflectionAgent()
        self.confidence_engine = ConfidenceEngine()
        self.decision_engine   = DecisionEngine()
        self.memory_graph      = MemoryGraph()

        self.graph = self._build_graph()
        logger.info("[Orchestrator] True Autonomous StateGraph compiled successfully.")

    def _build_graph(self):
        workflow = StateGraph(AgentState)

        workflow.add_node("router_node", self.router_node)
        workflow.add_node("extraction_node", self.extraction_node)
        workflow.add_node("memory_node", self.memory_node)
        workflow.add_node("analysis_node", self.analysis_node)
        workflow.add_node("scoring_node", self.scoring_node)
        workflow.add_node("execution_node", self.execution_node)
        workflow.add_node("reflection_node", self.reflection_node)
        workflow.add_node("persistence_node", self.persistence_node)
        workflow.add_node("finalizer_node", self.finalizer_node)

        workflow.set_entry_point("router_node")

        def route(state: AgentState):
            return state["next_node"]

        workflow.add_conditional_edges(
            "router_node",
            route,
            {
                "extraction_node": "extraction_node",
                "memory_node": "memory_node",
                "analysis_node": "analysis_node",
                "scoring_node": "scoring_node",
                "execution_node": "execution_node",
                "reflection_node": "reflection_node",
                "persistence_node": "persistence_node",
                "finalizer_node": "finalizer_node",
                "__end__": END,
            }
        )

        # Force state-driven execution (all non-final nodes yield back to router)
        workflow.add_edge("extraction_node", "router_node")
        workflow.add_edge("memory_node", "router_node")
        workflow.add_edge("analysis_node", "router_node")
        workflow.add_edge("scoring_node", "router_node")
        workflow.add_edge("execution_node", "router_node")
        workflow.add_edge("reflection_node", "router_node")
        workflow.add_edge("persistence_node", "finalizer_node")
        workflow.add_edge("finalizer_node", "router_node") # Router verifies termination

        return workflow.compile()

    # ══════════════════════════════════════════════════════════
    # Fully State-Driven Router
    # ══════════════════════════════════════════════════════════

    async def router_node(self, state: AgentState) -> dict:
        trace = list(state.get("trace", []))
        trace.append({"node": "router_node", "status": "success", "details": "Routing evaluation"})
        
        terminate = state.get("terminate", False)
        error_flag = state.get("error_flag", False)
        retry_count = state.get("retry_count", 0)
        confidence = state.get("confidence_score")
        score = (confidence * 100) if confidence and confidence <= 1.0 else (confidence or 0.0)
        
        if terminate:
            next_node = "__end__"
        elif error_flag:
            next_node = "reflection_node"
        elif retry_count >= MAX_GRAPH_RETRIES:
            next_node = "persistence_node"
        elif not state.get("extraction_result"):
            next_node = "extraction_node"
        elif not state.get("vendor_history") and "vendor_history" not in state:
            next_node = "memory_node"
        elif not state.get("anomaly_result"):
            next_node = "analysis_node"
        elif "confidence_score" not in state:
            next_node = "scoring_node"
        elif score < 70:
            next_node = "persistence_node"  # HITL required
        elif score >= 90 and not state.get("execution_result"):
            next_node = "execution_node"
        elif state.get("execution_result"):
            next_node = "persistence_node"
        else:
            next_node = "persistence_node"

        return {"next_node": next_node, "trace": trace}

    # ══════════════════════════════════════════════════════════
    # Worker Nodes
    # ══════════════════════════════════════════════════════════

    async def extraction_node(self, state: AgentState) -> dict:
        trace = list(state.get("trace", []))
        raw_input = state.get("raw_input", {})
        pipeline_logger = state.get("logger")
        
        try:
            # Fallback legacy logic adapted for structural compliance
            ext_obj = await self.vision_agent.extract(
                file_content=raw_input.get("file_content"),
                json_data=raw_input.get("json_data")
            )
            extraction_dict = ext_obj.model_dump()
            
            trace.append({
                "node": "extraction_node", 
                "status": "success", 
                "details": f"Extracted vendor: {extraction_dict.get('vendor')}"
            })
            pipeline_logger.log("extraction", f"Extracted: {extraction_dict.get('vendor')}", "success")
            
            return {"extraction_result": extraction_dict, "trace": trace}
        except Exception as e:
            trace.append({"node": "extraction_node", "status": "failed", "details": str(e)})
            return {"extraction_result": {}, "error_flag": True, "failure_reason": "PARSING_ERROR", "trace": trace}

    async def memory_node(self, state: AgentState) -> dict:
        trace = list(state.get("trace", []))
        vendor = state.get("extraction_result", {}).get("vendor", "Unknown")
        
        vendor_profile = self.memory_graph.get_vendor_history(vendor)
        trace.append({"node": "memory_node", "status": "success", "details": f"Loaded profile for {vendor}"})
        
        return {"vendor_history": vendor_profile, "similar_cases": [], "trace": trace}

    async def analysis_node(self, state: AgentState) -> dict:
        trace = list(state.get("trace", []))
        ext = state.get("extraction_result", {})
        vh = state.get("vendor_history", {})
        
        ext_model = ExtractionResult(**ext)
        vendor_history_list = [{"vendor": ext.get("vendor"), "amount": p, "date": ""} for p in vh.get("price_trend", [])]

        anomaly_result = await self.analytics_agent.analyze(extraction=ext_model, vendor_history=vendor_history_list)
        
        trace.append({
            "node": "analysis_node", 
            "status": "success", 
            "details": "Anomaly check complete", 
            "anomaly": anomaly_result.is_anomaly
        })
        
        return {"anomaly_result": anomaly_result.model_dump(), "trace": trace}

    async def scoring_node(self, state: AgentState) -> dict:
        trace = list(state.get("trace", []))
        ext = state.get("extraction_result", {})
        anomaly_dict = state.get("anomaly_result", {})
        
        anomaly_model = AnomalyResult(**anomaly_dict)
        historical_consistency = self.memory_graph.calculate_historical_consistency(
            vendor=ext.get("vendor", ""), amount=ext.get("amount", 0.0)
        )

        conf = self.confidence_engine.calculate(
            extraction_confidence=ext.get("extraction_confidence", 0.0),
            pattern_score=anomaly_dict.get("pattern_score", 1.0),
            historical_consistency=historical_consistency,
        )
        
        score_percentage = conf.score * 100 if conf.score <= 1.0 else conf.score
        
        if score_percentage >= 90:
            decision = "auto_execute"
        elif 70 <= score_percentage < 90:
            decision = "review"
        else:
            decision = "human_required"

        trace.append({"node": "scoring_node", "status": "success", "details": f"Score: {conf.score}, Decision: {decision}"})
        
        return {
            "confidence_score": conf.score,
            "decision": decision,
            "confidence_breakdown": conf.model_dump(),
            "decision_explanation": f"System confidence mapped to decision logic ({decision})",
            "trace": trace
        }

    async def execution_node(self, state: AgentState) -> dict:
        trace = list(state.get("trace", []))
        ext = state.get("extraction_result", {})
        strategy = state.get("retry_strategy", "default")
        
        trace.append({"node": "execution_node", "status": "running", "details": f"Using strategy: {strategy}"})
        
        browser_result = await self.browser_agent.run_task(
            f"submit_to_erp_{strategy}",
            {
                "invoice_id": state.get("invoice_id"),
                "vendor":     ext.get("vendor"),
                "amount":     ext.get("amount"),
                "date":       ext.get("date"),
                "strategy":   strategy
            },
        )

        success = browser_result.get("success", False)
        
        if success:
            trace.append({"node": "execution_node", "status": "success", "details": "ERP task completed"})
            return {
                "execution_result": browser_result,
                "browser_logs": browser_result.get("logs", []),
                "error_flag": False,
                "failure_reason": "",
                "trace": trace
            }
        else:
            reason = "BROWSER_TASK_FAILED"
            logs_str = str(browser_result.get("logs", []))
            if "DOM" in logs_str or "timeout" in logs_str.lower():
                reason = "DOM_CHANGE"
            elif "40" in logs_str or "50" in logs_str:
                reason = "API_ERROR"
                
            trace.append({"node": "execution_node", "status": "failed", "details": reason})
            return {
                "execution_result": {},
                "browser_logs": browser_result.get("logs", []),
                "error_flag": True,
                "failure_reason": reason,
                "trace": trace
            }

    async def reflection_node(self, state: AgentState) -> dict:
        trace = list(state.get("trace", []))
        retry_count = state.get("retry_count", 0) + 1
        reason = state.get("failure_reason", "UNKNOWN")
        
        if reason == "DOM_CHANGE":
            retry_strategy = "fallback_selectors_or_api"
        elif reason == "API_ERROR":
            retry_strategy = "exponential_backoff"
        elif reason == "PARSING_ERROR":
            retry_strategy = "use_vision_model"
        else:
            retry_strategy = "default_safe_mode"

        trace.append({"node": "reflection_node", "status": "success", "details": f"Generated strategy: {retry_strategy} for {reason}"})

        return {
            "retry_count": retry_count,
            "retry_strategy": retry_strategy,
            "error_flag": False, # Clear flag to allow normal retry flow
            "trace": trace
        }

    async def persistence_node(self, state: AgentState) -> dict:
        trace = list(state.get("trace", []))
        ext = state.get("extraction_result", {})
        vendor = ext.get("vendor", "")

        # Store structured intelligence properly
        intelligence_payload = {
            "vendor": vendor,
            "amount": ext.get("amount", 0.0),
            "date": ext.get("date", ""),
            "decision": state.get("decision", ""),
            "confidence": state.get("confidence_score", 0.0),
            "outcome": state.get("execution_result", {}),
            "anomaly": state.get("anomaly_result", {})
        }
        
        # In AutoTwin AI, we update MemoryGraph here.
        self.memory_graph.update_vendor_data(
            vendor=intelligence_payload["vendor"],
            amount=intelligence_payload["amount"],
            date=intelligence_payload["date"],
            anomaly=intelligence_payload["anomaly"].get("is_anomaly", False) if isinstance(intelligence_payload["anomaly"], dict) else False
        )
        
        try:
            from models.database import save_invoice, update_vendor_history
            user_id = state.get("raw_input", {}).get("user_id", "demo_user")
            
            doc = {
                "invoice_id": state.get("invoice_id", ""),
                "vendor": vendor,
                "amount": intelligence_payload["amount"],
                "date": intelligence_payload["date"],
                "currency": ext.get("currency", "INR"),
                "anomaly": intelligence_payload["anomaly"].get("is_anomaly", False) if isinstance(intelligence_payload["anomaly"], dict) else False,
                "confidence": state.get("confidence_score", 0.0),
                "risk_score": 1.0 - state.get("confidence_score", 0.0),
                "decision": state.get("decision", ""),
                "status": state.get("decision", ""),
            }
            await save_invoice(doc, user_id=user_id)
            await update_vendor_history(vendor, doc, user_id=user_id)
        except Exception as exc:
            logger.warning("[Orchestrator] DB save skipped: %s", exc)

        trace.append({"node": "persistence_node", "status": "success", "details": "Saved to memory and DB"})
        return {"trace": trace}

    async def finalizer_node(self, state: AgentState) -> dict:
        trace = list(state.get("trace", []))
        trace.append({"node": "finalizer_node", "status": "success", "details": "Closing output pipeline"})
        
        state.get("logger").log("complete", "Pipeline concluded organically.", "success")

        return {"terminate": True, "trace": trace}

    # ══════════════════════════════════════════════════════════
    # Public entry point (Backward-Compatible API)
    # ══════════════════════════════════════════════════════════

    async def process_invoice(
        self,
        invoice_id: Optional[str] = None,
        file_content: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        json_data: Optional[Dict[str, Any]] = None,
        user_id: str = "demo_user",
    ) -> ProcessInvoiceResponse:
        
        invoice_id = invoice_id or str(uuid4())
        pipeline_logger = PipelineLogger(invoice_id, user_id=user_id)
        start_ms = time.monotonic() * 1000

        initial_state: AgentState = {
            "invoice_id": invoice_id,
            "raw_input": {
                "file_content": file_content,
                "file_bytes": file_bytes,
                "json_data": json_data,
                "user_id": user_id
            },
            "extraction_result": {},
            "vendor_history": {},
            "similar_cases": [],
            "anomaly_result": {},
            # confidence_score purposefully missing to trigger extraction route cleanly
            "execution_result": {},
            "browser_logs": [],
            "failure_reason": "",
            "retry_strategy": "default",
            "retry_count": 0,
            "current_node": "init",
            "next_node": "",
            "error_flag": False,
            "terminate": False,
            "memory_update": {},
            "trace": [],
            
            "logger": pipeline_logger,
            "confidence_breakdown": {},
            "decision_explanation": ""
        }

        final_state = await self.graph.ainvoke(initial_state)

        elapsed_ms = round((time.monotonic() * 1000) - start_ms, 2)
        ext = final_state.get("extraction_result", {})
        anomaly_dict = final_state.get("anomaly_result", {})
        
        return ProcessInvoiceResponse(
            invoice_id=final_state.get("invoice_id", invoice_id),
            vendor=ext.get("vendor", "Unknown Vendor"),
            amount=ext.get("amount", 0.0),
            date=ext.get("date", ""),
            anomaly=anomaly_dict.get("is_anomaly", False),
            confidence=final_state.get("confidence_score", 0.0),
            status=final_state.get("decision", "pending"),
            decision=final_state.get("decision", "human_required"),
            explanation=final_state.get("decision_explanation", ""),
            anomaly_details=AnomalyResult(**anomaly_dict) if anomaly_dict.get("is_anomaly") else None,
            confidence_breakdown=ConfidenceSchema(**final_state.get("confidence_breakdown", {"score": 0.0, "reasoning": "Fallback", "breakdown": {}})),
            logs=pipeline_logger.get_logs(),
            retry_count=final_state.get("retry_count", 0),
            processing_time_ms=elapsed_ms,
            risk_score=1.0 - final_state.get("confidence_score", 0.0),
        )
