# pose_detector.py
# ARGUS — Member 2 — Shubham Pitty
# FINAL VERSION — shoulder centroid, works with desk blocking hips

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import time
import os
import urllib.request

MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "pose_landmarker.task"
)

if not os.path.exists(MODEL_PATH):
    print("Downloading MediaPipe model...")
    url = ("https://storage.googleapis.com/mediapipe-models/"
           "pose_landmarker/pose_landmarker_lite/float16/1/"
           "pose_landmarker_lite.task")
    urllib.request.urlretrieve(url, MODEL_PATH)
    print("Model downloaded.")

class PoseDetector:
    def __init__(self):
        base_options = python.BaseOptions(
            model_asset_path=MODEL_PATH
        )
        options = vision.PoseLandmarkerOptions(
            base_options=base_options,
            output_segmentation_masks=False,
            num_poses=6,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5
        )
        self.detector = vision.PoseLandmarker.create_from_options(
            options
        )

    def detect(self, frame):
        rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_img = mp.Image(
            image_format=mp.ImageFormat.SRGB, data=rgb
        )
        result  = self.detector.detect(mp_img)
        persons = []
        h, w    = frame.shape[:2]

        for pose_landmarks in result.pose_landmarks:
            lm = pose_landmarks

            def get(idx):
                return (lm[idx].x, lm[idx].y, lm[idx].z)

            landmarks = {
                0 : get(0),   # nose
                7 : get(7),   # left ear
                8 : get(8),   # right ear
                11: get(11),  # left shoulder
                12: get(12),  # right shoulder
                15: get(15),  # left wrist
                16: get(16),  # right wrist
                23: get(23),  # left hip
                24: get(24),  # right hip
            }

            # Shoulder midpoint as centroid
            # Always visible above exam desk
            cx = (lm[11].x + lm[12].x) / 2
            cy = (lm[11].y + lm[12].y) / 2

            persons.append({
                'landmarks': landmarks,
                'centroid' : (cx, cy)
            })

            # Draw key landmarks
            key_indices = [0, 7, 8, 11, 12, 15, 16, 23, 24]
            for idx in key_indices:
                x = int(lm[idx].x * w)
                y = int(lm[idx].y * h)
                cv2.circle(frame, (x, y), 5, (0, 255, 0), -1)

            # Draw shoulder line
            cv2.line(frame,
                (int(lm[11].x * w), int(lm[11].y * h)),
                (int(lm[12].x * w), int(lm[12].y * h)),
                (0, 255, 0), 2)

            # Draw centroid
            cv2.circle(frame,
                (int(cx * w), int(cy * h)),
                10, (0, 0, 255), -1)

        # Person count
        count = len(persons)
        color = (0, 255, 0) if count > 0 else (0, 0, 255)
        cv2.putText(frame,
            f"Persons: {count}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8, color, 2)

        return persons, frame


# ── Test ──
if __name__ == "__main__":
    detector  = PoseDetector()
    cap       = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("ERROR: Cannot open webcam. Try VideoCapture(1)")
        exit()
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    prev_time = 0

    print("Pose detector FINAL version running.")
    print("Sit 2-3 metres away. Press Q to quit.")

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        persons, frame = detector.detect(frame)

        curr_time = time.time()
        fps       = 1 / (curr_time - prev_time + 0.001)
        prev_time = curr_time

        cv2.putText(frame, f"FPS: {int(fps)}",
            (10, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7, (255, 255, 0), 2)

        for p in persons:
            cx, cy = p['centroid']
            lm     = p['landmarks']
            print(
                f"\rCentroid=({cx:.2f},{cy:.2f}) "
                f"L.Shldr=({lm[11][0]:.2f},{lm[11][1]:.2f}) "
                f"R.Shldr=({lm[12][0]:.2f},{lm[12][1]:.2f}) "
                f"FPS={int(fps)}",
                end=""
            )

        cv2.imshow('ARGUS Pose Detector FINAL', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print("\nDone.")