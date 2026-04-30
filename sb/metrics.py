"""
sb/metrics.py
-------------
Read/write operations for the sla_metrics Supabase table.
"""

from __future__ import annotations

import datetime
import logging

from sb.client import get_client

log   = logging.getLogger(__name__)
TABLE = "sla_metrics"


def push_metric_row(row: dict) -> dict | None:
    """
    Insert one scored telemetry row into sla_metrics.

    Expected keys:
        slice_id, cpu_pct, mem_mb, net_rx_kb, net_tx_kb,
        anomaly_score, is_anomaly (bool), confidence (float 0-100),
        attack_type (str | None), sampled_at (ISO-8601 string)
    """
    try:
        client = get_client()
        result = client.table(TABLE).insert(row).execute()
        log.info("[sb.metrics] Inserted row for slice=%s is_anomaly=%s",
                 row.get("slice_id"), row.get("is_anomaly"))
        return result.data[0] if result.data else None
    except Exception as exc:
        # Use WARNING so it always shows in docker logs at INFO level
        log.warning("[sb.metrics] push_metric_row FAILED: %s | row=%s", exc, row)
        return None


def fetch_recent_metrics(slice_id: str, limit: int = 500) -> list[dict]:
    """Fetch recent normal rows for a slice — used by retrainer."""
    try:
        result = (
            get_client()
            .table(TABLE)
            .select("cpu_pct, mem_mb, net_rx_kb, net_tx_kb, is_anomaly, sampled_at")
            .eq("slice_id", slice_id)
            .eq("is_anomaly", False)
            .order("sampled_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        log.warning("[sb.metrics] fetch_recent_metrics FAILED: %s", exc)
        return []


def fetch_all_recent_normal(limit: int = 2000) -> tuple[list[dict], list[str]]:
    """Fetch recent normal rows across all slices — used by global retrainer."""
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
        log.warning("[sb.metrics] fetch_all_recent_normal FAILED: %s", exc)
        return [], []


def fetch_sla_window(slice_id: str, window_hours: int) -> list[dict]:
    """Fetch rows for SLA compliance calculation over a rolling window."""
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
        log.warning("[sb.metrics] fetch_sla_window FAILED: %s", exc)
        return []