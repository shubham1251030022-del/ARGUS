# pose_detector.py
# ARGUS — Member 2 — Shubham Pitty
# Using new MediaPipe Pose Landmarker API

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import numpy as np
import time
import urllib.request
import os

# Download model file if not present
MODEL_PATH = "pose_landmarker.task"
if not os.path.exists(MODEL_PATH):
    print("Downloading MediaPipe model... please wait...")
    url = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
    urllib.request.urlretrieve(url, MODEL_PATH)
    print("Model downloaded.")

class PoseDetector:
    def __init__(self):
        base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
        options = vision.PoseLandmarkerOptions(
            base_options=base_options,
            output_segmentation_masks=False,
            num_poses=6
        )
        self.detector = vision.PoseLandmarker.create_from_options(options)

    def detect(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self.detector.detect(mp_image)

        persons = []
        h, w = frame.shape[:2]

        for pose_landmarks in result.pose_landmarks:
            lm = pose_landmarks

            def get(idx):
                return (lm[idx].x, lm[idx].y, lm[idx].z)

            landmarks = {
                0:  get(0),   # nose
                7:  get(7),   # left ear
                8:  get(8),   # right ear
                11: get(11),  # left shoulder
                12: get(12),  # right shoulder
                15: get(15),  # left wrist
                16: get(16),  # right wrist
                23: get(23),  # left hip
                24: get(24),  # right hip
            }

            cx = (lm[23].x + lm[24].x) / 2
            cy = (lm[23].y + lm[24].y) / 2

            persons.append({
                'landmarks': landmarks,
                'centroid': (cx, cy)
            })

            # Draw landmarks on frame
            for idx in [0,7,8,11,12,15,16,23,24]:
                x = int(lm[idx].x * w)
                y = int(lm[idx].y * h)
                cv2.circle(frame, (x, y), 6, (0, 255, 0), -1)

            # Draw centroid
            cv2.circle(frame,
                (int(cx * w), int(cy * h)),
                12, (0, 0, 255), -1)

            # Connect shoulders
            cv2.line(frame,
                (int(lm[11].x*w), int(lm[11].y*h)),
                (int(lm[12].x*w), int(lm[12].y*h)),
                (0, 255, 0), 2)

        if persons:
            cv2.putText(frame, f"Persons: {len(persons)}",
                (10, 60), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (0, 255, 0), 2)
        else:
            cv2.putText(frame, "No Person Detected",
                (10, 60), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (0, 0, 255), 2)

        return persons, frame


# ── Test ──
if __name__ == "__main__":
    detector = PoseDetector()
    cap = cv2.VideoCapture(0)
    prev_time = 0
    print("Running. Press Q to quit.")

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        persons, frame = detector.detect(frame)

        curr_time = time.time()
        fps = 1 / (curr_time - prev_time + 0.001)
        prev_time = curr_time

        cv2.putText(frame, f"FPS: {int(fps)}",
            (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
            0.8, (255, 255, 0), 2)

        for p in persons:
            cx, cy = p['centroid']
            lm = p['landmarks']
            print(f"\rCentroid=({cx:.2f},{cy:.2f}) "
                  f"L.Shldr=({lm[11][0]:.2f},{lm[11][1]:.2f}) "
                  f"FPS={int(fps)}", end="")

        cv2.imshow('ARGUS Pose Detector', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print("\nDone.")