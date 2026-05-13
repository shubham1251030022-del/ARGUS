"""
ARGUS — File 11: webapp/app.py  [ENHANCED v2]
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

Enhanced dashboard with:
  - Start Exam / Stop Exam buttons
  - ArUco Scan trigger button
  - Auto-refresh when detection stops
  - Arduino alert indicator
  - Session status management

Run: py -3.11 webapp/app.py
"""

import os
import sys
import json
import threading
import time
import subprocess
import serial
import serial.tools.list_ports

from flask import (Flask, Response, jsonify, request,
                   render_template_string, send_from_directory)

_THIS_DIR    = os.path.dirname(os.path.abspath(__file__))
_ROOT        = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _ROOT)

from webapp.alert_logger import AlertLogger

SNAPSHOT_DIR = os.path.join(_ROOT, "snapshots")

app    = Flask(__name__)
logger = AlertLogger()

# ── Live state ────────────────────────────────────────────────────────────────
_state_lock = threading.Lock()
_live_state = {
    "benches"    : {},
    "frame_count": 0,
    "fps"        : 0,
    "running"    : False,
    "last_update": 0,
    "exam_active": False,
    "exam_start" : None
}

_frame_lock   = threading.Lock()
_latest_frame = None

# ── Arduino ───────────────────────────────────────────────────────────────────
_arduino      = None
_arduino_port = None

def connect_arduino():
    """Auto-detect and connect Arduino."""
    global _arduino, _arduino_port
    ports = serial.tools.list_ports.comports()
    for p in ports:
        if "Arduino" in p.description or "CH340" in p.description \
                or "USB Serial" in p.description:
            try:
                _arduino      = serial.Serial(p.device, 9600, timeout=1)
                _arduino_port = p.device
                print(f"[ARDUINO] Connected on {p.device}")
                return True
            except Exception as e:
                print(f"[ARDUINO] Failed {p.device}: {e}")
    print("[ARDUINO] Not found — buzzer alerts disabled")
    return False

def trigger_arduino_alert(bench_id: str):
    """Send alert signal to Arduino buzzer."""
    global _arduino
    if _arduino and _arduino.is_open:
        try:
            _arduino.write(b'A')   # 'A' = Alert signal
            print(f"[ARDUINO] Alert triggered for {bench_id}")
        except Exception as e:
            print(f"[ARDUINO] Send error: {e}")
            _arduino = None


# ── Video stream ──────────────────────────────────────────────────────────────

def _generate_stream():
    while True:
        with _frame_lock:
            frame = _latest_frame
        if frame:
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n\r\n")
            time.sleep(0.05)
        else:
            time.sleep(0.1)

@app.route("/video_feed")
def video_feed():
    return Response(_generate_stream(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/api/frame", methods=["POST"])
def push_frame():
    global _latest_frame
    with _frame_lock:
        _latest_frame = request.data
    return jsonify({"ok": True})


# ── Status API ────────────────────────────────────────────────────────────────

@app.route("/api/status", methods=["GET"])
def get_status():
    with _state_lock:
        return jsonify(_live_state)

@app.route("/api/status", methods=["POST"])
def push_status():
    data = request.get_json(silent=True) or {}
    with _state_lock:
        _live_state.update(data)
        _live_state["last_update"] = time.time()

    # Trigger Arduino alert if any bench just hit threshold
    benches = data.get("benches", {})
    for bid, info in benches.items():
        if info.get("score", 0) >= 30:
            trigger_arduino_alert(bid)

    return jsonify({"ok": True})


# ── Exam control ──────────────────────────────────────────────────────────────

@app.route("/api/exam/start", methods=["POST"])
def start_exam():
    with _state_lock:
        _live_state["exam_active"] = True
        _live_state["exam_start"]  = time.strftime("%H:%M:%S")
    logger.clear_session()
    return jsonify({"ok": True, "started": _live_state["exam_start"]})

@app.route("/api/exam/stop", methods=["POST"])
def stop_exam():
    with _state_lock:
        _live_state["exam_active"] = False
        _live_state["running"]     = False
    return jsonify({"ok": True, "summary": logger.get_summary()})


# ── Alerts API ────────────────────────────────────────────────────────────────

@app.route("/api/alerts",          methods=["GET"])
def get_alerts():
    return jsonify(logger.get_all())

@app.route("/api/alerts/recent",   methods=["GET"])
def get_recent():
    return jsonify(logger.get_recent(request.args.get("n", 10, type=int)))

@app.route("/api/alert",           methods=["POST"])
def post_alert():
    data  = request.get_json(silent=True) or {}
    alert = logger.log_alert(
        bench         = data.get("bench", ""),
        student_name  = data.get("student_name", "Unknown"),
        roll_number   = data.get("roll_number", ""),
        score         = data.get("score", 0),
        ml_confidence = data.get("ml_confidence", 0.0),
        flags         = data.get("flags", {}),
        snapshot_path = data.get("snapshot_path", "")
    )
    trigger_arduino_alert(data.get("bench", ""))
    return jsonify(alert), 201

@app.route("/api/summary",         methods=["GET"])
def get_summary():
    return jsonify(logger.get_summary())

@app.route("/api/reviewed/<int:alert_id>", methods=["POST"])
def mark_reviewed(alert_id):
    return jsonify({"ok": logger.mark_reviewed(alert_id), "id": alert_id})

@app.route("/api/clear",           methods=["POST"])
def clear_session():
    logger.clear_session()
    with _state_lock:
        _live_state["benches"]     = {}
        _live_state["frame_count"] = 0
    return jsonify({"ok": True})

@app.route("/api/arduino/status",  methods=["GET"])
def arduino_status():
    return jsonify({
        "connected": _arduino is not None and _arduino.is_open,
        "port"     : _arduino_port
    })

@app.route("/api/arduino/connect", methods=["POST"])
def reconnect_arduino():
    connect_arduino()
    return jsonify({
        "connected": _arduino is not None,
        "port"     : _arduino_port
    })

@app.route("/snapshots/<path:filename>")
def serve_snapshot(filename):
    return send_from_directory(SNAPSHOT_DIR, filename)


# ════════════════════════════════════════════════════════════════════════════
# DASHBOARD HTML
# ════════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ARGUS — Exam Monitor</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0d1117; color:#e6edf3; font-family:'Segoe UI',sans-serif; }

.header {
  background:#161b22; border-bottom:1px solid #30363d;
  padding:12px 20px; display:flex; align-items:center;
  justify-content:space-between; flex-wrap:wrap; gap:8px;
}
.header h1 { font-size:1.2rem; color:#58a6ff; letter-spacing:2px; }
.header-right { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.meta { font-size:0.75rem; color:#8b949e; }

/* Controls */
.ctrl-bar {
  background:#161b22; border-bottom:1px solid #21262d;
  padding:10px 20px; display:flex; align-items:center; gap:10px; flex-wrap:wrap;
}
.btn { padding:7px 16px; border-radius:6px; border:none; cursor:pointer;
  font-size:0.78rem; font-weight:600; letter-spacing:0.5px; transition:all 0.2s; }
.btn-start  { background:#238636; color:#fff; }
.btn-start:hover  { background:#2ea043; }
.btn-stop   { background:#da3633; color:#fff; }
.btn-stop:hover   { background:#f85149; }
.btn-aruco  { background:#1f6feb; color:#fff; }
.btn-aruco:hover  { background:#388bfd; }
.btn-clear  { background:#21262d; color:#8b949e; border:1px solid #30363d; }
.btn-clear:hover  { border-color:#8b949e; color:#e6edf3; }
.btn:disabled { opacity:0.4; cursor:not-allowed; }

.exam-badge {
  padding:4px 12px; border-radius:12px; font-size:0.72rem; font-weight:700;
  letter-spacing:1px;
}
.exam-active   { background:#0d2614; color:#3fb950; border:1px solid #3fb950; }
.exam-inactive { background:#1a0d0d; color:#8b949e; border:1px solid #30363d; }

.arduino-dot { display:inline-block; width:8px; height:8px;
  border-radius:50%; margin-right:4px; }
.ard-on  { background:#3fb950; }
.ard-off { background:#484f58; }

/* Layout */
.container { display:grid; grid-template-columns:1fr 380px;
  gap:14px; padding:14px; height:calc(100vh - 103px); }
.left  { display:flex; flex-direction:column; gap:14px; overflow:hidden; }
.right { display:flex; flex-direction:column; gap:14px; overflow:hidden; }

/* Cards */
.card { background:#161b22; border:1px solid #30363d;
  border-radius:8px; padding:14px; }
.card-title { font-size:0.7rem; color:#8b949e; text-transform:uppercase;
  letter-spacing:1px; margin-bottom:10px; display:flex;
  justify-content:space-between; align-items:center; }

/* Feed */
.feed-wrap { background:#0d1117; border-radius:6px; overflow:hidden;
  flex:1; display:flex; align-items:center; justify-content:center;
  min-height:300px; position:relative; }
.feed-wrap img { width:100%; max-height:400px; object-fit:contain; }
.feed-offline { position:absolute; color:#484f58; font-size:0.85rem;
  text-align:center; }

/* Bench cards */
.bench-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
.bench-card { background:#0d1117; border:1px solid #30363d;
  border-radius:6px; padding:10px; transition:all 0.3s; }
.bench-card.alert   { border-color:#f85149; background:#1a0d0d; }
.bench-card.warning { border-color:#d29922; background:#1a1500; }
.bench-label { font-size:0.68rem; color:#8b949e; margin-bottom:3px; }
.bench-name  { font-size:0.82rem; font-weight:600; margin-bottom:6px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.score-bar-bg { background:#21262d; border-radius:4px; height:5px; }
.score-bar    { height:5px; border-radius:4px; transition:width 0.5s;
  background:#3fb950; }
.score-bar.w  { background:#d29922; }
.score-bar.a  { background:#f85149; }
.score-txt { font-size:0.72rem; color:#8b949e; margin-top:3px; }
.conf-txt  { font-size:0.65rem; color:#484f58; }

/* Summary */
.summary-row { display:flex; gap:12px; }
.stat { flex:1; text-align:center; }
.stat-val   { font-size:1.3rem; font-weight:700; color:#58a6ff; }
.stat-label { font-size:0.65rem; color:#8b949e; text-transform:uppercase; }

/* Alerts table */
.alerts-scroll { overflow-y:auto; flex:1; max-height:360px; }
table { width:100%; border-collapse:collapse; font-size:0.76rem; }
th { text-align:left; padding:5px 7px; color:#8b949e; font-weight:500;
  border-bottom:1px solid #21262d; position:sticky; top:0; background:#161b22; }
td { padding:5px 7px; border-bottom:1px solid #21262d; }
tr:hover td { background:#21262d; }
.badge { display:inline-block; padding:2px 6px; border-radius:10px;
  font-size:0.65rem; font-weight:700; }
.red    { background:#2d1117; color:#f85149; border:1px solid #f85149; }
.yellow { background:#1c1a00; color:#d29922; border:1px solid #d29922; }
.green  { background:#0d2614; color:#3fb950; border:1px solid #3fb950; }
.btn-sm { background:none; border:1px solid #30363d; color:#8b949e;
  padding:2px 7px; border-radius:4px; cursor:pointer; font-size:0.65rem; }
.btn-sm:hover { border-color:#58a6ff; color:#58a6ff; }
.reviewed-tick { color:#3fb950; font-size:0.7rem; }

/* Status dot */
.status-dot { display:inline-block; width:9px; height:9px;
  border-radius:50%; background:#484f58; margin-right:5px; }
.status-dot.on { background:#3fb950; animation:pulse 1.5s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

/* Stopped overlay */
.stopped-banner {
  display:none; background:#1a0d0d; border:1px solid #f85149;
  border-radius:6px; padding:8px 14px; color:#f85149;
  font-size:0.78rem; font-weight:600; text-align:center;
}
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <h1>⬡ ARGUS <span style="color:#8b949e;font-size:0.8rem;">EXAM MONITOR</span></h1>
  <div class="header-right">
    <span class="meta">
      <span class="status-dot" id="dot"></span>
      <span id="status-text">Standby</span>
      &nbsp;<span id="fps-tag" style="color:#3fb950;font-size:0.68rem;"></span>
    </span>
    <span class="meta">
      <span class="arduino-dot" id="ard-dot"></span>
      <span id="ard-text" style="font-size:0.68rem;">Arduino: —</span>
    </span>
    <span class="meta" id="clock"></span>
  </div>
</div>

<!-- Teacher Controls -->
<div class="ctrl-bar">
  <button class="btn btn-start"  id="btn-start"  onclick="startExam()">▶ Start Exam</button>
  <button class="btn btn-stop"   id="btn-stop"   onclick="stopExam()"  disabled>■ Stop Exam</button>
  <button class="btn btn-aruco"  id="btn-aruco"  onclick="scanAruco()">⬡ Scan ArUco Zones</button>
  <button class="btn btn-clear"                  onclick="clearSession()">✕ Clear Alerts</button>
  <span   class="exam-badge exam-inactive" id="exam-badge">EXAM NOT STARTED</span>
  <span   style="font-size:0.7rem;color:#8b949e;" id="exam-time"></span>
</div>

<div class="container">

  <!-- LEFT -->
  <div class="left">

    <!-- Stopped banner -->
    <div class="stopped-banner" id="stopped-banner">
      ⚠ Detection stopped — restart main.py to resume monitoring
    </div>

    <!-- Camera feed -->
    <div class="card" style="flex:1;">
      <div class="card-title">📷 Live Camera Feed</div>
      <div class="feed-wrap">
        <img id="live-feed" src="/video_feed"
             onerror="showFeedOffline()"
             onload="hideFeedOffline()">
        <div id="feed-offline" class="feed-offline" style="display:none;">
          Camera offline — start detection
        </div>
      </div>
    </div>

    <!-- Bench status -->
    <div class="card">
      <div class="card-title">🪑 Bench Status</div>
      <div class="bench-grid" id="bench-grid">
        <div class="bench-card" id="bench-B1">
          <div class="bench-label">B1</div>
          <div class="bench-name">—</div>
          <div class="score-bar-bg"><div class="score-bar" style="width:0%"></div></div>
          <div class="score-txt">Score: 0 / 30</div>
          <div class="conf-txt">Conf: —</div>
        </div>
        <div class="bench-card" id="bench-B2">
          <div class="bench-label">B2</div>
          <div class="bench-name">—</div>
          <div class="score-bar-bg"><div class="score-bar" style="width:0%"></div></div>
          <div class="score-txt">Score: 0 / 30</div>
          <div class="conf-txt">Conf: —</div>
        </div>
        <div class="bench-card" id="bench-B3">
          <div class="bench-label">B3</div>
          <div class="bench-name">—</div>
          <div class="score-bar-bg"><div class="score-bar" style="width:0%"></div></div>
          <div class="score-txt">Score: 0 / 30</div>
          <div class="conf-txt">Conf: —</div>
        </div>
      </div>
    </div>

  </div>

  <!-- RIGHT -->
  <div class="right">

    <!-- Summary -->
    <div class="card">
      <div class="card-title">📊 Session Summary</div>
      <div class="summary-row">
        <div class="stat"><div class="stat-val" id="s-total">0</div>
          <div class="stat-label">Alerts</div></div>
        <div class="stat"><div class="stat-val" id="s-students">0</div>
          <div class="stat-label">Students</div></div>
        <div class="stat"><div class="stat-val" id="s-high">0</div>
          <div class="stat-label">Peak</div></div>
        <div class="stat"><div class="stat-val" id="s-benches">0</div>
          <div class="stat-label">Benches</div></div>
      </div>
    </div>

    <!-- Alert log -->
    <div class="card" style="flex:1;display:flex;flex-direction:column;">
      <div class="card-title">
        🚨 Alert Log
        <span style="font-size:0.65rem;color:#484f58;">Auto-refreshes every 2s</span>
      </div>
      <div class="alerts-scroll">
        <table>
          <thead>
            <tr><th>#</th><th>Time</th><th>Bench</th>
                <th>Student</th><th>Score</th><th>Conf</th><th></th></tr>
          </thead>
          <tbody id="alert-tbody">
            <tr><td colspan="7" style="text-align:center;color:#484f58;padding:16px;">
              No alerts yet
            </td></tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>
</div>

<script>
// ── Clock ──
function updateClock() {
  const n = new Date();
  document.getElementById('clock').textContent =
    n.toLocaleTimeString('en-IN', {hour12:false});
}
setInterval(updateClock, 1000); updateClock();

// ── Feed offline handlers ──
function showFeedOffline() {
  document.getElementById('live-feed').style.display = 'none';
  document.getElementById('feed-offline').style.display = 'block';
}
function hideFeedOffline() {
  document.getElementById('live-feed').style.display = 'block';
  document.getElementById('feed-offline').style.display = 'none';
}

// ── Fetch status ──
let wasRunning = false;
async function fetchStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    const age  = Date.now()/1000 - (d.last_update||0);
    const live = d.running && age < 4;

    // Detection running indicator
    document.getElementById('dot').className = 'status-dot' + (live?' on':'');
    document.getElementById('status-text').textContent =
      live ? 'Detection Running' : 'Standby';
    document.getElementById('fps-tag').textContent =
      d.fps ? d.fps + ' FPS' : '';

    // Stopped banner
    document.getElementById('stopped-banner').style.display =
      (!live && wasRunning) ? 'block' : 'none';
    wasRunning = live;

    // Bench cards
    const benches = d.benches || {};
    ['B1','B2','B3'].forEach(b => {
      const info  = benches[b] || {};
      const score = info.score || 0;
      const conf  = info.ml_confidence || 0;
      const name  = info.student_name  || '—';
      const pct   = Math.min(100, (score/30)*100);
      const card  = document.getElementById('bench-' + b);
      if (!card) return;
      card.querySelector('.bench-name').textContent = name;
      card.querySelector('.score-txt').textContent  = `Score: ${score} / 30`;
      card.querySelector('.conf-txt').textContent   =
        conf ? `Conf: ${(conf*100).toFixed(0)}%` : 'Conf: —';
      const bar = card.querySelector('.score-bar');
      bar.style.width = pct + '%';
      bar.className = 'score-bar' + (score>=30?' a':score>=20?' w':'');
      card.className = 'bench-card' +
        (score>=30?' alert':score>=20?' warning':'');
    });
  } catch(e) {}
}

// ── Fetch alerts ──
async function fetchAlerts() {
  try {
    const [ar, sr] = await Promise.all([
      fetch('/api/alerts'), fetch('/api/summary')
    ]);
    const alerts  = await ar.json();
    const summary = await sr.json();

    document.getElementById('s-total').textContent    = summary.total_alerts    || 0;
    document.getElementById('s-students').textContent = summary.unique_students || 0;
    document.getElementById('s-high').textContent     = summary.highest_score   || 0;
    document.getElementById('s-benches').textContent  = (summary.benches_flagged||[]).length;

    const tbody = document.getElementById('alert-tbody');
    if (!alerts.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#484f58;padding:16px;">No alerts yet</td></tr>';
      return;
    }
    tbody.innerHTML = alerts.slice(0,50).map(a => {
      const sc = a.score;
      const bc = sc>=40?'red':sc>=30?'yellow':'green';
      const rv = a.reviewed
        ? '<span class="reviewed-tick">✓</span>'
        : `<button class="btn-sm" onclick="markReviewed(${a.id})">Review</button>`;
      return `<tr>
        <td>#${a.id}</td><td>${a.time}</td><td><b>${a.bench}</b></td>
        <td style="max-width:80px;overflow:hidden;text-overflow:ellipsis;">${a.student_name}</td>
        <td><span class="badge ${bc}">${sc}</span></td>
        <td>${(a.ml_confidence*100).toFixed(0)}%</td>
        <td>${rv}</td>
      </tr>`;
    }).join('');
  } catch(e) {}
}

// ── Arduino status ──
async function fetchArduino() {
  try {
    const r = await fetch('/api/arduino/status');
    const d = await r.json();
    document.getElementById('ard-dot').className =
      'arduino-dot ' + (d.connected ? 'ard-on' : 'ard-off');
    document.getElementById('ard-text').textContent =
      d.connected ? `Arduino: ${d.port}` : 'Arduino: Not connected';
  } catch(e) {}
}

// ── Teacher controls ──
async function startExam() {
  await fetch('/api/exam/start', {method:'POST'});
  document.getElementById('btn-start').disabled = true;
  document.getElementById('btn-stop').disabled  = false;
  document.getElementById('exam-badge').textContent = '● EXAM IN PROGRESS';
  document.getElementById('exam-badge').className   = 'exam-badge exam-active';
  document.getElementById('exam-time').textContent  =
    'Started: ' + new Date().toLocaleTimeString('en-IN',{hour12:false});
  fetchAlerts();
}

async function stopExam() {
  const r = await fetch('/api/exam/stop', {method:'POST'});
  const d = await r.json();
  document.getElementById('btn-start').disabled = false;
  document.getElementById('btn-stop').disabled  = true;
  document.getElementById('exam-badge').textContent = 'EXAM ENDED';
  document.getElementById('exam-badge').className   = 'exam-badge exam-inactive';
  alert(`Exam ended.\\nTotal alerts: ${d.summary?.total_alerts||0}\\nStudents flagged: ${d.summary?.unique_students||0}`);
  fetchAlerts();
}

async function scanAruco() {
  const btn = document.getElementById('btn-aruco');
  btn.disabled = true;
  btn.textContent = '⬡ Scanning...';
  // Opens ArUco scanner in new terminal (user must run manually)
  alert('Run this command in a new terminal:\\n\\npy -3.11 detection/aruco_scanner.py\\n\\nPoint camera at ArUco markers and press S to save zones.');
  btn.disabled = false;
  btn.textContent = '⬡ Scan ArUco Zones';
}

async function markReviewed(id) {
  await fetch('/api/reviewed/'+id, {method:'POST'});
  fetchAlerts();
}

async function clearSession() {
  if (!confirm('Clear all alerts for this session?')) return;
  await fetch('/api/clear', {method:'POST'});
  fetchAlerts(); fetchStatus();
}

// ── Poll ──
fetchStatus(); fetchAlerts(); fetchArduino();
setInterval(fetchStatus,  1000);
setInterval(fetchAlerts,  2000);
setInterval(fetchArduino, 5000);
</script>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


if __name__ == "__main__":
    print("=" * 55)
    print("  ARGUS — File 11: Enhanced Dashboard v2")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("=" * 55)
    print("\n  Dashboard : http://localhost:5000")
    print("  Controls  : Start/Stop Exam, ArUco Scan, Clear")
    print("\n  Press Ctrl+C to stop.\n")

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)

    # Try Arduino connection at startup
    try:
        connect_arduino()
    except Exception:
        print("[ARDUINO] pyserial not installed — run: pip install pyserial")

    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
