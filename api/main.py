"""
api/main.py
───────────
FastAPI scoring API for 5G Process Isolation Validator.

Endpoints (all original preserved + new):
  GET  /health                   – liveness + model status
  GET  /score                    – latest scored sample(s) with attack classification
  GET  /metrics/history          – last N rows per process
  POST /reload-model             – hot-reload model.pkl from disk
  POST /inject-attack            – software-inject anomaly rows into SQLite for demo
  GET  /inject-attack/status     – list processes with active injections
  GET  /incidents                – structured incident log (SQLite + Supabase mirror)
  GET  /incidents/export         – CSV download of incident log
  GET  /audit-log                – paginated anomaly audit table
  GET  /sla                      – SLA compliance % over rolling windows
  GET  /demo                     – process control console (HTML)
  POST /demo/inject              – trigger process breach demo
  POST /demo/restore             – restore isolation after demo
  GET  /demo/status              – current process breach status
  GET  /model/status             – Capstone 4: model age, drift rate, retrain log
  POST /exfil/ingest             – ingest cross-process exfiltration data
  GET  /exfil/latest             – view latest exfiltrated payloads

  ── AI Layer (arm's-length, read-only) ──────────────────────────────────────
  GET  /ai/forecast              – Predictive breach risk for a slice (Feature A)
  GET  /ai/incident/{id}/reason  – Forensic hypothesis for a closed incident (Feature B)
  GET  /ai/status                – Whether AI key is configured
"""

import io
import os
import time
import sqlite3
import asyncio
import logging
import threading
import subprocess
import datetime as _dt
import joblib
import numpy as np

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi import Request, Query, Body
from pydantic import BaseModel
import collections
import requests as _req

_exfil_store = collections.deque(maxlen=50)
_exfil_lock  = threading.Lock()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [api] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

DB_PATH       = os.environ.get("DB_PATH",    "/data/metrics.db")
MODEL_PATH    = os.environ.get("MODEL_PATH", "/ml/model.pkl")
FEATURE_NAMES = ["cpu_pct", "mem_mb", "net_rx_kb", "net_tx_kb"]

# ── Demo Control Config ─────────────────────────────────────────────
COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "network-slice-validator")
BREACH_NETWORK  = f"{COMPOSE_PROJECT}_slice_b_net"
_breach_active: bool = False

class InjectRequest(BaseModel):
    slice_id: str = "slice-a"   # defaults to slice-a for backward compat

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
        log.info("Model loaded: %d samples, trained_at=%.0f",
                 b.get("n_samples", 0), b.get("trained_at", 0))
        return True
    except Exception as e:
        log.error("Failed to load model: %s", e)
        return False


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute("ALTER TABLE metrics ADD COLUMN attack_type TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
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
            is_active      INTEGER DEFAULT 1,
            ai_forensic_note TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_inc_slice ON incidents(slice_id, started_at);
        CREATE TABLE IF NOT EXISTS drift_config (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    # Migrate existing incidents table if ai_forensic_note column is missing
    try:
        conn.execute("ALTER TABLE incidents ADD COLUMN ai_forensic_note TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    return conn


# ── Supabase helpers — all non-blocking, never raise ─────────────────────────

def _sb_push_metric(row: dict) -> None:
    try:
        from sb.metrics import push_metric_row
        push_metric_row(row)
    except Exception as exc:
        log.debug("Supabase metric push skipped: %s", exc)


def _sb_open_incident(record: dict) -> dict | None:
    """Insert a new incident in Supabase and return the created row (or None)."""
    try:
        from sb.incidents import open_incident
        return open_incident(record)
    except Exception as exc:
        log.debug("Supabase open_incident skipped: %s", exc)
        return None


def _sb_get_active_incident(slice_id: str) -> dict | None:
    """Return the currently open Supabase incident for a slice, or None."""
    try:
        from sb.incidents import fetch_active_incident
        return fetch_active_incident(slice_id)
    except Exception:
        return None


def _sb_close_incident(slice_id: str, resolved_at: str, duration_s: float) -> None:
    try:
        from sb.incidents import fetch_active_incident, close_incident
        inc = fetch_active_incident(slice_id)
        if inc:
            close_incident(inc["id"], resolved_at, duration_s)
    except Exception as exc:
        log.debug("Supabase close_incident skipped: %s", exc)


def _sb_fetch_incidents(limit: int) -> list[dict]:
    try:
        from sb.incidents import fetch_incidents
        return fetch_incidents(limit) or []
    except Exception:
        return []


def _sb_fetch_sla_window(slice_id: str, hours: int) -> list[dict]:
    try:
        from sb.metrics import fetch_sla_window
        return fetch_sla_window(slice_id, hours) or []
    except Exception:
        return []


def _sb_fetch_retrain_log(limit: int = 1) -> list[dict]:
    try:
        from sb.retrain import fetch_retrain_log
        return fetch_retrain_log(limit) or []
    except Exception:
        return []


def _sb_log_retrain(reason: str, n_samples: int, slice_ids: list[str]) -> None:
    """Write one row to model_retrain_log in Supabase. Never raises."""
    import json
    try:
        from sb.retrain import log_retrain
        log_retrain({
            "retrained_at": _dt.datetime.utcnow().isoformat(),
            "reason":       reason,
            "n_samples":    n_samples,
            "slice_ids":    json.dumps(slice_ids),
        })
        log.info("[sb.retrain] Logged retrain event: reason=%s slices=%s", reason, slice_ids)
    except Exception as exc:
        log.debug("Supabase log_retrain skipped: %s", exc)


# ── Attack classifier ─────────────────────────────────────────────────────────

def classify_attack(features: dict, bundle: dict) -> Optional[str]:
    means = bundle.get("feature_means", {})
    stds  = bundle.get("feature_stds",  {})
    if not means or not stds:
        return "Unknown Anomaly"

    z_scores = {}
    for name in FEATURE_NAMES:
        val  = features.get(name, 0.0)
        mean = means.get(name, 0.0)
        std  = max(stds.get(name, 1.0), 1e-6)
        z_scores[name] = (val - mean) / std

    elevated = [name for name, z in z_scores.items() if z > 3.0]

    # Combined Attack requires 3+ features simultaneously elevated.
    # With 2 elevated features we pick the dominant one (highest Z-score)
    # to avoid misclassifying single-vector attacks that have a minor
    # secondary spike (e.g. CPU spike causes a tiny memory uptick).
    if len(elevated) >= 3:
        return "Combined Attack"

    # Determine primary feature by highest absolute Z-score
    if elevated:
        dominant = max(elevated, key=lambda n: z_scores[n])
        if dominant == "cpu_pct":
            return "CPU Starvation"
        if dominant == "mem_mb":
            return "Memory Exhaustion"
        if dominant in ("net_rx_kb", "net_tx_kb"):
            return "Network Breach"

    # Fallback: even if no feature cleared Z>3, check which is most elevated
    dominant = max(FEATURE_NAMES, key=lambda n: z_scores.get(n, 0))
    if dominant == "cpu_pct":
        return "CPU Starvation"
    if dominant == "mem_mb":
        return "Memory Exhaustion"
    if dominant in ("net_rx_kb", "net_tx_kb"):
        return "Network Breach"
    return "Unknown Anomaly"


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_row(row: dict, bundle: dict) -> dict:
    features_vec = np.array([[row[f] for f in FEATURE_NAMES]], dtype=np.float32)
    X_scaled     = bundle["scaler"].transform(features_vec)
    raw_score    = float(bundle["model"].decision_function(X_scaled)[0])
    confidence   = (max(-0.5, min(0.5, raw_score)) + 0.5) * 100.0
    is_anomaly   = raw_score < 0.0

    features_dict = {f: row[f] for f in FEATURE_NAMES}
    attack_type   = classify_attack(features_dict, bundle) if is_anomaly else None

    return {
        "slice_id":             row["slice_id"],
        "timestamp":            row["timestamp"],
        "isolation_confidence": round(confidence, 2),
        "anomaly_score":        round(raw_score, 6),
        "is_anomaly":           is_anomaly,
        "attack_type":          attack_type,
        "features":             features_dict,
    }


# ── Incident correlator ───────────────────────────────────────────────────────

async def correlator_loop():
    log.info("Incident correlator started.")
    while True:
        await asyncio.sleep(10)
        try:
            conn   = get_db()
            slices = [s.strip() for s in
                      os.environ.get("SLICE_NAMES", "slice-a,slice-b").split(",")]
            for slice_id in slices:
                _correlate_slice(conn, slice_id)
            conn.close()
        except Exception as e:
            log.error("Correlator error: %s", e)


def _correlate_slice(conn: sqlite3.Connection, slice_id: str):
    rows = conn.execute(
        "SELECT id, timestamp, anomaly_score, attack_type FROM metrics "
        "WHERE slice_id=? AND anomaly_score IS NOT NULL "
        "ORDER BY timestamp DESC LIMIT 50",
        (slice_id,)
    ).fetchall()
    rows = list(reversed(rows))

    active = conn.execute(
        "SELECT id, started_at FROM incidents "
        "WHERE slice_id=? AND is_active=1 ORDER BY started_at DESC LIMIT 1",
        (slice_id,)
    ).fetchone()

    if not rows:
        return

    recent_anomalies = [r for r in rows[-5:]
                        if r["anomaly_score"] is not None and r["anomaly_score"] < 0]
    is_breaching = len(recent_anomalies) >= 2

    if is_breaching and not active:
        first       = recent_anomalies[0]
        attack_type = first["attack_type"]
        scores      = [r["anomaly_score"] for r in recent_anomalies]
        peak        = min(scores)
        min_conf    = (max(-0.5, min(0.5, peak)) + 0.5) * 100.0
        conn.execute(
            "INSERT INTO incidents "
            "(slice_id, attack_type, started_at, peak_score, min_confidence, is_active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (slice_id, attack_type, first["timestamp"], peak, min_conf)
        )
        conn.commit()
        log.info("Incident OPENED for %s (%s)", slice_id.replace("slice", "process"), attack_type)

        # Send email alert for real network breaches detected by the correlator
        try:
            from sb.email_alert import send_attack_alert
            with _bundle_lock:
                _b = dict(_bundle)
            last_row = conn.execute(
                "SELECT cpu_pct, mem_mb, net_rx_kb, net_tx_kb FROM metrics "
                "WHERE slice_id=? ORDER BY timestamp DESC LIMIT 1",
                (slice_id,)
            ).fetchone()
            features_dict = dict(last_row) if last_row else {}
            # Determine the public-facing host for links
            host = os.environ.get("PUBLIC_API_HOST", "http://localhost:8000")
            dash_host = os.environ.get("PUBLIC_DASHBOARD_HOST", "http://localhost:8501")
            send_attack_alert(
                slice_id=slice_id,
                attack_type=attack_type,
                confidence=min_conf,
                features=features_dict,
                restore_url=f"{host}/restore_isolation",
                dashboard_url=dash_host
            )
        except Exception as exc:
            log.debug("Correlator email alert skipped: %s", exc)

        # Only open a Supabase incident if one isn't already active
        # (the inject-attack path opens one immediately on button press)
        if not _sb_get_active_incident(slice_id):
            _sb_open_incident({
                "slice_id":       slice_id,
                "attack_type":    attack_type,
                "started_at":     _dt.datetime.utcfromtimestamp(
                                      first["timestamp"]).isoformat(),
                "peak_score":     peak,
                "min_confidence": min_conf,
                "is_active":      True,
            })

    elif not is_breaching and active:
        now      = time.time()
        duration = now - active["started_at"]
        conn.execute(
            "UPDATE incidents SET is_active=0, resolved_at=?, duration_s=? WHERE id=?",
            (now, duration, active["id"])
        )
        conn.commit()
        log.info("Incident CLOSED for %s after %.1fs", slice_id.replace("slice", "process"), duration)

        _sb_close_incident(
            slice_id,
            _dt.datetime.utcfromtimestamp(now).isoformat(),
            duration,
        )

        # ── AI Incident Reasoner (runs in background thread, never blocks) ──
        def _run_ai_reasoner(inc_id: int, sid: str, started: float):
            try:
                from ai.layer import reason_about_incident
                # Fetch the closed incident record
                _conn = get_db()
                inc_row = _conn.execute(
                    "SELECT * FROM incidents WHERE id=?", (inc_id,)
                ).fetchone()
                if not inc_row:
                    _conn.close()
                    return
                inc_dict = dict(inc_row)
                # Fetch telemetry window: 30 rows around the breach
                window_rows = _conn.execute(
                    "SELECT timestamp, cpu_pct, mem_mb, net_rx_kb, net_tx_kb, anomaly_score "
                    "FROM metrics WHERE slice_id=? AND timestamp >= ? AND timestamp <= ? "
                    "ORDER BY timestamp ASC LIMIT 30",
                    (sid, started - 30, now + 5)
                ).fetchall()
                telemetry = [dict(r) for r in window_rows]
                _conn.close()

                note = reason_about_incident(inc_dict, telemetry)
                if note:
                    _conn2 = get_db()
                    _conn2.execute(
                        "UPDATE incidents SET ai_forensic_note=? WHERE id=?",
                        (note, inc_id)
                    )
                    _conn2.commit()
                    _conn2.close()
                    log.info("[AI] Forensic note written for incident %d (%s)", inc_id, sid)
            except Exception as exc:
                log.warning("[AI] Reasoner background error: %s", exc)

        import threading
        t = threading.Thread(
            target=_run_ai_reasoner,
            args=(active["id"], slice_id, active["started_at"]),
            daemon=True,
        )
        t.start()


# ── Drift detector (Capstone 4) ───────────────────────────────────────────────

_drift_strikes    = 0
DRIFT_THRESHOLD   = 0.20
DRIFT_CONSECUTIVE = 3


async def drift_detector_loop():
    global _drift_strikes
    log.info("Drift detector started.")
    while True:
        await asyncio.sleep(60)
        try:
            conn = get_db()
            rows = conn.execute(
                "SELECT anomaly_score FROM metrics "
                "WHERE anomaly_score IS NOT NULL "
                "ORDER BY timestamp DESC LIMIT 100"
            ).fetchall()
            conn.close()

            if not rows:
                continue

            ratio = sum(1 for r in rows if r["anomaly_score"] < 0) / len(rows)
            log.info("Drift check: ratio=%.3f over last %d rows", ratio, len(rows))

            if ratio > DRIFT_THRESHOLD:
                _drift_strikes += 1
                log.warning("Drift strike %d/%d", _drift_strikes, DRIFT_CONSECUTIVE)
            else:
                _drift_strikes = 0

            if _drift_strikes >= DRIFT_CONSECUTIVE:
                log.warning("Drift threshold exceeded — setting retrain flag.")
                _drift_strikes = 0
                conn2 = get_db()
                conn2.execute(
                    "INSERT OR REPLACE INTO drift_config (key, value) "
                    "VALUES ('drift_flag', '1')"
                )
                conn2.commit()
                conn2.close()

        except Exception as exc:
            log.error("Drift detector error: %s", exc)


# ── Attack injection profiles ─────────────────────────────────────────────────

ATTACK_PROFILES = {
    # CPU Starvation: only cpu_pct spikes. Memory and network stay near Alpine idle.
    # Alpine idle baseline: cpu~0.5%, mem~3MB, net~0 KB/s
    "cpu": {
        "cpu_pct":   lambda: float(np.random.uniform(85, 99)),
        "mem_mb":    lambda: float(np.random.uniform(2.0, 5.0)),   # near-idle
        "net_rx_kb": lambda: float(np.random.uniform(0.0, 0.5)),   # near-idle
        "net_tx_kb": lambda: float(np.random.uniform(0.0, 0.3)),   # near-idle
    },
    # Memory Exhaustion: only mem_mb spikes. CPU stays low (no compute work).
    "memory": {
        "cpu_pct":   lambda: float(np.random.uniform(0.5, 3.0)),   # near-idle
        "mem_mb":    lambda: float(np.random.uniform(110, 128)),
        "net_rx_kb": lambda: float(np.random.uniform(0.0, 0.5)),   # near-idle
        "net_tx_kb": lambda: float(np.random.uniform(0.0, 0.3)),   # near-idle
    },
    # Network Breach: only net_rx/tx spike (cross-slice ping flood).
    # CPU tick from ping is minimal; memory is unaffected.
    "network_breach": {
        "cpu_pct":   lambda: float(np.random.uniform(0.5, 3.0)),   # near-idle
        "mem_mb":    lambda: float(np.random.uniform(2.0, 5.0)),   # near-idle
        "net_rx_kb": lambda: float(np.random.uniform(800, 2000)),
        "net_tx_kb": lambda: float(np.random.uniform(600, 1800)),
    },
}

_active_injections: dict[str, asyncio.Task] = {}
_injection_lock = asyncio.Lock()


async def _run_injection(slice_id: str, attack_type: str, duration_s: int = 60):
    profile      = ATTACK_PROFILES.get(attack_type, ATTACK_PROFILES["cpu"])
    end_time     = time.time() + duration_s
    first_row    = True
    log.info("Injection started: process=%s type=%s duration=%ds",
             slice_id.replace("slice", "process"), attack_type, duration_s)
    try:
        while time.time() < end_time:
            with _bundle_lock:
                bundle = dict(_bundle)

            ts   = time.time()
            vals = {k: v() for k, v in profile.items()}

            # Score the injected row so anomaly_score + attack_type are filled
            scored = {}
            if bundle:
                try:
                    scored = score_row(
                        {"slice_id": slice_id, "timestamp": ts, **vals},
                        bundle,
                    )
                except Exception:
                    pass

            anomaly_score = scored.get("anomaly_score")
            classified_at = scored.get("attack_type", attack_type.replace("_", " ").title())
            confidence    = scored.get("isolation_confidence", 0.0)

            conn = get_db()
            conn.execute(
                "INSERT INTO metrics "
                "(timestamp, slice_id, cpu_pct, mem_mb, net_rx_kb, net_tx_kb, "
                " anomaly_score, attack_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, slice_id,
                 vals["cpu_pct"], vals["mem_mb"],
                 vals["net_rx_kb"], vals["net_tx_kb"],
                 anomaly_score, classified_at)
            )
            conn.commit()
            conn.close()

            # Mirror every injected row to Supabase
            _sb_push_metric({
                "slice_id":    slice_id,
                "cpu_pct":     vals["cpu_pct"],
                "mem_mb":      vals["mem_mb"],
                "net_rx_kb":   vals["net_rx_kb"],
                "net_tx_kb":   vals["net_tx_kb"],
                "anomaly_score": anomaly_score,
                "is_anomaly":  True,
                "confidence":  confidence,
                "attack_type": classified_at,
                "sampled_at":  _dt.datetime.utcfromtimestamp(ts).isoformat(),
            })

            # Send email alert + open Supabase incident + log retrain — first row only
            if first_row:
                first_row = False

                # 1. Email alert
                try:
                    from sb.email_alert import send_attack_alert
                    host = os.environ.get("PUBLIC_API_HOST", "http://localhost:8000")
                    dash_host = os.environ.get("PUBLIC_DASHBOARD_HOST", "http://localhost:8501")
                    send_attack_alert(
                        slice_id=slice_id,
                        attack_type=classified_at,
                        confidence=confidence,
                        features=vals,
                        restore_url=f"{host}/restore_isolation",
                        dashboard_url=dash_host
                    )
                except Exception as exc:
                    log.debug("Email alert skipped: %s", exc)

                # 2. Immediately open a Supabase incident (don't wait for correlator)
                existing = _sb_get_active_incident(slice_id)
                if not existing:
                    _sb_open_incident({
                        "slice_id":       slice_id,
                        "attack_type":    classified_at,
                        "started_at":     _dt.datetime.utcfromtimestamp(ts).isoformat(),
                        "peak_score":     anomaly_score if anomaly_score is not None else -0.5,
                        "min_confidence": confidence,
                        "is_active":      True,
                    })
                    log.info("[inject] Supabase incident opened: process=%s type=%s",
                             slice_id.replace("slice", "process"), classified_at)

                # 3. Log a model_retrain_log row so the audit trail records
                #    which slices were active when this attack injection started.
                with _bundle_lock:
                    _b = dict(_bundle)
                n_samples = _b.get("n_samples", 0)
                _sb_log_retrain(
                    reason=f"attack_injection:{classified_at}",
                    n_samples=n_samples,
                    slice_ids=[slice_id],
                )

            await asyncio.sleep(5)
    except asyncio.CancelledError:
        log.info("Injection cancelled: slice=%s", slice_id)
    finally:
        async with _injection_lock:
            _active_injections.pop(slice_id, None)
        log.info("Injection finished: process=%s", slice_id.replace("slice", "process"))


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_bundle()
    asyncio.create_task(correlator_loop())
    asyncio.create_task(drift_detector_loop())
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="5G Process Isolation API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Demo Helpers ──────────────────────────────────────────────────────────────

def _run_cmd(cmd: list, timeout: int = 15) -> tuple[bool, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (True, res.stdout.strip()) if res.returncode == 0 else (False, res.stderr.strip())
    except Exception as e:
        return False, str(e)

def _do_inject_breach(target_slice: str = "slice-a"):
    global _breach_active
    
    slices_to_attack = []
    if target_slice == "BOTH":
        slices_to_attack = ["slice-a", "slice-b"]
    else:
        slices_to_attack = [target_slice]

    for ts in slices_to_attack:
        # Determine attacker and victim based on target slice
        if ts == "slice-b":
            attacker, victim = "slice-b", "slice-a"
            breach_net = f"{COMPOSE_PROJECT}_slice_a_net"
        else:
            attacker, victim = "slice-a", "slice-b"
            breach_net = BREACH_NETWORK  # slice_b_net

        log.info("[demo] Injecting process breach: %s → %s via %s", attacker.replace("slice", "process"), victim.replace("slice", "process"), breach_net)
        _run_cmd(["docker", "network", "connect", breach_net, attacker])
        _run_cmd(["docker", "exec", "-d", victim, "iperf3", "-s"])
        _run_cmd(["docker", "exec", "-d", attacker, "iperf3", "-c", victim, "-t", "30", "-b", "5M"])
    
    _breach_active = True

    # ── Signal agents ──
    l2 = os.environ.get("LAPTOP2_IP", "127.0.0.1")
    l3 = os.environ.get("LAPTOP3_IP", "127.0.0.1")
    try:
        _req.post(f"http://{l2}:9000/start", timeout=3)
        _req.post(f"http://{l3}:9001/start", timeout=3)
    except Exception as e:
        log.warning("Failed to signal agents: %s", e)
    log.info("[demo] Breach active. Anomaly expected in ~15s.")

def _do_restore_isolation():
    global _breach_active
    log.info("[demo] Restoring process isolation → executing powerful recovery commands")
    
    # Powerful recovery commands as requested
    try:
        # Disconnect containers from breach networks
        _run_cmd(["docker", "network", "disconnect", f"{COMPOSE_PROJECT}_slice_b_net", "slice-a"])
        _run_cmd(["docker", "network", "disconnect", f"{COMPOSE_PROJECT}_slice_a_net", "slice-b"])
        
        # Flush iptables and reset traffic control (if applicable in the environment)
        # Note: These might fail if not running with enough privileges or if tools aren't present
        # but we try them anyway as requested.
        os.system("iptables -F")
        os.system("tc qdisc del dev eth0 root || true")
        
        # Kill iperf3 processes
        _run_cmd(["docker", "exec", "slice-a", "pkill", "iperf3"])
        _run_cmd(["docker", "exec", "slice-b", "pkill", "iperf3"])
        
        # Signal agents to stop
        l2 = os.environ.get("LAPTOP2_IP", "127.0.0.1")
        l3 = os.environ.get("LAPTOP3_IP", "127.0.0.1")
        try:
            _req.post(f"http://{l2}:9000/stop", timeout=3)
            _req.post(f"http://{l3}:9001/stop", timeout=3)
        except Exception:
            pass
            
    except Exception as e:
        log.error("Restore isolation failed: %s", e)
    
    _breach_active = False
    log.info("[demo] Process isolation restored.")


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════


@app.get("/health")
def health():
    with _bundle_lock:
        loaded         = bool(_bundle)
        has_classifier = bool(_bundle.get("feature_means"))
    conn    = get_db()
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

    conn   = get_db()
    slices = (
        [slice_id] if slice_id
        else [s.strip() for s in
              os.environ.get("SLICE_NAMES", "slice-a,slice-b").split(",")]
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

        conn.execute(
            "UPDATE metrics SET anomaly_score=?, attack_type=? WHERE id=?",
            (scored["anomaly_score"], scored["attack_type"], row_dict["id"])
        )
        conn.commit()

        # Mirror to Supabase (best-effort)
        _sb_push_metric({
            "slice_id":    sid,
            "cpu_pct":     row_dict["cpu_pct"],
            "mem_mb":      row_dict["mem_mb"],
            "net_rx_kb":   row_dict["net_rx_kb"],
            "net_tx_kb":   row_dict["net_tx_kb"],
            "anomaly_score": scored["anomaly_score"],
            "is_anomaly":  scored["is_anomaly"],
            "confidence":  scored["isolation_confidence"],
            "attack_type": scored["attack_type"],
            "sampled_at":  _dt.datetime.utcfromtimestamp(
                               row_dict["timestamp"]).isoformat(),
        })

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
    global _drift_strikes
    ok = load_bundle()
    _drift_strikes = 0
    return {"status": "reloaded" if ok else "failed", "model_loaded": ok}


# ── Attack injection ──────────────────────────────────────────────────────────

@app.post("/inject-attack")
async def inject_attack(
    slice_id:    str = Query(...),
    attack_type: str = Query(...),
    duration_s:  int = Query(60, ge=10, le=300),
):
    if attack_type not in ATTACK_PROFILES:
        raise HTTPException(
            400, f"Unknown attack_type. Choose from: {list(ATTACK_PROFILES.keys())}"
        )
    async with _injection_lock:
        if slice_id in _active_injections:
            return {
                "status":  "already_running",
                "message": f"Injection already active on {slice_id}",
            }
        task = asyncio.create_task(
            _run_injection(slice_id, attack_type, duration_s)
        )
        _active_injections[slice_id] = task

    return {
        "status":      "started",
        "slice_id":    slice_id,
        "attack_type": attack_type,
        "duration_s":  duration_s,
        "message":     (f"Injecting {attack_type} into {slice_id.replace('slice', 'process')} for {duration_s}s. "
                        "Watch the gauge drop!"),
    }


@app.get("/inject-attack/status")
async def injection_status():
    async with _injection_lock:
        active = list(_active_injections.keys())
    return {"active_injections": [s.replace("slice", "process") for s in active]}


# ── Incidents ─────────────────────────────────────────────────────────────────

@app.get("/incidents")
def get_incidents(limit: int = Query(50, ge=1, le=500)):
    sb_rows = _sb_fetch_incidents(limit)
    if sb_rows:
        # Supabase rows don't have ai_forensic_note (column added locally to SQLite).
        # Merge it in from SQLite so the dashboard can display generated notes.
        try:
            conn = get_db()
            # Build a lookup: incident id → ai_forensic_note from SQLite
            sqlite_notes = {}
            for r in conn.execute("SELECT id, ai_forensic_note FROM incidents").fetchall():
                if r["ai_forensic_note"]:
                    sqlite_notes[r["id"]] = r["ai_forensic_note"]
            conn.close()
            if sqlite_notes:
                for row in sb_rows:
                    inc_id = row.get("id")
                    if inc_id and inc_id in sqlite_notes:
                        row["ai_forensic_note"] = sqlite_notes[inc_id]
        except Exception:
            pass  # never break the incidents list over a note merge
        return sb_rows
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM incidents ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/incidents/export")
def export_incidents():
    conn   = get_db()
    rows   = conn.execute(
        "SELECT * FROM incidents ORDER BY started_at DESC"
    ).fetchall()
    conn.close()
    fields = ["id", "slice_id", "attack_type", "started_at", "resolved_at",
              "peak_score", "min_confidence", "duration_s", "is_active"]
    lines  = [",".join(fields)]
    for r in rows:
        d = dict(r)
        lines.append(",".join(str(d.get(k, "")) for k in fields))
    return StreamingResponse(
        io.StringIO("\n".join(lines)),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=incidents.csv"},
    )


# ── Audit log ─────────────────────────────────────────────────────────────────

@app.get("/audit-log")
def audit_log(
    slice_id:     Optional[str] = Query(None),
    limit:        int           = Query(100, ge=1, le=1000),
    anomaly_only: bool          = Query(False),
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

    # Try Supabase first
    hours    = window_map[window] // 3600
    sb_rows  = _sb_fetch_sla_window(slice_id, hours)
    if sb_rows:
        total      = len(sb_rows)
        anomalous  = sum(1 for r in sb_rows if r.get("is_anomaly"))
        compliance = ((total - anomalous) / total * 100.0) if total > 0 else 100.0
        return {
            "slice_id":   slice_id,
            "window":     window,
            "total":      total,
            "anomalous":  anomalous,
            "compliance": round(compliance, 4),
            "source":     "supabase",
        }

    # SQLite fallback
    since     = time.time() - window_map[window]
    conn      = get_db()
    total     = conn.execute(
        "SELECT COUNT(*) FROM metrics "
        "WHERE slice_id=? AND timestamp>=? AND anomaly_score IS NOT NULL",
        (slice_id, since)
    ).fetchone()[0]
    anomalous = conn.execute(
        "SELECT COUNT(*) FROM metrics "
        "WHERE slice_id=? AND timestamp>=? AND anomaly_score < 0",
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
        "source":     "sqlite",
    }


# ── Demo Control Console Routes ───────────────────────────────────────────────

@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    html_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dashboard", "demo_control.html"))
    if not os.path.exists(html_path):
        return HTMLResponse("<h1>demo_control.html not found</h1>", status_code=404)
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/restore", response_class=HTMLResponse)
def restore_page():
    html_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "dashboard", "restore.html")
    )
    if not os.path.exists(html_path):
        return HTMLResponse("<h1>restore.html not found</h1>", status_code=404)
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.post("/demo/inject")
@app.post("/inject_attack")
def inject_breach(background_tasks: BackgroundTasks, body: InjectRequest = Body(default=InjectRequest())):
    if _breach_active:
        return {"status": "already_active", "breach_active": True}
    background_tasks.add_task(_do_inject_breach, body.slice_id)
    return {"status": "injected", "message": f"Process breach started on {body.slice_id.replace('slice', 'process')}. Watch dashboard in ~15s.", "breach_active": True}

@app.post("/demo/restore")
@app.post("/restore_isolation")
def restore_isolation_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(_do_restore_isolation)
    return {"status": "restoring", "message": "Process isolation restoring. Recovery in 30-60s.", "breach_active": False}

@app.get("/demo/status")
def demo_status():
    """Return current demo breach status."""
    return {"breach_active": _breach_active}

@app.post("/exfil/ingest")
async def exfil_ingest(request: Request):
    payload = await request.json()
    with _exfil_lock:
        _exfil_store.append(payload)
    return {"status": "ok"}

@app.get("/exfil/latest")
def exfil_latest(limit: int = Query(10, ge=1, le=50)):
    with _exfil_lock:
        items = list(_exfil_store)[-limit:]
    return {"items": items}


# ── Model status (Capstone 4) ─────────────────────────────────────────────────

@app.get("/model/status")
def model_status():
    with _bundle_lock:
        b = dict(_bundle)
    if not b:
        return {"status": "model not loaded"}

    trained_at  = b.get("trained_at")
    age_minutes = round((time.time() - trained_at) / 60, 1) if trained_at else None

    try:
        conn  = get_db()
        rows  = conn.execute(
            "SELECT anomaly_score FROM metrics "
            "WHERE anomaly_score IS NOT NULL ORDER BY timestamp DESC LIMIT 100"
        ).fetchall()
        conn.close()
        drift_ratio = (
            sum(1 for r in rows if r["anomaly_score"] < 0) / len(rows)
            if rows else 0.0
        )
    except Exception:
        drift_ratio = None

    last_retrain = _sb_fetch_retrain_log(limit=1)

    return {
        "trained_at":          trained_at,
        "age_minutes":         age_minutes,
        "n_samples":           b.get("n_samples"),
        "drift_ratio_100rows": round(drift_ratio, 3) if drift_ratio is not None else None,
        "drift_strikes":       _drift_strikes,
        "drift_threshold":     DRIFT_THRESHOLD,
        "last_retrain":        last_retrain[0] if last_retrain else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# AI LAYER ENDPOINTS  (arm's-length — read-only data, returns text/JSON only)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/ai/status")
def ai_status():
    """Check whether the AI layer is configured and ready."""
    import os as _os
    key_set = bool(_os.environ.get("GROQ_API_KEY", ""))
    return {
        "ai_available":  key_set,
        "model":         _os.environ.get("AI_MODEL", "llama-3.3-70b-versatile"),
        "forecaster_rows": int(_os.environ.get("AI_FORECASTER_ROWS", "50")),
        "message": (
            "AI layer active — predictive forecasting and incident reasoning enabled."
            if key_set else
            "GROQ_API_KEY not set. Add it to docker-compose.yml or .env."
        ),
    }


@app.get("/ai/forecast")
def ai_forecast(
    slice_id: str = Query(..., description="Slice to analyze, e.g. slice-a"),
    limit:    int = Query(50, ge=10, le=100),
):
    """
    Predictive breach forecaster (AI Feature A).

    Reads the last `limit` telemetry rows for the slice and asks Claude
    to reason about whether a breach is trending. Returns a risk_level
    of stable | rising | critical BEFORE the IsolationForest fires.

    The AI never touches the database directly — this endpoint reads
    /metrics/history data and passes it as a JSON payload to the API.
    """
    try:
        from ai.layer import predict_breach_risk
    except ImportError as exc:
        raise HTTPException(500, f"AI layer not installed: {exc}")

    conn = get_db()
    rows = conn.execute(
        "SELECT timestamp, cpu_pct, mem_mb, net_rx_kb, net_tx_kb, anomaly_score "
        "FROM metrics WHERE slice_id=? ORDER BY timestamp DESC LIMIT ?",
        (slice_id, limit),
    ).fetchall()
    conn.close()

    history = list(reversed([dict(r) for r in rows]))  # oldest → newest
    result  = predict_breach_risk(slice_id, history)
    return result


@app.get("/ai/incident/{incident_id}/reason")
def ai_incident_reason(incident_id: int):
    """
    Autonomous incident reasoner (AI Feature B).

    Fetches a closed incident by ID, loads the telemetry window around
    the breach, and returns (or triggers generation of) an AI forensic
    hypothesis. If the note already exists in the DB it is returned
    immediately. If not, it is generated on-demand and cached.

    The AI never modifies core system state — only writes ai_forensic_note
    to the incidents table.
    """
    try:
        from ai.layer import reason_about_incident
    except ImportError as exc:
        raise HTTPException(500, f"AI layer not installed: {exc}")

    conn = get_db()
    inc_row = conn.execute(
        "SELECT * FROM incidents WHERE id=?", (incident_id,)
    ).fetchone()

    if not inc_row:
        conn.close()
        raise HTTPException(404, f"Incident {incident_id} not found.")

    inc = dict(inc_row)

    # Return cached note if already generated
    if inc.get("ai_forensic_note"):
        conn.close()
        import json as _json
        try:
            return {"incident_id": incident_id, "note": _json.loads(inc["ai_forensic_note"]), "cached": True}
        except Exception:
            return {"incident_id": incident_id, "note": inc["ai_forensic_note"], "cached": True}

    # Generate on-demand for open incidents or ones that missed the auto-trigger
    started = inc.get("started_at", 0)
    ended   = inc.get("resolved_at") or time.time()

    window_rows = conn.execute(
        "SELECT timestamp, cpu_pct, mem_mb, net_rx_kb, net_tx_kb, anomaly_score "
        "FROM metrics WHERE slice_id=? AND timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp ASC LIMIT 30",
        (inc["slice_id"], started - 30, ended + 5)
    ).fetchall()
    telemetry = [dict(r) for r in window_rows]

    note = reason_about_incident(inc, telemetry)

    if note:
        conn.execute(
            "UPDATE incidents SET ai_forensic_note=? WHERE id=?",
            (note, incident_id)
        )
        conn.commit()

    conn.close()

    import json as _json
    try:
        parsed = _json.loads(note) if note else None
    except Exception:
        parsed = note

    return {
        "incident_id": incident_id,
        "note":        parsed,
        "cached":      False,
    }