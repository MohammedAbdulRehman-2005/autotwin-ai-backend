from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────
# 1. InvoiceSchema
# ─────────────────────────────────────────────
class InvoiceSchema(BaseModel):
    invoice_id: str = Field(default_factory=lambda: str(uuid4()))
    vendor: str
    amount: float
    date: str
    currency: str = "INR"
    raw_text: Optional[str] = None
    file_name: Optional[str] = None

    model_config = {"json_schema_extra": {"example": {
        "invoice_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "vendor": "Tata Consultancy Services",
        "amount": 125000.0,
        "date": "2024-01-15",
        "currency": "INR"
    }}}


# ─────────────────────────────────────────────
# 2. ExtractionResult
# ─────────────────────────────────────────────
class ExtractionResult(BaseModel):
    vendor: str
    amount: float
    date: str
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    currency: str

    @field_validator("extraction_confidence")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return round(max(0.0, min(1.0, v)), 4)


# ─────────────────────────────────────────────
# 3. AnomalyResult
# ─────────────────────────────────────────────
class AnomalyResult(BaseModel):
    is_anomaly: bool
    anomaly_type: Optional[str] = Field(
        default=None,
        description="One of: 'price_spike', 'duplicate', 'unusual_vendor'"
    )
    deviation_percentage: Optional[float] = None
    explanation: str

    @field_validator("anomaly_type")
    @classmethod
    def validate_anomaly_type(cls, v: Optional[str]) -> Optional[str]:
        allowed = {"price_spike", "duplicate", "unusual_vendor", None}
        if v not in allowed:
            raise ValueError(f"anomaly_type must be one of {allowed - {None}}")
        return v


# ─────────────────────────────────────────────
# 4. ConfidenceSchema
# ─────────────────────────────────────────────
class ConfidenceSchema(BaseModel):
    score: float = Field(ge=0.0, le=1.0, description="Overall weighted confidence score")
    extraction_weight: float = 0.4
    pattern_weight: float = 0.3
    historical_weight: float = 0.3
    extraction_score: float = Field(ge=0.0, le=1.0)
    pattern_score: float = Field(ge=0.0, le=1.0)
    historical_score: float = Field(ge=0.0, le=1.0)
    breakdown: Dict[str, Any]
    reasoning: str

    @field_validator("score", "extraction_score", "pattern_score", "historical_score")
    @classmethod
    def clamp_score(cls, v: float) -> float:
        return round(max(0.0, min(1.0, v)), 4)


# ─────────────────────────────────────────────
# 5. DecisionSchema
# ─────────────────────────────────────────────
class DecisionSchema(BaseModel):
    decision: str = Field(description="One of: 'auto_execute', 'warn', 'human_review'")
    action: str
    explanation: str
    requires_human: bool
    risk_level: str = Field(description="One of: 'low', 'medium', 'high'")
    status: str = Field(default="", description="Invoice status: 'approved', 'processed_with_warning', 'needs_review'")

    @field_validator("decision")
    @classmethod
    def validate_decision(cls, v: str) -> str:
        allowed = {"auto_execute", "warn", "human_review"}
        if v not in allowed:
            raise ValueError(f"decision must be one of {allowed}")
        return v

    @field_validator("risk_level")
    @classmethod
    def validate_risk_level(cls, v: str) -> str:
        allowed = {"low", "medium", "high"}
        if v not in allowed:
            raise ValueError(f"risk_level must be one of {allowed}")
        return v


# ─────────────────────────────────────────────
# 6. LogEntry
# ─────────────────────────────────────────────
class LogEntry(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    step: str
    message: str
    level: str = Field(description="One of: 'info', 'warning', 'error', 'success'")
    metadata: Optional[Dict[str, Any]] = None

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        allowed = {"info", "warning", "error", "success"}
        if v not in allowed:
            raise ValueError(f"level must be one of {allowed}")
        return v


# ─────────────────────────────────────────────
# 7. ProcessInvoiceResponse
# ─────────────────────────────────────────────
class ProcessInvoiceResponse(BaseModel):
    invoice_id: str
    vendor: str
    amount: float
    date: str
    anomaly: bool
    confidence: float = Field(ge=0.0, le=1.0)
    status: str
    decision: str
    explanation: str
    anomaly_details: Optional[AnomalyResult] = None
    confidence_breakdown: ConfidenceSchema
    logs: List[LogEntry] = Field(default_factory=list)
    retry_count: int = 0
    processing_time_ms: float
    risk_score: float = Field(ge=0.0, le=1.0)
    category: Optional[str] = None


# ─────────────────────────────────────────────
# 8. ApprovalRequest
# ─────────────────────────────────────────────
class ApprovalRequest(BaseModel):
    invoice_id: str
    updated_amount: Optional[float] = None
    approved: bool
    reviewer_notes: Optional[str] = None


# ─────────────────────────────────────────────
# 9. ApprovalResponse
# ─────────────────────────────────────────────
class ApprovalResponse(BaseModel):
    invoice_id: str
    approved: bool
    updated_confidence: float = Field(ge=0.0, le=1.0)
    new_decision: str
    memory_updated: bool
    message: str


# ─────────────────────────────────────────────
# 10. DashboardResponse
# ─────────────────────────────────────────────
class DashboardResponse(BaseModel):
    processed: int
    anomalies: int
    savings: float
    risk_score: float = Field(ge=0.0, le=1.0)
    auto_approved: int
    human_reviewed: int
    avg_confidence: float = Field(ge=0.0, le=1.0)
    top_vendors: List[Dict[str, Any]] = Field(default_factory=list)
