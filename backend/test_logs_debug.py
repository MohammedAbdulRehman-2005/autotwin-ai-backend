import asyncio
import logging
from models.database import save_log_entry, get_logs_for_invoice
from core.config import settings

logging.basicConfig(level=logging.DEBUG)

async def test_logs():
    user_id = "test_user"
    invoice_id = "f07cc1b0-1845-420a-b30f-bce6a31c69a7"
    
    # Let's insert a log
    await save_log_entry(invoice_id, {"step": "init", "message": "hello", "level": "info", "metadata": {"test": 1}}, user_id=user_id)
    
    logs = await get_logs_for_invoice(invoice_id, user_id=user_id)
    print("Logs fetched:")
    print(logs)

if __name__ == "__main__":
    asyncio.run(test_logs())
