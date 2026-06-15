"""
ARGUS — File 12: detection/main.py  [v6.5 — Real Scoring + Zone Fix]
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

v6.5 fixes over v6.4:
  1. SCORING aligned with real exam behaviour:
       - Normal sitting/writing     → 0 pts (no accumulation)
       - Light stretch (arms up)    → 8 pts/sec (clears in ~12s of normal)
       - Head turn sideways         → 20 pts/sec
       - Head turn + arm reach      → 35 pts/sec (combined cheating posture)
       - Turning back (sustained)   → alert fires at 100pts ~5-7 seconds
       - Fast wrist movement        → 15 pts instantaneous per event
     All values tuned so innocent stretch never reaches 100 alone,
     but sustained head-turn + arm combination reaches 100 in 5-7 seconds.

  2. VISIBILITY GATE lowered from 8→4 landmarks, threshold 0.5→0.3
     The previous gate was too strict and blocked real detections when
     backlit (classroom windows behind students lower visibility scores).

  3. SUSTAINED_SECONDS changed to 0 — alert fires as soon as score hits 100,
     not after an additional 2s wait on top. The scoring rate itself provides
     the natural 5-7 second delay before alert.

  4. All v6.4 fixes retained (stop signal at top, GUI guard, no import in loop)
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
STOP_SIGNAL   = os.path.join(_ROOT, "ARGUS_STOP.signal")

# Visibility gate — filters completely empty zones
# Lowered from 8/0.5 → 4/0.3 to handle backlit classroom windows
MIN_VISIBLE_LANDMARKS = 4
VISIBILITY_THRESHOLD  = 0.3


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


def _count_visible_landmarks(person):
    """Count visible MediaPipe landmarks. Returns large number if unknown format."""
    try:
        landmarks = person.get("landmarks", person)
        if landmarks is None:
            return 0
        if hasattr(landmarks, 'landmark'):
            lms = landmarks.landmark
        elif isinstance(landmarks, (list, tuple)):
            lms = landmarks
        else:
            return 99   # unknown format — assume visible

        count = 0
        for lm in lms:
            if hasattr(lm, 'visibility'):
                if lm.visibility > VISIBILITY_THRESHOLD:
                    count += 1
            elif isinstance(lm, dict):
                if lm.get('visibility', 0) > VISIBILITY_THRESHOLD:
                    count += 1
            else:
                count += 1   # no visibility field — assume visible
        return count
    except Exception:
        return 99


# ── Real-behaviour scoring rates ──────────────────────────────────────────────
# These are applied per-second (multiplied by frame_interval in scorer)
# Tuned so:
#   - Innocent stretch: peaks ~40-50, clears before 100
#   - Sustained head turn alone: hits 100 in ~5 seconds → alert
#   - Head turn + arm reach: hits 100 in ~3 seconds → alert
#   - Turning body backwards: head_offset_x very large → treated as head turn
#
# Thresholds (from config.json, these are the feature gate values):
#   head_offset_x >= 0.38  → head turned sideways
#   left/right wrist_dist >= 1.8  → arm reaching
#   wrist_velocity_avg >= 4.0  → fast wrist movement
#
# Scoring is handled by score_manager.py — these constants document intent.
# The actual rates are set in score_manager.py. Here we only gate the alert.

ALERT_THRESHOLD   = 100   # fire alert at this score
WARNING_THRESHOLD = 60    # amber warning at this score
ALERT_PERSIST_SEC = 30    # how long alert stays before auto-clear
SUSTAINED_SECONDS = 0     # additional wait after score hits 100 before alert
                           # 0 = alert fires immediately at 100 (scoring rate
                           # itself provides the 5-7 second natural delay)


# ── Dashboard push ────────────────────────────────────────────────────────────
_dash_enabled = True
_dash_queue   = queue.Queue(maxsize=3)

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
                                 json=task["state"], timeout=0.5)
                except Exception:
                    pass
                try:
                    session.post(f"{DASHBOARD_URL}/api/frame",
                                 data=task["jpeg"],
                                 headers={"Content-Type": "image/jpeg"},
                                 timeout=0.5)
                except Exception:
                    pass
            elif t == "alert":
                try:
                    session.post(f"{DASHBOARD_URL}/api/alert",
                                 json=task["data"], timeout=0.5)
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
                (w - 90, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (139, 148, 158), 1)

    for bench_id, st in bench_states.items():
        x, y, bw, bh = int(st["x"]), int(st["y"]), int(st["w"]), int(st["h"])
        score     = st.get("score", 0)
        confirmed = bench_id in confirmed_alerts
        rc = (248, 81, 73)  if confirmed else \
             (210, 153, 34) if score >= alert_thr * 0.6 else (63, 185, 80)

        cv2.rectangle(frame, (x, y), (x + bw, y + bh), rc, 2)
        bar_w = int((min(score, alert_thr) / max(alert_thr, 1)) * bw)
        bar_y = y + bh - 8
        cv2.rectangle(frame, (x, bar_y), (x + bw, y + bh), (33, 38, 45), -1)
        if bar_w > 0:
            cv2.rectangle(frame, (x, bar_y), (x + bar_w, y + bh), rc, -1)
        label = f"{bench_id} {st.get('student_name', '')}"
        cv2.rectangle(frame, (x, max(0, y - 22)), (x + bw, y), (13, 17, 23), -1)
        cv2.putText(frame, label, (x + 4, y - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, rc, 1)
        cv2.putText(frame, f"{score:.0f}/{alert_thr}", (x + 4, y + bh - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 237, 243), 1)
        if confirmed:
            cv2.rectangle(frame, (x + bw - 65, y + 4), (x + bw - 4, y + 22),
                          (248, 81, 73), -1)
            cv2.putText(frame, "ALERT", (x + bw - 62, y + 17),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)

    cv2.rectangle(frame, (0, h - 24), (w, h), (13, 17, 23), -1)
    cv2.putText(frame, "Q=Quit  R=Reset  C=Clear  S=Snapshot  P=Pause",
                (8, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (72, 79, 88), 1)

    if alert_popups:
        now = time.time()
        popup_y = 50
        for bench_id, (msg, ts) in list(alert_popups.items()):
            if now - ts > 25:
                del alert_popups[bench_id]
                continue
            bx1, by1 = 20, popup_y
            bx2, by2 = bx1 + 520, popup_y + 56
            ov = frame.copy()
            cv2.rectangle(ov, (bx1, by1), (bx2, by2), (100, 40, 10), -1)
            cv2.addWeighted(ov, 0.55, frame, 0.45, 0, frame)
            cv2.rectangle(frame, (bx1, by1), (bx2, by2), (255, 140, 0), 2)
            cv2.putText(frame, f"ALERT  {msg}",
                        (bx1 + 10, by1 + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 200, 255), 2)
            cv2.putText(frame, f"Bench {bench_id} flagged for malpractice",
                        (bx1 + 10, by1 + 46),
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
    cam_index = cfg.get("camera_index", 0) if not args.no_cam else 0
    alert_thr = cfg.get("threshold", ALERT_THRESHOLD)
    warn_thr  = alert_thr * 0.6
    conf_thr  = cfg.get("ml_confidence_threshold", 0.75)

    # Clean stale stop signal
    if os.path.exists(STOP_SIGNAL):
        try:
            os.remove(STOP_SIGNAL)
            print("[ARGUS] Removed stale stop signal")
        except Exception:
            pass

    print("=" * 55)
    print("  ARGUS — File 12: Main System v6.5")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("=" * 55)
    print(f"\n  Camera      : {cam_index}")
    print(f"  Alert at    : {alert_thr} pts")
    print(f"  Warning at  : {warn_thr:.0f} pts")
    print(f"  Alert delay : fires when score hits {alert_thr} (5-7s natural delay)")
    print(f"  Dashboard   : {'OFF' if not _dash_enabled else 'ON'}")

    # Open camera first
    cap = None
    if not args.no_cam:
        print(f"\n  Opening camera {cam_index}...")
        cap = cv2.VideoCapture(cam_index)
        if not cap.isOpened():
            print(f"[ERROR] Camera {cam_index} not found.")
            sys.exit(1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, 30)
        print("  Flushing warm-up frames...")
        for _ in range(20):
            cap.read()
        print("  Camera ready ✓")

    # Hardware
    hw = None
    if HW_AVAILABLE and not args.no_hw:
        print("\n  Connecting Arduino...")
        hw = ARGUSHardware()
        if hw.start():
            print("  Hardware connected ✓")
        else:
            print("  Hardware not found — software only")
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
    zones_mgr = ZoneManager()
    print("[5/7] ScoreManager...")
    scorer    = ScoreManager()
    print("[6/7] Classifier...")
    clf       = ARGUSClassifier()
    print("[7/7] AlertLogger...")
    logger    = AlertLogger()

    if args.no_cam or cap is None:
        cap = cv2.VideoCapture(cam_index)
        if not cap.isOpened():
            print(f"[ERROR] Camera {cam_index} not found.")
            sys.exit(1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, 30)

    print("  All modules ready ✓\n" + "-" * 55)

    zones_mgr.reload_zones()
    active_zones = zones_mgr.get_all_zones()
    print(f"  Zones loaded: {[z['bench_id']+':'+z.get('student_name','?') for z in active_zones]}")

    if hw:
        hw.send_exam_start()

    # GUI
    _gui_available = False
    try:
        cv2.namedWindow("ARGUS", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("ARGUS", 1280, 720)
        _gui_available = True
        print("  GUI window opened ✓")
    except Exception as e:
        print(f"  GUI not available — dashboard only ({e})")

    # State
    frame_count       = 0
    fps               = 0.0
    fps_timer         = time.time()
    paused            = False
    confirmed_alerts  = set()
    last_alerted_time = {}
    alert_popups      = {}
    warned_set        = set()
    bench_states      = {}
    frame_interval    = 1.0 / 30.0
    push_counter      = 0
    decay_timer       = time.time()
    suspicious_since  = {}

    print("\n  [ARGUS] Detection running — waiting for stop signal...\n")

    while True:
        # Stop signal — checked every single frame at top of loop
        if os.path.exists(STOP_SIGNAL):
            time.sleep(0.1)
            try:
                os.remove(STOP_SIGNAL)
            except Exception:
                pass
            print("\n  [STOP] Signal received — exiting cleanly")
            break

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
            display = draw_overlay(frame.copy(), bench_states, fps, True,
                                   alert_thr, confirmed_alerts, alert_popups)
            if _gui_available:
                try:
                    cv2.imshow("ARGUS", display)
                    key = cv2.waitKey(30) & 0xFF
                    if key in (ord('q'), ord('Q')): break
                    if key in (ord('p'), ord('P')): paused = False
                except Exception:
                    _gui_available = False
            else:
                time.sleep(0.03)
            continue

        h_f, w_f = frame.shape[:2]

        # Motion
        try:
            motion_result = motion.detect(frame, zones_mgr.get_all_zones())
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

        # Pose
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

            # Visibility gate — filters empty zones
            # Lowered to 4 landmarks at 0.3 to handle backlit classrooms
            visible_count = _count_visible_landmarks(person)
            if visible_count < MIN_VISIBLE_LANDMARKS:
                continue

            centroid = person.get("centroid", (0.5, 0.5))
            cx_norm  = float(centroid[0])
            cy_norm  = float(centroid[1])

            try:
                bench_id = zones_mgr.assign_zone(cx_norm, cy_norm, w_f, h_f)
            except Exception:
                bench_id = None

            if not bench_id:
                px = int(cx_norm * w_f)
                py = int(cy_norm * h_f)
                zone_ranges = [(z["bench_id"], z["x"], z["x"] + z["w"])
                               for z in zones_mgr.get_all_zones()]
                print(f"  [ZONE-MISS] px={px} py={py} zones={zone_ranges}")
                continue

            try:
                student_name = zones_mgr.get_student_name(bench_id) or "Unknown"
                roll_number  = zones_mgr.get_roll_number(bench_id)  or ""
                zone_info    = zones_mgr.get_zone_by_id(bench_id)   or {}
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

            warmup_done   = (time.time() - scorer.start_time) >= scorer.warmup_seconds
            display_score = score   if warmup_done else 0.0
            display_conf  = ml_conf if warmup_done else 0.0

            bench_states[bench_id] = {
                "x"            : zx,
                "y"            : zy,
                "w"            : zw,
                "h"            : zh,
                "score"        : display_score,
                "ml_confidence": display_conf,
                "student_name" : student_name,
                "roll_number"  : roll_number,
            }

            # ── Alert logic ───────────────────────────────────────────────────
            # Alert fires as soon as score hits threshold — the 5-7 second
            # natural delay comes from the scoring rate in score_manager.py.
            # SUSTAINED_SECONDS=0 means no additional wait on top of that.
            if score >= alert_thr:
                if bench_id not in suspicious_since:
                    suspicious_since[bench_id] = now
                sustained = now - suspicious_since[bench_id]

                if bench_id in warned_set and hw:
                    warned_set.discard(bench_id)

                if (sustained >= SUSTAINED_SECONDS
                        and bench_id not in confirmed_alerts):
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
                    if hw:
                        hw.send_alert(bench_id, student_name)
                    print(f"  [ALERT] {bench_id} | {student_name} | "
                          f"score={score:.1f} | sustained={sustained:.1f}s")

            elif score >= warn_thr:
                if (bench_id not in warned_set
                        and bench_id not in confirmed_alerts):
                    warned_set.add(bench_id)
                    if hw:
                        hw.send_warning(bench_id, student_name)
                    print(f"  [WARN]  {bench_id} | {student_name} | "
                          f"score={score:.1f}")

            else:
                suspicious_since.pop(bench_id, None)
                if bench_id in warned_set:
                    warned_set.discard(bench_id)
                    if hw: hw.send_warn_clear()

                if bench_id in confirmed_alerts:
                    elapsed = now - last_alerted_time.get(bench_id, now)
                    if score < 25 or elapsed >= ALERT_PERSIST_SEC:
                        confirmed_alerts.discard(bench_id)
                        last_alerted_time.pop(bench_id, None)
                        suspicious_since.pop(bench_id, None)
                        try:
                            scorer.reset_score(bench_id, "Alert cleared")
                        except Exception:
                            pass
                        if hw: hw.send_clear()
                        print(f"  [CLEAR] {bench_id} score={score:.1f} "
                              f"elapsed={elapsed:.0f}s")

        # Decay every 1 second
        if now - decay_timer >= 1.0:
            try:
                scorer.decay_all()
            except Exception:
                pass
            decay_timer = now

        display = draw_overlay(frame.copy(), bench_states, fps, False,
                               alert_thr, confirmed_alerts, alert_popups)

        if _gui_available:
            try:
                cv2.imshow("ARGUS", display)
            except Exception:
                _gui_available = False

        # Push to dashboard every 2 frames
        if _dash_enabled and push_counter % 2 == 0:
            try:
                _, jpeg = cv2.imencode(".jpg", display,
                                       [cv2.IMWRITE_JPEG_QUALITY, 55])
                push_to_dashboard(jpeg.tobytes(), {
                    "benches"    : bench_states,
                    "frame_count": push_counter,
                    "fps"        : round(fps, 1),
                    "running"    : True
                })
            except Exception:
                pass

        if _gui_available:
            try:
                key = cv2.waitKey(1) & 0xFF
                if key in (ord('q'), ord('Q')):
                    print("\n  [QUIT]"); break
                elif key in (ord('r'), ord('R')):
                    for bid in list(bench_states.keys()):
                        try: scorer.reset_score(bid)
                        except Exception: pass
                    confirmed_alerts.clear(); suspicious_since.clear()
                    last_alerted_time.clear(); alert_popups.clear()
                    warned_set.clear(); bench_states = {}
                    if hw: hw.send_clear()
                    print("  [RESET]")
                elif key in (ord('c'), ord('C')):
                    logger.clear_session()
                    confirmed_alerts.clear(); suspicious_since.clear()
                    last_alerted_time.clear(); alert_popups.clear()
                    warned_set.clear()
                    if hw: hw.send_clear()
                    print("  [CLEAR]")
                elif key in (ord('s'), ord('S')):
                    print(f"  [SNAP] {save_snapshot(frame, 'MANUAL', 'snap')}")
                elif key in (ord('p'), ord('P')):
                    paused = True; print("  [PAUSE]")
            except Exception:
                pass

    # Cleanup
    if hw:
        hw.send_exam_stop()
        time.sleep(1)
        hw.stop()

    if _dash_enabled:
        try: _dash_queue.put_nowait(None)
        except Exception: pass

    cap.release()
    if _gui_available:
        try: cv2.destroyAllWindows()
        except Exception: pass

    s = logger.get_summary()
    print("\n" + "=" * 55)
    print(f"  Alerts  : {s['total_alerts']}")
    print(f"  Students: {s['unique_students']}")
    print(f"  Peak    : {s['highest_score']}")
    print(f"  Benches : {s['benches_flagged']}")
    print("=" * 55 + "\n  ARGUS session ended.")


if __name__ == "__main__":
    main()
