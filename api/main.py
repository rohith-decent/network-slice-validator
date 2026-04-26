"""
api/main.py
───────────
FastAPI scoring server.
Reads latest metrics from SQLite, runs IsolationForest inference,
returns anomaly score and isolation confidence.

Endpoints:
  GET /health
  GET /score?slice_id=slice-a          ← latest score for one slice
  GET /score                           ← latest scores for all slices
  GET /metrics/history?slice_id=...&limit=100
"""

import os
import time
import sqlite3
import logging
import subprocess
from fastapi import BackgroundTasks
from fastapi.responses import HTMLResponse
from typing import Optional
from contextlib import asynccontextmanager

import joblib
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [api] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv(r"D:\hackathon\slice-monitor\.env")
DB_PATH    = os.environ.get("DB_PATH",    r"D:\hackathon\slice-monitor\data\metrics.db")
MODEL_PATH = os.environ.get("MODEL_PATH", r"D:\hackathon\slice-monitor\ml\model.pkl")
SLICE_NAMES = [s.strip() for s in os.environ.get("SLICE_NAMES", "slice-a,slice-b").split(",")]
# ── Demo Control Config ─────────────────────────────────────────────
COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "network-slice-validator")
BREACH_NETWORK  = f"{COMPOSE_PROJECT}_slice_b_net"
_breach_active: bool = False

# ── Global model bundle ───────────────────────────────────────────────────────

from typing import Optional
_bundle: Optional[dict] = None


def load_model():
    global _bundle
    if not os.path.exists(MODEL_PATH):
        log.warning("Model not found at %s — inference will be unavailable.", MODEL_PATH)
        _bundle = None
        return
    try:
        _bundle = joblib.load(MODEL_PATH)
        log.info("Model loaded: %d training samples, contamination=%.3f",
                 _bundle.get("n_samples", 0), _bundle.get("contamination", 0))
    except Exception as e:
        log.error("Failed to load model: %s", e)
        _bundle = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_model()
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="5G Slice Isolation Validator API",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _run_cmd(cmd: list, timeout: int = 15) -> tuple[bool, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (True, res.stdout.strip()) if res.returncode == 0 else (False, res.stderr.strip())
    except Exception as e:
        return False, str(e)

def _do_inject_breach():
    global _breach_active
    log.info("[demo] Injecting breach → %s", BREACH_NETWORK)
    _run_cmd(["docker", "network", "connect", BREACH_NETWORK, "slice-a"])
    _run_cmd(["docker", "exec", "-d", "slice-b", "iperf3", "-s"])
    _run_cmd(["docker", "exec", "-d", "slice-a", "iperf3", "-c", "slice-b", "-t", "30", "-b", "5M"])
    _breach_active = True
    log.info("[demo] Breach active. Anomaly expected in ~15s.")

def _do_restore_isolation():
    global _breach_active
    log.info("[demo] Restoring isolation → disconnecting %s", BREACH_NETWORK)
    _run_cmd(["docker", "network", "disconnect", BREACH_NETWORK, "slice-a"])
    _breach_active = False
    log.info("[demo] Isolation restored.")

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_latest_row(slice_id: str) -> Optional[sqlite3.Row]:
    """Fetch the single most recent row for a given slice."""
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT * FROM metrics WHERE slice_id = ? ORDER BY timestamp DESC LIMIT 1",
            (slice_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def get_history(slice_id: str, limit: int = 100) -> list[dict]:
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT timestamp, slice_id, cpu_pct, mem_mb, net_rx_kb, net_tx_kb, anomaly_score "
            "FROM metrics WHERE slice_id = ? ORDER BY timestamp DESC LIMIT ?",
            (slice_id, limit),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_db_row_count() -> int:
    conn = get_db()
    try:
        cur = conn.execute("SELECT COUNT(*) FROM metrics")
        return cur.fetchone()[0]
    except Exception:
        return 0
    finally:
        conn.close()


def compute_isolation_confidence(raw_score: float) -> float:
    """
    Maps IsolationForest decision_function output to 0–100% confidence.
    100% = perfectly isolated (normal).
    0%   = definite anomaly.
    Typical range of decision_function: roughly -0.5 to +0.5.
    """
    clamped = max(-0.5, min(0.5, raw_score))
    return round((clamped + 0.5) * 100.0, 2)


def score_row(row: sqlite3.Row) -> dict:
    """Run ML inference on a single row and return full score dict."""
    cpu_pct   = row["cpu_pct"]
    mem_mb    = row["mem_mb"]
    net_rx_kb = row["net_rx_kb"]
    net_tx_kb = row["net_tx_kb"]

    if _bundle is None:
        return {
            "timestamp":            row["timestamp"],
            "slice_id":             row["slice_id"],
            "anomaly_score":        None,
            "isolation_confidence": None,
            "is_anomaly":           None,
            "model_available":      False,
            "features": {
                "cpu_pct":   cpu_pct,
                "mem_mb":    mem_mb,
                "net_rx_kb": net_rx_kb,
                "net_tx_kb": net_tx_kb,
            },
        }

    scaler = _bundle["scaler"]
    model  = _bundle["model"]
    feature_vector = np.array([[cpu_pct, mem_mb, net_rx_kb, net_tx_kb]], dtype=np.float32)
    X_scaled = scaler.transform(feature_vector)

    raw_score  = float(model.decision_function(X_scaled)[0])
    is_anomaly = bool(model.predict(X_scaled)[0] == -1)
    confidence = compute_isolation_confidence(raw_score)

    # Write score back to DB asynchronously (best-effort)
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE metrics SET anomaly_score = ? WHERE id = ?",
            (raw_score, row["id"]),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    return {
        "timestamp":            row["timestamp"],
        "slice_id":             row["slice_id"],
        "anomaly_score":        round(raw_score, 6),
        "isolation_confidence": confidence,
        "is_anomaly":           is_anomaly,
        "model_available":      True,
        "features": {
            "cpu_pct":   round(cpu_pct, 3),
            "mem_mb":    round(mem_mb, 3),
            "net_rx_kb": round(net_rx_kb, 3),
            "net_tx_kb": round(net_tx_kb, 3),
        },
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    db_rows = get_db_row_count()
    return {
        "status":       "ok",
        "model_loaded": _bundle is not None,
        "db_rows":      db_rows,
        "timestamp":    time.time(),
    }


@app.get("/score")
def score(slice_id: Optional[str] = Query(default=None)):
    """
    Returns latest anomaly score.
    - ?slice_id=slice-a  → single slice result dict
    - no param           → list of all slices
    """
    if slice_id:
        row = get_latest_row(slice_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No data for slice '{slice_id}'")
        return score_row(row)

    # All slices
    results = []
    for name in SLICE_NAMES:
        row = get_latest_row(name)
        if row is not None:
            results.append(score_row(row))
    if not results:
        raise HTTPException(status_code=404, detail="No metrics data available yet.")
    return results


@app.get("/metrics/history")
def metrics_history(
    slice_id: str = Query(..., description="Slice name, e.g. slice-a"),
    limit: int    = Query(default=100, ge=1, le=1000),
):
    """Return last `limit` rows for a slice (most recent first)."""
    rows = get_history(slice_id, limit)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No history for slice '{slice_id}'")
    return rows


@app.post("/reload-model")
def reload_model():
    """Hot-reload the model from disk (useful after re-training)."""
    load_model()
    return {"status": "reloaded", "model_loaded": _bundle is not None}


# ── Mobile Control Console Routes ───────────────────────────────────
@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    html_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dashboard", "demo_control.html"))
    if not os.path.exists(html_path):
        return HTMLResponse("<h1>demo_control.html not found</h1>", status_code=404)
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.post("/demo/inject")
def inject_breach(background_tasks: BackgroundTasks):
    if _breach_active:
        return {"status": "already_active", "breach_active": True}
    background_tasks.add_task(_do_inject_breach)
    return {"status": "injected", "message": "Breach started. Watch dashboard in ~15s.", "breach_active": True}

@app.post("/demo/restore")
def restore_isolation(background_tasks: BackgroundTasks):
    background_tasks.add_task(_do_restore_isolation)
    return {"status": "restoring", "message": "Isolation restoring. Recovery in 30-60s.", "breach_active": False}

@app.get("/demo/status")
def get_status():
    return {"breach_active": _breach_active, "breach_network": BREACH_NETWORK, "timestamp": time.time()}