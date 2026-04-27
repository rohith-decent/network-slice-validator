"""
supabase/client.py
------------------
Manages the single Supabase connection for the entire project.

Configuration — set these as environment variables or in a .env file:
    SUPABASE_URL   https://your-project.supabase.co
    SUPABASE_KEY   your-anon-or-service-role-key

Nothing outside this file should call create_client() directly.
All other modules in this package obtain the client via get_client().
"""

import os

from supabase import create_client, Client  # supabase-py

SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY: str = os.environ.get("SUPABASE_KEY", "")

_client: Client | None = None


def get_client() -> Client:
    """
    Return a cached, reusable Supabase client.
    Raises EnvironmentError if the env vars are not set.
    """
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise EnvironmentError(
                "SUPABASE_URL and SUPABASE_KEY must be set as environment variables. "
                "Add them to your .env file or docker-compose.yml environment section."
            )
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client