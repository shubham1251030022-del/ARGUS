"""
ARGUS — File 11: webapp/app.py
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

Flask dashboard — real-time exam monitoring interface.
Runs offline on localhost:5000.

Routes:
    GET  /                     → dashboard HTML
    GET  /video_feed           → MJPEG live camera stream
    GET  /api/status           → current bench scores + flags (JSON)
    POST /api/status           → main.py pushes live frame data
    GET  /api/alerts           → all logged alerts (JSON)
    POST /api/alert            → main.py logs new alert
    GET  /api/summary          → session summary (JSON)
    POST /api/reviewed/<id>    → mark alert reviewed
    POST /api/clear            → clear session alerts
    GET  /snapshots/<filename> → serve snapshot images

Run: py -3.11 app.py   (from webapp/ folder)
     OR from ARGUS root: py -3.11 webapp/app.py
"""

import os
import sys
import json
import threading
import time
from flask import (Flask, Response, jsonify, request,
                   render_template_string, send_from_directory)

# ── Path setup — works from both webapp/ and ARGUS root ──────────────────────
_THIS_DIR    = os.path.dirname(os.path.abspath(__file__))
_ROOT        = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _ROOT)

from webapp.alert_logger import AlertLogger

SNAPSHOT_DIR = os.path.join(_ROOT, "snapshots")
ALERT_THRESHOLD = 30

# ── Flask app ─────────────────────────────────────────────────────────────────
app    = Flask(__name__)
logger = AlertLogger()

# ── Shared live state (written by main.py via POST /api/status) ───────────────
_state_lock = threading.Lock()
_live_state = {
    "benches"   : {},       # {bench_id: {score, confidence, flags, student}}
    "frame_count": 0,
    "fps"        : 0,
    "running"    : False,
    "last_update": 0
}

# ── Video frame buffer (written by main.py) ───────────────────────────────────
_frame_lock    = threading.Lock()
_latest_frame  = None   # raw JPEG bytes


# ════════════════════════════════════════════════════════════════════════════
# VIDEO STREAM
# ════════════════════════════════════════════════════════════════════════════

def _generate_stream():
    """MJPEG generator — yields latest frame continuously."""
    while True:
        with _frame_lock:
            frame = _latest_frame

        if frame:
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
        else:
            # No frame yet — send a placeholder
            time.sleep(0.05)

        time.sleep(0.033)   # ~30 fps cap


@app.route("/video_feed")
def video_feed():
    return Response(
        _generate_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/api/frame", methods=["POST"])
def push_frame():
    """main.py posts JPEG bytes here each frame."""
    global _latest_frame
    with _frame_lock:
        _latest_frame = request.data
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════════════════════════
# LIVE STATUS API
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/status", methods=["GET"])
def get_status():
    with _state_lock:
        return jsonify(_live_state)


@app.route("/api/status", methods=["POST"])
def push_status():
    """
    main.py posts live bench data here every frame.
    Body: {benches, frame_count, fps, running}
    """
    global _live_state
    data = request.get_json(silent=True) or {}
    with _state_lock:
        _live_state.update(data)
        _live_state["last_update"] = time.time()
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════════════════════════
# ALERTS API
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    alerts = logger.get_all()
    return jsonify(alerts)


@app.route("/api/alerts/recent", methods=["GET"])
def get_recent_alerts():
    n = request.args.get("n", 10, type=int)
    return jsonify(logger.get_recent(n))


@app.route("/api/alert", methods=["POST"])
def post_alert():
    """main.py posts alert data here when score hits threshold."""
    data = request.get_json(silent=True) or {}
    alert = logger.log_alert(
        bench          = data.get("bench", ""),
        student_name   = data.get("student_name", "Unknown"),
        roll_number    = data.get("roll_number", ""),
        score          = data.get("score", 0),
        ml_confidence  = data.get("ml_confidence", 0.0),
        flags          = data.get("flags", {}),
        snapshot_path  = data.get("snapshot_path", "")
    )
    return jsonify(alert), 201


@app.route("/api/summary", methods=["GET"])
def get_summary():
    return jsonify(logger.get_summary())


@app.route("/api/reviewed/<int:alert_id>", methods=["POST"])
def mark_reviewed(alert_id):
    ok = logger.mark_reviewed(alert_id)
    return jsonify({"ok": ok, "id": alert_id})


@app.route("/api/clear", methods=["POST"])
def clear_session():
    logger.clear_session()
    with _state_lock:
        _live_state["benches"] = {}
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
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ARGUS — Exam Monitor</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:#0d1117; color:#e6edf3; font-family:'Segoe UI',sans-serif; }

  /* ── Header ── */
  .header {
    background:#161b22; border-bottom:1px solid #30363d;
    padding:14px 24px; display:flex; align-items:center;
    justify-content:space-between;
  }
  .header h1 { font-size:1.3rem; color:#58a6ff; letter-spacing:2px; }
  .header .meta { font-size:0.78rem; color:#8b949e; }
  .status-dot { display:inline-block; width:10px; height:10px;
    border-radius:50%; background:#3fb950; margin-right:6px;
    animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

  /* ── Layout ── */
  .container { display:grid; grid-template-columns:1fr 380px;
    gap:16px; padding:16px; height:calc(100vh - 57px); }
  .left  { display:flex; flex-direction:column; gap:16px; overflow:hidden; }
  .right { display:flex; flex-direction:column; gap:16px; overflow:hidden; }

  /* ── Cards ── */
  .card { background:#161b22; border:1px solid #30363d;
    border-radius:8px; padding:14px; }
  .card-title { font-size:0.72rem; color:#8b949e; text-transform:uppercase;
    letter-spacing:1px; margin-bottom:10px; }

  /* ── Camera feed ── */
  .feed-wrapper { background:#0d1117; border-radius:6px; overflow:hidden;
    flex:1; display:flex; align-items:center; justify-content:center; }
  .feed-wrapper img { width:100%; max-height:420px; object-fit:contain; }
  .feed-placeholder { color:#484f58; font-size:0.85rem; text-align:center; }

  /* ── Bench cards ── */
  .bench-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
  .bench-card { background:#0d1117; border:1px solid #30363d;
    border-radius:6px; padding:10px; transition:border-color 0.3s; }
  .bench-card.alert { border-color:#f85149; background:#1a0d0d; }
  .bench-card.warning { border-color:#d29922; background:#1a1500; }
  .bench-label { font-size:0.7rem; color:#8b949e; margin-bottom:4px; }
  .bench-name  { font-size:0.85rem; font-weight:600; margin-bottom:6px;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .score-bar-bg { background:#21262d; border-radius:4px; height:6px; }
  .score-bar    { height:6px; border-radius:4px; transition:width 0.4s;
    background:#3fb950; }
  .score-bar.warn  { background:#d29922; }
  .score-bar.alert { background:#f85149; }
  .score-text { font-size:0.75rem; color:#8b949e; margin-top:4px; }
  .conf-text  { font-size:0.68rem; color:#484f58; }

  /* ── Summary bar ── */
  .summary-row { display:flex; gap:16px; }
  .stat { flex:1; text-align:center; }
  .stat-val  { font-size:1.4rem; font-weight:700; color:#58a6ff; }
  .stat-label{ font-size:0.68rem; color:#8b949e; text-transform:uppercase; }

  /* ── Alert table ── */
  .alerts-scroll { overflow-y:auto; flex:1; max-height:400px; }
  table { width:100%; border-collapse:collapse; font-size:0.78rem; }
  th { text-align:left; padding:6px 8px; color:#8b949e; font-weight:500;
    border-bottom:1px solid #21262d; position:sticky; top:0;
    background:#161b22; }
  td { padding:6px 8px; border-bottom:1px solid #21262d; }
  tr:hover td { background:#21262d; }
  .badge { display:inline-block; padding:2px 7px; border-radius:12px;
    font-size:0.68rem; font-weight:600; }
  .badge.red    { background:#2d1117; color:#f85149; border:1px solid #f85149; }
  .badge.yellow { background:#1c1a00; color:#d29922; border:1px solid #d29922; }
  .badge.green  { background:#0d2614; color:#3fb950; border:1px solid #3fb950; }
  .btn-review { background:none; border:1px solid #30363d; color:#8b949e;
    padding:2px 8px; border-radius:4px; cursor:pointer; font-size:0.68rem; }
  .btn-review:hover { border-color:#58a6ff; color:#58a6ff; }
  .reviewed { color:#3fb950; font-size:0.7rem; }

  /* ── FPS indicator ── */
  .fps-tag { font-size:0.68rem; color:#3fb950; margin-left:8px; }
</style>
</head>
<body>

<div class="header">
  <h1>⬡ ARGUS <span style="color:#8b949e;font-size:0.85rem;">EXAM MONITOR</span></h1>
  <div class="meta">
    <span class="status-dot" id="dot"></span>
    <span id="status-text">Connecting...</span>
    <span class="fps-tag" id="fps-tag"></span>
    &nbsp;|&nbsp; VIT Pune &nbsp;|&nbsp;
    <span id="clock"></span>
  </div>
</div>

<div class="container">

  <!-- LEFT COLUMN -->
  <div class="left">

    <!-- Camera Feed -->
    <div class="card" style="flex:1;">
      <div class="card-title">📷 Live Camera Feed</div>
      <div class="feed-wrapper" id="feed-wrapper">
        <img id="live-feed" src="/video_feed"
             onerror="this.style.display='none';document.getElementById('no-feed').style.display='block'">
        <div id="no-feed" class="feed-placeholder" style="display:none;">
          No video feed — start detection (main.py)
        </div>
      </div>
    </div>

    <!-- Bench Status -->
    <div class="card">
      <div class="card-title">🪑 Bench Status</div>
      <div class="bench-grid" id="bench-grid">
        <div class="bench-card" id="bench-B1">
          <div class="bench-label">B1</div>
          <div class="bench-name">—</div>
          <div class="score-bar-bg"><div class="score-bar" style="width:0%"></div></div>
          <div class="score-text">Score: 0</div>
          <div class="conf-text">Conf: —</div>
        </div>
        <div class="bench-card" id="bench-B2">
          <div class="bench-label">B2</div>
          <div class="bench-name">—</div>
          <div class="score-bar-bg"><div class="score-bar" style="width:0%"></div></div>
          <div class="score-text">Score: 0</div>
          <div class="conf-text">Conf: —</div>
        </div>
        <div class="bench-card" id="bench-B3">
          <div class="bench-label">B3</div>
          <div class="bench-name">—</div>
          <div class="score-bar-bg"><div class="score-bar" style="width:0%"></div></div>
          <div class="score-text">Score: 0</div>
          <div class="conf-text">Conf: —</div>
        </div>
      </div>
    </div>

  </div>

  <!-- RIGHT COLUMN -->
  <div class="right">

    <!-- Summary -->
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
          <div class="stat-label">Peak Score</div>
        </div>
        <div class="stat">
          <div class="stat-val" id="s-benches">0</div>
          <div class="stat-label">Benches</div>
        </div>
      </div>
    </div>

    <!-- Alert Log -->
    <div class="card" style="flex:1; display:flex; flex-direction:column;">
      <div class="card-title" style="display:flex;justify-content:space-between;align-items:center;">
        <span>🚨 Alert Log</span>
        <button class="btn-review" onclick="clearSession()">Clear Session</button>
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
            <tr><td colspan="7" style="text-align:center;color:#484f58;padding:20px;">
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
function updateClock() {
  const now = new Date();
  document.getElementById('clock').textContent =
    now.toLocaleTimeString('en-IN', {hour12:false});
}
setInterval(updateClock, 1000);
updateClock();

// ── Fetch live status ──────────────────────────────────────────────────────
async function fetchStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    const age = Date.now()/1000 - (d.last_update || 0);
    const live = d.running && age < 3;

    document.getElementById('dot').style.background = live ? '#3fb950' : '#484f58';
    document.getElementById('status-text').textContent =
      live ? 'Detection Running' : 'Standby';
    document.getElementById('fps-tag').textContent =
      d.fps ? d.fps + ' FPS' : '';

    // Update bench cards
    const benches = d.benches || {};
    ['B1','B2','B3'].forEach(b => {
      const info  = benches[b] || {};
      const score = info.score || 0;
      const conf  = info.ml_confidence || 0;
      const name  = info.student_name  || '—';
      const pct   = Math.min(100, (score / 30) * 100);

      const card = document.getElementById('bench-' + b);
      if (!card) return;
      card.querySelector('.bench-name').textContent = name;
      card.querySelector('.score-text').textContent = 'Score: ' + score;
      card.querySelector('.conf-text').textContent  =
        conf ? 'Conf: ' + (conf*100).toFixed(0) + '%' : 'Conf: —';

      const bar = card.querySelector('.score-bar');
      bar.style.width = pct + '%';
      bar.className = 'score-bar' +
        (score >= 30 ? ' alert' : score >= 20 ? ' warn' : '');
      card.className = 'bench-card' +
        (score >= 30 ? ' alert' : score >= 20 ? ' warning' : '');
    });
  } catch(e) {}
}

// ── Fetch alerts ───────────────────────────────────────────────────────────
async function fetchAlerts() {
  try {
    const [ar, sr] = await Promise.all([
      fetch('/api/alerts'),
      fetch('/api/summary')
    ]);
    const alerts  = await ar.json();
    const summary = await sr.json();

    // Summary
    document.getElementById('s-total').textContent    = summary.total_alerts    || 0;
    document.getElementById('s-students').textContent = summary.unique_students || 0;
    document.getElementById('s-high').textContent     = summary.highest_score   || 0;
    document.getElementById('s-benches').textContent  =
      (summary.benches_flagged || []).length;

    // Table
    const tbody = document.getElementById('alert-tbody');
    if (!alerts.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#484f58;padding:20px;">No alerts yet</td></tr>';
      return;
    }
    tbody.innerHTML = alerts.slice(0,50).map(a => {
      const sc = a.score;
      const badgeClass = sc >= 40 ? 'red' : sc >= 30 ? 'yellow' : 'green';
      const reviewed = a.reviewed
        ? '<span class="reviewed">✓</span>'
        : `<button class="btn-review" onclick="markReviewed(${a.id})">Review</button>`;
      return `<tr>
        <td>#${a.id}</td>
        <td>${a.time}</td>
        <td><b>${a.bench}</b></td>
        <td style="max-width:90px;overflow:hidden;text-overflow:ellipsis;">${a.student_name}</td>
        <td><span class="badge ${badgeClass}">${sc}</span></td>
        <td>${(a.ml_confidence*100).toFixed(0)}%</td>
        <td>${reviewed}</td>
      </tr>`;
    }).join('');
  } catch(e) {}
}

// ── Mark reviewed ──────────────────────────────────────────────────────────
async function markReviewed(id) {
  await fetch('/api/reviewed/' + id, {method:'POST'});
  fetchAlerts();
}

// ── Clear session ──────────────────────────────────────────────────────────
async function clearSession() {
  if (!confirm('Clear all alerts for this session?')) return;
  await fetch('/api/clear', {method:'POST'});
  fetchAlerts();
  fetchStatus();
}

// ── Poll ───────────────────────────────────────────────────────────────────
fetchStatus();
fetchAlerts();
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
    print("  ARGUS — File 11: Dashboard")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("=" * 55)
    print("\n  Dashboard : http://localhost:5000")
    print("  Video feed: http://localhost:5000/video_feed")
    print("  Alerts API: http://localhost:5000/api/alerts")
    print("\n  Press Ctrl+C to stop.\n")

    # Ensure snapshots folder exists
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)

    app.run(
        host    = "0.0.0.0",
        port    = 5000,
        debug   = False,
        threaded= True
    )
