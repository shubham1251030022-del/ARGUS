# zone_manager.py
# ARGUS — Member 3 — Sanhita Potdar
# Assigns detected persons to correct bench zones

import json
import os

class ZoneManager:
    def __init__(self, zones_file='zones.json'):
        self.zones_file = zones_file
        self.zones = []
        self.load_zones()

    def load_zones(self):
        if os.path.exists(self.zones_file):
            with open(self.zones_file, 'r') as f:
                self.zones = json.load(f)
            print(f"Loaded {len(self.zones)} zones from {self.zones_file}")
        else:
            # Default 3 demo zones if no file exists
            self.zones = [
                {'bench_id': 'B1', 'roll_number': 'STU001',
                 'name': 'Student 1',
                 'x1': 50,  'y1': 100, 'x2': 250, 'y2': 400,
                 'buffer': 20, 'status': 'ACTIVE'},
                {'bench_id': 'B2', 'roll_number': 'STU002',
                 'name': 'Student 2',
                 'x1': 270, 'y1': 100, 'x2': 470, 'y2': 400,
                 'buffer': 20, 'status': 'ACTIVE'},
                {'bench_id': 'B3', 'roll_number': 'STU003',
                 'name': 'Student 3',
                 'x1': 490, 'y1': 100, 'x2': 690, 'y2': 400,
                 'buffer': 20, 'status': 'ACTIVE'},
            ]
            self.save_zones()
            print("Created default 3 zones")

    def save_zones(self):
        with open(self.zones_file, 'w') as f:
            json.dump(self.zones, f, indent=2)

    def assign_zone(self, centroid_x, centroid_y, frame_width, frame_height):
        # Convert normalized coordinates to pixels
        px = int(centroid_x * frame_width)
        py = int(centroid_y * frame_height)

        for zone in self.zones:
            if zone.get('status') == 'INACTIVE':
                continue

            buf = zone.get('buffer', 20)
            x1 = zone['x1'] - buf
            x2 = zone['x2'] + buf
            y1 = zone['y1'] - buf
            y2 = zone['y2'] + buf

            if x1 <= px <= x2 and y1 <= py <= y2:
                return zone['bench_id']

        return None

    def get_active_zones(self):
        return [z for z in self.zones if z.get('status') != 'INACTIVE']

    def get_all_zones(self):
        return self.zones

    def get_zone_by_id(self, bench_id):
        for zone in self.zones:
            if zone['bench_id'] == bench_id:
                return zone
        return None

    def add_zone(self, zone_data):
        # Check overlap before adding
        for existing in self.zones:
            overlap = self._calculate_overlap(zone_data, existing)
            if overlap > 0.30:
                print(f"Warning: New zone overlaps {existing['bench_id']} by {overlap:.0%}")
        zone_data['status'] = 'ACTIVE'
        zone_data['buffer'] = zone_data.get('buffer', 20)
        self.zones.append(zone_data)
        self.save_zones()
        print(f"Added zone {zone_data['bench_id']}")

    def update_zone(self, bench_id, new_data):
        for i, zone in enumerate(self.zones):
            if zone['bench_id'] == bench_id:
                self.zones[i].update(new_data)
                self.save_zones()
                print(f"Updated zone {bench_id}")
                return True
        return False

    def remove_zone(self, bench_id):
        for zone in self.zones:
            if zone['bench_id'] == bench_id:
                zone['status'] = 'INACTIVE'
                self.save_zones()
                print(f"Deactivated zone {bench_id}")
                return True
        return False

    def _calculate_overlap(self, z1, z2):
        # Calculate overlap percentage between two zones
        x_overlap = max(0, min(z1['x2'], z2['x2']) - max(z1['x1'], z2['x1']))
        y_overlap = max(0, min(z1['y2'], z2['y2']) - max(z1['y1'], z2['y1']))
        overlap_area = x_overlap * y_overlap
        z1_area = (z1['x2'] - z1['x1']) * (z1['y2'] - z1['y1'])
        if z1_area == 0:
            return 0
        return overlap_area / z1_area


# ── Test ──
if __name__ == "__main__":
    import cv2
    import sys
    sys.path.append('.')
    from pose_detector import PoseDetector

    manager  = ZoneManager()
    detector = PoseDetector()

    cap = cv2.VideoCapture(0)
    print("\nZone manager running.")
    print("Sit inside a colored zone — terminal shows which bench you are in.")
    print("Press Q to quit.\n")

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        h, w = frame.shape[:2]

        # Draw all zones
        for zone in manager.get_active_zones():
            cv2.rectangle(frame,
                (zone['x1'], zone['y1']),
                (zone['x2'], zone['y2']),
                (0, 255, 0), 2)
            cv2.putText(frame,
                f"{zone['bench_id']} — {zone['name']}",
                (zone['x1'] + 5, zone['y1'] + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (0, 255, 0), 2)

        # Detect persons and assign to zones
        persons, frame = detector.detect(frame)

        for person in persons:
            cx, cy = person['centroid']
            bench = manager.assign_zone(cx, cy, w, h)

            # Draw centroid
            px = int(cx * w)
            py = int(cy * h)

            if bench:
                cv2.circle(frame, (px, py), 12, (0, 255, 255), -1)
                cv2.putText(frame,
                    f"BENCH: {bench}",
                    (px - 40, py - 20),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 255, 255), 2)
                print(f"\rPerson assigned to: {bench}    ", end="")
            else:
                cv2.circle(frame, (px, py), 12, (0, 0, 255), -1)
                cv2.putText(frame, "No Zone",
                    (px - 30, py - 20),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 0, 255), 2)
                print(f"\rPerson outside all zones    ", end="")

        cv2.imshow('ARGUS Zone Manager', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print("\nDone.")