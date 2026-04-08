import asyncio
import logging
import os
from dotenv import load_dotenv

# Force loading .env from the backend path
backend_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(backend_dir, '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)

from core.config import settings

logging.basicConfig(level=logging.INFO)

async def setup_analysis_schema():
    from models.database import _engine, _pg_available
    if _pg_available and _engine is not None:
        try:
            async with _engine.begin() as conn:
                from sqlalchemy import text
                
                # 1. users table already exists with id and phone columns


                # 2. purchase_orders table
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS purchase_orders (
                        id SERIAL PRIMARY KEY,
                        po_number VARCHAR UNIQUE NOT NULL,
                        vendor VARCHAR,
                        amount NUMERIC,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    );
                """))
                print("✅ Checked `purchase_orders` table.")

                # 3. invoice_analysis table
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS invoice_analysis (
                        id SERIAL PRIMARY KEY,
                        document_id VARCHAR UNIQUE NOT NULL,
                        user_id VARCHAR,
                        confidence_score NUMERIC,
                        status VARCHAR,
                        flags JSONB,
                        processed_at TIMESTAMPTZ DEFAULT NOW()
                    );
                """))
                print("✅ Checked `invoice_analysis` table.")
                

                
                print("🎉 Analysis schema setup complete!")
        except Exception as e:
            print(f"❌ Failed to setup analysis schema: {e}")
    else:
        print("⚠️ PostgreSQL not available. Check DATABASE_URL in .env.")

if __name__ == "__main__":
    asyncio.run(setup_analysis_schema())
