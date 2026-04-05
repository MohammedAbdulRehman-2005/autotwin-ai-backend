"""
services/agents/browser_agent.py
──────────────────────────────────
Self-healing browser agent simulation.
Mimics a real RPA / browser-automation layer with DOM-failure retry logic,
exponential backoff, and per-task result handlers.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger("autotwin_ai.browser_agent")

TaskName = Literal["submit_to_erp", "update_sheet", "send_notification"]

MAX_RETRIES          = 3
DOM_FAILURE_CHANCE   = 0.40    # 40 % probability of DOM failure on attempt 1
BASE_BACKOFF_SECONDS = 0.3     # exponential: 0.3 → 0.6 → 1.2 s (simulated)


class BrowserAgent:
    """
    Simulates an RPA browser agent that can fail due to DOM changes and
    self-heal by switching strategy on retry.
    """

    def __init__(self) -> None:
        self._strategy_memory: Dict[str, str] = {}  # task → last-good-strategy

    async def run_task(self, task_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Public entry point.
        Runs *task_name* with up to MAX_RETRIES attempts.

        Returns::
            {
                "success":     bool,
                "result":      dict,
                "retry_count": int,
                "logs":        List[str]
            }
        """
        logs: List[str] = []
        retry_count = 0

        self._log(logs, f"[BrowserAgent] Starting task: '{task_name}'")
        logger.info("[BrowserAgent] run_task | task=%s data_keys=%s", task_name, list(data.keys()))

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = await self._attempt_task(task_name, data, attempt, logs)
                self._log(logs, f"[BrowserAgent] Task '{task_name}' succeeded on attempt {attempt}.")
                logger.info("[BrowserAgent] Success | task=%s attempt=%d retries=%d",
                            task_name, attempt, retry_count)
                return {
                    "success": True,
                    "result": result,
                    "retry_count": retry_count,
                    "logs": logs,
                }
            except _DOMFailureError as exc:
                retry_count += 1
                backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                self._log(logs, f"[BrowserAgent] DOM changed on attempt {attempt}: {exc}. "
                                f"Retrying with new strategy in {backoff:.1f}s...")
                logger.warning("[BrowserAgent] DOM failure | attempt=%d backoff=%.1fs", attempt, backoff)
                await asyncio.sleep(backoff)
                # Update strategy memory so next attempt uses a fallback approach
                self._strategy_memory[task_name] = f"fallback_strategy_v{attempt}"
            except Exception as exc:  # noqa: BLE001
                self._log(logs, f"[BrowserAgent] Non-DOM error on attempt {attempt}: {exc}")
                logger.error("[BrowserAgent] Unexpected error | task=%s error=%s", task_name, exc)
                break

        self._log(logs, f"[BrowserAgent] Task '{task_name}' FAILED after {MAX_RETRIES} retries.")
        logger.error("[BrowserAgent] All retries exhausted | task=%s", task_name)
        return {
            "success": False,
            "result": {"error": f"Task '{task_name}' failed after {MAX_RETRIES} retries"},
            "retry_count": retry_count,
            "logs": logs,
        }

    # ──────────────────────────────────────────────────────────
    # Internal task runner
    # ──────────────────────────────────────────────────────────

    async def _attempt_task(
        self,
        task_name: str,
        data: Dict[str, Any],
        attempt: int,
        logs: List[str],
    ) -> Dict[str, Any]:
        """
        Simulate DOM interaction. On the first attempt there is a 40% chance
        of a DOM failure. Subsequent attempts use the remembered fallback strategy
        and always succeed (simulating real-world resilience).
        """
        strategy = self._strategy_memory.get(task_name, "primary_strategy")
        self._log(logs, f"[BrowserAgent] Attempt {attempt} using strategy: {strategy}")

        # Inject delay to simulate browser round-trip
        await asyncio.sleep(0.2)

        # Simulate DOM failure only on the very first attempt
        if attempt == 1 and random.random() < DOM_FAILURE_CHANCE:
            raise _DOMFailureError("Target element not found — DOM structure may have changed")

        # Route to task handler
        handlers = {
            "submit_to_erp":      self._submit_to_erp,
            "update_sheet":       self._update_sheet,
            "send_notification":  self._send_notification,
        }
        handler = handlers.get(task_name)
        if handler is None:
            raise ValueError(f"Unknown task: '{task_name}'")

        return await handler(data, strategy, logs)

    # ──────────────────────────────────────────────────────────
    # Task implementations
    # ──────────────────────────────────────────────────────────

    async def _submit_to_erp(
        self, data: Dict[str, Any], strategy: str, logs: List[str]
    ) -> Dict[str, Any]:
        self._log(logs, f"[BrowserAgent] Navigating to ERP portal (strategy={strategy})")
        self._log(logs, f"[BrowserAgent] Filling invoice form | vendor={data.get('vendor')} "
                        f"amount={data.get('amount')}")
        self._log(logs, "[BrowserAgent] Submitting form → ERP accepted the invoice.")
        return {
            "task": "submit_to_erp",
            "erp_ref": f"ERP-{random.randint(100000, 999999)}",
            "vendor": data.get("vendor"),
            "amount": data.get("amount"),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _update_sheet(
        self, data: Dict[str, Any], strategy: str, logs: List[str]
    ) -> Dict[str, Any]:
        self._log(logs, f"[BrowserAgent] Opening spreadsheet (strategy={strategy})")
        self._log(logs, f"[BrowserAgent] Appending row | invoice_id={data.get('invoice_id')}")
        self._log(logs, "[BrowserAgent] Sheet updated successfully.")
        return {
            "task": "update_sheet",
            "row_written": True,
            "invoice_id": data.get("invoice_id"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _send_notification(
        self, data: Dict[str, Any], strategy: str, logs: List[str]
    ) -> Dict[str, Any]:
        recipient  = data.get("recipient", "finance@company.com")
        invoice_id = data.get("invoice_id", "N/A")
        self._log(logs, f"[BrowserAgent] Composing notification email → {recipient}")
        self._log(logs, f"[BrowserAgent] Subject: Invoice {invoice_id} requires review")
        self._log(logs, "[BrowserAgent] Notification sent successfully.")
        return {
            "task": "send_notification",
            "recipient": recipient,
            "invoice_id": invoice_id,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }

    # ──────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _log(logs: List[str], message: str) -> None:
        """Append a timestamped message to the log list."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        entry = f"[{ts}] {message}"
        logs.append(entry)
        logger.debug(entry)


class _DOMFailureError(RuntimeError):
    """Internal sentinel raised when the simulated DOM interaction fails."""
