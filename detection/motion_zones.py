# motion_zones.py
# ARGUS — Member 3 — Sanhita Potdar
# Detects motion in each bench zone using pixel difference

import cv2
import numpy as np

class MotionZones:
    def __init__(self):
        self.prev_frame = None

    def detect(self, frame, zones):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        motion_scores = {}

        for zone in zones:
            bench_id = zone['bench_id']
            x1, y1, x2, y2 = zone['x1'], zone['y1'], zone['x2'], zone['y2']

            # Keep coordinates within frame boundaries
            h, w = frame.shape[:2]
            x1 = max(0, min(x1, w-1))
            x2 = max(0, min(x2, w-1))
            y1 = max(0, min(y1, h-1))
            y2 = max(0, min(y2, h-1))

            if x2 <= x1 or y2 <= y1:
                motion_scores[bench_id] = 0.0
                continue

            # Crop zone from current frame
            zone_gray = gray[y1:y2, x1:x2]

            if self.prev_frame is None:
                motion_scores[bench_id] = 0.0
                continue

            # Crop same zone from previous frame
            prev_gray = self.prev_frame[y1:y2, x1:x2]

            if zone_gray.shape != prev_gray.shape:
                motion_scores[bench_id] = 0.0
                continue

            # Compute pixel difference
            diff = cv2.absdiff(zone_gray, prev_gray)
            _, thresh = cv2.threshold(diff, 10, 255, cv2.THRESH_BINARY)
            score = np.mean(thresh) / 255.0
            motion_scores[bench_id] = round(score, 4)

            # Draw zone rectangle on frame
            color = (0, 0, 255) if score > 0.08 else \
                    (0, 165, 255) if score > 0.03 else \
                    (0, 255, 0)

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame,
                f"{bench_id}: {score:.2f}",
                (x1 + 5, y1 + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        self.prev_frame = gray
        return motion_scores, frame


# ── Test ──
if __name__ == "__main__":
    import cv2

    # 3 test bench zones
    test_zones = [
        {'bench_id': 'B1', 'x1': 50,  'y1': 100, 'x2': 250, 'y2': 400},
        {'bench_id': 'B2', 'x1': 270, 'y1': 100, 'x2': 470, 'y2': 400},
        {'bench_id': 'B3', 'x1': 490, 'y1': 100, 'x2': 690, 'y2': 400},
    ]

    detector = MotionZones()
    cap = cv2.VideoCapture(0)

    print("Motion zones running.")
    print("Green = calm | Orange = some motion | Red = high motion")
    print("Wave your hand inside a zone to see score increase.")
    print("Press Q to quit.")

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        scores, frame = detector.detect(frame, test_zones)

        print(f"\rB1:{scores.get('B1',0):.2f}  "
              f"B2:{scores.get('B2',0):.2f}  "
              f"B3:{scores.get('B3',0):.2f}", end="")

        cv2.imshow('ARGUS Motion Zones', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print("\nDone.")