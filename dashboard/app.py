"""
dashboard/app.py
────────────────
Streamlit operator dashboard for 5G Slice Isolation Monitor.

Pages:
  1. 🛡️ Live Monitor       – gauges, timeline, metric cards, alert feed
  2. 🔍 Anomaly Classifier  – per-attack-type breakdown, Z-score analysis
  3. 📋 Audit & Incident Log – incident history + audit table + PDF export
  4. 📊 SLA & Model Health  – NetworkX SLA graph + model health panel
"""

import io
import os
import time
import requests
import pandas as pd
import networkx as nx
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from plotly.subplots import make_subplots
from datetime import datetime
from typing import Optional

# PDF generation
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

API_BASE    = os.environ.get("API_BASE", "http://localhost:8000")
SLICE_NAMES = [s.strip() for s in os.environ.get("SLICE_NAMES", "slice-a,slice-b").split(",")]
REFRESH_S   = 3

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="5G Slice Isolation Monitor",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .main { background-color: #0d1117; }
    .block-container { padding-top: 1rem; }
    .stMetric {
        background: #161b22;
        border-radius: 8px;
        padding: 12px;
        border: 1px solid #30363d;
    }
    .stMetric label {
        color: #8b949e !important;
        font-size: 0.75rem !important;
        text-transform: uppercase;
    }
    .alert-card {
        background: #2d1117;
        border-left: 4px solid #f85149;
        border-radius: 4px;
        padding: 8px 12px;
        margin: 4px 0;
        font-size: 0.85rem;
        color: #f0f6fc;
    }
    .normal-card {
        background: #0d1a0d;
        border-left: 4px solid #3fb950;
        border-radius: 4px;
        padding: 8px 12px;
        margin: 4px 0;
        font-size: 0.85rem;
        color: #f0f6fc;
    }
    .incident-open {
        background: #2d1117;
        border-left: 4px solid #f85149;
        border-radius: 6px;
        padding: 10px 14px;
        margin: 6px 0;
        font-size: 0.88rem;
        color: #f0f6fc;
    }
    .incident-closed {
        background: #161b22;
        border-left: 4px solid #3fb950;
        border-radius: 6px;
        padding: 10px 14px;
        margin: 6px 0;
        font-size: 0.88rem;
        color: #8b949e;
    }
    h1 { color: #58a6ff !important; }
    h2, h3 { color: #c9d1d9 !important; }
</style>
""", unsafe_allow_html=True)


# ── API helpers ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=REFRESH_S, show_spinner=False)
def fetch_scores() -> list[dict]:
    try:
        r = requests.get(f"{API_BASE}/score", timeout=3)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def get_scores_with_fallback() -> dict:
    """
    Fetch latest scores and merge with last-known-good values stored in
    session state. This prevents un-injected slices from dropping to 0
    when the API is momentarily slow or a fetch returns partial data.
    """
    if "last_good_scores" not in st.session_state:
        st.session_state["last_good_scores"] = {}

    fresh = fetch_scores()
    for s in fresh:
        sid = s.get("slice_id")
        if sid and s.get("isolation_confidence") is not None:
            st.session_state["last_good_scores"][sid] = s

    return st.session_state["last_good_scores"]


@st.cache_data(ttl=REFRESH_S, show_spinner=False)
def fetch_history(slice_id: str, limit: int = 120) -> pd.DataFrame:
    try:
        r = requests.get(
            f"{API_BASE}/metrics/history",
            params={"slice_id": slice_id, "limit": limit},
            timeout=3,
        )
        r.raise_for_status()
        df = pd.DataFrame(r.json())
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        df = df.sort_values("timestamp")
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=10, show_spinner=False)
def fetch_health() -> dict:
    try:
        r = requests.get(f"{API_BASE}/health", timeout=2)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"status": "unreachable", "model_loaded": False, "db_rows": 0}


@st.cache_data(ttl=5, show_spinner=False)
def fetch_incidents(limit: int = 100) -> list[dict]:
    try:
        r = requests.get(f"{API_BASE}/incidents", params={"limit": limit}, timeout=3)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


@st.cache_data(ttl=5, show_spinner=False)
def fetch_audit_log(
    slice_id: Optional[str] = None,
    limit: int = 200,
    anomaly_only: bool = False,
) -> pd.DataFrame:
    try:
        params = {"limit": limit, "anomaly_only": str(anomaly_only).lower()}
        if slice_id:
            params["slice_id"] = slice_id
        r = requests.get(f"{API_BASE}/audit-log", params=params, timeout=5)
        r.raise_for_status()
        df = pd.DataFrame(r.json())
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
            df = df.sort_values("timestamp", ascending=False)
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=30, show_spinner=False)
def fetch_sla(slice_id: str, window: str) -> dict:
    try:
        r = requests.get(
            f"{API_BASE}/sla",
            params={"slice_id": slice_id, "window": window},
            timeout=3,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"compliance": 100.0, "total": 0, "anomalous": 0}


@st.cache_data(ttl=30, show_spinner=False)
def fetch_model_status() -> dict:
    try:
        r = requests.get(f"{API_BASE}/model/status", timeout=3)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def fetch_injection_status() -> list[str]:
    try:
        r = requests.get(f"{API_BASE}/inject-attack/status", timeout=2)
        r.raise_for_status()
        return r.json().get("active_injections", [])
    except Exception:
        return []


# ── AI Layer fetch helpers ─────────────────────────────────────────────────────

@st.cache_data(ttl=30, show_spinner=False)
def fetch_ai_forecast(slice_id: str) -> dict:
    """Fetch predictive breach risk from AI layer. 30s cache to avoid hammering API."""
    try:
        r = requests.get(
            f"{API_BASE}/ai/forecast",
            params={"slice_id": slice_id, "limit": 50},
            timeout=25,  # AI call can take up to 20s
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"risk_level": "stable", "ai_available": False, "reasoning": "AI layer unavailable."}


def fetch_ai_incident_note(incident_id: int) -> dict:
    """Fetch or generate AI forensic note for a closed incident. Not cached — note is generated once."""
    try:
        r = requests.get(f"{API_BASE}/ai/incident/{incident_id}/reason", timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


@st.cache_data(ttl=60, show_spinner=False)
def fetch_ai_status() -> dict:
    try:
        r = requests.get(f"{API_BASE}/ai/status", timeout=3)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"ai_available": False}


# ── Attack badge ───────────────────────────────────────────────────────────────

ATTACK_BADGE: dict[str, tuple[str, str, str]] = {
    "CPU Starvation":    ("🔴", "#b91c1c", "CPU Starvation"),
    "Memory Exhaustion": ("🟠", "#b45309", "Memory Exhaustion"),
    "Network Breach":    ("🟣", "#7c3aed", "Network Breach"),
    "Combined Attack":   ("⚫", "#374151", "Combined Attack"),
    "Unknown Anomaly":   ("⚠️", "#374151", "Unknown Anomaly"),
}


def _badge_html(attack_type: Optional[str]) -> str:
    if not attack_type:
        attack_type = "Unknown Anomaly"
    icon, bg, label = ATTACK_BADGE.get(attack_type, ("⚠️", "#374151", attack_type))
    return (
        f'<span style="background:{bg};color:#fff;padding:2px 8px;'
        f'border-radius:12px;font-size:0.72rem;font-weight:700;'
        f'letter-spacing:0.03em;">{icon} {label}</span>'
    )


def confidence_color(val: float | None) -> str:
    if val is None:
        return "#8b949e"
    if val >= 70:
        return "#3fb950"
    elif val >= 40:
        return "#d29922"
    return "#f85149"


# ── AI Predictive Panel ────────────────────────────────────────────────────────

_RISK_CONFIG = {
    "stable":   {"icon": "🟢", "label": "STABLE",   "color": "#3fb950", "bg": "#0d1a0d", "border": "#3fb950"},
    "rising":   {"icon": "🟡", "label": "RISING",   "color": "#d29922", "bg": "#1a1400", "border": "#d29922"},
    "critical": {"icon": "🔴", "label": "CRITICAL", "color": "#f85149", "bg": "#2d1117", "border": "#f85149"},
}


def render_ai_forecast_panel(selected_slices: list[str]):
    """
    Renders the AI Predictive Breach Forecaster panel.
    Sits above the IsolationForest gauge — fires BEFORE the gauge drops.
    """
    ai_status = fetch_ai_status()
    if not ai_status.get("ai_available"):
        st.info(
            "🤖 AI Forecaster offline — set `GROQ_API_KEY` in your `.env` file to enable predictive breach warnings.",
            icon="ℹ️",
        )
        return

    st.markdown("### 🤖 AI Predictive Breach Forecaster")
    st.caption(
        "Claude analyzes the last 50 telemetry samples and predicts trajectory "
        "— fires **before** the IsolationForest gauge drops."
    )

    forecast_cols = st.columns(len(selected_slices))
    for i, name in enumerate(selected_slices):
        with forecast_cols[i]:
            with st.spinner(f"Analyzing {name}…"):
                forecast = fetch_ai_forecast(name)

            import html as _html
            risk      = forecast.get("risk_level", "stable")
            cfg       = _RISK_CONFIG.get(risk, _RISK_CONFIG["stable"])
            conf_pct  = forecast.get("confidence_pct", 0)
            # Escape AI-generated text to prevent broken HTML rendering
            reasoning = _html.escape(forecast.get("reasoning", ""))
            action    = _html.escape(forecast.get("recommended_action", ""))
            concerns  = forecast.get("features_of_concern", [])
            available = forecast.get("ai_available", False)

            concern_tags = " ".join(
                f'<span style="background:#1f2937;color:#9ca3af;padding:1px 6px;'
                f'border-radius:8px;font-size:0.70rem;">{_html.escape(str(c))}</span>'
                for c in concerns
            ) if concerns else ""

            concern_row = f'<div style="margin-bottom:6px;">{concern_tags}</div>' if concern_tags else ""
            st.markdown(
                f'<div style="background:{cfg["bg"]};border:1px solid {cfg["border"]};'
                f'border-radius:10px;padding:14px 16px;margin-bottom:8px;">'
                f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">'
                f'<span style="font-size:1.4rem;">{cfg["icon"]}</span>'
                f'<span style="color:{cfg["color"]};font-weight:700;font-size:1rem;'
                f'letter-spacing:0.05em;">{_html.escape(name)} — BREACH RISK: {cfg["label"]}</span>'
                f'<span style="color:#8b949e;font-size:0.78rem;margin-left:auto;">'
                f'AI confidence: {conf_pct}%</span>'
                f'</div>'
                f'{concern_row}'
                f'<div style="color:#c9d1d9;font-size:0.83rem;line-height:1.5;margin-bottom:6px;">'
                f'{reasoning}</div>'
                f'<div style="color:#8b949e;font-size:0.78rem;font-style:italic;">'
                f'&#9654; {action}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            if not available:
                st.caption("⚠️ AI layer returned no result — check API key and logs.")


# ── Gauge chart ────────────────────────────────────────────────────────────────

def make_gauge(title: str, value: float | None) -> go.Figure:
    display_val = value if value is not None else 0
    color = confidence_color(value)
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=display_val,
        title={"text": title, "font": {"size": 14, "color": "#c9d1d9"}},
        number={"suffix": "%", "font": {"size": 24, "color": color}},
        delta={
            "reference": 70,
            "increasing": {"color": "#3fb950"},
            "decreasing": {"color": "#f85149"},
        },
        gauge={
            "axis": {"range": [0, 100], "tickcolor": "#30363d",
                     "tickfont": {"color": "#8b949e"}},
            "bar":  {"color": color, "thickness": 0.3},
            "bgcolor":     "#161b22",
            "bordercolor": "#30363d",
            "borderwidth": 1,
            "steps": [
                {"range": [0,  40],  "color": "#2d1117"},
                {"range": [40, 70],  "color": "#1a1500"},
                {"range": [70, 100], "color": "#0d1a0d"},
            ],
            "threshold": {
                "line": {"color": "#f85149", "width": 2},
                "thickness": 0.8,
                "value": 40,
            },
        },
    ))
    fig.update_layout(
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        height=220,
        margin=dict(t=40, b=10, l=20, r=20),
    )
    return fig


# ── Timeline chart ─────────────────────────────────────────────────────────────

def make_timeline(dfs: dict[str, pd.DataFrame]) -> go.Figure:
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        subplot_titles=("CPU %", "Memory (MB)", "Network RX KB"),
        vertical_spacing=0.08,
    )
    colors_map = {"slice-a": "#58a6ff", "slice-b": "#f78166"}
    for slice_id, df in dfs.items():
        if df.empty:
            continue
        c = colors_map.get(slice_id, "#8b949e")
        fig.add_trace(go.Scatter(x=df["timestamp"], y=df["cpu_pct"],
            name=f"{slice_id} CPU", line=dict(color=c, width=1.5)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df["timestamp"], y=df["mem_mb"],
            name=f"{slice_id} MEM",
            line=dict(color=c, width=1.5, dash="dot")), row=2, col=1)
        fig.add_trace(go.Scatter(x=df["timestamp"], y=df["net_rx_kb"],
            name=f"{slice_id} RX",
            line=dict(color=c, width=1.5, dash="dash")), row=3, col=1)
        if "anomaly_score" in df.columns and df["anomaly_score"].notna().any():
            anomalies = df[df["anomaly_score"] < 0]
            if not anomalies.empty:
                fig.add_trace(go.Scatter(
                    x=anomalies["timestamp"], y=anomalies["cpu_pct"],
                    mode="markers",
                    marker=dict(color="#f85149", size=8, symbol="x"),
                    name=f"{slice_id} anomaly",
                ), row=1, col=1)
    fig.update_layout(
        paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        font=dict(color="#8b949e", size=11),
        legend=dict(bgcolor="#161b22", bordercolor="#30363d", font=dict(size=10)),
        height=420, margin=dict(t=40, b=20, l=40, r=20),
    )
    for i in range(1, 4):
        fig.update_yaxes(gridcolor="#21262d", zerolinecolor="#21262d", row=i, col=1)
        fig.update_xaxes(gridcolor="#21262d", row=i, col=1)
    return fig


# ── Alert feed ─────────────────────────────────────────────────────────────────

def render_alert_feed(dfs: dict[str, pd.DataFrame], score_map: dict):
    events = []
    for slice_id, df in dfs.items():
        if df.empty or "anomaly_score" not in df.columns:
            continue
        recent = df.tail(60)
        if not recent["anomaly_score"].notna().any():
            continue
        anom = recent[recent["anomaly_score"] < 0]
        for _, row in anom.iterrows():
            conf = max(0.0, min(0.5, row["anomaly_score"]) + 0.5) * 100
            attack_type = (
                row.get("attack_type")
                if "attack_type" in row.index and pd.notna(row.get("attack_type"))
                else score_map.get(slice_id, {}).get("attack_type")
            )
            events.append({
                "ts": row["timestamp"], "slice_id": slice_id,
                "score": row["anomaly_score"], "conf": conf,
                "cpu": row.get("cpu_pct", 0), "mem": row.get("mem_mb", 0),
                "net_rx": row.get("net_rx_kb", 0), "attack_type": attack_type,
            })
    events.sort(key=lambda e: e["ts"], reverse=True)
    if not events:
        st.markdown(
            '<div class="normal-card">✅ No anomalies detected — all slices isolated normally.</div>',
            unsafe_allow_html=True,
        )
        return
    for e in events[:10]:
        ts_str = e["ts"].strftime("%H:%M:%S") if hasattr(e["ts"], "strftime") else str(e["ts"])
        badge  = _badge_html(e.get("attack_type"))
        st.markdown(
            f'<div class="alert-card">'
            f'{badge}&nbsp;&nbsp;'
            f'<strong>{e["slice_id"]}</strong> @ {ts_str} — '
            f'Confidence: <strong>{e["conf"]:.1f}%</strong> | '
            f'Score: {e["score"]:.4f} | '
            f'CPU: {e["cpu"]:.1f}% | MEM: {e["mem"]:.1f}MB | RX: {e["net_rx"]:.2f}KB'
            f'</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# PDF REPORT GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def generate_pdf_report(incidents: list[dict], audit_df: pd.DataFrame) -> bytes:
    """Generate a styled PDF report of all attack incidents and audit anomalies."""
    buf    = io.BytesIO()
    doc    = SimpleDocTemplate(buf, pagesize=A4,
                                topMargin=2*cm, bottomMargin=2*cm,
                                leftMargin=2*cm, rightMargin=2*cm)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontSize=20,
        textColor=colors.HexColor("#1a56db"),
        spaceAfter=6,
        alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#6b7280"),
        spaceAfter=16,
        alignment=TA_CENTER,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=colors.HexColor("#1e3a5f"),
        spaceBefore=14,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#374151"),
        leading=13,
    )

    ATTACK_COLORS_PDF = {
        "CPU Starvation":    colors.HexColor("#fee2e2"),
        "Memory Exhaustion": colors.HexColor("#ffedd5"),
        "Network Breach":    colors.HexColor("#ede9fe"),
        "Combined Attack":   colors.HexColor("#f3f4f6"),
        "Unknown Anomaly":   colors.HexColor("#f9fafb"),
    }
    ATTACK_TEXT_PDF = {
        "CPU Starvation":    colors.HexColor("#991b1b"),
        "Memory Exhaustion": colors.HexColor("#92400e"),
        "Network Breach":    colors.HexColor("#5b21b6"),
        "Combined Attack":   colors.HexColor("#374151"),
        "Unknown Anomaly":   colors.HexColor("#374151"),
    }

    story = []

    # Title
    story.append(Paragraph("5G/6G Network Slicing Isolation Validator", title_style))
    story.append(Paragraph(
        f"Attack Incident Report  •  Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC",
        subtitle_style,
    ))
    story.append(HRFlowable(width="100%", thickness=1.5,
                             color=colors.HexColor("#1a56db"), spaceAfter=12))

    # Summary stats
    story.append(Paragraph("Executive Summary", section_style))
    total_incidents  = len(incidents)
    active_incidents = sum(1 for i in incidents if i.get("is_active") in (1, True))
    resolved         = total_incidents - active_incidents
    total_anomalies  = (
        len(audit_df[audit_df["anomaly_score"] < 0])
        if not audit_df.empty and "anomaly_score" in audit_df.columns else 0
    )

    summary_data = [
        ["Metric", "Value"],
        ["Total Incidents Recorded",    str(total_incidents)],
        ["Active (Unresolved) Incidents", str(active_incidents)],
        ["Resolved Incidents",           str(resolved)],
        ["Total Anomalous Samples",      str(total_anomalies)],
        ["Report Timestamp (UTC)",       datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")],
    ]
    summary_table = Table(summary_data, colWidths=[9*cm, 7*cm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#1a56db")),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0), 10),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.HexColor("#f8fafc"), colors.white]),
        ("FONTNAME",     (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",     (0, 1), (-1, -1), 9),
        ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("TOPPADDING",   (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 14))

    # Incident log
    story.append(Paragraph("Incident Log", section_style))
    if not incidents:
        story.append(Paragraph("No incidents recorded.", body_style))
    else:
        inc_header = ["#", "Slice", "Attack Type", "Started (UTC)",
                      "Resolved", "Duration", "Min Conf%", "Status"]
        inc_data   = [inc_header]
        for idx, inc in enumerate(incidents, 1):
            try:
                sv = inc["started_at"]
                started = (
                    datetime.fromisoformat(str(sv)).strftime("%Y-%m-%d %H:%M:%S")
                    if isinstance(sv, str)
                    else datetime.utcfromtimestamp(float(sv)).strftime("%Y-%m-%d %H:%M:%S")
                )
            except Exception:
                started = str(inc.get("started_at", "?"))
            try:
                rv = inc.get("resolved_at")
                resolved_str = (
                    datetime.fromisoformat(str(rv)).strftime("%H:%M:%S")
                    if isinstance(rv, str)
                    else datetime.utcfromtimestamp(float(rv)).strftime("%H:%M:%S")
                ) if rv else "Ongoing"
            except Exception:
                resolved_str = "Ongoing"

            duration = f"{inc['duration_s']:.0f}s" if inc.get("duration_s") else "—"
            conf     = f"{inc['min_confidence']:.1f}" if inc.get("min_confidence") else "—"
            status   = "Active" if inc.get("is_active") in (1, True) else "Resolved"
            attack   = inc.get("attack_type") or "Unknown"
            inc_data.append([str(idx), inc["slice_id"], attack,
                             started, resolved_str, duration, conf, status])

        col_w = [0.8*cm, 2.4*cm, 3.4*cm, 4*cm, 2.2*cm, 2*cm, 1.8*cm, 2.2*cm]
        inc_table = Table(inc_data, colWidths=col_w, repeatRows=1)
        ts = [
            ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
            ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
            ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, -1), 8),
            ("FONTNAME",     (0, 1), (-1, -1), "Helvetica"),
            ("GRID",         (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ("LEFTPADDING",  (0, 0), (-1, -1), 5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.HexColor("#f9fafb"), colors.white]),
        ]
        for row_i, inc in enumerate(incidents, 1):
            at = inc.get("attack_type") or "Unknown Anomaly"
            bg = ATTACK_COLORS_PDF.get(at, colors.HexColor("#f9fafb"))
            tc = ATTACK_TEXT_PDF.get(at, colors.HexColor("#374151"))
            ts += [
                ("BACKGROUND", (2, row_i), (2, row_i), bg),
                ("TEXTCOLOR",  (2, row_i), (2, row_i), tc),
                ("FONTNAME",   (2, row_i), (2, row_i), "Helvetica-Bold"),
            ]
        inc_table.setStyle(TableStyle(ts))
        story.append(inc_table)

    story.append(Spacer(1, 16))

    # Anomaly audit table (top 50)
    story.append(Paragraph("Top 50 Anomalous Samples (Audit Trail)", section_style))
    if audit_df.empty or "anomaly_score" not in audit_df.columns:
        story.append(Paragraph("No anomaly data available.", body_style))
    else:
        df_anom = audit_df[audit_df["anomaly_score"] < 0].head(50).copy()
        if df_anom.empty:
            story.append(Paragraph("No anomalies in current window.", body_style))
        else:
            audit_header = ["Time", "Slice", "CPU%", "MemMB",
                            "RX KB/s", "TX KB/s", "Score", "Attack Type"]
            audit_data   = [audit_header]
            for _, row in df_anom.iterrows():
                ts_fmt = (row["timestamp"].strftime("%H:%M:%S")
                          if hasattr(row["timestamp"], "strftime") else str(row["timestamp"]))
                audit_data.append([
                    ts_fmt,
                    str(row.get("slice_id", "")),
                    f"{row.get('cpu_pct', 0):.1f}",
                    f"{row.get('mem_mb', 0):.1f}",
                    f"{row.get('net_rx_kb', 0):.2f}",
                    f"{row.get('net_tx_kb', 0):.2f}",
                    f"{row.get('anomaly_score', 0):.4f}",
                    str(row.get("attack_type") or "Unknown"),
                ])
            a_col_w = [1.8*cm, 2.2*cm, 1.8*cm, 2*cm, 2*cm, 2*cm, 2.2*cm, 3.5*cm]
            a_table = Table(audit_data, colWidths=a_col_w, repeatRows=1)
            a_table.setStyle(TableStyle([
                ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#374151")),
                ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
                ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE",     (0, 0), (-1, -1), 7.5),
                ("GRID",         (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e7eb")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.HexColor("#fff1f2"), colors.HexColor("#fef2f2")]),
                ("TOPPADDING",   (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
                ("LEFTPADDING",  (0, 0), (-1, -1), 4),
            ]))
            story.append(a_table)

    # Footer
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.8,
                             color=colors.HexColor("#d1d5db")))
    story.append(Paragraph(
        "5G/6G Network Slicing Isolation Validator — Confidential Team Report",
        ParagraphStyle("Footer", parent=styles["Normal"],
                       fontSize=8, textColor=colors.HexColor("#9ca3af"),
                       alignment=TA_CENTER, spaceBefore=6),
    ))

    doc.build(story)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# NETWORKX SLA GRAPH
# ══════════════════════════════════════════════════════════════════════════════

def render_sla_networkx_graph(selected_slices: list[str]) -> None:
    """
    NetworkX topology where centre = 5G Core, outer nodes = slices.
    Edge colour / label = SLA compliance %. Second chart = scatter of
    anomaly count vs compliance for analytical depth.
    """
    sla_windows   = ["1h", "24h", "7d"]
    window_labels = {"1h": "1 Hour", "24h": "24 Hours", "7d": "7 Days"}

    selected_window = st.selectbox(
        "SLA window", sla_windows,
        format_func=lambda w: window_labels[w],
        key="sla_nx_window",
    )

    sla_results: dict[str, dict] = {
        name: fetch_sla(name, selected_window) for name in selected_slices
    }

    # ── NetworkX graph ────────────────────────────────────────────────────────
    G = nx.Graph()
    core_node = "5G Core"
    G.add_node(core_node, node_type="core")
    for name in selected_slices:
        comp = sla_results[name].get("compliance", 100.0)
        G.add_node(name, node_type="slice", compliance=comp)
        G.add_edge(core_node, name, compliance=comp)

    pos = nx.spring_layout(G, seed=42, k=2.5)

    def _node_color(node):
        if G.nodes[node].get("node_type") == "core":
            return "#1a56db"
        comp = G.nodes[node].get("compliance", 100.0)
        return "#16a34a" if comp >= 99.5 else ("#d97706" if comp >= 98.0 else "#dc2626")

    node_colors = [_node_color(n) for n in G.nodes()]
    node_sizes  = [1800 if G.nodes[n].get("node_type") == "core" else 1200
                   for n in G.nodes()]

    def _edge_color(u, v):
        comp = G[u][v].get("compliance", 100.0)
        return "#16a34a" if comp >= 99.5 else ("#d97706" if comp >= 98.0 else "#dc2626")

    edge_colors = [_edge_color(u, v) for u, v in G.edges()]
    edge_widths = [
        4.0 if G[u][v].get("compliance", 100.0) >= 99.5
        else (2.5 if G[u][v].get("compliance", 100.0) >= 98.0 else 1.5)
        for u, v in G.edges()
    ]

    fig_nx, ax = plt.subplots(figsize=(8, 5), facecolor="#0d1117")
    ax.set_facecolor("#0d1117")
    nx.draw_networkx_nodes(G, pos, ax=ax, node_color=node_colors,
                           node_size=node_sizes, alpha=0.92)
    nx.draw_networkx_edges(G, pos, ax=ax, edge_color=edge_colors,
                           width=edge_widths, alpha=0.85)
    nx.draw_networkx_labels(G, pos, ax=ax, font_color="#ffffff",
                            font_size=9, font_weight="bold")
    edge_labels = {(u, v): f"{G[u][v]['compliance']:.1f}%" for u, v in G.edges()}
    nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, ax=ax,
                                 font_color="#a0aec0", font_size=8,
                                 bbox=dict(boxstyle="round,pad=0.2",
                                           fc="#161b22", ec="none", alpha=0.7))
    legend_patches = [
        mpatches.Patch(color="#16a34a", label="SLA ≥ 99.5% (OK)"),
        mpatches.Patch(color="#d97706", label="SLA 98–99.5% (Warning)"),
        mpatches.Patch(color="#dc2626", label="SLA < 98% (Breach)"),
        mpatches.Patch(color="#1a56db", label="5G Core"),
    ]
    ax.legend(handles=legend_patches, loc="upper right",
              facecolor="#161b22", edgecolor="#30363d",
              labelcolor="#c9d1d9", fontsize=8)
    ax.axis("off")
    ax.set_title(f"Slice SLA Topology — {window_labels[selected_window]}",
                 color="#c9d1d9", fontsize=12, pad=10)
    st.pyplot(fig_nx, use_container_width=True)
    plt.close(fig_nx)

    # ── Scatter: anomaly count vs SLA compliance ──────────────────────────────
    st.markdown("#### Anomaly Count vs SLA Compliance")
    st.caption("Each bubble = one slice. Position shows compliance; bubble size reflects anomaly count.")

    scatter_rows = []
    for name in selected_slices:
        sd   = sla_results[name]
        comp = sd.get("compliance", 100.0)
        anom = sd.get("anomalous", 0)
        total= sd.get("total", 0)
        scatter_rows.append({"Slice": name, "Compliance": comp,
                             "Anomalies": anom, "Total": total})
    df_sc = pd.DataFrame(scatter_rows)

    if not df_sc.empty:
        df_sc["Color"]   = df_sc["Compliance"].apply(
            lambda c: "#16a34a" if c >= 99.5 else ("#d97706" if c >= 98.0 else "#dc2626")
        )
        df_sc["SizeVal"] = df_sc["Anomalies"].apply(lambda x: max(x * 8, 12))

        fig_sc = go.Figure()
        for _, row in df_sc.iterrows():
            fig_sc.add_trace(go.Scatter(
                x=[row["Compliance"]],
                y=[row["Anomalies"]],
                mode="markers+text",
                marker=dict(size=row["SizeVal"], color=row["Color"],
                            opacity=0.85, line=dict(color="#ffffff", width=1.5)),
                text=[row["Slice"]],
                textposition="top center",
                textfont=dict(color="#c9d1d9", size=11),
                name=row["Slice"],
                hovertemplate=(
                    f"<b>{row['Slice']}</b><br>"
                    f"SLA: {row['Compliance']:.2f}%<br>"
                    f"Anomalies: {row['Anomalies']}<br>"
                    f"Total samples: {row['Total']}<extra></extra>"
                ),
            ))
        fig_sc.add_vline(x=99.5, line_dash="dash", line_color="#16a34a", opacity=0.6,
                         annotation_text="99.5% target", annotation_font_color="#16a34a")
        fig_sc.add_vline(x=98.0, line_dash="dot", line_color="#d97706", opacity=0.6,
                         annotation_text="98% warn", annotation_font_color="#d97706")
        fig_sc.update_layout(
            paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
            font=dict(color="#c9d1d9"),
            xaxis=dict(title="SLA Compliance %", gridcolor="#21262d",
                       range=[max(0, df_sc["Compliance"].min() - 2), 100.5]),
            yaxis=dict(title="Anomaly Count", gridcolor="#21262d", rangemode="tozero"),
            height=320, margin=dict(t=20, b=40, l=50, r=20),
            showlegend=False,
        )
        st.plotly_chart(fig_sc, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

def render_sidebar() -> tuple[str, int, int, list[str]]:
    with st.sidebar:
        st.title("⚙️ Controls")

        page = st.radio(
            "Navigate",
            ["🛡️ Live Monitor", "🔍 Anomaly Classifier",
             "📋 Audit & Incident Log", "📊 SLA & Model Health"],
            key="nav_page",
        )

        st.divider()

        refresh_rate    = st.slider("Refresh interval (s)", 2, 30, REFRESH_S)
        history_len     = st.slider("History window (points)", 20, 200, 120)
        selected_slices = st.multiselect(
            "Slices to display", SLICE_NAMES, default=SLICE_NAMES
        )

        st.divider()

        # System status
        health = fetch_health()
        st.markdown("### System Status")
        st.markdown(
            f"{'🟢' if health.get('status') == 'ok' else '🔴'} "
            f"**API:** {health.get('status', 'unknown')}"
        )
        st.markdown(
            f"{'🟢' if health.get('model_loaded') else '🔴'} "
            f"**Model:** {'loaded' if health.get('model_loaded') else 'not loaded'}"
        )
        st.markdown(
            f"{'🟢' if health.get('classifier_ready') else '🟡'} "
            f"**Classifier:** {'ready' if health.get('classifier_ready') else 'needs retrain'}"
        )
        st.markdown(f"💾 **DB rows:** {health.get('db_rows', 0)}")

        st.divider()

        # ── Attack Simulation ──────────────────────────────────────────────
        st.markdown("### 🎯 Attack Simulation")
        st.caption("Injects spiked rows for ~60s so the ML model detects an attack.")

        sim_slice = st.selectbox("Target slice", SLICE_NAMES, key="sim_slice")
        sim_type  = st.selectbox(
            "Attack type",
            ["cpu", "memory", "network_breach"],
            format_func=lambda x: {
                "cpu":            "🔴 CPU Starvation  (cpu_pct → 85–99%)",
                "memory":         "🟠 Memory Exhaustion  (mem_mb → 110–128)",
                "network_breach": "🟣 Network Breach  (rx/tx → 800–2000 KB)",
            }[x],
            key="sim_type",
        )

        active_injections = fetch_injection_status()
        is_running = sim_slice in active_injections

        if is_running:
            st.warning(
                f"⏳ Injection running on **{sim_slice}** — ~60s total, refreshes every 5s"
            )
            st.button("💉 Inject Attack", disabled=True, key="inject_btn")
        else:
            if st.button("💉 Inject Attack (~60s)", key="inject_btn", type="primary"):
                try:
                    r = requests.post(
                        f"{API_BASE}/inject-attack",
                        params={"slice_id": sim_slice, "attack_type": sim_type},
                        timeout=5,
                    )
                    if r.status_code == 200:
                        resp = r.json()
                        if resp.get("status") == "started":
                            st.success(
                                f"✅ **{sim_type.replace('_', ' ').title()}** injection "
                                f"started on **{sim_slice}** — watch the gauge drop!"
                            )
                        else:
                            st.info(resp.get("message", "Already running."))
                    else:
                        st.error(f"API returned {r.status_code}: {r.text}")
                except Exception as ex:
                    st.error(f"Could not reach API: {ex}")

    return page, refresh_rate, history_len, selected_slices


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1: LIVE MONITOR
# ══════════════════════════════════════════════════════════════════════════════

def page_live_monitor(refresh_rate: int, history_len: int, selected_slices: list[str]):
    st.title("🛡️ 5G Network Slicing Isolation Monitor")
    st.caption(
        f"Live telemetry • Refreshes every {refresh_rate}s • "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    score_map = get_scores_with_fallback()
    dfs: dict[str, pd.DataFrame] = {
        name: fetch_history(name, limit=history_len) for name in selected_slices
    }

    gauge_cols = st.columns(len(selected_slices))
    for i, name in enumerate(selected_slices):
        with gauge_cols[i]:
            score_data  = score_map.get(name, {})
            conf        = score_data.get("isolation_confidence")
            attack_type = score_data.get("attack_type")
            st.plotly_chart(
                make_gauge(f"{name} Isolation Confidence", conf),
                use_container_width=True, key=f"gauge_{name}",
            )
            if attack_type:
                st.markdown(
                    f"<div style='text-align:center;margin-top:-12px'>"
                    f"{_badge_html(attack_type)}</div>",
                    unsafe_allow_html=True,
                )

    # ── AI Predictive Forecaster ──────────────────────────────────────────────
    render_ai_forecast_panel(selected_slices)
    st.divider()
    st.markdown("### Current Metrics")
    card_cols = st.columns(len(selected_slices) * 4)
    for i, name in enumerate(selected_slices):
        score_data = score_map.get(name, {})
        features   = score_data.get("features", {})
        base_i     = i * 4
        with card_cols[base_i]:
            st.metric(f"{name} CPU", f"{features.get('cpu_pct', 0):.1f}%",
                      delta="⚠️ ANOMALY" if score_data.get("is_anomaly") else "normal",
                      delta_color="inverse" if score_data.get("is_anomaly") else "normal")
        with card_cols[base_i + 1]:
            st.metric(f"{name} MEM", f"{features.get('mem_mb', 0):.1f} MB")
        with card_cols[base_i + 2]:
            st.metric(f"{name} RX", f"{features.get('net_rx_kb', 0):.2f} KB/s")
        with card_cols[base_i + 3]:
            st.metric(f"{name} TX", f"{features.get('net_tx_kb', 0):.2f} KB/s")

    st.divider()
    st.markdown("### Metric Timeline")
    if any(not df.empty for df in dfs.values()):
        st.plotly_chart(make_timeline(dfs), use_container_width=True)
    else:
        st.info("Waiting for telemetry data… (collector may still be starting)")

    st.markdown("### Alert Feed (last 10 anomalies)")
    render_alert_feed(dfs, score_map)

    # ── Exfiltration Live Feed ────────────────────────────────────────
    st.markdown("---")
    st.subheader("🚨 Cross-Slice Exfiltration Monitor")

    try:
        resp = requests.get(f"{API_BASE}/exfil/latest?limit=10", timeout=2)
        items = resp.json().get("items", []) if resp.ok else []
    except Exception:
        items = []

    if not items:
        st.info("🔒 Isolation active — no exfiltration detected")
    else:
        st.error("⚠️ DATA LEAK DETECTED: slice-a → slice-b")
        for batch in reversed(items):
            ts = batch.get("timestamp", 0)
            label = time.strftime("%H:%M:%S", time.localtime(ts))
            for p in batch.get("patterns", []):
                icon = {"typing": "⚡", "video": "🎬", "camera": "📷"}.get(p["type"], "🔍")
                conf = p["confidence"]
                bar_val = conf / 100
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.markdown(f"**{icon} {p['details']} — {conf}% confidence** `{label}`")
                    st.progress(bar_val)
                with col2:
                    st.metric("Confidence", f"{conf}%")



# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2: ANOMALY CLASSIFIER
# ══════════════════════════════════════════════════════════════════════════════

def page_anomaly_classifier(selected_slices: list[str]):
    st.title("🔍 Anomaly Classifier")
    st.caption("Attack-type breakdown and Z-score feature analysis for detected anomalies.")

    all_rows = []
    for name in selected_slices:
        df = fetch_history(name, limit=500)
        if not df.empty:
            all_rows.append(df)
    if not all_rows:
        st.info("No telemetry data yet. Let the collector run for a bit.")
        return

    df_all    = pd.concat(all_rows, ignore_index=True)
    df_scored = df_all[df_all["anomaly_score"].notna()].copy()

    if df_scored.empty:
        st.info("No scored rows yet — give it a moment.")
        return

    df_anomalies = df_scored[df_scored["anomaly_score"] < 0].copy()

    total_rows    = len(df_scored)
    total_anomaly = len(df_anomalies)
    pct_anomaly   = (total_anomaly / total_rows * 100) if total_rows > 0 else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Scored Samples", total_rows)
    k2.metric("Anomaly Count", total_anomaly)
    k3.metric("Anomaly Rate", f"{pct_anomaly:.1f}%",
              delta="⚠️ High" if pct_anomaly > 10 else "Normal",
              delta_color="inverse" if pct_anomaly > 10 else "off")
    k4.metric("Slices Monitored", len(selected_slices))

    st.divider()

    # Attack type distribution — donut pie only (bar chart removed per request)
    if not df_anomalies.empty and "attack_type" in df_anomalies.columns:
        st.markdown("### Attack Type Distribution")
        type_counts = (
            df_anomalies["attack_type"]
            .fillna("Unknown Anomaly")
            .value_counts()
            .reset_index()
        )
        type_counts.columns = ["Attack Type", "Count"]
        colors_map = {
            "CPU Starvation":    "#b91c1c",
            "Memory Exhaustion": "#b45309",
            "Network Breach":    "#7c3aed",
            "Combined Attack":   "#374151",
            "Unknown Anomaly":   "#4b5563",
        }
        pie_colors = [colors_map.get(t, "#4b5563") for t in type_counts["Attack Type"]]
        fig_pie = go.Figure(go.Pie(
            labels=type_counts["Attack Type"],
            values=type_counts["Count"],
            marker_colors=pie_colors,
            hole=0.45,
            textinfo="percent+label",
            textfont_size=12,
        ))
        fig_pie.update_layout(
            title="Attack Type Share",
            paper_bgcolor="#0d1117",
            font=dict(color="#c9d1d9"),
            height=320, margin=dict(t=40, b=20, l=20, r=20),
            showlegend=True,
            legend=dict(bgcolor="#161b22", font=dict(size=11)),
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    st.divider()

    # Feature signature — metrics only, no redundant bar chart
    st.markdown("### Feature Signature per Attack Type")
    st.caption(
        "Average CPU, Memory, and Network values during each detected attack type "
        "— compared to normal baseline."
    )

    normal_df  = df_scored[df_scored["anomaly_score"] >= 0]
    normal_avg = {
        "cpu_pct":   normal_df["cpu_pct"].mean()   if not normal_df.empty else 0,
        "mem_mb":    normal_df["mem_mb"].mean()    if not normal_df.empty else 0,
        "net_rx_kb": normal_df["net_rx_kb"].mean() if not normal_df.empty else 0,
        "net_tx_kb": normal_df["net_tx_kb"].mean() if not normal_df.empty else 0,
    }

    if not df_anomalies.empty and "attack_type" in df_anomalies.columns:
        for attack_type, group in df_anomalies.groupby("attack_type"):
            icon = ATTACK_BADGE.get(attack_type, ("⚠️", "#374151", attack_type))[0]
            with st.expander(
                f"{icon} {attack_type}  —  {len(group)} events", expanded=True
            ):
                st.markdown(
                    f"{_badge_html(attack_type)}", unsafe_allow_html=True
                )
                ac1, ac2, ac3, ac4 = st.columns(4)
                ac1.metric("Avg CPU %",   f"{group['cpu_pct'].mean():.1f}%",
                           delta=f"+{group['cpu_pct'].mean() - normal_avg['cpu_pct']:.1f}% vs normal",
                           delta_color="inverse")
                ac2.metric("Avg Mem MB",  f"{group['mem_mb'].mean():.1f}",
                           delta=f"+{group['mem_mb'].mean() - normal_avg['mem_mb']:.1f} vs normal",
                           delta_color="inverse")
                ac3.metric("Avg RX KB/s", f"{group['net_rx_kb'].mean():.2f}",
                           delta=f"+{group['net_rx_kb'].mean() - normal_avg['net_rx_kb']:.2f} vs normal",
                           delta_color="inverse")
                ac4.metric("Avg TX KB/s", f"{group['net_tx_kb'].mean():.2f}",
                           delta=f"+{group['net_tx_kb'].mean() - normal_avg['net_tx_kb']:.2f} vs normal",
                           delta_color="inverse")
    else:
        st.info("No classified anomalies yet. Inject an attack from the sidebar.")

    st.divider()

    # Anomaly score timeline
    st.markdown("### Anomaly Score Timeline")
    st.caption("Scores below 0 = anomalous. Lower = more severe.")
    fig_score = go.Figure()
    slice_colors = {"slice-a": "#58a6ff", "slice-b": "#f78166"}
    for name in selected_slices:
        df_s = df_scored[df_scored["slice_id"] == name]
        if df_s.empty:
            continue
        fig_score.add_trace(go.Scatter(
            x=df_s["timestamp"], y=df_s["anomaly_score"],
            name=name, line=dict(color=slice_colors.get(name, "#8b949e"), width=1.5),
            mode="lines",
        ))
    fig_score.add_hline(y=0, line_dash="dash", line_color="#f85149",
                        annotation_text="Anomaly Threshold",
                        annotation_position="bottom right")
    fig_score.update_layout(
        paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        font=dict(color="#c9d1d9"), height=280,
        margin=dict(t=20, b=20, l=40, r=20),
        xaxis=dict(gridcolor="#21262d"),
        yaxis=dict(gridcolor="#21262d", title="Decision Score"),
        legend=dict(bgcolor="#161b22"),
    )
    st.plotly_chart(fig_score, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3: AUDIT & INCIDENT LOG
# ══════════════════════════════════════════════════════════════════════════════

def page_audit_log(selected_slices: list[str]):
    st.title("📋 Audit & Incident Log")
    st.caption("Structured incident history, full anomaly audit trail, and PDF report export.")

    st.markdown("### 🚨 Incident History")
    incidents = fetch_incidents(limit=100)

    if not incidents:
        st.info("No incidents recorded yet. Inject an attack to generate one.")
    else:
        st.markdown(f"[⬇️ Download incidents CSV]({API_BASE}/incidents/export)")
        for inc in incidents:
            try:
                sv = inc["started_at"]
                started = (
                    datetime.fromisoformat(str(sv)).strftime("%Y-%m-%d %H:%M:%S")
                    if isinstance(sv, str)
                    else datetime.utcfromtimestamp(float(sv)).strftime("%Y-%m-%d %H:%M:%S")
                )
            except Exception:
                started = str(inc.get("started_at", "?"))
            try:
                rv = inc.get("resolved_at")
                resolved = (
                    datetime.fromisoformat(str(rv)).strftime("%H:%M:%S")
                    if isinstance(rv, str)
                    else datetime.utcfromtimestamp(float(rv)).strftime("%H:%M:%S")
                ) if rv else "ongoing"
            except Exception:
                resolved = "ongoing"

            duration = f"{inc['duration_s']:.0f}s" if inc.get("duration_s") else "—"
            badge    = _badge_html(inc.get("attack_type"))
            is_act   = inc.get("is_active") in (1, True)
            status   = "🔴 ACTIVE" if is_act else "✅ Resolved"
            css_cls  = "incident-open" if is_act else "incident-closed"
            conf     = f"{inc['min_confidence']:.1f}%" if inc.get("min_confidence") else "—"
            st.markdown(
                f'<div class="{css_cls}">'
                f'{status} &nbsp; {badge} &nbsp;&nbsp;'
                f'<strong>{inc["slice_id"]}</strong> — '
                f'Started: {started} &nbsp;|&nbsp; '
                f'Resolved: {resolved} &nbsp;|&nbsp; '
                f'Duration: {duration} &nbsp;|&nbsp; '
                f'Min Confidence: {conf}'
                f'</div>',
                unsafe_allow_html=True,
            )
            # ── AI Forensic Note ──────────────────────────────────────────
            inc_id = inc.get("id")
            ai_note_raw = inc.get("ai_forensic_note")
            if not is_act and inc_id:
                with st.expander(f"🤖 AI Forensic Analysis — incident #{inc_id}", expanded=False):
                    if ai_note_raw:
                        import json as _json
                        try:
                            note = _json.loads(ai_note_raw) if isinstance(ai_note_raw, str) else ai_note_raw
                            nc1, nc2 = st.columns(2)
                            nc1.markdown(f"**Attack vector:** `{note.get('attack_vector','?')}`")
                            nc2.markdown(f"**Severity:** `{note.get('severity','?')}` | **Confidence:** `{note.get('confidence','?')}`")
                            st.markdown(f"**Hypothesis:** {note.get('hypothesis','')}")
                            st.markdown(f"**Pre-breach signal:** {note.get('pre_breach_signal','')}")
                            st.info(f"▶ **Recommended action:** {note.get('recommended_action','')}")
                        except Exception:
                            st.text(str(ai_note_raw))
                    else:
                        if st.button(f"Generate AI forensic note", key=f"ai_gen_{inc_id}"):
                            with st.spinner("Asking AI to reason about this incident…"):
                                result = fetch_ai_incident_note(inc_id)
                            note_data = result.get("note")
                            if note_data:
                                # Clear incident cache so the rerun picks up the new note
                                fetch_incidents.clear()
                                st.success("Forensic note generated — loading now…")
                                st.rerun()
                            else:
                                st.error("AI layer returned no result. Check GROQ_API_KEY and logs.")

    st.divider()

    st.markdown("### 🗂️ Anomaly Audit Table")
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        filter_slice = st.selectbox("Filter by slice", ["All"] + SLICE_NAMES,
                                    key="audit_slice")
    with col_f2:
        audit_limit = st.slider("Max rows", 50, 500, 200, key="audit_limit")
    with col_f3:
        anomaly_only = st.checkbox("Anomalies only", value=True, key="audit_anomaly_only")

    slice_param = None if filter_slice == "All" else filter_slice
    df_audit = fetch_audit_log(
        slice_id=slice_param, limit=audit_limit, anomaly_only=anomaly_only
    )

    if df_audit.empty:
        st.info("No rows match the current filters.")
    else:
        display_df = df_audit.copy()
        display_df["timestamp"] = display_df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
        if "anomaly_score" in display_df.columns:
            display_df["is_anomaly"] = display_df["anomaly_score"].apply(
                lambda x: "⚠️ YES" if pd.notna(x) and x < 0 else "✅ NO"
            )
        cols_show = ["timestamp", "slice_id", "cpu_pct", "mem_mb",
                     "net_rx_kb", "net_tx_kb", "anomaly_score", "attack_type", "is_anomaly"]
        cols_show = [c for c in cols_show if c in display_df.columns]
        st.dataframe(
            display_df[cols_show].rename(columns={
                "timestamp": "Time", "slice_id": "Slice",
                "cpu_pct": "CPU %", "mem_mb": "Mem MB",
                "net_rx_kb": "RX KB/s", "net_tx_kb": "TX KB/s",
                "anomaly_score": "Score", "attack_type": "Attack Type",
                "is_anomaly": "Anomaly?",
            }),
            use_container_width=True, height=400,
        )

        csv_bytes = display_df[cols_show].to_csv(index=False).encode("utf-8")
        c1, c2 = st.columns(2)
        with c1:
            st.download_button(
                "⬇️ Download as CSV", data=csv_bytes,
                file_name=f"audit_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
            )
        with c2:
            if st.button("📄 Generate PDF Report", type="secondary", key="pdf_btn"):
                with st.spinner("Generating PDF report…"):
                    pdf_bytes = generate_pdf_report(incidents, df_audit)
                st.download_button(
                    "⬇️ Download PDF Report", data=pdf_bytes,
                    file_name=f"attack_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                    mime="application/pdf",
                    key="pdf_download",
                )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4: SLA & MODEL HEALTH
# ══════════════════════════════════════════════════════════════════════════════

def page_sla_model_health(selected_slices: list[str]):
    st.title("📊 SLA & Model Health")
    st.caption("SLA compliance topology, scatter analysis, and ML model health panel.")

    st.markdown("### 📋 SLA Compliance Scoreboard")
    sla_cols = st.columns(len(selected_slices) * 3)
    for i, name in enumerate(selected_slices):
        for j, window in enumerate(["1h", "24h", "7d"]):
            sla_data = fetch_sla(name, window)
            comp     = sla_data.get("compliance", 100.0)
            source   = sla_data.get("source", "sqlite")
            color    = "normal" if comp >= 99.5 else ("off" if comp >= 98 else "inverse")
            sla_cols[i * 3 + j].metric(
                f"{name} SLA {window}", f"{comp:.2f}%",
                delta=(
                    f"🟢 OK ({source})" if comp >= 99.5
                    else (f"🟡 Warn ({source})" if comp >= 98
                          else f"🔴 Breach ({source})")
                ),
                delta_color=color,
            )

    st.divider()
    st.markdown("### 🌐 SLA Network Topology Graph")
    st.caption(
        "Node and edge colour reflect SLA compliance tier. "
        "Edge labels show compliance % for the selected window."
    )
    render_sla_networkx_graph(selected_slices)

    st.divider()
    st.markdown("### 🤖 Model Health")
    m_status = fetch_model_status()

    if m_status and m_status.get("trained_at"):
        mh1, mh2, mh3, mh4 = st.columns(4)
        mh1.metric("Model Age",        f"{m_status.get('age_minutes', '?')} min")
        mh2.metric("Training Samples", str(m_status.get("n_samples", "?")))
        drift = m_status.get("drift_ratio_100rows")
        mh3.metric("Drift Rate (100 rows)",
                   f"{drift * 100:.1f}%" if drift is not None else "?",
                   delta="⚠️ High" if drift and drift > 0.20 else "Normal",
                   delta_color="inverse" if drift and drift > 0.20 else "off")
        mh4.metric("Drift Strikes", f"{m_status.get('drift_strikes', 0)} / 3")

        if drift is not None:
            fig_drift = go.Figure(go.Indicator(
                mode="gauge+number",
                value=drift * 100,
                title={"text": "Anomaly Drift Rate %",
                       "font": {"color": "#c9d1d9", "size": 13}},
                number={"suffix": "%", "font": {"color": "#c9d1d9"}},
                gauge={
                    "axis": {"range": [0, 50], "tickcolor": "#30363d"},
                    "bar":  {"color": "#f85149" if drift > 0.20 else "#58a6ff"},
                    "bgcolor": "#161b22",
                    "threshold": {"line": {"color": "#d29922", "width": 3}, "value": 20},
                    "steps": [
                        {"range": [0,  20], "color": "#0d1a0d"},
                        {"range": [20, 35], "color": "#1a1500"},
                        {"range": [35, 50], "color": "#2d1117"},
                    ],
                },
            ))
            fig_drift.update_layout(
                paper_bgcolor="#0d1117", height=200,
                margin=dict(t=40, b=0, l=20, r=20),
            )
            st.plotly_chart(fig_drift, use_container_width=True)

        last = m_status.get("last_retrain")
        if last:
            st.caption(
                f"Last retrain — at: {last.get('retrained_at', '?')} | "
                f"reason: **{last.get('reason', '?')}** | "
                f"samples: {last.get('n_samples', '?')}"
            )
        if st.button("🔄 Reload Model Now", type="secondary"):
            try:
                r = requests.post(f"{API_BASE}/reload-model", timeout=5)
                if r.ok:
                    st.success("Model reloaded.")
                else:
                    st.error("Reload failed.")
            except Exception as exc:
                st.error(f"Could not reach API: {exc}")
    else:
        st.info("Model status unavailable — API may still be starting.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    page, refresh_rate, history_len, selected_slices = render_sidebar()

    if page == "🛡️ Live Monitor":
        page_live_monitor(refresh_rate, history_len, selected_slices)
        time.sleep(refresh_rate)
        st.rerun()
    elif page == "🔍 Anomaly Classifier":
        page_anomaly_classifier(selected_slices)
    elif page == "📋 Audit & Incident Log":
        page_audit_log(selected_slices)
    elif page == "📊 SLA & Model Health":
        page_sla_model_health(selected_slices)


if __name__ == "__main__":
    main()