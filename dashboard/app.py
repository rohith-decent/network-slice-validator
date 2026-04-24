"""
dashboard/app.py
────────────────
Streamlit operator dashboard for 5G Slice Isolation Monitor.
Polls FastAPI every 3 seconds. Shows:
  - Isolation confidence gauges (per slice)
  - Live metric timeline (cpu, mem, net)
  - Anomaly alert feed
  - Per-slice metric cards
"""

import os
import time
import requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

API_BASE    = os.environ.get("API_BASE", "http://localhost:8000")
SLICE_NAMES = [s.strip() for s in os.environ.get("SLICE_NAMES", "slice-a,slice-b").split(",")]
REFRESH_S   = 3

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="5G Slice Isolation Monitor",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .main { background-color: #0d1117; }
    .block-container { padding-top: 1rem; }
    .stMetric { background: #161b22; border-radius: 8px; padding: 12px; border: 1px solid #30363d; }
    .stMetric label { color: #8b949e !important; font-size: 0.75rem !important; text-transform: uppercase; }
    .stMetric .metric-value { color: #f0f6fc !important; }
    .alert-card { background: #2d1117; border-left: 4px solid #f85149; border-radius: 4px; padding: 8px 12px; margin: 4px 0; font-size: 0.85rem; color: #f0f6fc; }
    .normal-card { background: #0d1a0d; border-left: 4px solid #3fb950; border-radius: 4px; padding: 8px 12px; margin: 4px 0; font-size: 0.85rem; color: #f0f6fc; }
    h1 { color: #58a6ff !important; }
    h2, h3 { color: #c9d1d9 !important; }
</style>
""", unsafe_allow_html=True)


# ── API helpers ───────────────────────────────────────────────────────────────

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
        r = requests.get(f"{API_BASE}/metrics/history",
                         params={"slice_id": slice_id, "limit": limit}, timeout=3)
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


# ── Gauge chart ───────────────────────────────────────────────────────────────

def confidence_color(val: float | None) -> str:
    if val is None:
        return "#8b949e"
    if val >= 70:
        return "#3fb950"
    elif val >= 40:
        return "#d29922"
    return "#f85149"


def make_gauge(title: str, value: float | None, slice_id: str) -> go.Figure:
    display_val = value if value is not None else 0
    color = confidence_color(value)

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=display_val,
        title={"text": title, "font": {"size": 14, "color": "#c9d1d9"}},
        number={"suffix": "%", "font": {"size": 24, "color": color}},
        delta={"reference": 70, "increasing": {"color": "#3fb950"}, "decreasing": {"color": "#f85149"}},
        gauge={
            "axis": {"range": [0, 100], "tickcolor": "#30363d", "tickfont": {"color": "#8b949e"}},
            "bar": {"color": color, "thickness": 0.3},
            "bgcolor": "#161b22",
            "bordercolor": "#30363d",
            "borderwidth": 1,
            "steps": [
                {"range": [0, 40],   "color": "#2d1117"},
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


# ── Timeline chart ────────────────────────────────────────────────────────────

def make_timeline(dfs: dict[str, pd.DataFrame]) -> go.Figure:
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        subplot_titles=("CPU %", "Memory (MB)", "Network RX KB"),
        vertical_spacing=0.08,
    )
    colors = {"slice-a": "#58a6ff", "slice-b": "#f78166"}

    for slice_id, df in dfs.items():
        if df.empty:
            continue
        c = colors.get(slice_id, "#8b949e")

        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=df["cpu_pct"],
            name=f"{slice_id} CPU", line=dict(color=c, width=1.5),
            showlegend=True,
        ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=df["mem_mb"],
            name=f"{slice_id} MEM", line=dict(color=c, width=1.5, dash="dot"),
            showlegend=True,
        ), row=2, col=1)

        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=df["net_rx_kb"],
            name=f"{slice_id} RX", line=dict(color=c, width=1.5, dash="dash"),
            showlegend=True,
        ), row=3, col=1)

        # Overlay anomaly markers
        if "anomaly_score" in df.columns:
            anomalies = df[df["anomaly_score"] < 0] if df["anomaly_score"].notna().any() else pd.DataFrame()
            if not anomalies.empty:
                fig.add_trace(go.Scatter(
                    x=anomalies["timestamp"], y=anomalies["cpu_pct"],
                    mode="markers", marker=dict(color="#f85149", size=8, symbol="x"),
                    name=f"{slice_id} anomaly", showlegend=True,
                ), row=1, col=1)

    fig.update_layout(
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        font=dict(color="#8b949e", size=11),
        legend=dict(bgcolor="#161b22", bordercolor="#30363d", font=dict(size=10)),
        height=420,
        margin=dict(t=40, b=20, l=40, r=20),
    )
    for i in range(1, 4):
        fig.update_yaxes(gridcolor="#21262d", zerolinecolor="#21262d", row=i, col=1)
        fig.update_xaxes(gridcolor="#21262d", row=i, col=1)

    return fig


# ── Alert feed ────────────────────────────────────────────────────────────────

def render_alert_feed(dfs: dict[str, pd.DataFrame]):
    events = []
    for slice_id, df in dfs.items():
        if df.empty or "anomaly_score" not in df.columns:
            continue
        recent = df.tail(60)
        anom = recent[recent["anomaly_score"] < 0] if recent["anomaly_score"].notna().any() else pd.DataFrame()
        for _, row in anom.iterrows():
            conf = max(0.0, min(0.5, row["anomaly_score"]) + 0.5) * 100
            events.append({
                "ts":        row["timestamp"],
                "slice_id":  slice_id,
                "score":     row["anomaly_score"],
                "conf":      conf,
                "cpu":       row.get("cpu_pct", 0),
                "mem":       row.get("mem_mb", 0),
                "net_rx":    row.get("net_rx_kb", 0),
            })

    events.sort(key=lambda e: e["ts"], reverse=True)

    if not events:
        st.markdown('<div class="normal-card">✅ No anomalies detected — all slices isolated normally.</div>',
                    unsafe_allow_html=True)
        return

    for e in events[:10]:
        ts_str = e["ts"].strftime("%H:%M:%S") if hasattr(e["ts"], "strftime") else str(e["ts"])
        st.markdown(
            f'<div class="alert-card">'
            f'⚠️ <strong>{e["slice_id"]}</strong> @ {ts_str} — '
            f'Confidence: <strong>{e["conf"]:.1f}%</strong> | '
            f'Score: {e["score"]:.4f} | '
            f'CPU: {e["cpu"]:.1f}% | MEM: {e["mem"]:.1f}MB | RX: {e["net_rx"]:.2f}KB'
            f'</div>',
            unsafe_allow_html=True,
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # ── Sidebar ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.title("⚙️ Controls")
        refresh_rate = st.slider("Refresh interval (s)", 2, 30, REFRESH_S)
        history_len  = st.slider("History window (points)", 20, 200, 120)
        selected_slices = st.multiselect("Slices to display", SLICE_NAMES, default=SLICE_NAMES)

        st.divider()
        health = fetch_health()
        st.markdown("### System Status")
        status_color = "🟢" if health.get("status") == "ok" else "🔴"
        st.markdown(f"{status_color} **API:** {health.get('status', 'unknown')}")
        model_color = "🟢" if health.get("model_loaded") else "🔴"
        st.markdown(f"{model_color} **Model:** {'loaded' if health.get('model_loaded') else 'not loaded'}")
        st.markdown(f"💾 **DB rows:** {health.get('db_rows', 0)}")
        st.divider()
        st.caption("Demo injection commands:")
        st.code("docker exec slice-a sh -c 'while true; do :; done &'", language="bash")
        st.code("docker network connect slice_b_net slice-a", language="bash")

    # ── Header ───────────────────────────────────────────────────────────────
    st.title("🛡️ 5G Network Slicing Isolation Monitor")
    st.caption(f"Live telemetry • Refreshes every {refresh_rate}s • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Fetch data ────────────────────────────────────────────────────────────
    scores    = fetch_scores()
    score_map = {s["slice_id"]: s for s in scores if "slice_id" in s}
    dfs       = {}
    for name in selected_slices:
        dfs[name] = fetch_history(name, limit=history_len)

    # ── Top row: gauges + summary cards ──────────────────────────────────────
    gauge_cols = st.columns(len(selected_slices))
    for i, name in enumerate(selected_slices):
        with gauge_cols[i]:
            score_data = score_map.get(name, {})
            conf = score_data.get("isolation_confidence")
            st.plotly_chart(
                make_gauge(f"{name} Isolation Confidence", conf, name),
                use_container_width=True, key=f"gauge_{name}",
            )

    # ── Middle row: metric cards ──────────────────────────────────────────────
    st.markdown("### Current Metrics")
    card_cols = st.columns(len(selected_slices) * 4)
    for i, name in enumerate(selected_slices):
        score_data = score_map.get(name, {})
        features   = score_data.get("features", {})
        base_i     = i * 4
        with card_cols[base_i]:
            delta_color = "inverse" if (score_data.get("is_anomaly") is True) else "normal"
            st.metric(f"{name} CPU", f"{features.get('cpu_pct', 0):.1f}%",
                      delta="⚠️ ANOMALY" if score_data.get("is_anomaly") else "normal",
                      delta_color=delta_color)
        with card_cols[base_i + 1]:
            st.metric(f"{name} MEM", f"{features.get('mem_mb', 0):.1f} MB")
        with card_cols[base_i + 2]:
            st.metric(f"{name} RX", f"{features.get('net_rx_kb', 0):.2f} KB/s")
        with card_cols[base_i + 3]:
            st.metric(f"{name} TX", f"{features.get('net_tx_kb', 0):.2f} KB/s")

    st.divider()

    # ── Timeline chart ────────────────────────────────────────────────────────
    st.markdown("### Metric Timeline")
    if any(not df.empty for df in dfs.values()):
        st.plotly_chart(make_timeline(dfs), use_container_width=True)
    else:
        st.info("Waiting for telemetry data... (collector may still be starting)")

    # ── Alert feed ────────────────────────────────────────────────────────────
    st.markdown("### Alert Feed (last 10 anomalies)")
    render_alert_feed(dfs)

    # ── Auto-refresh ──────────────────────────────────────────────────────────
    time.sleep(refresh_rate)
    st.rerun()


if __name__ == "__main__":
    main()
