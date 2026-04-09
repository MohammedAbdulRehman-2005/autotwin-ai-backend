require('dotenv').config();
const express = require('express');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const { createClient } = require('@supabase/supabase-js');
const Groq = require('groq-sdk');

const app = express();
app.use(express.json());

// Railway Health Check
app.get('/health', (req, res) => res.status(200).send('OK'));

let latestQR = null;

// QR Code Web Endpoint
app.get('/qr', async (req, res) => {
  if (!latestQR) {
    return res.send('<h2>WhatsApp is currently connected or starting up! If you need to scan a QR code, please wait a moment and refresh.</h2>');
  }
  try {
    const qrcodeImg = require('qrcode');
    const url = await qrcodeImg.toDataURL(latestQR);
    res.send(`
      <div style="font-family: sans-serif; text-align: center; margin-top: 50px;">
        <h2>Scan this QR code with WhatsApp</h2>
        <img src="${url}" style="width: 300px; height: 300px; border: 2px solid #ccc; border-radius: 10px;" />
        <p>Refresh this page if it times out.</p>
      </div>
    `);
  } catch (err) {
    res.status(500).send('Error generating QR Image');
  }
});

const PORT = process.env.PORT || 3001;
const FASTAPI_URL = process.env.FASTAPI_URL || 'http://localhost:8000';


// ── Clients ─────────────────────────────────────────────────
const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_ANON_KEY);
const groq = new Groq({ apiKey: process.env.GROQ_API_KEY });

// ── WhatsApp Setup ───────────────────────────────────────────
const client = new Client({
  authStrategy: new LocalAuth({ dataPath: ".wwebjs_auth" }),
  puppeteer: {
    headless: true,
    ...(process.env.PUPPETEER_EXECUTABLE_PATH
      ? { executablePath: process.env.PUPPETEER_EXECUTABLE_PATH }
      : {}),
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-accelerated-2d-canvas",
      "--no-first-run",
      "--no-zygote",
      "--disable-gpu",
      "--disable-site-isolation-trials",
      "--disable-software-rasterizer",
      "--disable-extensions",
      "--memory-pressure-off",
      "--single-process"
    ]
  }
});

client.on('qr', (qr) => {
  latestQR = qr;
  console.log('\n=========================================');
  console.log('📌 NEW QR CODE GENERATED!');
  console.log('🌐 OPEN YOUR DEPLOYMENT URL + /qr TO SCAN IT AS AN IMAGE!');
  console.log('Example: https://your-railway-app.up.railway.app/qr');
  console.log('=========================================\n');
  qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
  latestQR = null;
  console.log('✅ WhatsApp Web Client is READY!');
});

// ══════════════════════════════════════════════════════════════
// INTENT-SPECIFIC DATA FETCHERS
// ══════════════════════════════════════════════════════════════

const todayISO = () => {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
};

async function fetchInvoiceSummary() {
  try {
    const { data: docs } = await supabase
      .from('extracted_documents')
      .select('id, vendor, amount, anomaly, decision, confidence, created_at')
      .gte('created_at', todayISO())
      .order('created_at', { ascending: false });

    const total = docs?.length || 0;
    const anomalies = docs?.filter(d => d.anomaly).length || 0;
    const autoApproved = docs?.filter(d => d.decision === 'auto_execute').length || 0;
    const pending = docs?.filter(d => d.decision === 'human_review').length || 0;
    const avgConf = total > 0
      ? (docs.reduce((s, d) => s + (d.confidence || 0), 0) / total * 100).toFixed(1)
      : 0;

    // Top vendors by volume
    const vendorMap = {};
    docs?.forEach(d => {
      vendorMap[d.vendor] = (vendorMap[d.vendor] || 0) + 1;
    });
    const topVendors = Object.entries(vendorMap)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([v, c]) => `${v} (${c})`);

    return {
      intent: 'invoice_summary',
      total_invoices: total,
      anomalies,
      auto_approved: autoApproved,
      pending_review: pending,
      avg_confidence_pct: avgConf,
      top_vendors: topVendors,
    };
  } catch (e) {
    console.error('fetchInvoiceSummary error:', e.message);
    return null;
  }
}

async function fetchAnomalyDetails() {
  try {
    const { data: docs } = await supabase
      .from('extracted_documents')
      .select('vendor, amount, confidence, explanation, anomaly_details, created_at')
      .eq('anomaly', true)
      .gte('created_at', todayISO())
      .order('created_at', { ascending: false })
      .limit(10);

    return {
      intent: 'anomaly_details',
      count: docs?.length || 0,
      anomalies: docs?.map(d => ({
        vendor: d.vendor,
        amount: d.amount,
        confidence: ((d.confidence || 0) * 100).toFixed(0) + '%',
        reason: d.explanation || 'No explanation provided',
      })) || [],
    };
  } catch (e) {
    console.error('fetchAnomalyDetails error:', e.message);
    return null;
  }
}

async function fetchCashFlowData() {
  try {
    const { data: txns } = await supabase
      .from('transactions')
      .select('amount, vendor, category, date, anomaly_score')
      .gte('created_at', todayISO())
      .order('date', { ascending: false });

    const totalInflow = txns?.reduce((s, t) => s + (t.amount || 0), 0) || 0;
    const byCategory = {};
    txns?.forEach(t => {
      byCategory[t.category || 'Other'] = (byCategory[t.category || 'Other'] || 0) + t.amount;
    });

    const topCategories = Object.entries(byCategory)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([cat, amt]) => `${cat}: ₹${amt.toFixed(0)}`);

    return {
      intent: 'cash_flow',
      total_transactions: txns?.length || 0,
      total_amount_inr: totalInflow.toFixed(2),
      avg_transaction: txns?.length
        ? (totalInflow / txns.length).toFixed(2)
        : '0.00',
      category_breakdown: topCategories,
    };
  } catch (e) {
    console.error('fetchCashFlowData error:', e.message);
    return null;
  }
}

async function fetchPaymentStatus() {
  try {
    const { data: txns } = await supabase
      .from('transactions')
      .select('vendor, amount, date, category')
      .gte('created_at', todayISO())
      .order('date', { ascending: false })
      .limit(10);

    const { data: approvals } = await supabase
      .from('approvals')
      .select('invoice_id, status, notes, resolved_at')
      .gte('created_at', todayISO());

    return {
      intent: 'payment_status',
      payments_done: txns?.length || 0,
      total_paid_inr: txns?.reduce((s, t) => s + (t.amount || 0), 0).toFixed(2) || '0',
      recent_payments: txns?.slice(0, 5).map(t => ({
        vendor: t.vendor,
        amount: `₹${t.amount}`,
        category: t.category,
      })) || [],
      approvals_today: approvals?.length || 0,
      approved_count: approvals?.filter(a => a.status === 'approved').length || 0,
      rejected_count: approvals?.filter(a => a.status === 'rejected').length || 0,
    };
  } catch (e) {
    console.error('fetchPaymentStatus error:', e.message);
    return null;
  }
}

async function fetchPendingReview() {
  try {
    const { data: docs } = await supabase
      .from('extracted_documents')
      .select('vendor, amount, confidence, explanation, created_at')
      .eq('decision', 'human_review')
      .order('created_at', { ascending: false })
      .limit(10);

    return {
      intent: 'pending_review',
      count: docs?.length || 0,
      items: docs?.map(d => ({
        vendor: d.vendor,
        amount: `₹${d.amount}`,
        confidence: ((d.confidence || 0) * 100).toFixed(0) + '%',
        reason: d.explanation || 'Manual check required',
      })) || [],
    };
  } catch (e) {
    console.error('fetchPendingReview error:', e.message);
    return null;
  }
}

async function fetchDailyReport() {
  const [inv, anomaly, cash, pay] = await Promise.all([
    fetchInvoiceSummary(), fetchAnomalyDetails(), fetchCashFlowData(), fetchPaymentStatus()
  ]);
  return {
    intent: 'daily_report',
    invoice_summary: inv,
    anomaly_summary: { count: anomaly?.count },
    cash_flow: { total_transactions: cash?.total_transactions, total_amount_inr: cash?.total_amount_inr },
    payments: { done: pay?.payments_done, total_paid: pay?.total_paid_inr, approvals: pay?.approvals_today },
  };
}

// ══════════════════════════════════════════════════════════════
// AI GENERATION (PERSONALISED PER INTENT)
// ══════════════════════════════════════════════════════════════

const SYSTEM_PROMPT = `You are AutoTwin AI, an intelligent invoice & finance assistant.
Rules:
- ALWAYS answer based STRICTLY on the provided business data below.
- NEVER give generic stats — personalize the response to the exact question asked.
- Use emojis selectively (not on every line).
- Keep the response concise and WhatsApp-friendly (no markdown headers, use bullet points).
- If a value is 0 or null, say "None today" — do NOT fabricate data.
- Detect the user's language and reply in the SAME language.`;

async function generatePersonalisedResponse(data, userMessage) {
  try {
    console.log(`🧠 Generating AI response for intent=${data?.intent || 'general'}...`);
    const completion = await groq.chat.completions.create({
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        {
          role: "user",
          content: `User asked: "${userMessage}"\n\nBusiness data (use ONLY this):\n${JSON.stringify(data, null, 2)}\n\nRespond in a personalised, helpful way strictly based on the data above.`
        },
      ],
      model: "llama-3.3-70b-versatile",
      temperature: 0.3,
      max_tokens: 400,
    });
    return completion.choices[0].message.content;
  } catch (e) {
    console.error('❌ Groq failed:', e.message);
    return `⚠️ AI engine temporarily unavailable. Here's the raw data:\n${JSON.stringify(data, null, 2)}`;
  }
}

// ══════════════════════════════════════════════════════════════
// INTENT DETECTION ENGINE
// ══════════════════════════════════════════════════════════════

function detectIntent(text) {
  const t = text.toLowerCase();

  if (['hi', 'hello', 'hey', 'namaste', 'hii', 'yo'].includes(t)) return 'menu';

  if (t === '1' || /invoice\s*(summary|status|count|list|today)/.test(t)) return 'invoice_summary';
  if (t === '2' || /payment\s*(status|done|made|completed|list)|paid|transactions/.test(t)) return 'payment_status';
  if (t === '3' || /daily\s*report|full report|overview|summary/.test(t)) return 'daily_report';
  if (t === '4' || /anomal|fraud|suspicious|flagged|risk/.test(t)) return 'anomaly_details';
  if (t === '5' || /pending|review|waiting|needs.review|human review/.test(t)) return 'pending_review';
  if (/cash\s*flow|cashflow|money flow|inflow|outflow|spend|expenditure|expense/.test(t)) return 'cash_flow';

  // Natural language fallback
  if (/invoice|bill|document|vendor|processed/.test(t)) return 'invoice_summary';
  if (/payment|pay|transaction|transfer|amount paid/.test(t)) return 'payment_status';
  if (/anomal|warning|alert|flag/.test(t)) return 'anomaly_details';
  if (/pending|review|check|approve/.test(t)) return 'pending_review';

  return null;
}

const MENU = `👋 Welcome to *AutoTwin AI*

What would you like to know?

1️⃣ Invoice Summary
2️⃣ Payment Status
3️⃣ Daily Report
4️⃣ Anomaly Details
5️⃣ Pending Reviews

💬 Or just ask naturally — e.g. "What's the cash flow today?"`;

// ══════════════════════════════════════════════════════════════
// WHATSAPP MESSAGE HANDLER
// ══════════════════════════════════════════════════════════════

client.on('message', async msg => {
  try {
    const text = msg.body.trim();
    const upper = text.toUpperCase();

    // ── 1. APPROVAL HANDLER ─────────────────────────────────
    // Handles: "APPROVE <document_id>" or "REJECT <document_id>"
    const approveMatch = upper.match(/^(APPROVE|REJECT)\s+([\w-]+)$/);
    if (approveMatch) {
      const action   = approveMatch[1];
      const docId    = approveMatch[2];
      const approved = action === 'APPROVE';
      console.log(`\ud83d\udcdd Approval reply: ${action} for document_id=${docId}`);
      try {
        const axios = require('axios');
        const resp = await axios.post(`${FASTAPI_URL}/api/approve`, {
          invoice_id: docId,
          approved: approved,
          reviewer_notes: `WhatsApp ${action.toLowerCase()} by ${msg.from}`
        });
        const d = resp.data;
        const confirmMsg = approved
          ? `\u2705 Invoice *${docId}* has been *Approved*\n\ud83d\udcca New confidence: ${(d.updated_confidence * 100).toFixed(0)}%\n${d.message}`
          : `\u274c Invoice *${docId}* has been *Rejected*\n\ud83d\udccc Flagged for re-processing.\n${d.message}`;
        await msg.reply(confirmMsg);
        return;
      } catch (err) {
        console.error('\u274c FastAPI approval callback failed:', err.message);
        await msg.reply(`\u26a0\ufe0f Could not process your ${action} request. Please use the dashboard.`);
        return;
      }
    }

    // ── 2. MENU & INTENT ROUTING ─────────────────────────────
    const intent = detectIntent(text);
    console.log(`\ud83d\udce5 Message: "${text}" \u2192 intent: ${intent || 'none'}`);

    if (intent === 'menu' || !intent) {
      return await msg.reply(MENU);
    }

    const loadingMessages = {
      invoice_summary: '\ud83d\udcc4 Fetching invoice details...',
      payment_status:  '\ud83d\udcb0 Checking payment records...',
      daily_report:    '\ud83d\udcca Building your daily report...',
      anomaly_details: '\ud83d\udd0d Scanning anomalies...',
      pending_review:  '\ud83d\udd52 Fetching pending reviews...',
      cash_flow:       '\ud83d\udcb9 Analysing cash flow...',
    };

    await msg.reply(loadingMessages[intent] || '\u23f3 Processing...');

    let data;
    switch (intent) {
      case 'invoice_summary': data = await fetchInvoiceSummary(); break;
      case 'payment_status':  data = await fetchPaymentStatus();  break;
      case 'daily_report':    data = await fetchDailyReport();    break;
      case 'anomaly_details': data = await fetchAnomalyDetails(); break;
      case 'pending_review':  data = await fetchPendingReview();  break;
      case 'cash_flow':       data = await fetchCashFlowData();   break;
      default: data = await fetchInvoiceSummary();
    }

    const response = await generatePersonalisedResponse(data, text);
    console.log('\ud83d\udce4 Sending personalised response...');
    await msg.reply(response);

  } catch (err) {
    console.error('\u274c Message handler error:', err);
    await msg.reply('\u26a0\ufe0f AutoTwin AI encountered an error. Please try again.');
  }
});

client.initialize();

// ══════════════════════════════════════════════════════════════
// REST API ENDPOINTS
// ══════════════════════════════════════════════════════════════

// Direct send (used by Python analysis engine)
app.post('/send', async (req, res) => {
  try {
    const { number, message } = req.body;
    if (!number || !message) return res.status(400).json({ error: "Missing number or message" });
    const chatId = number.includes('@c.us') ? number : `${number}@c.us`;
    await client.sendMessage(chatId, message);
    res.json({ success: true, message: "Sent!" });
  } catch (e) {
    console.error('POST /send error:', e);
    res.status(500).json({ error: e.message });
  }
});

// Legacy endpoint kept for compatibility
app.post('/api/send', async (req, res) => {
  try {
    const { number, message } = req.body;
    if (!number || !message) return res.status(400).json({ error: "Missing number or message" });
    const chatId = number.includes('@c.us') ? number : `${number}@c.us`;
    await client.sendMessage(chatId, message);
    res.json({ success: true, message: "Sent!" });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/daily-summary', async (req, res) => {
  try {
    const { number } = req.body;
    if (!number) return res.status(400).json({ error: "Missing number" });
    const chatId = number.includes('@c.us') ? number : `${number}@c.us`;
    const data = await fetchDailyReport();
    const summary = await generatePersonalisedResponse(data, "Generate a daily automated report.");
    await client.sendMessage(chatId, summary);
    res.json({ success: true, summary_sent: summary });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.listen(PORT, '0.0.0.0', () => console.log(`🚀 REST API Server running on port ${PORT}`));
