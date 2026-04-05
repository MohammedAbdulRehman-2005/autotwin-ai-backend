"""
tests/test_api.py
──────────────────
Integration tests for AutoTwin AI API endpoints.
Uses the shared AsyncClient fixture from conftest.py (no live server needed).

Run with:
    pytest tests/test_api.py -v
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# The `client` fixture is provided by tests/conftest.py
# pytest.ini sets asyncio_mode = auto (no pytestmark needed)


# ══════════════════════════════════════════════════════════════
# Health endpoint
# ══════════════════════════════════════════════════════════════

class TestHealthEndpoint:

    async def test_health_endpoint_returns_200(self, client: AsyncClient) -> None:
        response = await client.get("/api/health")
        assert response.status_code == 200

    async def test_health_response_shape(self, client: AsyncClient) -> None:
        response = await client.get("/api/health")
        body = response.json()
        assert "status" in body
        assert body["status"] == "ok"
        assert "version" in body
        assert "timestamp" in body

    async def test_health_includes_demo_mode_flag(self, client: AsyncClient) -> None:
        response = await client.get("/api/health")
        body = response.json()
        assert "demo_mode" in body
        assert isinstance(body["demo_mode"], bool)


# ══════════════════════════════════════════════════════════════
# Auth endpoint
# ══════════════════════════════════════════════════════════════

class TestAuthEndpoint:

    async def test_login_demo_user_returns_token(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/auth/token",
            data={"username": "demo", "password": "demo123"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert isinstance(body["access_token"], str)
        assert len(body["access_token"]) > 20

    async def test_login_wrong_password_returns_401(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/auth/token",
            data={"username": "demo", "password": "wrongpassword"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 401

    async def test_login_unknown_user_returns_401(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/auth/token",
            data={"username": "ghost", "password": "nobody"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 401


# ══════════════════════════════════════════════════════════════
# Demo-run endpoint
# ══════════════════════════════════════════════════════════════

class TestDemoRun:

    async def test_demo_run_returns_200(self, client: AsyncClient) -> None:
        response = await client.post("/api/demo-run")
        assert response.status_code == 200

    async def test_demo_run_returns_complete_response(self, client: AsyncClient) -> None:
        response = await client.post("/api/demo-run")
        body = response.json()

        required_keys = {
            "invoice_id", "vendor", "amount", "date",
            "anomaly", "confidence", "status", "decision",
            "explanation", "confidence_breakdown", "logs",
            "retry_count", "processing_time_ms", "risk_score",
        }
        missing = required_keys - body.keys()
        assert not missing, f"Missing keys in demo-run response: {missing}"

    async def test_demo_run_vendor_is_technovendor(self, client: AsyncClient) -> None:
        response = await client.post("/api/demo-run")
        body = response.json()
        assert body["vendor"] == "TechnoVendor Inc."

    async def test_demo_run_amount_is_10000(self, client: AsyncClient) -> None:
        response = await client.post("/api/demo-run")
        body = response.json()
        assert body["amount"] == 10000.0

    async def test_demo_run_confidence_is_float_between_0_and_1(self, client: AsyncClient) -> None:
        response = await client.post("/api/demo-run")
        body = response.json()
        assert 0.0 <= body["confidence"] <= 1.0

    async def test_demo_run_decision_is_valid(self, client: AsyncClient) -> None:
        response = await client.post("/api/demo-run")
        body = response.json()
        assert body["decision"] in {"auto_execute", "warn", "human_review"}

    async def test_demo_run_has_confidence_breakdown(self, client: AsyncClient) -> None:
        response = await client.post("/api/demo-run")
        body = response.json()
        breakdown = body.get("confidence_breakdown", {})
        assert "score" in breakdown
        assert "breakdown" in breakdown
        assert "reasoning" in breakdown

    async def test_demo_run_logs_are_non_empty(self, client: AsyncClient) -> None:
        response = await client.post("/api/demo-run")
        body = response.json()
        logs = body.get("logs", [])
        assert isinstance(logs, list)
        assert len(logs) > 0

    async def test_demo_run_detects_anomaly_or_warns(self, client: AsyncClient) -> None:
        """
        TechnoVendor @ ₹10,000 is 2× the ₹5,000 historical avg.
        The pipeline should detect a price-spike anomaly OR issue a warning.
        """
        response = await client.post("/api/demo-run")
        body = response.json()
        # Either flagged as anomaly OR routed away from auto_execute
        assert body["anomaly"] is True or body["decision"] != "auto_execute"


# ══════════════════════════════════════════════════════════════
# Dashboard endpoint
# ══════════════════════════════════════════════════════════════

class TestDashboard:

    async def test_dashboard_returns_200(self, client: AsyncClient) -> None:
        response = await client.get("/api/dashboard")
        assert response.status_code == 200

    async def test_dashboard_returns_stats(self, client: AsyncClient) -> None:
        response = await client.get("/api/dashboard")
        body = response.json()

        required_keys = {
            "processed", "anomalies", "savings", "risk_score",
            "auto_approved", "human_reviewed", "avg_confidence", "top_vendors",
        }
        missing = required_keys - body.keys()
        assert not missing, f"Missing dashboard keys: {missing}"

    async def test_dashboard_processed_is_positive(self, client: AsyncClient) -> None:
        response = await client.get("/api/dashboard")
        body = response.json()
        assert body["processed"] > 0

    async def test_dashboard_risk_score_in_range(self, client: AsyncClient) -> None:
        response = await client.get("/api/dashboard")
        body = response.json()
        assert 0.0 <= body["risk_score"] <= 1.0

    async def test_dashboard_avg_confidence_in_range(self, client: AsyncClient) -> None:
        response = await client.get("/api/dashboard")
        body = response.json()
        assert 0.0 <= body["avg_confidence"] <= 1.0

    async def test_dashboard_top_vendors_is_list(self, client: AsyncClient) -> None:
        response = await client.get("/api/dashboard")
        body = response.json()
        assert isinstance(body["top_vendors"], list)


# ══════════════════════════════════════════════════════════════
# Process invoice endpoint (JSON body)
# ══════════════════════════════════════════════════════════════

class TestProcessInvoiceJson:

    _PAYLOAD = {
        "vendor":   "CloudServe Ltd.",
        "amount":   8500.0,
        "date":     "2024-03-01",
        "currency": "INR",
    }

    async def test_process_invoice_json_returns_200(self, client: AsyncClient) -> None:
        response = await client.post("/api/process-invoice", json=self._PAYLOAD)
        assert response.status_code == 200

    async def test_process_invoice_json_returns_correct_vendor(self, client: AsyncClient) -> None:
        response = await client.post("/api/process-invoice", json=self._PAYLOAD)
        body = response.json()
        assert body["vendor"] == "CloudServe Ltd."

    async def test_process_invoice_json_returns_correct_amount(self, client: AsyncClient) -> None:
        response = await client.post("/api/process-invoice", json=self._PAYLOAD)
        body = response.json()
        assert body["amount"] == 8500.0

    async def test_process_invoice_json_has_invoice_id(self, client: AsyncClient) -> None:
        response = await client.post("/api/process-invoice", json=self._PAYLOAD)
        body = response.json()
        assert "invoice_id" in body
        assert isinstance(body["invoice_id"], str)
        assert len(body["invoice_id"]) > 5

    async def test_process_invoice_json_confidence_in_range(self, client: AsyncClient) -> None:
        response = await client.post("/api/process-invoice", json=self._PAYLOAD)
        body = response.json()
        assert 0.0 <= body["confidence"] <= 1.0

    async def test_process_invoice_json_valid_decision(self, client: AsyncClient) -> None:
        response = await client.post("/api/process-invoice", json=self._PAYLOAD)
        body = response.json()
        assert body["decision"] in {"auto_execute", "warn", "human_review"}

    async def test_process_invoice_json_processing_time_positive(self, client: AsyncClient) -> None:
        response = await client.post("/api/process-invoice", json=self._PAYLOAD)
        body = response.json()
        assert body["processing_time_ms"] > 0

    async def test_process_invoice_no_body_returns_422(self, client: AsyncClient) -> None:
        """Submitting no identifiable payload must return 422 Unprocessable Entity."""
        response = await client.post(
            "/api/process-invoice",
            content=b"",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code in {400, 422}

    async def test_process_invoice_idempotent_id(self, client: AsyncClient) -> None:
        """Each request must produce a unique invoice_id."""
        r1 = await client.post("/api/process-invoice", json=self._PAYLOAD)
        r2 = await client.post("/api/process-invoice", json=self._PAYLOAD)
        assert r1.json()["invoice_id"] != r2.json()["invoice_id"]


# ══════════════════════════════════════════════════════════════
# Root redirect
# ══════════════════════════════════════════════════════════════

class TestRoot:

    async def test_root_redirects_to_docs(self, client: AsyncClient) -> None:
        response = await client.get("/", follow_redirects=False)
        assert response.status_code in {301, 302, 307, 308}
        assert "/docs" in response.headers.get("location", "")
