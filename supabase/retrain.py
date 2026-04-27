"""
supabase/retrain.py
--------------------
All read/write operations for the `model_retrain_log` table.

Table schema (see schema.sql):
    id, retrained_at, reason, n_samples, slice_ids

Public functions:
    log_retrain(entry)      → insert one retrain event record
    fetch_retrain_log(n)    → return the most recent N retrain records
"""

from __future__ import annotations

from supabase.client import get_client

TABLE = "model_retrain_log"


def log_retrain(entry: dict) -> None:
    """
    Record a model retrain event in Supabase.

    Expected keys:
        retrained_at (ISO-8601 str)  — when the retrain ran
        reason (str)                 — 'startup' | 'drift_detected' | 'manual'
        n_samples (int)              — how many rows the model was trained on
        slice_ids (str)              — JSON array string, e.g. '["slice-a","slice-b"]'

    Failures are printed but never raised — the training path must not block.
    """
    try:
        get_client().table(TABLE).insert(entry).execute()
    except Exception as exc:
        print(f"[supabase.retrain] log_retrain failed: {exc}")


def fetch_retrain_log(limit: int = 10) -> list[dict]:
    """
    Return the most recent retrain log entries.
    Used by GET /model/status to show the last retrain reason and timestamp.
    """
    try:
        result = (
            get_client()
            .table(TABLE)
            .select("*")
            .order("retrained_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        print(f"[supabase.retrain] fetch_retrain_log failed: {exc}")
        return []