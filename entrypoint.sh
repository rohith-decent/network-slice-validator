#!/bin/bash
set -e

DB_PATH="${DB_PATH:-/data/metrics.db}"
MODEL_PATH="${MODEL_PATH:-/ml/model.pkl}"
COLLECTOR_INTERVAL="${COLLECTOR_INTERVAL:-5}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

echo "======================================================"
echo "  5G Network Slicing Isolation Validator"
echo "======================================================"
echo "[entrypoint] DB_PATH=$DB_PATH"
echo "[entrypoint] MODEL_PATH=$MODEL_PATH"

# ── Step 1: Wait for Docker socket ───────────────────────────────────
echo "[entrypoint] Waiting for Docker socket..."
for i in $(seq 1 30); do
    if [ -S /var/run/docker.sock ]; then
        echo "[entrypoint] Docker socket available."
        break
    fi
    sleep 1
done

# ── Step 2: Bootstrap DB schema ──────────────────────────────────────
echo "[entrypoint] Initializing SQLite schema..."
python -c "
import sqlite3, os
db = os.environ.get('DB_PATH', '/data/metrics.db')
conn = sqlite3.connect(db)
conn.executescript('''
    CREATE TABLE IF NOT EXISTS metrics (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp     REAL    NOT NULL,
        slice_id      TEXT    NOT NULL,
        cpu_pct       REAL    NOT NULL,
        mem_mb        REAL    NOT NULL,
        net_rx_kb     REAL    NOT NULL,
        net_tx_kb     REAL    NOT NULL,
        anomaly_score REAL    DEFAULT NULL,
        attack_type   TEXT    DEFAULT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ts       ON metrics(timestamp);
    CREATE INDEX IF NOT EXISTS idx_slice_ts ON metrics(slice_id, timestamp);
    CREATE TABLE IF NOT EXISTS drift_config (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
''')
conn.commit()
conn.close()
print('[entrypoint] Schema OK.')
"

# ── Step 3: Collect baseline data (60 seconds) ───────────────────────
echo "[entrypoint] Collecting baseline telemetry for 60 seconds..."
python /app/collector/main.py --duration 60 &
COLLECTOR_PID=$!
wait $COLLECTOR_PID
echo "[entrypoint] Baseline collection done."

# ── Step 4: Train model (from SQLite baseline) ───────────────────────
echo "[entrypoint] Training IsolationForest model..."
python /app/ml/train.py
echo "[entrypoint] Model trained: $MODEL_PATH"

# ── Step 5: Start collector in background (continuous) ───────────────
echo "[entrypoint] Starting continuous collector..."
python /app/collector/main.py &
COLLECTOR_PID=$!
echo "[entrypoint] Collector PID=$COLLECTOR_PID"

# ── Step 6: Start FastAPI ─────────────────────────────────────────────
echo "[entrypoint] Starting FastAPI on :8000..."
uvicorn api.main:app --host 0.0.0.0 --port 8000 --log-level warning &
API_PID=$!
echo "[entrypoint] API PID=$API_PID"

# Wait for API to be ready
for i in $(seq 1 20); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "[entrypoint] API is ready."
        break
    fi
    sleep 2
done

# ── Step 7: Start Streamlit dashboard ────────────────────────────────
echo "[entrypoint] Starting Streamlit dashboard on :8501..."
streamlit run /app/dashboard/app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false &
DASH_PID=$!
echo "[entrypoint] Dashboard PID=$DASH_PID"

echo ""
echo "======================================================"
echo "  All services running!"
echo "  API:       http://localhost:8000"
echo "  Dashboard: http://localhost:8501"
echo "======================================================"

trap 'echo "Shutting down..."; kill $COLLECTOR_PID $API_PID $DASH_PID 2>/dev/null; exit 0' SIGTERM SIGINT

# ── Step 8: Watchdog loop ─────────────────────────────────────────────
# Every 10s: restart collector if it died
# Every 15min: check drift_flag set by the API's drift_detector;
#              if set, retrain from Supabase and hot-reload the model

LAST_RETRAIN_CHECK=$(date +%s)
RETRAIN_INTERVAL=900   # 15 minutes

while true; do
    sleep 10

    # a) Collector heartbeat
    if ! kill -0 $COLLECTOR_PID 2>/dev/null; then
        echo "[watchdog] Restarting collector..."
        python /app/collector/main.py &
        COLLECTOR_PID=$!
        echo "[watchdog] Collector restarted PID=$COLLECTOR_PID"
    fi

    # b) Drift-triggered retrain (every 15 minutes)
    NOW=$(date +%s)
    ELAPSED=$(( NOW - LAST_RETRAIN_CHECK ))

    if [ "$ELAPSED" -ge "$RETRAIN_INTERVAL" ]; then
        LAST_RETRAIN_CHECK=$NOW

        DRIFT_FLAG=$(python - <<'PYEOF'
import sqlite3, os, pathlib
db = pathlib.Path(os.environ.get("DB_PATH", "/data/metrics.db"))
if not db.exists():
    print("0")
else:
    try:
        conn = sqlite3.connect(db)
        row  = conn.execute(
            "SELECT value FROM drift_config WHERE key='drift_flag'"
        ).fetchone()
        print(row[0] if row else "0")
        conn.close()
    except Exception:
        print("0")
PYEOF
)

        if [ "$DRIFT_FLAG" = "1" ]; then
            echo "[watchdog] drift_flag=1 — retraining from Supabase..."
            RETRAIN_REASON=drift_detected python /app/ml/train.py --source supabase --reason drift_detected \
                && echo "[watchdog] Retrain complete." \
                || echo "[watchdog] WARNING: retrain failed, keeping existing model."

            # Hot-reload the new model into the running API
            curl -sf -X POST http://localhost:8000/reload-model > /dev/null \
                && echo "[watchdog] Model hot-reloaded into API." \
                || echo "[watchdog] WARNING: hot-reload request failed."

            # Clear the drift flag
            python - <<'PYEOF'
import sqlite3, os, pathlib
conn = sqlite3.connect(pathlib.Path(os.environ.get("DB_PATH", "/data/metrics.db")))
conn.execute(
    "INSERT OR REPLACE INTO drift_config (key, value) VALUES ('drift_flag', '0')"
)
conn.commit()
conn.close()
print("[watchdog] drift_flag cleared.")
PYEOF
        fi
    fi
done