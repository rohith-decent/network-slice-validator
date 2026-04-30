"""
sb/client.py
------------
Manages the single Supabase connection for the entire project.

IMPORTANT: SUPABASE_URL and SUPABASE_KEY are read inside get_client()
on every cold-start call — NOT at module import time. This ensures the
env vars are available when the function runs inside Docker, even if the
module was imported early during startup.

Set in your .env file (docker-compose picks this up automatically):
    SUPABASE_URL=https://your-project.supabase.co
    SUPABASE_KEY=your-service-role-key
"""

import os
import logging

from supabase import create_client, Client  # supabase-py package

log = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    """
    Return a cached Supabase client.
    Reads SUPABASE_URL / SUPABASE_KEY from os.environ on every cold start
    so Docker env vars are always picked up correctly.
    Raises EnvironmentError if either var is missing.
    """
    global _client
    if _client is None:
        # Read here — NOT at module level — so Docker env vars are visible
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_KEY", "").strip()

        if not url or not key:
            raise EnvironmentError(
                "SUPABASE_URL and SUPABASE_KEY must be set. "
                f"Got URL={'<set>' if url else '<MISSING>'} "
                f"KEY={'<set>' if key else '<MISSING>'}"
            )

        log.info("[sb.client] Connecting to Supabase: %s", url)
        _client = create_client(url, key)
        log.info("[sb.client] Supabase client ready.")

    return _client


def reset_client() -> None:
    """Force a new connection on next get_client() call (useful after env changes)."""
    global _client
    _client = None