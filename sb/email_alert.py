"""
sb/email_alert.py
-----------------
Sends an HTML alert email via Gmail SMTP when an attack is detected.

Required env vars (add to your .env file):
    ALERT_EMAIL_FROM=you@gmail.com
    ALERT_EMAIL_TO=you@gmail.com          # can be same address
    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx  # 16-char Google App Password

The 16-digit App Password from myaccount.google.com can be reused across
projects — it is tied to your Google account, not to any specific app.

Usage:
    from sb.email_alert import send_attack_alert
    send_attack_alert(slice_id="slice-a", attack_type="CPU Starvation",
                      confidence=28.4, features={...})
"""

from __future__ import annotations

import os
import logging
import smtplib
import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

# ── Colour map for attack badge ───────────────────────────────────
_ATTACK_COLOURS: dict[str, str] = {
    "CPU Starvation":     "#e53e3e",   # red
    "Memory Exhaustion":  "#dd6b20",   # orange
    "Network Breach":     "#805ad5",   # purple
    "Combined Attack":    "#d53f8c",   # pink
}
_DEFAULT_COLOUR = "#718096"            # grey for unknown


def _build_html(slice_id: str, attack_type: str | None,
                 confidence: float, features: dict, restore_url: str = "",
                 dashboard_url: str = "") -> str:
    """Return a styled HTML email body."""
    colour   = _ATTACK_COLOURS.get(attack_type or "", _DEFAULT_COLOUR)
    label    = attack_type or "Unknown Anomaly"
    ts       = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    conf_bar = max(0, min(100, confidence))

    rows = "".join(
        f"<tr><td style='padding:6px 12px;color:#4a5568;'>{k}</td>"
        f"<td style='padding:6px 12px;font-weight:600;color:#1a202c;'>{v:.2f}</td></tr>"
        for k, v in features.items()
    )

    restore_btn = (
        f"<a href='{restore_url}' "
        f"style='background:#276749;color:#ffffff;text-decoration:none;"
        f"padding:12px 24px;border-radius:6px;font-weight:600;"
        f"font-size:14px;display:inline-block;'>"
        f"🔒 Restore Process Isolation</a>"
    ) if restore_url else ""

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f7fafc;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f7fafc;padding:32px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:8px;
                    box-shadow:0 2px 8px rgba(0,0,0,.08);overflow:hidden;">

        <!-- Header -->
        <tr>
          <td style="background:{colour};padding:24px 32px;">
            <p style="margin:0;font-size:11px;color:rgba(255,255,255,.8);
                      text-transform:uppercase;letter-spacing:1px;">
              🔴 Security Alert — Network Process Isolation Validator
            </p>
            <h1 style="margin:8px 0 0;font-size:22px;color:#ffffff;">
              {label} Detected
            </h1>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:28px 32px;">

            <!-- Slice + timestamp -->
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="margin-bottom:24px;">
              <tr>
                <td style="background:#f7fafc;border-radius:6px;
                           padding:14px 18px;border-left:4px solid {colour};">
                  <p style="margin:0;font-size:12px;color:#718096;">Affected Process</p>
                  <p style="margin:4px 0 0;font-size:18px;font-weight:700;
                            color:#1a202c;">{slice_id.replace("slice", "process")}</p>
                </td>
                <td width="16"></td>
                <td style="background:#f7fafc;border-radius:6px;
                           padding:14px 18px;">
                  <p style="margin:0;font-size:12px;color:#718096;">Detected At</p>
                  <p style="margin:4px 0 0;font-size:14px;font-weight:600;
                            color:#1a202c;">{ts}</p>
                </td>
              </tr>
            </table>

            <!-- Confidence bar -->
            <p style="margin:0 0 6px;font-size:13px;color:#4a5568;font-weight:600;">
              Isolation Confidence
            </p>
            <div style="background:#e2e8f0;border-radius:4px;height:12px;
                        overflow:hidden;margin-bottom:4px;">
              <div style="width:{conf_bar}%;background:{colour};height:100%;
                          border-radius:4px;"></div>
            </div>
            <p style="margin:0 0 24px;font-size:20px;font-weight:700;color:{colour};">
              {confidence:.1f}%
            </p>

            <!-- Feature table -->
            <p style="margin:0 0 10px;font-size:13px;color:#4a5568;font-weight:600;">
              Telemetry at Time of Detection
            </p>
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="border:1px solid #e2e8f0;border-radius:6px;
                          border-collapse:collapse;font-size:14px;">
              <tr style="background:#f7fafc;">
                <th style="padding:8px 12px;text-align:left;color:#718096;
                           font-weight:600;border-bottom:1px solid #e2e8f0;">
                  Metric
                </th>
                <th style="padding:8px 12px;text-align:left;color:#718096;
                           font-weight:600;border-bottom:1px solid #e2e8f0;">
                  Value
                </th>
              </tr>
              {rows}
            </table>

            <!-- CTA -->
            <div style="margin-top:28px;text-align:center;">
              <a href="{dashboard_url or 'http://localhost:8501'}"
                 style="background:{colour};color:#ffffff;
                        text-decoration:none;padding:12px 24px;
                        border-radius:6px;font-weight:600;
                        font-size:14px;display:inline-block;
                        margin-right:12px;">
                Open Dashboard →
              </a>
              {restore_btn}
            </div>

          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f7fafc;padding:16px 32px;
                     border-top:1px solid #e2e8f0;">
            <p style="margin:0;font-size:11px;color:#a0aec0;text-align:center;">
              5G/6G Network Process Isolation Validator — automated alert
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>
""".strip()


def send_attack_alert(
    slice_id: str,
    attack_type: str | None,
    confidence: float,
    features: dict,
    restore_url: str = "",
    dashboard_url: str = "",
) -> bool:
    """
    Send an HTML alert email via Gmail SMTP.

    Returns True if sent successfully, False otherwise (never raises).
    Silently skips if env vars are not configured.
    """
    sender   = os.environ.get("ALERT_EMAIL_FROM", "").strip()
    receiver = os.environ.get("ALERT_EMAIL_TO", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()

    if not sender or not receiver or not password:
        log.debug(
            "[email_alert] Skipping — ALERT_EMAIL_FROM / ALERT_EMAIL_TO / "
            "GMAIL_APP_PASSWORD not all set."
        )
        return False

    label   = attack_type or "Unknown Anomaly"
    subject = f"🔴 [{slice_id.replace('slice', 'process')}] {label} — Isolation Confidence {confidence:.1f}%"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = receiver

    # Plain-text fallback
    plain = (
        f"ALERT: {label} detected on {slice_id.replace('slice', 'process')}\n"
        f"Isolation Confidence: {confidence:.1f}%\n"
        f"Features: {features}\n"
        f"Time: {datetime.datetime.utcnow().isoformat()} UTC\n"
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(_build_html(slice_id, attack_type, confidence, features, restore_url, dashboard_url), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        log.info("[email_alert] Alert sent → %s  subject=%s", receiver, subject)
        return True
    except Exception as exc:
        log.warning("[email_alert] Failed to send alert: %s", exc)
        return False