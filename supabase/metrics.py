"""
supabase/metrics.py
--------------------
All read/write operations for the `sla_metrics` table.

Table schema (see schema.sql):
    id, slice_id, cpu_pct, mem_mb, net_rx_kb, net_tx_kb,
    anomaly_score, is_anomaly, confidence, attack_type, sampled_at

Public functions:
    push_metric_row(row)                  → mirror one scored sample
    fetch_recent_metrics(slice_id, limit) → used by retrainer
    fetch_sla_window(slice_id, hours)     → used by SLA compliance calculator
"""

from __future__ import annotations

import datetime

from supabase.client import get_client

TABLE = "sla_metrics"


def push_metric_row(row: dict) -> dict | None:
    """
    Insert one telemetry + anomaly row into sla_metrics.

    Expected keys:
        slice_id, cpu_pct, mem_mb, net_rx_kb, net_tx_kb,
        anomaly_score, is_anomaly (bool), confidence (0-100),
        attack_type (str | None), sampled_at (ISO-8601 string)

    Returns the inserted row dict, or None on failure.
    Failures are printed but never raised — the scoring path must not block.
    """
    try:
        result = get_client().table(TABLE).insert(row).execute()
        return result.data[0] if result.data else None
    except Exception as exc:
        print(f"[supabase.metrics] push_metric_row failed: {exc}")
        return None


def fetch_recent_metrics(slice_id: str, limit: int = 500) -> list[dict]:
    """
    Pull the most recent rows for a given slice.
    Used by ml/train.py when retraining from Supabase data.
    Only non-anomalous rows are useful for teaching the model 'normal'.

    Returns a list of dicts with keys:
        cpu_pct, mem_mb, net_rx_kb, net_tx_kb, is_anomaly, sampled_at
    """
    try:
        result = (
            get_client()
            .table(TABLE)
            .select("cpu_pct, mem_mb, net_rx_kb, net_tx_kb, is_anomaly, sampled_at")
            .eq("slice_id", slice_id)
            .eq("is_anomaly", False)        # only normal samples for retraining
            .order("sampled_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        print(f"[supabase.metrics] fetch_recent_metrics failed: {exc}")
        return []


def fetch_all_recent_normal(limit: int = 2000) -> tuple[list[dict], list[str]]:
    """
    Fetch the most recent normal rows across ALL slices.
    Returns (rows, slice_id_list).
    Used by the global retrain path in ml/train.py.
    """
    try:
        result = (
            get_client()
            .table(TABLE)
            .select("slice_id, cpu_pct, mem_mb, net_rx_kb, net_tx_kb")
            .eq("is_anomaly", False)
            .order("sampled_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows      = result.data or []
        slice_ids = list({r["slice_id"] for r in rows})
        return rows, slice_ids
    except Exception as exc:
        print(f"[supabase.metrics] fetch_all_recent_normal failed: {exc}")
        return [], []


def fetch_sla_window(slice_id: str, window_hours: int) -> list[dict]:
    """
    Return all rows for a slice within the last `window_hours` hours.
    Used by the SLA compliance calculator in api/main.py.

    Returns a list of dicts with keys: is_anomaly, sampled_at
    """
    cutoff = (
        datetime.datetime.utcnow() - datetime.timedelta(hours=window_hours)
    ).isoformat()

    try:
        result = (
            get_client()
            .table(TABLE)
            .select("is_anomaly, sampled_at")
            .eq("slice_id", slice_id)
            .gte("sampled_at", cutoff)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        print(f"[supabase.metrics] fetch_sla_window failed: {exc}")
        return []