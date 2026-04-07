import asyncio
import logging
from core.config import settings

logging.basicConfig(level=logging.INFO)

async def alter_schema():
    from models.database import _engine, _pg_available
    if _pg_available and _engine is not None:
        try:
            async with _engine.begin() as conn:
                from sqlalchemy import text
                await conn.execute(text("ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS invoice_id uuid;"))
                print("✅ Added invoice_id to agent_logs.")
        except Exception as e:
            print(f"❌ Failed to alter schema: {e}")

if __name__ == "__main__":
    asyncio.run(alter_schema())
