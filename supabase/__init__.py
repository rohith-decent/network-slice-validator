"""
supabase/
---------
Self-contained Supabase integration module.

Import surface (everything else is internal):

    from supabase import get_client
    from supabase.metrics  import push_metric_row, fetch_recent_metrics, fetch_sla_window
    from supabase.incidents import open_incident, close_incident, fetch_active_incident, fetch_incidents
    from supabase.retrain  import log_retrain, fetch_retrain_log

The rest of the codebase only ever imports from this package —
it never reaches into supabase/client.py directly.
"""

from supabase.client import get_client  # noqa: F401