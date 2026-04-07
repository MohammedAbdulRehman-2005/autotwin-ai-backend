import asyncio
import logging
from core.config import settings

logging.basicConfig(level=logging.INFO)

async def test_startup():
    print("Testing connection...")
    
    # 1. Test PostgreSQL Engine
    from models.database import _engine, _pg_available
    if _pg_available and _engine is not None:
        try:
            async with _engine.connect() as conn:
                from sqlalchemy import text
                await conn.execute(text("SELECT 1"))
            print("✅ Supabase PostgreSQL connected.")
        except Exception as e:
            print(f"❌ PostgreSQL probe failed: {e}")
    else:
        print("❌ PostgreSQL unavailable.")

    # 2. Test Supabase Storage
    try:
        from models.supabase_client import ensure_bucket_exists, is_storage_available, _supabase_client
        if is_storage_available() and _supabase_client is not None:
            await ensure_bucket_exists()
            buckets = _supabase_client.storage.list_buckets()
            bucket_names = [b.name for b in buckets]
            print(f"✅ Supabase Storage ready. Buckets: {bucket_names}")
        else:
            print("❌ Supabase Storage unavailable.")
    except Exception as e:
        print(f"❌ Storage bootstrap error: {e}")
        
    # Shutdown engine
    if _engine is not None:
        await _engine.dispose()

if __name__ == "__main__":
    asyncio.run(test_startup())
