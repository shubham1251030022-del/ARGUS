"""
ARGUS — File 11: webapp/app.py  [DEMO READY v7]
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

v7 fixes over v6:
  1. Hex grid much brighter — visible on dark background
  2. Toast notifications repositioned — no longer overlap right panel
  3. Toast stacks downward cleanly without covering UI
  4. Button icons render correctly

Run: py -3.11 webapp/app.py  (from ARGUS root)
"""

import os, sys, json, threading, time, subprocess, io
from datetime import datetime
from flask import (Flask, Response, jsonify, request,
                   render_template_string, send_from_directory, send_file)

_THIS_DIR        = os.path.dirname(os.path.abspath(__file__))
_ROOT            = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _ROOT)
from webapp.alert_logger import AlertLogger

SNAPSHOT_DIR     = os.path.join(_ROOT, "snapshots")
REPORTS_DIR      = os.path.join(_ROOT, "reports")
DETECTION_SCRIPT = os.path.join(_ROOT, "detection", "main.py")
ARUCO_SCRIPT     = os.path.join(_ROOT, "detection", "aruco_scanner.py")
CONFIG_FILE      = os.path.join(_ROOT, "detection", "config.json")
ZONES_FILE       = os.path.join(_ROOT, "detection", "zones.json")

app    = Flask(__name__)
logger = AlertLogger()

def get_threshold():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f).get("threshold", 100)
    except:
        return 100

_detection_process = None
_aruco_process     = None
_process_lock      = threading.Lock()
_state_lock        = threading.Lock()
_live_state = {
    "benches":{}, "frame_count":0, "fps":0, "running":False,
    "last_update":0, "exam_active":False, "exam_start":None,
    "exam_start_ts":None, "aruco_scanning":False, "aruco_done":False,
}
_frame_lock   = threading.Lock()
_latest_frame = None

def is_detection_running():
    global _detection_process
    with _process_lock:
        return _detection_process is not None and _detection_process.poll() is None

def start_detection():
    global _detection_process
    with _process_lock:
        if _detection_process and _detection_process.poll() is None:
            _detection_process.terminate(); _detection_process.wait(timeout=3)
        cmd = f"py -3.11 {DETECTION_SCRIPT}"
        _detection_process = subprocess.Popen(
            cmd, shell=True, cwd=_ROOT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        return _detection_process.pid

def stop_detection():
    global _detection_process
    with _process_lock:
        if _detection_process and _detection_process.poll() is None:
            try:
                _detection_process.terminate(); _detection_process.wait(timeout=5)
            except:
                try: _detection_process.kill()
                except: pass
            _detection_process = None
            return True
        return False

def start_aruco_scan():
    global _aruco_process
    def _run():
        global _aruco_process
        with _state_lock:
            _live_state["aruco_scanning"] = True
            _live_state["aruco_done"]     = False
        _aruco_process = subprocess.Popen(
            f"py -3.11 {ARUCO_SCRIPT}", shell=True, cwd=_ROOT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        _aruco_process.wait()
        with _state_lock:
            _live_state["aruco_scanning"] = False
            _live_state["aruco_done"]     = True
    threading.Thread(target=_run, daemon=True).start()

def generate_excel(alerts, summary, exam_start, exam_end):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = openpyxl.Workbook(); ws = wb.active
    ws.title = "ARGUS Report"
    for col,w in zip('ABCDEFGH',[5,10,8,28,14,8,8,10]):
        ws.column_dimensions[col].width = w
    hf = PatternFill("solid",fgColor="0D1117")
    tf = PatternFill("solid",fgColor="161B22")
    rf = PatternFill("solid",fgColor="2D1117")
    r=1
    ws.merge_cells(f'A{r}:H{r}')
    c=ws[f'A{r}']; c.value="ARGUS — Exam Malpractice Detection Report"
    c.font=Font(bold=True,color="00D4FF",size=13); c.fill=hf
    c.alignment=Alignment(horizontal='center'); ws.row_dimensions[r].height=26; r+=1
    ws.merge_cells(f'A{r}:H{r}')
    c=ws[f'A{r}']; c.value="VIT Pune | CSAIML-E | Group 01"
    c.font=Font(color="8B949E",size=9); c.fill=hf
    c.alignment=Alignment(horizontal='center'); r+=2
    ws.merge_cells(f'A{r}:H{r}')
    ws[f'A{r}'].value="SESSION SUMMARY"
    ws[f'A{r}'].font=Font(bold=True,color="00D4FF",size=9)
    ws[f'A{r}'].fill=tf; r+=1
    for label,value in [
        ("Exam Started",exam_start or"—"),("Exam Ended",exam_end or"—"),
        ("Total Alerts",str(summary.get("total_alerts",0))),
        ("Students Flagged",str(summary.get("unique_students",0))),
        ("Peak Score",str(summary.get("highest_score",0))),
        ("Benches Flagged",", ".join(summary.get("benches_flagged",[])))]:
        ws[f'A{r}'].value=label; ws[f'A{r}'].font=Font(color="8B949E",size=9)
        ws.merge_cells(f'B{r}:H{r}')
        ws[f'B{r}'].value=value; ws[f'B{r}'].font=Font(color="E6EDF3",size=9,bold=True); r+=1
    r+=1
    for ci,h in enumerate(["#","Time","Bench","Student","Roll No","Score","Conf","Reviewed"],1):
        c=ws.cell(row=r,column=ci,value=h)
        c.font=Font(bold=True,color="8B949E",size=8); c.fill=tf
        c.alignment=Alignment(horizontal='center')
    r+=1
    for a in alerts:
        vals=[a.get("id",""),a.get("time",""),a.get("bench",""),
              a.get("student_name",""),a.get("roll_number",""),
              a.get("score",0),f"{a.get('ml_confidence',0)*100:.0f}%",
              "Yes" if a.get("reviewed") else "No"]
        is_hi=a.get("score",0)>=80
        for ci,v in enumerate(vals,1):
            c=ws.cell(row=r,column=ci,value=v)
            c.alignment=Alignment(horizontal='center')
            if is_hi:
                c.fill=rf; c.font=Font(color="F85149" if ci in[3,4,6] else "E6EDF3",size=9)
            else:
                c.font=Font(color="E6EDF3",size=9)
        r+=1
    buf=io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf

def generate_pdf(alerts, summary, exam_start, exam_end, filename):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate,Table,TableStyle,Paragraph,Spacer
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    os.makedirs(REPORTS_DIR,exist_ok=True)
    path=os.path.join(REPORTS_DIR,filename)
    doc=SimpleDocTemplate(path,pagesize=A4,leftMargin=15*mm,rightMargin=15*mm,
                          topMargin=15*mm,bottomMargin=15*mm)
    CYAN=colors.HexColor("#00D4FF"); MUTED=colors.HexColor("#8B949E")
    WHITE=colors.HexColor("#E6EDF3"); DARK=colors.HexColor("#161B22")
    RED=colors.HexColor("#FF6B35"); BG=colors.HexColor("#0D1117")
    title_s=ParagraphStyle('t',fontSize=17,textColor=CYAN,fontName='Helvetica-Bold',spaceAfter=4)
    sub_s=ParagraphStyle('s',fontSize=9,textColor=MUTED,fontName='Helvetica',spaceAfter=12)
    sec_s=ParagraphStyle('h',fontSize=10,textColor=CYAN,fontName='Helvetica-Bold',spaceAfter=6)
    story=[]
    story.append(Paragraph("ARGUS — Exam Malpractice Detection Report",title_s))
    story.append(Paragraph("VIT Pune | CSAIML-E | Group 01",sub_s))
    story.append(Paragraph("SESSION SUMMARY",sec_s))
    sum_data=[
        ["Exam Started",exam_start or"—","Total Alerts",str(summary.get("total_alerts",0))],
        ["Exam Ended",exam_end or"—","Students Flagged",str(summary.get("unique_students",0))],
        ["Peak Score",str(summary.get("highest_score",0)),"Benches Flagged",
         ", ".join(summary.get("benches_flagged",[]))],
    ]
    st=Table(sum_data,colWidths=[40*mm,45*mm,45*mm,45*mm])
    st.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),DARK),
        ('TEXTCOLOR',(0,0),(0,-1),MUTED),('TEXTCOLOR',(2,0),(2,-1),MUTED),
        ('TEXTCOLOR',(1,0),(1,-1),WHITE),('TEXTCOLOR',(3,0),(3,-1),WHITE),
        ('FONTNAME',(0,0),(-1,-1),'Helvetica'),
        ('FONTNAME',(1,0),(1,-1),'Helvetica-Bold'),
        ('FONTNAME',(3,0),(3,-1),'Helvetica-Bold'),
        ('FONTSIZE',(0,0),(-1,-1),9),
        ('GRID',(0,0),(-1,-1),0.5,colors.HexColor("#21262D")),
        ('PADDING',(0,0),(-1,-1),6),
    ]))
    story.append(st); story.append(Spacer(1,8*mm))
    story.append(Paragraph("ALERT LOG",sec_s))
    headers=["#","Time","Bench","Student Name","Roll Number","Score","ML Conf","Reviewed"]
    data=[headers]+[[str(a.get("id","")),a.get("time",""),a.get("bench",""),
        a.get("student_name",""),a.get("roll_number",""),str(a.get("score",0)),
        f"{a.get('ml_confidence',0)*100:.0f}%","Yes" if a.get("reviewed") else "No"]
        for a in alerts]
    col_w=[10*mm,18*mm,15*mm,45*mm,28*mm,14*mm,16*mm,18*mm]
    tbl=Table(data,colWidths=col_w,repeatRows=1)
    tstyle=[
        ('BACKGROUND',(0,0),(-1,0),DARK),('TEXTCOLOR',(0,0),(-1,0),MUTED),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('FONTSIZE',(0,0),(-1,-1),8),('FONTNAME',(0,1),(-1,-1),'Helvetica'),
        ('TEXTCOLOR',(0,1),(-1,-1),WHITE),('BACKGROUND',(0,1),(-1,-1),BG),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[BG,DARK]),
        ('GRID',(0,0),(-1,-1),0.3,colors.HexColor("#21262D")),
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('ALIGN',(3,0),(3,-1),'LEFT'),('ALIGN',(4,0),(4,-1),'LEFT'),
        ('PADDING',(0,0),(-1,-1),4),
    ]
    for i,a in enumerate(alerts,1):
        if a.get("score",0)>=80:
            tstyle.append(('BACKGROUND',(0,i),(-1,i),colors.HexColor("#1A0D0D")))
            tstyle.append(('TEXTCOLOR',(5,i),(5,i),RED))
    tbl.setStyle(TableStyle(tstyle))
    story.append(tbl)
    doc.build(story)
    return path

@app.route("/api/seating/rooms", methods=["POST"])
def get_rooms():
    if "file" not in request.files:
        return jsonify({"ok":False,"msg":"No file"}),400
    f=request.files["file"]
    try:
        import pandas as pd
        df=pd.read_excel(io.BytesIO(f.read()))
        rc=next((c for c in df.columns if "room" in c.lower()),None)
        bc=next((c for c in df.columns if "bench" in c.lower() and "side" not in c.lower()),None)
        nc=next((c for c in df.columns if "name" in c.lower() or "candidate" in c.lower()),None)
        pc=next((c for c in df.columns if "prn" in c.lower() or "roll" in c.lower()),None)
        if not all([rc,bc,nc,pc]):
            return jsonify({"ok":False,"msg":f"Columns not found. Got:{list(df.columns)}"}),400
        rooms=sorted(df[rc].dropna().unique().tolist())
        df.to_json("/tmp/seating_upload.json",orient="records")
        return jsonify({"ok":True,"rooms":[str(r) for r in rooms],
                        "room_col":rc,"bench_col":bc,"name_col":nc,"prn_col":pc})
    except Exception as e:
        return jsonify({"ok":False,"msg":str(e)}),500

@app.route("/api/seating/assign", methods=["POST"])
def assign_seating():
    data=request.get_json(silent=True) or {}
    try:
        import pandas as pd
        df=pd.read_json("/tmp/seating_upload.json")
        room=str(data.get("room",""))
        bench_map=data.get("bench_map",{})
        rc,bc,nc,pc=data.get("room_col"),data.get("bench_col"),data.get("name_col"),data.get("prn_col")
        room_df=df[df[rc].astype(str)==room]
        if room_df.empty:
            return jsonify({"ok":False,"msg":f"Room {room} not found"}),400
        try:
            with open(ZONES_FILE) as f: zones=json.load(f)
        except: zones={}
        updated=[]
        for ab,ebn in bench_map.items():
            row=room_df[room_df[bc].astype(str)==str(ebn)]
            if row.empty: continue
            row=row.iloc[0]
            name,prn=str(row[nc]),str(row[pc])
            if ab in zones:
                zones[ab]["student_name"]=name; zones[ab]["roll_number"]=prn
                updated.append(f"{ab} -> {name} ({prn})")
        with open(ZONES_FILE,"w") as f: json.dump(zones,f,indent=2)
        return jsonify({"ok":True,"updated":updated})
    except Exception as e:
        return jsonify({"ok":False,"msg":str(e)}),500

@app.route("/api/zones",methods=["GET"])
def get_zones():
    try:
        with open(ZONES_FILE) as f: return jsonify(json.load(f))
    except: return jsonify({})

def _generate_stream():
    while True:
        with _frame_lock: frame=_latest_frame
        if frame:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"+frame+b"\r\n\r\n"
            time.sleep(0.05)
        else: time.sleep(0.1)

@app.route("/video_feed")
def video_feed():
    return Response(_generate_stream(),mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/api/frame",methods=["POST"])
def push_frame():
    global _latest_frame
    with _frame_lock: _latest_frame=request.data
    return jsonify({"ok":True})

@app.route("/api/status",methods=["GET"])
def get_status():
    with _state_lock: state=dict(_live_state)
    state["detection_running"]=is_detection_running()
    state["threshold"]=get_threshold()
    return jsonify(state)

@app.route("/api/status",methods=["POST"])
def push_status():
    data=request.get_json(silent=True) or {}
    with _state_lock:
        _live_state.update(data); _live_state["last_update"]=time.time()
    return jsonify({"ok":True})

@app.route("/api/exam/start",methods=["POST"])
def start_exam():
    logger.clear_session()
    pid=start_detection()
    now=time.strftime("%H:%M:%S")
    with _state_lock:
        _live_state["exam_active"]=True; _live_state["exam_start"]=now
        _live_state["exam_start_ts"]=time.strftime("%Y-%m-%d %H:%M:%S")
        _live_state["running"]=True
    return jsonify({"ok":True,"pid":pid,"started":now})

@app.route("/api/exam/stop",methods=["POST"])
def stop_exam():
    stopped=stop_detection()
    exam_end=time.strftime("%H:%M:%S")
    alerts=logger.get_all(); summary=logger.get_summary()
    with _state_lock:
        exam_start=_live_state.get("exam_start","--")
        _live_state["exam_active"]=False; _live_state["running"]=False
        _live_state["benches"]={}
    ts=datetime.now().strftime("%Y-%m-%d_%H-%M")
    pdf_name=f"ARGUS_Report_{ts}.pdf"
    try: generate_pdf(alerts,summary,exam_start,exam_end,pdf_name)
    except Exception as e: print(f"[PDF error] {e}")
    try:
        excel_buf=generate_excel(alerts,summary,exam_start,exam_end)
        return send_file(excel_buf,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,download_name=f"ARGUS_Report_{ts}.xlsx")
    except Exception as e:
        return jsonify({"ok":True,"stopped":stopped,"summary":summary,"error":str(e)})

@app.route("/api/exam/status",methods=["GET"])
def exam_status():
    return jsonify({"detection_running":is_detection_running(),
                    "exam_active":_live_state.get("exam_active",False),
                    "exam_start":_live_state.get("exam_start")})

@app.route("/api/aruco/scan",methods=["POST"])
def aruco_scan():
    if _live_state.get("aruco_scanning"):
        return jsonify({"ok":False,"msg":"Already scanning"})
    start_aruco_scan()
    return jsonify({"ok":True})

@app.route("/api/aruco/status",methods=["GET"])
def aruco_status():
    return jsonify({"scanning":_live_state.get("aruco_scanning",False),
                    "done":_live_state.get("aruco_done",False)})

@app.route("/api/alerts",methods=["GET"])
def get_alerts(): return jsonify(logger.get_all())

@app.route("/api/alert",methods=["POST"])
def post_alert():
    data=request.get_json(silent=True) or {}
    alert=logger.log_alert(bench=data.get("bench",""),
        student_name=data.get("student_name","Unknown"),
        roll_number=data.get("roll_number",""),score=data.get("score",0),
        ml_confidence=data.get("ml_confidence",0.0),flags=data.get("flags",{}),
        snapshot_path=data.get("snapshot_path",""))
    return jsonify(alert),201

@app.route("/api/summary",methods=["GET"])
def get_summary(): return jsonify(logger.get_summary())

@app.route("/api/reviewed/<int:alert_id>",methods=["POST"])
def mark_reviewed(alert_id):
    return jsonify({"ok":logger.mark_reviewed(alert_id),"id":alert_id})

@app.route("/api/clear",methods=["POST"])
def clear_session():
    logger.clear_session()
    with _state_lock:
        _live_state["benches"]={}; _live_state["frame_count"]=0
    return jsonify({"ok":True})

@app.route("/snapshots/<path:filename>")
def serve_snapshot(filename): return send_from_directory(SNAPSHOT_DIR,filename)

@app.route("/api/reports",methods=["GET"])
def list_reports():
    os.makedirs(REPORTS_DIR,exist_ok=True)
    files=[]
    for f in sorted(os.listdir(REPORTS_DIR),reverse=True):
        if f.endswith(".pdf"):
            p=os.path.join(REPORTS_DIR,f)
            files.append({"filename":f,
                "date":datetime.fromtimestamp(os.path.getmtime(p)).strftime("%d %b %Y, %H:%M"),
                "size_kb":round(os.path.getsize(p)/1024,1)})
    return jsonify(files)

@app.route("/reports/<path:filename>")
def serve_report(filename): return send_from_directory(REPORTS_DIR,filename)


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ARGUS — Exam Monitor</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;600;700;900&family=Share+Tech+Mono&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg:    #04080f;
  --bg2:   #080e1a;
  --bg3:   #0c1422;
  --bg4:   #101c2e;
  --cyan:  #00d4ff;
  --cyan2: #0099cc;
  --green: #00ff88;
  --amber: #ffaa00;
  --red:   #ff2d55;
  --muted: #4a6080;
  --text:  #cce4ff;
  --dim:   #1e3050;
  --bord:  #0f2040;
}
* { margin:0; padding:0; box-sizing:border-box; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Rajdhani', sans-serif;
  height: 100vh; overflow: hidden;
  display: flex; flex-direction: column;
}

/* HEX GRID — bright enough to see */
body::before {
  content: '';
  position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='56' height='48'%3E%3Cpolygon points='28,2 54,16 54,44 28,58 2,44 2,16' fill='none' stroke='%23163060' stroke-width='1.2'/%3E%3C/svg%3E");
  background-size: 56px 48px;
  opacity: 0.9;
}

/* Gradient vignette on top of grid */
body::after {
  content: '';
  position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background: radial-gradient(ellipse at 50% 0%, rgba(0,212,255,0.06) 0%, transparent 65%),
              radial-gradient(ellipse at 100% 100%, rgba(0,255,136,0.03) 0%, transparent 50%);
}

.header, .ctrl, .layout, #toast-zone { position: relative; z-index: 1; }

/* HEADER */
.header {
  background: linear-gradient(90deg, #050c1a 0%, #091526 50%, #050c1a 100%);
  border-bottom: 1px solid #0d2a50;
  padding: 0 20px; height: 58px;
  display: flex; align-items: center; justify-content: space-between;
  box-shadow: 0 2px 30px rgba(0,212,255,0.12);
  flex-shrink: 0;
}
.logo { display: flex; align-items: center; gap: 12px; }
.logo-hex {
  width: 38px; height: 38px;
  background: linear-gradient(135deg, #00aadd, #00d4ff);
  clip-path: polygon(50% 0%,100% 25%,100% 75%,50% 100%,0% 75%,0% 25%);
  display: flex; align-items: center; justify-content: center;
  font-family: 'Orbitron', monospace; font-weight: 900;
  font-size: 0.9rem; color: #000;
  box-shadow: 0 0 20px rgba(0,212,255,0.6);
  animation: hexGlow 3s ease-in-out infinite;
}
@keyframes hexGlow {
  0%,100% { box-shadow: 0 0 15px rgba(0,212,255,0.5); }
  50%      { box-shadow: 0 0 35px rgba(0,212,255,1); }
}
.logo-name { font-family: 'Orbitron', monospace; font-weight: 900;
  font-size: 1.25rem; letter-spacing: 4px; color: var(--cyan);
  text-shadow: 0 0 25px rgba(0,212,255,0.6); }
.logo-sub  { font-family: 'Share Tech Mono', monospace;
  font-size: 0.58rem; color: var(--muted); letter-spacing: 2px; margin-top: 1px; }

.hdr-center { position: absolute; left: 50%; transform: translateX(-50%);
  text-align: center; }
.sys-lbl { font-family: 'Orbitron', monospace; font-size: 0.52rem;
  color: var(--muted); letter-spacing: 3px; }
.sys-val { font-family: 'Share Tech Mono', monospace; font-size: 0.78rem;
  color: var(--cyan); letter-spacing: 1px; margin-top: 1px; }

.hdr-right { display: flex; align-items: center; gap: 14px; }
.live-pill { display: flex; align-items: center; gap: 7px;
  background: var(--bg3); border: 1px solid var(--bord);
  border-radius: 4px; padding: 5px 13px;
  font-family: 'Share Tech Mono', monospace; font-size: 0.72rem; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
.dot.live { background: var(--green); box-shadow: 0 0 10px var(--green);
  animation: blink 1.4s infinite; }
.dot.scan { background: var(--cyan); box-shadow: 0 0 10px var(--cyan);
  animation: blink 0.7s infinite; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.25} }
.clock { font-family: 'Orbitron', monospace; font-size: 0.9rem;
  color: var(--cyan); font-weight: 600; letter-spacing: 2px; }

/* CTRL BAR */
.ctrl {
  background: var(--bg2); border-bottom: 1px solid var(--bord);
  padding: 8px 20px; display: flex; align-items: center;
  gap: 8px; flex-shrink: 0; flex-wrap: wrap;
}
.btn {
  font-family: 'Rajdhani', sans-serif; font-weight: 700;
  font-size: 0.78rem; letter-spacing: 1px; text-transform: uppercase;
  padding: 7px 16px; border-radius: 3px; border: none;
  cursor: pointer; transition: all 0.18s;
  display: flex; align-items: center; gap: 6px;
}
.btn:active { transform: scale(0.96); }
.btn:disabled { opacity: 0.3; cursor: not-allowed; }
.btn-start { background: linear-gradient(135deg,#00cc66,#00ff88); color:#000;
  box-shadow: 0 0 14px rgba(0,255,136,0.35); }
.btn-start:hover:not(:disabled) { box-shadow: 0 0 28px rgba(0,255,136,0.7); }
.btn-stop  { background: linear-gradient(135deg,#cc001a,#ff2d55); color:#fff;
  box-shadow: 0 0 14px rgba(255,45,85,0.35); }
.btn-stop:hover:not(:disabled)  { box-shadow: 0 0 28px rgba(255,45,85,0.7); }
.btn-aruco { background: linear-gradient(135deg,#0077bb,#00d4ff); color:#000;
  box-shadow: 0 0 14px rgba(0,212,255,0.35); }
.btn-aruco:hover:not(:disabled) { box-shadow: 0 0 28px rgba(0,212,255,0.7); }
.btn-ghost { background: transparent; color: var(--muted);
  border: 1px solid var(--bord); }
.btn-ghost:hover { border-color: var(--cyan); color: var(--cyan);
  box-shadow: 0 0 10px rgba(0,212,255,0.15); }
.btn-del   { background: transparent; color: var(--muted);
  border: 1px solid var(--bord); }
.btn-del:hover { border-color: var(--red); color: var(--red); }

.etag {
  margin-left: 6px; padding: 5px 16px; border-radius: 3px;
  font-family: 'Orbitron', monospace; font-size: 0.6rem;
  font-weight: 700; letter-spacing: 2px; border: 1px solid;
  transition: all 0.3s;
}
.etag-idle   { color: var(--muted); border-color: var(--bord); }
.etag-active { color: var(--green); border-color: var(--green);
  background: rgba(0,255,136,0.05);
  box-shadow: 0 0 12px rgba(0,255,136,0.2);
  animation: tagPulse 2s infinite; }
.etag-ended  { color: #ff6b35; border-color: #ff6b35;
  background: rgba(255,107,53,0.05); }
.etag-scan   { color: var(--cyan); border-color: var(--cyan);
  background: rgba(0,212,255,0.05); animation: tagPulse 0.8s infinite; }
@keyframes tagPulse { 0%,100%{opacity:1} 50%{opacity:0.55} }
.etime { font-family:'Share Tech Mono',monospace; font-size:0.68rem; color:var(--muted); }

/* LAYOUT */
.layout {
  display: grid; grid-template-columns: 1fr 350px;
  gap: 10px; padding: 10px;
  flex: 1; overflow: hidden; min-height: 0;
}
.col-l { display:flex; flex-direction:column; gap:10px; overflow:hidden; min-height:0; }
.col-r { display:flex; flex-direction:column; gap:10px; overflow:hidden; min-height:0; }

/* PANEL */
.panel {
  background: rgba(8,14,26,0.92);
  border: 1px solid #0d2040;
  border-radius: 4px; padding: 12px;
  flex-shrink: 0; position: relative; overflow: hidden;
  backdrop-filter: blur(4px);
}
.panel::before {
  content:''; position:absolute; top:0; left:0; right:0; height:1px;
  background: linear-gradient(90deg,transparent,#0d4070,transparent);
}
.panel.fill { flex:1; display:flex; flex-direction:column; overflow:hidden; min-height:0; }
.phdr {
  font-family:'Orbitron',monospace; font-size:0.58rem; color:var(--muted);
  letter-spacing:2px; text-transform:uppercase; margin-bottom:10px;
  display:flex; justify-content:space-between; align-items:center;
}
.phdr span { color: var(--cyan2); }

/* FEED */
.feed-wrap {
  background: #010408; border-radius:3px;
  flex:1; display:flex; align-items:center; justify-content:center;
  min-height:0; overflow:hidden; position:relative;
}
.feed-wrap::after {
  content:''; position:absolute; inset:0; z-index:2; pointer-events:none;
  background: repeating-linear-gradient(
    0deg, transparent, transparent 3px,
    rgba(0,0,0,0.07) 3px, rgba(0,0,0,0.07) 4px);
}
/* corner brackets */
.fc { position:absolute; inset:10px; z-index:3; pointer-events:none; }
.fc::before,.fc::after { content:''; position:absolute;
  width:22px; height:22px; border-color:var(--cyan); border-style:solid; }
.fc::before { top:0; left:0; border-width:2px 0 0 2px; }
.fc::after  { bottom:0; right:0; border-width:0 2px 2px 0; }
.fc2::before { top:0; right:0; border-width:2px 2px 0 0; }
.fc2::after  { bottom:0; left:0; border-width:0 0 2px 2px; }

.feed-wrap img { width:100%; height:100%; object-fit:contain; z-index:1; }
.feed-off {
  position:absolute; z-index:4; display:flex;
  flex-direction:column; align-items:center; gap:12px;
}
.feed-off-ico { font-size:2.5rem; opacity:0.2; }
.feed-off-txt { font-family:'Share Tech Mono',monospace; font-size:0.72rem;
  color:var(--muted); letter-spacing:2px; }

.rec { position:absolute; top:16px; right:16px; z-index:5;
  display:none; align-items:center; gap:5px;
  background:rgba(255,45,85,0.12); border:1px solid var(--red);
  border-radius:3px; padding:3px 10px;
  font-family:'Orbitron',monospace; font-size:0.52rem;
  color:var(--red); letter-spacing:2px; }
.rec.on { display:flex; }
.rec-d { width:7px; height:7px; border-radius:50%;
  background:var(--red); animation:blink 1s infinite; }

.scan-ov {
  display:none; position:absolute; inset:0; z-index:10;
  background:rgba(0,15,30,0.75); flex-direction:column;
  align-items:center; justify-content:center; gap:14px;
}
.scan-ov.on { display:flex; }
.spin-ring { width:56px; height:56px; border-radius:50%;
  border:2px solid transparent;
  border-top-color:var(--cyan); border-right-color:var(--cyan);
  animation:spin 0.8s linear infinite; }
@keyframes spin { to{transform:rotate(360deg)} }
.spin-txt { font-family:'Orbitron',monospace; font-size:0.62rem;
  color:var(--cyan); letter-spacing:3px; animation:blink 1s infinite; }

/* BENCH CARDS */
.bench-row { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }
.bc {
  background: rgba(12,20,34,0.9);
  border: 1px solid #142035;
  border-radius:4px; padding:10px; transition:all 0.35s;
  position:relative; overflow:hidden;
}
.bc::before { content:''; position:absolute; top:0; left:0; right:0;
  height:2px; background:var(--muted); opacity:0.2; transition:all 0.35s; }
.bc:hover { border-color:var(--cyan2); transform:translateY(-1px); }
.bc.warn  { border-color:var(--amber); background:rgba(255,170,0,0.05); }
.bc.warn::before { background:var(--amber); opacity:1; }
.bc.crit  { border-color:var(--red); background:rgba(255,45,85,0.07);
  box-shadow:0 0 18px rgba(255,45,85,0.2);
  animation:cardAlert 0.5s ease; }
.bc.crit::before { background:var(--red); opacity:1; }
@keyframes cardAlert {
  0%,100%{transform:translateX(0)}
  20%{transform:translateX(-4px)} 40%{transform:translateX(4px)}
  60%{transform:translateX(-3px)} 80%{transform:translateX(3px)}
}
.bc-id   { font-family:'Orbitron',monospace; font-size:0.58rem;
  color:var(--muted); letter-spacing:2px; margin-bottom:3px; }
.bc-name { font-size:0.82rem; font-weight:600; margin-bottom:7px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.bar-bg { background:#060e1c; border-radius:2px; height:5px; overflow:hidden; }
.bar    { height:5px; border-radius:2px; transition:width 0.5s, background 0.3s; }
.bar.ok   { background:linear-gradient(90deg,#00aa55,#00ff88); }
.bar.warn { background:linear-gradient(90deg,#bb7700,#ffaa00);
  box-shadow:0 0 8px rgba(255,170,0,0.4); }
.bar.crit { background:linear-gradient(90deg,#bb0018,#ff2d55);
  box-shadow:0 0 10px rgba(255,45,85,0.5);
  animation:barPulse 0.9s ease-in-out infinite; }
@keyframes barPulse { 0%,100%{opacity:1} 50%{opacity:0.65} }
.bc-meta { display:flex; justify-content:space-between;
  margin-top:5px; font-family:'Share Tech Mono',monospace;
  font-size:0.62rem; color:var(--muted); }
.alert-chip { display:none; background:var(--red); color:#fff;
  font-family:'Orbitron',monospace; font-size:0.5rem;
  padding:2px 7px; border-radius:2px; letter-spacing:1px;
  margin-top:4px; animation:blink 1s infinite; }
.alert-chip.on { display:inline-block; }

/* THREAT METER */
.threat-wrap { display:flex; align-items:center; gap:8px; }
.t-lbl { font-family:'Orbitron',monospace; font-size:0.52rem;
  color:var(--muted); letter-spacing:1px; }
.t-track { width:110px; background:#060e1c; border-radius:2px;
  height:6px; overflow:hidden; }
.t-fill { height:6px; border-radius:2px; transition:width 0.6s;
  background:linear-gradient(90deg,var(--green),var(--amber),var(--red)); }
.t-lvl { font-family:'Orbitron',monospace; font-size:0.6rem;
  min-width:55px; text-align:right; font-weight:700; }

/* STATS */
.stat-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; }
.stat { text-align:center; padding:8px 4px;
  background:rgba(8,14,26,0.8); border:1px solid #0d2040;
  border-radius:3px; transition:all 0.25s; }
.stat:hover { border-color:var(--cyan2); }
.stat-v { font-family:'Orbitron',monospace; font-size:1.3rem;
  font-weight:900; color:var(--cyan); }
.stat-v.hot { color:var(--red); }
.stat-l { font-family:'Share Tech Mono',monospace; font-size:0.58rem;
  color:var(--muted); letter-spacing:1px; margin-top:3px; }

/* ALERT LOG */
.log-scroll { overflow-y:auto; flex:1; min-height:0; }
.log-scroll::-webkit-scrollbar { width:3px; }
.log-scroll::-webkit-scrollbar-thumb { background:#0d2040; border-radius:2px; }
table { width:100%; border-collapse:collapse; font-size:0.7rem; }
th { text-align:left; padding:5px 6px; color:var(--muted);
  font-family:'Orbitron',monospace; font-size:0.5rem; letter-spacing:1px;
  border-bottom:1px solid #0d2040;
  position:sticky; top:0; background:rgba(8,14,26,0.95); z-index:1; }
td { padding:5px 6px; border-bottom:1px solid #0d2040;
  font-family:'Share Tech Mono',monospace; }
tr.new-row { animation:rowIn 0.4s ease; }
@keyframes rowIn { from{opacity:0;transform:translateX(-8px)} to{opacity:1;transform:none} }
tr:hover td { background:rgba(13,32,64,0.5); }
.sbadge { display:inline-block; padding:2px 7px; border-radius:2px;
  font-family:'Orbitron',monospace; font-size:0.52rem; font-weight:700; }
.sbadge.r { background:rgba(255,45,85,0.15); color:var(--red);
  border:1px solid rgba(255,45,85,0.3); }
.sbadge.a { background:rgba(255,170,0,0.1); color:var(--amber);
  border:1px solid rgba(255,170,0,0.3); }
.btn-rev { background:none; border:1px solid #0d2040;
  color:var(--muted); padding:2px 7px; border-radius:2px;
  cursor:pointer; font-family:'Share Tech Mono',monospace; font-size:0.6rem; }
.btn-rev:hover { border-color:var(--cyan); color:var(--cyan); }
.ok-mark { color:var(--green); }

/* TOASTS — bottom-left so they never cover the right panel */
#toast-zone {
  position: fixed;
  bottom: 16px;
  left: 16px;
  z-index: 200;
  display: flex;
  flex-direction: column-reverse;
  gap: 8px;
  pointer-events: none;
  max-width: 300px;
}
.toast {
  background: rgba(8,14,26,0.97);
  border: 1px solid var(--red);
  border-left: 4px solid var(--red);
  border-radius: 4px; padding: 12px 14px;
  box-shadow: 0 0 30px rgba(255,45,85,0.35);
  animation: toastIn 0.4s cubic-bezier(0.34,1.56,0.64,1);
  pointer-events: auto; position: relative; overflow: hidden;
}
.toast::before { content:''; position:absolute; top:0; left:0; right:0; height:1px;
  background:linear-gradient(90deg,transparent,var(--red),transparent); }
@keyframes toastIn {
  from{opacity:0;transform:translateX(-40px) scale(0.9)}
  to{opacity:1;transform:none}
}
@keyframes toastOut {
  from{opacity:1;max-height:120px}
  to{opacity:0;transform:translateX(-40px);max-height:0;padding:0;margin:0}
}
.toast.out { animation:toastOut 0.3s ease forwards; }
.toast-hdr { display:flex; align-items:center; gap:7px; margin-bottom:5px; }
.toast-title { font-family:'Orbitron',monospace; font-size:0.6rem;
  color:var(--red); letter-spacing:2px; font-weight:700; }
.toast-body { font-family:'Share Tech Mono',monospace;
  font-size:0.75rem; color:var(--text); margin-bottom:3px; }
.toast-meta { font-family:'Share Tech Mono',monospace;
  font-size:0.6rem; color:var(--muted); }
.toast-bar { position:absolute; bottom:0; left:0; height:2px;
  background:var(--red); animation:tbar 6s linear forwards; }
@keyframes tbar { from{width:100%} to{width:0%} }

/* MODALS */
.modal-bg { display:none; position:fixed; inset:0; z-index:100;
  background:rgba(0,4,12,0.88); align-items:center; justify-content:center;
  backdrop-filter:blur(5px); }
.modal-bg.open { display:flex; }
.modal { background:var(--bg3); border:1px solid var(--cyan2);
  border-radius:6px; padding:24px; width:500px;
  max-width:94vw; max-height:85vh; overflow-y:auto;
  box-shadow:0 0 60px rgba(0,212,255,0.12);
  animation:mIn 0.3s cubic-bezier(0.34,1.56,0.64,1); }
@keyframes mIn { from{opacity:0;transform:scale(0.9) translateY(20px)} to{opacity:1;transform:none} }
.modal::before { content:''; display:block; height:1px;
  background:linear-gradient(90deg,transparent,var(--cyan),transparent);
  margin:-24px -24px 20px; }
.modal h2 { font-family:'Orbitron',monospace; font-size:0.85rem;
  color:var(--cyan); letter-spacing:2px; margin-bottom:6px; }
.modal p  { font-size:0.8rem; color:var(--muted); margin-bottom:16px;
  font-family:'Share Tech Mono',monospace; }
.form-group { margin-bottom:12px; }
.form-group label { font-family:'Orbitron',monospace; font-size:0.55rem;
  color:var(--muted); letter-spacing:2px; display:block; margin-bottom:5px; }
.form-group input,.form-group select {
  width:100%; background:var(--bg2); border:1px solid var(--bord);
  color:var(--text); padding:8px 12px; border-radius:3px;
  font-family:'Share Tech Mono',monospace; font-size:0.8rem; outline:none; }
.form-group input:focus,.form-group select:focus {
  border-color:var(--cyan); box-shadow:0 0 10px rgba(0,212,255,0.1); }
.ba-grid { display:grid; grid-template-columns:auto 1fr; gap:8px 12px; align-items:center; }
.ba-grid label { font-family:'Orbitron',monospace; font-size:0.6rem;
  color:var(--cyan2); letter-spacing:1px; }
.modal-btns { display:flex; gap:8px; justify-content:flex-end; margin-top:16px; }
.modal-msg  { font-family:'Share Tech Mono',monospace; font-size:0.7rem;
  color:var(--green); margin-top:10px; min-height:16px; }
.divider { border:none; border-top:1px solid var(--bord); margin:14px 0; }
.rep-item { display:flex; align-items:center; justify-content:space-between;
  padding:10px 0; border-bottom:1px solid var(--bord); }
.rep-item:last-child { border-bottom:none; }
.rep-name { font-family:'Share Tech Mono',monospace; font-size:0.72rem; color:var(--cyan); }
.rep-date { font-family:'Share Tech Mono',monospace; font-size:0.62rem; color:var(--muted); margin-top:2px; }
.btn-dl { background:var(--bg2); border:1px solid var(--bord);
  color:var(--muted); padding:4px 12px; border-radius:3px;
  cursor:pointer; font-family:'Orbitron',monospace; font-size:0.5rem;
  letter-spacing:1px; text-decoration:none; transition:all 0.2s; }
.btn-dl:hover { border-color:var(--cyan); color:var(--cyan); }
.no-rep { text-align:center; color:var(--muted);
  font-family:'Share Tech Mono',monospace; padding:24px; font-size:0.75rem; }
</style>
</head>
<body>

<!-- TOASTS bottom-left -->
<div id="toast-zone"></div>

<!-- HEADER -->
<div class="header">
  <div class="logo">
    <div class="logo-hex">A</div>
    <div>
      <div class="logo-name">ARGUS</div>
      <div class="logo-sub">EXAM SURVEILLANCE SYSTEM &bull; VIT PUNE &bull; CSAIML-E</div>
    </div>
  </div>
  <div class="hdr-center">
    <div class="sys-lbl">SYSTEM STATUS</div>
    <div class="sys-val" id="sys-val">STANDBY &mdash; AWAITING EXAM START</div>
  </div>
  <div class="hdr-right">
    <div class="live-pill">
      <div class="dot" id="dot"></div>
      <span id="st-txt">OFFLINE</span>
      <span id="fps-txt" style="color:var(--green);margin-left:6px;"></span>
    </div>
    <div class="clock" id="clock"></div>
  </div>
</div>

<!-- CTRL BAR -->
<div class="ctrl">
  <button class="btn btn-start" id="btn-start" onclick="startExam()">&#9654; START EXAM</button>
  <button class="btn btn-stop"  id="btn-stop"  onclick="stopExam()"  disabled>&#9632; STOP EXAM</button>
  <button class="btn btn-aruco" id="btn-aruco" onclick="scanAruco()">&#9638; SCAN ARUCO</button>
  <button class="btn btn-ghost" onclick="openUpload()">&#8593; SEATING FILE</button>
  <button class="btn btn-ghost" onclick="openReports()">&#128196; REPORTS</button>
  <button class="btn btn-del"   onclick="clearAlerts()">&#10005; CLEAR</button>
  <div class="etag etag-idle" id="etag">STANDBY</div>
  <div class="etime" id="etime"></div>
</div>

<!-- LAYOUT -->
<div class="layout">
  <div class="col-l">
    <!-- Feed -->
    <div class="panel fill">
      <div class="phdr">
        <span>&#9673; LIVE SURVEILLANCE FEED</span>
        <span id="aruco-lbl" style="display:none;color:var(--cyan);font-size:0.58rem;">
          &#9638; ZONE CALIBRATION IN PROGRESS
        </span>
      </div>
      <div class="feed-wrap" id="feed-wrap">
        <img id="live-img" src="/video_feed"
             onerror="feedErr()" onload="feedOk()" style="display:none;">
        <div class="fc"></div>
        <div class="fc fc2"></div>
        <div id="feed-off" class="feed-off">
          <div class="feed-off-ico">&#9673;</div>
          <div class="feed-off-txt">NO SIGNAL &mdash; START EXAM TO ACTIVATE</div>
        </div>
        <div class="scan-ov" id="scan-ov">
          <div class="spin-ring"></div>
          <div class="spin-txt">SCANNING ARUCO MARKERS</div>
        </div>
      </div>
      <div class="rec" id="rec"><div class="rec-d"></div>REC</div>
    </div>

    <!-- Bench Status -->
    <div class="panel">
      <div class="phdr">
        <span>&#9632; BENCH STATUS</span>
        <div class="threat-wrap">
          <span class="t-lbl">THREAT</span>
          <div class="t-track">
            <div class="t-fill" id="t-fill" style="width:0%"></div>
          </div>
          <span class="t-lvl" id="t-lvl" style="color:var(--green);">LOW</span>
        </div>
      </div>
      <div class="bench-row" id="bench-row"></div>
    </div>
  </div>

  <div class="col-r">
    <!-- Stats -->
    <div class="panel">
      <div class="phdr"><span>&#9632; SESSION METRICS</span></div>
      <div class="stat-grid">
        <div class="stat"><div class="stat-v" id="s-al">0</div><div class="stat-l">ALERTS</div></div>
        <div class="stat"><div class="stat-v" id="s-st">0</div><div class="stat-l">FLAGGED</div></div>
        <div class="stat"><div class="stat-v" id="s-pk">0</div><div class="stat-l">PEAK</div></div>
        <div class="stat"><div class="stat-v" id="s-bn">0</div><div class="stat-l">BENCHES</div></div>
      </div>
    </div>

    <!-- Alert Log -->
    <div class="panel fill">
      <div class="phdr">
        <span>&#9888; INCIDENT LOG</span>
        <span style="font-size:0.55rem;color:var(--dim);">LIVE &bull; 2s REFRESH</span>
      </div>
      <div class="log-scroll">
        <table>
          <thead>
            <tr><th>#</th><th>TIME</th><th>BENCH</th>
            <th>STUDENT</th><th>SCORE</th><th>CONF</th><th></th></tr>
          </thead>
          <tbody id="log-body">
            <tr><td colspan="7" style="text-align:center;color:var(--muted);
              padding:24px;font-family:Share Tech Mono,monospace;font-size:.72rem;
              letter-spacing:1px;">NO INCIDENTS RECORDED</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- UPLOAD MODAL -->
<div class="modal-bg" id="m-upload" onclick="closeBg(event,'m-upload')">
  <div class="modal">
    <h2>&#8593; UPLOAD SEATING FILE</h2>
    <p>Upload the official VIT seating Excel to map students to ARGUS bench zones.</p>
    <div class="form-group">
      <label>Excel File (.xlsx)</label>
      <input type="file" id="xl-file" accept=".xlsx,.xls" onchange="parseXL()">
    </div>
    <div id="room-sec" style="display:none;">
      <div class="form-group">
        <label>Room Number</label>
        <select id="room-sel" onchange="onRoom()">
          <option value="">-- Select Room --</option>
        </select>
      </div>
      <hr class="divider">
      <div class="form-group">
        <label>Bench Assignment (ARGUS Zone &rarr; Excel Bench No.)</label>
        <div class="ba-grid">
          <label>B1 LEFT</label>   <input type="number" id="b1" placeholder="e.g. 1" min="1">
          <label>B2 MIDDLE</label> <input type="number" id="b2" placeholder="e.g. 2" min="1">
          <label>B3 RIGHT</label>  <input type="number" id="b3" placeholder="e.g. 3" min="1">
        </div>
      </div>
    </div>
    <div class="modal-msg" id="ul-msg"></div>
    <div class="modal-btns">
      <button class="btn btn-del" onclick="document.getElementById('m-upload').classList.remove('open')">CANCEL</button>
      <button class="btn btn-aruco" id="btn-assign" style="display:none;" onclick="doAssign()">ASSIGN STUDENTS</button>
    </div>
  </div>
</div>

<!-- REPORTS MODAL -->
<div class="modal-bg" id="m-reports" onclick="closeBg(event,'m-reports')">
  <div class="modal">
    <h2>&#128196; EXAM REPORTS</h2>
    <p>All past exam PDFs. Auto-saved when exam stops.</p>
    <div id="rep-list"><div class="no-rep">Loading...</div></div>
    <div class="modal-btns">
      <button class="btn btn-del" onclick="document.getElementById('m-reports').classList.remove('open')">CLOSE</button>
    </div>
  </div>
</div>

<script>
let THR=100, examOn=false, colMeta={};
let prevTotal=0, prevIds=new Set();
const _ctrs={};

// Clock
(function tick(){
  document.getElementById('clock').textContent=
    new Date().toLocaleTimeString('en-IN',{hour12:false});
  setTimeout(tick,1000);
})();

// Feed
function feedErr(){ document.getElementById('live-img').style.display='none';
  document.getElementById('feed-off').style.display='flex';
  document.getElementById('rec').classList.remove('on'); }
function feedOk() { document.getElementById('live-img').style.display='block';
  document.getElementById('feed-off').style.display='none';
  if(examOn) document.getElementById('rec').classList.add('on'); }

// Bench render
function renderBenches(b){
  const row=document.getElementById('bench-row');
  const ids=Object.keys(b).length?Object.keys(b):['B1','B2','B3'];
  let mx=0;
  row.innerHTML=ids.map(id=>{
    const info=b[id]||{};
    const sc=info.score||0, cf=info.ml_confidence||0, nm=info.student_name||'--';
    const pct=Math.min(100,(sc/THR)*100);
    if(sc>mx) mx=sc;
    const isCrit=sc>=THR, isWarn=!isCrit&&sc>=THR*0.6;
    const bCls=isCrit?'crit':isWarn?'warn':'ok';
    const cCls=isCrit?' crit':isWarn?' warn':'';
    const chip=isCrit?'<div class="alert-chip on">ALERT</div>':'';
    return `<div class="bc${cCls}">
      <div class="bc-id">${id}</div>
      <div class="bc-name">${nm}</div>
      <div class="bar-bg"><div class="bar ${bCls}" style="width:${pct}%"></div></div>
      <div class="bc-meta"><span>${sc}/${THR}</span><span>${cf?Math.round(cf*100)+'% CONF':'--'}</span></div>
      ${chip}</div>`;
  }).join('');
  // Threat
  const tp=Math.min(100,(mx/THR)*100);
  document.getElementById('t-fill').style.width=tp+'%';
  const lv=tp>=100?'CRITICAL':tp>=60?'HIGH':tp>=30?'MEDIUM':'LOW';
  const lc=tp>=100?'var(--red)':tp>=60?'#ff6b35':tp>=30?'var(--amber)':'var(--green)';
  const tl=document.getElementById('t-lvl');
  tl.textContent=lv; tl.style.color=lc;
}

// Status poll
async function fetchStatus(){
  try{
    const r=await fetch('/api/status'); const d=await r.json();
    if(d.threshold) THR=d.threshold;
    const age=Date.now()/1000-(d.last_update||0);
    const live=d.detection_running&&age<5;
    document.getElementById('dot').className='dot'+(live?' live':d.aruco_scanning?' scan':'');
    document.getElementById('st-txt').textContent=d.aruco_scanning?'SCANNING':live?'LIVE':'OFFLINE';
    document.getElementById('fps-txt').textContent=live&&d.fps?d.fps+' FPS':'';
    document.getElementById('scan-ov').className='scan-ov'+(d.aruco_scanning?' on':'');
    document.getElementById('aruco-lbl').style.display=d.aruco_scanning?'block':'none';
    document.getElementById('sys-val').textContent=
      d.aruco_scanning?'ARUCO ZONE CALIBRATION IN PROGRESS':
      live?'MONITORING ACTIVE -- '+Object.keys(d.benches||{}).length+' ZONES':
      examOn?'EXAM ACTIVE -- DETECTION OFFLINE':'STANDBY -- AWAITING EXAM START';
    renderBenches(d.benches||{});
  }catch(e){}
}

// Alert poll
async function fetchAlerts(){
  try{
    const [ar,sr]=await Promise.all([fetch('/api/alerts'),fetch('/api/summary')]);
    const alerts=await ar.json(); const sum=await sr.json();
    const ta=sum.total_alerts||0;
    cnt('s-al',ta,ta>0); cnt('s-st',sum.unique_students||0);
    cnt('s-pk',sum.highest_score||0); cnt('s-bn',(sum.benches_flagged||[]).length);
    if(alerts.length>prevTotal){
      for(let i=prevTotal;i<alerts.length;i++){
        if(!prevIds.has(alerts[i].id)){ prevIds.add(alerts[i].id); toast(alerts[i]); }
      }
    }
    prevTotal=alerts.length;
    const tb=document.getElementById('log-body');
    if(!alerts.length){
      tb.innerHTML='<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px;font-family:Share Tech Mono,monospace;font-size:.72rem;letter-spacing:1px;">NO INCIDENTS RECORDED</td></tr>';
      return;
    }
    tb.innerHTML=alerts.slice(0,50).map((a,i)=>{
      const bc=a.score>=80?'r':'a';
      const rv=a.reviewed?'<span class="ok-mark">&#10003;</span>'
        :`<button class="btn-rev" onclick="markRev(${a.id})">REVIEW</button>`;
      return `<tr${i===0&&alerts.length>prevTotal-1?' class="new-row"':''}>
        <td style="color:var(--muted)">#${a.id}</td><td>${a.time}</td>
        <td><b style="color:var(--cyan)">${a.bench}</b></td>
        <td style="max-width:68px;overflow:hidden;text-overflow:ellipsis">${a.student_name}</td>
        <td><span class="sbadge ${bc}">${a.score}</span></td>
        <td style="color:var(--muted)">${Math.round(a.ml_confidence*100)}%</td>
        <td>${rv}</td></tr>`;
    }).join('');
  }catch(e){}
}

// Animated counter
function cnt(id,val,hot){
  const el=document.getElementById(id);
  el.className='stat-v'+(hot?' hot':'');
  const cur=parseInt(el.textContent)||0;
  if(cur===val) return;
  const step=Math.ceil(Math.abs(val-cur)/8);
  clearInterval(_ctrs[id]);
  _ctrs[id]=setInterval(()=>{
    const v=parseInt(el.textContent)||0;
    if(v===val){clearInterval(_ctrs[id]);return;}
    el.textContent=v<val?Math.min(v+step,val):Math.max(v-step,val);
  },40);
}

// Toast (bottom-left)
function toast(a){
  const z=document.getElementById('toast-zone');
  const t=document.createElement('div');
  t.className='toast';
  t.innerHTML=`<div class="toast-hdr">
    <span style="font-size:1rem;">&#9888;</span>
    <span class="toast-title">MALPRACTICE DETECTED</span></div>
  <div class="toast-body">${a.student_name} &mdash; ${a.bench}</div>
  <div class="toast-meta">Roll: ${a.roll_number||'--'} &bull; Score: ${a.score} &bull; ${a.time}</div>
  <div class="toast-bar"></div>`;
  z.appendChild(t);
  t.onclick=()=>dismiss(t);
  setTimeout(()=>dismiss(t),6200);
}
function dismiss(t){ t.classList.add('out'); setTimeout(()=>t.remove(),310); }

// Exam controls
async function startExam(){
  const b=document.getElementById('btn-start');
  b.disabled=true; b.textContent='INITIALIZING...';
  try{
    const r=await fetch('/api/exam/start',{method:'POST',
      headers:{'Content-Type':'application/json'},body:'{}'});
    const d=await r.json();
    if(d.ok){
      examOn=true; prevTotal=0; prevIds.clear();
      document.getElementById('btn-stop').disabled=false;
      document.getElementById('btn-start').disabled=true;
      document.getElementById('btn-start').innerHTML='&#9654; START EXAM';
      document.getElementById('etag').textContent='EXAM IN PROGRESS';
      document.getElementById('etag').className='etag etag-active';
      document.getElementById('etime').textContent='Started '+d.started;
      setTimeout(()=>{
        const img=document.getElementById('live-img');
        img.src='/video_feed?'+Date.now();
      },2000);
    }
  }catch(e){ b.disabled=false; b.innerHTML='&#9654; START EXAM'; alert('Failed to start.'); }
}

async function stopExam(){
  if(!confirm('Stop exam?\n\nExcel will download automatically.\nPDF saved to reports/ folder.')) return;
  const b=document.getElementById('btn-stop');
  b.disabled=true; b.textContent='STOPPING...';
  try{
    const r=await fetch('/api/exam/stop',{method:'POST'});
    const ct=r.headers.get('Content-Type')||'';
    if(ct.includes('spreadsheet')||ct.includes('excel')){
      const blob=await r.blob();
      const cd=r.headers.get('Content-Disposition')||'';
      const m=cd.match(/filename="?([^"]+)"?/);
      const a=document.createElement('a');
      a.href=URL.createObjectURL(blob);
      a.download=m?m[1]:'ARGUS_Report.xlsx'; a.click();
      alert('Exam ended.\nExcel downloaded.\nPDF saved to reports/ folder.');
    }else{ await r.json(); }
  }catch(e){ console.error(e); }
  examOn=false;
  document.getElementById('btn-start').disabled=false;
  document.getElementById('btn-stop').disabled=true;
  document.getElementById('btn-stop').innerHTML='&#9632; STOP EXAM';
  document.getElementById('btn-start').innerHTML='&#9654; START EXAM';
  document.getElementById('etag').textContent='EXAM ENDED';
  document.getElementById('etag').className='etag etag-ended';
  document.getElementById('etime').textContent='';
  document.getElementById('rec').classList.remove('on');
  feedErr(); fetchAlerts();
}

async function scanAruco(){
  const b=document.getElementById('btn-aruco');
  b.disabled=true; b.textContent='SCANNING...';
  document.getElementById('etag').textContent='SCANNING ZONES';
  document.getElementById('etag').className='etag etag-scan';
  await fetch('/api/aruco/scan',{method:'POST'});
  const poll=setInterval(async()=>{
    const r=await fetch('/api/aruco/status'); const d=await r.json();
    if(!d.scanning){
      clearInterval(poll);
      b.disabled=false; b.innerHTML='&#9638; SCAN ARUCO';
      document.getElementById('etag').textContent=examOn?'EXAM IN PROGRESS':'ZONES UPDATED';
      document.getElementById('etag').className='etag '+(examOn?'etag-active':'etag-idle');
      if(d.done) alert('ArUco scan complete! Zones saved.');
    }
  },1000);
}

async function markRev(id){ await fetch('/api/reviewed/'+id,{method:'POST'}); fetchAlerts(); }
async function clearAlerts(){
  if(!confirm('Clear all alert records?')) return;
  await fetch('/api/clear',{method:'POST'});
  prevTotal=0; prevIds.clear(); fetchAlerts();
}

function closeBg(e,id){ if(e.target.id===id) document.getElementById(id).classList.remove('open'); }

function openUpload(){
  document.getElementById('m-upload').classList.add('open');
  document.getElementById('ul-msg').textContent='';
  document.getElementById('room-sec').style.display='none';
  document.getElementById('btn-assign').style.display='none';
}
async function parseXL(){
  const file=document.getElementById('xl-file').files[0]; if(!file) return;
  document.getElementById('ul-msg').textContent='PARSING...';
  const fd=new FormData(); fd.append('file',file);
  try{
    const r=await fetch('/api/seating/rooms',{method:'POST',body:fd});
    const d=await r.json();
    if(!d.ok){ document.getElementById('ul-msg').textContent='ERR: '+d.msg; return; }
    colMeta={room_col:d.room_col,bench_col:d.bench_col,name_col:d.name_col,prn_col:d.prn_col};
    const sel=document.getElementById('room-sel');
    sel.innerHTML='<option value="">-- Select Room --</option>'+
      d.rooms.map(r=>`<option value="${r}">${r}</option>`).join('');
    document.getElementById('room-sec').style.display='block';
    document.getElementById('ul-msg').textContent=d.rooms.length+' ROOMS FOUND';
  }catch(e){ document.getElementById('ul-msg').textContent='PARSE FAILED'; }
}
function onRoom(){
  if(!document.getElementById('room-sel').value) return;
  document.getElementById('btn-assign').style.display='flex';
  document.getElementById('ul-msg').textContent='ENTER BENCH NUMBERS FOR EACH ZONE';
}
async function doAssign(){
  const room=document.getElementById('room-sel').value;
  if(!room){ document.getElementById('ul-msg').textContent='SELECT ROOM FIRST'; return; }
  document.getElementById('ul-msg').textContent='ASSIGNING...';
  const r=await fetch('/api/seating/assign',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({room,bench_map:{
      B1:document.getElementById('b1').value,
      B2:document.getElementById('b2').value,
      B3:document.getElementById('b3').value},...colMeta})});
  const d=await r.json();
  if(d.ok){
    document.getElementById('ul-msg').textContent='ASSIGNED: '+(d.updated||[]).join(' | ');
    setTimeout(()=>document.getElementById('m-upload').classList.remove('open'),1800);
  }else{ document.getElementById('ul-msg').textContent='ERROR: '+d.msg; }
}
async function openReports(){
  document.getElementById('m-reports').classList.add('open');
  const el=document.getElementById('rep-list');
  el.innerHTML='<div class="no-rep">LOADING...</div>';
  const r=await fetch('/api/reports'); const files=await r.json();
  if(!files.length){
    el.innerHTML='<div class="no-rep">NO REPORTS YET.<br>AUTO-SAVED WHEN EXAM STOPS.</div>';
    return;
  }
  el.innerHTML=files.map(f=>`
    <div class="rep-item">
      <div><div class="rep-name">${f.filename}</div>
      <div class="rep-date">${f.date} &bull; ${f.size_kb} KB</div></div>
      <a class="btn-dl" href="/reports/${f.filename}" target="_blank" download="${f.filename}">DOWNLOAD</a>
    </div>`).join('');
}

renderBenches({});
fetchStatus(); fetchAlerts();
setInterval(fetchStatus,1000);
setInterval(fetchAlerts,2000);
</script>
</body>
</html>"""

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)

if __name__ == "__main__":
    print("="*55)
    print("  ARGUS -- Dashboard [DEMO READY v7]")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("="*55)
    print("\n  Open: http://localhost:5000\n")
    os.makedirs(SNAPSHOT_DIR,exist_ok=True)
    os.makedirs(REPORTS_DIR,exist_ok=True)
    app.run(host="0.0.0.0",port=5000,debug=False,threaded=True)
