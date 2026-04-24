"""
collector/simulate.py
─────────────────────
Simulates two 5G network slices (slice-a, slice-b) WITHOUT Docker.
Writes realistic CPU, memory, and network metrics to SQLite every 5 seconds.
Also supports injecting a breach scenario to trigger the AI anomaly detector.
"""

import os
import sys
import time
import math
import random
import sqlite3
import logging
import argparse
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [simulator] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH  = os.environ.get("DB_PATH",  r"D:\hackathon\slice-monitor\data\metrics.db")
INTERVAL = int(os.environ.get("COLLECTOR_INTERVAL", "5"))

# ── Breach flag (toggled by keyboard input thread) ────────────────────────────
_breach_active = False
_breach_lock   = threading.Lock()


def is_breach() -> bool:
    with _breach_lock:
        return _breach_active


def set_breach(val: bool):
    global _breach_active
    with _breach_lock:
        _breach_active = val


# ── DB setup ──────────────────────────────────────────────────────────────────
def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
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


def insert_row(conn, ts, slice_id, cpu_pct, mem_mb, net_rx_kb, net_tx_kb):
    conn.execute(
        "INSERT INTO metrics (timestamp, slice_id, cpu_pct, mem_mb, net_rx_kb, net_tx_kb) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ts, slice_id, cpu_pct, mem_mb, net_rx_kb, net_tx_kb),
    )
    conn.commit()


# ── Metric generators ─────────────────────────────────────────────────────────
# Each slice has a "personality" — IoT slice-a is low and steady,
# Enterprise slice-b is busier with more traffic.

_tick = 0  # global counter for wave patterns

def gen_slice_a(breach: bool) -> tuple:
    """IoT slice — normally quiet: ~5-15% CPU, ~30-50MB RAM, low network."""
    global _tick
    base_cpu = 8.0 + 4.0 * math.sin(_tick * 0.3)
    base_mem = 38.0 + 6.0 * math.sin(_tick * 0.1)
    base_rx  = 0.8 + 0.4 * random.random()
    base_tx  = 0.5 + 0.3 * random.random()

    if breach:
        # Breach: sudden CPU spike + massive cross-slice network flood
        cpu     = base_cpu + random.uniform(40, 65)   # CPU jumps to 50-80%
        mem     = base_mem + random.uniform(20, 40)   # RAM pressure
        net_rx  = base_rx  + random.uniform(80, 200)  # cross-slice flood
        net_tx  = base_tx  + random.uniform(60, 150)
    else:
        cpu    = max(0.1, base_cpu + random.uniform(-1.5, 1.5))
        mem    = max(1.0, base_mem + random.uniform(-2.0, 2.0))
        net_rx = max(0.0, base_rx)
        net_tx = max(0.0, base_tx)

    return round(cpu, 2), round(mem, 2), round(net_rx, 3), round(net_tx, 3)


def gen_slice_b(breach: bool) -> tuple:
    """Enterprise slice — moderate baseline: ~15-30% CPU, ~60-80MB RAM."""
    global _tick
    base_cpu = 20.0 + 8.0 * math.sin(_tick * 0.2 + 1.0)
    base_mem = 68.0 + 8.0 * math.cos(_tick * 0.15)
    base_rx  = 3.5 + 1.5 * random.random()
    base_tx  = 2.0 + 1.0 * random.random()

    if breach:
        # Breach: memory pressure + abnormal traffic spike
        cpu     = base_cpu + random.uniform(25, 45)
        mem     = base_mem + random.uniform(30, 50)
        net_rx  = base_rx  + random.uniform(100, 250)
        net_tx  = base_tx  + random.uniform(80, 180)
    else:
        cpu    = max(0.1, base_cpu + random.uniform(-2.0, 2.0))
        mem    = max(1.0, base_mem + random.uniform(-3.0, 3.0))
        net_rx = max(0.0, base_rx)
        net_tx = max(0.0, base_tx)

    return round(cpu, 2), round(mem, 2), round(net_rx, 3), round(net_tx, 3)


# ── Keyboard control thread ───────────────────────────────────────────────────
def keyboard_listener():
    """
    Runs in background. Type commands and press Enter:
      b  = toggle breach ON/OFF
      q  = quit simulator
    """
    print("\n" + "="*55)
    print("  SIMULATOR CONTROLS (type command + Enter):")
    print("  b = INJECT BREACH (anomaly scenario)")
    print("  n = RESTORE NORMAL (clear breach)")
    print("  q = QUIT simulator")
    print("="*55 + "\n")
    while True:
        try:
            cmd = input().strip().lower()
            if cmd == "b":
                set_breach(True)
                log.warning("🚨 BREACH INJECTED — anomaly metrics now active!")
            elif cmd == "n":
                set_breach(False)
                log.info("✅ BREACH CLEARED — returning to normal metrics.")
            elif cmd == "q":
                log.info("Quit command received. Stopping simulator.")
                os._exit(0)
            else:
                print("  Unknown command. Use: b=breach  n=normal  q=quit")
        except (EOFError, KeyboardInterrupt):
            break


# ── Main loop ─────────────────────────────────────────────────────────────────
def run(duration_seconds=None):
    global _tick
    log.info("Starting 5G slice simulator | DB=%s | interval=%ds", DB_PATH, INTERVAL)
    log.info("Simulating: slice-a (IoT) and slice-b (Enterprise)")

    conn    = get_db()
    start   = time.time()
    cycles  = 0

    # Start keyboard listener in background thread
    t = threading.Thread(target=keyboard_listener, daemon=True)
    t.start()

    try:
        while True:
            ts     = time.time()
            breach = is_breach()
            _tick += 1

            cpu_a, mem_a, rx_a, tx_a = gen_slice_a(breach)
            cpu_b, mem_b, rx_b, tx_b = gen_slice_b(breach)

            insert_row(conn, ts, "slice-a", cpu_a, mem_a, rx_a, tx_a)
            insert_row(conn, ts, "slice-b", cpu_b, mem_b, rx_b, tx_b)

            cycles += 1
            status = "🚨 BREACH" if breach else "✅ normal"
            log.info(
                "Cycle %3d [%s] | "
                "slice-a cpu=%.1f%% mem=%.1fMB rx=%.2fKB | "
                "slice-b cpu=%.1f%% mem=%.1fMB rx=%.2fKB",
                cycles, status,
                cpu_a, mem_a, rx_a,
                cpu_b, mem_b, rx_b,
            )

            if duration_seconds and (time.time() - start) >= duration_seconds:
                log.info("Duration %ds reached — stopping.", duration_seconds)
                break

            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        log.info("Simulator stopped.")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=None,
                        help="Run for N seconds then exit (used for baseline collection)")
    args = parser.parse_args()
    run(duration_seconds=args.duration)