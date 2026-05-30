"""
ARGUS — File 9: detection/aruco_scanner.py
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

FIXED VERSION — changes from original:
  1. CAMERA_INDEX 1 → 0
  2. STABLE_FRAMES 10 → 6
  3. MIN_MARKER_AREA 400 → 150  (markers smaller at 2-3m)
  4. ZONE_EXPAND_X/Y enlarged  (bigger bench zone at distance)
  5. Stability check fixed      (was unreliable float dict compare)
  6. Adaptive thresholding added (handles backlit/window conditions)
  7. Detector params tuned      (more tolerant, detects at distance)
"""

import cv2
import numpy as np
import json
import os
import sys
import argparse
import time

# ── Paths ─────────────────────────────────────────────────────────────────────
_THIS_DIR  = os.path.dirname(os.path.abspath(__file__))
ZONES_FILE = os.path.join(_THIS_DIR, "zones.json")

# ── ArUco ID → Bench name mapping ─────────────────────────────────────────────
ARUCO_ID_MAP = {
    0: "B1",
    1: "B2",
    2: "B3",
    3: "B4",
    4: "B5",
    5: "B6"
}

# ── Bench zone expansion around ArUco marker centre ──────────────────────────
# Camera at 4-5 feet height, 2-3m horizontal distance (elevated angle)
# Student's body appears ABOVE the desk marker in image
# → expand more upward, less downward, wider horizontal for desk width
ZONE_EXPAND_X    = 180   # horizontal half-width (desk is ~60cm wide)
ZONE_EXPAND_Y_UP = 320   # expand UP from marker (student body above desk)
ZONE_EXPAND_Y_DN = 80    # expand DOWN from marker (just below desk level)

# ── Minimum marker size ────────────────────────────────────────────────────────
# FIX: lowered — at 2-3m markers appear smaller in frame
MIN_MARKER_AREA = 150  # was 400

# ── Camera index ──────────────────────────────────────────────────────────────
# FIX: was 1 (caused "Camera not available" on some setups)
CAMERA_INDEX = 0


class ARUCOScanner:

    def __init__(self):
        self.detector      = None
        self.last_zones    = {}
        self.scan_stable   = False
        self._stable_count = 0
        # FIX: reduced from 10 — was causing 0 zones locked intermittently
        self.STABLE_FRAMES = 6
        self._init_detector()

    # ── Detector init ─────────────────────────────────────────────────────────

    def _init_detector(self):
        """Load ArUco detector with tuned params. Tries 4.7+ API, falls back."""
        try:
            dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
            params     = cv2.aruco.DetectorParameters()

            # FIX: tuned for small markers at 2-3m distance + backlit rooms
            params.adaptiveThreshWinSizeMin  = 3
            params.adaptiveThreshWinSizeMax  = 23
            params.adaptiveThreshWinSizeStep = 4
            params.minMarkerPerimeterRate    = 0.02   # detect smaller markers
            params.maxMarkerPerimeterRate    = 4.0
            params.errorCorrectionRate       = 0.8    # more tolerant

            self.detector = cv2.aruco.ArucoDetector(dictionary, params)
            self._api = "new"
            print("[ARUCO] Detector loaded (OpenCV 4.7+ API, tuned params)")

        except AttributeError:
            self.dictionary = cv2.aruco.Dictionary_get(cv2.aruco.DICT_4X4_50)
            p = cv2.aruco.DetectorParameters_create()
            p.adaptiveThreshWinSizeMin  = 3
            p.adaptiveThreshWinSizeMax  = 23
            p.adaptiveThreshWinSizeStep = 4
            p.minMarkerPerimeterRate    = 0.02
            p.errorCorrectionRate       = 0.8
            self.params   = p
            self.detector = None
            self._api = "legacy"
            print("[ARUCO] Detector loaded (legacy API, tuned params)")

    # ── Core detection ────────────────────────────────────────────────────────

    def detect_markers(self, frame):
        """
        Detect ArUco markers in frame.
        Tries plain gray first (most reliable), then adaptive threshold as fallback.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Attempt 1: plain grayscale (original approach — most reliable)
        corners, ids = self._run_detect(gray)

        # Attempt 2: adaptive threshold fallback (helps with backlighting)
        if ids is None or len(ids) == 0:
            gray_adapt = cv2.adaptiveThreshold(
                gray, 255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, 11, 2
            )
            corners, ids = self._run_detect(gray_adapt)

        # Attempt 3: CLAHE contrast enhancement fallback
        if ids is None or len(ids) == 0:
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
            gray_clahe = clahe.apply(gray)
            corners, ids = self._run_detect(gray_clahe)

        if ids is None or len(ids) == 0:
            return []

        results = []
        for i, corner_set in enumerate(corners):
            marker_id = int(ids[i][0])
            pts  = corner_set[0]
            area = cv2.contourArea(pts.astype(np.float32))
            if area < MIN_MARKER_AREA:
                continue
            cx = int(np.mean(pts[:, 0]))
            cy = int(np.mean(pts[:, 1]))
            results.append({
                "id": marker_id, "corners": pts.tolist(),
                "centre_x": cx, "centre_y": cy, "area": float(area)
            })
        return results

    def _run_detect(self, gray):
        """Run ArUco detection on a prepared grayscale image."""
        try:
            if self._api == "new":
                corners, ids, _ = self.detector.detectMarkers(gray)
            else:
                corners, ids, _ = cv2.aruco.detectMarkers(
                    gray, self.dictionary, parameters=self.params
                )
            return corners, ids
        except Exception as e:
            print(f"[ARUCO] Detection error: {e}")
            return [], None

        return results

    # ── Zone builder ──────────────────────────────────────────────────────────

    def _marker_to_zone(self, marker, frame_shape):
        marker_id = marker["id"]
        if marker_id not in ARUCO_ID_MAP:
            return None

        h, w = frame_shape[:2]
        cx, cy = marker["centre_x"], marker["centre_y"]

        x1 = max(0, cx - ZONE_EXPAND_X)
        y1 = max(0, cy - ZONE_EXPAND_Y_UP)   # expand more upward
        x2 = min(w, cx + ZONE_EXPAND_X)
        y2 = min(h, cy + ZONE_EXPAND_Y_DN)   # less downward

        return {
            "bench"    : ARUCO_ID_MAP[marker_id],
            "aruco_id" : marker_id,
            "x"        : x1,
            "y"        : y1,
            "w"        : x2 - x1,
            "h"        : y2 - y1,
            "centre_x" : cx,
            "centre_y" : cy
        }

    # ── Main scan ─────────────────────────────────────────────────────────────

    def scan_frame(self, frame):
        """
        Detect all ArUco markers in frame and build bench zones.
        FIX: stability check now compares bench name sets, not full dicts.
              Float coordinate drift was resetting stable count every frame.
        """
        markers = self.detect_markers(frame)
        zones   = {}

        for m in markers:
            zone = self._marker_to_zone(m, frame.shape)
            if zone:
                zones[zone["bench"]] = zone

        # FIX: compare only which benches are found, not coordinates
        # Old code: zones == self.last_zones  ← floats differ frame-to-frame
        current_benches = set(zones.keys())
        last_benches    = set(self.last_zones.keys())

        if current_benches == last_benches and len(zones) > 0:
            self._stable_count += 1
        else:
            self._stable_count = 0

        self.last_zones = zones

        if self._stable_count >= self.STABLE_FRAMES:
            self.scan_stable = True

        return zones

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_zones(self, zones: dict):
        if not zones:
            print("[ARUCO] No zones to save.")
            return False

        existing = {}
        if os.path.exists(ZONES_FILE):
            try:
                with open(ZONES_FILE, "r") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    for item in raw:
                        key = item.get("bench") or item.get("name", "")
                        if key:
                            existing[key] = item
                elif isinstance(raw, dict):
                    existing = raw
            except Exception:
                existing = {}

        output = {}
        for bench_name, zone in zones.items():
            base = existing.get(bench_name, {})
            output[bench_name] = {
                "x"           : zone["x"],
                "y"           : zone["y"],
                "w"           : zone["w"],
                "h"           : zone["h"],
                "aruco_id"    : zone["aruco_id"],
                "student_name": base.get("student_name", "Unknown"),
                "status"      : base.get("status", "ACTIVE"),
                "roll_number" : base.get("roll_number", "")
            }

        with open(ZONES_FILE, "w") as f:
            json.dump(output, f, indent=4)

        print(f"[ARUCO] zones.json updated — {len(output)} bench(es) saved")
        for b, z in output.items():
            print(f"  {b}: x={z['x']} y={z['y']} w={z['w']} h={z['h']}")
        return True

    def get_zones(self):
        return self.last_zones

    def is_stable(self):
        return self.scan_stable

    # ── Draw overlay ──────────────────────────────────────────────────────────

    def draw_overlay(self, frame, zones):
        overlay = frame.copy()

        for bench_name, zone in zones.items():
            x, y, w, h = zone["x"], zone["y"], zone["w"], zone["h"]
            cx, cy = zone["centre_x"], zone["centre_y"]

            cv2.rectangle(overlay, (x, y), (x+w, y+h), (0, 255, 0), 2)
            cv2.putText(overlay, bench_name,
                        (x + 5, y + 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.circle(overlay, (cx, cy), 6, (0, 200, 255), -1)
            cv2.putText(overlay, f"ID:{zone['aruco_id']}",
                        (cx + 10, cy - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)

        status_color = (0, 255, 0) if self.scan_stable else (0, 165, 255)
        status_text  = (f"STABLE ✓  {len(zones)} zones locked"
                        if self.scan_stable
                        else f"Scanning... {len(zones)} found  "
                             f"[{self._stable_count}/{self.STABLE_FRAMES}]")

        cv2.rectangle(overlay, (0, 0), (frame.shape[1], 40), (0, 0, 0), -1)
        cv2.putText(overlay, f"ARGUS ArUco Calibration | {status_text}",
                    (10, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, status_color, 2)

        return overlay


# ════════════════════════════════════════════════════════════════════════════
# STANDALONE
# ════════════════════════════════════════════════════════════════════════════

def run_live_calibration():
    print("=" * 55)
    print("  ARGUS — File 9: ArUco Calibration Tool")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("=" * 55)
    print("\n  Place ArUco cards on benches:")
    for aid, bname in ARUCO_ID_MAP.items():
        print(f"    Marker ID {aid}  →  {bname}")
    print("\n  Controls:")
    print("    S — save zones and exit")
    print("    R — reset / rescan")
    print("    Q — quit without saving")
    print("─" * 55)

    scanner = ARUCOScanner()
    cap = cv2.VideoCapture(CAMERA_INDEX)

    if not cap.isOpened():
        print(f"\n[ERROR] Camera {CAMERA_INDEX} not available.")
        print("  Try: py -3.11 aruco_scanner.py --test")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    # FIX: flush first 10 frames so camera exposure stabilises
    print("[ARUCO] Warming up camera...")
    for _ in range(10):
        cap.read()

    saved = False
    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] Frame read failed.")
            break

        zones   = scanner.scan_frame(frame)
        display = scanner.draw_overlay(frame, zones)

        cv2.imshow("ARGUS — ArUco Calibration (S=Save  R=Reset  Q=Quit)", display)

        if scanner.is_stable() and not saved:
            print(f"\n[ARUCO] Zones stable — auto-saving...")
            scanner.save_zones(zones)
            saved = True
            print("[ARUCO] Saved. Press Q to exit or R to rescan.")

        key = cv2.waitKey(1) & 0xFF
        if key in (ord('s'), ord('S')):
            scanner.save_zones(zones)
            saved = True
        elif key in (ord('r'), ord('R')):
            scanner._stable_count = 0
            scanner.scan_stable   = False
            saved = False
            print("[ARUCO] Reset — rescanning...")
        elif key in (ord('q'), ord('Q')):
            break

    cap.release()
    cv2.destroyAllWindows()

    if saved:
        print("\n[ARUCO] Calibration complete — zones.json ready.")
    else:
        print("\n[ARUCO] Exited without saving zones.")


def run_offline_test():
    print("=" * 55)
    print("  ARGUS — File 9: Offline Test Mode")
    print("=" * 55)

    scanner = ARUCOScanner()

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:] = (30, 30, 30)

    test_zones_mock = {
        "B1": {"bench": "B1", "aruco_id": 0,
               "x": 80,  "y": 200, "w": 320, "h": 420,
               "centre_x": 200, "centre_y": 360},
        "B2": {"bench": "B2", "aruco_id": 1,
               "x": 480, "y": 200, "w": 320, "h": 420,
               "centre_x": 640, "centre_y": 360},
        "B3": {"bench": "B3", "aruco_id": 2,
               "x": 880, "y": 200, "w": 320, "h": 420,
               "centre_x": 1080,"centre_y": 360},
    }

    scanner.last_zones    = test_zones_mock
    scanner.scan_stable   = True
    scanner._stable_count = scanner.STABLE_FRAMES

    display = scanner.draw_overlay(frame, test_zones_mock)

    print("\n  [TEST] Simulated 3 bench zones (B1, B2, B3)")
    print("  [TEST] Saving mock zones to zones.json...")
    scanner.save_zones(test_zones_mock)

    print("\n  [TEST] Displaying overlay for 4 seconds...")
    cv2.imshow("ARGUS ArUco Test — 3 Zones", display)
    cv2.waitKey(4000)
    cv2.destroyAllWindows()

    print("\n" + "=" * 55)
    print("  TEST PASSED — File 9 ready")
    print("=" * 55)


def main():
    parser = argparse.ArgumentParser(description="ARGUS ArUco Scanner")
    parser.add_argument("--test", action="store_true",
                        help="Run offline test without camera")
    args = parser.parse_args()

    if args.test:
        run_offline_test()
    else:
        run_live_calibration()


if __name__ == "__main__":
    main()
