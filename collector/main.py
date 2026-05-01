"""
collector/main.py
─────────────────
Polls Docker stats every COLLECTOR_INTERVAL seconds.
Writes real CPU%, memory MB, and network delta KB to SQLite.
NO random/fake data — all values from actual container resources.
"""

import os
import sys
import time
import json
import sqlite3
import logging
import argparse
import subprocess
from typing import Optional

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [collector] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

DB_PATH          = os.environ.get("DB_PATH", "/data/metrics.db")
INTERVAL         = int(os.environ.get("COLLECTOR_INTERVAL", "5"))
SLICE_NAMES_ENV  = os.environ.get("SLICE_NAMES", "slice-a,slice-b")
SLICE_NAMES      = [s.strip() for s in SLICE_NAMES_ENV.split(",")]


# ── DB helpers ──────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
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
    """)
    conn.commit()
    return conn


def insert_row(conn: sqlite3.Connection, ts: float, slice_id: str,
               cpu_pct: float, mem_mb: float, net_rx_kb: float, net_tx_kb: float):
    conn.execute(
        "INSERT INTO metrics (timestamp, slice_id, cpu_pct, mem_mb, net_rx_kb, net_tx_kb) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ts, slice_id, cpu_pct, mem_mb, net_rx_kb, net_tx_kb),
    )
    conn.commit()


# ── Docker stats scraping ───────────────────────────────────────────────────

def run_docker_stats() -> list[dict]:
    """
    Run `docker stats --no-stream --format json` and return parsed list.
    Returns empty list on any failure.
    """
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             '{"name":"{{.Name}}","cpu":"{{.CPUPerc}}","mem":"{{.MemUsage}}","net":"{{.NetIO}}"}'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            log.warning("docker stats exited %d: %s", result.returncode, result.stderr.strip())
            return []
        rows = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as e:
                    log.debug("JSON parse error: %s | line: %s", e, line)
        return rows
    except subprocess.TimeoutExpired:
        log.warning("docker stats timed out")
        return []
    except FileNotFoundError:
        log.error("docker binary not found — is Docker socket mounted?")
        return []
    except Exception as e:
        log.error("Unexpected error in docker stats: %s", e)
        return []


def parse_cpu_pct(raw: str) -> Optional[float]:
    """'12.34%' → 12.34"""
    try:
        return float(raw.strip().replace("%", ""))
    except (ValueError, AttributeError):
        return None


def parse_mem_mb(raw: str) -> Optional[float]:
    """
    '45.6MiB / 128MiB' → 45.6
    Handles MiB, GiB, kB, MB, GB.
    """
    try:
        used = raw.split("/")[0].strip()
        if "GiB" in used or "GB" in used:
            num = float(used.replace("GiB", "").replace("GB", "").strip())
            return num * 1024.0
        elif "MiB" in used or "MB" in used:
            num = float(used.replace("MiB", "").replace("MB", "").strip())
            return num
        elif "kB" in used or "KiB" in used or "KB" in used:
            num = float(used.replace("kB", "").replace("KiB", "").replace("KB", "").strip())
            return num / 1024.0
        elif "B" in used:
            num = float(used.replace("B", "").strip())
            return num / (1024.0 * 1024.0)
        return None
    except (ValueError, IndexError, AttributeError):
        return None


def parse_net_bytes(raw: str) -> tuple[float, float]:
    """
    '1.2kB / 3.4MB' → (rx_bytes, tx_bytes)
    Returns (0.0, 0.0) on parse failure.
    """
    def to_bytes(s: str) -> float:
        s = s.strip()
        try:
            if "GB" in s or "GiB" in s:
                return float(s.replace("GB","").replace("GiB","").strip()) * 1e9
            elif "MB" in s or "MiB" in s:
                return float(s.replace("MB","").replace("MiB","").strip()) * 1e6
            elif "kB" in s or "KB" in s or "KiB" in s:
                return float(s.replace("kB","").replace("KB","").replace("KiB","").strip()) * 1e3
            elif "B" in s:
                return float(s.replace("B","").strip())
            return 0.0
        except ValueError:
            return 0.0

    try:
        parts = raw.split("/")
        rx = to_bytes(parts[0])
        tx = to_bytes(parts[1]) if len(parts) > 1 else 0.0
        return rx, tx
    except Exception:
        return 0.0, 0.0


# ── State for computing network deltas ─────────────────────────────────────

_prev_net: dict[str, tuple[float, float]] = {}  # slice_id → (prev_rx_bytes, prev_tx_bytes)


def compute_net_delta_kb(slice_id: str, rx_bytes: float, tx_bytes: float) -> tuple[float, float]:
    """
    Returns (delta_rx_kb, delta_tx_kb) since last sample.
    First call returns (0, 0) — we can't know the delta without a baseline.
    """
    global _prev_net
    if slice_id not in _prev_net:
        _prev_net[slice_id] = (rx_bytes, tx_bytes)
        return 0.0, 0.0
    prev_rx, prev_tx = _prev_net[slice_id]
    delta_rx = max(0.0, rx_bytes - prev_rx) / 1024.0
    delta_tx = max(0.0, tx_bytes - prev_tx) / 1024.0
    _prev_net[slice_id] = (rx_bytes, tx_bytes)
    return delta_rx, delta_tx


# ── Main collection loop ─────────────────────────────────────────────────────

def collect_once(conn: sqlite3.Connection) -> int:
    """
    Single collection pass. Returns number of rows written.
    """
    ts = time.time()
    stats = run_docker_stats()
    if not stats:
        log.warning("No stats returned from Docker — skipping cycle.")
        return 0

    written = 0
    for row in stats:
        name = row.get("name", "").strip()
        if name not in SLICE_NAMES:
            continue

        cpu_pct = parse_cpu_pct(row.get("cpu", "0%"))
        mem_mb  = parse_mem_mb(row.get("mem", "0B / 0B"))
        rx_raw, tx_raw = parse_net_bytes(row.get("net", "0B / 0B"))
        net_rx_kb, net_tx_kb = compute_net_delta_kb(name, rx_raw, tx_raw)

        if cpu_pct is None or mem_mb is None:
            log.warning("Failed to parse stats for %s: %s", name, row)
            continue

        insert_row(conn, ts, name, cpu_pct, mem_mb, net_rx_kb, net_tx_kb)
        log.debug("Wrote [%s] cpu=%.2f%% mem=%.2fMB rx=%.2fKB tx=%.2fKB",
                  name, cpu_pct, mem_mb, net_rx_kb, net_tx_kb)
        written += 1

    return written


def run(duration_seconds: Optional[int] = None):
    """
    Main loop. If duration_seconds is set, stops after that many seconds.
    Otherwise runs indefinitely.
    """
    log.info("Starting collector | processes=%s interval=%ds db=%s",
             SLICE_NAMES, INTERVAL, DB_PATH)
    conn = get_db()
    start = time.time()
    cycles = 0

    try:
        while True:
            written = collect_once(conn)
            cycles += 1
            if written > 0:
                log.info("Cycle %d: wrote %d rows", cycles, written)
            else:
                log.info("Cycle %d: no rows written (processes may still be starting)", cycles)

            if duration_seconds and (time.time() - start) >= duration_seconds:
                log.info("Duration %ds elapsed — stopping baseline collection.", duration_seconds)
                break

            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        log.info("Collector stopped by user.")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=None,
                        help="Run for N seconds then exit (for baseline collection)")
    args = parser.parse_args()
    run(duration_seconds=args.duration)
