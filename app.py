import os
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go

from analytics import (
    validate_and_load, compute_cohort_matrix, compute_core_stats, find_m1_m2_cliff,
    compute_orders_trend,
    validate_and_load_snapshot, compute_funnel_deltas, compute_metric_series,
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

.section-label { font-size: 11px; letter-spacing: .06em; text-transform: uppercase;
    color: #5A6B7E; margin: 28px 0 4px 0; }
.section-sub { font-size: 12px; color: #5A6B7E; margin-bottom: 10px; }

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

.flag-card { background: #F7F8F7; border-radius: 4px; padding: 10px 14px; margin-bottom: 8px;
    display: flex; justify-content: space-between; align-items: center; }
.flag-title { font-size: 13px; font-weight: 600; color: #12213A; }
.flag-period { font-size: 11px; color: #8A94A0; }
.flag-value { font-size: 15px; font-weight: 700; }

.footer-note { text-align:center; font-family:'IBM Plex Mono', monospace; font-size:11px;
    color:#5A6B7E; letter-spacing:.06em; margin-top: 40px;}
</style>
""", unsafe_allow_html=True)

CHART_COLORS = {
    "blue": "#2a78d6", "green": "#1baf7a", "gray": "#c3c2b7",
    "red": "#d03b3b", "amber": "#eda100", "grid": "#e1e0d9", "muted": "#898781",
}

PLOTLY_LAYOUT = dict(
    plot_bgcolor="white", paper_bgcolor="white",
    font=dict(color="#52514e", size=12),
    margin=dict(l=0, r=10, t=10, b=10),
)


def section(label, sub=None):
    st.markdown(f'<div class="section-label">{label}</div>', unsafe_allow_html=True)
    if sub:
        st.markdown(f'<div class="section-sub">{sub}</div>', unsafe_allow_html=True)


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
    _sample_preview = generate_snapshot_sample() if mode == "metrics_snapshot" else generate_sample()
    st.download_button(
        "⬇ Download sample CSV",
        data=_sample_preview.to_csv(index=False).encode("utf-8"),
        file_name=f"sample_{mode}.csv",
        mime="text/csv",
        use_container_width=True,
    )

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
orders_trend = None
metric_series = None

try:
    if mode == "order_level":
        df = validate_and_load(df_raw)
        with st.spinner("Computing cohort retention..."):
            cohort_matrix = compute_cohort_matrix(df)
            stats = compute_core_stats(df)
            cliff = find_m1_m2_cliff(cohort_matrix)
            orders_trend = compute_orders_trend(df)
        stats_payload = {"core_stats": stats, "cliff": cliff}
    else:
        df = validate_and_load_snapshot(df_raw)
        with st.spinner("Computing period-over-period deltas..."):
            funnel_findings = compute_funnel_deltas(df)
            metric_series = compute_metric_series(df)
        stats_payload = {"findings": funnel_findings}
except ValueError as e:
    st.error(str(e))
    st.stop()

# ---------- 01 · Your data ----------
section("01 · Your data")

if mode == "order_level":
    kc = st.columns(5)
    kc[0].metric("Total orders", f"{stats['total_orders']:,}")
    kc[1].metric("Customers", f"{stats['total_customers']:,}")
    kc[2].metric("Repeat rate", f"{stats['repeat_rate_pct']}%")
    kc[3].metric("Avg order value", f"{stats['avg_order_value']:,.0f}")
    kc[4].metric("Date range", f"{stats['date_range']['start']} → {stats['date_range']['end']}")

    if orders_trend and len(orders_trend["months"]) > 1:
        st.markdown("**Orders over time**")
        st.caption("New customers vs. repeat orders, by month")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=orders_trend["months"], y=orders_trend["new_customers"],
                                  name="New customers", mode="lines+markers",
                                  line=dict(color=CHART_COLORS["blue"], width=2),
                                  fill="tozeroy", fillcolor="rgba(42,120,214,0.08)"))
        fig.add_trace(go.Scatter(x=orders_trend["months"], y=orders_trend["repeat_orders"],
                                  name="Repeat orders", mode="lines+markers",
                                  line=dict(color=CHART_COLORS["green"], width=2),
                                  fill="tozeroy", fillcolor="rgba(27,175,122,0.08)"))
        fig.update_layout(**PLOTLY_LAYOUT, height=240,
                           legend=dict(orientation="h", y=1.15, x=0),
                           xaxis=dict(gridcolor=CHART_COLORS["grid"]),
                           yaxis=dict(gridcolor=CHART_COLORS["grid"]))
        st.plotly_chart(fig, use_container_width=True)

    dc1, dc2 = st.columns(2)
    with dc1:
        if stats.get("channel_repeat_rate"):
            st.markdown("**Repeat rate by channel**")
            st.caption("Where loyal customers actually come from")
            ch = stats["channel_repeat_rate"]
            channels = sorted(ch, key=ch.get, reverse=True)
            fig = go.Figure(go.Bar(
                x=[ch[c] for c in channels], y=channels, orientation="h",
                marker_color=CHART_COLORS["blue"],
                text=[f"{ch[c]}%" for c in channels], textposition="outside",
            ))
            fig.update_layout(**PLOTLY_LAYOUT, height=200,
                               xaxis=dict(visible=False), yaxis=dict(gridcolor=CHART_COLORS["grid"]))
            st.plotly_chart(fig, use_container_width=True)
    with dc2:
        if stats.get("discount_dependency"):
            st.markdown("**Discount dependency**")
            st.caption("Repeat rate by first-order type")
            dd = stats["discount_dependency"]
            labels = ["Discounted first order", "Full-price first order"]
            values = [dd["discounted_first_order_repeat_pct"], dd["full_price_first_order_repeat_pct"]]
            fig = go.Figure(go.Bar(
                x=labels, y=values, marker_color=[CHART_COLORS["red"], CHART_COLORS["green"]],
                text=[f"{v}%" for v in values], textposition="outside",
            ))
            fig.update_layout(**PLOTLY_LAYOUT, height=200,
                               yaxis=dict(visible=False), xaxis=dict(gridcolor=CHART_COLORS["grid"]))
            st.plotly_chart(fig, use_container_width=True)

    if stats.get("category_mix"):
        st.markdown("**Category mix: loyalists vs. one-time buyers**")
        st.caption("What each group actually orders")
        cm = stats["category_mix"]
        loyal, onetime = cm["top_categories_loyalists"], cm["top_categories_one_timers"]
        categories = list(dict.fromkeys(list(loyal.keys()) + list(onetime.keys())))
        fig = go.Figure()
        fig.add_trace(go.Bar(name="Loyalists", x=categories,
                              y=[loyal.get(c, 0) for c in categories], marker_color=CHART_COLORS["blue"]))
        fig.add_trace(go.Bar(name="One-time buyers", x=categories,
                              y=[onetime.get(c, 0) for c in categories], marker_color=CHART_COLORS["gray"]))
        fig.update_layout(**PLOTLY_LAYOUT, height=240, barmode="group",
                           legend=dict(orientation="h", y=1.15, x=0),
                           yaxis=dict(gridcolor=CHART_COLORS["grid"]))
        st.plotly_chart(fig, use_container_width=True)

    # ---------- 02 · Cohort retention ----------
    section("02 · Cohort retention", "% of each monthly cohort still ordering, by month since acquisition")
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

    diagnosis_section_label = "03 · AI diagnosis"

else:  # metrics_snapshot
    flagged_count = sum(1 for f in funnel_findings if f["flagged"])
    kc = st.columns(5)
    kc[0].metric("Metrics tracked", df["metric_name"].nunique())
    kc[1].metric("Segments", df["segment"].nunique())
    kc[2].metric("Readings loaded", len(df))
    kc[3].metric("Date range", f"{df['date'].min().date()} → {df['date'].max().date()}")
    kc[4].metric("Flagged changes", flagged_count)

    if metric_series:
        st.markdown("**Trends by metric**")
        st.caption("Every segment overlaid, one mini chart per metric")
        metrics = list(metric_series.keys())
        cols = st.columns(min(3, len(metrics)) or 1)
        palette = [CHART_COLORS["blue"], CHART_COLORS["gray"], CHART_COLORS["amber"], CHART_COLORS["green"]]
        for i, metric in enumerate(metrics):
            with cols[i % len(cols)]:
                st.markdown(f"<div style='font-size:13px;font-weight:600;margin-bottom:4px'>{metric}</div>",
                             unsafe_allow_html=True)
                data = metric_series[metric]
                fig = go.Figure()
                for j, (seg, values) in enumerate(data["segments"].items()):
                    fig.add_trace(go.Scatter(x=data["dates"], y=values, name=seg, mode="lines+markers",
                                              line=dict(color=palette[j % len(palette)], width=2),
                                              marker=dict(size=5)))
                fig.update_layout(**PLOTLY_LAYOUT, height=160, showlegend=False,
                                   xaxis=dict(gridcolor=CHART_COLORS["grid"], tickfont=dict(size=9)),
                                   yaxis=dict(gridcolor=CHART_COLORS["grid"], tickfont=dict(size=9)))
                st.plotly_chart(fig, use_container_width=True)
        legend_html = " &nbsp;&nbsp; ".join(
            f'<span style="color:{palette[j % len(palette)]}">●</span> {seg}'
            for j, seg in enumerate(next(iter(metric_series.values()))["segments"].keys())
        )
        st.markdown(f"<div style='font-size:12px;color:#5A6B7E;margin-top:4px'>{legend_html}</div>",
                     unsafe_allow_html=True)

    if funnel_findings:
        st.markdown("**Flagged changes**")
        fc = st.columns(2)
        for i, f in enumerate(funnel_findings):
            direction = "▲" if f["pct_change"] > 0 else "▼"
            color = CHART_COLORS["green"] if f["pct_change"] > 0 else CHART_COLORS["red"]
            with fc[i % 2]:
                st.markdown(f"""
                <div class="flag-card">
                    <div>
                        <div class="flag-title">{f['segment']} · {f['metric']}</div>
                        <div class="flag-period">{f['period_from']} → {f['period_to']}</div>
                    </div>
                    <div class="flag-value" style="color:{color}">{direction} {abs(f['pct_change'])}%</div>
                </div>
                """, unsafe_allow_html=True)

    diagnosis_section_label = "02 · AI diagnosis"

# ---------- AI Diagnosis (n8n webhook) ----------
run_key = (mode, st.session_state.get("source"))
if "diagnosis" not in st.session_state or st.session_state.get("last_run_key") != run_key:
    if not os.environ.get("N8N_WEBHOOK_URL"):
        st.session_state["diagnosis"] = None
        st.session_state["pdf_url"] = None
        st.session_state["diagnosis_error"] = (
            "N8N_WEBHOOK_URL is not set. Add it as an environment variable to enable the AI diagnosis "
            "(the data above still works without it)."
        )
    else:
        with st.spinner("Running the diagnosis workflow..."):
            try:
                result = diagnose(mode, stats_payload, context_note)
                st.session_state["diagnosis"] = result.get("diagnosis")
                st.session_state["pdf_url"] = result.get("pdf_url")
                st.session_state["last_run_key"] = run_key
                st.session_state["diagnosis_error"] = None
            except Exception as e:
                st.session_state["diagnosis"] = None
                st.session_state["pdf_url"] = None
                st.session_state["diagnosis_error"] = f"Diagnosis failed: {e}"

diag = st.session_state.get("diagnosis")
pdf_url = st.session_state.get("pdf_url")
diagnosis_error = st.session_state.get("diagnosis_error")

# ---------- Diagnosis + Plays ----------
section(diagnosis_section_label)

if diagnosis_error:
    st.error(diagnosis_error)

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

    if diag.get("plays"):
        st.markdown("**Prescribed plays**" if mode == "order_level" else "**Recommended experiments**")
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

# ---------- PDF download ----------
if pdf_url:
    st.markdown("**Download report**")
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
