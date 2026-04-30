"""
sb/
---
Self-contained Supabase integration module.
Named 'sb/' (not 'supabase/') to avoid clashing with the supabase-py pip package.

Public imports:
    from sb.client    import get_client
    from sb.metrics   import push_metric_row, fetch_recent_metrics, fetch_sla_window
    from sb.incidents import open_incident, close_incident, fetch_active_incident, fetch_incidents
    from sb.retrain   import log_retrain, fetch_retrain_log
"""

from sb.client import get_client  # noqa: F401