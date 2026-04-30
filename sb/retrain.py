"""
sb/retrain.py
-------------
Read/write operations for the model_retrain_log Supabase table.
"""

from __future__ import annotations

import logging

from sb.client import get_client

log   = logging.getLogger(__name__)
TABLE = "model_retrain_log"


def log_retrain(entry: dict) -> None:
    """
    Record a model retrain event.

    Expected keys:
        retrained_at (ISO-8601 str), reason (str),
        n_samples (int), slice_ids (JSON array string)
    """
    try:
        get_client().table(TABLE).insert(entry).execute()
        log.info("[sb.retrain] Retrain logged: reason=%s samples=%s",
                 entry.get("reason"), entry.get("n_samples"))
    except Exception as exc:
        log.warning("[sb.retrain] log_retrain FAILED: %s | entry=%s", exc, entry)


def fetch_retrain_log(limit: int = 10) -> list[dict]:
    """Return the most recent retrain log entries."""
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
        log.warning("[sb.retrain] fetch_retrain_log FAILED: %s", exc)
        return []