"""
Funnel Diagnostics — analytics engine.

All numbers are computed here with plain pandas/numpy. Nothing in this file
calls an LLM. The AI layer (ai_diagnosis.py) only ever receives the *outputs*
of this module — small aggregate stats, never raw rows.

Two input modes are supported:

1. "order_level" — raw D2C order history (customer_id, order_date, order_value).
   Unlocks cohort retention heatmap, M1->M2 cliff detection, discount
   dependency, and channel repeat-rate analysis.

2. "metrics_snapshot" — any pre-aggregated funnel metric, in long/tidy format
   (date, segment, metric_name, value). Works for activation, conversion,
   retention, or any other funnel metric you track, broken out by segment
   and time period. Computes period-over-period deltas and flags anomalies.
"""

import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Mode 1: order-level data (D2C orders -> cohort/retention analysis)
# ---------------------------------------------------------------------------

REQUIRED_COLS = ["customer_id", "order_date", "order_value"]
OPTIONAL_COLS = ["category", "discount_used", "channel"]


def validate_and_load(df: pd.DataFrame) -> pd.DataFrame:
    """Validate required columns exist and coerce types. Raises ValueError with
    a user-facing message if something required is missing or unparseable."""
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required column(s): {', '.join(missing)}. "
            f"Your file needs: {', '.join(REQUIRED_COLS)}"
        )

    df = df.copy()
    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    if df["order_date"].isna().all():
        raise ValueError(
            "Couldn't parse any dates in 'order_date'. Use a format like "
            "YYYY-MM-DD or DD/MM/YYYY."
        )
    df = df.dropna(subset=["order_date"])

    df["order_value"] = pd.to_numeric(df["order_value"], errors="coerce")
    df = df.dropna(subset=["order_value"])

    if "discount_used" in df.columns:
        df["discount_used"] = df["discount_used"].astype(str).str.strip().str.lower().isin(
            ["1", "true", "yes", "y"]
        )

    df = df.sort_values(["customer_id", "order_date"]).reset_index(drop=True)
    return df


def compute_cohort_matrix(df: pd.DataFrame, max_months: int = 6) -> pd.DataFrame:
    """Standard month-cohort retention matrix. Rows = acquisition month,
    columns = M0..Mn, values = % of that cohort's customers who ordered again
    in that relative month."""
    df = df.copy()
    df["order_month"] = df["order_date"].dt.to_period("M")

    first_order_month = df.groupby("customer_id")["order_month"].min()
    df["cohort_month"] = df["customer_id"].map(first_order_month)
    df["month_index"] = (
        (df["order_month"].dt.year - df["cohort_month"].dt.year) * 12
        + (df["order_month"].dt.month - df["cohort_month"].dt.month)
    )

    cohort_sizes = first_order_month.value_counts().sort_index()

    pivot = (
        df.groupby(["cohort_month", "month_index"])["customer_id"]
        .nunique()
        .reset_index()
        .pivot(index="cohort_month", columns="month_index", values="customer_id")
    )

    pivot = pivot.reindex(sorted(pivot.index))
    max_col = min(max_months - 1, pivot.columns.max() if len(pivot.columns) else 0)
    pivot = pivot.loc[:, [c for c in pivot.columns if c <= max_col]]

    retention_pct = pivot.divide(cohort_sizes.reindex(pivot.index), axis=0) * 100

    result = retention_pct.copy()
    result.insert(0, "cohort_size", cohort_sizes.reindex(pivot.index).values)
    result.index = result.index.astype(str)
    return result.round(1)


def compute_core_stats(df: pd.DataFrame) -> dict:
    """The headline numbers that drive the diagnosis."""
    order_counts = df.groupby("customer_id").size()
    total_customers = len(order_counts)
    repeaters = (order_counts >= 2).sum()
    repeat_rate = round(repeaters / total_customers * 100, 1) if total_customers else 0.0

    dates_by_cust = df.groupby("customer_id")["order_date"].apply(list)
    gaps = []
    for dates in dates_by_cust:
        if len(dates) >= 2:
            d = sorted(dates)
            gaps.append((d[1] - d[0]).days)
    median_time_to_2nd = float(np.median(gaps)) if gaps else None

    stats = {
        "total_customers": int(total_customers),
        "total_orders": int(len(df)),
        "repeat_rate_pct": repeat_rate,
        "one_time_customers_pct": round(100 - repeat_rate, 1),
        "median_days_to_second_order": median_time_to_2nd,
        "avg_order_value": round(float(df["order_value"].mean()), 2),
        "date_range": {
            "start": str(df["order_date"].min().date()),
            "end": str(df["order_date"].max().date()),
        },
    }

    if "discount_used" in df.columns:
        cust_discount = df.groupby("customer_id")["discount_used"].first()
        cust_repeat = order_counts >= 2
        joined = pd.DataFrame({"first_discounted": cust_discount, "repeated": cust_repeat})
        if joined["first_discounted"].any() and (~joined["first_discounted"]).any():
            disc_repeat = joined[joined["first_discounted"]]["repeated"].mean() * 100
            full_repeat = joined[~joined["first_discounted"]]["repeated"].mean() * 100
            stats["discount_dependency"] = {
                "discounted_first_order_repeat_pct": round(disc_repeat, 1),
                "full_price_first_order_repeat_pct": round(full_repeat, 1),
            }

    if "category" in df.columns:
        loyalists = order_counts[order_counts >= 3].index
        one_timers = order_counts[order_counts == 1].index
        loyalist_cats = (
            df[df["customer_id"].isin(loyalists)]["category"].value_counts(normalize=True).head(3) * 100
        ).round(1).to_dict()
        one_timer_cats = (
            df[df["customer_id"].isin(one_timers)]["category"].value_counts(normalize=True).head(3) * 100
        ).round(1).to_dict()
        stats["category_mix"] = {
            "top_categories_loyalists": loyalist_cats,
            "top_categories_one_timers": one_timer_cats,
        }

    if "channel" in df.columns:
        stats["channel_repeat_rate"] = (
            df.groupby("channel")["customer_id"]
            .apply(lambda ids: order_counts.reindex(ids.unique()).ge(2).mean() * 100)
            .round(1)
            .to_dict()
        )

    return stats


def find_m1_m2_cliff(cohort_matrix: pd.DataFrame) -> dict | None:
    """Detect whether there's a consistent steep drop between month 1 and
    month 2 retention across cohorts — a common, specific, quotable pattern."""
    if 1 not in cohort_matrix.columns or 2 not in cohort_matrix.columns:
        return None
    valid = cohort_matrix.dropna(subset=[1, 2])
    if valid.empty:
        return None
    drop = (valid[1] - valid[2]).mean()
    return {
        "avg_m1_pct": round(valid[1].mean(), 1),
        "avg_m2_pct": round(valid[2].mean(), 1),
        "avg_drop_pp": round(drop, 1),
    }


# ---------------------------------------------------------------------------
# Mode 2: metrics snapshot (any funnel metric -> delta/anomaly analysis)
# ---------------------------------------------------------------------------

SNAPSHOT_REQUIRED_COLS = ["date", "segment", "metric_name", "value"]


def validate_and_load_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """Validate the tidy metrics-snapshot schema and coerce types."""
    missing = [c for c in SNAPSHOT_REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required column(s): {', '.join(missing)}. "
            f"Your file needs: {', '.join(SNAPSHOT_REQUIRED_COLS)}"
        )

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if df["date"].isna().all():
        raise ValueError(
            "Couldn't parse any dates in 'date'. Use a format like YYYY-MM-DD."
        )
    df = df.dropna(subset=["date"])

    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])

    df["segment"] = df["segment"].astype(str).str.strip()
    df["metric_name"] = df["metric_name"].astype(str).str.strip()

    return df.sort_values(["segment", "metric_name", "date"]).reset_index(drop=True)


def compute_funnel_deltas(df: pd.DataFrame, flag_threshold_pct: float = 10.0) -> list[dict]:
    """Group by segment + metric, compute period-over-period % change, and
    flag moves at or beyond the threshold. Falls back to the largest moves
    if nothing crosses the threshold, so there's always something to diagnose."""
    findings = []

    for (segment, metric), group in df.groupby(["segment", "metric_name"]):
        series = group.sort_values("date")
        dates = series["date"].dt.strftime("%Y-%m-%d").tolist()
        values = series["value"].tolist()

        for i in range(1, len(values)):
            prev, curr = values[i - 1], values[i]
            if prev == 0:
                continue
            pct_change = (curr - prev) / abs(prev) * 100
            findings.append({
                "segment": segment,
                "metric": metric,
                "period_from": dates[i - 1],
                "period_to": dates[i],
                "value_from": prev,
                "value_to": curr,
                "pct_change": round(pct_change, 1),
                "flagged": abs(pct_change) >= flag_threshold_pct,
            })

    flagged = [f for f in findings if f["flagged"]]
    if flagged:
        return sorted(flagged, key=lambda f: abs(f["pct_change"]), reverse=True)

    return sorted(findings, key=lambda f: abs(f["pct_change"]), reverse=True)[:5]
