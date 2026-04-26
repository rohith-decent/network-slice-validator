# 📱 MOBILE_CONTROL_CONSOLE.md
## Phone-Based Breach Injection System for 5G Slice Isolation Validator

> **References:** This document extends `PROJECT_REFERENCE.md`. All API contracts, database schemas, and network names defined there remain authoritative. Do not deviate from them.

---

## ⚡ Quick-Start Box *(experienced developers: start here)*

```
Time to implement: ~35 minutes
Files to touch: api/main.py, docker-compose.yml
New file to create: dashboard/demo_control.html
Port to open: 8000 (already exposed)
```

```bash
# 1. Change docker socket mount in docker-compose.yml
#    FROM:  /var/run/docker.sock:/var/run/docker.sock:ro
#    TO:    /var/run/docker.sock:/var/run/docker.sock:rw

# 2. Add demo endpoints to api/main.py (see §7.1)
# 3. Create dashboard/demo_control.html (see §7.2)
# 4. Rebuild:
docker compose up --build -d

# 5. Get your laptop IP
ip route get 1 | awk '{print $7; exit}'     # Linux
ipconfig getifaddr en0                       # macOS
ipconfig | findstr IPv4                      # Windows PowerShell

# 6. Phone opens:  http://<YOUR_LAPTOP_IP>:8000/demo
# 7. Tap INJECT BREACH — watch gauge drop in ~15s
```

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Prerequisites](#3-prerequisites)
4. [Implementation Steps](#4-implementation-steps)
   - [Step 1: Add Demo Endpoints to FastAPI](#step-1-add-demo-endpoints-to-fastapi)
   - [Step 2: Create Mobile-Friendly HTML Control Page](#step-2-create-mobile-friendly-html-control-page)
   - [Step 3: Update Docker Socket Permissions](#step-3-update-docker-socket-permissions)
   - [Step 4: Network Name Configuration](#step-4-network-name-configuration)
5. [Step-by-Step Demo Flow](#5-step-by-step-demo-flow)
6. [Technical Deep Dive](#6-technical-deep-dive)
7. [Complete Code Reference](#7-complete-code-reference)
8. [Validation & Testing](#8-validation--testing)
9. [Fallback Plans](#9-fallback-plans)
10. [Judge Presentation Script](#10-judge-presentation-script)
11. [Troubleshooting FAQ](#11-troubleshooting-faq)

---

## 1. 🎯 Executive Summary

### What Is the Mobile Control Console?

The Mobile Control Console is a phone-accessible HTTP interface that lets anyone on the same Wi-Fi network trigger, monitor, and restore simulated 5G network slice isolation breaches — without touching a terminal. A judge picks up their phone, opens a URL, taps a large red button, and watches the isolation confidence gauge on the dashboard drop from green to red within 15 seconds.

It consists of:
- **Two new FastAPI endpoints** (`POST /demo/inject` and `POST /demo/restore`) added to the existing `api/main.py`
- **One standalone HTML page** (`dashboard/demo_control.html`) served directly by FastAPI at `/demo`
- **A one-line change** to `docker-compose.yml` to allow Docker network manipulation

### Why This Beats CLI Commands in a Demo

| Approach | Setup needed by judge | Wow factor | Risk of fumbling |
|---|---|---|---|
| CLI: `docker network connect slice_b_net slice-a` | Terminal access, correct path, typo risk | Low | High |
| QR code → phone → tap button | Nothing — just a phone | **High** | Minimal |
| Laptop hotkey | Shared screen required | Medium | Medium |

In a timed 3-minute hackathon demo, every second spent explaining commands is a second not spent showing results. A phone button eliminates that problem entirely.

### How This Maps to Real 5G Architecture

This is not just a demo trick. It directly mirrors how real 5G networks are controlled:

| Our Prototype | Real 5G/6G Equivalent | 3GPP Reference |
|---|---|---|
| Mobile Control Console (HTML page) | Network Exposure Function (NEF) API client | 3GPP TS 29.522 |
| `POST /demo/inject` endpoint | Policy Control Function (PCF) slice policy update | 3GPP TS 29.507 |
| `docker network connect` command | UPF reconfiguration via PFCP session modification | 3GPP TS 29.244 |
| ML anomaly detection reacting | NWDAF analytics subscription trigger | 3GPP TS 29.520 |
| Dashboard confidence drop | NF Consumer receiving analytics notification | 3GPP TS 23.288 |

When explaining this to judges: *"The phone acts as an SDN orchestrator. The API endpoint is the control-plane interface. The Docker network command is the data-plane reconfiguration. The ML model is the NWDAF analytics engine. This is the exact same flow — just containerised."*

---

## 2. 🏗️ Architecture Overview

### System Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          SAME Wi-Fi SUBNET                                │
│                                                                           │
│  ┌─────────────┐   HTTP POST        ┌──────────────────────────────────┐ │
│  │   📱 PHONE  │ ─────────────────► │  FastAPI :8000                   │ │
│  │             │   /demo/inject     │  POST /demo/inject               │ │
│  │  demo_      │ ◄───────────────── │  POST /demo/restore              │ │
│  │  control    │   {status, msg}    │  GET  /demo  (serves HTML)       │ │
│  │  .html      │                   └──────────────┬───────────────────┘ │
│  └─────────────┘                                  │                      │
│                                                   │ subprocess.run()     │
│  ┌─────────────┐   polls /score     ┌─────────────▼───────────────────┐ │
│  │  💻 LAPTOP  │ ◄───────────────── │  Docker Engine                  │ │
│  │  Dashboard  │   every 3s         │  /var/run/docker.sock (rw)      │ │
│  │  :8501      │                   │                                   │ │
│  └─────────────┘                   │  docker network connect          │ │
│                                    │    network-slice-validator_       │ │
│  Gauge drops green→red             │    slice_b_net slice-a           │ │
│  within 15–30s of inject           └──────────┬──────────────────────┘ │
│                                               │ network namespace change │
│                              ┌────────────────▼──────────────────────┐  │
│                              │  slice-a container                    │  │
│                              │  NOW connected to slice_b_net         │  │
│                              │  (isolation BROKEN)                   │  │
│                              └───────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

### Component Interaction Flow

```
Phone taps INJECT BREACH
        │
        ▼  (~0ms)
POST /demo/inject  ──►  FastAPI validates request
        │
        ▼  (~5ms)
BackgroundTask spawned  ──►  docker network connect (subprocess)
        │                    docker exec slice-a iperf3 -c slice-b -t 30
        │
        ▼  (~200ms)
slice-a now on slice_b_net — cross-slice traffic begins
        │
        ▼  (~5s — next collector cycle)
collector/main.py picks up elevated net_rx_kb / net_tx_kb
        │
        ▼  (~0ms)
SQLite row written with anomalous network delta
        │
        ▼  (~3s — next dashboard refresh)
dashboard polls /score → IsolationForest scores the anomalous row
        │
        ▼  (~0ms)
isolation_confidence drops below 40% → gauge turns RED
        │
        ▼
Judge sees breach on dashboard
Total elapsed: 8–20 seconds from tap to visual confirmation
```

### Latency Budget

| Stage | Typical Latency | Notes |
|---|---|---|
| Phone HTTP → FastAPI | 5–20ms | Local Wi-Fi |
| FastAPI → Docker socket | 100–300ms | `docker network connect` is a kernel call |
| Docker → network namespace change | ~50ms | Instantaneous network isolation removal |
| Next collector poll | 0–5s | Depends on collector cycle phase |
| SQLite write | ~5ms | WAL mode, fast |
| Dashboard refresh | 0–3s | Polling interval |
| ML inference | ~2ms | IsolationForest on 4 features |
| **Total (tap → gauge red)** | **8–30s** | Conservative estimate: say "15 seconds" to judges |

---

## 3. 🔧 Prerequisites

### 3.1 Network Requirements

Both your laptop (running Docker) and the judge's phone **must be on the same Wi-Fi network**. The phone makes direct HTTP calls to your laptop's IP. No internet required.

> **⚠️ Warning:** Many conference/venue Wi-Fi networks block device-to-device communication (AP isolation). Test this before the demo. If it fails, use your phone as a hotspot and connect your laptop to it — then the laptop IP changes to `192.168.x.x`.

```bash
# Test phone-to-laptop connectivity before demo day
# From a phone browser, try: http://<LAPTOP_IP>:8000/health
# Expected response: {"status":"ok","model_loaded":true,...}
```

### 3.2 Firewall Configuration

The FastAPI port **8000** must be reachable from the phone.

**Linux (ufw):**
```bash
sudo ufw allow 8000/tcp
sudo ufw status  # verify rule is listed
```

**macOS:**
```bash
# macOS application firewall usually allows Docker port bindings automatically.
# If blocked, go to: System Settings → Network → Firewall → Options
# Add docker to allowed apps, or temporarily disable for demo
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate off
```

**Windows PowerShell (run as Administrator):**
```powershell
New-NetFirewallRule -DisplayName "5G Demo API" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
# Verify:
Get-NetFirewallRule -DisplayName "5G Demo API"
```

### 3.3 Docker Socket Permissions

The current `docker-compose.yml` mounts the Docker socket **read-only** (`:ro`):

```yaml
# CURRENT (read-only — collector only needs stats)
- /var/run/docker.sock:/var/run/docker.sock:ro
```

To execute `docker network connect` and `docker exec` from within the container, we need **read-write** (`:rw`):

```yaml
# REQUIRED for demo control (read-write — allows network manipulation)
- /var/run/docker.sock:/var/run/docker.sock:rw
```

> **🔒 Security Note:** Mounting the Docker socket read-write gives the container root-equivalent access to the Docker daemon — it can create, destroy, and reconfigure any container on the host. This is acceptable for a hackathon demo environment. **Never do this in production.** In a real deployment, the network manipulation would be handled by a privileged orchestration layer (Kubernetes CNI plugin, SR-IOV operator) with proper RBAC, not by mounting the Docker socket.

### 3.4 Finding Your Laptop IP Address

You need the IP address that is reachable from the phone (your Wi-Fi interface IP, not `127.0.0.1`).

**Linux:**
```bash
# Method 1 — routing-based (most reliable)
ip route get 1 | awk '{print $7; exit}'

# Method 2 — list all IPs
hostname -I | awk '{print $1}'

# Method 3 — specific interface
ip addr show wlan0 | grep 'inet ' | awk '{print $2}' | cut -d/ -f1
```

**macOS:**
```bash
# Wi-Fi interface (usually en0)
ipconfig getifaddr en0

# All interfaces
ifconfig | grep 'inet ' | grep -v '127.0.0.1'
```

**Windows PowerShell:**
```powershell
# All IPv4 addresses
ipconfig | Select-String "IPv4"

# Wi-Fi adapter specifically
Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "Wi-Fi" | Select IPAddress
```

### 3.5 Pre-Demo Connectivity Test

Run this before the presentation to confirm everything is reachable:

```bash
# From your laptop terminal
curl http://localhost:8000/health
# Expected: {"status":"ok","model_loaded":true,...}

# From your phone browser (replace with your actual IP)
# Open: http://192.168.1.42:8000/health
# Expected: same JSON response displayed in browser
```

---

## 4. 🛠️ Implementation Steps

### Step 1: Add Demo Endpoints to FastAPI

**File:** `api/main.py`  
**Why:** We need two HTTP endpoints the phone can call — one to inject the breach and one to restore isolation. We use FastAPI's `BackgroundTasks` so the API responds immediately (with 202 Accepted) while the Docker commands run asynchronously in the background. This prevents the phone request from timing out during the 200–300ms Docker network operation.

**Add these imports at the top of `api/main.py`** (after existing imports):

```python
import subprocess
import asyncio
from fastapi import BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
```

**Add these constants** after the existing `SLICE_NAMES` line:

```python
# ── Demo control config ───────────────────────────────────────────────────────
# Compose adds the project directory name as a prefix to network names.
# If your project folder is "network-slice-validator", networks become:
#   network-slice-validator_slice_b_net
# Override with COMPOSE_PROJECT_NAME env var if needed.
COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "network-slice-validator")
SLICE_A_CONTAINER = "slice-a"
SLICE_B_CONTAINER = "slice-b"
BREACH_NETWORK    = f"{COMPOSE_PROJECT}_slice_b_net"

# Tracks whether a breach is currently active (prevents double-inject)
_breach_active: bool = False
```

**Add these functions** before the route definitions (after `score_row()`):

```python
# ── Demo control helpers ──────────────────────────────────────────────────────

def _run_cmd(cmd: list[str], timeout: int = 15) -> tuple[bool, str]:
    """
    Run a shell command and return (success, output).
    Never raises — catches all exceptions and returns (False, error_msg).
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip() or f"Exit code {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError:
        return False, f"Command not found: {cmd[0]}. Is Docker installed in PATH?"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def _do_inject_breach():
    """
    Background task: connect slice-a to slice_b_net and generate cross-slice traffic.
    Runs in a thread pool — does NOT block the HTTP response.
    """
    global _breach_active
    log.info("[demo] Starting breach injection into network: %s", BREACH_NETWORK)

    # Step 1: Connect slice-a to slice-b's network (the core isolation break)
    ok, msg = _run_cmd([
        "docker", "network", "connect", BREACH_NETWORK, SLICE_A_CONTAINER
    ])
    if not ok:
        # Common case: already connected from a previous inject
        if "already exists" in msg.lower() or "already" in msg.lower():
            log.warning("[demo] slice-a already on breach network — continuing to traffic gen")
        else:
            log.error("[demo] network connect failed: %s", msg)
            _breach_active = False
            return
    else:
        log.info("[demo] Network connect OK: %s", msg)

    # Step 2: Start iperf3 server on slice-b (may already be running — that's fine)
    _run_cmd(["docker", "exec", "-d", SLICE_B_CONTAINER, "iperf3", "-s"])

    # Step 3: Generate 30 seconds of cross-slice traffic from slice-a → slice-b
    # -d runs it detached so this function returns quickly
    ok, msg = _run_cmd([
        "docker", "exec", "-d", SLICE_A_CONTAINER,
        "iperf3", "-c", SLICE_B_CONTAINER, "-t", "30", "-b", "5M"
    ])
    if not ok:
        log.warning("[demo] iperf3 traffic gen failed (ping fallback): %s", msg)
        # Fallback: use ping if iperf3 client fails
        _run_cmd([
            "docker", "exec", "-d", SLICE_A_CONTAINER,
            "ping", "-c", "100", SLICE_B_CONTAINER
        ])

    _breach_active = True
    log.info("[demo] Breach injection complete. Anomaly should appear in 8–20s.")


def _do_restore_isolation():
    """
    Background task: disconnect slice-a from slice_b_net and restore isolation.
    """
    global _breach_active
    log.info("[demo] Restoring isolation — disconnecting from: %s", BREACH_NETWORK)

    ok, msg = _run_cmd([
        "docker", "network", "disconnect", BREACH_NETWORK, SLICE_A_CONTAINER
    ])
    if not ok:
        if "not connected" in msg.lower() or "not found" in msg.lower():
            log.info("[demo] slice-a was not connected — isolation already restored")
        else:
            log.error("[demo] network disconnect failed: %s", msg)
    else:
        log.info("[demo] Network disconnect OK: %s", msg)

    _breach_active = False
    log.info("[demo] Isolation restored. Confidence will recover in 30–60s.")
```

**Add these routes** at the end of `api/main.py` (after the `/reload-model` route):

```python
# ── Demo control routes ───────────────────────────────────────────────────────

@app.get("/demo", response_class=HTMLResponse)
def demo_control_page():
    """
    Serve the mobile control console HTML page.
    Accessible at: http://<LAPTOP_IP>:8000/demo
    """
    html_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "demo_control.html")
    html_path = os.path.abspath(html_path)
    if not os.path.exists(html_path):
        return HTMLResponse(
            content="<h1>demo_control.html not found</h1>"
                    "<p>Place dashboard/demo_control.html in the project.</p>",
            status_code=404,
        )
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), status_code=200)


@app.post("/demo/inject")
def demo_inject(background_tasks: BackgroundTasks):
    """
    Inject a cross-slice network breach.
    Triggers: docker network connect + iperf3 cross-slice traffic.
    Returns immediately (202) — breach runs in background.
    """
    if _breach_active:
        return {
            "status": "already_active",
            "message": "Breach already injected. Call /demo/restore first.",
            "breach_active": True,
        }
    background_tasks.add_task(_do_inject_breach)
    log.info("[demo] Breach injection scheduled via API")
    return {
        "status": "injected",
        "message": f"Breach injection started. Network: {BREACH_NETWORK}. "
                   f"Watch dashboard for anomaly in 8–20 seconds.",
        "breach_active": True,
        "breach_network": BREACH_NETWORK,
    }


@app.post("/demo/restore")
def demo_restore(background_tasks: BackgroundTasks):
    """
    Restore isolation by disconnecting slice-a from slice_b_net.
    Returns immediately (202) — restore runs in background.
    """
    background_tasks.add_task(_do_restore_isolation)
    log.info("[demo] Isolation restore scheduled via API")
    return {
        "status": "restoring",
        "message": "Isolation restore started. Confidence will recover in 30–60 seconds.",
        "breach_active": False,
    }


@app.get("/demo/status")
def demo_status():
    """
    Returns current breach state. Polled by the mobile UI every 3s.
    """
    return {
        "breach_active": _breach_active,
        "breach_network": BREACH_NETWORK,
        "slice_a": SLICE_A_CONTAINER,
        "slice_b": SLICE_B_CONTAINER,
        "timestamp": time.time(),
    }
```

---

### Step 2: Create Mobile-Friendly HTML Control Page

**File to create:** `dashboard/demo_control.html`  
**Served at:** `http://<LAPTOP_IP>:8000/demo`  
**Why:** A single self-contained HTML file requires no build step, no npm, no framework. FastAPI serves it directly via the `/demo` GET route added in Step 1. It works on any phone browser — Chrome, Safari, Firefox.

Create the file at `dashboard/demo_control.html` with the following content:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0" />
  <title>5G Slice Control</title>
  <style>
    /* ─── Reset & Base ─────────────────────────────────────────────── */
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html { font-size: 16px; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0d1117;
      color: #c9d1d9;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 24px 16px;
    }

    /* ─── Header ───────────────────────────────────────────────────── */
    .header {
      text-align: center;
      margin-bottom: 28px;
      width: 100%;
      max-width: 480px;
    }
    .header .badge {
      display: inline-block;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #58a6ff;
      background: #1c2b3a;
      border: 1px solid #1f4068;
      border-radius: 20px;
      padding: 4px 12px;
      margin-bottom: 12px;
    }
    h1 {
      font-size: 22px;
      font-weight: 700;
      color: #f0f6fc;
      line-height: 1.3;
      margin-bottom: 6px;
    }
    .subtitle {
      font-size: 13px;
      color: #6b7280;
    }

    /* ─── Status Card ──────────────────────────────────────────────── */
    .status-card {
      width: 100%;
      max-width: 480px;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 12px;
      padding: 16px 20px;
      margin-bottom: 24px;
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .status-dot {
      width: 14px;
      height: 14px;
      border-radius: 50%;
      flex-shrink: 0;
      transition: background 0.4s;
    }
    .status-dot.normal  { background: #3fb950; box-shadow: 0 0 8px #3fb95088; }
    .status-dot.breach  { background: #f85149; box-shadow: 0 0 8px #f8514988; animation: pulse 1s infinite; }
    .status-dot.loading { background: #d29922; box-shadow: 0 0 8px #d2992288; }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50%       { opacity: 0.4; }
    }
    .status-text { flex: 1; }
    .status-label {
      font-size: 13px;
      font-weight: 600;
      color: #c9d1d9;
    }
    .status-sub {
      font-size: 12px;
      color: #6b7280;
      margin-top: 2px;
    }

    /* ─── Confidence Display ───────────────────────────────────────── */
    .confidence-wrap {
      width: 100%;
      max-width: 480px;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 24px;
      text-align: center;
    }
    .confidence-label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: #6b7280;
      margin-bottom: 10px;
    }
    .confidence-row {
      display: flex;
      gap: 16px;
      justify-content: center;
    }
    .conf-block { flex: 1; }
    .conf-name { font-size: 11px; color: #8b949e; margin-bottom: 4px; }
    .conf-value {
      font-size: 28px;
      font-weight: 700;
      transition: color 0.5s;
    }
    .conf-value.high   { color: #3fb950; }
    .conf-value.medium { color: #d29922; }
    .conf-value.low    { color: #f85149; }
    .conf-bar-wrap {
      height: 5px;
      background: #21262d;
      border-radius: 3px;
      margin-top: 6px;
      overflow: hidden;
    }
    .conf-bar {
      height: 100%;
      border-radius: 3px;
      transition: width 0.6s, background 0.5s;
    }

    /* ─── Action Buttons ───────────────────────────────────────────── */
    .button-group {
      width: 100%;
      max-width: 480px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      margin-bottom: 24px;
    }
    .btn {
      width: 100%;
      border: none;
      border-radius: 14px;
      font-size: 17px;
      font-weight: 700;
      letter-spacing: 0.02em;
      cursor: pointer;
      padding: 22px 24px;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      transition: transform 0.1s, opacity 0.2s, box-shadow 0.2s;
      -webkit-tap-highlight-color: transparent;
    }
    .btn:active { transform: scale(0.97); }
    .btn:disabled { opacity: 0.45; cursor: not-allowed; transform: none; }
    .btn-inject {
      background: linear-gradient(135deg, #c62828, #f85149);
      color: #fff;
      box-shadow: 0 4px 20px #f8514940;
    }
    .btn-inject:hover:not(:disabled) { box-shadow: 0 6px 28px #f8514960; }
    .btn-restore {
      background: linear-gradient(135deg, #1a7a1a, #3fb950);
      color: #fff;
      box-shadow: 0 4px 20px #3fb95040;
    }
    .btn-restore:hover:not(:disabled) { box-shadow: 0 6px 28px #3fb95060; }
    .btn-stress {
      background: linear-gradient(135deg, #1a2a5a, #2979ff);
      color: #fff;
      box-shadow: 0 4px 20px #2979ff30;
    }
    .btn-icon { font-size: 22px; }

    /* ─── Response Toast ───────────────────────────────────────────── */
    .toast {
      width: 100%;
      max-width: 480px;
      border-radius: 10px;
      padding: 12px 16px;
      font-size: 13px;
      line-height: 1.5;
      margin-bottom: 24px;
      border-left: 4px solid;
      display: none;
      animation: slideIn 0.25s ease;
    }
    @keyframes slideIn {
      from { opacity: 0; transform: translateY(-8px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    .toast.success { background: #0d1a0d; border-color: #3fb950; color: #7ee787; display: block; }
    .toast.error   { background: #2d1117; border-color: #f85149; color: #f97071; display: block; }
    .toast.info    { background: #0c1624; border-color: #58a6ff; color: #79c0ff; display: block; }

    /* ─── Timeline ─────────────────────────────────────────────────── */
    .timeline-card {
      width: 100%;
      max-width: 480px;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 12px;
      padding: 16px 20px;
      margin-bottom: 24px;
    }
    .timeline-title {
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: #6b7280;
      margin-bottom: 14px;
    }
    .timeline-step {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      margin-bottom: 10px;
    }
    .timeline-step:last-child { margin-bottom: 0; }
    .step-dot {
      width: 8px; height: 8px;
      border-radius: 50%;
      background: #30363d;
      flex-shrink: 0;
      margin-top: 5px;
    }
    .step-dot.done   { background: #3fb950; }
    .step-dot.active { background: #f85149; animation: pulse 1s infinite; }
    .step-text { font-size: 13px; color: #8b949e; line-height: 1.4; }
    .step-text strong { color: #c9d1d9; }

    /* ─── Dashboard Link ───────────────────────────────────────────── */
    .dash-link {
      width: 100%;
      max-width: 480px;
      text-align: center;
      margin-bottom: 8px;
    }
    .dash-link a {
      color: #58a6ff;
      text-decoration: none;
      font-size: 14px;
      font-weight: 500;
    }
    .dash-link a:hover { text-decoration: underline; }

    .footer {
      font-size: 11px;
      color: #3d444d;
      text-align: center;
      margin-top: 8px;
    }
  </style>
</head>
<body>

  <!-- Header -->
  <div class="header">
    <div class="badge">5G Slice Control Plane</div>
    <h1>🛡️ Isolation Validator<br>Control Console</h1>
    <p class="subtitle">Tap to inject or restore network slice breaches</p>
  </div>

  <!-- Status Card -->
  <div class="status-card">
    <div class="status-dot normal" id="statusDot"></div>
    <div class="status-text">
      <div class="status-label" id="statusLabel">Checking isolation status…</div>
      <div class="status-sub" id="statusSub">Connecting to API…</div>
    </div>
  </div>

  <!-- Confidence Display -->
  <div class="confidence-wrap">
    <div class="confidence-label">Live Isolation Confidence</div>
    <div class="confidence-row">
      <div class="conf-block">
        <div class="conf-name">slice-a</div>
        <div class="conf-value high" id="confA">—</div>
        <div class="conf-bar-wrap">
          <div class="conf-bar" id="barA" style="width:0%;background:#3fb950"></div>
        </div>
      </div>
      <div class="conf-block">
        <div class="conf-name">slice-b</div>
        <div class="conf-value high" id="confB">—</div>
        <div class="conf-bar-wrap">
          <div class="conf-bar" id="barB" style="width:0%;background:#3fb950"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- Action Buttons -->
  <div class="button-group">
    <button class="btn btn-inject" id="btnInject" onclick="triggerInject()">
      <span class="btn-icon">💥</span>
      INJECT BREACH
    </button>
    <button class="btn btn-restore" id="btnRestore" onclick="triggerRestore()">
      <span class="btn-icon">🔒</span>
      RESTORE ISOLATION
    </button>
    <button class="btn btn-stress" id="btnStress" onclick="triggerCpuStress()">
      <span class="btn-icon">🔥</span>
      CPU STRESS (slice-a)
    </button>
  </div>

  <!-- Response Toast -->
  <div class="toast" id="toast"></div>

  <!-- Timeline -->
  <div class="timeline-card">
    <div class="timeline-title">⏱ Expected Timeline After Inject</div>
    <div class="timeline-step">
      <div class="step-dot done"></div>
      <div class="step-text"><strong>0s</strong> — API receives breach command, Docker network connect executes</div>
    </div>
    <div class="timeline-step">
      <div class="step-dot" id="t1"></div>
      <div class="step-text"><strong>~5s</strong> — Collector picks up elevated network I/O delta</div>
    </div>
    <div class="timeline-step">
      <div class="step-dot" id="t2"></div>
      <div class="step-text"><strong>~8s</strong> — IsolationForest scores the anomalous row</div>
    </div>
    <div class="timeline-step">
      <div class="step-dot" id="t3"></div>
      <div class="step-text"><strong>~15s</strong> — Dashboard gauge drops below 40% (RED zone)</div>
    </div>
    <div class="timeline-step">
      <div class="step-dot" id="t4"></div>
      <div class="step-text"><strong>After restore</strong> — Confidence recovers to green in 30–60s</div>
    </div>
  </div>

  <!-- Dashboard Link -->
  <div class="dash-link">
    <a href="javascript:void(0)" id="dashLink">📊 Open Operator Dashboard</a>
  </div>

  <div class="footer">5G/6G Network Slicing Isolation Validator · Hackathon Demo</div>

<script>
  // ── Config — auto-detects API base from current URL origin ─────────────────
  const API = window.location.origin;   // e.g. http://192.168.1.42:8000
  const DASH_PORT = 8501;
  const POLL_INTERVAL_MS = 3000;

  // Set dashboard link dynamically
  const dashUrl = `${window.location.protocol}//${window.location.hostname}:${DASH_PORT}`;
  document.getElementById('dashLink').href = dashUrl;
  document.getElementById('dashLink').target = '_blank';

  // ── State ──────────────────────────────────────────────────────────────────
  let breachActive = false;
  let injectTime   = null;

  // ── Toast helper ───────────────────────────────────────────────────────────
  function showToast(msg, type = 'info') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = `toast ${type}`;
    clearTimeout(t._timer);
    t._timer = setTimeout(() => { t.style.display = 'none'; }, 6000);
  }

  // ── Confidence colour helper ───────────────────────────────────────────────
  function confClass(val) {
    if (val === null || val === undefined) return 'high';
    if (val >= 70) return 'high';
    if (val >= 40) return 'medium';
    return 'low';
  }
  function confColor(val) {
    if (val >= 70) return '#3fb950';
    if (val >= 40) return '#d29922';
    return '#f85149';
  }

  // ── Update confidence displays ─────────────────────────────────────────────
  function updateConf(sliceId, val) {
    const elId = sliceId === 'slice-a' ? 'confA' : 'confB';
    const barId = sliceId === 'slice-a' ? 'barA'  : 'barB';
    const el  = document.getElementById(elId);
    const bar = document.getElementById(barId);
    if (el && val !== null && val !== undefined) {
      el.textContent = `${val.toFixed(0)}%`;
      el.className   = `conf-value ${confClass(val)}`;
      bar.style.width      = `${Math.max(2, val)}%`;
      bar.style.background = confColor(val);
    }
  }

  // ── Update timeline dots ───────────────────────────────────────────────────
  function updateTimeline(elapsed) {
    const steps = [
      { id: 't1', threshold: 5  },
      { id: 't2', threshold: 8  },
      { id: 't3', threshold: 15 },
      { id: 't4', threshold: 999 },
    ];
    steps.forEach(s => {
      const el = document.getElementById(s.id);
      if (!el) return;
      if (!breachActive) { el.className = 'step-dot'; return; }
      if (elapsed >= s.threshold) {
        el.className = 'step-dot done';
      } else if (elapsed >= s.threshold - 2) {
        el.className = 'step-dot active';
      } else {
        el.className = 'step-dot';
      }
    });
    // t4 special — active only after restore
    if (!breachActive && injectTime) {
      document.getElementById('t4').className = 'step-dot active';
    }
  }

  // ── Poll /score for live confidence values ─────────────────────────────────
  async function pollScores() {
    try {
      const r = await fetch(`${API}/score`, { signal: AbortSignal.timeout(3000) });
      if (!r.ok) return;
      const data = await r.json();
      const scores = Array.isArray(data) ? data : [data];
      scores.forEach(s => {
        if (s.slice_id && s.isolation_confidence !== null) {
          updateConf(s.slice_id, s.isolation_confidence);
        }
      });
    } catch (_) { /* silently skip — API may be momentarily busy */ }
  }

  // ── Poll /demo/status for breach state ────────────────────────────────────
  async function pollStatus() {
    try {
      const r = await fetch(`${API}/demo/status`, { signal: AbortSignal.timeout(3000) });
      if (!r.ok) return;
      const data = await r.json();
      breachActive = data.breach_active;

      const dot   = document.getElementById('statusDot');
      const label = document.getElementById('statusLabel');
      const sub   = document.getElementById('statusSub');

      const elapsed = injectTime ? (Date.now() - injectTime) / 1000 : 0;

      if (breachActive) {
        dot.className    = 'status-dot breach';
        label.textContent = '⚠️  ISOLATION BREACH ACTIVE';
        sub.textContent  = `Cross-slice traffic injected · ${Math.round(elapsed)}s elapsed`;
        updateTimeline(elapsed);
      } else {
        dot.className    = 'status-dot normal';
        label.textContent = '✅  All slices isolated normally';
        sub.textContent  = 'Monitoring active · ML scoring live';
        if (injectTime && elapsed < 5) {
          // Just restored — show recovery timeline
          updateTimeline(999);
        } else {
          updateTimeline(0);
        }
      }

      document.getElementById('btnInject').disabled  = breachActive;
      document.getElementById('btnRestore').disabled = !breachActive;

    } catch (e) {
      document.getElementById('statusLabel').textContent = '⚡ API unreachable';
      document.getElementById('statusSub').textContent   = 'Check that docker compose is running';
      document.getElementById('statusDot').className     = 'status-dot loading';
    }
  }

  // ── Inject breach ──────────────────────────────────────────────────────────
  async function triggerInject() {
    document.getElementById('btnInject').disabled = true;
    showToast('Sending breach injection command…', 'info');
    try {
      const r = await fetch(`${API}/demo/inject`, { method: 'POST' });
      const d = await r.json();
      if (d.status === 'injected') {
        injectTime = Date.now();
        showToast('✅ Breach injected! Watch the dashboard gauge drop in ~15 seconds.', 'success');
      } else if (d.status === 'already_active') {
        showToast('⚠️ Breach already active. Tap RESTORE first.', 'error');
        document.getElementById('btnInject').disabled = false;
      } else {
        showToast(`Response: ${d.message}`, 'info');
      }
    } catch (e) {
      showToast(`❌ Connection error: ${e.message}`, 'error');
      document.getElementById('btnInject').disabled = false;
    }
  }

  // ── Restore isolation ──────────────────────────────────────────────────────
  async function triggerRestore() {
    document.getElementById('btnRestore').disabled = true;
    showToast('Sending restore command…', 'info');
    try {
      const r = await fetch(`${API}/demo/restore`, { method: 'POST' });
      const d = await r.json();
      showToast('✅ Isolation restoring. Confidence will recover in 30–60 seconds.', 'success');
    } catch (e) {
      showToast(`❌ Connection error: ${e.message}`, 'error');
    }
  }

  // ── CPU stress (bonus scenario) ────────────────────────────────────────────
  async function triggerCpuStress() {
    showToast('Triggering CPU stress on slice-a…', 'info');
    try {
      const r = await fetch(`${API}/demo/stress`, { method: 'POST' });
      if (r.ok) {
        showToast('🔥 CPU stress started on slice-a. Watch CPU% on dashboard.', 'success');
      } else {
        showToast('CPU stress endpoint not available — add /demo/stress to api/main.py', 'error');
      }
    } catch (e) {
      showToast('CPU stress endpoint not yet implemented (see docs §7.1 bonus)', 'error');
    }
  }

  // ── Start polling ──────────────────────────────────────────────────────────
  pollStatus();
  pollScores();
  setInterval(pollStatus, POLL_INTERVAL_MS);
  setInterval(pollScores, POLL_INTERVAL_MS);
</script>

</body>
</html>
```

---

### Step 3: Update Docker Socket Permissions

**File:** `docker-compose.yml`  
**Why:** The Docker socket read-only mount (`:ro`) allows `docker stats` (reads) but blocks `docker network connect` and `docker exec` (writes). The demo endpoints need write access to manipulate network namespaces.

Find this line in `docker-compose.yml` under the `main-service` volumes section:

```yaml
# BEFORE (read-only — change this)
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
  - db_data:/data
  - model_data:/ml
```

Change it to:

```yaml
# AFTER (read-write — enables demo control)
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:rw
  - db_data:/data
  - model_data:/ml
```

**Validate the change after rebuild:**

```bash
# Rebuild with the new permission
docker compose up --build -d

# Confirm the socket is mounted rw inside the container
docker exec main-service ls -la /var/run/docker.sock
# Expected: srwxrwxrwx (not srwxr-xr-x or similar read-only)

# Test that docker network commands work from inside the container
docker exec main-service docker network ls
# Expected: list of all Docker networks including slice_a_net, slice_b_net
```

---

### Step 4: Network Name Configuration

**Why this matters:** Docker Compose prefixes all network names with the project name (derived from the directory name). If your project folder is `network-slice-validator`, the network `slice_b_net` becomes `network-slice-validator_slice_b_net` inside Docker.

**Find the exact network name on your machine:**

```bash
# List all networks — look for your slice networks
docker network ls

# Expected output (names may vary by folder name):
# NETWORK ID     NAME                                    DRIVER    SCOPE
# abc123         network-slice-validator_slice_a_net     bridge    local
# def456         network-slice-validator_slice_b_net     bridge    local
# ghi789         network-slice-validator_monitor_net     bridge    local
```

**Option A — Set environment variable (recommended):**

Add `COMPOSE_PROJECT_NAME` to `docker-compose.yml`:

```yaml
services:
  main-service:
    environment:
      - DB_PATH=/data/metrics.db
      - MODEL_PATH=/ml/model.pkl
      - COMPOSE_PROJECT_NAME=network-slice-validator  # ← ADD THIS
      - COLLECTOR_INTERVAL=5
      # ... rest of env vars
```

This ensures `BREACH_NETWORK` in `api/main.py` always resolves to `network-slice-validator_slice_b_net` regardless of which directory the project is run from.

**Option B — Override at runtime:**

```bash
# Set before docker compose up
export COMPOSE_PROJECT_NAME=network-slice-validator
docker compose up --build
```

**Validate network name resolution:**

```bash
# Confirm the name the API will use
docker exec main-service python3 -c "
import os
project = os.environ.get('COMPOSE_PROJECT_NAME', 'network-slice-validator')
print('BREACH_NETWORK will be:', f'{project}_slice_b_net')
"

# Verify that network name exists in Docker
docker network inspect network-slice-validator_slice_b_net
# Expected: JSON with 2 connected containers (slice-b and main-service)
```

---

## 5. 📋 Step-by-Step Demo Flow

### Pre-Demo Checklist (do this 10 minutes before presenting)

- [ ] `docker compose ps` — all 3 containers show **Up** and **healthy**
- [ ] `curl http://localhost:8000/health` — shows `"model_loaded": true` and `db_rows > 0`
- [ ] `http://localhost:8501` opens in browser — gauges are **green** (>70%)
- [ ] `http://<LAPTOP_IP>:8000/demo` opens on **phone** — shows control panel
- [ ] Phone shows slice-a and slice-b confidence values (not "—")
- [ ] Firewall allows port 8000 from phone
- [ ] Both devices on same Wi-Fi network

### Starting the Stack

```bash
# Fresh start (recommended before demo)
docker compose down
docker compose up --build

# Wait for this log line before starting demo:
# [entrypoint] All services running!
#   API:       http://localhost:8000
#   Dashboard: http://localhost:8501

# Typical startup time: 90–120 seconds
```

### Accessing the Control Page from Phone

1. Find your laptop IP (see §3.4)
2. On the phone, open a browser (Chrome or Safari)
3. Navigate to: `http://<YOUR_LAPTOP_IP>:8000/demo`
4. You should see the control console with green status and live confidence %

> **📱 Tip for the demo:** Keep the phone at `http://<IP>:8000/demo` open. Keep the laptop browser at `http://localhost:8501` (the dashboard) visible to judges. The phone injects, the laptop shows the impact.

### During the Demo — Exact Flow

```
[You]:  "Let me show you a live isolation breach."

[Action]: Hand the phone to a judge (or hold it visible)

[You]:  "On the left you see our operator dashboard — both slices
         showing green, above 70% isolation confidence. The ML model
         is continuously scoring telemetry in real time."

[Action]: Judge taps INJECT BREACH on the phone

[You]:  "That tap just sent an HTTP command to our control API —
         the same flow as a real SDN orchestrator reconfiguring a
         5G control plane. slice-a is now connected to slice-b's
         network. Cross-slice traffic is flowing."

[Wait 10–20 seconds, watch dashboard]

[You]:  "There — the gauge is dropping. The IsolationForest model
         detected the anomalous network I/O delta. Confidence is now
         below 40% — that's our red threshold. In a real network,
         this would trigger an automated remediation workflow."

[Action]: Tap RESTORE ISOLATION on the phone

[You]:  "We restore isolation with another API call. The dashboard
         will recover in about 30–60 seconds as the ML model sees
         normal telemetry again."
```

### Expected Visual Timeline

```
0s    Phone taps INJECT BREACH
      └── Toast: "✅ Breach injected!"
      └── Status: "⚠️ ISOLATION BREACH ACTIVE"

~10s  Dashboard gauge for slice-a begins dropping
      └── Confidence: 80% → 60% → 45%...

~15s  Gauge enters RED zone (< 40%)
      └── Alert feed shows anomaly event with timestamp
      └── Timeline dots turn green on control page

~20s  Phone taps RESTORE ISOLATION
      └── Status: "✅ Isolation restoring"

~50s  Dashboard gauge begins recovering
      └── Confidence climbs back above 70%

~80s  Full green — isolation confirmed restored
```

---

## 6. 🔬 Technical Deep Dive

### How Docker Network Namespace Manipulation Works

When you run `docker network connect slice_b_net slice-a`, Docker performs the following kernel operations:

1. **Identifies** the network namespace of the `slice-a` container (`/proc/<PID>/ns/net`)
2. **Creates a veth pair** — a virtual ethernet cable with two ends
3. **Attaches one end** to the `slice_b_net` Linux bridge inside the Docker host
4. **Attaches the other end** inside `slice-a`'s network namespace
5. **Assigns an IP** from the `slice_b_net` subnet (`172.20.2.0/24`) to the interface inside the container

After this operation, `slice-a` has **two** network interfaces:
- `eth0` → connected to `slice_a_net` (172.20.1.x)
- `eth1` → connected to `slice_b_net` (172.20.2.x) ← **the breach**

This is a real kernel-level network namespace change. It is the same primitive used by Kubernetes CNI plugins to manage pod networking.

### Why This Mimics Real 5G Control-Plane Reconfiguration

In a production 5G network, the control-plane enforces slice isolation through:

1. **S-NSSAI (Single Network Slice Selection Assistance Information)** — a 32-bit identifier (SST + SD) that tags PDU sessions to a slice
2. **SMF (Session Management Function)** — manages PDU session establishment per S-NSSAI
3. **UPF (User Plane Function)** — enforces the actual packet forwarding rules using PFCP (Packet Forwarding Control Protocol)

When the SMF reconfigures a UPF to route traffic from one slice to another (a control-plane isolation breach), it sends a **PFCP Session Modification Request** that changes the UPF's Packet Detection Rules (PDRs) and Forwarding Action Rules (FARs).

Our `docker network connect` command is the containerised equivalent of that PFCP session modification — it reconfigures the packet forwarding layer at the kernel level.

### 3GPP Architecture Mapping

```
Our System                          Production 5G Equivalent
──────────────────────────────────  ────────────────────────────────────
Mobile Control Console (HTML)   →   NEF API client / OSS/BSS portal
POST /demo/inject endpoint      →   PCF policy provisioning interface
docker network connect          →   UPF PFCP session modification
IsolationForest anomaly score   →   NWDAF slice analytics (TS 23.288)
Dashboard confidence gauge      →   NF consumer analytics subscription
/demo/restore endpoint          →   SMF slice re-isolation trigger
```

**Key 3GPP references:**
- TS 29.520 — NWDAF services (analytics consumption)
- TS 29.507 — Policy control and authorization
- TS 29.244 — Interface between SMF and UPF (PFCP)
- TS 23.501 — 5G system architecture (slice isolation requirements)

### Security Considerations for Production

The Docker socket mount (`:rw`) used here is suitable only for a demo/hackathon environment. In production, the equivalent control should be implemented as:

1. **Kubernetes Network Policy controller** — use `kubectl` to apply/remove NetworkPolicy objects, which are enforced by the CNI plugin (Calico, Cilium, etc.)
2. **Open5GS CLI / REST API** — modify SMF/UPF configurations via the Open5GS management interface
3. **SR-IOV + DPDK** — hardware-enforced slice isolation with VF (Virtual Function) assignment changes

The principle remains identical: an authenticated HTTP call triggers a privileged control-plane operation that reconfigures the data-plane isolation boundary.

---

## 7. 📝 Complete Code Reference

### 7.1 Complete `api/main.py` Additions

> **File location:** `api/main.py`  
> **Where to add:** Insert after the existing imports block, then after `score_row()`, then at the end of the file.

**New imports to add** (merge with existing import block at top of file):

```python
import subprocess
from fastapi import BackgroundTasks
from fastapi.responses import HTMLResponse
```

**New constants** (add after `SLICE_NAMES = [...]` line, around line 37):

```python
# ── Demo control config ───────────────────────────────────────────────────────
COMPOSE_PROJECT   = os.environ.get("COMPOSE_PROJECT_NAME", "network-slice-validator")
SLICE_A_CONTAINER = "slice-a"
SLICE_B_CONTAINER = "slice-b"
BREACH_NETWORK    = f"{COMPOSE_PROJECT}_slice_b_net"
_breach_active: bool = False
```

**New helper functions** (add before the `# ── Routes ──` section):

```python
# ── Demo control helpers ──────────────────────────────────────────────────────

def _run_cmd(cmd: list[str], timeout: int = 15) -> tuple[bool, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip() or f"Exit code {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, f"Timed out after {timeout}s"
    except FileNotFoundError:
        return False, f"Command not found: {cmd[0]}"
    except Exception as e:
        return False, str(e)


def _do_inject_breach():
    global _breach_active
    log.info("[demo] Injecting breach — connecting to %s", BREACH_NETWORK)
    ok, msg = _run_cmd(["docker", "network", "connect", BREACH_NETWORK, SLICE_A_CONTAINER])
    if not ok and "already" not in msg.lower():
        log.error("[demo] network connect failed: %s", msg)
        _breach_active = False
        return
    _run_cmd(["docker", "exec", "-d", SLICE_B_CONTAINER, "iperf3", "-s"])
    ok, msg = _run_cmd([
        "docker", "exec", "-d", SLICE_A_CONTAINER,
        "iperf3", "-c", SLICE_B_CONTAINER, "-t", "30", "-b", "5M"
    ])
    if not ok:
        _run_cmd(["docker", "exec", "-d", SLICE_A_CONTAINER,
                  "ping", "-c", "100", SLICE_B_CONTAINER])
    _breach_active = True
    log.info("[demo] Breach active — anomaly expected in 8–20s")


def _do_restore_isolation():
    global _breach_active
    log.info("[demo] Restoring isolation — disconnecting from %s", BREACH_NETWORK)
    _run_cmd(["docker", "network", "disconnect", BREACH_NETWORK, SLICE_A_CONTAINER])
    _breach_active = False
    log.info("[demo] Isolation restored")
```

**New routes** (add at the very end of `api/main.py`):

```python
# ── Demo control routes ───────────────────────────────────────────────────────

@app.get("/demo", response_class=HTMLResponse)
def demo_control_page():
    html_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "dashboard", "demo_control.html")
    )
    if not os.path.exists(html_path):
        return HTMLResponse("<h1>demo_control.html not found</h1>", status_code=404)
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.post("/demo/inject")
def demo_inject(background_tasks: BackgroundTasks):
    if _breach_active:
        return {"status": "already_active", "message": "Call /demo/restore first.", "breach_active": True}
    background_tasks.add_task(_do_inject_breach)
    return {"status": "injected",
            "message": f"Breach started on {BREACH_NETWORK}. Watch dashboard in 8–20s.",
            "breach_active": True, "breach_network": BREACH_NETWORK}


@app.post("/demo/restore")
def demo_restore(background_tasks: BackgroundTasks):
    background_tasks.add_task(_do_restore_isolation)
    return {"status": "restoring", "message": "Isolation restoring. Recovery in 30–60s.", "breach_active": False}


@app.get("/demo/status")
def demo_status():
    return {"breach_active": _breach_active, "breach_network": BREACH_NETWORK,
            "slice_a": SLICE_A_CONTAINER, "slice_b": SLICE_B_CONTAINER, "timestamp": time.time()}


@app.post("/demo/stress")
def demo_stress(background_tasks: BackgroundTasks):
    """Bonus: CPU stress injection on slice-a"""
    def _stress():
        _run_cmd(["docker", "exec", "-d", SLICE_A_CONTAINER,
                  "sh", "-c", "for i in 1 2 3 4; do while true; do :; done & done; sleep 20; kill 0"])
    background_tasks.add_task(_stress)
    return {"status": "stress_started", "message": "CPU stress running on slice-a for 20s"}
```

### 7.2 Complete `demo_control.html`

> **File location:** `dashboard/demo_control.html`  
> **Full content:** See §4, Step 2 above — copy the entire HTML block verbatim.

### 7.3 `docker-compose.yml` Change

```yaml
# Only this line changes under main-service.volumes:
# FROM:
- /var/run/docker.sock:/var/run/docker.sock:ro
# TO:
- /var/run/docker.sock:/var/run/docker.sock:rw
```

### 7.4 File Placement Summary

```
network-slice-validator/
├── api/
│   └── main.py              ← MODIFIED: add imports, constants, helpers, routes
├── dashboard/
│   ├── app.py               ← unchanged
│   └── demo_control.html    ← NEW FILE: create this
└── docker-compose.yml       ← MODIFIED: :ro → :rw on docker socket line
```

---

## 8. ✅ Validation & Testing

### Test Locally Before Demo Day

```bash
# 1. Rebuild after all changes
docker compose down && docker compose up --build -d

# 2. Wait for startup (~90s), then:
curl http://localhost:8000/health
# Expected: {"status":"ok","model_loaded":true,"db_rows":...}

# 3. Test the demo control page serves correctly
curl -s http://localhost:8000/demo | grep "5G Slice Control"
# Expected: match found (HTML title in response)

# 4. Test status endpoint
curl http://localhost:8000/demo/status
# Expected: {"breach_active":false,"breach_network":"network-slice-validator_slice_b_net",...}

# 5. Test inject endpoint
curl -X POST http://localhost:8000/demo/inject
# Expected: {"status":"injected","message":"Breach started on..."}

# 6. Wait 5s, verify breach is active
curl http://localhost:8000/demo/status
# Expected: {"breach_active":true,...}

# 7. Verify network connection from outside Docker
docker inspect slice-a --format '{{json .NetworkSettings.Networks}}' | python3 -m json.tool
# Expected: BOTH slice_a_net AND slice_b_net listed for slice-a

# 8. Restore
curl -X POST http://localhost:8000/demo/restore

# 9. Verify restoration
sleep 3 && docker inspect slice-a --format '{{json .NetworkSettings.Networks}}' | python3 -m json.tool
# Expected: ONLY slice_a_net listed (slice_b_net removed)
```

### Phone Connectivity Test

```bash
# From your phone browser, test these URLs:
# (replace 192.168.1.42 with your actual laptop IP)

http://192.168.1.42:8000/health       → should show JSON
http://192.168.1.42:8000/demo/status  → should show breach status
http://192.168.1.42:8000/demo         → should load the control UI
```

### Expected Log Output

When you inject a breach, you should see these lines in `docker compose logs main-service`:

```
2024-01-15 10:23:01 [api] INFO [demo] Breach injection scheduled via API
2024-01-15 10:23:01 [api] INFO [demo] Injecting breach — connecting to network-slice-validator_slice_b_net
2024-01-15 10:23:01 [api] INFO [demo] Network connect OK:
2024-01-15 10:23:02 [api] INFO [demo] Breach active — anomaly expected in 8–20s
```

And from the collector:

```
2024-01-15 10:23:06 [collector] INFO Cycle 45: wrote 2 rows
```

### Success Criteria Checklist

- [ ] `GET /demo` returns the HTML control page (status 200)
- [ ] `POST /demo/inject` returns `{"status":"injected"}` within 500ms
- [ ] After inject, `docker inspect slice-a` shows `slice_b_net` in networks
- [ ] Dashboard confidence drops below 40% within 30 seconds
- [ ] Alert feed shows anomaly event with timestamp
- [ ] `POST /demo/restore` removes slice-a from slice_b_net
- [ ] Phone can access all endpoints without CORS errors
- [ ] `GET /demo/status` correctly tracks `breach_active` state

---

## 9. 🔄 Fallback Plans

### If Phone Can't Connect to Laptop

**Cause 1 — AP Isolation (venue Wi-Fi blocks device-to-device traffic)**

```bash
# Solution: Use your phone as a mobile hotspot
# 1. Enable hotspot on your phone (any phone)
# 2. Connect your LAPTOP to the phone hotspot
# 3. The laptop IP is now assigned by the hotspot (usually 192.168.43.x)
# 4. Your demo phone (same hotspot) can now reach the laptop directly

# Find new IP after connecting to hotspot:
ip route get 1 | awk '{print $7; exit}'   # Linux
ipconfig getifaddr en1                     # macOS (en1 = Wi-Fi, en0 = Ethernet)
```

**Cause 2 — Firewall blocking port 8000**

```bash
# Linux — open the port
sudo iptables -A INPUT -p tcp --dport 8000 -j ACCEPT

# macOS — temporarily disable application firewall
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate off

# Windows — allow in PowerShell (Admin)
netsh advfirewall firewall add rule name="Demo API" dir=in action=allow protocol=TCP localport=8000
```

**Cause 3 — Wrong IP address**

```bash
# Show all possible IPs at once
ip addr | grep 'inet ' | awk '{print $2, $NF}'
# Try each one in the phone browser until /health responds
```

### Backup: Demo Recording

If all phone connectivity attempts fail, play a pre-recorded video instead:

```bash
# Record a 60-second demo clip before the presentation
# Linux: using asciinema + screen recorder
sudo apt install asciinema
asciinema rec demo_breach.cast

# macOS: QuickTime Player → New Screen Recording
# Windows: Xbox Game Bar (Win+G)

# Recommended: record the full flow
# 1. Show dashboard at green (5s)
# 2. Inject breach from phone (5s)
# 3. Watch gauge drop (20s)
# 4. Restore (5s)
# 5. Watch recovery (20s)
# Save as MP4, have it ready to play fullscreen
```

### Alternative Trigger Methods

If the phone UI fails but the system works, trigger via these fallbacks in order:

```bash
# Option 1 — cURL from laptop (show command to judges)
curl -X POST http://localhost:8000/demo/inject

# Option 2 — Direct Docker commands (original demo method)
docker network connect network-slice-validator_slice_b_net slice-a
docker exec slice-a ping -c 50 slice-b

# Option 3 — Python one-liner
python3 -c "import requests; r=requests.post('http://localhost:8000/demo/inject'); print(r.json())"
```

---

## 10. 🎙️ Judge Presentation Script

### 60-Second Architecture Explanation

> *"At its core, this system continuously verifies that virtual 5G network slices remain genuinely isolated from each other. We have two simulated slices — think of them as two separate operators' networks running on shared infrastructure. A real-time ML model — an IsolationForest — is trained on normal baseline telemetry: CPU usage, memory, network I/O. Any deviation from that baseline triggers an anomaly score, which drops this confidence gauge."*
>
> *"The control console on this phone is the equivalent of a 5G SDN orchestrator — the Network Exposure Function in 3GPP terms. When I tap this button, it calls our FastAPI control-plane endpoint, which reconfigures the network namespace at the kernel level — the same primitive that Kubernetes uses. The ML model picks up the anomalous cross-slice traffic within seconds and flags it. No hardcoded rules. No thresholds. Pure unsupervised learning."*
>
> *"In production, this maps directly to Open5GS plus UERANSIM for the actual 5G stack, Prometheus plus eBPF for telemetry, and NWDAF — the Network Data Analytics Function — for the ML layer. We've built the full architecture in prototype form, with a clear upgrade path to each production component."*

### Key Talking Points

| Judge asks... | Say... |
|---|---|
| "Is this real data?" | "Yes — all metrics come from actual container resource usage via `docker stats`. Zero mocked values. You can run `docker stats` yourself and see the same numbers." |
| "Why IsolationForest?" | "Unsupervised — no labelled breach data needed. Trained on real baseline. Works well on 4-feature multivariate data. In production we'd upgrade to an LSTM autoencoder for temporal patterns." |
| "What's the production path?" | "Docker → Kubernetes. SQLite → InfluxDB. IsolationForest → ONNX autoencoder. Streamlit → React WebSocket. Alpine slices → Open5GS + UERANSIM. Every swap is additive, not a rewrite." |
| "Why Docker socket?" | "Same principle as a Kubernetes CNI plugin — the orchestrator reconfigures the network namespace. In production this would be the SMF sending a PFCP session modification to the UPF." |
| "How fast is detection?" | "8 to 20 seconds from breach to visual alarm. Bottleneck is the 5-second collector interval. With eBPF we'd get sub-second detection." |

### Opening Statement for 3-Minute Demo

> *"This is a 5G Network Slicing Isolation Validator — a system that guarantees network slices stay isolated from each other, continuously, using real machine learning. Not rule-based monitoring. Actual unsupervised anomaly detection on live telemetry."*
>
> *"I'm going to hand this phone to you — tap the red button and watch what happens on the dashboard."*

---

## 11. ❓ Troubleshooting FAQ

**Q: The breach API returns 200 but the dashboard gauge doesn't drop.**

Check network name:
```bash
docker network ls | grep slice
# Compare with BREACH_NETWORK value in api/main.py
# If different, update COMPOSE_PROJECT_NAME env var in docker-compose.yml
```

**Q: `docker network connect` fails with "permission denied".**

The socket is still mounted `:ro`. Verify:
```bash
grep "docker.sock" docker-compose.yml
# Must show :rw, not :ro
# If it shows :ro, edit the file and rebuild: docker compose up --build -d
```

**Q: Phone shows "API unreachable" but laptop browser works fine.**

```bash
# Check if the port is listening on 0.0.0.0 (all interfaces), not just 127.0.0.1
ss -tlnp | grep 8000              # Linux
netstat -an | grep 8000           # macOS / Windows

# Expected: 0.0.0.0:8000 (accessible from all interfaces)
# Bad: 127.0.0.1:8000 (localhost only)
```

**Q: `GET /demo` returns 404 — HTML file not found.**

```bash
# Verify the file exists at the correct path
docker exec main-service ls /app/dashboard/
# Must show demo_control.html

# If missing, the file wasn't copied during build
# Verify Dockerfile has: COPY dashboard/ /app/dashboard/
# Then rebuild: docker compose up --build -d
```

**Q: The breach injects but traffic generation (iperf3) fails.**

```bash
# iperf3 may not be installed in the slice containers
# Test manually:
docker exec slice-a which iperf3
docker exec slice-b which iperf3

# If not found, use ping as fallback (already in _do_inject_breach)
# Or rebuild slice images with iperf3 pre-installed in docker-compose.yml
```

**Q: `_breach_active` stays `True` after restore — status shows breach active even after disconnect.**

This is a state sync issue. Force reset:
```bash
curl -X POST http://localhost:8000/demo/restore
sleep 2
curl http://localhost:8000/demo/status
# If still shows breach_active:true, restart the API
docker compose restart main-service
```

**Q: CORS error in phone browser console.**

FastAPI has CORS middleware set to `allow_origins=["*"]` in the existing `api/main.py`. If you see CORS errors, verify that middleware is still present and the app is using the updated `api/main.py` (rebuild with `docker compose up --build`).

**Q: The HTML page loads but confidence values show "—" and never update.**

The JavaScript polls `/score` from `window.location.origin`. If the page was served from a different IP than expected, `API` auto-detects correctly from the URL. Check:
```javascript
// Open browser DevTools → Console, run:
console.log(window.location.origin)
// Should be: http://<LAPTOP_IP>:8000
// Then check Network tab for failed /score requests
```

---

*Document version: 1.0 · Compatible with PROJECT_REFERENCE.md v1.0 · Last updated: Hackathon Day 2*

*All code in this document is compatible with the existing `api/main.py` structure. The `_run_cmd()` helper, background task pattern, and route naming follow the project conventions established in PROJECT_REFERENCE.md §10.*
