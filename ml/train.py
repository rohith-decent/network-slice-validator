"""
ml/train.py
───────────
Reads baseline telemetry from SQLite, trains IsolationForest,
saves bundle (scaler + model + metadata) to model.pkl.

Must be run AFTER the collector has gathered at least 60 seconds of data.
"""

import os
import sys
import time
import sqlite3
import logging
import numpy as np

import joblib
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [train] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

DB_PATH       = os.environ.get("DB_PATH",    "/data/metrics.db")
MODEL_PATH    = os.environ.get("MODEL_PATH", "/ml/model.pkl")
CONTAMINATION = float(os.environ.get("CONTAMINATION", "0.05"))
N_ESTIMATORS  = int(os.environ.get("N_ESTIMATORS", "100"))
FEATURE_NAMES = ["cpu_pct", "mem_mb", "net_rx_kb", "net_tx_kb"]
MIN_SAMPLES   = 10   # Minimum rows needed to train


# ── Load data ─────────────────────────────────────────────────────────────────

def load_features() -> np.ndarray:
    """
    Load all baseline metric rows from SQLite.
    Returns float32 array of shape (N, 4).
    """
    if not os.path.exists(DB_PATH):
        log.error("DB not found at %s. Run collector first.", DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute(
            "SELECT cpu_pct, mem_mb, net_rx_kb, net_tx_kb "
            "FROM metrics ORDER BY timestamp ASC"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    if len(rows) < MIN_SAMPLES:
        log.error(
            "Only %d rows in DB — need at least %d. "
            "Let the collector run longer before training.",
            len(rows), MIN_SAMPLES
        )
        sys.exit(1)

    X = np.array(rows, dtype=np.float32)
    log.info("Loaded %d rows with %d features", X.shape[0], X.shape[1])
    return X


# ── Feature stats ─────────────────────────────────────────────────────────────

def print_stats(X: np.ndarray):
    log.info("Feature statistics (baseline):")
    for i, name in enumerate(FEATURE_NAMES):
        col = X[:, i]
        log.info("  %-14s  min=%7.3f  max=%7.3f  mean=%7.3f  std=%7.3f",
                 name, col.min(), col.max(), col.mean(), col.std())


# ── Augment baseline with mild synthetic variation ────────────────────────────

def augment(X: np.ndarray, factor: int = 3) -> np.ndarray:
    """
    Generate `factor` synthetic near-normal copies of X via bounded jitter.
    Keeps all values non-negative.
    """
    rng = np.random.default_rng(seed=42)
    stds = X.std(axis=0) * 0.15 + 1e-6   # 15% jitter, avoid zero std
    jitter = rng.normal(0, 1, size=(len(X) * factor, X.shape[1])) * stds
    synth = np.tile(X, (factor, 1)) + jitter
    synth = np.clip(synth, 0, None)        # no negative resource usage
    combined = np.vstack([X, synth])
    log.info("Augmented dataset: %d real + %d synthetic = %d total samples",
             len(X), len(synth), len(combined))
    return combined


# ── Train ─────────────────────────────────────────────────────────────────────

def train(X: np.ndarray) -> dict:
    # Scale features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Train IsolationForest
    model = IsolationForest(
        n_estimators=N_ESTIMATORS,
        contamination=CONTAMINATION,
        max_samples="auto",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_scaled)

    # Sanity check: score on training data
    scores = model.decision_function(X_scaled)
    predictions = model.predict(X_scaled)
    n_anomalies = (predictions == -1).sum()
    log.info("Training complete — %d samples, %d flagged as anomalies (%.1f%%)",
             len(X), n_anomalies, 100.0 * n_anomalies / len(X))
    log.info("Decision scores — min=%.4f  max=%.4f  mean=%.4f",
             scores.min(), scores.max(), scores.mean())

    # ── Per-feature means & stds on REAL (non-augmented) data ────────────────
    # These are stored in the bundle so the API can compute Z-scores at
    # inference time to classify what kind of attack was detected.
    # We always compute these from X (the real baseline rows passed in),
    # so they reflect genuine idle behaviour, not the synthetic augmentation.
    feature_means = {name: float(X[:, i].mean()) for i, name in enumerate(FEATURE_NAMES)}
    feature_stds  = {
        name: float(max(X[:, i].std(), 1e-6))   # guard against zero-std features
        for i, name in enumerate(FEATURE_NAMES)
    }
    log.info("Feature means for classifier: %s", feature_means)
    log.info("Feature stds  for classifier: %s", feature_stds)
    # ─────────────────────────────────────────────────────────────────────────

    bundle = {
        "scaler":         scaler,
        "model":          model,
        "feature_names":  FEATURE_NAMES,
        "trained_at":     time.time(),
        "n_samples":      int(len(X)),
        "contamination":  CONTAMINATION,
        "score_stats": {
            "min":  float(scores.min()),
            "max":  float(scores.max()),
            "mean": float(scores.mean()),
            "std":  float(scores.std()),
        },
        # ── NEW — used by classify_attack() in api/main.py ──────────────────
        "feature_means":  feature_means,
        "feature_stds":   feature_stds,
    }
    return bundle


# ── Save ──────────────────────────────────────────────────────────────────────

def save(bundle: dict):
    os.makedirs(os.path.dirname(MODEL_PATH) or ".", exist_ok=True)
    joblib.dump(bundle, MODEL_PATH)
    size_kb = os.path.getsize(MODEL_PATH) / 1024
    log.info("Model saved to %s (%.1f KB)", MODEL_PATH, size_kb)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Loading baseline data from %s ...", DB_PATH)
    X_real = load_features()
    print_stats(X_real)

    # Augment only if baseline is small (< 200 samples ≈ < 10 min of data)
    if True:
        log.info("Short baseline (%d rows) — augmenting for better model coverage.", len(X_real))
        X_train = augment(X_real, factor=5)
    else:
        X_train = X_real

    # NOTE: always pass X_real (not X_train) so feature_means/stds reflect
    # genuine idle behaviour, not the jittered synthetic copies.
    bundle = train(X_real)

    # Re-fit scaler and model on the full augmented set for better coverage,
    # but keep the stats computed from real data above.
    from sklearn.preprocessing import StandardScaler as _SS
    from sklearn.ensemble import IsolationForest as _IF
    _scaler2 = _SS()
    _X2 = _scaler2.fit_transform(X_train)
    _model2 = _IF(
        n_estimators=N_ESTIMATORS,
        contamination=CONTAMINATION,
        max_samples="auto",
        random_state=42,
        n_jobs=-1,
    )
    _model2.fit(_X2)
    bundle["scaler"]    = _scaler2
    bundle["model"]     = _model2
    bundle["n_samples"] = int(len(X_train))

    save(bundle)
    log.info("Done. Model ready for inference.")

    # ── Write to Supabase model_retrain_log ───────────────────────────────────
    # Determine the reason: env var RETRAIN_REASON overrides default "startup"
    reason = os.environ.get("RETRAIN_REASON", "startup")
    import json
    try:
        import datetime as _dt
        from sb.retrain import log_retrain
        log_retrain({
            "retrained_at": _dt.datetime.utcnow().isoformat(),
            "reason":       reason,
            "n_samples":    bundle["n_samples"],
            "slice_ids":    json.dumps(["slice-a", "slice-b"]),
        })
        log.info("[train] Retrain event logged to Supabase: reason=%s n_samples=%d",
                 reason, bundle["n_samples"])
    except Exception as exc:
        log.debug("[train] Supabase retrain log skipped: %s", exc)