require('dotenv').config();
const express = require('express');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const { createClient } = require('@supabase/supabase-js');
const Groq = require('groq-sdk');

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 3001;

// ── 1. Initializes Database and AI ─────────────────────────────────────
const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_ANON_KEY);
const groq = new Groq({ apiKey: process.env.GROQ_API_KEY });

// ── 2. WhatsApp Client Setup ───────────────────────────────────────────
const client = new Client({
  authStrategy: new LocalAuth({
    dataPath: ".wwebjs_auth"
  }),
  puppeteer: {
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-accelerated-2d-canvas",
      "--no-first-run",
      "--no-zygote",
      "--single-process",
      "--disable-gpu"
    ]
  }
});

client.on('qr', (qr) => {
  console.log('\n=========================================');
  console.log('📱 SCAN THIS QR CODE WITH WHATSAPP:');
  console.log('=========================================\n');
  qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
  console.log('✅ WhatsApp Web Client is READY!');
});

// ── 3. Data Fetching (Supabase) ────────────────────────────────────────
async function getTodaysStats() {
  try {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const startOfDay = today.toISOString();

    console.log('🔍 Fetching data from Supabase...');
    
    // Fetch newly processed documents today
    const { data: docs } = await supabase
      .from('extracted_documents')
      .select('*')
      .gte('created_at', startOfDay);

    // Fetch transactions/payments today
    const { data: payments } = await supabase
      .from('transactions')
      .select('*')
      .gte('created_at', startOfDay);

    const invoicesProcessed = docs ? docs.length : 0;
    const anomalies = docs ? docs.filter(d => d.anomaly === true).length : 0;
    const humanReviews = docs ? docs.filter(d => d.decision === 'human_review').length : 0;
    const paymentsDone = payments ? payments.length : 0;

    return {
      invoices_processed: invoicesProcessed,
      anomalies: anomalies,
      pending_review: humanReviews,
      payments_done: paymentsDone
    };
  } catch (error) {
    console.error('❌ Supabase fetch failed:', error.message);
    return null;
  }
}

// ── 4. AI Generation (Groq) ────────────────────────────────────────────
async function generateSummary(data, userMessage) {
  try {
    console.log('🧠 Generating AI summary via Groq...');

    const chatCompletion = await groq.chat.completions.create({
      messages: [
        {
          role: "system",
          content: "You are an AI business assistant.\n- Detect the user's language automatically\n- Respond in the SAME language\n- Keep responses short and WhatsApp-friendly\n- Use bullet points and emojis\n- Provide actionable insights when possible",
        },
        {
          role: "user",
          content: `User message: ${userMessage}\nBusiness data: ${JSON.stringify(data)}\nFormulate the response matching the user's intent based on the context data.`,
        },
      ],
      model: "llama-3.3-70b-versatile", // Switched to 70B versatile model
      temperature: 0.5,
    });

    return chatCompletion.choices[0].message.content;
  } catch (error) {
    console.error('❌ Groq API failed:', error.message);
    // ── FALLBACK for Demo ──
    return `*AutoTwin AI Daily Summary* 🤖\n\nFallback Mode Active.\n- Invoices Processed: ${data.invoices_processed}\n- Anomalies Detected: ${data.anomalies}\n- Pending Reviews: ${data.pending_review}\n- Payments Done: ${data.payments_done}\n\n_Please check the dashboard for details._`;
  }
}

// ── 5. WhatsApp Auto-Reply ─────────────────────────────────────────────
client.on('message', async msg => {
  try {
    const text = msg.body.trim().toLowerCase();
    const greetings = ['hi', 'hello', 'hey', 'namaste'];
    
    // 1. Menu Trigger (Greetings)
    if (greetings.includes(text)) {
      console.log(`📥 Received greeting from ${msg.from}`);
      const menu = `👋 Welcome to AutoTwin AI\n\nHow can I assist you today?\n\n1️⃣ Invoice Summary\n2️⃣ Payment Status\n3️⃣ Daily Report\n\n👉 Reply with 1, 2, or 3`;
      return await msg.reply(menu);
    }
    
    // 2. Option & Natural Language Intent Routing
    let queryIntent = null;
    let loadingMsg = '⏳ Gathering your intelligence report...';

    if (text === '1') {
      loadingMsg = '📄 Fetching Invoice Summary...';
      queryIntent = 'Focus strictly on the invoice summary, including anomalies and pending invoices.';
    } else if (text === '2') {
      loadingMsg = '💰 Fetching Payment Status...';
      queryIntent = 'Focus strictly on the payment status and transactions completed today.';
    } else if (text === '3') {
      loadingMsg = '📊 Generating Daily Report...';
      queryIntent = 'Provide a full daily report of all business data today.';
    } else if (text.includes('summary') || text.includes('invoice') || text.includes('payment') || text.includes('report')) {
      loadingMsg = '⏳ Gathering your intelligence report...';
      queryIntent = msg.body; // Full natural language context
    }

    // 3. Dispatch to LLM safely
    if (queryIntent) {
      console.log(`📥 Received trigger message: "${msg.body}" from ${msg.from}`);
      await msg.reply(loadingMsg);
      
      const stats = await getTodaysStats() || { invoices_processed: 0, anomalies: 0, pending_review: 0, payments_done: 0 };
      
      const aiResponse = await generateSummary(stats, queryIntent);
      console.log('📤 Sending WhatsApp response...');
      await msg.reply(aiResponse);
    }
  } catch (err) {
    console.error('❌ Message Handler Execution Error:', err);
  }
});

client.initialize();

// ── 6. REST API Endpoints ──────────────────────────────────────────────

app.post('/api/send', async (req, res) => {
  try {
    const { number, message } = req.body;
    if (!number || !message) return res.status(400).json({ error: "Missing number or message" });

    // Ensure number has @c.us suffix
    const chatId = number.includes('@c.us') ? number : `${number}@c.us`;
    await client.sendMessage(chatId, message);
    
    res.json({ success: true, message: "Message sent!" });
  } catch (error) {
    console.error('API Send Error:', error);
    res.status(500).json({ error: error.message });
  }
});

app.post('/api/daily-summary', async (req, res) => {
  try {
    const { number } = req.body;
    if (!number) return res.status(400).json({ error: "Missing Target Number" });

    const chatId = number.includes('@c.us') ? number : `${number}@c.us`;
    
    const stats = await getTodaysStats() || { invoices_processed: 0, anomalies: 0, pending_review: 0, payments_done: 0 };
    const summary = await generateSummary(stats, "Generate a daily automated report.");

    await client.sendMessage(chatId, summary);
    res.json({ success: true, summary_sent: summary });
    
  } catch (error) {
    console.error('API Summary Error:', error);
    res.status(500).json({ error: error.message });
  }
});

app.listen(PORT, () => {
  console.log(`🚀 REST API Server running on port ${PORT}`);
});
