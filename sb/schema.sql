-- =============================================================
-- supabase/schema.sql
-- Run this ONCE in the Supabase SQL Editor.
-- Creates all three tables needed for Capstone 3 (SLA) and
-- Capstone 4 (Drift Detection + Auto-Retrain).
-- =============================================================


-- -----------------------------------------------------------
-- 1. sla_metrics
--    One row per collector sample.  Mirrors SQLite metrics
--    but lives in Supabase for SLA queries and retraining.
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS sla_metrics (
    id             BIGSERIAL    PRIMARY KEY,
    slice_id       TEXT         NOT NULL,
    cpu_pct        FLOAT        NOT NULL,
    mem_mb         FLOAT        NOT NULL,
    net_rx_kb      FLOAT        NOT NULL DEFAULT 0,
    net_tx_kb      FLOAT        NOT NULL DEFAULT 0,
    anomaly_score  FLOAT,
    is_anomaly     BOOLEAN      NOT NULL DEFAULT FALSE,
    confidence     FLOAT,                        -- 0-100 Isolation Confidence
    attack_type    TEXT,                         -- NULL when no anomaly
    sampled_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Fast per-slice time-range queries (SLA window lookups)
CREATE INDEX IF NOT EXISTS idx_sla_metrics_slice_time
    ON sla_metrics (slice_id, sampled_at DESC);


-- -----------------------------------------------------------
-- 2. incidents
--    Structured incident records created by the correlator.
--    Compatible with the Capstone 2 schema in the project doc.
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS incidents (
    id             BIGSERIAL    PRIMARY KEY,
    slice_id       TEXT         NOT NULL,
    attack_type    TEXT,
    started_at     TIMESTAMPTZ  NOT NULL,
    resolved_at    TIMESTAMPTZ,
    peak_score     FLOAT,
    min_confidence FLOAT,
    duration_s     FLOAT,
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE
);

-- Fast active-incident lookups per slice
CREATE INDEX IF NOT EXISTS idx_incidents_active
    ON incidents (slice_id, is_active, started_at DESC);


-- -----------------------------------------------------------
-- 3. model_retrain_log
--    One row per retrain event — audit trail for Capstone 4.
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_retrain_log (
    id           BIGSERIAL    PRIMARY KEY,
    retrained_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    reason       TEXT         NOT NULL,   -- startup | drift_detected | manual
    n_samples    INTEGER      NOT NULL,
    slice_ids    TEXT         NOT NULL    -- JSON array e.g. '["slice-a","slice-b"]'
);