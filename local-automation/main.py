import asyncio
from fastapi import FastAPI
from pydantic import BaseModel
from playwright.async_api import async_playwright
import uvicorn

app = FastAPI(title="Local Automation ERP System")

class AutomationRequest(BaseModel):
    vendor: str
    amount: float
    gst: float = 0.0
    invoice_id: str
    po_number: str

@app.post("/run-automation")
async def run_automation(request: AutomationRequest):
    print("\n" + "="*40)
    print("--- New Automation Request Received ---")
    print(f"Vendor:     {request.vendor}")
    print(f"Amount:     {request.amount}")
    print(f"GST:        {request.gst}")
    print(f"Invoice ID: {request.invoice_id}")
    print(f"PO Number:  {request.po_number}")
    print("="*40)
    
    try:
        print("🌐 Opening ERP...")
        async with async_playwright() as p:
            # Launch chromium in non-headless mode for visibility (demo-safe)
            # slow_mo adds a delay between actions so the demo is visible
            browser = await p.chromium.launch(headless=False, slow_mo=300)
            page = await browser.new_page()
            
            # Open local ERP page
            await page.goto("http://localhost:5500/erp.html")
            
            print("✍ Filling data...")
            # Fill inputs according to the requirements
            await page.fill('input[name="vendor"]', request.vendor)
            await page.fill('input[name="invoice_id"]', request.invoice_id)
            await page.fill('input[name="po"]', request.po_number)
            await page.fill('input[name="gst"]', str(request.gst))
            await page.fill('input[name="amount"]', str(request.amount))
            
            # Optional: highlight fields for demo visibility
            await page.evaluate("""() => {
                document.querySelectorAll('input').forEach(el => {
                    el.style.border = '2px solid #28a745';
                    el.style.backgroundColor = '#e8f5e9';
                });
            }""")
            
            print("📤 Submitting...")
            await asyncio.sleep(1) # Extra pause right before submit
            await page.click('button[type="submit"]')
            
            # Wait 2 seconds as requested
            await asyncio.sleep(2)
            
            # Save screenshot
            screenshot_path = "submission_screenshot.png"
            await page.screenshot(path=screenshot_path)
            
            print("✅ Done")
            print(f"📸 Screenshot saved to {screenshot_path}")
            
            await browser.close()
            
            return {"status": "success"}

    except Exception as e:
        print(f"❌ Automation failed: {e}")
        return {"status": "failed", "error": str(e)}

if __name__ == "__main__":
    # Binding to 0.0.0.0 is crucial so that other machines (e.g. backend)
    # can reach this via http://<LOCAL_IP>:9000/run-automation
    uvicorn.run("main:app", host="0.0.0.0", port=9000, reload=True)
