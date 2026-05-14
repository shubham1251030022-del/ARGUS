"""
ARGUS — File 11: webapp/app.py [FULL AUTO v3]
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

ONE-CLICK SYSTEM:
  Start Exam  → auto-launches detection/main.py
  Stop Exam   → kills detection process
  Scan ArUco  → auto-launches aruco_scanner.py

Teacher only needs to:
  1. Run: py -3.11 webapp/app.py
  2. Open: http://localhost:5000
  3. Click buttons

Run: py -3.11 webapp/app.py  (from ARGUS root)
"""

import os
import sys
import json
import threading
import time
import subprocess

from flask import (Flask, Response, jsonify, request,
                   render_template_string, send_from_directory)

_THIS_DIR    = os.path.dirname(os.path.abspath(__file__))
_ROOT        = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _ROOT)

from webapp.alert_logger import AlertLogger

SNAPSHOT_DIR   = os.path.join(_ROOT, "snapshots")
DETECTION_SCRIPT = os.path.join(_ROOT, "detection", "main.py")
ARUCO_SCRIPT     = os.path.join(_ROOT, "detection", "aruco_scanner.py")
PYTHON_CMD       = "py -3.11"

app    = Flask(__name__)
logger = AlertLogger()

# ── Process management ────────────────────────────────────────────────────────
_detection_process = None
_aruco_process     = None
_process_lock      = threading.Lock()

# ── Live state ────────────────────────────────────────────────────────────────
_state_lock = threading.Lock()
_live_state = {
    "benches"     : {},
    "frame_count" : 0,
    "fps"         : 0,
    "running"     : False,
    "last_update" : 0,
    "exam_active" : False,
    "exam_start"  : None,
    "aruco_scanning": False,
    "aruco_done"  : False
}

_frame_lock   = threading.Lock()
_latest_frame = None


# ════════════════════════════════════════════════════════════════════════════
# PROCESS MANAGEMENT
# ════════════════════════════════════════════════════════════════════════════

def is_detection_running():
    global _detection_process
    with _process_lock:
        if _detection_process is None:
            return False
        return _detection_process.poll() is None

def start_detection(camera_index=0):
    """Launch detection/main.py as background process."""
    global _detection_process
    with _process_lock:
        # Kill existing if running
        if _detection_process and _detection_process.poll() is None:
            _detection_process.terminate()
            _detection_process.wait(timeout=3)

        cmd = f"py -3.11 {DETECTION_SCRIPT}"
        if camera_index == 0:
            cmd += " --no-cam"

        print(f"[APP] Launching: {cmd}")
        _detection_process = subprocess.Popen(
            cmd,
            shell=True,
            cwd=_ROOT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
        print(f"[APP] Detection PID: {_detection_process.pid}")
        return _detection_process.pid

def stop_detection():
    """Kill detection process."""
    global _detection_process
    with _process_lock:
        if _detection_process and _detection_process.poll() is None:
            try:
                _detection_process.terminate()
                _detection_process.wait(timeout=5)
                print("[APP] Detection stopped.")
            except Exception as e:
                print(f"[APP] Stop error: {e}")
                try:
                    _detection_process.kill()
                except Exception:
                    pass
            _detection_process = None
            return True
        return False

def start_aruco_scan():
    """Launch aruco_scanner.py and monitor completion."""
    global _aruco_process

    def _run_aruco():
        global _aruco_process
        with _state_lock:
            _live_state["aruco_scanning"] = True
            _live_state["aruco_done"]     = False

        cmd = f"py -3.11 {ARUCO_SCRIPT}"
        print(f"[APP] Launching ArUco: {cmd}")
        _aruco_process = subprocess.Popen(
            cmd, shell=True, cwd=_ROOT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
        _aruco_process.wait()

        with _state_lock:
            _live_state["aruco_scanning"] = False
            _live_state["aruco_done"]     = True
        print("[APP] ArUco scan complete.")

    t = threading.Thread(target=_run_aruco, daemon=True)
    t.start()


# ════════════════════════════════════════════════════════════════════════════
# VIDEO STREAM
# ════════════════════════════════════════════════════════════════════════════

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


# ════════════════════════════════════════════════════════════════════════════
# STATUS API
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/status", methods=["GET"])
def get_status():
    with _state_lock:
        state = dict(_live_state)
    state["detection_running"] = is_detection_running()
    return jsonify(state)

@app.route("/api/status", methods=["POST"])
def push_status():
    data = request.get_json(silent=True) or {}
    with _state_lock:
        _live_state.update(data)
        _live_state["last_update"] = time.time()
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════════════════════════
# EXAM CONTROL API
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/exam/start", methods=["POST"])
def start_exam():
    data = request.get_json(silent=True) or {}
    cam  = data.get("camera_index", 0)

    # Clear previous session
    logger.clear_session()

    # Launch detection
    pid = start_detection(camera_index=cam)

    with _state_lock:
        _live_state["exam_active"] = True
        _live_state["exam_start"]  = time.strftime("%H:%M:%S")
        _live_state["running"]     = True

    return jsonify({
        "ok"     : True,
        "pid"    : pid,
        "started": _live_state["exam_start"]
    })

@app.route("/api/exam/stop", methods=["POST"])
def stop_exam():
    stopped = stop_detection()

    with _state_lock:
        _live_state["exam_active"] = False
        _live_state["running"]     = False
        _live_state["benches"]     = {}

    summary = logger.get_summary()
    return jsonify({"ok": True, "stopped": stopped, "summary": summary})

@app.route("/api/exam/status", methods=["GET"])
def exam_status():
    return jsonify({
        "detection_running": is_detection_running(),
        "exam_active"      : _live_state.get("exam_active", False),
        "exam_start"       : _live_state.get("exam_start")
    })

@app.route("/api/aruco/scan", methods=["POST"])
def aruco_scan():
    if _live_state.get("aruco_scanning"):
        return jsonify({"ok": False, "msg": "Scan already in progress"})
    start_aruco_scan()
    return jsonify({"ok": True, "msg": "ArUco scan started"})

@app.route("/api/aruco/status", methods=["GET"])
def aruco_status():
    return jsonify({
        "scanning": _live_state.get("aruco_scanning", False),
        "done"    : _live_state.get("aruco_done", False)
    })


# ════════════════════════════════════════════════════════════════════════════
# ALERTS API
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/alerts",        methods=["GET"])
def get_alerts():
    return jsonify(logger.get_all())

@app.route("/api/alert",         methods=["POST"])
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
    return jsonify(alert), 201

@app.route("/api/summary",       methods=["GET"])
def get_summary():
    return jsonify(logger.get_summary())

@app.route("/api/reviewed/<int:alert_id>", methods=["POST"])
def mark_reviewed(alert_id):
    return jsonify({"ok": logger.mark_reviewed(alert_id), "id": alert_id})

@app.route("/api/clear",         methods=["POST"])
def clear_session():
    logger.clear_session()
    with _state_lock:
        _live_state["benches"]     = {}
        _live_state["frame_count"] = 0
    return jsonify({"ok": True})

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

.ctrl-bar {
  background:#161b22; border-bottom:1px solid #21262d;
  padding:10px 20px; display:flex; align-items:center;
  gap:10px; flex-wrap:wrap;
}
.btn { padding:8px 18px; border-radius:6px; border:none;
  cursor:pointer; font-size:0.8rem; font-weight:600;
  letter-spacing:0.5px; transition:all 0.2s; }
.btn-start  { background:#238636; color:#fff; }
.btn-start:hover:not(:disabled)  { background:#2ea043; }
.btn-stop   { background:#da3633; color:#fff; }
.btn-stop:hover:not(:disabled)   { background:#f85149; }
.btn-aruco  { background:#1f6feb; color:#fff; }
.btn-aruco:hover:not(:disabled)  { background:#388bfd; }
.btn-clear  { background:#21262d; color:#8b949e;
  border:1px solid #30363d; }
.btn-clear:hover { border-color:#8b949e; color:#e6edf3; }
.btn:disabled { opacity:0.4; cursor:not-allowed; }

.exam-badge { padding:4px 12px; border-radius:12px;
  font-size:0.72rem; font-weight:700; letter-spacing:1px; }
.exam-active   { background:#0d2614; color:#3fb950;
  border:1px solid #3fb950; }
.exam-inactive { background:#1a0d0d; color:#8b949e;
  border:1px solid #30363d; }
.exam-scanning { background:#0d1a2d; color:#58a6ff;
  border:1px solid #58a6ff; animation:pulse 1s infinite; }

@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }

.container { display:grid; grid-template-columns:1fr 380px;
  gap:14px; padding:14px; height:calc(100vh - 107px); }
.left  { display:flex; flex-direction:column; gap:14px; overflow:hidden; }
.right { display:flex; flex-direction:column; gap:14px; overflow:hidden; }

.card { background:#161b22; border:1px solid #30363d;
  border-radius:8px; padding:14px; }
.card-title { font-size:0.7rem; color:#8b949e;
  text-transform:uppercase; letter-spacing:1px;
  margin-bottom:10px; display:flex;
  justify-content:space-between; align-items:center; }

.feed-wrap { background:#0d1117; border-radius:6px;
  overflow:hidden; flex:1; display:flex;
  align-items:center; justify-content:center;
  min-height:280px; position:relative; }
.feed-wrap img { width:100%; max-height:380px; object-fit:contain; }
.feed-offline { position:absolute; color:#484f58;
  font-size:0.85rem; text-align:center; padding:20px; }

.stopped-banner { display:none; background:#1a0d0d;
  border:1px solid #f85149; border-radius:6px;
  padding:8px 14px; color:#f85149; font-size:0.78rem;
  font-weight:600; text-align:center; }

.bench-grid { display:grid;
  grid-template-columns:repeat(3,1fr); gap:10px; }
.bench-card { background:#0d1117; border:1px solid #30363d;
  border-radius:6px; padding:10px; transition:all 0.3s; }
.bench-card.alert   { border-color:#f85149; background:#1a0d0d; }
.bench-card.warning { border-color:#d29922; background:#1a1500; }
.bench-label { font-size:0.68rem; color:#8b949e; margin-bottom:3px; }
.bench-name  { font-size:0.82rem; font-weight:600; margin-bottom:6px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.score-bar-bg { background:#21262d; border-radius:4px; height:5px; }
.score-bar { height:5px; border-radius:4px; transition:width 0.5s;
  background:#3fb950; }
.score-bar.w { background:#d29922; }
.score-bar.a { background:#f85149; }
.score-txt { font-size:0.72rem; color:#8b949e; margin-top:3px; }
.conf-txt  { font-size:0.65rem; color:#484f58; }

.summary-row { display:flex; gap:12px; }
.stat { flex:1; text-align:center; }
.stat-val   { font-size:1.3rem; font-weight:700; color:#58a6ff; }
.stat-label { font-size:0.65rem; color:#8b949e; text-transform:uppercase; }

.alerts-scroll { overflow-y:auto; flex:1; max-height:340px; }
table { width:100%; border-collapse:collapse; font-size:0.76rem; }
th { text-align:left; padding:5px 7px; color:#8b949e; font-weight:500;
  border-bottom:1px solid #21262d; position:sticky; top:0;
  background:#161b22; z-index:1; }
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
.tick { color:#3fb950; font-size:0.7rem; }

.status-dot { display:inline-block; width:9px; height:9px;
  border-radius:50%; background:#484f58; margin-right:5px; }
.status-dot.on { background:#3fb950; animation:pulse 1.5s infinite; }
</style>
</head>
<body>

<div class="header">
  <h1>⬡ ARGUS <span style="color:#8b949e;font-size:0.8rem;">EXAM MONITOR</span></h1>
  <div class="header-right">
    <span class="meta">
      <span class="status-dot" id="dot"></span>
      <span id="status-text">Standby</span>
      <span id="fps-tag" style="color:#3fb950;font-size:0.68rem;margin-left:6px;"></span>
    </span>
    <span class="meta" id="clock"></span>
  </div>
</div>

<div class="ctrl-bar">
  <button class="btn btn-start" id="btn-start" onclick="startExam()">
    ▶ Start Exam
  </button>
  <button class="btn btn-stop" id="btn-stop" onclick="stopExam()" disabled>
    ■ Stop Exam
  </button>
  <button class="btn btn-aruco" id="btn-aruco" onclick="scanAruco()">
    ⬡ Scan ArUco Zones
  </button>
  <button class="btn btn-clear" onclick="clearAlerts()">
    ✕ Clear Alerts
  </button>
  <span class="exam-badge exam-inactive" id="exam-badge">
    EXAM NOT STARTED
  </span>
  <span style="font-size:0.7rem;color:#8b949e;" id="exam-time"></span>
</div>

<div class="container">
  <div class="left">

    <div class="stopped-banner" id="stopped-banner">
      ⚠ Detection stopped — click Start Exam to resume
    </div>

    <div class="card" style="flex:1;">
      <div class="card-title">📷 Live Camera Feed</div>
      <div class="feed-wrap">
        <img id="live-feed" src="/video_feed"
             onerror="showOffline()" onload="hideOffline()">
        <div id="feed-offline" class="feed-offline" style="display:none;">
          No feed — click Start Exam
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">🪑 Bench Status</div>
      <div class="bench-grid" id="bench-grid">
        <div class="bench-card" id="bench-B1">
          <div class="bench-label">B1</div>
          <div class="bench-name">—</div>
          <div class="score-bar-bg">
            <div class="score-bar" style="width:0%"></div>
          </div>
          <div class="score-txt">Score: 0</div>
          <div class="conf-txt">Conf: —</div>
        </div>
        <div class="bench-card" id="bench-B2">
          <div class="bench-label">B2</div>
          <div class="bench-name">—</div>
          <div class="score-bar-bg">
            <div class="score-bar" style="width:0%"></div>
          </div>
          <div class="score-txt">Score: 0</div>
          <div class="conf-txt">Conf: —</div>
        </div>
        <div class="bench-card" id="bench-B3">
          <div class="bench-label">B3</div>
          <div class="bench-name">—</div>
          <div class="score-bar-bg">
            <div class="score-bar" style="width:0%"></div>
          </div>
          <div class="score-txt">Score: 0</div>
          <div class="conf-txt">Conf: —</div>
        </div>
      </div>
    </div>

  </div>

  <div class="right">

    <div class="card">
      <div class="card-title">📊 Session Summary</div>
      <div class="summary-row">
        <div class="stat">
          <div class="stat-val" id="s-total">0</div>
          <div class="stat-label">Alerts</div>
        </div>
        <div class="stat">
          <div class="stat-val" id="s-students">0</div>
          <div class="stat-label">Students</div>
        </div>
        <div class="stat">
          <div class="stat-val" id="s-high">0</div>
          <div class="stat-label">Peak</div>
        </div>
        <div class="stat">
          <div class="stat-val" id="s-benches">0</div>
          <div class="stat-label">Benches</div>
        </div>
      </div>
    </div>

    <div class="card" style="flex:1;display:flex;flex-direction:column;">
      <div class="card-title">
        🚨 Alert Log
        <span style="font-size:0.62rem;color:#484f58;">
          Live — auto refresh
        </span>
      </div>
      <div class="alerts-scroll">
        <table>
          <thead>
            <tr>
              <th>#</th><th>Time</th><th>Bench</th>
              <th>Student</th><th>Score</th><th>Conf</th><th></th>
            </tr>
          </thead>
          <tbody id="alert-tbody">
            <tr><td colspan="7"
              style="text-align:center;color:#484f58;padding:20px;">
              No alerts yet
            </td></tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>
</div>

<script>
// ── Clock ──────────────────────────────────────────────────────────────────
function tick() {
  document.getElementById('clock').textContent =
    new Date().toLocaleTimeString('en-IN', {hour12:false});
}
setInterval(tick, 1000); tick();

// ── Feed ───────────────────────────────────────────────────────────────────
function showOffline() {
  document.getElementById('live-feed').style.display = 'none';
  document.getElementById('feed-offline').style.display = 'block';
}
function hideOffline() {
  document.getElementById('live-feed').style.display = 'block';
  document.getElementById('feed-offline').style.display = 'none';
}

// ── Status polling ─────────────────────────────────────────────────────────
let prevRunning = false;
let examStarted = false;

async function fetchStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();

    const age  = Date.now()/1000 - (d.last_update || 0);
    const live = d.detection_running && age < 5;

    // Header dot
    document.getElementById('dot').className =
      'status-dot' + (live ? ' on' : '');
    document.getElementById('status-text').textContent =
      live ? 'Detection Running' : 'Standby';
    document.getElementById('fps-tag').textContent =
      d.fps ? d.fps + ' FPS' : '';

    // Stopped banner
    document.getElementById('stopped-banner').style.display =
      (!live && prevRunning) ? 'block' : 'none';
    prevRunning = live;

    // Bench cards
    const THRESHOLD = 75;
    const benches = d.benches || {};
    ['B1','B2','B3'].forEach(b => {
      const info  = benches[b] || {};
      const score = info.score || 0;
      const conf  = info.ml_confidence || 0;
      const name  = info.student_name  || '—';
      const pct   = Math.min(100, (score / THRESHOLD) * 100);
      const card  = document.getElementById('bench-' + b);
      if (!card) return;
      card.querySelector('.bench-name').textContent = name;
      card.querySelector('.score-txt').textContent =
        `Score: ${score} / ${THRESHOLD}`;
      card.querySelector('.conf-txt').textContent =
        conf ? `Conf: ${(conf*100).toFixed(0)}%` : 'Conf: —';
      const bar = card.querySelector('.score-bar');
      bar.style.width = pct + '%';
      bar.className   = 'score-bar' +
        (score >= THRESHOLD ? ' a' : score >= THRESHOLD*0.6 ? ' w' : '');
      card.className  = 'bench-card' +
        (score >= THRESHOLD ? ' alert' :
         score >= THRESHOLD*0.6 ? ' warning' : '');
    });
  } catch(e) {}
}

// ── Alerts polling ─────────────────────────────────────────────────────────
async function fetchAlerts() {
  try {
    const [ar, sr] = await Promise.all([
      fetch('/api/alerts'), fetch('/api/summary')
    ]);
    const alerts  = await ar.json();
    const summary = await sr.json();

    document.getElementById('s-total').textContent =
      summary.total_alerts    || 0;
    document.getElementById('s-students').textContent =
      summary.unique_students || 0;
    document.getElementById('s-high').textContent =
      summary.highest_score   || 0;
    document.getElementById('s-benches').textContent =
      (summary.benches_flagged || []).length;

    const tbody = document.getElementById('alert-tbody');
    if (!alerts.length) {
      tbody.innerHTML =
        '<tr><td colspan="7" style="text-align:center;' +
        'color:#484f58;padding:16px;">No alerts yet</td></tr>';
      return;
    }
    tbody.innerHTML = alerts.slice(0, 50).map(a => {
      const sc = a.score;
      const bc = sc >= 60 ? 'red' : 'yellow';
      const rv = a.reviewed
        ? '<span class="tick">✓</span>'
        : `<button class="btn-sm"
             onclick="markReviewed(${a.id})">Review</button>`;
      return `<tr>
        <td>#${a.id}</td>
        <td>${a.time}</td>
        <td><b>${a.bench}</b></td>
        <td style="max-width:80px;overflow:hidden;
          text-overflow:ellipsis;">${a.student_name}</td>
        <td><span class="badge ${bc}">${sc}</span></td>
        <td>${(a.ml_confidence*100).toFixed(0)}%</td>
        <td>${rv}</td>
      </tr>`;
    }).join('');
  } catch(e) {}
}

// ── Teacher controls ───────────────────────────────────────────────────────
async function startExam() {
  const btn = document.getElementById('btn-start');
  btn.disabled = true;
  btn.textContent = '⏳ Starting...';

  try {
    const r = await fetch('/api/exam/start', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({camera_index: 0})
    });
    const d = await r.json();

    if (d.ok) {
      examStarted = true;
      document.getElementById('btn-stop').disabled  = false;
      document.getElementById('btn-start').disabled = true;
      document.getElementById('btn-start').textContent = '▶ Start Exam';
      document.getElementById('exam-badge').textContent = '● EXAM IN PROGRESS';
      document.getElementById('exam-badge').className   =
        'exam-badge exam-active';
      document.getElementById('exam-time').textContent  =
        'Started: ' + d.started;
      document.getElementById('stopped-banner').style.display = 'none';
      // Reload feed
      setTimeout(() => {
        const img = document.getElementById('live-feed');
        img.src = '/video_feed?' + Date.now();
      }, 2000);
    }
  } catch(e) {
    btn.disabled = false;
    btn.textContent = '▶ Start Exam';
    alert('Failed to start detection. Check server logs.');
  }
}

async function stopExam() {
  if (!confirm('Stop exam and end detection?')) return;

  const r = await fetch('/api/exam/stop', {method:'POST'});
  const d = await r.json();

  examStarted = false;
  document.getElementById('btn-start').disabled = false;
  document.getElementById('btn-stop').disabled  = true;
  document.getElementById('exam-badge').textContent = 'EXAM ENDED';
  document.getElementById('exam-badge').className   =
    'exam-badge exam-inactive';
  document.getElementById('exam-time').textContent  = '';

  const s = d.summary || {};
  alert(`Exam ended.\n\nTotal alerts: ${s.total_alerts||0}\n` +
        `Students flagged: ${s.unique_students||0}\n` +
        `Peak score: ${s.highest_score||0}`);
  fetchAlerts();
}

async function scanAruco() {
  const btn = document.getElementById('btn-aruco');
  btn.disabled = true;
  btn.textContent = '⬡ Scanning...';
  document.getElementById('exam-badge').textContent = 'SCANNING ZONES...';
  document.getElementById('exam-badge').className   =
    'exam-badge exam-scanning';

  try {
    await fetch('/api/aruco/scan', {method:'POST'});

    // Poll until done
    const poll = setInterval(async () => {
      const r = await fetch('/api/aruco/status');
      const d = await r.json();
      if (!d.scanning) {
        clearInterval(poll);
        btn.disabled = false;
        btn.textContent = '⬡ Scan ArUco Zones';
        document.getElementById('exam-badge').textContent =
          examStarted ? '● EXAM IN PROGRESS' : 'ZONES UPDATED ✓';
        document.getElementById('exam-badge').className =
          examStarted ? 'exam-badge exam-active' : 'exam-badge exam-inactive';
        if (d.done) alert('ArUco scan complete! Zones saved to zones.json');
      }
    }, 1000);

  } catch(e) {
    btn.disabled = false;
    btn.textContent = '⬡ Scan ArUco Zones';
  }
}

async function markReviewed(id) {
  await fetch('/api/reviewed/' + id, {method:'POST'});
  fetchAlerts();
}

async function clearAlerts() {
  if (!confirm('Clear all alerts for this session?')) return;
  await fetch('/api/clear', {method:'POST'});
  fetchAlerts();
}

// ── Poll ───────────────────────────────────────────────────────────────────
fetchStatus(); fetchAlerts();
setInterval(fetchStatus, 1000);
setInterval(fetchAlerts, 2000);
</script>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 55)
    print("  ARGUS — Dashboard [FULL AUTO v3]")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("=" * 55)
    print("\n  Open: http://localhost:5000")
    print("  Teacher controls: Start/Stop/Scan all automated")
    print("\n  Press Ctrl+C to stop.\n")
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
