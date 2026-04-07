import asyncio
import logging
from core.config import settings

logging.basicConfig(level=logging.INFO)

async def apply_schema():
    from models.database import _engine, _pg_available
    if _pg_available and _engine is not None:
        try:
            async with _engine.begin() as conn:
                from sqlalchemy import text
                
                with open("models/migrations/init_schema.sql", "r", encoding="utf-8") as f:
                    sql_text = f.read()
                
                # SQLAlchemy text() cannot execute multiple statements properly at once in asyncpg
                # if there are DDLs mixed, but for simple schemas asyncpg can sometimes handle it.
                # However we can split by ";" and execute if needed.
                # We'll try passing the whole file first (asyncpg allows this for simple scripts).
                await conn.execute(text(sql_text))
                print("✅ Schema applied successfully.")
        except Exception as e:
            print(f"❌ Failed to apply schema: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(apply_schema())
