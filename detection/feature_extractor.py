# feature_extractor.py
# ARGUS — Member 2 — Shubham Pitty
# FIXED v2 — Horizontal-only wrist distance
#
# ROOT CAUSE OF FALSE ALERTS (old version):
#   wrist_dist = total distance from hip to wrist
#   When writing at desk: arm drops DOWN → large vertical component
#   → high wrist_dist → system incorrectly reads writing as arm extension
#
# FIX:
#   wrist_dist = HORIZONTAL distance from shoulder only
#   Writing at desk: wrist moves DOWN, not sideways → near-zero horizontal dist
#   Reaching toward neighbor: wrist moves SIDEWAYS → large horizontal dist
#   This correctly distinguishes writing from cheating at ANY camera distance

import math
import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


class FeatureExtractor:
    def __init__(self):
        self.prev_left_wrist  = None
        self.prev_right_wrist = None

    def extract(self, landmarks,
                zone_motion_score=0.0,
                frame_interval=0.033):
        try:
            nose           = landmarks[0]
            left_shoulder  = landmarks[11]
            right_shoulder = landmarks[12]
            left_wrist     = landmarks[15]
            right_wrist    = landmarks[16]

            # ── Feature 1: Shoulder Angle ──────────────────────────────────
            dx = right_shoulder[0] - left_shoulder[0]
            dy = right_shoulder[1] - left_shoulder[1]
            shoulder_angle = abs(
                math.degrees(math.atan2(dy, dx))
            ) if abs(dx) > 0.001 else 0.0
            shoulder_angle = min(shoulder_angle, 90.0)

            # ── Shared: shoulder geometry ───────────────────────────────────
            shoulder_mid_x = (left_shoulder[0] + right_shoulder[0]) / 2
            shoulder_mid_y = (left_shoulder[1] + right_shoulder[1]) / 2
            shoulder_width = abs(right_shoulder[0] - left_shoulder[0])
            norm = shoulder_width if shoulder_width > 0.001 else 0.1

            # ── Feature 2: Head Offset X ────────────────────────────────────
            head_offset_x = abs(nose[0] - shoulder_mid_x) / norm

            # ── Feature 3: Head Offset Y ────────────────────────────────────
            head_offset_y = abs(nose[1] - shoulder_mid_y) / norm

            # ── Features 4 & 5: Wrist Distance ─────────────────────────────
            # FIX: Use HORIZONTAL distance from shoulder only.
            #
            # OLD: total distance from hip → high for normal writing (arm drops down)
            # NEW: horizontal distance from shoulder → near-zero for writing
            #      (arm goes down to desk), high for reaching toward neighbor
            #
            # Writing at desk:  wrist_x ≈ shoulder_x  → dist ≈ 0.0-0.2
            # Reaching neighbor: wrist_x far from shoulder_x → dist ≈ 0.5-2.0
            #
            # This is also fully distance-invariant since norm = shoulder_width

            left_wrist_dist  = abs(left_wrist[0]  - left_shoulder[0])  / norm
            right_wrist_dist = abs(right_wrist[0] - right_shoulder[0]) / norm

            # ── Feature 6: Wrist Velocity ───────────────────────────────────
            if self.prev_left_wrist is None:
                wrist_velocity_avg = 0.0
            else:
                fi = max(frame_interval, 0.01)
                left_vel = math.sqrt(
                    (left_wrist[0]  - self.prev_left_wrist[0])  ** 2 +
                    (left_wrist[1]  - self.prev_left_wrist[1])  ** 2
                ) / fi
                right_vel = math.sqrt(
                    (right_wrist[0] - self.prev_right_wrist[0]) ** 2 +
                    (right_wrist[1] - self.prev_right_wrist[1]) ** 2
                ) / fi
                wrist_velocity_avg = (left_vel + right_vel) / 2

            self.prev_left_wrist  = left_wrist
            self.prev_right_wrist = right_wrist

            # ── Feature 7: Zone Motion Score ────────────────────────────────
            return [
                round(shoulder_angle,     4),
                round(head_offset_x,      4),
                round(head_offset_y,      4),
                round(left_wrist_dist,    4),
                round(right_wrist_dist,   4),
                round(wrist_velocity_avg, 4),
                round(zone_motion_score,  4),
            ]

        except Exception as e:
            print(f"Feature extraction error: {e}")
            return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import cv2
    from pose_detector import PoseDetector

    detector  = PoseDetector()
    extractor = FeatureExtractor()
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    print("Feature Extractor v2 — Horizontal wrist distance")
    print("Sit 2-3m away. Write naturally. Wrist dist should stay LOW.")
    print("Extend arm sideways — Wrist dist should go HIGH.")
    print(f"{'Shldr':>8} {'HdX':>7} {'HdY':>7} "
          f"{'LWr':>7} {'RWr':>7} {'Vel':>9} {'Motion':>8}")
    print("-" * 65)

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        persons, frame = detector.detect(frame)
        for person in persons:
            f = extractor.extract(person['landmarks'])
            print(
                f"\r{f[0]:>8.2f} {f[1]:>7.3f} {f[2]:>7.3f} "
                f"{f[3]:>7.3f} {f[4]:>7.3f} {f[5]:>9.3f} {f[6]:>8.3f}",
                end=""
            )
            col = (0, 0, 255) if f[0] > 38 else (0, 255, 0)
            cv2.putText(frame, f"Shoulder: {f[0]:.1f}deg", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
            arm_col = (0, 0, 255) if (f[3] > 0.5 or f[4] > 0.5) else (0, 255, 0)
            cv2.putText(frame, f"LWrist_H:{f[3]:.2f}  RWrist_H:{f[4]:.2f}",
                        (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.6, arm_col, 2)
        cv2.imshow('ARGUS Feature Extractor v2', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    cap.release()
    cv2.destroyAllWindows()
    print("\nDone.")
