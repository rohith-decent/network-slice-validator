"""
api/main.py
───────────
FastAPI scoring API for 5G Slice Isolation Validator.

Endpoints:
  GET  /health                   – liveness + model status
  GET  /score                    – latest scored sample(s) with attack classification
  GET  /metrics/history          – last N rows per slice
  POST /reload-model             – hot-reload model.pkl from disk
  POST /inject-attack            – software-inject anomaly rows into SQLite for demo
  GET  /inject-attack/status     – list slices with active injections
  GET  /incidents                – structured incident log
  GET  /incidents/export         – CSV download of incident log
  GET  /audit-log                – paginated anomaly audit table
  GET  /sla                      – SLA compliance % over rolling windows
"""

import os
import time
import sqlite3
import asyncio
import logging
import threading
import joblib
import numpy as np

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import io

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [api] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

DB_PATH    = os.environ.get("DB_PATH",    "/data/metrics.db")
MODEL_PATH = os.environ.get("MODEL_PATH", "/ml/model.pkl")
FEATURE_NAMES = ["cpu_pct", "mem_mb", "net_rx_kb", "net_tx_kb"]

# ── Global model bundle ───────────────────────────────────────────────────────

_bundle: dict = {}
_bundle_lock  = threading.Lock()


def load_bundle() -> bool:
    global _bundle
    if not os.path.exists(MODEL_PATH):
        log.warning("model.pkl not found at %s", MODEL_PATH)
        return False
    try:
        b = joblib.load(MODEL_PATH)
        with _bundle_lock:
            _bundle = b
        log.info("Model loaded: %d samples, trained_at=%.0f", b.get("n_samples", 0), b.get("trained_at", 0))
        return True
    except Exception as e:
        log.error("Failed to load model: %s", e)
        return False


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # Ensure attack_type column exists (added by Capstone 1)
    try:
        conn.execute("ALTER TABLE metrics ADD COLUMN attack_type TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    # Create incidents table
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS incidents (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            slice_id       TEXT    NOT NULL,
            attack_type    TEXT,
            started_at     REAL    NOT NULL,
            resolved_at    REAL,
            peak_score     REAL,
            min_confidence REAL,
            duration_s     REAL,
            is_active      INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_inc_slice ON incidents(slice_id, started_at);
    """)
    conn.commit()
    return conn


# ── Attack classifier ─────────────────────────────────────────────────────────

def classify_attack(features: dict, bundle: dict) -> Optional[str]:
    """
    Returns attack type string if anomaly detected, else None.
    Uses Z-scores against training baseline to identify which feature(s) spiked.
    """
    means = bundle.get("feature_means", {})
    stds  = bundle.get("feature_stds",  {})
    if not means or not stds:
        return "Unknown Anomaly"

    z_scores = {}
    for name in FEATURE_NAMES:
        val   = features.get(name, 0.0)
        mean  = means.get(name, 0.0)
        std   = max(stds.get(name, 1.0), 1e-6)
        z_scores[name] = (val - mean) / std

    elevated = [name for name, z in z_scores.items() if z > 3.0]

    if len(elevated) >= 2:
        return "Combined Attack"
    if "cpu_pct" in elevated:
        return "CPU Starvation"
    if "mem_mb" in elevated:
        return "Memory Exhaustion"
    if "net_rx_kb" in elevated or "net_tx_kb" in elevated:
        return "Network Breach"
    # Anomaly flagged but no single feature is 3σ above baseline
    return "Unknown Anomaly"


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_row(row: dict, bundle: dict) -> dict:
    """Score a single DB row and return enriched dict."""
    features_vec = np.array(
        [[row[f] for f in FEATURE_NAMES]], dtype=np.float32
    )
    X_scaled = bundle["scaler"].transform(features_vec)
    raw_score = float(bundle["model"].decision_function(X_scaled)[0])
    confidence = (max(-0.5, min(0.5, raw_score)) + 0.5) * 100.0
    is_anomaly = raw_score < 0.0

    features_dict = {f: row[f] for f in FEATURE_NAMES}
    attack_type = classify_attack(features_dict, bundle) if is_anomaly else None

    return {
        "slice_id":            row["slice_id"],
        "timestamp":           row["timestamp"],
        "isolation_confidence": round(confidence, 2),
        "anomaly_score":       round(raw_score, 6),
        "is_anomaly":          is_anomaly,
        "attack_type":         attack_type,
        "features":            features_dict,
    }


# ── Incident correlator ───────────────────────────────────────────────────────

_correlator_running = False

async def correlator_loop():
    """Background task: groups contiguous anomaly rows into incident records."""
    global _correlator_running
    _correlator_running = True
    log.info("Incident correlator started.")
    while True:
        await asyncio.sleep(10)
        try:
            conn = get_db()
            slices = [s.strip() for s in os.environ.get("SLICE_NAMES", "slice-a,slice-b").split(",")]
            for slice_id in slices:
                _correlate_slice(conn, slice_id)
            conn.close()
        except Exception as e:
            log.error("Correlator error: %s", e)


def _correlate_slice(conn: sqlite3.Connection, slice_id: str):
    """Open/close incidents for a single slice."""
    # Get last 50 rows ordered ascending
    rows = conn.execute(
        "SELECT id, timestamp, anomaly_score, attack_type FROM metrics "
        "WHERE slice_id=? AND anomaly_score IS NOT NULL "
        "ORDER BY timestamp DESC LIMIT 50",
        (slice_id,)
    ).fetchall()
    rows = list(reversed(rows))  # oldest first

    # Check for active incident
    active = conn.execute(
        "SELECT id, started_at FROM incidents WHERE slice_id=? AND is_active=1 ORDER BY started_at DESC LIMIT 1",
        (slice_id,)
    ).fetchone()

    if not rows:
        return

    recent_anomalies = [r for r in rows[-5:] if r["anomaly_score"] is not None and r["anomaly_score"] < 0]
    is_breaching = len(recent_anomalies) >= 2

    if is_breaching and not active:
        # Open new incident
        first = recent_anomalies[0]
        attack_type = first["attack_type"]
        scores = [r["anomaly_score"] for r in recent_anomalies]
        peak   = min(scores)  # most negative = worst
        min_conf = (max(-0.5, min(0.5, peak)) + 0.5) * 100.0
        conn.execute(
            "INSERT INTO incidents (slice_id, attack_type, started_at, peak_score, min_confidence, is_active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (slice_id, attack_type, first["timestamp"], peak, min_conf)
        )
        conn.commit()
        log.info("Incident OPENED for %s (%s)", slice_id, attack_type)

    elif not is_breaching and active:
        # Close incident
        now = time.time()
        duration = now - active["started_at"]
        conn.execute(
            "UPDATE incidents SET is_active=0, resolved_at=?, duration_s=? WHERE id=?",
            (now, duration, active["id"])
        )
        conn.commit()
        log.info("Incident CLOSED for %s after %.1fs", slice_id, duration)


# ── Active software injections ────────────────────────────────────────────────
# Maps slice_id → asyncio.Task (cancelled when injection ends)

_active_injections: dict[str, asyncio.Task] = {}
_injection_lock = asyncio.Lock()

ATTACK_PROFILES = {
    "cpu": {
        "cpu_pct":   lambda: float(np.random.uniform(85, 99)),
        "mem_mb":    lambda: float(np.random.uniform(35, 55)),
        "net_rx_kb": lambda: float(np.random.uniform(0.5, 2.0)),
        "net_tx_kb": lambda: float(np.random.uniform(0.3, 1.5)),
    },
    "memory": {
        "cpu_pct":   lambda: float(np.random.uniform(5, 18)),
        "mem_mb":    lambda: float(np.random.uniform(110, 128)),
        "net_rx_kb": lambda: float(np.random.uniform(0.5, 2.0)),
        "net_tx_kb": lambda: float(np.random.uniform(0.3, 1.5)),
    },
    "network_breach": {
        "cpu_pct":   lambda: float(np.random.uniform(10, 30)),
        "mem_mb":    lambda: float(np.random.uniform(40, 70)),
        "net_rx_kb": lambda: float(np.random.uniform(800, 2000)),
        "net_tx_kb": lambda: float(np.random.uniform(600, 1800)),
    },
}


async def _run_injection(slice_id: str, attack_type: str, duration_s: int = 60):
    """Writes anomalous rows to SQLite every 5s for duration_s seconds."""
    profile = ATTACK_PROFILES.get(attack_type, ATTACK_PROFILES["cpu"])
    log.info("Injection started: slice=%s type=%s duration=%ds", slice_id, attack_type, duration_s)
    end_time = time.time() + duration_s
    try:
        while time.time() < end_time:
            conn = get_db()
            ts = time.time()
            vals = {k: v() for k, v in profile.items()}
            conn.execute(
                "INSERT INTO metrics (timestamp, slice_id, cpu_pct, mem_mb, net_rx_kb, net_tx_kb) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, slice_id, vals["cpu_pct"], vals["mem_mb"], vals["net_rx_kb"], vals["net_tx_kb"])
            )
            conn.commit()
            conn.close()
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        log.info("Injection cancelled: slice=%s", slice_id)
    finally:
        async with _injection_lock:
            _active_injections.pop(slice_id, None)
        log.info("Injection finished: slice=%s", slice_id)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_bundle()
    # Start incident correlator
    asyncio.create_task(correlator_loop())
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="5G Slice Isolation API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    with _bundle_lock:
        loaded = bool(_bundle)
        has_classifier = bool(_bundle.get("feature_means"))
    conn = get_db()
    db_rows = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    conn.close()
    return {
        "status":           "ok" if loaded else "model_not_loaded",
        "model_loaded":     loaded,
        "classifier_ready": has_classifier,
        "db_rows":          db_rows,
    }


@app.get("/score")
def score(slice_id: Optional[str] = Query(None)):
    with _bundle_lock:
        bundle = dict(_bundle)
    if not bundle:
        raise HTTPException(503, "Model not loaded")

    conn = get_db()
    slices = (
        [slice_id]
        if slice_id
        else [s.strip() for s in os.environ.get("SLICE_NAMES", "slice-a,slice-b").split(",")]
    )
    results = []
    for sid in slices:
        row = conn.execute(
            "SELECT * FROM metrics WHERE slice_id=? ORDER BY timestamp DESC LIMIT 1",
            (sid,)
        ).fetchone()
        if not row:
            continue
        row_dict = dict(row)
        scored   = score_row(row_dict, bundle)

        # Write back anomaly_score and attack_type to DB
        conn.execute(
            "UPDATE metrics SET anomaly_score=?, attack_type=? WHERE id=?",
            (scored["anomaly_score"], scored["attack_type"], row_dict["id"])
        )
        conn.commit()
        results.append(scored)

    conn.close()
    return results if not slice_id else (results[0] if results else {})


@app.get("/metrics/history")
def metrics_history(
    slice_id: str = Query(...),
    limit:    int = Query(120, ge=1, le=1000),
):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM metrics WHERE slice_id=? ORDER BY timestamp DESC LIMIT ?",
        (slice_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/reload-model")
def reload_model():
    ok = load_bundle()
    return {"status": "reloaded" if ok else "failed", "model_loaded": ok}


# ── Attack injection ──────────────────────────────────────────────────────────

@app.post("/inject-attack")
async def inject_attack(
    slice_id:    str = Query(...),
    attack_type: str = Query(...),
    duration_s:  int = Query(60, ge=10, le=300),
):
    if attack_type not in ATTACK_PROFILES:
        raise HTTPException(400, f"Unknown attack_type. Choose from: {list(ATTACK_PROFILES.keys())}")

    async with _injection_lock:
        if slice_id in _active_injections:
            return {"status": "already_running", "message": f"Injection already active on {slice_id}"}
        task = asyncio.create_task(_run_injection(slice_id, attack_type, duration_s))
        _active_injections[slice_id] = task

    return {
        "status":      "started",
        "slice_id":    slice_id,
        "attack_type": attack_type,
        "duration_s":  duration_s,
        "message":     f"Injecting {attack_type} into {slice_id} for {duration_s}s. Watch the gauge drop!",
    }


@app.get("/inject-attack/status")
async def injection_status():
    async with _injection_lock:
        active = list(_active_injections.keys())
    return {"active_injections": active}


# ── Incidents ─────────────────────────────────────────────────────────────────

@app.get("/incidents")
def get_incidents(limit: int = Query(50, ge=1, le=500)):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM incidents ORDER BY started_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/incidents/export")
def export_incidents():
    conn = get_db()
    rows = conn.execute("SELECT * FROM incidents ORDER BY started_at DESC").fetchall()
    conn.close()

    lines = ["id,slice_id,attack_type,started_at,resolved_at,peak_score,min_confidence,duration_s,is_active"]
    for r in rows:
        d = dict(r)
        lines.append(",".join(str(d.get(k, "")) for k in
                     ["id","slice_id","attack_type","started_at","resolved_at",
                      "peak_score","min_confidence","duration_s","is_active"]))
    csv_content = "\n".join(lines)

    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=incidents.csv"},
    )


# ── Audit log ─────────────────────────────────────────────────────────────────

@app.get("/audit-log")
def audit_log(
    slice_id: Optional[str] = Query(None),
    limit:    int            = Query(100, ge=1, le=1000),
    anomaly_only: bool       = Query(False),
):
    conn  = get_db()
    query = "SELECT * FROM metrics WHERE anomaly_score IS NOT NULL"
    args  = []
    if slice_id:
        query += " AND slice_id=?"
        args.append(slice_id)
    if anomaly_only:
        query += " AND anomaly_score < 0"
    query += " ORDER BY timestamp DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(query, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── SLA ───────────────────────────────────────────────────────────────────────

@app.get("/sla")
def sla(
    slice_id: str = Query(...),
    window:   str = Query("1h"),
):
    window_map = {"1h": 3600, "24h": 86400, "7d": 604800}
    if window not in window_map:
        raise HTTPException(400, "window must be 1h, 24h, or 7d")
    since = time.time() - window_map[window]
    conn  = get_db()
    total = conn.execute(
        "SELECT COUNT(*) FROM metrics WHERE slice_id=? AND timestamp>=? AND anomaly_score IS NOT NULL",
        (slice_id, since)
    ).fetchone()[0]
    anomalous = conn.execute(
        "SELECT COUNT(*) FROM metrics WHERE slice_id=? AND timestamp>=? AND anomaly_score < 0",
        (slice_id, since)
    ).fetchone()[0]
    conn.close()
    compliance = ((total - anomalous) / total * 100.0) if total > 0 else 100.0
    return {
        "slice_id":   slice_id,
        "window":     window,
        "total":      total,
        "anomalous":  anomalous,
        "compliance": round(compliance, 4),
    }