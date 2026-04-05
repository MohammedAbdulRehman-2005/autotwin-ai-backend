"""
services/logger.py
───────────────────
Per-invoice structured pipeline logger for AutoTwin AI.

FIX LOG:
  - asyncio.ensure_future replaced with safe try/create_task that
    gracefully skips DB persist when there is no running event loop
    (e.g. during synchronous test collection or import-time init).
  - LogEntry timestamp now uses datetime.now(timezone.utc) (not deprecated utcnow).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from models.schemas import LogEntry

_py_logger = logging.getLogger("autotwin_ai.pipeline_logger")

_LEVEL_ICONS: Dict[str, str] = {
    "info":    "ℹ️",
    "warning": "⚠️",
    "error":   "❌",
    "success": "✅",
}


class PipelineLogger:
    """
    Structured, per-invoice logger.  Stores entries in memory and
    fire-and-forgets async DB writes without blocking the pipeline.

    Usage::

        pl = PipelineLogger(invoice_id="abc-123")
        pl.log("extraction", "Vision agent started", "info")
        entries = pl.get_logs()   # List[LogEntry]
        raw     = pl.to_dict()    # List[dict]  (for JSON response)
    """

    def __init__(self, invoice_id: str) -> None:
        self.invoice_id = invoice_id
        self._entries: List[LogEntry] = []
        _py_logger.debug("[PipelineLogger] Initialised for invoice_id=%s", invoice_id)

    # ──────────────────────────────────────────────────────────
    # Core log method (synchronous — no await)
    # ──────────────────────────────────────────────────────────

    def log(
        self,
        step: str,
        message: str,
        level: str = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LogEntry:
        """
        Record a pipeline step entry.

        Returns the created LogEntry (also stored internally).
        The async DB write is fire-and-forgotten safely.
        """
        level = level.lower()
        if level not in _LEVEL_ICONS:
            level = "info"

        entry = LogEntry(
            timestamp=datetime.now(timezone.utc),
            step=step,
            message=message,
            level=level,
            metadata=metadata or {},
        )
        self._entries.append(entry)

        py_level = (
            logging.WARNING if level == "warning"
            else logging.ERROR if level == "error"
            else logging.INFO
        )
        _py_logger.log(
            py_level,
            "[%s] [%s] %s %s",
            self.invoice_id[:8],
            step,
            _LEVEL_ICONS.get(level, ""),
            message,
        )

        # ── Safe fire-and-forget DB write ──────────────────────
        # asyncio.ensure_future() raises RuntimeError when there is no
        # current event loop (e.g. during synchronous test imports).
        # asyncio.create_task() is preferred in Python ≥3.10 and also
        # requires a running loop.  We guard both cases.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._persist(entry))
        except RuntimeError:
            # No running loop — skip DB write silently (test / startup context)
            pass

        return entry

    # ──────────────────────────────────────────────────────────
    # Accessors
    # ──────────────────────────────────────────────────────────

    def get_logs(self) -> List[LogEntry]:
        """Return all stored LogEntry objects."""
        return list(self._entries)

    def to_dict(self) -> List[Dict[str, Any]]:
        """
        Return entries as plain dicts with a frontend-ready 'display' field.
        """
        result: List[Dict[str, Any]] = []
        for entry in self._entries:
            icon = _LEVEL_ICONS.get(entry.level, "")
            result.append({
                "timestamp": entry.timestamp.isoformat(),
                "step":      entry.step,
                "message":   entry.message,
                "level":     entry.level,
                "metadata":  entry.metadata or {},
                "display":   f"{icon} [{entry.step.upper()}] {entry.message}",
            })
        return result

    def get_summary(self) -> Dict[str, Any]:
        """Return counts by log level plus last step name."""
        counts: Dict[str, int] = {"info": 0, "warning": 0, "error": 0, "success": 0}
        for e in self._entries:
            counts[e.level] = counts.get(e.level, 0) + 1
        return {
            "invoice_id":    self.invoice_id,
            "total_entries": len(self._entries),
            "counts":        counts,
            "last_step":     self._entries[-1].step if self._entries else None,
        }

    # ──────────────────────────────────────────────────────────
    # DB persistence
    # ──────────────────────────────────────────────────────────

    async def _persist(self, entry: LogEntry) -> None:
        """
        Async DB write — silently swallowed on any error so the pipeline
        is never blocked by a storage failure.
        """
        try:
            from models.database import save_log_entry  # deferred to avoid circular import
            await save_log_entry(
                self.invoice_id,
                {
                    "timestamp": entry.timestamp.isoformat(),
                    "step":      entry.step,
                    "message":   entry.message,
                    "level":     entry.level,
                    "metadata":  entry.metadata or {},
                },
            )
        except Exception as exc:  # noqa: BLE001
            _py_logger.debug("[PipelineLogger] DB persist skipped: %s", exc)
