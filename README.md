# Funnel Diagnostics

Upload funnel metrics (or raw D2C order history) → get a diagnosed leak, a
visual breakdown, and 3 ranked recommendations with sample copy — downloadable
as a PDF.

**Architecture:** two systems, one clean seam.
- **Streamlit app** (this repo) owns the frontend, file upload, and all
  deterministic math (cohort retention %, time-to-2nd-order, discount
  dependency, or period-over-period funnel deltas) — computed with pandas,
  never by an LLM.
- **n8n workflow** (`funnel-diagnostics-agent.json`) owns the AI reasoning:
  a 5-agent chain (Interpreter → Root Cause → Prioritization → Play Designer
  → Compiler) that receives only the *computed stats* — never raw rows —
  diagnoses the leak, and returns a structured verdict plus 3 ranked plays.
  It also generates the downloadable PDF.

The Streamlit app calls the n8n workflow over a webhook (`ai_diagnosis.py`)
and renders whatever comes back. Swap the n8n workflow for a different
backend and the frontend doesn't need to change.

## Two input modes

1. **Metrics snapshot** — any funnel metric, pre-aggregated. Columns:
   `date, segment, metric_name, value`. One row per date + segment + metric.
   Works for activation, conversion, retention — anything you track over time
   by segment. This is the primary, general-purpose mode.
2. **Order-level data** — raw D2C order history. Columns:
   `customer_id, order_date, order_value` (optional: `category, discount_used,
   channel`). Unlocks the cohort retention heatmap and lifecycle-specific
   diagnosis (M1→M2 cliff, discount dependency, channel repeat rate).

Both modes feed the same n8n agent chain and render in the same UI shell;
only the middle "detail" section (heatmap vs. flagged-changes table) differs.

## Files

- `app.py` — Streamlit UI (mode toggle, upload, sample data, results, PDF download)
- `analytics.py` — deterministic engine for both modes
- `ai_diagnosis.py` — calls the n8n webhook, returns the structured diagnosis + PDF link
- `sample_data.py` — generates realistic sample data for both modes (each with
  a deliberate anomaly baked in, so the app is demo-able with zero upload)
- `funnel-diagnostics-agent.json` — the n8n workflow to import
- `requirements.txt`, `.env.example` — Python dependencies and required env var

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env   # then paste your n8n webhook URL into .env
export $(cat .env | xargs)
streamlit run app.py
```

## Set up the n8n workflow

See the companion setup guide for full step-by-step instructions. Short version:
1. Import `funnel-diagnostics-agent.json` into n8n (Cloud or self-hosted).
2. Add a **Groq** credential (free at console.groq.com) to the shared "Groq
   Chat Model" node — powers all 5 agents.
3. Add a **PDF.co** credential (free at pdf.co, Header Auth, header name
   `x-api-key`) to the "Generate PDF" node.
4. Activate the workflow and copy its production webhook URL into
   `N8N_WEBHOOK_URL`.

## Deploy to Render (Streamlit app)

1. Push this folder to a new GitHub repo.
2. Render → New → Web Service → connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`
5. Environment tab → add `N8N_WEBHOOK_URL` with your n8n production webhook URL.
6. Deploy. Free tier is fine to start; note the cold-start delay after idle.

## Known limitations / next iteration ideas

- Cohort matrix currently caps at 6 months — fine for a 6-12 month order
  history, would need a rolling window for longer histories.
- `channel_repeat_rate` attributes a customer's repeat status to every
  channel they've ordered through — directionally useful, not a true
  channel-attribution model.
- Funnel-snapshot delta detection compares consecutive periods only — no
  seasonality adjustment or statistical significance testing.
- No persistent storage — each session is a fresh upload/run. Worth adding
  the same JSONBin/Google Sheets pattern used in other projects if you want
  to save diagnoses across sessions.
- The Compiler agent's `category_benchmark` / `best_in_class` numbers are
  LLM-estimated assumptions, not sourced data — call this out if presenting
  the tool, or wire in a real benchmark source later.
