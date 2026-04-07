import asyncio
import logging
from uuid import uuid4
from models.database import save_invoice, get_invoice, save_log_entry, update_vendor_history

logging.basicConfig(level=logging.DEBUG)

async def test():
    user_id = "test_user"
    invoice_id = str(uuid4())
    print("\n--- Testing save_invoice ---")
    data = {
        "invoice_id": invoice_id,
        "vendor": "Test Vendor",
        "amount": 100.0,
        "date": "2023-10-10",
        "currency": "USD",
        "status": "pending",
        "confidence": 0.90,
    }
    
    try:
        await save_invoice(data, user_id=user_id)
        print("✅ save_invoice passed")
    except Exception as e:
        print(f"❌ save_invoice FAILED: {e}")
        
    print("\n--- Testing update_vendor_history ---")
    try:
        await update_vendor_history("Test Vendor", data, user_id=user_id)
        print("✅ update_vendor_history passed")
    except Exception as e:
        print(f"❌ update_vendor_history FAILED: {e}")
        
    print("\n--- Testing save_log_entry ---")
    try:
        await save_log_entry(invoice_id, {"step": "init", "message": "test", "level": "info"}, user_id=user_id)
        print("✅ save_log_entry passed")
    except Exception as e:
        print(f"❌ save_log_entry FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(test())
