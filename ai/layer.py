"""
ai/layer.py
───────────
AI layer for 5G Slice Isolation Validator.

Two features — both read-only, stateless, arm's-length from core systems:

  1. PredictiveForecaster
     Called every ~30s per slice. Receives last 50 telemetry rows from
     /metrics/history. Asks the LLM to reason about trajectory and return
     a structured risk assessment BEFORE the IsolationForest fires.

  2. IncidentReasoner
     Called once per closed incident. Receives the incident record + the
     telemetry window around the breach. Returns a forensic hypothesis
     written into the incidents table as ai_forensic_note.

Both functions call the Groq API directly via requests.
No extra SDK needed — uses the OpenAI-compatible Groq chat completions endpoint.

Environment variables:
    GROQ_API_KEY        — required (set in .env, loaded via docker-compose env_file)
    AI_MODEL            — optional, defaults to llama-3.3-70b-versatile
    AI_FORECASTER_ROWS  — optional, rows fed to forecaster (default 50)
"""

from __future__ import annotations

import os
import json
import logging
import requests as _req
from typing import Optional

log = logging.getLogger(__name__)

_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── IMPORTANT: read env vars at CALL TIME, not import time ──────────────────
# Module-level constants (e.g. _API_KEY = os.environ.get(...)) are evaluated
# once when Python first imports this file. If the container env isn't fully
# initialised yet, or if the module is imported before docker-compose injects
# the env vars, the key will be permanently empty for the process lifetime.
# Using helper functions forces a fresh os.environ lookup on every call.

def _api_key() -> str:
    return os.environ.get("GROQ_API_KEY", "").strip()

def _model() -> str:
    return os.environ.get("AI_MODEL", "llama-3.3-70b-versatile")

def _max_rows() -> int:
    return int(os.environ.get("AI_FORECASTER_ROWS", "50"))


def _strip_fences(text: str) -> str:
    """
    Strip markdown code fences that LLMs sometimes wrap JSON in.
    Handles ```json ... ```, ``` ... ```, and leading/trailing whitespace.
    Also handles truncated responses by attempting to close open JSON objects.
    """
    text = text.strip()
    if text.startswith("```"):
        # skip the opening fence line (e.g. ```json)
        text = text[text.index("\n") + 1:] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    text = text.strip()

    # If JSON appears truncated (no closing brace), attempt to repair it
    # by closing any open string and the object — better than returning None
    if text.startswith("{") and not text.endswith("}"):
        # Close any unterminated string value first
        if text.count('"') % 2 != 0:
            text += '"'
        text += "}"

    return text.strip()


def _call_groq(system: str, user: str, max_tokens: int = 400) -> Optional[str]:
    """
    Make a single call to the Groq Chat Completions API.
    Returns the text response, or None on any error.
    Never raises — the AI layer must never crash the core system.
    """
    key = _api_key()
    if not key:
        log.warning("[AI] GROQ_API_KEY not set — skipping AI call. "
                    "Make sure it is in your .env file and docker-compose "
                    "env_file points to it.")
        return None

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    }
    body = {
        "model":      _model(),
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    import time as _time
    for attempt in range(3):  # up to 3 attempts with backoff on rate-limit
        try:
            log.info("[AI] Calling Groq API — model=%s max_tokens=%d attempt=%d",
                     _model(), max_tokens, attempt + 1)
            resp = _req.post(_API_URL, headers=headers, json=body, timeout=30)

            # Log the status so it's visible in docker logs
            log.info("[AI] Groq response status: %d", resp.status_code)

            if resp.status_code == 429:
                # Rate limited — parse retry-after header or use exponential backoff
                retry_after = int(resp.headers.get("retry-after", 2 ** (attempt + 1)))
                log.warning("[AI] Groq rate limited (429) — waiting %ds before retry", retry_after)
                _time.sleep(min(retry_after, 15))  # cap at 15s
                continue

            if not resp.ok:
                log.warning("[AI] Groq API error %d: %s", resp.status_code, resp.text[:300])
                return None

            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            log.info("[AI] Groq returned %d chars", len(content))
            return content

        except _req.exceptions.Timeout:
            log.warning("[AI] Groq API call timed out after 30s (attempt %d).", attempt + 1)
            if attempt < 2:
                _time.sleep(2)
            continue
        except Exception as exc:
            log.warning("[AI] Groq API call failed: %s", exc)
            return None
    # All attempts exhausted
    log.warning("[AI] All Groq API attempts failed.")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE 1 — PREDICTIVE BREACH FORECASTER
# ══════════════════════════════════════════════════════════════════════════════

_FORECASTER_SYSTEM = """
You are a cybersecurity AI embedded in a 5G network slice isolation monitoring system.
You receive the last 50 telemetry samples (JSON array) for one network slice.
Each row has: timestamp (unix), cpu_pct, mem_mb, net_rx_kb, net_tx_kb, anomaly_score (null if not yet scored).

Your job: analyze the TRAJECTORY of these metrics and predict whether a breach is imminent.
You are running BEFORE the IsolationForest fires — your value is early warning.

Respond with ONLY a valid JSON object in this exact schema:
{
  "risk_level": "stable" | "rising" | "critical",
  "confidence_pct": <integer 0-100, your confidence in this prediction>,
  "features_of_concern": [<list of feature names that are trending anomalously, e.g. "net_rx_kb">],
  "reasoning": "<2-3 sentence explanation of what you see in the trajectory, be specific about which metrics and the direction of change>",
  "recommended_action": "<one short sentence — what the operator should watch or do>"
}

Rules:
- "stable": no concerning trends, normal operation
- "rising": one or more metrics trending upward in a pattern that could precede a breach, but no breach yet
- "critical": strong multi-metric anomaly pattern, breach likely within the next 30-60 seconds
- Be concise. Operators read this in real time.
- Do NOT reproduce the raw data. Summarize the pattern.
- If fewer than 10 rows are present, return risk_level "stable" with reasoning "insufficient data".
""".strip()


def predict_breach_risk(slice_id: str, history_rows: list[dict]) -> dict:
    """
    Analyze telemetry trajectory for a slice and return a risk prediction.
    """
    key_present = bool(_api_key())
    default = {
        "slice_id":            slice_id,
        "risk_level":          "stable",
        "confidence_pct":      0,
        "features_of_concern": [],
        "reasoning":           "Awaiting AI response (may be rate-limited or still loading)." if key_present else "GROQ_API_KEY not set — AI forecasting disabled.",
        "recommended_action":  "Continue monitoring.",
        "ai_available":        key_present,  # key is set, even if this specific call failed
    }

    if not history_rows:
        return default

    rows = _max_rows()
    trimmed = []
    for r in history_rows[-rows:]:
        trimmed.append({
            "timestamp":     r.get("timestamp"),
            "cpu_pct":       round(float(r.get("cpu_pct", 0)), 2),
            "mem_mb":        round(float(r.get("mem_mb", 0)), 2),
            "net_rx_kb":     round(float(r.get("net_rx_kb", 0)), 2),
            "net_tx_kb":     round(float(r.get("net_tx_kb", 0)), 2),
            "anomaly_score": r.get("anomaly_score"),
        })

    user_msg = (
        f"Slice ID: {slice_id}\n"
        f"Sample count: {len(trimmed)}\n"
        f"Telemetry (oldest \u2192 newest):\n"
        f"{json.dumps(trimmed, separators=(',', ':'))}"
    )

    raw = _call_groq(_FORECASTER_SYSTEM, user_msg, max_tokens=600)
    if not raw:
        return default

    try:
        parsed = json.loads(_strip_fences(raw))
        return {
            "slice_id":            slice_id,
            "risk_level":          parsed.get("risk_level", "stable"),
            "confidence_pct":      int(parsed.get("confidence_pct", 0)),
            "features_of_concern": parsed.get("features_of_concern", []),
            "reasoning":           parsed.get("reasoning", ""),
            "recommended_action":  parsed.get("recommended_action", ""),
            "ai_available":        True,
        }
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("[AI] Forecaster JSON parse error: %s | raw=%s", exc, raw[:300])
        return default


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE 2 — AUTONOMOUS INCIDENT REASONER
# ══════════════════════════════════════════════════════════════════════════════

_REASONER_SYSTEM = """
You are a forensic cybersecurity AI for a 5G network slice isolation validator.
You receive a closed incident record and the telemetry window (30 rows before + during the breach).

Your job: produce a structured forensic hypothesis explaining WHAT caused this breach, WHY it happened,
and what the operator should do next. This is NOT a summary — it is causal reasoning.

Respond with ONLY a valid JSON object in this exact schema:
{
  "hypothesis": "<2-3 sentence causal explanation: what type of event, what triggered it, why this slice>",
  "attack_vector": "<one of: cross-slice-flooding | cpu-exhaustion | memory-pressure | coordinated-multi-vector | unknown>",
  "confidence": "<high | medium | low>",
  "pre_breach_signal": "<describe what the telemetry looked like in the 30s before the breach fired>",
  "recommended_action": "<specific action: e.g. 'Verify external network attachments to slice-a and check routing policy for PDU session isolation'>",
  "severity": "<critical | high | medium | low>"
}

Rules:
- Be specific. Name actual metric values if relevant (e.g. "net_rx_kb spiked to 1400 KB/s").
- Do NOT say "the model detected" — reason about the actual network behavior.
- If the pattern looks like a software-injected simulation rather than a real attack, note that.
- Keep each field concise — operators read this in a live dashboard.
""".strip()


def reason_about_incident(incident: dict, telemetry_window: list[dict]) -> Optional[str]:
    """
    Generate a forensic note for a closed incident.
    Returns a clean JSON string to store as ai_forensic_note, or None on failure.
    """
    if not incident:
        return None

    trimmed_telemetry = []
    for r in telemetry_window[-30:]:
        trimmed_telemetry.append({
            "timestamp":     r.get("timestamp"),
            "cpu_pct":       round(float(r.get("cpu_pct", 0)), 2),
            "mem_mb":        round(float(r.get("mem_mb", 0)), 2),
            "net_rx_kb":     round(float(r.get("net_rx_kb", 0)), 2),
            "net_tx_kb":     round(float(r.get("net_tx_kb", 0)), 2),
            "anomaly_score": r.get("anomaly_score"),
        })

    user_msg = (
        f"Incident record:\n{json.dumps(incident, default=str, indent=2)}\n\n"
        f"Telemetry window (up to 30 rows around the breach):\n"
        f"{json.dumps(trimmed_telemetry, separators=(',', ':'))}"
    )

    raw = _call_groq(_REASONER_SYSTEM, user_msg, max_tokens=1024)
    if not raw:
        return None

    # Strip markdown fences then validate before storing
    try:
        cleaned = _strip_fences(raw)
        json.loads(cleaned)   # validate — raises if not real JSON
        return cleaned
    except json.JSONDecodeError:
        # Last resort: extract whatever JSON object we can find in the raw text
        import re as _re
        match = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if match:
            candidate = match.group(0)
            try:
                json.loads(candidate)
                log.warning("[AI] Reasoner used regex-extracted JSON fallback.")
                return candidate
            except json.JSONDecodeError:
                pass
        log.warning("[AI] Reasoner returned non-JSON after all attempts. raw=%s", raw[:400])
        return None