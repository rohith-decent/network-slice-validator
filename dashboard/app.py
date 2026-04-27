"""
dashboard/app.py — 5G Slice Isolation Monitor
Cyberpunk/Military grade UI with neon accents
"""

import os, time, requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

API_BASE    = os.environ.get("API_BASE", "http://localhost:8000")
SLICE_NAMES = [s.strip() for s in os.environ.get("SLICE_NAMES", "slice-a,slice-b").split(",")]
REFRESH_S   = 3

st.set_page_config(page_title="5G Slice Monitor", page_icon="🛡️",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Share Tech Mono', monospace !important;
    background-color: #010b14 !important;
    color: #a0d8ef !important;
}
.stApp {
    background: radial-gradient(ellipse at top, #001a2e 0%, #010b14 70%);
    background-attachment: fixed;
}
h1 {
    font-family: 'Orbitron', monospace !important;
    font-size: 1.6rem !important;
    font-weight: 700 !important;
    color: #00ffff !important;
    text-shadow: 0 0 30px #00ffff, 0 0 60px #00ffffaa;
    letter-spacing: 6px;
    text-transform: uppercase;
    text-align: center;
    padding: 0.5rem 0;
    border-bottom: 1px solid #00ffff33;
    margin-bottom: 0.5rem;
}
h2, h3 {
    font-family: 'Orbitron', monospace !important;
    color: #00ccff !important;
    font-size: 0.8rem !important;
    letter-spacing: 4px;
    text-transform: uppercase;
    border-left: 3px solid #00ffff;
    padding-left: 10px;
    text-shadow: 0 0 8px #00ccff66;
}
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #000d1a 0%, #001a2e 100%) !important;
    border-right: 1px solid #00ffff22 !important;
}
[data-testid="stSidebar"] * { color: #7ecfff !important; }
[data-testid="stSidebar"] h2 { border-left: 3px solid #00ffff; }

[data-testid="stMetric"] {
    background: linear-gradient(135deg, #001a2e 0%, #002a3e 100%) !important;
    border: 1px solid #00ffff33 !important;
    border-radius: 8px !important;
    padding: 1rem !important;
    box-shadow: 0 0 20px #00ffff0d, inset 0 1px 0 #00ffff22;
    position: relative;
    overflow: hidden;
}
[data-testid="stMetric"]::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, transparent, #00ffff, transparent);
}
[data-testid="stMetricLabel"] p {
    color: #00ffff88 !important;
    font-size: 0.65rem !important;
    letter-spacing: 3px !important;
    text-transform: uppercase !important;
    font-family: 'Share Tech Mono', monospace !important;
}
[data-testid="stMetricValue"] {
    color: #00ffff !important;
    font-size: 1.6rem !important;
    font-family: 'Orbitron', monospace !important;
    text-shadow: 0 0 10px #00ffff88 !important;
}
[data-testid="stMetricDelta"] {
    font-size: 0.7rem !important;
    letter-spacing: 1px !important;
}

.alert-card {
    background: linear-gradient(135deg, #1a0008, #2d0010);
    border: 1px solid #ff003344;
    border-left: 4px solid #ff0033;
    border-radius: 6px;
    padding: 12px 16px;
    margin: 6px 0;
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.78rem;
    color: #ffaaaa;
    box-shadow: 0 0 15px #ff003322, inset 0 0 20px #ff000008;
    animation: pulse-red 2s infinite;
}
@keyframes pulse-red {
    0%, 100% { border-left-color: #ff0033; box-shadow: 0 0 15px #ff003322; }
    50%       { border-left-color: #ff6688; box-shadow: 0 0 25px #ff003344; }
}
.normal-card {
    background: linear-gradient(135deg, #001a0a, #002d14);
    border: 1px solid #00ff6633;
    border-left: 4px solid #00ff66;
    border-radius: 6px;
    padding: 12px 16px;
    margin: 6px 0;
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.78rem;
    color: #aaffcc;
    box-shadow: 0 0 15px #00ff6622;
}
.header-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: linear-gradient(90deg, #001a2e, #002a3e, #001a2e);
    border: 1px solid #00ffff22;
    border-radius: 8px;
    padding: 8px 20px;
    margin-bottom: 1rem;
    font-size: 0.72rem;
    color: #00ffff88;
    letter-spacing: 2px;
}
.status-dot-ok  { display:inline-block; width:8px; height:8px; border-radius:50%;
                  background:#00ff66; box-shadow:0 0 8px #00ff66; margin-right:6px; }
.status-dot-err { display:inline-block; width:8px; height:8px; border-radius:50%;
                  background:#ff0033; box-shadow:0 0 8px #ff0033; margin-right:6px;
                  animation: pulse-red 1s infinite; }
.stPlotlyChart {
    border: 1px solid #00ffff1a;
    border-radius: 8px;
    background: #000d1a;
    box-shadow: 0 0 30px #00ffff08;
}
hr { border-color: #00ffff11 !important; }
::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-track { background: #010b14; }
::-webkit-scrollbar-thumb { background: #00ffff33; border-radius: 2px; }
</style>
""", unsafe_allow_html=True)


# ── API helpers ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=REFRESH_S)
def fetch_scores():
    try:
        r = requests.get(f"{API_BASE}/score", timeout=3)
        r.raise_for_status()
        d = r.json()
        return d if isinstance(d, list) else [d]
    except: return []

@st.cache_data(ttl=REFRESH_S)
def fetch_history(slice_id, limit=120):
    try:
        r = requests.get(f"{API_BASE}/metrics/history",
                         params={"slice_id": slice_id, "limit": limit}, timeout=3)
        r.raise_for_status()
        df = pd.DataFrame(r.json())
        if df.empty: return df
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        return df.sort_values("timestamp")
    except: return pd.DataFrame()

@st.cache_data(ttl=10)
def fetch_health():
    try:
        r = requests.get(f"{API_BASE}/health", timeout=2)
        r.raise_for_status()
        return r.json()
    except: return {"status":"unreachable","model_loaded":False,"db_rows":0}


# ── Charts ────────────────────────────────────────────────────────────────────
def color_for(val):
    if val is None: return "#00ffff"
    if val >= 70:   return "#00ff66"
    if val >= 40:   return "#ffcc00"
    return "#ff0033"

def make_gauge(title, value):
    v   = value if value is not None else 0
    col = color_for(value)
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=v,
        title={"text": title,
               "font": {"size": 11, "color": "#00ccff",
                        "family": "Share Tech Mono, monospace"}},
        number={"suffix": "%",
                "font": {"size": 32, "color": col,
                         "family": "Orbitron, monospace"}},
        delta={"reference": 70,
               "increasing": {"color": "#00ff66"},
               "decreasing": {"color": "#ff0033"}},
        gauge={
            "axis": {"range": [0,100],
                     "tickfont": {"color":"#00ffff44","size":8},
                     "tickcolor":"#00ffff22"},
            "bar":  {"color": col, "thickness": 0.2},
            "bgcolor": "#000d1a",
            "bordercolor": "#00ffff22",
            "borderwidth": 1,
            "steps": [
                {"range":[0,40],   "color":"#1a0008"},
                {"range":[40,70],  "color":"#1a1400"},
                {"range":[70,100], "color":"#001a0a"},
            ],
            "threshold":{
                "line":{"color":"#ff0033","width":3},
                "thickness":0.8, "value":40},
        },
    ))
    fig.update_layout(
        paper_bgcolor="#000d1a", plot_bgcolor="#000d1a",
        height=240, margin=dict(t=60,b=10,l=20,r=20),
        font=dict(family="Share Tech Mono, monospace", color="#00ffff"),
    )
    return fig

def make_timeline(dfs):
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
        subplot_titles=["▸ CPU %","▸ MEMORY MB","▸ NETWORK RX KB"],
        vertical_spacing=0.1)
    palette = {"slice-a":"#00ffff","slice-b":"#ff6b6b"}
    for sid, df in dfs.items():
        if df.empty: continue
        c = palette.get(sid,"#80d8ff")
        fig.add_trace(go.Scatter(x=df["timestamp"],y=df["cpu_pct"],
            name=f"{sid} CPU", line=dict(color=c,width=2),
            fill="tozeroy", fillcolor=f"rgba({int(c[1:3],16)},{int(c[3:5],16)},{int(c[5:7],16)},0.05)",
        ), row=1, col=1)
        fig.add_trace(go.Scatter(x=df["timestamp"],y=df["mem_mb"],
            name=f"{sid} MEM", line=dict(color=c,width=1.5,dash="dot"),
        ), row=2, col=1)
        fig.add_trace(go.Scatter(x=df["timestamp"],y=df["net_rx_kb"],
            name=f"{sid} RX", line=dict(color=c,width=1.5,dash="dash"),
        ), row=3, col=1)
        if "anomaly_score" in df.columns and df["anomaly_score"].notna().any():
            anom = df[df["anomaly_score"]<0]
            if not anom.empty:
                fig.add_trace(go.Scatter(x=anom["timestamp"],y=anom["cpu_pct"],
                    mode="markers",
                    marker=dict(color="#ff0033",size=12,symbol="x",
                                line=dict(color="#ff0033",width=2)),
                    name=f"{sid} BREACH",
                ), row=1, col=1)
    fig.update_layout(
        paper_bgcolor="#000d1a", plot_bgcolor="#000d1a",
        font=dict(color="#00ffff66",size=10,family="Share Tech Mono"),
        legend=dict(bgcolor="#001a2e",bordercolor="#00ffff22",
                    borderwidth=1,font=dict(size=9,color="#00ffff")),
        height=460, margin=dict(t=40,b=20,l=50,r=20),
    )
    for i in range(1,4):
        fig.update_yaxes(gridcolor="#00ffff0d",zerolinecolor="#00ffff1a",
                         tickfont=dict(color="#00ffff55"),row=i,col=1)
        fig.update_xaxes(gridcolor="#00ffff0d",
                         tickfont=dict(color="#00ffff55"),row=i,col=1)
    for ann in fig.layout.annotations:
        ann.font.color="#00ccff99"
        ann.font.size=9
        ann.font.family="Share Tech Mono, monospace"
    return fig

def render_alerts(dfs):
    events=[]
    for sid, df in dfs.items():
        if df.empty or "anomaly_score" not in df.columns: continue
        if not df["anomaly_score"].notna().any(): continue
        anom = df.tail(60)
        anom = anom[anom["anomaly_score"]<0]
        for _, row in anom.iterrows():
            conf = (min(0.5,max(-0.5,row["anomaly_score"]))+0.5)*100
            events.append({"ts":row["timestamp"],"sid":sid,
                           "score":row["anomaly_score"],"conf":conf,
                           "cpu":row.get("cpu_pct",0),
                           "mem":row.get("mem_mb",0),
                           "rx":row.get("net_rx_kb",0)})
    events.sort(key=lambda e:e["ts"],reverse=True)
    if not events:
        st.markdown('<div class="normal-card">◉ ALL SYSTEMS NOMINAL — No isolation breaches detected across all slices</div>',
                    unsafe_allow_html=True)
        return
    for e in events[:10]:
        ts = e["ts"].strftime("%H:%M:%S") if hasattr(e["ts"],"strftime") else str(e["ts"])
        st.markdown(
            f'<div class="alert-card">'
            f'⚠ ISOLATION BREACH [{e["sid"].upper()}] @ {ts} &nbsp;|&nbsp; '
            f'CONFIDENCE: <strong>{e["conf"]:.1f}%</strong> &nbsp;|&nbsp; '
            f'SCORE: {e["score"]:.4f} &nbsp;|&nbsp; '
            f'CPU: {e["cpu"]:.1f}% &nbsp;|&nbsp; '
            f'MEM: {e["mem"]:.0f}MB &nbsp;|&nbsp; '
            f'RX: {e["rx"]:.2f}KB'
            f'</div>', unsafe_allow_html=True)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    with st.sidebar:
        st.markdown("## ⚙ CONTROLS")
        refresh_rate    = st.slider("Refresh (s)", 2, 30, REFRESH_S)
        history_len     = st.slider("History pts", 20, 200, 120)
        selected_slices = st.multiselect("Active Slices", SLICE_NAMES, default=SLICE_NAMES)
        st.divider()

        health    = fetch_health()
        api_ok    = health.get("status") == "ok"
        model_ok  = health.get("model_loaded", False)
        api_dot   = '<span class="status-dot-ok"></span>'  if api_ok   else '<span class="status-dot-err"></span>'
        model_dot = '<span class="status-dot-ok"></span>'  if model_ok else '<span class="status-dot-err"></span>'

        st.markdown("### SYSTEM STATUS")
        st.markdown(f"{api_dot} API &nbsp; {'ONLINE' if api_ok else 'OFFLINE'}",   unsafe_allow_html=True)
        st.markdown(f"{model_dot} MODEL &nbsp; {'LOADED' if model_ok else 'MISSING'}", unsafe_allow_html=True)
        st.markdown(f"<span style='color:#00ffff66;font-size:0.75rem'>DB ROWS &nbsp; {health.get('db_rows',0)}</span>",
                    unsafe_allow_html=True)
        st.divider()
        st.markdown("### INJECT BREACH")
        st.caption("In simulator terminal:")
        st.code("b", language="bash")
        st.caption("Restore normal:")
        st.code("n", language="bash")

    # Header
    st.markdown("# 🛡 5G NETWORK SLICE ISOLATION MONITOR")
    now = datetime.now().strftime("%Y-%m-%d &nbsp; %H:%M:%S")
    st.markdown(f"""
    <div class="header-bar">
        <span>◉ LIVE TELEMETRY ACTIVE</span>
        <span>AI MODEL: ISOLATION FOREST</span>
        <span>REFRESH: {refresh_rate}s</span>
        <span>{now}</span>
    </div>""", unsafe_allow_html=True)

    # Data
    scores    = fetch_scores()
    score_map = {s["slice_id"]:s for s in scores if "slice_id" in s}
    dfs       = {n: fetch_history(n, limit=history_len) for n in selected_slices}

    # Gauges
    st.markdown("### ◈ ISOLATION CONFIDENCE SCORE")
    gcols = st.columns(len(selected_slices))
    for i, name in enumerate(selected_slices):
        with gcols[i]:
            sd   = score_map.get(name,{})
            conf = sd.get("isolation_confidence")
            st.plotly_chart(make_gauge(f"◈ {name.upper()}", conf),
                            use_container_width=True, key=f"g_{name}")

    # Metric cards
    st.markdown("### ◈ LIVE TELEMETRY")
    ccols = st.columns(len(selected_slices)*4)
    for i, name in enumerate(selected_slices):
        sd   = score_map.get(name,{})
        feat = sd.get("features",{})
        b    = i*4
        anom = sd.get("is_anomaly",False)
        with ccols[b]:
            st.metric(f"{name.upper()} CPU",
                      f"{feat.get('cpu_pct',0):.1f}%",
                      delta="⚠ ANOMALY" if anom else "NOMINAL",
                      delta_color="inverse" if anom else "normal")
        with ccols[b+1]:
            st.metric(f"{name.upper()} MEM", f"{feat.get('mem_mb',0):.1f} MB")
        with ccols[b+2]:
            st.metric(f"{name.upper()} RX",  f"{feat.get('net_rx_kb',0):.2f} KB/s")
        with ccols[b+3]:
            st.metric(f"{name.upper()} TX",  f"{feat.get('net_tx_kb',0):.2f} KB/s")

    st.markdown("---")

    # Timeline
    st.markdown("### ◈ METRIC TIMELINE")
    if any(not df.empty for df in dfs.values()):
        st.plotly_chart(make_timeline(dfs), use_container_width=True)
    else:
        st.info("▸ Awaiting telemetry data...")

    # Alerts
    st.markdown("### ◈ ALERT FEED")
    render_alerts(dfs)

    time.sleep(refresh_rate)
    st.rerun()

if __name__ == "__main__":
    main()