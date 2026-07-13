"""
AI interpretation layer.

Sends pre-computed stats (never raw rows) to the Funnel Diagnostics n8n
workflow's webhook. n8n runs a 5-agent reasoning chain (Groq-hosted models)
and returns a structured diagnosis plus a downloadable PDF link.

This module has zero LLM/API-key logic of its own on purpose — all AI
orchestration lives in the n8n workflow, not in this app. This file is just
the HTTP boundary between them.
"""

import os
import requests

REQUEST_TIMEOUT_SECONDS = 120


def get_webhook_url() -> str:
    url = os.environ.get("N8N_WEBHOOK_URL")
    if not url:
        raise RuntimeError(
            "N8N_WEBHOOK_URL is not set. Add it as an environment variable "
            "pointing at your n8n workflow's webhook URL (Settings/Environment "
            "locally via .env, or in Render's Environment tab)."
        )
    return url


def diagnose(mode: str, stats: dict, context: str = "") -> dict:
    """
    Args:
        mode: "order_level" or "metrics_snapshot"
        stats: the computed stats dict for that mode
            - order_level: {"core_stats": {...}, "cliff": {...} | None}
            - metrics_snapshot: {"findings": [...]}
        context: optional free-text note from the user (releases, campaigns, etc.)

    Returns:
        {"diagnosis": {verdict_headline, diagnosis_detail, benchmark, plays},
         "pdf_url": "https://..." | None}
    """
    url = get_webhook_url()
    payload = {"mode": mode, "stats": stats, "context": context or ""}

    try:
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(
            f"Couldn't reach the diagnosis workflow at {url}: {e}"
        )

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(
            f"Workflow returned a non-JSON response: {resp.text[:500]}"
        )

    if "diagnosis" not in data:
        raise RuntimeError(
            f"Workflow response is missing the 'diagnosis' field: {data}"
        )

    return data
