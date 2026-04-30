"""
sb/incidents.py
---------------
Read/write operations for the incidents Supabase table.
"""

from __future__ import annotations

import logging

from sb.client import get_client

log   = logging.getLogger(__name__)
TABLE = "incidents"


def open_incident(record: dict) -> dict | None:
    """Insert a new active incident row."""
    try:
        result = get_client().table(TABLE).insert(record).execute()
        log.info("[sb.incidents] Opened incident for slice=%s type=%s",
                 record.get("slice_id"), record.get("attack_type"))
        return result.data[0] if result.data else None
    except Exception as exc:
        log.warning("[sb.incidents] open_incident FAILED: %s | record=%s", exc, record)
        return None


def close_incident(incident_id: int, resolved_at: str, duration_s: float) -> None:
    """Mark an incident as resolved."""
    try:
        get_client().table(TABLE).update(
            {"resolved_at": resolved_at, "duration_s": duration_s, "is_active": False}
        ).eq("id", incident_id).execute()
        log.info("[sb.incidents] Closed incident id=%s duration=%.1fs",
                 incident_id, duration_s)
    except Exception as exc:
        log.warning("[sb.incidents] close_incident FAILED: %s", exc)


def fetch_active_incident(slice_id: str) -> dict | None:
    """Return the currently open incident for a slice, or None."""
    try:
        result = (
            get_client()
            .table(TABLE)
            .select("*")
            .eq("slice_id", slice_id)
            .eq("is_active", True)
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as exc:
        log.warning("[sb.incidents] fetch_active_incident FAILED: %s", exc)
        return None


def fetch_incidents(limit: int = 50) -> list[dict]:
    """Return the most recent incidents across all slices."""
    try:
        result = (
            get_client()
            .table(TABLE)
            .select("*")
            .order("started_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        log.warning("[sb.incidents] fetch_incidents FAILED: %s", exc)
        return []