"""
supabase/incidents.py
---------------------
All read/write operations for the `incidents` table.

Table schema (see schema.sql):
    id, slice_id, attack_type, started_at, resolved_at,
    peak_score, min_confidence, duration_s, is_active

Public functions:
    open_incident(record)                  → insert a new active incident
    close_incident(id, resolved_at, dur)   → mark incident as resolved
    fetch_active_incident(slice_id)        → get the open incident for a slice
    fetch_incidents(limit)                 → recent incidents across all slices
"""

from __future__ import annotations

from supabase.client import get_client

TABLE = "incidents"


def open_incident(record: dict) -> dict | None:
    """
    Insert a new active incident row.

    Expected keys:
        slice_id (str), attack_type (str | None),
        started_at (ISO-8601 str), peak_score (float),
        min_confidence (float), is_active (bool = True)

    Returns the inserted row dict, or None on failure.
    """
    try:
        result = get_client().table(TABLE).insert(record).execute()
        return result.data[0] if result.data else None
    except Exception as exc:
        print(f"[supabase.incidents] open_incident failed: {exc}")
        return None


def close_incident(incident_id: int, resolved_at: str, duration_s: float) -> None:
    """
    Mark an existing incident as resolved.

    Args:
        incident_id : Supabase row id returned when the incident was opened
        resolved_at : ISO-8601 timestamp string
        duration_s  : total incident duration in seconds
    """
    try:
        get_client().table(TABLE).update(
            {
                "resolved_at": resolved_at,
                "duration_s":  duration_s,
                "is_active":   False,
            }
        ).eq("id", incident_id).execute()
    except Exception as exc:
        print(f"[supabase.incidents] close_incident failed: {exc}")


def fetch_active_incident(slice_id: str) -> dict | None:
    """
    Return the currently open incident for a slice, or None if none exists.
    Used by the correlator to decide whether to open a new incident or
    close the existing one.
    """
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
        print(f"[supabase.incidents] fetch_active_incident failed: {exc}")
        return None


def fetch_incidents(limit: int = 50) -> list[dict]:
    """
    Return the most recent incidents across all slices.
    Used by GET /incidents and GET /incidents/export.
    """
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
        print(f"[supabase.incidents] fetch_incidents failed: {exc}")
        return []