"""
ARGUS — Real Normal Data Collector
py -3.11 ml/collect_real_normal.py
Controls: SPACE=pause  Q=quit+save  R=reset
"""

import cv2
import csv
import time
import os
import sys
import numpy as np

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT     = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "detection"))

OUTPUT_FILE = os.path.join(_THIS_DIR, "real_normal.csv")
CAMERA_INDEX = 0

FEATURE_KEYS = [
    "shoulder_angle", "head_offset_x", "head_offset_y",
    "left_wrist_dist", "right_wrist_dist",
    "wrist_velocity_avg", "zone_motion_score"
]

def main():
    print("=" * 55)
    print("  ARGUS — Real Normal Data Collector")
    print("=" * 55)
    print("\n  Sit at 2-3m from camera exactly as during exam.")
    print("  Naturally: sit straight, write, read, slight lean")
    print("  Controls: SPACE=pause  Q=quit+save  R=reset")
    print("\n  Starting in 5 seconds — get in position!")
    print("=" * 55)
    time.sleep(5)

    from feature_extractor import FeatureExtractor
    from pose_detector import PoseDetector

    extractor     = FeatureExtractor()
    pose_detector = PoseDetector()
    print("[OK] Modules loaded")

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print(f"[ERROR] Camera {CAMERA_INDEX} not available")
        sys.exit(1)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    for _ in range(15):
        cap.read()

    samples   = []
    paused    = False
    prev_time = time.time()

    print(f"  Collecting... target = 400 samples")
    print(f"  Output: {OUTPUT_FILE}\n")

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        now            = time.time()
        frame_interval = max(now - prev_time, 0.01)
        prev_time      = now
        display        = frame.copy()

        if not paused:
            try:
                result  = pose_detector.detect(frame)
                persons = result[0] if isinstance(result, tuple) else result
                if persons:
                    person = persons[0] if isinstance(persons, list) else persons
                    lm     = person.get("landmarks", person)

                    # Draw centroid dot
                    centroid = person.get("centroid", (0.5, 0.5))
                    cx = int(centroid[0] * frame.shape[1])
                    cy = int(centroid[1] * frame.shape[0])
                    cv2.circle(display, (cx, cy), 10, (0, 255, 0), -1)

                    features = extractor.extract(
                        lm,
                        zone_motion_score=0.0,
                        frame_interval=frame_interval
                    )
                    if features is not None:
                        f_list = features if isinstance(features, list) \
                                 else list(features.values())
                        if len(f_list) >= 7:
                            samples.append(f_list[:7] + [0])
            except Exception:
                pass

        # ── Overlay ──────────────────────────────────────────
        h, w = display.shape[:2]
        cv2.rectangle(display, (0, 0), (w, 45), (13, 17, 23), -1)
        clr = (63, 185, 80) if len(samples) < 400 else (255, 200, 0)
        cv2.putText(display,
            f"Samples: {len(samples)}/400  |  "
            f"{'PAUSED' if paused else 'RECORDING'}",
            (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, clr, 2)

        # Progress bar
        pct = min(len(samples) / 400, 1.0)
        cv2.rectangle(display, (0, 45), (w, 52), (33, 38, 45), -1)
        cv2.rectangle(display, (0, 45), (int(w * pct), 52), clr, -1)

        cv2.rectangle(display, (0, h-28), (w, h), (13, 17, 23), -1)
        cv2.putText(display, "SPACE=pause  Q=quit+save  R=reset",
            (8, h-9), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (72, 79, 88), 1)

        if len(samples) >= 400:
            cv2.rectangle(display, (0, 55), (w, 110), (0, 100, 0), -1)
            cv2.putText(display, "400 SAMPLES DONE — press Q to save",
                (20, 92), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

        cv2.imshow("ARGUS — Normal Data Collection", display)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), ord('Q')):
            break
        elif key == ord(' '):
            paused = not paused
            print(f"  {'PAUSED' if paused else 'RESUMED'} — {len(samples)} samples")
        elif key in (ord('r'), ord('R')):
            samples = []
            print("  RESET")

    cap.release()
    cv2.destroyAllWindows()

    if not samples:
        print("\n[WARN] No samples collected.")
        return

    with open(OUTPUT_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(FEATURE_KEYS + ['label'])
        writer.writerows(samples)

    print(f"\n[SAVED] {len(samples)} real normal samples → {OUTPUT_FILE}")
    print("\nNext: py -3.11 ml/merge_and_train.py")

if __name__ == "__main__":
    main()
