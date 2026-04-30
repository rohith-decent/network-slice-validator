# Implementation Plan: Demo Control & Email Alert Changes

---

## Overview

Two changes are required:

1. **Demo Control UI** (`dashboard/demo_control.html`) — Replace the single "INJECT BREACH" button with per-slice attack options (Slice A or Slice B), and remove the "RESTORE ISOLATION" button from the mobile UI entirely.
2. **Email Alerts + Restore UI** (`sb/email_alert.py` + new file `dashboard/restore.html`) — Add a "Restore Isolation" link in alert emails that opens a dedicated restore page, and add a new API endpoint to support it.

---

## Change 1 — Demo Control HTML

**File:** `dashboard/demo_control.html`

### What to do

#### Step 1.1 — Remove the "RESTORE ISOLATION" button

Find and delete this block in the `<!-- Action Buttons -->` section:

```html
<button class="btn btn-restore" id="btnRestore" onclick="triggerRestore()">
  <span class="btn-icon">🔒</span>
  RESTORE ISOLATION
</button>
```

Also remove the `btn-restore` CSS rule (optional cleanup):

```css
.btn-restore {
  background: linear-gradient(135deg, #1a7a1a, #3fb950);
  color: #fff;
  box-shadow: 0 4px 20px #3fb95040;
}
.btn-restore:hover:not(:disabled) { box-shadow: 0 6px 28px #3fb95060; }
```

#### Step 1.2 — Replace "INJECT BREACH" with two per-slice buttons

Replace the single inject button:

```html
<!-- BEFORE -->
<button class="btn btn-inject" id="btnInject" onclick="triggerInject()">
  <span class="btn-icon">💥</span>
  INJECT BREACH
</button>
```

With two buttons:

```html
<!-- AFTER -->
<button class="btn btn-inject" id="btnInjectA" onclick="triggerInject('slice-a')">
  <span class="btn-icon">💥</span>
  ATTACK SLICE A
</button>
<button class="btn btn-inject" id="btnInjectB" onclick="triggerInject('slice-b')">
  <span class="btn-icon">💥</span>
  ATTACK SLICE B
</button>
```

#### Step 1.3 — Update the `triggerInject` JavaScript function

The current `triggerInject()` function sends a POST to `/demo/inject` with no body. Update it to accept a `sliceId` parameter and pass it:

```js
// BEFORE
async function triggerInject() {
  document.getElementById('btnInject').disabled = true;
  showToast('Sending breach injection command…', 'info');
  try {
    const r = await fetch(`${API}/demo/inject`, { method: 'POST' });
    ...
  }
}
```

```js
// AFTER
async function triggerInject(sliceId) {
  const btnId = sliceId === 'slice-a' ? 'btnInjectA' : 'btnInjectB';
  document.getElementById(btnId).disabled = true;
  showToast(`Sending breach injection command for ${sliceId}…`, 'info');
  try {
    const r = await fetch(`${API}/demo/inject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slice_id: sliceId })
    });
    const d = await r.json();
    if (d.status === 'injected') {
      injectTime = Date.now();
      showToast(`✅ Breach injected on ${sliceId}! Watch the dashboard gauge drop in ~15 seconds.`, 'success');
    } else if (d.status === 'already_active') {
      showToast(`⚠️ Breach already active on ${sliceId}.`, 'error');
      document.getElementById(btnId).disabled = false;
    } else {
      showToast(`Response: ${d.message}`, 'info');
    }
  } catch (e) {
    showToast(`❌ Connection error: ${e.message}`, 'error');
    document.getElementById(btnId).disabled = false;
  }
}
```

#### Step 1.4 — Remove `triggerRestore()` function

Delete the entire `triggerRestore()` function from the `<script>` block since the button no longer exists.

#### Step 1.5 — Update `pollStatus` to handle the two buttons

The current `pollStatus` disables/enables a single `btnInject` and `btnRestore`. Update it to manage both new buttons:

```js
// BEFORE
document.getElementById('btnInject').disabled  = breachActive;
document.getElementById('btnRestore').disabled = !breachActive;

// AFTER
document.getElementById('btnInjectA').disabled = breachActive;
document.getElementById('btnInjectB').disabled = breachActive;
```

#### Step 1.6 — Update the subtitle text

```html
<!-- BEFORE -->
<p class="subtitle">Tap to inject or restore network slice breaches</p>

<!-- AFTER -->
<p class="subtitle">Tap to attack Slice A or Slice B independently</p>
```

---

## Change 2 — API: Accept `slice_id` in `/demo/inject`

**File:** `api/main.py`

The current `/demo/inject` endpoint calls `_do_inject_breach()` which always targets `slice-a → slice_b_net`. You need to make it accept a `slice_id` parameter and route accordingly.

### Step 2.1 — Add a request body model

Near the top of `api/main.py` where other Pydantic models are defined, add:

```python
class InjectRequest(BaseModel):
    slice_id: str = "slice-a"   # defaults to slice-a for backward compat
```

### Step 2.2 — Update `_do_inject_breach` to accept a target slice

```python
# BEFORE
def _do_inject_breach():
    global _breach_active
    log.info("[demo] Injecting breach → %s", BREACH_NETWORK)
    _run_cmd(["docker", "network", "connect", BREACH_NETWORK, "slice-a"])
    _run_cmd(["docker", "exec", "-d", "slice-b", "iperf3", "-s"])
    _run_cmd(["docker", "exec", "-d", "slice-a", "iperf3", "-c", "slice-b", "-t", "30", "-b", "5M"])
    _breach_active = True
    ...
```

```python
# AFTER
def _do_inject_breach(target_slice: str = "slice-a"):
    global _breach_active
    # Determine attacker and victim based on target slice
    if target_slice == "slice-b":
        attacker, victim = "slice-b", "slice-a"
        breach_net = f"{COMPOSE_PROJECT}_slice_a_net"
    else:
        attacker, victim = "slice-a", "slice-b"
        breach_net = BREACH_NETWORK  # slice_b_net

    log.info("[demo] Injecting breach: %s → %s via %s", attacker, victim, breach_net)
    _run_cmd(["docker", "network", "connect", breach_net, attacker])
    _run_cmd(["docker", "exec", "-d", victim, "iperf3", "-s"])
    _run_cmd(["docker", "exec", "-d", attacker, "iperf3", "-c", victim, "-t", "30", "-b", "5M"])
    _breach_active = True
    ...
```

> **Note:** You may need to confirm the exact network name for `slice_a_net` in your `docker-compose.yml`. Check the `networks:` section and replicate the pattern used for `BREACH_NETWORK`.

### Step 2.3 — Update the `/demo/inject` endpoint

```python
# BEFORE
@app.post("/demo/inject")
def inject_breach(background_tasks: BackgroundTasks):
    if _breach_active:
        return {"status": "already_active", "breach_active": True}
    background_tasks.add_task(_do_inject_breach)
    return {"status": "injected", "message": "Breach started. Watch dashboard in ~15s.", "breach_active": True}
```

```python
# AFTER
@app.post("/demo/inject")
def inject_breach(background_tasks: BackgroundTasks, body: InjectRequest = Body(default=InjectRequest())):
    if _breach_active:
        return {"status": "already_active", "breach_active": True}
    background_tasks.add_task(_do_inject_breach, body.slice_id)
    return {"status": "injected", "message": f"Breach started on {body.slice_id}. Watch dashboard in ~15s.", "breach_active": True}
```

Make sure `Body` is imported from `fastapi`:
```python
from fastapi import Body
```

---

## Change 3 — New Restore Page (`dashboard/restore.html`)

Create a new file `dashboard/restore.html`. This is a standalone mobile-friendly page that the email link points to. It shows a single "Restore Isolation" button and the current confidence levels.

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0" />
  <title>Restore Isolation</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0d1117;
      color: #c9d1d9;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 32px 16px;
    }
    .card {
      width: 100%;
      max-width: 420px;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 16px;
      padding: 32px 28px;
      text-align: center;
    }
    .alert-badge {
      display: inline-block;
      background: #2d1117;
      border: 1px solid #f85149;
      color: #f85149;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      border-radius: 20px;
      padding: 4px 14px;
      margin-bottom: 20px;
    }
    h1 { font-size: 20px; font-weight: 700; color: #f0f6fc; margin-bottom: 8px; }
    .sub { font-size: 13px; color: #6b7280; margin-bottom: 28px; }
    .conf-row { display: flex; gap: 16px; margin-bottom: 28px; }
    .conf-block { flex: 1; background: #0d1117; border-radius: 10px; padding: 14px; }
    .conf-name { font-size: 11px; color: #8b949e; margin-bottom: 4px; }
    .conf-value { font-size: 26px; font-weight: 700; }
    .high { color: #3fb950; } .medium { color: #d29922; } .low { color: #f85149; }
    .btn-restore {
      width: 100%;
      border: none;
      border-radius: 14px;
      font-size: 17px;
      font-weight: 700;
      cursor: pointer;
      padding: 22px;
      background: linear-gradient(135deg, #1a7a1a, #3fb950);
      color: #fff;
      box-shadow: 0 4px 20px #3fb95040;
      transition: transform 0.1s, opacity 0.2s;
    }
    .btn-restore:active { transform: scale(0.97); }
    .btn-restore:disabled { opacity: 0.45; cursor: not-allowed; }
    .toast {
      margin-top: 20px;
      padding: 12px 16px;
      border-radius: 10px;
      font-size: 13px;
      border-left: 4px solid;
      display: none;
    }
    .toast.success { background: #0d1a0d; border-color: #3fb950; color: #7ee787; display: block; }
    .toast.error   { background: #2d1117; border-color: #f85149; color: #f97071; display: block; }
    .footer { margin-top: 28px; font-size: 11px; color: #3d444d; }
  </style>
</head>
<body>
  <div class="card">
    <div class="alert-badge">🔴 Breach Detected</div>
    <h1>Restore Network Isolation</h1>
    <p class="sub">Tap the button below to immediately restore slice isolation and begin confidence recovery.</p>

    <div class="conf-row">
      <div class="conf-block">
        <div class="conf-name">slice-a</div>
        <div class="conf-value low" id="confA">—</div>
      </div>
      <div class="conf-block">
        <div class="conf-name">slice-b</div>
        <div class="conf-value low" id="confB">—</div>
      </div>
    </div>

    <button class="btn-restore" id="btnRestore" onclick="triggerRestore()">
      🔒 RESTORE ISOLATION
    </button>

    <div class="toast" id="toast"></div>
    <div class="footer">5G/6G Network Slicing Isolation Validator</div>
  </div>

<script>
  const API = window.location.origin;

  function showToast(msg, type) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = `toast ${type}`;
  }

  function confClass(v) {
    if (v >= 70) return 'high';
    if (v >= 40) return 'medium';
    return 'low';
  }

  async function pollScores() {
    try {
      const r = await fetch(`${API}/score`, { signal: AbortSignal.timeout(3000) });
      if (!r.ok) return;
      const data = await r.json();
      const scores = Array.isArray(data) ? data : [data];
      scores.forEach(s => {
        if (!s.slice_id || s.isolation_confidence === null) return;
        const elId = s.slice_id === 'slice-a' ? 'confA' : 'confB';
        const el = document.getElementById(elId);
        if (el) {
          el.textContent = `${s.isolation_confidence.toFixed(0)}%`;
          el.className = `conf-value ${confClass(s.isolation_confidence)}`;
        }
      });
    } catch (_) {}
  }

  async function triggerRestore() {
    document.getElementById('btnRestore').disabled = true;
    showToast('Sending restore command…', 'info');
    try {
      const r = await fetch(`${API}/demo/restore`, { method: 'POST' });
      if (r.ok) {
        showToast('✅ Isolation restoring. Confidence will recover in 30–60 seconds.', 'success');
      } else {
        showToast('❌ Restore failed. Check API logs.', 'error');
        document.getElementById('btnRestore').disabled = false;
      }
    } catch (e) {
      showToast(`❌ Connection error: ${e.message}`, 'error');
      document.getElementById('btnRestore').disabled = false;
    }
  }

  pollScores();
  setInterval(pollScores, 3000);
</script>
</body>
</html>
```

---

## Change 4 — Serve the Restore Page from the API

**File:** `api/main.py`

Add a new GET endpoint to serve `restore.html`, similar to how `/demo` serves `demo_control.html`:

```python
@app.get("/restore", response_class=HTMLResponse)
def restore_page():
    html_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "dashboard", "restore.html")
    )
    if not os.path.exists(html_path):
        return HTMLResponse("<h1>restore.html not found</h1>", status_code=404)
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())
```

---

## Change 5 — Update Email Alert with Restore URL

**File:** `sb/email_alert.py`

### Step 5.1 — Add `restore_url` parameter to `_build_html`

The function signature currently is:
```python
def _build_html(slice_id, attack_type, confidence, features) -> str:
```

Change it to:
```python
def _build_html(slice_id, attack_type, confidence, features, restore_url: str = "") -> str:
```

### Step 5.2 — Add the Restore button in the email HTML

Find the existing CTA block inside `_build_html`:

```python
            <!-- CTA -->\n            <div style=\"margin-top:28px;text-align:center;\">\n              <a href=\"http://localhost:8501\"\n                 style=\"background:{colour};color:#ffffff;\n                        text-decoration:none;padding:12px 28px;\n                        border-radius:6px;font-weight:600;\n                        font-size:14px;display:inline-block;\">\n                Open Dashboard →\n              </a>\n            </div>
```

Replace it with two buttons side by side:

```python
            <!-- CTA -->\n            <div style=\"margin-top:28px;text-align:center;\">\n              <a href=\"http://localhost:8501\"\n                 style=\"background:{colour};color:#ffffff;\n                        text-decoration:none;padding:12px 24px;\n                        border-radius:6px;font-weight:600;\n                        font-size:14px;display:inline-block;\n                        margin-right:12px;\">\n                Open Dashboard →\n              </a>\n              {restore_btn}\n            </div>
```

And build `restore_btn` conditionally at the top of the function:

```python
restore_btn = (
    f"<a href='{restore_url}' "
    f"style='background:#276749;color:#ffffff;text-decoration:none;"
    f"padding:12px 24px;border-radius:6px;font-weight:600;"
    f"font-size:14px;display:inline-block;'>"
    f"🔒 Restore Isolation</a>"
) if restore_url else ""
```

### Step 5.3 — Pass `restore_url` through `send_attack_alert`

Update the public function signature:

```python
# BEFORE
def send_attack_alert(slice_id, attack_type, confidence, features) -> bool:

# AFTER
def send_attack_alert(slice_id, attack_type, confidence, features, restore_url: str = "") -> bool:
```

And pass it to `_build_html`:

```python
# BEFORE
msg.attach(MIMEText(_build_html(slice_id, attack_type, confidence, features), "html"))

# AFTER
msg.attach(MIMEText(_build_html(slice_id, attack_type, confidence, features, restore_url), "html"))
```

### Step 5.4 — Pass the restore URL when calling `send_attack_alert` from the API

**File:** `api/main.py`

Find the call site in `_correlate_slice` (around line 318):

```python
send_attack_alert(
    slice_id=slice_id,
    ...
)
```

Update it to include `restore_url`:

```python
# Determine the public-facing host for the restore link
host = os.environ.get("PUBLIC_API_HOST", "http://localhost:8000")
send_attack_alert(
    slice_id=slice_id,
    attack_type=attack_type,
    confidence=confidence,
    features=features,
    restore_url=f"{host}/restore",
)
```

### Step 5.5 — Add `PUBLIC_API_HOST` env var

Add to your `.env` file (or `docker-compose.yml` environment section):

```env
PUBLIC_API_HOST=http://<YOUR_SERVER_IP>:8000
```

Replace `<YOUR_SERVER_IP>` with the actual IP or domain reachable from the email recipient's device (e.g., `http://192.168.1.42:8000`).

---

## File Summary

| File | Action |
|---|---|
| `dashboard/demo_control.html` | Remove Restore button, add Attack Slice A / Attack Slice B buttons, update JS |
| `dashboard/restore.html` | **Create new** — standalone restore page with confidence display |
| `api/main.py` | Add `/restore` endpoint, update `/demo/inject` to accept `slice_id`, pass `restore_url` to email alerts |
| `sb/email_alert.py` | Add `restore_url` param to `_build_html` and `send_attack_alert`, render restore button in email |
| `.env` | Add `PUBLIC_API_HOST=http://<YOUR_IP>:8000` |

---

## Testing Checklist

- [ ] Open `/demo` on mobile — confirm two separate attack buttons (Slice A, Slice B) appear
- [ ] Confirm "RESTORE ISOLATION" button is gone from the demo control page
- [ ] Tap "ATTACK SLICE A" — confirm only Slice A gauge drops on the dashboard
- [ ] Tap "ATTACK SLICE B" — confirm only Slice B gauge drops on the dashboard
- [ ] Trigger a breach and wait for the email alert to arrive
- [ ] Click the "Restore Isolation" link in the email — confirm it opens `/restore`
- [ ] Tap the "RESTORE ISOLATION" button on the restore page — confirm confidence recovers in 30–60s on the dashboard
- [ ] Confirm the `/restore` page polls and displays live confidence values for both slices
