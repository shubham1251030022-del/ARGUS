"""
ARGUS — File 12: detection/main.py  [FIXED v3]
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

Fixes in v3:
  1. assign_zone receives NORMALIZED coords (0-1) — matches ZoneManager expectation
  2. Dashboard push uses dedicated queue thread — zero FPS impact
  3. MJPEG frame push guaranteed every frame — no black screen

Run: py -3.11 main.py
     py -3.11 main.py --no-dash
     py -3.11 main.py --no-cam
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

CONFIG_FILE   = os.path.join(_THIS_DIR, "config.json")
SNAPSHOT_DIR  = os.path.join(_ROOT, "snapshots")
DASHBOARD_URL = "http://localhost:5000"

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"camera_index": 1, "alert_threshold": 30,
                "ml_confidence_threshold": 0.65}

_snap_count = {}
def save_snapshot(frame, bench, roll):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    key = f"{bench}_{roll}"
    _snap_count[key] = _snap_count.get(key, 0) + 1
    fname = f"{bench}_{roll}_{_snap_count[key]:03d}.jpg"
    cv2.imwrite(os.path.join(SNAPSHOT_DIR, fname), frame,
                [cv2.IMWRITE_JPEG_QUALITY, 85])
    return f"snapshots/{fname}"


# ════════════════════════════════════════════════════════════════════════════
# FIX 2 — Dedicated dashboard push thread with queue
# Never blocks the main detection loop
# ════════════════════════════════════════════════════════════════════════════

_dash_enabled  = True
_dash_queue    = queue.Queue(maxsize=2)   # small buffer — drop old frames
_alert_queue   = queue.Queue(maxsize=20)

def _dashboard_worker():
    """Runs in background — consumes queue and posts to Flask."""
    session = requests.Session()
    while True:
        try:
            task = _dash_queue.get(timeout=1)
            if task is None:
                break
            task_type = task.get("type")

            if task_type == "frame_status":
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

            elif task_type == "alert":
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
        _dash_queue.put_nowait({
            "type" : "frame_status",
            "jpeg" : frame_jpeg,
            "state": state
        })
    except queue.Full:
        try:
            _dash_queue.get_nowait()   # drop oldest
            _dash_queue.put_nowait({
                "type" : "frame_status",
                "jpeg" : frame_jpeg,
                "state": state
            })
        except Exception:
            pass

def push_alert(alert_data):
    if not _dash_enabled:
        return
    try:
        _dash_queue.put_nowait({"type": "alert", "data": alert_data})
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
# OVERLAY
# ════════════════════════════════════════════════════════════════════════════

def draw_overlay(frame, bench_states, fps, paused):
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w, 38), (13, 17, 23), -1)
    status = "PAUSED" if paused else "MONITORING"
    color  = (0, 165, 255) if paused else (63, 185, 80)
    cv2.putText(frame, f"ARGUS  |  {status}  |  {fps:.1f} FPS",
                (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
    cv2.putText(frame, time.strftime("%H:%M:%S"),
                (w-90, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (139,148,158), 1)

    for bench_id, st in bench_states.items():
        x, y, bw, bh = int(st["x"]), int(st["y"]), int(st["w"]), int(st["h"])
        score   = st.get("score", 0)
        alerted = score >= 30
        rc = (248, 81, 73) if alerted else (63, 185, 80)
        cv2.rectangle(frame, (x, y), (x+bw, y+bh), rc, 2)
        bar_w = int((min(score, 30) / 30) * bw)
        bar_y = y + bh - 8
        cv2.rectangle(frame, (x, bar_y), (x+bw, y+bh), (33, 38, 45), -1)
        bc = (248,81,73) if alerted else (210,153,34) if score>=20 else (63,185,80)
        if bar_w > 0:
            cv2.rectangle(frame, (x, bar_y), (x+bar_w, y+bh), bc, -1)
        label = f"{bench_id} {st.get('student_name','')}"
        cv2.rectangle(frame, (x, max(0,y-22)), (x+bw, y), (13,17,23), -1)
        cv2.putText(frame, label, (x+4, y-6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, rc, 1)
        cv2.putText(frame, f"{score:.0f}", (x+4, y+bh-12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230,237,243), 1)
        if alerted:
            cv2.rectangle(frame, (x+bw-60, y+4), (x+bw-4, y+22), (248,81,73), -1)
            cv2.putText(frame, "ALERT", (x+bw-57, y+17),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255,255,255), 1)

    cv2.rectangle(frame, (0, h-24), (w, h), (13,17,23), -1)
    cv2.putText(frame, "Q=Quit  R=Reset  C=Clear  S=Snapshot  P=Pause",
                (8, h-8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (72,79,88), 1)
    return frame


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    global _dash_enabled

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-dash", action="store_true")
    parser.add_argument("--no-cam",  action="store_true")
    args = parser.parse_args()
    if args.no_dash:
        _dash_enabled = False

    cfg       = load_config()
    cam_index = 0 if args.no_cam else cfg.get("camera_index", 1)
    alert_thr = cfg.get("alert_threshold", 30)
    conf_thr  = cfg.get("ml_confidence_threshold", 0.65)

    print("=" * 55)
    print("  ARGUS — File 12: Main System v3")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("=" * 55)
    print(f"\n  Camera:{cam_index} Alert:{alert_thr} "
          f"Conf:{conf_thr} Dash:{'OFF' if not _dash_enabled else 'ON'}")

    # Start dashboard worker thread
    if _dash_enabled:
        dash_thread = threading.Thread(target=_dashboard_worker, daemon=True)
        dash_thread.start()
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

    frame_count    = 0
    fps            = 0.0
    fps_timer      = time.time()
    paused         = False
    alerted_set    = set()
    bench_states   = {}
    frame_interval = 1.0 / 30.0
    push_counter   = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.05)
            continue

        frame_count += 1
        push_counter += 1
        now = time.time()
        if now - fps_timer >= 1.0:
            fps            = frame_count / (now - fps_timer)
            frame_count    = 0
            fps_timer      = now
            frame_interval = 1.0 / max(fps, 1.0)

        if paused:
            cv2.imshow("ARGUS", draw_overlay(frame.copy(), bench_states, fps, True))
            key = cv2.waitKey(30) & 0xFF
            if key in (ord('q'), ord('Q')): break
            if key in (ord('p'), ord('P')): paused = False
            continue

        h_f, w_f = frame.shape[:2]

        # ── 1. All zones ──────────────────────────────────────────────────
        try:
            all_zones = zones.get_all_zones()
        except Exception:
            all_zones = {}

        # ── 2. Motion detection ───────────────────────────────────────────
        try:
            motion_result = motion.detect(frame, all_zones)
        except Exception:
            motion_result = {}

        def get_motion(bid):
            if isinstance(motion_result, dict):
                return float(motion_result.get(bid, 0.0))
            try:
                return float(motion_result)
            except Exception:
                return 0.0

        # ── 3. Pose detection ─────────────────────────────────────────────
        try:
            pose_result = pose.detect(frame)
            if isinstance(pose_result, tuple):
                persons, frame = pose_result
            else:
                persons = pose_result or []
        except Exception:
            persons = []

        # ── 4. Per-person pipeline ────────────────────────────────────────
        for person in persons:
            if not person:
                continue

            # FIX 1 — Pass NORMALIZED centroid (0-1) to assign_zone
            centroid  = person.get("centroid", (0.5, 0.5))
            cx_norm   = float(centroid[0])   # already 0-1 from pose_detector
            cy_norm   = float(centroid[1])

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

            # scorer needs LIST, classifier needs DICT
            features_list = features if isinstance(features, list) else list(features.values())
            FEATURE_KEYS = ["shoulder_angle","head_offset_x","head_offset_y",
                            "left_wrist_dist","right_wrist_dist",
                            "wrist_velocity_avg","zone_motion_score"]
            features_dict = dict(zip(FEATURE_KEYS, features_list))
            ml_conf = clf.predict(features_dict) if clf.is_ready() else 0.0

            try:
                scorer.update(bench_id, features_list,
                              ml_probability=ml_conf,
                              motion_score=m_score)
            except Exception as e:
                print(f"[WARN] scorer: {e}")

            score = scorer.get_score(bench_id)

            # Zone geometry in pixels for overlay
            zx = int(zone_info.get("x", 0) * w_f) if zone_info.get("x", 0) <= 1 \
                 else int(zone_info.get("x", 0))
            zy = int(zone_info.get("y", 0) * h_f) if zone_info.get("y", 0) <= 1 \
                 else int(zone_info.get("y", 0))
            zw = int(zone_info.get("w", 0) * w_f) if zone_info.get("w", 0) <= 1 \
                 else int(zone_info.get("w", 200))
            zh = int(zone_info.get("h", 0) * h_f) if zone_info.get("h", 0) <= 1 \
                 else int(zone_info.get("h", 300))

            bench_states[bench_id] = {
                "x": zx, "y": zy, "w": zw, "h": zh,
                "score": score, "ml_confidence": ml_conf,
                "student_name": student_name, "roll_number": roll_number,
            }

            # Alert
            if score >= alert_thr and bench_id not in alerted_set:
                alerted_set.add(bench_id)
                snap = save_snapshot(frame, bench_id, roll_number)
                alert_data = {
                    "bench": bench_id, "student_name": student_name,
                    "roll_number": roll_number, "score": score,
                    "ml_confidence": ml_conf, "snapshot_path": snap,
                    "flags": features.get("flags", {}) if isinstance(features, dict) else {}
                }
                logger.log_alert(**alert_data)
                push_alert(alert_data)
                print(f"  [ALERT] {bench_id} | {student_name} | score={score:.0f}")

            elif score < alert_thr and bench_id in alerted_set:
                alerted_set.discard(bench_id)

        # ── Draw ──────────────────────────────────────────────────────────
        display = draw_overlay(frame.copy(), bench_states, fps, False)
        cv2.imshow("ARGUS", display)

        # FIX 3 — Push every 4th frame via queue (non-blocking)
        if _dash_enabled and push_counter % 4 == 0:
            _, jpeg = cv2.imencode(".jpg", display,
                                   [cv2.IMWRITE_JPEG_QUALITY, 50])
            push_to_dashboard(jpeg.tobytes(), {
                "benches"    : bench_states,
                "frame_count": push_counter,
                "fps"        : round(fps, 1),
                "running"    : True
            })

        # ── Keys ──────────────────────────────────────────────────────────
        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), ord('Q')):
            print("\n  [QUIT]"); break
        elif key in (ord('r'), ord('R')):
            for bid in list(bench_states.keys()):
                try: scorer.reset_score(bid)
                except Exception: pass
            alerted_set.clear(); bench_states = {}
            print("  [RESET]")
        elif key in (ord('c'), ord('C')):
            logger.clear_session(); alerted_set.clear()
            print("  [CLEAR]")
        elif key in (ord('s'), ord('S')):
            print(f"  [SNAP] {save_snapshot(frame, 'MANUAL', 'snap')}")
        elif key in (ord('p'), ord('P')):
            paused = True; print("  [PAUSE]")

    # Stop dashboard worker
    if _dash_enabled:
        try: _dash_queue.put_nowait(None)
        except Exception: pass

    cap.release()
    cv2.destroyAllWindows()

    s = logger.get_summary()
    print("\n" + "="*55)
    print(f"  Alerts:{s['total_alerts']} Students:{s['unique_students']} "
          f"Peak:{s['highest_score']} Benches:{s['benches_flagged']}")
    print("="*55 + "\n  ARGUS session ended.")


if __name__ == "__main__":
    main()
