# feature_extractor.py
# ARGUS — Member 2 — Shubham Pitty
# FINAL VERSION — shoulder width normalization
# Works correctly when hips hidden behind exam desk

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
            left_hip       = landmarks[23]
            right_hip      = landmarks[24]

            # ── Feature 1: Shoulder Angle ──
            dx = right_shoulder[0] - left_shoulder[0]
            dy = right_shoulder[1] - left_shoulder[1]
            shoulder_angle = abs(
                math.degrees(math.atan2(dy, dx))
            ) if abs(dx) > 0.001 else 0.0
            shoulder_angle = min(shoulder_angle, 90.0)

            # ── Shared calculations ──
            shoulder_mid_x = (
                left_shoulder[0] + right_shoulder[0]
            ) / 2
            shoulder_mid_y = (
                left_shoulder[1] + right_shoulder[1]
            ) / 2
            shoulder_width = abs(
                right_shoulder[0] - left_shoulder[0]
            )

            # Use shoulder width for all normalization
            # Works even when hips are hidden by exam desk
            norm = shoulder_width if shoulder_width > 0.001 else 0.1

            # ── Feature 2: Head Offset X ──
            head_offset_x = abs(
                nose[0] - shoulder_mid_x
            ) / norm

            # ── Feature 3: Head Offset Y ──
            head_offset_y = abs(
                nose[1] - shoulder_mid_y
            ) / norm

            # ── Feature 4: Left Wrist Distance ──
            # Use shoulder as reference if hip not visible
            ref_lx = left_hip[0]  if left_hip[1]  < 0.95 \
                     else left_shoulder[0]
            ref_ly = left_hip[1]  if left_hip[1]  < 0.95 \
                     else left_shoulder[1]

            ref_rx = right_hip[0] if right_hip[1] < 0.95 \
                     else right_shoulder[0]
            ref_ry = right_hip[1] if right_hip[1] < 0.95 \
                     else right_shoulder[1]

            left_wrist_dist = math.sqrt(
                (left_wrist[0]  - ref_lx) ** 2 +
                (left_wrist[1]  - ref_ly) ** 2
            ) / norm

            # ── Feature 5: Right Wrist Distance ──
            right_wrist_dist = math.sqrt(
                (right_wrist[0] - ref_rx) ** 2 +
                (right_wrist[1] - ref_ry) ** 2
            ) / norm

            # ── Feature 6: Wrist Velocity ──
            if self.prev_left_wrist is None:
                wrist_velocity_avg = 0.0
            else:
                fi = max(frame_interval, 0.01)
                left_vel = math.sqrt(
                    (left_wrist[0]  -
                     self.prev_left_wrist[0])  ** 2 +
                    (left_wrist[1]  -
                     self.prev_left_wrist[1])  ** 2
                ) / fi
                right_vel = math.sqrt(
                    (right_wrist[0] -
                     self.prev_right_wrist[0]) ** 2 +
                    (right_wrist[1] -
                     self.prev_right_wrist[1]) ** 2
                ) / fi
                wrist_velocity_avg = (left_vel + right_vel) / 2

            self.prev_left_wrist  = left_wrist
            self.prev_right_wrist = right_wrist

            # ── Feature 7: Zone Motion Score ──
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


# ── Test ──
if __name__ == "__main__":
    import cv2
    from pose_detector import PoseDetector

    detector  = PoseDetector()
    extractor = FeatureExtractor()
    cap       = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    print("Feature Extractor FINAL version running.")
    print("Sit 2-3 metres away. Press Q to quit.\n")
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
                f"{f[3]:>7.3f} {f[4]:>7.3f} {f[5]:>9.3f} "
                f"{f[6]:>8.3f}",
                end=""
            )

            # Color shoulder angle red if suspicious
            col = (0, 0, 255) if f[0] > 22 else (0, 255, 0)
            cv2.putText(frame,
                f"Shoulder: {f[0]:.1f}deg",
                (10, 90),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
            cv2.putText(frame,
                f"LWrist:{f[3]:.2f} RWrist:{f[4]:.2f}",
                (10, 120),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6, (255, 255, 0), 2)
            cv2.putText(frame,
                f"Velocity:{f[5]:.2f}",
                (10, 150),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6, (255, 165, 0), 2)

        cv2.imshow('ARGUS Feature Extractor FINAL', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print("\nDone.")