"""
dashboard/app.py
────────────────
Streamlit operator dashboard for 5G Slice Isolation Monitor.

Pages:
  1. 🛡️ Live Monitor       – gauges, timeline, metric cards, alert feed
  2. 🔍 Anomaly Classifier  – per-attack-type breakdown, Z-score analysis
  3. 📋 Audit & Incident Log – SLA scoreboard, incident history, model health,
                               full audit table, CSV export
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
    .model-health-card {
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 14px 16px;
        margin: 6px 0;
    }
    h1 { color: #58a6ff !important; }
    h2, h3 { color: #c9d1d9 !important; }
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


@st.cache_data(ttl=30)
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


@st.cache_data(ttl=30)
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
    colors = {"slice-a": "#58a6ff", "slice-b": "#f78166"}
    for slice_id, df in dfs.items():
        if df.empty:
            continue
        c = colors.get(slice_id, "#8b949e")
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
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

def render_sidebar() -> tuple[str, int, int, list[str]]:
    with st.sidebar:
        st.title("⚙️ Controls")

        page = st.radio(
            "Navigate",
            ["🛡️ Live Monitor", "🔍 Anomaly Classifier", "📋 Audit & Incident Log"],
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
# PAGE 1: LIVE MONITOR
# ══════════════════════════════════════════════════════════════════════════════

def page_live_monitor(refresh_rate: int, history_len: int, selected_slices: list[str]):
    st.title("🛡️ 5G Network Slicing Isolation Monitor")
    st.caption(
        f"Live telemetry • Refreshes every {refresh_rate}s • "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    scores    = fetch_scores()
    score_map = {s["slice_id"]: s for s in scores if "slice_id" in s}
    dfs: dict[str, pd.DataFrame] = {
        name: fetch_history(name, limit=history_len) for name in selected_slices
    }

    # Gauges
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

    # Summary KPIs
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

    # Attack type distribution
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

    # Feature signature per attack type
    st.markdown("### Feature Signature per Attack Type")
    st.caption(
        "Average CPU, Memory, and Network values when each attack type is detected "
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
            with st.expander(
                f"{_badge_html(attack_type)} &nbsp; {len(group)} events", expanded=True
            ):
                st.markdown("", unsafe_allow_html=True)
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

                features  = ["cpu_pct", "mem_mb", "net_rx_kb", "net_tx_kb"]
                labels    = ["CPU %", "Mem MB", "RX KB/s", "TX KB/s"]
                atk_vals  = [group[f].mean() for f in features]
                norm_vals = [normal_avg[f] for f in features]

                fig_feat = go.Figure()
                fig_feat.add_trace(go.Bar(
                    name="During Attack", x=labels, y=atk_vals,
                    marker_color="#f85149", opacity=0.85,
                ))
                fig_feat.add_trace(go.Bar(
                    name="Normal Baseline", x=labels, y=norm_vals,
                    marker_color="#3fb950", opacity=0.65,
                ))
                fig_feat.update_layout(
                    barmode="group",
                    paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                    font=dict(color="#c9d1d9", size=11),
                    height=220, margin=dict(t=20, b=20, l=20, r=20),
                    legend=dict(bgcolor="#161b22", font=dict(size=10)),
                    xaxis=dict(gridcolor="#21262d"),
                    yaxis=dict(gridcolor="#21262d"),
                )
                st.plotly_chart(fig_feat, use_container_width=True)
    else:
        st.info("No classified anomalies yet. Inject an attack from the sidebar to see classification.")

    st.divider()

    # Anomaly score timeline
    st.markdown("### Anomaly Score Timeline")
    st.caption(
        "Scores below 0 = anomalous (IsolationForest decision function). "
        "Lower = more anomalous."
    )

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
    fig_score.add_hline(
        y=0, line_dash="dash", line_color="#f85149",
        annotation_text="Anomaly Threshold",
        annotation_position="bottom right",
    )
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
    st.caption("SLA compliance, structured incident history, model health, and full anomaly audit trail.")

    # ── SLA Scoreboard ────────────────────────────────────────────────────────
    st.markdown("### 📊 SLA Compliance Scoreboard")
    sla_cols = st.columns(len(selected_slices) * 3)
    for i, name in enumerate(selected_slices):
        for j, window in enumerate(["1h", "24h", "7d"]):
            sla_data = fetch_sla(name, window)
            comp     = sla_data.get("compliance", 100.0)
            source   = sla_data.get("source", "sqlite")
            color    = "normal" if comp >= 99.5 else ("off" if comp >= 98 else "inverse")
            sla_cols[i * 3 + j].metric(
                f"{name} SLA {window}",
                f"{comp:.2f}%",
                delta=(
                    f"🟢 OK ({source})" if comp >= 99.5
                    else (f"🟡 Warn ({source})" if comp >= 98
                          else f"🔴 Breach ({source})")
                ),
                delta_color=color,
            )

    # SLA heatmap (anomaly density by hour of day, last 7 days)
    st.markdown("#### Anomaly Density Heatmap (last 7 days)")
    all_hist = []
    for name in selected_slices:
        df_h = fetch_history(name, limit=500)
        if not df_h.empty:
            all_hist.append(df_h)

    if all_hist:
        df_heat = pd.concat(all_hist, ignore_index=True)
        if "anomaly_score" in df_heat.columns and "timestamp" in df_heat.columns:
            df_heat["hour"]       = df_heat["timestamp"].dt.hour
            df_heat["weekday"]    = df_heat["timestamp"].dt.day_name()
            df_heat["is_anomaly"] = df_heat["anomaly_score"].apply(
                lambda x: 1 if pd.notna(x) and x < 0 else 0
            )
            hm = df_heat.groupby(["weekday", "hour"])["is_anomaly"].sum().reset_index()
            day_order     = ["Monday", "Tuesday", "Wednesday", "Thursday",
                             "Friday", "Saturday", "Sunday"]
            hm["weekday"] = pd.Categorical(hm["weekday"], categories=day_order, ordered=True)
            hm            = hm.sort_values("weekday")
            fig_heat = px.density_heatmap(
                hm, x="hour", y="weekday", z="is_anomaly",
                color_continuous_scale="Reds",
                labels={"hour": "Hour of Day", "weekday": "Day",
                        "is_anomaly": "Anomalies"},
            )
            fig_heat.update_layout(
                paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                font=dict(color="#c9d1d9"),
                height=300, margin=dict(t=20, b=0),
            )
            st.plotly_chart(fig_heat, use_container_width=True)
    else:
        st.caption("Not enough history for heatmap yet.")

    st.divider()

    # ── Model Health (Capstone 4) ──────────────────────────────────────────────
    st.markdown("### 🤖 Model Health")
    m_status = fetch_model_status()

    if m_status and m_status.get("trained_at"):
        mh1, mh2, mh3, mh4 = st.columns(4)
        mh1.metric("Model Age",          f"{m_status.get('age_minutes', '?')} min")
        mh2.metric("Training Samples",   str(m_status.get("n_samples", "?")))
        drift = m_status.get("drift_ratio_100rows")
        mh3.metric("Drift Rate (100 rows)",
                   f"{drift * 100:.1f}%" if drift is not None else "?",
                   delta="⚠️ High" if drift and drift > 0.20 else "Normal",
                   delta_color="inverse" if drift and drift > 0.20 else "off")
        mh4.metric("Drift Strikes",
                   f"{m_status.get('drift_strikes', 0)} / 3")

        # Drift gauge
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
                    "threshold": {
                        "line": {"color": "#d29922", "width": 3},
                        "value": 20,
                    },
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

        # Manual retrain trigger
        if st.button("🔄 Reload Model Now", type="secondary"):
            r = requests.post(f"{API_BASE}/reload-model", timeout=5)
            if r.ok:
                st.success("Model reloaded successfully.")
            else:
                st.error("Reload failed.")
    else:
        st.info("Model status unavailable — API may still be starting.")

    st.divider()

    # ── Incident log ──────────────────────────────────────────────────────────
    st.markdown("### 🚨 Incident History")
    incidents = fetch_incidents(limit=100)

    if not incidents:
        st.info("No incidents recorded yet. Inject an attack to generate one.")
    else:
        st.markdown(f"[⬇️ Download incidents CSV]({API_BASE}/incidents/export)")
        for inc in incidents:
            # Handle both Unix timestamps (SQLite) and ISO strings (Supabase)
            try:
                started_val = inc["started_at"]
                started = (
                    datetime.fromisoformat(str(started_val)).strftime("%Y-%m-%d %H:%M:%S")
                    if isinstance(started_val, str)
                    else datetime.utcfromtimestamp(float(started_val)).strftime("%Y-%m-%d %H:%M:%S")
                )
            except Exception:
                started = str(inc.get("started_at", "?"))

            try:
                resolved_val = inc.get("resolved_at")
                if resolved_val:
                    resolved = (
                        datetime.fromisoformat(str(resolved_val)).strftime("%H:%M:%S")
                        if isinstance(resolved_val, str)
                        else datetime.utcfromtimestamp(float(resolved_val)).strftime("%H:%M:%S")
                    )
                else:
                    resolved = "ongoing"
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

    st.divider()

    # ── Full audit table ──────────────────────────────────────────────────────
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
                "timestamp":    "Time",
                "slice_id":     "Slice",
                "cpu_pct":      "CPU %",
                "mem_mb":       "Mem MB",
                "net_rx_kb":    "RX KB/s",
                "net_tx_kb":    "TX KB/s",
                "anomaly_score": "Score",
                "attack_type":  "Attack Type",
                "is_anomaly":   "Anomaly?",
            }),
            use_container_width=True,
            height=400,
        )

        csv_bytes = display_df[cols_show].to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download as CSV",
            data=csv_bytes,
            file_name=f"audit_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
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