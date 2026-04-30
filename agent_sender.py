"""
agent_sender.py
Slice A — Activity pattern capture + WebSocket stream.
Endpoints: POST /start, POST /stop, GET /status, WS /stream
"""
import asyncio
import json
import logging
import random
import time
import psutil
import uvicorn
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Configure logging with colors and better format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [slice-a] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

_streaming = False
_clients: set = set()

PATTERN_LIBRARY = {
    "typing": [
        {"type": "typing", "confidence": 87, "details": "115 WPM fast typing"},
        {"type": "typing", "confidence": 74, "details": "92 WPM medium pace"},
        {"type": "typing", "confidence": 91, "details": "Burst: 130 WPM sprint"},
    ],
    "video": [
        {"type": "video", "confidence": 92, "details": "Video decode signature"},
        {"type": "video", "confidence": 85, "details": "H.264 decode burst"},
    ],
    "camera": [
        {"type": "camera", "confidence": 78, "details": "Camera access pattern"},
        {"type": "camera", "confidence": 82, "details": "Frame capture rhythm"},
    ],
}

def capture_patterns() -> list:
    cpu = psutil.cpu_percent(interval=0.1)
    chosen_types = random.sample(list(PATTERN_LIBRARY.keys()), k=random.randint(1, 3))
    patterns = []
    for t in chosen_types:
        p = random.choice(PATTERN_LIBRARY[t]).copy()
        p["confidence"] = min(99, p["confidence"] + int(cpu / 10))
        patterns.append(p)
    return patterns

def log_activity(patterns: list):
    """Print activity simulation to terminal with visual formatting"""
    activity_icons = {
        "typing": "⌨️",
        "video": "🎬",
        "camera": "📷"
    }
    
    print("\n" + "="*70)
    print(f"📊 SLICE A ACTIVITY SIMULATION — {time.strftime('%H:%M:%S')}")
    print("-"*70)
    
    for p in patterns:
        icon = activity_icons.get(p["type"], "🔍")
        activity_name = p["details"]
        confidence = p["confidence"]
        
        # Create visual bar
        bar_length = confidence // 5
        bar = "█" * bar_length + "░" * (20 - bar_length)
        
        print(f"  {icon}  {activity_name:<40} {bar} {confidence}%")
    
    print("="*70 + "\n")

app = FastAPI(title="Slice A Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.post("/start")
def start_stream():
    global _streaming
    _streaming = True
    log.info("✅ BREACH SIGNAL RECEIVED — streaming started")
    log.info("🚀 Slice A will now simulate user activities...")
    print("\n" + "🔴 "*30)
    print("⚠️  NETWORK BREACH DETECTED — ISOLATION COMPROMISED")
    print("📡 Starting activity pattern capture and streaming to Slice B")
    print("🔴 "*30 + "\n")
    return {"status": "streaming"}

@app.post("/stop")
def stop_stream():
    global _streaming
    _streaming = False
    log.info("✅ RESTORE SIGNAL RECEIVED — streaming stopped")
    print("\n" + "🟢 "*30)
    print("✅ ISOLATION RESTORED — network breach resolved")
    print("🛑 Stopped activity pattern streaming")
    print("🟢 "*30 + "\n")
    return {"status": "stopped"}

@app.get("/status")
def status():
    state = "STREAMING" if _streaming else "IDLE"
    state_icon = "🔴" if _streaming else "🟢"
    log.info(f"📋 Status check: {state_icon} {state} | Connected receivers: {len(_clients)}")
    return {"streaming": _streaming, "connected_receivers": len(_clients)}

@app.websocket("/stream")
async def websocket_stream(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    log.info(f"🔗 Receiver connected from {ws.client.host}")
    print(f"\n📡 WebSocket connection established with Slice B")
    print(f"👥 Total connected receivers: {len(_clients)}\n")
    
    try:
        while True:
            if _streaming:
                payload = {
                    "timestamp": time.time(),
                    "slice_id": "slice-a",
                    "patterns": capture_patterns(),
                }
                
                # Log the activity being simulated
                log.info("📤 Streaming activity patterns to Slice B...")
                log_activity(payload["patterns"])
                
                await ws.send_text(json.dumps(payload))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        _clients.discard(ws)
        log.info(f"🔌 Receiver disconnected. Remaining: {len(_clients)}")
        print(f"\n❌ WebSocket client disconnected")
        print(f"👥 Active receivers: {len(_clients)}\n")

if __name__ == "__main__":
    print("\n" + "╔" + "═"*68 + "╗")
    print("║" + " "*15 + "️  SLICE A AGENT STARTING" + " "*25 + "║")
    print("╚" + "═"*68 + "╝")
    print("\n📋 Configuration:")
    print(f"   🌐 Listening on: 0.0.0.0:9000")
    print(f"   📊 WebSocket endpoint: ws://<your-ip>:9000/stream")
    print(f"   🎯 Control endpoints:")
    print(f"      • POST http://localhost:9000/start  — Begin streaming")
    print(f"      • POST http://localhost:9000/stop   — Stop streaming")
    print(f"      • GET  http://localhost:9000/status — Check status")
    print("\n⏳ Waiting for breach signal...")
    print("💡 Tip: Use curl or the mobile control console to trigger\n")
    
    uvicorn.run("agent_sender:app", host="0.0.0.0", port=9000, log_level="info")