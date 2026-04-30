"""
api/main.py
───────────
FastAPI scoring API for 5G Slice Isolation Validator.

Endpoints (all original preserved + new):
  GET  /health                   – liveness + model status
  GET  /score                    – latest scored sample(s) with attack classification
  GET  /metrics/history          – last N rows per slice
  POST /reload-model             – hot-reload model.pkl from disk
  POST /inject-attack            – software-inject anomaly rows into SQLite for demo
  GET  /inject-attack/status     – list slices with active injections
  GET  /incidents                – structured incident log (SQLite + Supabase mirror)
  GET  /incidents/export         – CSV download of incident log
  GET  /audit-log                – paginated anomaly audit table
  GET  /sla                      – SLA compliance % over rolling windows
  GET  /model/status             – Capstone 4: model age, drift rate, retrain log
"""

import io
import os
import time
import sqlite3
import asyncio
import logging
import threading
import datetime as _dt
import joblib
import numpy as np

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [api] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

DB_PATH       = os.environ.get("DB_PATH",    "/data/metrics.db")
MODEL_PATH    = os.environ.get("MODEL_PATH", "/ml/model.pkl")
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
            is_active      INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_inc_slice ON incidents(slice_id, started_at);
        CREATE TABLE IF NOT EXISTS drift_config (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
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

    if len(elevated) >= 2:
        return "Combined Attack"
    if "cpu_pct" in elevated:
        return "CPU Starvation"
    if "mem_mb" in elevated:
        return "Memory Exhaustion"
    if "net_rx_kb" in elevated or "net_tx_kb" in elevated:
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
        log.info("Incident OPENED for %s (%s)", slice_id, attack_type)

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
            send_attack_alert(
                slice_id=slice_id,
                attack_type=attack_type,
                confidence=min_conf,
                features=features_dict,
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
        log.info("Incident CLOSED for %s after %.1fs", slice_id, duration)

        _sb_close_incident(
            slice_id,
            _dt.datetime.utcfromtimestamp(now).isoformat(),
            duration,
        )


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

_active_injections: dict[str, asyncio.Task] = {}
_injection_lock = asyncio.Lock()


async def _run_injection(slice_id: str, attack_type: str, duration_s: int = 60):
    profile      = ATTACK_PROFILES.get(attack_type, ATTACK_PROFILES["cpu"])
    end_time     = time.time() + duration_s
    first_row    = True
    log.info("Injection started: slice=%s type=%s duration=%ds",
             slice_id, attack_type, duration_s)
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
                    send_attack_alert(
                        slice_id=slice_id,
                        attack_type=classified_at,
                        confidence=confidence,
                        features=vals,
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
                    log.info("[inject] Supabase incident opened: slice=%s type=%s",
                             slice_id, classified_at)

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
        log.info("Injection finished: slice=%s", slice_id)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_bundle()
    asyncio.create_task(correlator_loop())
    asyncio.create_task(drift_detector_loop())
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="5G Slice Isolation API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        "message":     (f"Injecting {attack_type} into {slice_id} for {duration_s}s. "
                        "Watch the gauge drop!"),
    }


@app.get("/inject-attack/status")
async def injection_status():
    async with _injection_lock:
        active = list(_active_injections.keys())
    return {"active_injections": active}


# ── Incidents ─────────────────────────────────────────────────────────────────

@app.get("/incidents")
def get_incidents(limit: int = Query(50, ge=1, le=500)):
    sb_rows = _sb_fetch_incidents(limit)
    if sb_rows:
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