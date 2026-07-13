# Funnel Diagnostics — Setup Guide

## What this is
A two-part app: a Streamlit frontend (upload + deterministic math + results UI)
talking to an n8n workflow (5-agent AI diagnosis + PDF generation) over a
webhook. See `README.md` for full architecture notes.

## Files in this project
- `app.py`, `analytics.py`, `ai_diagnosis.py`, `sample_data.py` — the Streamlit app
- `requirements.txt`, `.env.example` — Python setup
- `funnel-diagnostics-agent.json` — the n8n workflow to import
- `README.md` — architecture and deploy reference

## Step 1 — Import the n8n workflow
In n8n: **Workflows → Add workflow → Import from File**, select
`funnel-diagnostics-agent.json`.

## Step 2 — Add credentials (both free)

**Groq** (powers all 5 AI agents)
- Free API key at console.groq.com
- Open any Agent node → the attached "Groq Chat Model" node → create new
  credential → paste your key. All 5 agents share this one node, so you only
  do this once.

**PDF.co** (converts the final report to a downloadable PDF)
- Free sign-up at pdf.co, grab your API key from the dashboard
- Open the "Generate PDF" node → Credentials → create new **Header Auth**
  credential → Name: `x-api-key`, Value: your PDF.co key

## Step 3 — Activate and copy the webhook URL
Activate the workflow. Open the "Webhook" node and copy its **production**
URL (not the test URL) — it'll look like
`https://your-instance.app.n8n.cloud/webhook/funnel-diagnosis`.

## Step 4 — Run the Streamlit app
```bash
pip install -r requirements.txt
cp .env.example .env
# paste the webhook URL from Step 3 into .env as N8N_WEBHOOK_URL
export $(cat .env | xargs)
streamlit run app.py
```

## Step 5 — Test it
1. Open the local Streamlit URL it prints
2. Pick a mode ("Metrics snapshot" is the default and needs no special data —
   D2C order data needs the order-level schema, see `README.md`)
3. Click "Load sample data" (or upload your own CSV)
4. You should see the diagnosis, benchmark chart, and 3 prescribed plays
   appear, followed by a PDF download button

## If something breaks

- **"N8N_WEBHOOK_URL is not set"** — check `.env` was created and exported,
  or that the Render environment variable is set if deployed
- **Agent nodes error / no response** — check the Groq credential is attached
  and you haven't hit Groq's free-tier rate limit (add a Wait node between
  agents in n8n if so)
- **PDF generation fails** — check the PDF.co credential header name is
  exactly `x-api-key`, and your free-tier quota isn't exhausted. The app
  still shows the diagnosis even if the PDF step fails — only the download
  button will be missing.
- **Compiler agent output won't parse as JSON** — Groq models occasionally
  wrap JSON in markdown fences despite instructions; if this happens
  repeatedly, add a small cleanup step to the "Format Report HTML" code node
  to strip leading/trailing backticks before `JSON.parse`.
- **Webhook times out** — 5 sequential agent calls plus a PDF generation call
  can take 30-90 seconds; this is normal. If it's consistently too slow,
  consider running the Prioritization + Play Designer agents in parallel
  instead of sequentially.
