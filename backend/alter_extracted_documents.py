"""
alter_extracted_documents.py
─────────────────────────────
Migration: add rich extraction columns + financial document type column.

Run once:
    python alter_extracted_documents.py

Or paste the SQL directly into Supabase SQL Editor.
"""

import asyncio
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration")

_SQL = """
-- Rich invoice extraction fields
ALTER TABLE extracted_documents
  ADD COLUMN IF NOT EXISTS invoice_no        TEXT,
  ADD COLUMN IF NOT EXISTS due_date          TEXT,
  ADD COLUMN IF NOT EXISTS payment_terms     TEXT,
  ADD COLUMN IF NOT EXISTS subtotal          REAL,
  ADD COLUMN IF NOT EXISTS gst_rate          REAL,
  ADD COLUMN IF NOT EXISTS gst_amount        REAL,
  ADD COLUMN IF NOT EXISTS line_items        JSONB,
  ADD COLUMN IF NOT EXISTS seller_gstin      TEXT,
  ADD COLUMN IF NOT EXISTS buyer_gstin       TEXT,
  ADD COLUMN IF NOT EXISTS buyer_company     TEXT,
  ADD COLUMN IF NOT EXISTS notes             TEXT;

-- Financial document type for user_spreadsheets
ALTER TABLE user_spreadsheets
  ADD COLUMN IF NOT EXISTS type TEXT DEFAULT 'ledger';
"""


async def run():
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        logger.error("DATABASE_URL not set — cannot run migration.")
        return

    def _fix_url(url: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return url.replace(prefix, "postgresql+asyncpg://", 1)
        return url

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlalchemy import text

    engine = create_async_engine(_fix_url(database_url), poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text(_SQL))
    logger.info("Migration complete.")


if __name__ == "__main__":
    asyncio.run(run())
