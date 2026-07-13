"""Sample dataset generators for both input modes, so the app is demo-able
with zero upload.

- generate() -> order-level D2C orders with a deliberate M1->M2 retention
  cliff and discount-dependency pattern.
- generate_snapshot() -> a tidy funnel-metrics snapshot with a deliberate
  mobile activation-rate decline across three weekly periods.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

CATEGORIES = ["Thali Combos", "Snacks & Sides", "Desserts", "Beverages", "Family Packs"]
CHANNELS = ["WhatsApp", "App", "Swiggy", "Zomato"]


def generate(seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    start = datetime(2026, 1, 1)
    cust_id = 1

    for month in range(6):  # Jan..Jun
        cohort_start = start + timedelta(days=30 * month)
        n_new = rng.integers(400, 650)

        for _ in range(n_new):
            cid = f"C{cust_id:05d}"
            cust_id += 1
            first_discounted = rng.random() < 0.55
            first_date = cohort_start + timedelta(days=int(rng.integers(0, 28)))
            rows.append(_row(cid, first_date, rng, first_discounted))

            base_repeat_p = 0.19 if first_discounted else 0.39
            recency_bonus = month * 0.012
            p_second = min(0.6, base_repeat_p + recency_bonus)

            n_orders = 1
            last_date = first_date
            while rng.random() < (p_second if n_orders == 1 else 0.55) and n_orders < 6:
                gap_days = int(rng.integers(30, 55)) if n_orders == 1 else int(rng.integers(18, 35))
                last_date = last_date + timedelta(days=gap_days)
                if last_date > datetime(2026, 6, 30):
                    break
                rows.append(_row(cid, last_date, rng, rng.random() < 0.15))
                n_orders += 1

    df = pd.DataFrame(rows)
    return df.sort_values(["customer_id", "order_date"]).reset_index(drop=True)


def _row(cid, date, rng, discounted):
    return {
        "customer_id": cid,
        "order_date": date.strftime("%Y-%m-%d"),
        "order_value": round(float(rng.normal(420, 90)), 2),
        "category": rng.choice(CATEGORIES),
        "discount_used": discounted,
        "channel": rng.choice(CHANNELS, p=[0.35, 0.25, 0.25, 0.15]),
    }


METRICS = ["activation_rate", "day7_retention", "signup_to_paid"]
SEGMENTS = ["mobile", "web"]


def generate_snapshot(seed: int = 7) -> pd.DataFrame:
    """Three weekly snapshots, two segments, three funnel metrics. Mobile
    activation rate declines ~12% then ~16% week over week — a real anomaly
    for the diagnosis to find. Everything else drifts within normal noise."""
    rng = np.random.default_rng(seed)
    rows = []
    base_date = datetime(2026, 6, 1)

    base_values = {
        ("mobile", "activation_rate"): 0.42,
        ("web", "activation_rate"): 0.51,
        ("mobile", "day7_retention"): 0.28,
        ("web", "day7_retention"): 0.33,
        ("mobile", "signup_to_paid"): 0.06,
        ("web", "signup_to_paid"): 0.09,
    }

    for week in range(3):
        date = (base_date + timedelta(days=7 * week)).strftime("%Y-%m-%d")
        for (segment, metric), base in base_values.items():
            if metric == "activation_rate" and segment == "mobile":
                # deliberate decline: base, -12%, then another -16%
                value = base if week == 0 else base * (0.88 if week == 1 else 0.88 * 0.84)
            else:
                noise = rng.normal(0, 0.015)
                value = max(0.01, base + noise)
            rows.append({
                "date": date,
                "segment": segment,
                "metric_name": metric,
                "value": round(float(value), 4),
            })

    return pd.DataFrame(rows)
