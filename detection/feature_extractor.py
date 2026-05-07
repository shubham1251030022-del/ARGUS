# feature_extractor.py
# ARGUS — Member 2 — Shubham Pitty
# Calculates 7 behavioral features from pose landmarks

import numpy as np
import math

class FeatureExtractor:
    def __init__(self):
        self.prev_left_wrist  = None
        self.prev_right_wrist = None

    def extract(self, landmarks, zone_motion_score=0.0, frame_interval=0.033):
        try:
            nose           = landmarks[0]
            left_shoulder  = landmarks[11]
            right_shoulder = landmarks[12]
            left_wrist     = landmarks[15]
            right_wrist    = landmarks[16]
            left_hip       = landmarks[23]
            right_hip      = landmarks[24]

            # Feature 1 — Shoulder Angle
            dx = right_shoulder[0] - left_shoulder[0]
            dy = right_shoulder[1] - left_shoulder[1]
            shoulder_angle = abs(math.degrees(math.atan2(dy, dx))) if abs(dx) > 0.001 else 0.0

            # Feature 2 & 3 — Head Offset X and Y
            shoulder_mid_x = (left_shoulder[0] + right_shoulder[0]) / 2
            shoulder_mid_y = (left_shoulder[1] + right_shoulder[1]) / 2
            shoulder_width = abs(right_shoulder[0] - left_shoulder[0])
            torso_height   = abs(shoulder_mid_y - ((left_hip[1] + right_hip[1]) / 2))

            head_offset_x = abs(nose[0] - shoulder_mid_x) / shoulder_width if shoulder_width > 0.001 else 0.0
            head_offset_y = abs(nose[1] - shoulder_mid_y) / torso_height   if torso_height  > 0.001 else 0.0

            # Feature 4 & 5 — Wrist Distances
            left_wrist_dist = math.sqrt(
                (left_wrist[0]  - left_hip[0])**2 +
                (left_wrist[1]  - left_hip[1])**2
            ) / torso_height if torso_height > 0.001 else 0.0

            right_wrist_dist = math.sqrt(
                (right_wrist[0] - right_hip[0])**2 +
                (right_wrist[1] - right_hip[1])**2
            ) / torso_height if torso_height > 0.001 else 0.0

            # Feature 6 — Wrist Velocity
            if self.prev_left_wrist is None:
                wrist_velocity_avg = 0.0
            else:
                left_vel = math.sqrt(
                    (left_wrist[0]  - self.prev_left_wrist[0])**2 +
                    (left_wrist[1]  - self.prev_left_wrist[1])**2
                ) / frame_interval

                right_vel = math.sqrt(
                    (right_wrist[0] - self.prev_right_wrist[0])**2 +
                    (right_wrist[1] - self.prev_right_wrist[1])**2
                ) / frame_interval

                wrist_velocity_avg = (left_vel + right_vel) / 2

            self.prev_left_wrist  = left_wrist
            self.prev_right_wrist = right_wrist

            # Feature 7 — Zone Motion Score (from motion_zones.py)
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
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from pose_detector import PoseDetector

    detector  = PoseDetector()
    extractor = FeatureExtractor()

    cap = cv2.VideoCapture(0)
    print("Feature extractor running. Press Q to quit.")
    print(f"\n{'Shoulder':>10} {'HeadX':>8} {'HeadY':>8} {'LWrist':>8} {'RWrist':>8} {'Velocity':>10} {'Motion':>8}")
    print("-" * 70)

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        persons, frame = detector.detect(frame)

        for person in persons:
            f = extractor.extract(person['landmarks'])

            print(f"\r{f[0]:>10.3f} {f[1]:>8.3f} {f[2]:>8.3f} "
                  f"{f[3]:>8.3f} {f[4]:>8.3f} {f[5]:>10.3f} "
                  f"{f[6]:>8.3f}", end="")

            # Show on webcam window
            color = (0, 0, 255) if f[0] > 20 else (0, 255, 0)
            cv2.putText(frame,
                f"Shoulder Angle: {f[0]:.1f} deg",
                (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            cv2.putText(frame,
                f"L.Wrist: {f[3]:.2f}  R.Wrist: {f[4]:.2f}",
                (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
            cv2.putText(frame,
                f"Velocity: {f[5]:.2f}",
                (10, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 165, 0), 2)

        cv2.imshow('ARGUS Feature Extractor', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print("\nDone.")