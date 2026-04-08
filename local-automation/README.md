# Local Automation Agent Setup

I have created the Local Automation Agent exactly according to your specifications. The service runs a FastAPI server, accepts POST requests from the cloud backend, and visually fills out a local dummy ERP system using Playwright.

## File Structure

The new component has been placed securely in the `local-automation` folder within your backend workspace:

```text
autotwin AI backend/
 ├── local-automation/
 │    ├── main.py            # FastAPI + Playwright server
 │    ├── erp.html           # Simple ERP form page
 │    ├── requirements.txt   # Dependencies
 │    ├── README.md          # These run instructions
```

## Running the System

You'll need two terminals for this demo to work fully.

### 1. Set Up and Install Dependencies
Open a terminal in the `local-automation` directory:

```bash
cd "c:\Users\lenov\Desktop\autotwin AI backend\local-automation"

# Create a virtual environment (optional but recommended)
python -m venv venv
venv\Scripts\activate

# Install requirements
pip install -r requirements.txt

# Install Playwright browsers (CRITICAL for first time use)
playwright install chromium
```

### 2. Start the Dummy ERP Page
We need a local server to serve the `erp.html` file on port 5500:

```bash
# In the local-automation folder
python -m http.server 5500
```
This serves `http://localhost:5500/erp.html`

### 3. Start the FastAPI Server
Open a second terminal in the `local-automation` directory (ensure your virtual environment is active):

```bash
uvicorn main:app --host 0.0.0.0 --port 9000
```
*Note: We bind to `0.0.0.0` to fulfill the requirement that your Railway cloud backend can reach it via `http://<YOUR_PUBLIC_MAPPED_IP>:9000/run-automation`.*

## Testing the Trigger locally

You can test this works by mimicking your cloud backend and making a curl POST request:

```bash
curl -X POST http://localhost:9000/run-automation \
-H "Content-Type: application/json" \
-d '{"vendor": "ABC Ltd", "amount": 5000, "invoice_id": "INV123", "po_number": "PO123"}'
```

Wait a moment – you will visually see Chromium open, visit `http://localhost:5500/erp.html`, highlight the fields as it types, and submit the data. An automated screenshot is also saved as `submission_screenshot.png` directly in the folder!
