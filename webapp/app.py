"""
ARGUS — File 11: webapp/app.py  [v10 — Teacher Workflow]
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

v10 changes:
  - Excel upload parses PRN/Name/Bench No and assigns students to B1/B2/B3
  - Scan ArUco runs as background thread inside app.py (no terminal needed)
  - Start Exam launches main.py as subprocess (teacher never touches terminal)
  - Step-by-step workflow: Upload Excel → Scan ArUco → Start Exam
  - Dashboard guides teacher through each step with status indicators
  - All 3 fixes from v9.1 retained (score thresholds 100/60, exam timer)

Teacher workflow:
  1. Open localhost:5000
  2. Upload ARGUS_Seating.xlsx
  3. Click Scan ArUco (place markers first)
  4. Click Start Exam
  5. Monitor dashboard
  6. Click Stop Exam
"""

import os
import sys
import json
import threading
import time
import subprocess
from datetime import datetime

from flask import (Flask, Response, jsonify, request,
                   render_template_string, send_from_directory)

# ── Path setup ────────────────────────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT     = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "detection"))

from webapp.alert_logger import AlertLogger

SNAPSHOT_DIR  = os.path.join(_ROOT, "snapshots")
ZONES_FILE    = os.path.join(_ROOT, "detection", "zones.json")
SEATING_FILE  = os.path.join(_ROOT, "seating_upload.json")
MAIN_PY       = os.path.join(_ROOT, "detection", "main.py")

ALERT_THRESHOLD   = 100
WARNING_THRESHOLD = 60
MAX_SCORE         = 100

app    = Flask(__name__)
logger = AlertLogger()

# ── Live state ────────────────────────────────────────────────────────────────
_state_lock = threading.Lock()
_live_state = {
    "benches": {}, "frame_count": 0,
    "fps": 0, "running": False, "last_update": 0
}

# ── Frame buffer ──────────────────────────────────────────────────────────────
_frame_lock   = threading.Lock()
_latest_frame = None

# ── Exam state ────────────────────────────────────────────────────────────────
_exam_lock       = threading.Lock()
_exam_active     = False
_exam_start_time = None
_main_process    = None   # subprocess.Popen handle

# ── ArUco scan state ──────────────────────────────────────────────────────────
_aruco_lock   = threading.Lock()
_aruco_state  = {
    "running"     : False,
    "done"        : False,
    "zones_found" : 0,
    "zones"       : {},
    "message"     : "Not started",
    "error"       : ""
}

# ── Workflow state ─────────────────────────────────────────────────────────────
# IDLE → SEATING_READY → ARUCO_READY → EXAM_ACTIVE → EXAM_ENDED
_workflow_state = "IDLE"


def _set_workflow(state):
    global _workflow_state
    _workflow_state = state


# ════════════════════════════════════════════════════════════════════════════
# EXCEL SEATING UPLOAD
# ════════════════════════════════════════════════════════════════════════════

def _parse_excel(file_bytes):
    """
    Parse ARGUS_Seating.xlsx bytes.
    Expected columns: PRN, Candidate Name, Bench No, Room, Division, Bench Side
    Returns: dict {bench_id: {student_name, roll_number, ...}}
    """
    import io
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(file_bytes))
        ws = wb.active
    except Exception as e:
        return None, f"Cannot open Excel: {e}"

    headers = []
    rows    = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            headers = [str(c).strip() if c else "" for c in row]
        else:
            if any(c is not None for c in row):
                rows.append(row)

    # Flexible column matching
    def find_col(keywords):
        for kw in keywords:
            for j, h in enumerate(headers):
                if kw.lower() in h.lower():
                    return j
        return None

    col_prn   = find_col(["prn", "roll", "id"])
    col_name  = find_col(["candidate", "name", "student"])
    col_bench = find_col(["bench no", "bench_no", "bench"])
    col_room  = find_col(["room"])

    if col_bench is None:
        return None, "Could not find 'Bench No' column in Excel"

    result = {}
    for row in rows:
        bench_raw = row[col_bench] if col_bench is not None else None
        if bench_raw is None:
            continue
        try:
            bench_num = int(bench_raw)
        except (ValueError, TypeError):
            continue
        bench_id  = f"B{bench_num}"
        name      = str(row[col_name]).strip()  if col_name  is not None else "Unknown"
        prn       = str(row[col_prn]).strip()   if col_prn   is not None else ""
        room      = str(row[col_room]).strip()  if col_room  is not None else ""

        result[bench_id] = {
            "student_name": name,
            "roll_number" : prn,
            "room"        : room,
            "bench"       : bench_id,
            "status"      : "ACTIVE"
        }

    return result, None


def _update_zones_with_students(seating):
    """Merge student names into zones.json without overwriting coordinates."""
    existing = {}
    if os.path.exists(ZONES_FILE):
        try:
            with open(ZONES_FILE) as f:
                raw = json.load(f)
            # FIX: handle both list and dict formats for zones.json
            if isinstance(raw, list):
                for item in raw:
                    key = item.get("bench") or item.get("name", "")
                    if key:
                        existing[key] = item
            elif isinstance(raw, dict):
                existing = raw
        except Exception:
            existing = {}

    for bench_id, info in seating.items():
        if bench_id not in existing:
            existing[bench_id] = {}
        existing[bench_id]["student_name"] = info["student_name"]
        existing[bench_id]["roll_number"]  = info["roll_number"]
        existing[bench_id]["status"]       = "ACTIVE"

    with open(ZONES_FILE, "w") as f:
        json.dump(existing, f, indent=4)


@app.route("/api/seating/upload", methods=["POST"])
def seating_upload():
    """Accept Excel file upload, parse it, update zones.json."""
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "No file provided"}), 400

    try:
        file_bytes = file.read()
        seating, err = _parse_excel(file_bytes)
        if err:
            return jsonify({"ok": False, "error": err}), 400

        # Save seating JSON
        with open(SEATING_FILE, "w") as f:
            json.dump(seating, f, indent=4)

        # Update zones.json with student names
        _update_zones_with_students(seating)

        _set_workflow("SEATING_READY")
        print(f"[ARGUS] Seating uploaded — {len(seating)} students assigned")
        for bid, s in seating.items():
            print(f"  {bid}: {s['student_name']} ({s['roll_number']})")

        return jsonify({"ok": True, "seating": seating})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/seating", methods=["GET"])
def get_seating():
    if os.path.exists(SEATING_FILE):
        try:
            with open(SEATING_FILE) as f:
                return jsonify(json.load(f))
        except Exception:
            pass
    return jsonify({})


# ════════════════════════════════════════════════════════════════════════════
# ARUCO SCAN (background thread — no terminal needed)
# ════════════════════════════════════════════════════════════════════════════

def _aruco_scan_thread():
    """
    Runs ARUCOScanner in a background thread.
    Opens camera, scans until stable or 45s timeout, saves zones.json.
    """
    global _aruco_state

    with _aruco_lock:
        _aruco_state = {
            "running": True, "done": False,
            "zones_found": 0, "zones": {},
            "message": "Opening camera...", "error": ""
        }

    try:
        import cv2
        sys.path.insert(0, os.path.join(_ROOT, "detection"))
        from aruco_scanner import ARUCOScanner

        scanner = ARUCOScanner()
        cap = cv2.VideoCapture(0)

        if not cap.isOpened():
            with _aruco_lock:
                _aruco_state.update({
                    "running": False, "done": True,
                    "error": "Camera not available (index 0)"
                })
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        # Flush warm-up frames
        for _ in range(10):
            cap.read()

        with _aruco_lock:
            _aruco_state["message"] = "Scanning for ArUco markers..."

        start_time = time.time()
        TIMEOUT    = 90
        # Total hit count — doesn't reset on missed frames
        hit_count  = 0
        HIT_NEED   = 6
        last_zones = {}

        while True:
            if time.time() - start_time > TIMEOUT:
                with _aruco_lock:
                    _aruco_state.update({
                        "running": False, "done": True,
                        "error": "Timeout — hold marker flat facing camera, "
                                 "ensure room light hits marker directly."
                    })
                break

            ret, frame = cap.read()
            if not ret:
                continue

            zones = scanner.scan_frame(frame)

            if len(zones) > 0:
                last_zones = zones
                hit_count += 1

            with _aruco_lock:
                found_list = list(last_zones.keys())
                _aruco_state["zones_found"] = len(found_list)
                _aruco_state["message"] = (
                    f"Found {len(found_list)} marker(s): {', '.join(sorted(found_list))}"
                    f" — locking [{hit_count}/{HIT_NEED}]"
                    if found_list
                    else "Searching... Point camera at ArUco markers"
                )

            # Push frame to dashboard so feed is visible during scan
            try:
                _, jpeg = cv2.imencode(".jpg", frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 40])
                with _frame_lock:
                    global _latest_frame
                    _latest_frame = jpeg.tobytes()
            except Exception:
                pass

            if hit_count >= HIT_NEED:
                zones = last_zones
                # Merge student names from seating file
                seating = {}
                if os.path.exists(SEATING_FILE):
                    try:
                        with open(SEATING_FILE) as f:
                            seating = json.load(f)
                    except Exception:
                        pass

                for bench_id, zone in zones.items():
                    if bench_id in seating:
                        zone["student_name"] = seating[bench_id]["student_name"]
                        zone["roll_number"]  = seating[bench_id]["roll_number"]

                scanner.save_zones(zones)
                _set_workflow("ARUCO_READY")

                with _aruco_lock:
                    _aruco_state.update({
                        "running"    : False,
                        "done"       : True,
                        "zones_found": len(zones),
                        "zones"      : {k: {
                                            "bench"  : k,
                                            "student": zones[k].get("student_name", "Unknown"),
                                            "roll"   : zones[k].get("roll_number", "")
                                        } for k in zones},
                        "message": f"✓ {len(zones)} zone(s) locked and saved"
                    })
                print(f"[ARUCO] Scan complete — {len(zones)} zones locked")
                break

            time.sleep(0.05)

        cap.release()

    except Exception as e:
        with _aruco_lock:
            _aruco_state.update({
                "running": False, "done": True,
                "error": str(e)
            })
        print(f"[ARUCO] Scan thread error: {e}")


@app.route("/api/aruco/start", methods=["POST"])
def aruco_start():
    with _aruco_lock:
        if _aruco_state["running"]:
            return jsonify({"ok": False, "error": "Scan already running"}), 400

    t = threading.Thread(target=_aruco_scan_thread, daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/aruco/status", methods=["GET"])
def aruco_status():
    with _aruco_lock:
        return jsonify(dict(_aruco_state))


# ════════════════════════════════════════════════════════════════════════════
# EXAM CONTROL (launches main.py as subprocess)
# ════════════════════════════════════════════════════════════════════════════

def _export_session_excel():
    """
    Export current session alerts to Excel.
    Returns file path or None on failure.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        import datetime

        alerts = logger.get_all()
        summary = logger.get_summary()

        wb = Workbook()
        ws = wb.active
        ws.title = "ARGUS Session Report"

        # Header styling
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill("solid", fgColor="1a1a2e")

        # Title row
        ws.merge_cells("A1:G1")
        ws["A1"] = f"ARGUS Exam Session Report — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        ws["A1"].font = Font(bold=True, size=13)
        ws["A1"].alignment = Alignment(horizontal="center")

        # Summary row
        ws.merge_cells("A2:G2")
        ws["A2"] = (f"Total Alerts: {summary.get('total_alerts',0)} | "
                    f"Students Flagged: {summary.get('unique_students',0)} | "
                    f"Peak Score: {summary.get('highest_score',0)} | "
                    f"Benches: {', '.join(summary.get('benches_flagged',[]))}")
        ws["A2"].font = Font(italic=True)

        # Column headers
        headers = ["#", "Time", "Bench", "Student", "Roll No", "Score", "ML Conf %"]
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=4, column=col, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        # Alert rows
        for row_idx, alert in enumerate(alerts, 5):
            ws.cell(row=row_idx, column=1, value=alert.get("id", ""))
            ws.cell(row=row_idx, column=2, value=alert.get("time", ""))
            ws.cell(row=row_idx, column=3, value=alert.get("bench", ""))
            ws.cell(row=row_idx, column=4, value=alert.get("student_name", ""))
            ws.cell(row=row_idx, column=5, value=alert.get("roll_number", ""))
            ws.cell(row=row_idx, column=6, value=alert.get("score", 0))
            conf_pct = round(alert.get("ml_confidence", 0) * 100, 1)
            ws.cell(row=row_idx, column=7, value=conf_pct)

        # Column widths
        for col, width in zip("ABCDEFG", [5, 10, 8, 20, 15, 8, 10]):
            ws.column_dimensions[col].width = width

        # Save
        reports_dir = os.path.join(_ROOT, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        fname = f"ARGUS_Report_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        fpath = os.path.join(reports_dir, fname)
        wb.save(fpath)
        print(f"[ARGUS] Report saved: {fpath}")
        return fname, fpath
    except Exception as e:
        print(f"[ARGUS] Excel export failed: {e}")
        return None, None


def _ensure_default_zones():
    """
    If zones.json has no coordinates (ArUco not scanned),
    write default zones dividing 1280x720 frame into 3 equal strips.
    Students still get detected — just without precise bench mapping.
    """
    seating = {}
    if os.path.exists(SEATING_FILE):
        try:
            with open(SEATING_FILE) as f:
                seating = json.load(f)
        except Exception:
            seating = {}

    existing = {}
    if os.path.exists(ZONES_FILE):
        try:
            with open(ZONES_FILE) as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                existing = raw
        except Exception:
            existing = {}

    # Check if any zone has valid coordinates
    has_coords = any(
        isinstance(v, dict) and v.get('x') is not None
        for v in existing.values()
    )
    if has_coords:
        return  # ArUco already done, nothing to do

    # Write default zones — 3 equal vertical strips for 1280x720
    default_zones = {
        "B1": {"x": 0,   "y": 50, "w": 380, "h": 620, "aruco_id": 0},
        "B2": {"x": 400, "y": 50, "w": 380, "h": 620, "aruco_id": 1},
        "B3": {"x": 800, "y": 50, "w": 380, "h": 620, "aruco_id": 2},
    }
    for bench_id, zone in default_zones.items():
        s = seating.get(bench_id, {})
        zone["student_name"] = s.get("student_name", "Unknown")
        zone["roll_number"]  = s.get("roll_number", "")
        zone["status"]       = "ACTIVE"

    with open(ZONES_FILE, "w") as f:
        json.dump(default_zones, f, indent=4)
    print("[ARGUS] Default zones written to zones.json (ArUco not scanned)")


@app.route("/api/exam/start", methods=["POST"])
def exam_start():
    global _exam_active, _exam_start_time, _main_process

    with _exam_lock:
        if _exam_active:
            return jsonify({"ok": False, "error": "Exam already active"}), 400

        try:
            # Block start if ArUco scan still running (camera conflict)
            with _aruco_lock:
                aruco_running = _aruco_state.get("running", False)
            if aruco_running:
                return jsonify({
                    "ok": False,
                    "error": "ArUco scan still running — wait for it to complete first"
                }), 400

            # Write default zones if ArUco not scanned
            _ensure_default_zones()

            # Launch main.py in visible terminal window (Windows)
            if os.name == 'nt':
                cmd = f'cd /d "{_ROOT}" && py -3.11 detection/main.py'
                _main_process = subprocess.Popen(
                    cmd, shell=True,
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
            else:
                _main_process = subprocess.Popen(
                    [sys.executable, MAIN_PY], cwd=_ROOT
                )
            _exam_active     = True
            _exam_start_time = time.time()
            _set_workflow("EXAM_ACTIVE")
            # Auto-clear previous session alerts
            logger.clear_session()
            with _state_lock:
                _live_state["benches"] = {}
                _live_state["frame_count"] = 0
            print(f"[ARGUS] Exam started — main.py PID {_main_process.pid}")
            return jsonify({"ok": True, "pid": _main_process.pid})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/exam/stop", methods=["POST"])
def exam_stop():
    global _exam_active, _main_process

    with _exam_lock:
        _exam_active = False
        _set_workflow("EXAM_ENDED")

        if _main_process and _main_process.poll() is None:
            try:
                _main_process.terminate()
                _main_process.wait(timeout=5)
                print(f"[ARGUS] main.py terminated (PID {_main_process.pid})")
            except Exception as e:
                print(f"[ARGUS] Stop error: {e}")
        _main_process = None

    # Auto-export Excel report
    fname, fpath = _export_session_excel()
    return jsonify({
        "ok": True,
        "report_file": fname,
        "report_ready": fname is not None
    })


@app.route("/api/exam/status", methods=["GET"])
def exam_status():
    with _exam_lock:
        active   = _exam_active
        start_ts = _exam_start_time
        pid      = _main_process.pid if _main_process else None
    elapsed = int(time.time() - start_ts) if (active and start_ts) else 0
    return jsonify({
        "active"     : active,
        "elapsed_sec": elapsed,
        "pid"        : pid
    })


@app.route("/api/workflow", methods=["GET"])
def get_workflow():
    seating_ok = os.path.exists(SEATING_FILE)
    aruco_ok   = _workflow_state in ("ARUCO_READY", "EXAM_ACTIVE", "EXAM_ENDED")
    return jsonify({
        "state"     : _workflow_state,
        "seating_ok": seating_ok,
        "aruco_ok"  : aruco_ok,
        "exam_active": _exam_active
    })


# ════════════════════════════════════════════════════════════════════════════
# VIDEO + STATUS
# ════════════════════════════════════════════════════════════════════════════

def _generate_stream():
    while True:
        with _frame_lock:
            frame = _latest_frame
        if frame:
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
        else:
            time.sleep(0.05)
        time.sleep(0.033)


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


@app.route("/api/status", methods=["GET"])
def get_status():
    with _state_lock:
        state = dict(_live_state)
    with _exam_lock:
        state["exam_active"]  = _exam_active
        state["exam_elapsed"] = (
            int(time.time() - _exam_start_time)
            if (_exam_active and _exam_start_time) else 0
        )
    return jsonify(state)


@app.route("/api/status", methods=["POST"])
def push_status():
    global _live_state
    data = request.get_json(silent=True) or {}
    with _state_lock:
        _live_state.update(data)
        _live_state["last_update"] = time.time()
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════════════════════════
# ALERTS
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/alerts",         methods=["GET"])
def get_alerts():       return jsonify(logger.get_all())

@app.route("/api/alerts/recent",  methods=["GET"])
def get_recent_alerts():
    return jsonify(logger.get_recent(request.args.get("n", 10, type=int)))

@app.route("/api/alert",          methods=["POST"])
def post_alert():
    d = request.get_json(silent=True) or {}
    a = logger.log_alert(
        bench=d.get("bench",""), student_name=d.get("student_name","Unknown"),
        roll_number=d.get("roll_number",""), score=d.get("score",0),
        ml_confidence=d.get("ml_confidence",0.0),
        flags=d.get("flags",{}), snapshot_path=d.get("snapshot_path","")
    )
    return jsonify(a), 201

@app.route("/api/summary",        methods=["GET"])
def get_summary():      return jsonify(logger.get_summary())

@app.route("/api/reviewed/<int:alert_id>", methods=["POST"])
def mark_reviewed(alert_id):
    return jsonify({"ok": logger.mark_reviewed(alert_id), "id": alert_id})

@app.route("/api/clear",          methods=["POST"])
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

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ARGUS — Exam Monitor</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0d1117; color:#e6edf3;
       font-family:'Segoe UI',sans-serif; min-height:100vh; }

/* ── Header ── */
.header { background:#161b22; border-bottom:1px solid #30363d;
  padding:12px 24px; display:flex; align-items:center;
  justify-content:space-between; }
.header h1 { font-size:1.2rem; color:#58a6ff; letter-spacing:2px; }
.header .meta { font-size:0.78rem; color:#8b949e;
  display:flex; align-items:center; gap:14px; }
.status-dot { display:inline-block; width:9px; height:9px;
  border-radius:50%; background:#3fb950; margin-right:5px;
  animation:pulse 1.5s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

/* ── Workflow bar ── */
.workflow-bar { background:#0d1117; border-bottom:1px solid #21262d;
  padding:10px 24px; display:flex; align-items:center; gap:0; }
.step { display:flex; align-items:center; gap:8px;
  padding:6px 16px; border-radius:6px; font-size:0.78rem;
  color:#484f58; border:1px solid transparent; transition:all .3s; }
.step.done    { color:#3fb950; border-color:#238636; background:#0d2614; }
.step.active  { color:#58a6ff; border-color:#1d4ed8; background:#0d1b3e; }
.step.pending { color:#484f58; }
.step-num { width:20px; height:20px; border-radius:50%;
  background:#21262d; display:flex; align-items:center;
  justify-content:center; font-size:0.68rem; font-weight:700; }
.step.done .step-num  { background:#238636; color:#fff; }
.step.active .step-num{ background:#1d4ed8; color:#fff; }
.step-arrow { color:#30363d; padding:0 6px; font-size:0.9rem; }

/* ── Toolbar ── */
.toolbar { background:#161b22; border-bottom:1px solid #30363d;
  padding:8px 24px; display:flex; gap:10px; align-items:center;
  flex-wrap:wrap; }
.btn { padding:7px 16px; border-radius:5px; border:none; cursor:pointer;
  font-size:0.78rem; font-weight:600; letter-spacing:.5px;
  transition:opacity .2s; display:flex; align-items:center; gap:6px; }
.btn:disabled { opacity:.35; cursor:not-allowed; }
.btn:not(:disabled):hover { opacity:.82; }
.btn-upload { background:#21262d; color:#e6edf3;
  border:1px solid #30363d; position:relative; overflow:hidden; }
.btn-upload input[type=file] { position:absolute; inset:0;
  opacity:0; cursor:pointer; width:100%; }
.btn-aruco  { background:#1d4ed8; color:#fff; }
.btn-start  { background:#238636; color:#fff; }
.btn-stop   { background:#b91c1c; color:#fff; }
.btn-clear  { background:#21262d; color:#8b949e;
  border:1px solid #30363d; }
.exam-timer { font-size:0.85rem; font-weight:700; letter-spacing:1px;
  padding:5px 12px; border-radius:4px; min-width:82px;
  text-align:center; color:#484f58; border:1px solid #21262d;
  background:#0d1117; }
.exam-timer.on { color:#3fb950; border-color:#238636; background:#0d2614; }

/* ── Layout ── */
.container { display:grid; grid-template-columns:1fr 370px;
  gap:14px; padding:14px; height:calc(100vh - 140px); }
.left  { display:flex; flex-direction:column; gap:14px; overflow:hidden; }
.right { display:flex; flex-direction:column; gap:14px; overflow-y:auto; }

/* ── Cards ── */
.card { background:#161b22; border:1px solid #30363d;
  border-radius:8px; padding:14px; }
.card-title { font-size:0.7rem; color:#8b949e; text-transform:uppercase;
  letter-spacing:1px; margin-bottom:10px;
  display:flex; justify-content:space-between; align-items:center; }

/* ── Feed ── */
.feed-wrap { background:#0d1117; border-radius:6px; overflow:hidden;
  flex:1; display:flex; align-items:center; justify-content:center;
  min-height:240px; position:relative; }
.feed-wrap img { width:100%; max-height:400px; object-fit:contain; }
.feed-placeholder { color:#484f58; font-size:0.82rem;
  text-align:center; padding:40px; }
.det-badge { position:absolute; top:8px; right:8px; font-size:0.65rem;
  padding:3px 8px; border-radius:3px; font-weight:700; }
.det-badge.on  { background:#0d2614; color:#3fb950; border:1px solid #238636; }
.det-badge.off { background:#1a0d0d; color:#f85149; border:1px solid #f85149; }

/* ── Bench cards ── */
.bench-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
.bench-card { background:#0d1117; border:1px solid #30363d;
  border-radius:6px; padding:10px; transition:all .3s; }
.bench-card.alert   { border-color:#f85149; background:#1a0d0d; }
.bench-card.warning { border-color:#d29922; background:#1a1500; }
.bench-label { font-size:0.68rem; color:#8b949e; margin-bottom:2px; }
.bench-name  { font-size:0.82rem; font-weight:600; margin-bottom:5px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.bench-roll  { font-size:0.65rem; color:#484f58; margin-bottom:5px; }
.bar-bg  { background:#21262d; border-radius:3px; height:5px; }
.bar     { height:5px; border-radius:3px; background:#3fb950;
  transition:width .4s; }
.bar.warn  { background:#d29922; }
.bar.alert { background:#f85149; }
.score-txt { font-size:0.72rem; color:#8b949e; margin-top:3px; }
.conf-txt  { font-size:0.65rem; color:#484f58; }
.awaiting  { font-size:0.72rem; color:#484f58; font-style:italic; }

/* ── Seating table ── */
.seating-table { width:100%; border-collapse:collapse; font-size:0.75rem; }
.seating-table th { color:#8b949e; padding:5px 8px; text-align:left;
  border-bottom:1px solid #21262d; }
.seating-table td { padding:5px 8px; border-bottom:1px solid #161b22; }
.seating-table tr:hover td { background:#21262d; }

/* ── ArUco status ── */
.aruco-status { font-size:0.75rem; padding:8px 12px; border-radius:5px;
  margin-top:6px; }
.aruco-status.scanning { background:#0d1b3e; color:#58a6ff;
  border:1px solid #1d4ed8; }
.aruco-status.done     { background:#0d2614; color:#3fb950;
  border:1px solid #238636; }
.aruco-status.error    { background:#1a0d0d; color:#f85149;
  border:1px solid #f85149; }
.aruco-status.idle     { background:#161b22; color:#484f58;
  border:1px solid #21262d; }
.progress-dots::after { content:'...'; animation:dots 1.2s steps(4) infinite; }
@keyframes dots { 0%{content:''} 25%{content:'.'} 50%{content:'..'} 75%{content:'...'} }

/* ── Summary ── */
.sum-row { display:flex; gap:12px; }
.stat { flex:1; text-align:center; }
.stat-val   { font-size:1.3rem; font-weight:700; color:#58a6ff; }
.stat-label { font-size:0.65rem; color:#8b949e; text-transform:uppercase; }

/* ── Alert table ── */
.alerts-scroll { overflow-y:auto; max-height:280px; }
table.alert-tbl { width:100%; border-collapse:collapse; font-size:0.75rem; }
table.alert-tbl th { text-align:left; padding:5px 7px; color:#8b949e;
  border-bottom:1px solid #21262d; position:sticky;
  top:0; background:#161b22; }
table.alert-tbl td { padding:5px 7px; border-bottom:1px solid #21262d; }
table.alert-tbl tr:hover td { background:#21262d; }
.badge { display:inline-block; padding:1px 6px; border-radius:10px;
  font-size:0.65rem; font-weight:700; }
.badge.red    { background:#2d1117;color:#f85149;border:1px solid #f85149; }
.badge.yellow { background:#1c1a00;color:#d29922;border:1px solid #d29922; }
.badge.green  { background:#0d2614;color:#3fb950;border:1px solid #3fb950; }
.btn-rev { background:none; border:1px solid #30363d; color:#8b949e;
  padding:2px 7px; border-radius:3px; cursor:pointer; font-size:0.65rem; }
.btn-rev:hover { border-color:#58a6ff; color:#58a6ff; }
.reviewed { color:#3fb950; font-size:0.68rem; }
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <h1>⬡ ARGUS <span style="color:#8b949e;font-size:0.82rem;"> EXAM SURVEILLANCE · VIT PUNE · CSAIML-E</span></h1>
  <div class="meta">
    <span><span class="status-dot" id="dot"></span>
    <span id="status-text">Standby</span></span>
    <span id="fps-tag" style="color:#3fb950;font-size:0.68rem;"></span>
    <span id="clock"></span>
  </div>
</div>

<!-- Workflow steps -->
<div class="workflow-bar">
  <div class="step pending" id="step1">
    <div class="step-num">1</div>
    <span>Upload Seating File</span>
  </div>
  <div class="step-arrow">›</div>
  <div class="step pending" id="step2">
    <div class="step-num">2</div>
    <span>Scan ArUco Markers</span>
  </div>
  <div class="step-arrow">›</div>
  <div class="step pending" id="step3">
    <div class="step-num">3</div>
    <span>Start Exam</span>
  </div>
</div>

<!-- Toolbar -->
<div class="toolbar">

  <!-- Step 1: Upload Excel -->
  <button class="btn btn-upload" id="btn-upload" title="Upload ARGUS_Seating.xlsx">
    ↑ SEATING FILE
    <input type="file" id="seating-input" accept=".xlsx,.xls"
           onchange="uploadSeating(this)">
  </button>

  <!-- Step 2: Scan ArUco -->
  <button class="btn btn-aruco" id="btn-aruco"
          onclick="startAruco()" disabled>
    ⊞ SCAN ARUCO
  </button>

  <!-- Step 3: Start/Stop Exam -->
  <button class="btn btn-start" id="btn-start"
          onclick="startExam()" disabled>
    ▶ START EXAM
  </button>
  <button class="btn btn-stop" id="btn-stop"
          onclick="stopExam()" disabled>
    ■ STOP EXAM
  </button>

  <button class="btn btn-clear" onclick="clearSession()">✕ CLEAR</button>

  <div class="exam-timer" id="exam-timer">00:00:00</div>
  <span id="exam-label" style="font-size:0.72rem;color:#484f58;"></span>
</div>

<!-- Main layout -->
<div class="container">

  <!-- LEFT -->
  <div class="left">

    <!-- Live feed -->
    <div class="card" style="flex:1;">
      <div class="card-title">
        <span>📷 LIVE SURVEILLANCE FEED</span>
        <span id="det-badge" class="det-badge off">DETECTION OFFLINE</span>
      </div>
      <div class="feed-wrap">
        <img id="live-feed" src="/video_feed"
             onerror="this.style.display='none';
                      document.getElementById('no-feed').style.display='block'">
        <div id="no-feed" class="feed-placeholder" style="display:none;">
          Detection offline — start exam to begin monitoring
        </div>
      </div>
    </div>

    <!-- Bench status -->
    <div class="card">
      <div class="card-title">🪑 BENCH STATUS</div>
      <div class="bench-grid">
        <div class="bench-card" id="bench-B1">
          <div class="bench-label">B1</div>
          <div class="bench-name awaiting" id="name-B1">Awaiting student</div>
          <div class="bench-roll" id="roll-B1"></div>
          <div class="bar-bg"><div class="bar" id="bar-B1" style="width:0%"></div></div>
          <div class="score-txt" id="score-B1">0 / 100</div>
          <div class="conf-txt"  id="conf-B1">—</div>
        </div>
        <div class="bench-card" id="bench-B2">
          <div class="bench-label">B2</div>
          <div class="bench-name awaiting" id="name-B2">Awaiting student</div>
          <div class="bench-roll" id="roll-B2"></div>
          <div class="bar-bg"><div class="bar" id="bar-B2" style="width:0%"></div></div>
          <div class="score-txt" id="score-B2">0 / 100</div>
          <div class="conf-txt"  id="conf-B2">—</div>
        </div>
        <div class="bench-card" id="bench-B3">
          <div class="bench-label">B3</div>
          <div class="bench-name awaiting" id="name-B3">Awaiting student</div>
          <div class="bench-roll" id="roll-B3"></div>
          <div class="bar-bg"><div class="bar" id="bar-B3" style="width:0%"></div></div>
          <div class="score-txt" id="score-B3">0 / 100</div>
          <div class="conf-txt"  id="conf-B3">—</div>
        </div>
      </div>
    </div>

  </div>

  <!-- RIGHT -->
  <div class="right">

    <!-- Seating panel -->
    <div class="card">
      <div class="card-title">
        <span>📋 SEATING ASSIGNMENT</span>
        <span id="seating-status" style="font-size:0.68rem;color:#484f58;">Not uploaded</span>
      </div>
      <table class="seating-table" id="seating-table">
        <thead><tr><th>Bench</th><th>Name</th><th>PRN</th></tr></thead>
        <tbody id="seating-tbody">
          <tr><td colspan="3" style="color:#484f58;font-style:italic;">
            Upload Excel file to assign students</td></tr>
        </tbody>
      </table>
      <!-- ArUco status line -->
      <div class="aruco-status idle" id="aruco-msg">
        ArUco: Not scanned
      </div>
    </div>

    <!-- Summary -->
    <div class="card">
      <div class="card-title">📊 SESSION METRICS</div>
      <div class="sum-row">
        <div class="stat"><div class="stat-val" id="s-total">0</div>
          <div class="stat-label">Alerts</div></div>
        <div class="stat"><div class="stat-val" id="s-students">0</div>
          <div class="stat-label">Flagged</div></div>
        <div class="stat"><div class="stat-val" id="s-high">0</div>
          <div class="stat-label">Peak Score</div></div>
        <div class="stat"><div class="stat-val" id="s-benches">0</div>
          <div class="stat-label">Benches</div></div>
      </div>
    </div>

    <!-- Alert log -->
    <div class="card" style="flex:1;">
      <div class="card-title">
        <span>🚨 INCIDENT LOG</span>
        <span id="log-ts" style="font-size:0.65rem;color:#484f58;">Live</span>
      </div>
      <div class="alerts-scroll">
        <table class="alert-tbl">
          <thead><tr>
            <th>#</th><th>Time</th><th>Bench</th>
            <th>Student</th><th>Score</th><th>Conf</th><th></th>
          </tr></thead>
          <tbody id="alert-tbody">
            <tr><td colspan="7"
              style="text-align:center;color:#484f58;padding:16px;">
              No incidents yet</td></tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>
</div>

<script>
// ── Constants ──────────────────────────────────────────────────────────────
const ALERT_THR   = 100;
const WARN_THR    = 60;
const MAX_SCORE   = 100;

// ── State ──────────────────────────────────────────────────────────────────
let _seatingDone = false;
let _arucoDone   = false;
let _examActive  = false;
let _arucoPolling = null;

// ── Clock ──────────────────────────────────────────────────────────────────
function updateClock() {
  document.getElementById('clock').textContent =
    new Date().toLocaleTimeString('en-IN', {hour12:false});
}
setInterval(updateClock, 1000); updateClock();

// ── Workflow steps UI ──────────────────────────────────────────────────────
function updateSteps() {
  const s1 = document.getElementById('step1');
  const s2 = document.getElementById('step2');
  const s3 = document.getElementById('step3');

  s1.className = 'step ' + (_seatingDone ? 'done' : 'active');
  s2.className = 'step ' + (_arucoDone   ? 'done' :
                             _seatingDone ? 'active' : 'pending');
  s3.className = 'step ' + (_examActive  ? 'done' :
                             _arucoDone  ? 'active' : 'pending');

  document.getElementById('btn-aruco').disabled = !_seatingDone || _examActive;
  document.getElementById('btn-start').disabled = !_arucoDone  || _examActive;
  document.getElementById('btn-stop').disabled  = !_examActive;
}

// ── Upload seating Excel ───────────────────────────────────────────────────
async function uploadSeating(input) {
  if (!input.files.length) return;
  const formData = new FormData();
  formData.append('file', input.files[0]);

  document.getElementById('seating-status').textContent = 'Uploading...';
  document.getElementById('seating-status').style.color = '#58a6ff';

  try {
    const r = await fetch('/api/seating/upload', {method:'POST', body:formData});
    const d = await r.json();

    if (!d.ok) {
      document.getElementById('seating-status').textContent = 'Error: ' + d.error;
      document.getElementById('seating-status').style.color = '#f85149';
      return;
    }

    _seatingDone = true;
    document.getElementById('seating-status').textContent =
      Object.keys(d.seating).length + ' students assigned';
    document.getElementById('seating-status').style.color = '#3fb950';

    // Fill seating table + bench name cards
    const tbody = document.getElementById('seating-tbody');
    tbody.innerHTML = Object.entries(d.seating).map(([bench, s]) =>
      `<tr>
        <td><b>${bench}</b></td>
        <td>${s.student_name}</td>
        <td style="color:#484f58">${s.roll_number}</td>
      </tr>`
    ).join('');

    // Update bench name cards
    Object.entries(d.seating).forEach(([bench, s]) => {
      const nameEl = document.getElementById('name-' + bench);
      const rollEl = document.getElementById('roll-' + bench);
      if (nameEl) {
        nameEl.textContent = s.student_name;
        nameEl.classList.remove('awaiting');
      }
      if (rollEl) rollEl.textContent = s.roll_number;
    });

    updateSteps();
  } catch(e) {
    document.getElementById('seating-status').textContent = 'Upload failed';
    document.getElementById('seating-status').style.color = '#f85149';
  }
  input.value = '';
}

// ── Scan ArUco ────────────────────────────────────────────────────────────
async function startAruco() {
  const msgEl = document.getElementById('aruco-msg');
  msgEl.className = 'aruco-status scanning';
  msgEl.innerHTML = '<span class="progress-dots">Scanning</span>';

  await fetch('/api/aruco/start', {method:'POST'});

  // Poll status every 500ms
  _arucoPolling = setInterval(async () => {
    try {
      const r = await fetch('/api/aruco/status');
      const d = await r.json();

      if (d.error) {
        msgEl.className = 'aruco-status error';
        msgEl.textContent = '✗ ' + d.error;
        clearInterval(_arucoPolling);
        return;
      }

      if (d.running) {
        msgEl.innerHTML = `<span class="progress-dots">${d.message || 'Scanning'}</span>`;
        return;
      }

      if (d.done) {
        clearInterval(_arucoPolling);
        msgEl.className = 'aruco-status done';
        msgEl.textContent = d.message;
        _arucoDone = true;

        // Update seating table with zone info
        if (d.zones) {
          Object.entries(d.zones).forEach(([bench, info]) => {
            const nameEl = document.getElementById('name-' + bench);
            if (nameEl && info.student && info.student !== 'Unknown') {
              nameEl.textContent = info.student;
              nameEl.classList.remove('awaiting');
            }
          });
        }
        updateSteps();
      }
    } catch(e) {}
  }, 500);
}

// ── Exam control ──────────────────────────────────────────────────────────
async function startExam() {
  const r = await fetch('/api/exam/start', {method:'POST'});
  const d = await r.json();
  if (!d.ok) { alert('Start failed: ' + d.error); return; }
  _examActive = true;
  updateSteps();
}

async function stopExam() {
  if (!confirm('Stop the exam? This will generate an Excel report.')) return;
  const r = await fetch('/api/exam/stop', {method:'POST'});
  const d = await r.json();
  _examActive = false;
  updateSteps();
  document.getElementById('exam-timer').className = 'exam-timer';
  document.getElementById('exam-label').textContent = 'EXAM ENDED';
  // Auto-download Excel report
  if (d.report_ready && d.report_file) {
    const a = document.createElement('a');
    a.href = '/api/report/download/' + encodeURIComponent(d.report_file);
    a.download = d.report_file;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }
}

// ── Exam timer ─────────────────────────────────────────────────────────────
function fmtSec(s) {
  return [Math.floor(s/3600), Math.floor((s%3600)/60), s%60]
    .map(n => String(n).padStart(2,'0')).join(':');
}

// ── Fetch live status ──────────────────────────────────────────────────────
async function fetchStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    const age  = Date.now()/1000 - (d.last_update || 0);
    const live = d.running && age < 8;  // FIX: was 3, low FPS caused flicker

    document.getElementById('dot').style.background = live ? '#3fb950' : '#484f58';
    document.getElementById('status-text').textContent =
      live ? 'Detection Running' : 'Standby';
    document.getElementById('fps-tag').textContent =
      d.fps ? d.fps + ' FPS' : '';

    const badge = document.getElementById('det-badge');
    badge.textContent  = live ? 'DETECTION ACTIVE' : 'DETECTION OFFLINE';
    badge.className    = 'det-badge ' + (live ? 'on' : 'off');

    // Exam timer
    if (d.exam_active) {
      _examActive = true;
      document.getElementById('exam-timer').className = 'exam-timer on';
      document.getElementById('exam-timer').textContent = fmtSec(d.exam_elapsed || 0);
      document.getElementById('exam-label').textContent = 'EXAM IN PROGRESS';
      document.getElementById('exam-label').style.color = '#3fb950';
      updateSteps();
    }

    // Bench cards
    const benches = d.benches || {};
    ['B1','B2','B3'].forEach(b => {
      const info  = benches[b] || {};
      const score = info.score || 0;
      const conf  = info.ml_confidence || 0;
      const name  = info.student_name || null;
      const pct   = Math.min(100, (score / MAX_SCORE) * 100);

      if (name) {
        const nameEl = document.getElementById('name-' + b);
        if (nameEl) {
          nameEl.textContent = name;
          nameEl.classList.remove('awaiting');
        }
      }

      const bar = document.getElementById('bar-' + b);
      if (bar) {
        bar.style.width = pct + '%';
        bar.className = 'bar' +
          (score >= ALERT_THR ? ' alert' : score >= WARN_THR ? ' warn' : '');
      }

      const scoreEl = document.getElementById('score-' + b);
      if (scoreEl) scoreEl.textContent = score + ' / ' + MAX_SCORE;

      const confEl = document.getElementById('conf-' + b);
      if (confEl) confEl.textContent = conf ? (conf*100).toFixed(0)+'% conf' : '—';

      const card = document.getElementById('bench-' + b);
      if (card) card.className = 'bench-card' +
        (score >= ALERT_THR ? ' alert' : score >= WARN_THR ? ' warning' : '');
    });
  } catch(e) {}
}

// ── Fetch alerts ───────────────────────────────────────────────────────────
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
    document.getElementById('s-benches').textContent  =
      (summary.benches_flagged || []).length;

    const tbody = document.getElementById('alert-tbody');
    if (!alerts.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;' +
        'color:#484f58;padding:16px;">No incidents yet</td></tr>';
      return;
    }
    tbody.innerHTML = alerts.slice(0,50).map(a => {
      const sc = a.score;
      const bc = sc >= ALERT_THR ? 'red' : sc >= WARN_THR ? 'yellow' : 'green';
      const rv = a.reviewed
        ? '<span class="reviewed">✓</span>'
        : `<button class="btn-rev" onclick="markReviewed(${a.id})">Review</button>`;
      return `<tr>
        <td>#${a.id}</td><td>${a.time}</td><td><b>${a.bench}</b></td>
        <td style="max-width:80px;overflow:hidden;text-overflow:ellipsis;">
          ${a.student_name}</td>
        <td><span class="badge ${bc}">${sc}</span></td>
        <td>${(a.ml_confidence*100).toFixed(0)}%</td>
        <td>${rv}</td></tr>`;
    }).join('');

    document.getElementById('log-ts').textContent =
      new Date().toLocaleTimeString('en-IN',{hour12:false});
  } catch(e) {}
}

async function markReviewed(id) {
  await fetch('/api/reviewed/' + id, {method:'POST'});
  fetchAlerts();
}

async function clearSession() {
  if (!confirm('Clear all incidents for this session?')) return;
  await fetch('/api/clear', {method:'POST'});
  fetchAlerts(); fetchStatus();
}

// ── Load existing seating on page load ────────────────────────────────────
async function loadExistingSeating() {
  try {
    const r = await fetch('/api/seating');
    const d = await r.json();
    if (!Object.keys(d).length) return;

    _seatingDone = true;
    document.getElementById('seating-status').textContent =
      Object.keys(d).length + ' students assigned';
    document.getElementById('seating-status').style.color = '#3fb950';

    const tbody = document.getElementById('seating-tbody');
    tbody.innerHTML = Object.entries(d).map(([bench, s]) =>
      `<tr><td><b>${bench}</b></td><td>${s.student_name}</td>
       <td style="color:#484f58">${s.roll_number}</td></tr>`
    ).join('');

    Object.entries(d).forEach(([bench, s]) => {
      const nameEl = document.getElementById('name-' + bench);
      const rollEl = document.getElementById('roll-' + bench);
      if (nameEl) { nameEl.textContent = s.student_name; nameEl.classList.remove('awaiting'); }
      if (rollEl) rollEl.textContent = s.roll_number;
    });

    updateSteps();
  } catch(e) {}
}

// ── Init ───────────────────────────────────────────────────────────────────
loadExistingSeating();
updateSteps();
fetchStatus();
fetchAlerts();
setInterval(fetchStatus, 1000);
setInterval(fetchAlerts, 2000);
</script>
</body>
</html>
"""


@app.route("/api/report/download/<path:filename>")
def download_report(filename):
    """Serve Excel report file for download."""
    reports_dir = os.path.join(_ROOT, "reports")
    return send_from_directory(reports_dir, filename, as_attachment=True)


@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 55)
    print("  ARGUS — File 11: Dashboard v10")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("=" * 55)
    print("\n  Open: http://localhost:5000")
    print("\n  Teacher workflow:")
    print("    1. Upload ARGUS_Seating.xlsx")
    print("    2. Place ArUco markers → Click Scan ArUco")
    print("    3. Click Start Exam")
    print("\n  Press Ctrl+C to stop.\n")

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)

    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
