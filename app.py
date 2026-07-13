import os
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go

from analytics import (
    validate_and_load, compute_cohort_matrix, compute_core_stats, find_m1_m2_cliff,
    validate_and_load_snapshot, compute_funnel_deltas,
)
from ai_diagnosis import diagnose
from sample_data import generate as generate_sample, generate_snapshot as generate_snapshot_sample

st.set_page_config(page_title="Funnel Diagnostics", page_icon="\U0001F4CA", layout="centered")

# ---------- Styling ----------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

html, body, [class*="css"]  { font-family: 'Inter', sans-serif; }
h1, h2, h3 { font-family: 'Space Grotesk', sans-serif !important; }
.mono { font-family: 'IBM Plex Mono', monospace; }

.verdict-box {
    background: #FFFFFF; border: 1px solid #DDE2E0; border-top: 4px solid #C24632;
    border-radius: 4px; padding: 24px 26px; margin: 10px 0 22px 0;
}
.verdict-box h3 { font-size: 22px; line-height: 1.3; margin-bottom: 8px; color:#12213A;}
.verdict-box p { color: #5A6B7E; font-size: 14.5px; }

.play-card {
    background: #FFFFFF; border: 1px solid #DDE2E0; border-radius: 4px;
    padding: 18px 20px; margin-bottom: 12px;
}
.play-rank { font-family: 'IBM Plex Mono', monospace; font-size: 10.5px; letter-spacing:.1em;
    color: #5A6B7E; text-transform: uppercase; }
.play-title { font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 17px; margin: 4px 0;}
.play-impact { font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: #2E7D6B; font-weight:600; margin-bottom:8px;}
.play-kv { font-size: 13px; color: #5A6B7E; }
.play-kv b { color: #12213A; }
.play-copy { background: #F1F4F2; border-left: 3px solid #2E7D6B; padding: 8px 12px;
    font-size: 12.5px; margin-top: 8px; border-radius: 0 3px 3px 0; }
.copy-tag { font-family:'IBM Plex Mono', monospace; font-size:10px; letter-spacing:.1em;
    text-transform:uppercase; color:#5A6B7E; display:block; margin-bottom:3px;}
.footer-note { text-align:center; font-family:'IBM Plex Mono', monospace; font-size:11px;
    color:#5A6B7E; letter-spacing:.06em; margin-top: 40px;}
</style>
""", unsafe_allow_html=True)

st.title("\U0001F4CA Funnel Diagnostics")
st.caption("Upload funnel metrics → get a diagnosed leak + ranked recommendations. "
           "Math is computed deterministically; AI (a 5-agent chain in n8n) only interprets the numbers.")

# ---------- Mode selection ----------
mode_label = st.radio(
    "What kind of data are you uploading?",
    ["Metrics snapshot (any funnel metric)", "Order-level data (D2C orders)"],
    help="Metrics snapshot: pre-aggregated numbers by date/segment/metric — works for any funnel stage. "
         "Order-level: raw order history — unlocks cohort retention heatmap and lifecycle diagnosis.",
)
mode = "metrics_snapshot" if mode_label.startswith("Metrics snapshot") else "order_level"

# ---------- Input ----------
col1, col2 = st.columns([3, 1])
with col1:
    if mode == "metrics_snapshot":
        uploaded = st.file_uploader("Upload metrics CSV", type=["csv"],
                                     help="Required columns: date, segment, metric_name, value. "
                                          "One row per date + segment + metric.")
    else:
        uploaded = st.file_uploader("Upload orders CSV", type=["csv"],
                                     help="Required columns: customer_id, order_date, order_value. "
                                          "Optional: category, discount_used, channel")
with col2:
    st.write("")
    st.write("")
    use_sample = st.button("▸ Load sample data", use_container_width=True)

context_note = st.text_input(
    "Context (optional)",
    placeholder="Any releases, campaigns, or changes this period?",
)

df_raw = None
if use_sample:
    df_raw = generate_snapshot_sample() if mode == "metrics_snapshot" else generate_sample()
    st.session_state["source"] = f"sample_{mode}"
elif uploaded is not None:
    try:
        df_raw = pd.read_csv(uploaded)
        st.session_state["source"] = f"upload_{mode}"
    except Exception as e:
        st.error(f"Couldn't read that file: {e}")

if df_raw is None:
    st.info("Upload a CSV or load sample data to run the diagnosis.")
    st.stop()

# ---------- Validate + compute (mode-specific) ----------
cohort_matrix = None
cliff = None
funnel_findings = None

try:
    if mode == "order_level":
        df = validate_and_load(df_raw)
        with st.spinner("Computing cohort retention..."):
            cohort_matrix = compute_cohort_matrix(df)
            stats = compute_core_stats(df)
            cliff = find_m1_m2_cliff(cohort_matrix)
        st.success(f"Loaded {stats['total_orders']:,} orders from {stats['total_customers']:,} customers "
                   f"({stats['date_range']['start']} to {stats['date_range']['end']})")
        stats_payload = {"core_stats": stats, "cliff": cliff}
    else:
        df = validate_and_load_snapshot(df_raw)
        with st.spinner("Computing period-over-period deltas..."):
            funnel_findings = compute_funnel_deltas(df)
        st.success(f"Loaded {len(df):,} metric readings across "
                   f"{df['segment'].nunique()} segment(s) and {df['metric_name'].nunique()} metric(s)")
        stats_payload = {"findings": funnel_findings}
except ValueError as e:
    st.error(str(e))
    st.stop()

# ---------- AI Diagnosis (n8n webhook) ----------
run_key = (mode, st.session_state.get("source"))
if "diagnosis" not in st.session_state or st.session_state.get("last_run_key") != run_key:
    if not os.environ.get("N8N_WEBHOOK_URL"):
        st.error("N8N_WEBHOOK_URL is not set. Add it as an environment variable to enable the AI "
                 "diagnosis (the numbers above still work without it).")
        st.session_state["diagnosis"] = None
        st.session_state["pdf_url"] = None
    else:
        with st.spinner("Running the diagnosis workflow..."):
            try:
                result = diagnose(mode, stats_payload, context_note)
                st.session_state["diagnosis"] = result.get("diagnosis")
                st.session_state["pdf_url"] = result.get("pdf_url")
                st.session_state["last_run_key"] = run_key
            except Exception as e:
                st.error(f"Diagnosis failed: {e}")
                st.session_state["diagnosis"] = None
                st.session_state["pdf_url"] = None

diag = st.session_state.get("diagnosis")
pdf_url = st.session_state.get("pdf_url")

# ---------- Verdict ----------
st.markdown("### 01 · Diagnosis")
if diag:
    st.markdown(f"""
    <div class="verdict-box">
        <h3>{diag.get('verdict_headline', '')}</h3>
        <p>{diag.get('diagnosis_detail', '')}</p>
    </div>
    """, unsafe_allow_html=True)

    b = diag.get("benchmark", {})
    if b:
        metric_label = b.get("metric_label", "Your value")
        bench_df = pd.DataFrame({
            "Metric": [metric_label, "Category benchmark", "Best-in-class"],
            "Value": [b.get("your_value", 0), b.get("category_benchmark", 0), b.get("best_in_class", 0)],
        })
        fig = go.Figure(go.Bar(
            x=bench_df["Value"], y=bench_df["Metric"], orientation="h",
            marker_color=["#12213A", "#9AA8B5", "#2E7D6B"],
            text=[f"{v}%" for v in bench_df["Value"]], textposition="outside",
        ))
        fig.update_layout(height=180, margin=dict(l=0, r=30, t=10, b=10),
                           xaxis=dict(range=[0, max(bench_df["Value"], default=1) * 1.3 or 1],
                                      showgrid=False, visible=False),
                           yaxis=dict(showgrid=False), plot_bgcolor="white", paper_bgcolor="white")
        st.plotly_chart(fig, use_container_width=True)
else:
    if mode == "order_level":
        st.info(f"Repeat rate: **{stats['repeat_rate_pct']}%** · "
                f"Median days to 2nd order: **{stats.get('median_days_to_second_order', 'n/a')}**")
    else:
        flagged_count = sum(1 for f in funnel_findings if f["flagged"])
        st.info(f"{flagged_count} flagged change(s) out of {len(funnel_findings)} computed · "
                f"add N8N_WEBHOOK_URL to generate the full diagnosis")

# ---------- Mode-specific detail view ----------
if mode == "order_level" and cohort_matrix is not None:
    st.markdown("### 02 · Cohort Retention")
    month_cols = [c for c in cohort_matrix.columns if c != "cohort_size"]
    heat_vals = cohort_matrix[month_cols].values
    fig2 = go.Figure(data=go.Heatmap(
        z=heat_vals, x=[f"M{c}" for c in month_cols], y=cohort_matrix.index,
        colorscale=[[0, "#EDF3F0"], [1, "#2E7D6B"]], showscale=False,
        text=[[f"{v:.0f}%" if pd.notna(v) else "" for v in row] for row in heat_vals],
        texttemplate="%{text}", hoverinfo="skip",
    ))
    fig2.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=10),
                        xaxis=dict(side="top"), plot_bgcolor="white", paper_bgcolor="white")
    st.plotly_chart(fig2, use_container_width=True)

    if cliff:
        st.caption(f"⚠️ Average M1 retention: **{cliff['avg_m1_pct']}%** → M2: **{cliff['avg_m2_pct']}%** "
                   f"— a **{cliff['avg_drop_pp']}pp drop** consistent across cohorts.")
elif mode == "metrics_snapshot" and funnel_findings is not None:
    st.markdown("### 02 · Flagged Changes")
    findings_df = pd.DataFrame(funnel_findings)
    if not findings_df.empty:
        display_df = findings_df[["segment", "metric", "period_from", "period_to",
                                   "value_from", "value_to", "pct_change", "flagged"]]
        st.dataframe(display_df, use_container_width=True, hide_index=True)
    else:
        st.caption("No period-over-period changes could be computed from this data.")

# ---------- Plays / Recommendations ----------
st.markdown("### 03 · Prescribed Plays")
if diag and diag.get("plays"):
    for play in diag["plays"]:
        st.markdown(f"""
        <div class="play-card">
            <div class="play-rank">{play.get('rank', '')}</div>
            <div class="play-title">{play.get('title', '')}</div>
            <div class="play-impact">{play.get('estimated_impact', '')}</div>
            <div class="play-kv"><b>Segment:</b> {play.get('segment', '')}<br>
            <b>Trigger:</b> {play.get('trigger', '')}<br>
            <b>Channel:</b> {play.get('channel', '')}</div>
            <div class="play-copy"><span class="copy-tag">Detail</span>{play.get('sample_message', '')}</div>
        </div>
        """, unsafe_allow_html=True)
else:
    st.caption("Add N8N_WEBHOOK_URL to generate prescribed plays.")

# ---------- PDF download ----------
if pdf_url:
    st.markdown("### 04 · Download Report")
    try:
        pdf_resp = requests.get(pdf_url, timeout=60)
        pdf_resp.raise_for_status()
        st.download_button(
            "⬇️ Download PDF report",
            data=pdf_resp.content,
            file_name="funnel-diagnostics-report.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    except Exception as e:
        st.warning(f"Couldn't fetch the generated PDF ({e}). Direct link: {pdf_url}")

st.markdown('<div class="footer-note">FUNNEL DIAGNOSTICS · DETERMINISTIC MATH · AI INTERPRETATION · NO RAW DATA LEAVES THE APP</div>',
            unsafe_allow_html=True)
