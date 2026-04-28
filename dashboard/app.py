"""
dashboard/app.py
────────────────
Streamlit operator dashboard for 5G Slice Isolation Monitor.

Pages:
  1. 🛡️ Live Monitor     – gauges, timeline, metric cards, alert feed
  2. 🔍 Anomaly Classifier – per-attack-type breakdown, Z-score analysis
  3. 📋 Audit & Incident Log – full paginated table, incident history, CSV export
"""

import os
import time
import requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import datetime
from typing import Optional

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
    /* ── Base ── */
    .main { background-color: #050b14; }
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }

    /* ── Metric cards — high-contrast bordered boxes ── */
    .stMetric {
        background: #0a1628;
        border: 1px solid #00d4ff33;
        border-radius: 10px;
        padding: 14px 16px;
        box-shadow: 0 0 12px #00d4ff18;
    }
    .stMetric label {
        color: #7ecfff !important;
        font-size: 0.72rem !important;
        font-weight: 700 !important;
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }
    .stMetric [data-testid="stMetricValue"] {
        color: #e8f4fd !important;
        font-size: 1.4rem !important;
        font-weight: 700 !important;
    }
    .stMetric [data-testid="stMetricDelta"] {
        font-size:0.75rem !important;
    }

    /* ── Section card wrapper ── */
    .cyber-card {
        background: #0a1628;
        border: 1px solid #00d4ff22;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 16px;
        box-shadow: 0 0 20px #00d4ff10;
    }

    /* ── Alert feed cards ── */
    .alert-card {
        background: #1a0a0a;
        border: 1px solid #f8514966;
        border-left: 4px solid #f85149;
        border-radius: 6px;
        padding: 10px 14px;
        margin: 5px 0;
        font-size: 0.85rem;
        color: #ffdcdc;
    }
    .normal-card {
        background: #051a0a;
        border: 1px solid #3fb95066;
        border-left: 4px solid #3fb950;
        border-radius: 6px;
        padding: 10px 14px;
        margin: 5px 0;
        font-size: 0.85rem;
        color: #d4f7d4;
    }

    /* ── Incident cards ── */
    .incident-open {
        background: #1a0a0a;
        border: 1px solid #f85149aa;
        border-left: 4px solid #f85149;
        border-radius: 8px;
        padding: 12px 16px;
        margin: 6px 0;
        font-size: 0.88rem;
        color: #ffdcdc;
    }
    .incident-closed {
        background: #0a1628;
        border: 1px solid #30363d;
        border-left: 4px solid #3fb950;
        border-radius: 8px;
        padding: 12px 16px;
        margin: 6px 0;
        font-size: 0.88rem;
        color: #8bb8c8;
    }

    /* ── Page title ── */
    h1 {
        color: #00d4ff !important;
        font-family: 'Courier New', monospace !important;
        font-size: 1.8rem !important;
        letter-spacing: 0.04em;
        text-shadow: 0 0 20px #00d4ff66;
    }
    h2, h3 {
        color: #7ecfff !important;
        font-family: 'Courier New', monospace !important;
        letter-spacing: 0.02em;
    }

    /* ── Sidebar ── */
    section[data-testid="stSidebar"] {
        background: #020810 !important;
        border-right: 1px solid #00d4ff22;
    }
    section[data-testid="stSidebar"] .stMarkdown p,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] .stRadio label {
        color: #a8d8ea !important;
        font-size: 0.85rem;
    }
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3 {
        color: #00d4ff !important;
        font-size: 1rem !important;
        text-shadow: 0 0 10px #00d4ff55;
    }

    /* ── Dataframe / table ── */
    .stDataFrame {
        border: 1px solid #00d4ff22 !important;
        border-radius: 8px !important;
    }
    [data-testid="stDataFrameResizable"] th {
        background: #0a1628 !important;
        color: #7ecfff !important;
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }
    [data-testid="stDataFrameResizable"] td {
        color: #c8ddf0 !important;
        font-size: 0.82rem;
    }

    /* ── Buttons ── */
    .stButton > button {
        background: #0a1628 !important;
        color: #00d4ff !important;
        border: 1px solid #00d4ff55 !important;
        border-radius: 6px !important;
        font-weight: 700;
        letter-spacing: 0.05em;
        transition: all 0.2s;
    }
    .stButton > button:hover {
        background: #00d4ff15 !important;
        border-color: #00d4ff !important;
        box-shadow: 0 0 12px #00d4ff44;
    }
    .stButton > button[kind="primary"] {
        background: #1a0030 !important;
        color: #bf80ff !important;
        border-color: #7c3aed88 !important;
    }

    /* ── Expander ── */
    .streamlit-expanderHeader {
        background: #0a1628 !important;
        border: 1px solid #00d4ff22 !important;
        border-radius: 8px !important;
        color: #a8d8ea !important;
    }
    .streamlit-expanderContent {
        background: #050b14 !important;
        border: 1px solid #00d4ff15 !important;
        border-top: none !important;
    }

    /* ── Selectbox / Slider labels ── */
    .stSelectbox label, .stSlider label, .stMultiSelect label, .stCheckbox label {
        color: #7ecfff !important;
        font-size: 0.78rem !important;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    /* ── Caption / sub-text ── */
    .stCaption, .stMarkdown small {
        color: #5a7a8a !important;
    }

    /* ── Divider ── */
    hr {
        border-color: #00d4ff18 !important;
    }

    /* ── Code block ── */
    .stCode {
        background: #020810 !important;
        border: 1px solid #00d4ff22 !important;
        border-radius: 6px !important;
    }
    .stCode code {
        color: #3fb950 !important;
        font-size: 0.78rem !important;
    }

    /* ── Info / warning boxes ── */
    .stAlert {
        border-radius: 8px !important;
        border-left-width: 4px !important;
    }
</style>
""", unsafe_allow_html=True)


# ── API helpers ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=REFRESH_S)
def fetch_scores() -> list[dict]:
    try:
        r = requests.get(f"{API_BASE}/score", timeout=3)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


@st.cache_data(ttl=REFRESH_S)
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


@st.cache_data(ttl=10)
def fetch_health() -> dict:
    try:
        r = requests.get(f"{API_BASE}/health", timeout=2)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"status": "unreachable", "model_loaded": False, "db_rows": 0}


@st.cache_data(ttl=5)
def fetch_incidents(limit: int = 100) -> list[dict]:
    try:
        r = requests.get(f"{API_BASE}/incidents", params={"limit": limit}, timeout=3)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


@st.cache_data(ttl=5)
def fetch_audit_log(slice_id: Optional[str] = None, limit: int = 200, anomaly_only: bool = False) -> pd.DataFrame:
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


@st.cache_data(ttl=30)
def fetch_sla(slice_id: str, window: str) -> dict:
    try:
        r = requests.get(f"{API_BASE}/sla", params={"slice_id": slice_id, "window": window}, timeout=3)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"compliance": 100.0, "total": 0, "anomalous": 0}


def fetch_injection_status() -> list[str]:
    try:
        r = requests.get(f"{API_BASE}/inject-attack/status", timeout=2)
        r.raise_for_status()
        return r.json().get("active_injections", [])
    except Exception:
        return []


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


# ── Gauge chart ────────────────────────────────────────────────────────────────

def make_gauge(title: str, value: float | None) -> go.Figure:
    display_val = value if value is not None else 0
    color = confidence_color(value)

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=display_val,
        title={"text": title, "font": {"size": 13, "color": "#7ecfff", "family": "Segoe UI"}},
        number={"suffix": "%", "font": {"size": 28, "color": color, "family": "Segoe UI"}, "valueformat": ".1f"},
        delta={
            "reference": 70,
            "increasing": {"color": "#3fb950"},
            "decreasing": {"color": "#f85149"},
            "font": {"size": 12},
        },
        gauge={
            "axis": {
                "range": [0, 100],
                "tickcolor": "#3a5a6a",
                "tickfont": {"color": "#7ecfff", "size": 10},
                "tickwidth": 1,
                "dtick": 20,
            },
            "bar": {"color": color, "thickness": 0.38},
            "bgcolor": "#020810",
            "bordercolor": "#00d4ff",  # ✅ Fixed: 6-digit hex only (no alpha)
            "borderwidth": 2,
            "steps": [
                {"range": [0, 40],    "color": "#1a0505"},
                {"range": [40, 70],   "color": "#1a1100"},
                {"range": [70, 100],  "color": "#051a08"},
            ],
            "threshold": {
                "line": {"color": "#f85149", "width": 3},
                "thickness": 0.85,
                "value": 40,
            },
        },
    ))
    fig.update_layout(
        paper_bgcolor="#050b14",
        plot_bgcolor="#050b14",
        height=230,
        margin=dict(t=40, b=10, l=30, r=30),
    )
    return fig

# ── Timeline chart ─────────────────────────────────────────────────────────────

def make_timeline(dfs: dict[str, pd.DataFrame]) -> go.Figure:
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        subplot_titles=("CPU %", "Memory (MB)", "Network RX KB"),
        vertical_spacing=0.1,
    )

    colors = {"slice-a": "#00d4ff", "slice-b": "#f78166"}
    # ✅ fillcolor SUPPORTS rgba/alpha - this is the ONLY place alpha works
    fill_colors = {"slice-a": "rgba(0,212,255,0.08)", "slice-b": "rgba(247,129,102,0.08)"}

    for slice_id, df in dfs.items():
        if df.empty:
            continue
        c = colors.get(slice_id, "#8b949e")
        fc = fill_colors.get(slice_id, "rgba(255,255,255,0.05)")

        # CPU — filled area
        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=df["cpu_pct"],
            name=f"{slice_id} CPU",
            line=dict(color=c, width=2),
            fill="tozeroy", fillcolor=fc,  # ✅ fillcolor supports alpha
            mode="lines",
        ), row=1, col=1)

        # Memory — dashed line with fill
        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=df["mem_mb"],
            name=f"{slice_id} MEM",
            line=dict(color=c, width=2, dash="dot"),
            fill="tozeroy", fillcolor=fc,
            mode="lines",
        ), row=2, col=1)

        # Network — dashed
        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=df["net_rx_kb"],
            name=f"{slice_id} RX",
            line=dict(color=c, width=2, dash="dash"),
            fill="tozeroy", fillcolor=fc,
            mode="lines",
        ), row=3, col=1)

        # Anomaly markers
        if "anomaly_score" in df.columns and df["anomaly_score"].notna().any():
            anomalies = df[df["anomaly_score"] < 0]
            if not anomalies.empty:
                fig.add_trace(go.Scatter(
                    x=anomalies["timestamp"], y=anomalies["cpu_pct"],
                    mode="markers",
                    marker=dict(color="#f85149", size=10, symbol="x",
                                line=dict(width=2, color="#ff0000")),
                    name=f"{slice_id} ⚠ anomaly",
                ), row=1, col=1)

    # ✅ All colors here are 6-digit hex only
    axis_style = dict(
        gridcolor="#0f2030",
        zerolinecolor="#0f2030",
        linecolor="#00d4ff",  # ✅ Fixed: 6-digit hex
        tickfont=dict(color="#7ecfff", size=10),
        title_font=dict(color="#7ecfff"),
    )

    fig.update_layout(
        paper_bgcolor="#050b14",
        plot_bgcolor="#050b14",
        font=dict(color="#7ecfff", size=11, family="Segoe UI"),
        legend=dict(
            bgcolor="#0a1628",
            bordercolor="#00d4ff",  # ✅ Fixed: 6-digit hex only
            borderwidth=1,
            font=dict(size=10, color="#a8d8ea"),
        ),
        height=450,
        margin=dict(t=50, b=20, l=50, r=20),
    )
    for i in range(1, 4):
        fig.update_yaxes(**axis_style, row=i, col=1)
        fig.update_xaxes(**axis_style, row=i, col=1)

    # Subplot title color
    for annotation in fig.layout.annotations:
        annotation.font.color = "#7ecfff"
        annotation.font.family = "Segoe UI"

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
        badge = _badge_html(e.get("attack_type"))
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
# ── SIDEBAR (shared across all pages) ─────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def render_sidebar() -> tuple[str, int, int, list[str]]:
    """Returns (selected_page, refresh_rate, history_len, selected_slices)."""
    with st.sidebar:
        st.title("⚙️ Controls")

        # Page navigation
        page = st.radio(
            "Navigate",
            ["🛡️ Live Monitor", "🔍 Anomaly Classifier", "📋 Audit & Incident Log"],
            key="nav_page",
        )

        st.divider()

        refresh_rate    = st.slider("Refresh interval (s)", 2, 30, REFRESH_S)
        history_len     = st.slider("History window (points)", 20, 200, 120)
        selected_slices = st.multiselect("Slices to display", SLICE_NAMES, default=SLICE_NAMES)

        st.divider()

        # System status
        health = fetch_health()
        st.markdown("### System Status")
        st.markdown(f"{'🟢' if health.get('status') == 'ok' else '🔴'} **API:** {health.get('status', 'unknown')}")
        st.markdown(f"{'🟢' if health.get('model_loaded') else '🔴'} **Model:** {'loaded' if health.get('model_loaded') else 'not loaded'}")
        st.markdown(f"{'🟢' if health.get('classifier_ready') else '🟡'} **Classifier:** {'ready' if health.get('classifier_ready') else 'needs retrain'}")
        st.markdown(f"💾 **DB rows:** {health.get('db_rows', 0)}")

        st.divider()

        # ── Attack Simulation ───────────────────────────────────────────────
        st.markdown("### 🎯 Attack Simulation")
        st.caption("Injects spiked rows into SQLite for ~60s so the ML model detects an attack.")

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
            st.warning(f"⏳ Injection running on **{sim_slice}** — ~60s total, refreshes every 5s")
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

        st.divider()
        st.caption("Manual Docker commands (real network breach):")
        st.code("docker exec -d slice-b iperf3 -s", language="bash")
        st.code(
            "docker network connect \\\n"
            "  network-slice-validator_slice_b_net slice-a",
            language="bash",
        )
        st.code("docker exec slice-a iperf3 -c slice-b -t 20 -b 5M", language="bash")

    return page, refresh_rate, history_len, selected_slices


# ══════════════════════════════════════════════════════════════════════════════
# ── PAGE 1: LIVE MONITOR ───────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def page_live_monitor(refresh_rate: int, history_len: int, selected_slices: list[str]):
    st.title("🛡️ 5G Network Slicing Isolation Monitor")
    st.caption(
        f"Live telemetry • Refreshes every {refresh_rate}s • "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    scores    = fetch_scores()
    score_map = {s["slice_id"]: s for s in scores if "slice_id" in s}
    dfs: dict[str, pd.DataFrame] = {name: fetch_history(name, limit=history_len) for name in selected_slices}

    # Gauges
    gauge_cols = st.columns(len(selected_slices))
    for i, name in enumerate(selected_slices):
        with gauge_cols[i]:
            score_data  = score_map.get(name, {})
            conf        = score_data.get("isolation_confidence")
            attack_type = score_data.get("attack_type")
            st.plotly_chart(make_gauge(f"{name} Isolation Confidence", conf),
                            use_container_width=True, key=f"gauge_{name}")
            if attack_type:
                st.markdown(
                    f"<div style='text-align:center;margin-top:-12px'>"
                    f"{_badge_html(attack_type)}</div>",
                    unsafe_allow_html=True,
                )

    # Metric cards
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

    # Timeline
    st.markdown("### Metric Timeline")
    if any(not df.empty for df in dfs.values()):
        st.plotly_chart(make_timeline(dfs), use_container_width=True)
    else:
        st.info("Waiting for telemetry data… (collector may still be starting)")

    # Alert feed
    st.markdown("### Alert Feed (last 10 anomalies)")
    render_alert_feed(dfs, score_map)


# ══════════════════════════════════════════════════════════════════════════════
# ── PAGE 2: ANOMALY CLASSIFIER ─────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def page_anomaly_classifier(selected_slices: list[str]):
    st.title("🔍 Anomaly Classifier")
    st.caption("Attack-type breakdown and Z-score feature analysis for detected anomalies.")

    # Pull recent history for analysis
    all_rows = []
    for name in selected_slices:
        df = fetch_history(name, limit=500)
        if not df.empty:
            all_rows.append(df)
    if not all_rows:
        st.info("No telemetry data yet. Let the collector run for a bit.")
        return

    df_all = pd.concat(all_rows, ignore_index=True)
    df_scored = df_all[df_all["anomaly_score"].notna()].copy()

    if df_scored.empty:
        st.info("No scored rows yet — the API scores rows as the dashboard polls /score. Give it a moment.")
        return

    df_anomalies = df_scored[df_scored["anomaly_score"] < 0].copy()

    # ── Summary KPIs ──────────────────────────────────────────────────────────
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

    # ── Attack type distribution ──────────────────────────────────────────────
    if not df_anomalies.empty and "attack_type" in df_anomalies.columns:
        st.markdown("### Attack Type Distribution")
        type_counts = df_anomalies["attack_type"].fillna("Unknown Anomaly").value_counts().reset_index()
        type_counts.columns = ["Attack Type", "Count"]

        colors_map = {
            "CPU Starvation":    "#b91c1c",
            "Memory Exhaustion": "#b45309",
            "Network Breach":    "#7c3aed",
            "Combined Attack":   "#374151",
            "Unknown Anomaly":   "#4b5563",
        }
        bar_colors = [colors_map.get(t, "#4b5563") for t in type_counts["Attack Type"]]

        col_bar, col_pie = st.columns(2)

        with col_bar:
            fig_bar = go.Figure(go.Bar(
                x=type_counts["Attack Type"],
                y=type_counts["Count"],
                marker_color=bar_colors,
                text=type_counts["Count"],
                textposition="auto",
            ))
            fig_bar.update_layout(
                title="Anomaly Count by Type",
                paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                font=dict(color="#c9d1d9"),
                xaxis=dict(gridcolor="#21262d"),
                yaxis=dict(gridcolor="#21262d"),
                height=300, margin=dict(t=40, b=20, l=20, r=20),
            )
            st.plotly_chart(fig_bar, use_container_width=True)

        with col_pie:
            fig_pie = go.Figure(go.Pie(
                labels=type_counts["Attack Type"],
                values=type_counts["Count"],
                marker_colors=bar_colors,
                hole=0.4,
                textinfo="percent+label",
                textfont_size=11,
            ))
            fig_pie.update_layout(
                title="Attack Type Share",
                paper_bgcolor="#0d1117",
                font=dict(color="#c9d1d9"),
                height=300, margin=dict(t=40, b=20, l=20, r=20),
                showlegend=False,
            )
            st.plotly_chart(fig_pie, use_container_width=True)

    st.divider()

    # ── Feature analysis per attack type ──────────────────────────────────────
    st.markdown("### Feature Signature per Attack Type")
    st.caption("Average CPU, Memory, and Network values when each attack type is detected — compared to normal baseline.")

    normal_df  = df_scored[df_scored["anomaly_score"] >= 0]
    normal_avg = {
        "cpu_pct":   normal_df["cpu_pct"].mean()   if not normal_df.empty else 0,
        "mem_mb":    normal_df["mem_mb"].mean()    if not normal_df.empty else 0,
        "net_rx_kb": normal_df["net_rx_kb"].mean() if not normal_df.empty else 0,
        "net_tx_kb": normal_df["net_tx_kb"].mean() if not normal_df.empty else 0,
    }

    if not df_anomalies.empty and "attack_type" in df_anomalies.columns:
            for attack_type, group in df_anomalies.groupby("attack_type"):
                st.markdown(
            f'<div class="cyber-card" style="padding:12px 16px; margin-bottom:8px">'
            f'{_badge_html(attack_type)}&nbsp;&nbsp;'
            f'<strong style="color:#e8f4fd">{len(group)} events</strong></div>',
            unsafe_allow_html=True
        )
                with st.container():
                    st.markdown('<div style="padding-left:12px; border-left:2px solid #00d4ff33; margin-bottom:12px">', unsafe_allow_html=True)

                    ac1, ac2, ac3, ac4 = st.columns(4)
                    ac1.metric("Avg CPU %", f"{group['cpu_pct'].mean():.1f}%",
                       delta=f"+{group['cpu_pct'].mean() - normal_avg['cpu_pct']:.1f}% vs normal",
                       delta_color="inverse")
                    ac2.metric("Avg Mem MB", f"{group['mem_mb'].mean():.1f}",
                       delta=f"+{group['mem_mb'].mean() - normal_avg['mem_mb']:.1f} vs normal",
                       delta_color="inverse")
                    ac3.metric("Avg RX KB/s", f"{group['net_rx_kb'].mean():.2f}",
                       delta=f"+{group['net_rx_kb'].mean() - normal_avg['net_rx_kb']:.2f} vs normal",
                       delta_color="inverse")
                    ac4.metric("Avg TX KB/s", f"{group['net_tx_kb'].mean():.2f}",
                       delta=f"+{group['net_tx_kb'].mean() - normal_avg['net_tx_kb']:.2f} vs normal",
                       delta_color="inverse")

                    features = ["cpu_pct", "mem_mb", "net_rx_kb", "net_tx_kb"]
                    labels = ["CPU %", "Mem MB", "RX KB/s", "TX KB/s"]
                    atk_vals = [group[f].mean() for f in features]
                    norm_vals = [normal_avg[f] for f in features]

                    fig_feat = go.Figure()
                    fig_feat.add_trace(go.Bar(name="During Attack", x=labels, y=atk_vals,
                                      marker_color="#f85149", opacity=0.85))
                    fig_feat.add_trace(go.Bar(name="Normal Baseline", x=labels, y=norm_vals,
                                      marker_color="#3fb950", opacity=0.65))
                    fig_feat.update_layout(
                        barmode="group", paper_bgcolor="#050b14", plot_bgcolor="#050b14",
                        font=dict(color="#c9d1d9", size=11),
                        height=220, margin=dict(t=20, b=20, l=20, r=20),
                        legend=dict(bgcolor="#161b22", font=dict(size=10)),
                        xaxis=dict(gridcolor="#21262d"),
                        yaxis=dict(gridcolor="#21262d"),
                    )
                    st.plotly_chart(fig_feat, use_container_width=True)
                    st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.info("No classified anomalies yet. Inject an attack from the sidebar.")

    st.divider()

    # ── Anomaly score timeline ─────────────────────────────────────────────────
    st.markdown("### Anomaly Score Timeline")
    st.caption("Scores below 0 = anomalous (IsolationForest decision function). Lower = more anomalous.")

    fig_score = go.Figure()
    slice_colors = {"slice-a": "#58a6ff", "slice-b": "#f78166"}
    for name in selected_slices:
        df_s = df_scored[df_scored["slice_id"] == name]
        if df_s.empty:
            continue
        c = slice_colors.get(name, "#8b949e")
        fig_score.add_trace(go.Scatter(
            x=df_s["timestamp"], y=df_s["anomaly_score"],
            name=name, line=dict(color=c, width=1.5), mode="lines",
        ))
    fig_score.add_hline(y=0, line_dash="dash", line_color="#f85149",
                        annotation_text="Anomaly Threshold", annotation_position="bottom right")
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
# ── PAGE 3: AUDIT & INCIDENT LOG ───────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def page_audit_log(selected_slices: list[str]):
    st.title("📋 Audit & Incident Log")
    st.caption("Structured incident history, SLA compliance, and full anomaly audit trail.")

    # ── SLA Scoreboard ────────────────────────────────────────────────────────
    st.markdown("### SLA Compliance Scoreboard")
    sla_cols = st.columns(len(selected_slices) * 3)
    for i, name in enumerate(selected_slices):
        for j, window in enumerate(["1h", "24h", "7d"]):
            sla_data = fetch_sla(name, window)
            comp     = sla_data.get("compliance", 100.0)
            color    = "normal" if comp >= 99.5 else ("off" if comp >= 98 else "inverse")
            sla_cols[i * 3 + j].metric(
                f"{name} SLA {window}",
                f"{comp:.2f}%",
                delta="🟢 OK" if comp >= 99.5 else ("🟡 Warn" if comp >= 98 else "🔴 Breach"),
                delta_color=color,
            )

    st.divider()

    # ── Incident log ──────────────────────────────────────────────────────────
    st.markdown("### Incident History")
    incidents = fetch_incidents(limit=100)

    if not incidents:
        st.info("No incidents recorded yet. Inject an attack to generate one.")
    else:
        # Export button
        csv_url = f"{API_BASE}/incidents/export"
        st.markdown(f"[⬇️ Download incidents CSV]({csv_url})")

        for inc in incidents:
            started  = datetime.fromtimestamp(inc["started_at"]).strftime("%Y-%m-%d %H:%M:%S")
            resolved = (
                datetime.fromtimestamp(inc["resolved_at"]).strftime("%H:%M:%S")
                if inc.get("resolved_at") else "ongoing"
            )
            duration = f"{inc['duration_s']:.0f}s" if inc.get("duration_s") else "—"
            badge    = _badge_html(inc.get("attack_type"))
            status   = "🔴 ACTIVE" if inc.get("is_active") else "✅ Resolved"
            css_cls  = "incident-open" if inc.get("is_active") else "incident-closed"
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

    st.divider()

    # ── Full audit table ──────────────────────────────────────────────────────
    st.markdown("### Anomaly Audit Table")

    col_filter1, col_filter2, col_filter3 = st.columns(3)
    with col_filter1:
        filter_slice = st.selectbox("Filter by slice", ["All"] + SLICE_NAMES, key="audit_slice")
    with col_filter2:
        audit_limit = st.slider("Max rows", 50, 500, 200, key="audit_limit")
    with col_filter3:
        anomaly_only = st.checkbox("Anomalies only", value=True, key="audit_anomaly_only")

    slice_param = None if filter_slice == "All" else filter_slice
    df_audit = fetch_audit_log(slice_id=slice_param, limit=audit_limit, anomaly_only=anomaly_only)

    if df_audit.empty:
        st.info("No rows match the current filters.")
    else:
        # Format for display
        display_df = df_audit.copy()
        display_df["timestamp"] = display_df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
        if "anomaly_score" in display_df.columns:
            display_df["is_anomaly"] = display_df["anomaly_score"].apply(
                lambda x: "⚠️ YES" if pd.notna(x) and x < 0 else "✅ NO"
            )
        cols_show = ["timestamp", "slice_id", "cpu_pct", "mem_mb", "net_rx_kb", "net_tx_kb",
                     "anomaly_score", "attack_type", "is_anomaly"]
        cols_show = [c for c in cols_show if c in display_df.columns]

        st.dataframe(
            display_df[cols_show].rename(columns={
                "timestamp": "Time", "slice_id": "Slice",
                "cpu_pct": "CPU %", "mem_mb": "Mem MB",
                "net_rx_kb": "RX KB/s", "net_tx_kb": "TX KB/s",
                "anomaly_score": "Score", "attack_type": "Attack Type",
                "is_anomaly": "Anomaly?",
            }),
            use_container_width=True,
            height=400,
        )

        # CSV download
        csv_bytes = display_df[cols_show].to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download as CSV",
            data=csv_bytes,
            file_name=f"audit_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )


# ══════════════════════════════════════════════════════════════════════════════
# ── MAIN ───────────────────────────────────────────════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════

def main():
    page, refresh_rate, history_len, selected_slices = render_sidebar()

    if page == "🛡️ Live Monitor":
        page_live_monitor(refresh_rate, history_len, selected_slices)
    elif page == "🔍 Anomaly Classifier":
        page_anomaly_classifier(selected_slices)
    elif page == "📋 Audit & Incident Log":
        page_audit_log(selected_slices)

    time.sleep(refresh_rate)
    st.rerun()


if __name__ == "__main__":
    main()