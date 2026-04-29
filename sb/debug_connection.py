"""
sb/debug_connection.py
----------------------
Run this INSIDE the container to diagnose Supabase connectivity issues.

Usage:
    docker exec main-service python /app/sb/debug_connection.py

It will print exactly what is failing so you can fix it.
"""

import os
import sys
import datetime

print("=" * 60)
print("  Supabase Connection Debug")
print("=" * 60)

# ── Step 1: Check env vars ────────────────────────────────────────
url = os.environ.get("SUPABASE_URL", "")
key = os.environ.get("SUPABASE_KEY", "")

print(f"\n[1] SUPABASE_URL : {'SET -> ' + url if url else '*** MISSING ***'}")
print(f"[1] SUPABASE_KEY : {'SET (length=' + str(len(key)) + ')' if key else '*** MISSING ***'}")

if not url or not key:
    print("\n❌ Env vars not set. Add them to your .env file and rebuild.")
    print("   SUPABASE_URL=https://your-project.supabase.co")
    print("   SUPABASE_KEY=your-service-role-key")
    sys.exit(1)

# ── Step 2: Try to import supabase-py ────────────────────────────
print("\n[2] Importing supabase-py...")
try:
    from supabase import create_client
    print("    ✅ supabase-py imported OK")
except ImportError as e:
    print(f"    ❌ Import failed: {e}")
    print("    Fix: add 'supabase>=2.0.0' to requirements.txt and rebuild")
    sys.exit(1)

# ── Step 3: Create client ─────────────────────────────────────────
print("\n[3] Creating Supabase client...")
try:
    client = create_client(url, key)
    print("    ✅ Client created OK")
except Exception as e:
    print(f"    ❌ create_client failed: {e}")
    sys.exit(1)

# ── Step 4: Test sla_metrics insert ──────────────────────────────
print("\n[4] Testing INSERT into sla_metrics...")
try:
    test_row = {
        "slice_id":     "debug-test",
        "cpu_pct":      1.0,
        "mem_mb":       1.0,
        "net_rx_kb":    0.0,
        "net_tx_kb":    0.0,
        "anomaly_score": -0.1,
        "is_anomaly":   True,
        "confidence":   45.0,
        "attack_type":  "debug",
        "sampled_at":   datetime.datetime.utcnow().isoformat(),
    }
    result = client.table("sla_metrics").insert(test_row).execute()
    if result.data:
        print(f"    ✅ INSERT OK — row id={result.data[0].get('id')}")
    else:
        print(f"    ⚠️  INSERT returned no data: {result}")
except Exception as e:
    print(f"    ❌ INSERT failed: {e}")
    print("    Common causes:")
    print("      - Table 'sla_metrics' does not exist → run supabase/schema.sql")
    print("      - RLS policy blocking insert → use service role key, not anon key")
    print("      - Column mismatch → check schema.sql matches the insert payload")

# ── Step 5: Test incidents insert ────────────────────────────────
print("\n[5] Testing INSERT into incidents...")
try:
    test_inc = {
        "slice_id":       "debug-test",
        "attack_type":    "debug",
        "started_at":     datetime.datetime.utcnow().isoformat(),
        "peak_score":     -0.3,
        "min_confidence": 35.0,
        "is_active":      True,
    }
    result = client.table("incidents").insert(test_inc).execute()
    if result.data:
        print(f"    ✅ INSERT OK — row id={result.data[0].get('id')}")
    else:
        print(f"    ⚠️  INSERT returned no data: {result}")
except Exception as e:
    print(f"    ❌ INSERT failed: {e}")

# ── Step 6: Test model_retrain_log insert ────────────────────────
print("\n[6] Testing INSERT into model_retrain_log...")
try:
    test_log = {
        "retrained_at": datetime.datetime.utcnow().isoformat(),
        "reason":       "debug",
        "n_samples":    0,
        "slice_ids":    '["debug-test"]',
    }
    result = client.table("model_retrain_log").insert(test_log).execute()
    if result.data:
        print(f"    ✅ INSERT OK — row id={result.data[0].get('id')}")
    else:
        print(f"    ⚠️  INSERT returned no data: {result}")
except Exception as e:
    print(f"    ❌ INSERT failed: {e}")

print("\n" + "=" * 60)
print("  Debug complete. Fix any ❌ above then rebuild.")
print("=" * 60)