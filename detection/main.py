"""
ARGUS — File 12: detection/main.py  [FINAL v6 — Hardware Integration]
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

v6 adds over v5:
  - Arduino hardware integration (Green/Amber/Red LED + Buzzer + LCD)
  - Green LED on when exam starts
  - Amber LED when score enters warning zone (60-99)
  - Red LED + 3 buzzer beeps when alert confirmed (10s sustained)
  - Hardware runs in background thread — zero FPS impact
  - If Arduino not connected, system runs normally without hardware

Run: py -3.11 main.py
     py -3.11 main.py --no-dash
     py -3.11 main.py --no-cam
     py -3.11 main.py --no-hw     (disable hardware)
Controls: Q=Quit R=Reset C=Clear S=Snapshot P=Pause
"""

import cv2
import json
import os
import sys
import time
import threading
import queue
import argparse
import requests
import numpy as np

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT     = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _THIS_DIR)

from pose_detector       import PoseDetector
from feature_extractor   import FeatureExtractor
from motion_zones        import MotionZones
from zone_manager        import ZoneManager
from score_manager       import ScoreManager
from classifier          import ARGUSClassifier
from webapp.alert_logger import AlertLogger

# Hardware — optional, graceful fallback if not connected
try:
    sys.path.insert(0, _ROOT)
    from hardware.serial_handler import ARGUSHardware
    HW_AVAILABLE = True
except ImportError:
    HW_AVAILABLE = False
    print("[HW] serial_handler not found — hardware disabled")

CONFIG_FILE   = os.path.join(_THIS_DIR, "config.json")
SNAPSHOT_DIR  = os.path.join(_ROOT, "snapshots")
DASHBOARD_URL = "http://localhost:5000"

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"camera_index": 0, "threshold": 100,
                "ml_confidence_threshold": 0.75}

_snap_count = {}
def save_snapshot(frame, bench, roll):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    key = f"{bench}_{roll}"
    _snap_count[key] = _snap_count.get(key, 0) + 1
    fname = f"{bench}_{roll}_{_snap_count[key]:03d}.jpg"
    cv2.imwrite(os.path.join(SNAPSHOT_DIR, fname), frame,
                [cv2.IMWRITE_JPEG_QUALITY, 85])
    return f"snapshots/{fname}"


# ── Dashboard push queue ──────────────────────────────────────────────────────
_dash_enabled = True
_dash_queue   = queue.Queue(maxsize=2)

def _dashboard_worker():
    session = requests.Session()
    while True:
        try:
            task = _dash_queue.get(timeout=1)
            if task is None:
                break
            t = task.get("type")
            if t == "frame_status":
                try:
                    session.post(f"{DASHBOARD_URL}/api/status",
                                 json=task["state"], timeout=0.3)
                except Exception:
                    pass
                try:
                    session.post(f"{DASHBOARD_URL}/api/frame",
                                 data=task["jpeg"],
                                 headers={"Content-Type": "image/jpeg"},
                                 timeout=0.3)
                except Exception:
                    pass
            elif t == "alert":
                try:
                    session.post(f"{DASHBOARD_URL}/api/alert",
                                 json=task["data"], timeout=0.3)
                except Exception:
                    pass
        except queue.Empty:
            continue
        except Exception:
            continue

def push_to_dashboard(frame_jpeg, state):
    if not _dash_enabled:
        return
    try:
        _dash_queue.put_nowait({"type": "frame_status",
                                "jpeg": frame_jpeg, "state": state})
    except queue.Full:
        try:
            _dash_queue.get_nowait()
            _dash_queue.put_nowait({"type": "frame_status",
                                    "jpeg": frame_jpeg, "state": state})
        except Exception:
            pass

def push_alert(alert_data):
    if not _dash_enabled:
        return
    try:
        _dash_queue.put_nowait({"type": "alert", "data": alert_data})
    except Exception:
        pass


# ── Overlay ───────────────────────────────────────────────────────────────────
def draw_overlay(frame, bench_states, fps, paused,
                 alert_thr, confirmed_alerts, alert_popups):
    h, w = frame.shape[:2]

    cv2.rectangle(frame, (0, 0), (w, 38), (13, 17, 23), -1)
    status = "PAUSED" if paused else "MONITORING"
    color  = (0, 165, 255) if paused else (63, 185, 80)
    cv2.putText(frame, f"ARGUS  |  {status}  |  {fps:.1f} FPS",
                (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
    cv2.putText(frame, time.strftime("%H:%M:%S"),
                (w-90, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (139, 148, 158), 1)

    for bench_id, st in bench_states.items():
        x, y, bw, bh = int(st["x"]), int(st["y"]), int(st["w"]), int(st["h"])
        score     = st.get("score", 0)
        confirmed = bench_id in confirmed_alerts
        rc = (248, 81, 73) if confirmed else \
             (210, 153, 34) if score >= alert_thr * 0.6 else (63, 185, 80)

        cv2.rectangle(frame, (x, y), (x+bw, y+bh), rc, 2)
        bar_w = int((min(score, alert_thr) / alert_thr) * bw)
        bar_y = y + bh - 8
        cv2.rectangle(frame, (x, bar_y), (x+bw, y+bh), (33, 38, 45), -1)
        if bar_w > 0:
            cv2.rectangle(frame, (x, bar_y), (x+bar_w, y+bh), rc, -1)
        label = f"{bench_id} {st.get('student_name', '')}"
        cv2.rectangle(frame, (x, max(0, y-22)), (x+bw, y), (13, 17, 23), -1)
        cv2.putText(frame, label, (x+4, y-6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, rc, 1)
        cv2.putText(frame, f"{score:.0f}/{alert_thr}", (x+4, y+bh-12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 237, 243), 1)
        if confirmed:
            cv2.rectangle(frame, (x+bw-65, y+4), (x+bw-4, y+22),
                          (248, 81, 73), -1)
            cv2.putText(frame, "ALERT", (x+bw-62, y+17),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)

    cv2.rectangle(frame, (0, h-24), (w, h), (13, 17, 23), -1)
    cv2.putText(frame, "Q=Quit  R=Reset  C=Clear  S=Snapshot  P=Pause",
                (8, h-8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (72, 79, 88), 1)

    if alert_popups:
        now = time.time()
        popup_y = 50
        for bench_id, (msg, ts) in list(alert_popups.items()):
            if now - ts > 25:
                del alert_popups[bench_id]
                continue
            box_x1, box_y1 = 20, popup_y
            box_x2, box_y2 = box_x1 + 520, popup_y + 56
            overlay = frame.copy()
            cv2.rectangle(overlay, (box_x1, box_y1),
                          (box_x2, box_y2), (100, 40, 10), -1)
            cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
            cv2.rectangle(frame, (box_x1, box_y1),
                          (box_x2, box_y2), (255, 140, 0), 2)
            cv2.putText(frame, f"ALERT  {msg}",
                        (box_x1 + 10, box_y1 + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 200, 255), 2)
            cv2.putText(frame, f"Bench {bench_id} flagged for malpractice",
                        (box_x1 + 10, box_y1 + 46),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (180, 220, 255), 1)
            popup_y += 66

    return frame


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global _dash_enabled

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-dash", action="store_true")
    parser.add_argument("--no-cam",  action="store_true")
    parser.add_argument("--no-hw",   action="store_true")
    args = parser.parse_args()
    if args.no_dash:
        _dash_enabled = False

    cfg       = load_config()
    cam_index = 0 if args.no_cam else cfg.get("camera_index", 0)
    alert_thr = cfg.get("threshold", 100)
    conf_thr  = cfg.get("ml_confidence_threshold", 0.75)
    warn_thr  = alert_thr * 0.6   # warning zone threshold

    print("=" * 55)
    print("  ARGUS — File 12: Main System v6")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("=" * 55)
    print(f"\n  Camera   : {cam_index}")
    print(f"  Threshold: {alert_thr}")
    print(f"  Warning  : {warn_thr:.0f}")
    print(f"  ML conf  : {conf_thr}")
    print(f"  Dashboard: {'OFF' if not _dash_enabled else 'ON'}")

    # ── Hardware init ─────────────────────────────────────────────────────────
    hw = None
    if HW_AVAILABLE and not args.no_hw:
        print("\n  Connecting Arduino hardware...")
        hw = ARGUSHardware()
        if hw.start():
            print("  Hardware connected ✓")
        else:
            print("  Hardware not found — running without hardware")
            hw = None
    else:
        print("  Hardware: DISABLED")

    if _dash_enabled:
        threading.Thread(target=_dashboard_worker, daemon=True).start()
        print("  Dashboard worker started ✓")

    print("\n[1/7] PoseDetector...")
    pose      = PoseDetector()
    print("[2/7] FeatureExtractor...")
    extractor = FeatureExtractor()
    print("[3/7] MotionZones...")
    motion    = MotionZones()
    print("[4/7] ZoneManager...")
    zones     = ZoneManager()
    print("[5/7] ScoreManager...")
    scorer    = ScoreManager()
    print("[6/7] Classifier...")
    clf       = ARGUSClassifier()
    print("[7/7] AlertLogger...")
    logger    = AlertLogger()

    print(f"\n  Opening camera {cam_index}...")
    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        print(f"[ERROR] Camera {cam_index} not found. Try --no-cam")
        sys.exit(1)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)
    print("  Camera open ✓\n" + "-"*55)

    # Signal exam start to hardware
    if hw:
        hw.send_exam_start()

    # ── State variables ───────────────────────────────────────────────────────
    frame_count       = 0
    fps               = 0.0
    fps_timer         = time.time()
    paused            = False
    confirmed_alerts  = set()
    last_alerted_time = {}
    alert_popups      = {}
    warned_set        = set()   # benches currently in warning state on hardware
    bench_states      = {}
    frame_interval    = 1.0 / 30.0
    push_counter      = 0
    decay_timer       = time.time()
    suspicious_since  = {}
    SUSTAINED_SECONDS = 10
    ALERT_PERSIST_SEC = 30

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.05)
            continue

        frame_count  += 1
        push_counter += 1
        now = time.time()
        if now - fps_timer >= 1.0:
            fps            = frame_count / (now - fps_timer)
            frame_count    = 0
            fps_timer      = now
            frame_interval = 1.0 / max(fps, 1.0)

        if paused:
            cv2.imshow("ARGUS", draw_overlay(
                frame.copy(), bench_states, fps, True,
                alert_thr, confirmed_alerts, alert_popups))
            key = cv2.waitKey(30) & 0xFF
            if key in (ord('q'), ord('Q')): break
            if key in (ord('p'), ord('P')): paused = False
            continue

        h_f, w_f = frame.shape[:2]

        try:
            all_zones = zones.get_all_zones()
        except Exception:
            all_zones = []

        try:
            motion_result = motion.detect(frame, all_zones)
            if isinstance(motion_result, tuple):
                motion_scores, frame = motion_result
            else:
                motion_scores = motion_result
        except Exception:
            motion_scores = {}

        def get_motion(bid):
            if isinstance(motion_scores, dict):
                return float(motion_scores.get(bid, 0.0))
            return 0.0

        try:
            pose_result = pose.detect(frame)
            if isinstance(pose_result, tuple):
                persons, frame = pose_result
            else:
                persons = pose_result or []
        except Exception:
            persons = []

        for person in persons:
            if not person:
                continue

            centroid = person.get("centroid", (0.5, 0.5))
            cx_norm  = float(centroid[0])
            cy_norm  = float(centroid[1])

            try:
                bench_id = zones.assign_zone(cx_norm, cy_norm, w_f, h_f)
            except Exception:
                bench_id = None
            if not bench_id:
                continue

            try:
                student_name = zones.get_student_name(bench_id) or "Unknown"
                roll_number  = zones.get_roll_number(bench_id)  or ""
                zone_info    = zones.get_zone_by_id(bench_id)   or {}
            except Exception:
                student_name, roll_number, zone_info = "Unknown", "", {}

            m_score   = get_motion(bench_id)
            landmarks = person.get("landmarks", person)

            try:
                features = extractor.extract(
                    landmarks,
                    zone_motion_score=m_score,
                    frame_interval=frame_interval
                )
            except Exception as e:
                print(f"[WARN] extract: {e}")
                continue

            if features is None:
                continue

            features_list = features if isinstance(features, list) \
                            else list(features.values())
            FEATURE_KEYS = [
                "shoulder_angle", "head_offset_x", "head_offset_y",
                "left_wrist_dist", "right_wrist_dist",
                "wrist_velocity_avg", "zone_motion_score"
            ]
            features_dict = dict(zip(FEATURE_KEYS, features_list))
            ml_conf = clf.predict(features_dict) if clf.is_ready() else 0.0

            try:
                scorer.update(bench_id, features_list,
                              ml_probability=ml_conf,
                              motion_score=m_score)
            except Exception as e:
                print(f"[WARN] scorer: {e}")

            score = scorer.get_score(bench_id)

            zx = int(zone_info.get("x", 0))
            zy = int(zone_info.get("y", 0))
            zw = int(zone_info.get("w", 200))
            zh = int(zone_info.get("h", 300))

            bench_states[bench_id] = {
                "x": zx, "y": zy, "w": zw, "h": zh,
                "score": score, "ml_confidence": ml_conf,
                "student_name": student_name, "roll_number": roll_number,
            }

            # ── Alert + Warning logic ─────────────────────────────────────────
            if score >= alert_thr:
                if bench_id not in suspicious_since:
                    suspicious_since[bench_id] = now
                sustained = now - suspicious_since[bench_id]

                # Clear warning LED if we're now in alert territory
                if bench_id in warned_set and hw:
                    warned_set.discard(bench_id)
                    # send_alert will handle WARN_CLEAR internally

                if sustained >= SUSTAINED_SECONDS \
                        and bench_id not in confirmed_alerts:
                    # ALERT confirmed
                    confirmed_alerts.add(bench_id)
                    last_alerted_time[bench_id] = now
                    snap = save_snapshot(frame, bench_id, roll_number)
                    alert_data = {
                        "bench"        : bench_id,
                        "student_name" : student_name,
                        "roll_number"  : roll_number,
                        "score"        : score,
                        "ml_confidence": ml_conf,
                        "snapshot_path": snap,
                        "flags"        : {}
                    }
                    logger.log_alert(**alert_data)
                    push_alert(alert_data)
                    alert_popups[bench_id] = (
                        f"{student_name} ({roll_number})", now)
                    # Hardware: Red LED + 3 beeps
                    if hw:
                        hw.send_alert(bench_id, student_name)
                    print(f"  [ALERT] {bench_id} | {student_name} | "
                          f"score={score:.0f} | sustained={sustained:.1f}s")

            elif score >= warn_thr:
                # WARNING zone — amber LED
                if bench_id not in warned_set \
                        and bench_id not in confirmed_alerts:
                    warned_set.add(bench_id)
                    if hw:
                        hw.send_warning(bench_id, student_name)
                    print(f"  [WARN] {bench_id} | {student_name} | "
                          f"score={score:.0f}")

            else:
                # Score dropped below warning zone
                suspicious_since.pop(bench_id, None)

                # Clear warning state on hardware
                if bench_id in warned_set:
                    warned_set.discard(bench_id)
                    if hw:
                        hw.send_warn_clear()

                # Clear confirmed alert after ALERT_PERSIST_SEC
                if bench_id in confirmed_alerts:
                    elapsed = now - last_alerted_time.get(bench_id, now)
                    if elapsed >= ALERT_PERSIST_SEC:
                        confirmed_alerts.discard(bench_id)
                        last_alerted_time.pop(bench_id, None)
                        if hw:
                            hw.send_clear()
                        print(f"  [CLEAR] {bench_id} alert cleared "
                              f"after {elapsed:.0f}s")

        # Decay every 8 seconds
        if now - decay_timer >= 8.0:
            try:
                scorer.decay_all()
            except Exception:
                pass
            decay_timer = now

        # Draw overlay
        display = draw_overlay(frame.copy(), bench_states, fps, False,
                               alert_thr, confirmed_alerts, alert_popups)
        cv2.imshow("ARGUS", display)

        # Push to dashboard every 4th frame
        if _dash_enabled and push_counter % 4 == 0:
            _, jpeg = cv2.imencode(".jpg", display,
                                   [cv2.IMWRITE_JPEG_QUALITY, 50])
            push_to_dashboard(jpeg.tobytes(), {
                "benches"    : bench_states,
                "frame_count": push_counter,
                "fps"        : round(fps, 1),
                "running"    : True
            })

        # Keys
        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), ord('Q')):
            print("\n  [QUIT]"); break
        elif key in (ord('r'), ord('R')):
            for bid in list(bench_states.keys()):
                try: scorer.reset_score(bid)
                except Exception: pass
            confirmed_alerts.clear()
            suspicious_since.clear()
            last_alerted_time.clear()
            alert_popups.clear()
            warned_set.clear()
            bench_states = {}
            if hw: hw.send_clear()
            print("  [RESET]")
        elif key in (ord('c'), ord('C')):
            logger.clear_session()
            confirmed_alerts.clear()
            suspicious_since.clear()
            last_alerted_time.clear()
            alert_popups.clear()
            warned_set.clear()
            if hw: hw.send_clear()
            print("  [CLEAR]")
        elif key in (ord('s'), ord('S')):
            print(f"  [SNAP] {save_snapshot(frame, 'MANUAL', 'snap')}")
        elif key in (ord('p'), ord('P')):
            paused = True
            print("  [PAUSE]")

    # Cleanup
    if hw:
        hw.send_exam_stop()
        time.sleep(1)
        hw.stop()

    if _dash_enabled:
        try: _dash_queue.put_nowait(None)
        except Exception: pass

    cap.release()
    cv2.destroyAllWindows()

    s = logger.get_summary()
    print("\n" + "="*55)
    print(f"  Alerts:{s['total_alerts']} "
          f"Students:{s['unique_students']} "
          f"Peak:{s['highest_score']} "
          f"Benches:{s['benches_flagged']}")
    print("="*55 + "\n  ARGUS session ended.")


if __name__ == "__main__":
    main()
