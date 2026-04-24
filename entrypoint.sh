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
        anomaly_score REAL    DEFAULT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ts       ON metrics(timestamp);
    CREATE INDEX IF NOT EXISTS idx_slice_ts ON metrics(slice_id, timestamp);
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

# ── Step 4: Train model ──────────────────────────────────────────────
echo "[entrypoint] Training IsolationForest model..."
python /app/ml/train.py
echo "[entrypoint] Model trained: $MODEL_PATH"

# ── Step 5: Start collector in background (continuous) ───────────────
echo "[entrypoint] Starting continuous collector..."
python /app/collector/main.py &
echo "[entrypoint] Collector PID=$!"

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

# ── Keep container alive, restart children if they die ───────────────
trap 'echo "Shutting down..."; kill $COLLECTOR_PID $API_PID $DASH_PID 2>/dev/null; exit 0' SIGTERM SIGINT

while true; do
    # Restart collector if it died
    if ! kill -0 $COLLECTOR_PID 2>/dev/null; then
        echo "[watchdog] Restarting collector..."
        python /app/collector/main.py &
        COLLECTOR_PID=$!
    fi
    sleep 10
done
