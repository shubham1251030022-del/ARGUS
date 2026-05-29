# zone_manager.py
# ARGUS — Member 3 — Sanhita Potdar
# FIXED v2:
#   1. Uses absolute path (detection/zones.json) — no more root vs detection conflict
#   2. Handles BOTH dict format (aruco_scanner) and list format (legacy)
#   3. Handles both x,y,w,h and x1,y1,x2,y2 field names
#   4. get_student_name() now checks 'student_name' AND 'name'
#   5. get_zone_by_id() returns consistent format main.py expects (x,y,w,h)

import json
import os

# Always resolve relative to this file → detection/zones.json
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


class ZoneManager:
    def __init__(self, zones_file=None):
        # FIX: use absolute path so it works regardless of working directory
        self.zones_file = zones_file or os.path.join(_THIS_DIR, "zones.json")
        self.zones = []
        self.load_zones()

    # ── Load ──────────────────────────────────────────────────────────────────

    def load_zones(self):
        """
        Load zones from JSON file.
        Handles two formats:
          A) Dict format (aruco_scanner): {"B1": {x, y, w, h, student_name, ...}}
          B) List format (legacy):        [{bench_id, x1, y1, x2, y2, name, ...}]
        Both are converted to internal unified format.
        """
        if os.path.exists(self.zones_file):
            try:
                with open(self.zones_file, 'r') as f:
                    raw = json.load(f)

                if isinstance(raw, dict):
                    # Format A — aruco_scanner / app.py default zones
                    self.zones = []
                    for bench_id, data in raw.items():
                        self.zones.append(self._normalize_zone(bench_id, data))
                elif isinstance(raw, list):
                    # Format B — legacy zone_manager format
                    self.zones = []
                    for item in raw:
                        bid = item.get('bench_id', item.get('name', 'B?'))
                        self.zones.append(self._normalize_zone(bid, item))

                print(f"Loaded {len(self.zones)} zones from {self.zones_file}")

            except Exception as e:
                print(f"[ZoneManager] Load error: {e} — using defaults")
                self._create_defaults()
        else:
            self._create_defaults()

    def _normalize_zone(self, bench_id, data):
        """
        Convert any zone format to unified internal format.
        Internal format uses x, y, w, h (matches aruco_scanner + main.py).
        """
        # Handle x,y,w,h format (aruco_scanner)
        if 'x' in data and 'w' in data:
            x  = int(data.get('x', 0))
            y  = int(data.get('y', 0))
            w  = int(data.get('w', 200))
            h  = int(data.get('h', 300))
        # Handle x1,y1,x2,y2 format (legacy)
        elif 'x1' in data:
            x  = int(data.get('x1', 0))
            y  = int(data.get('y1', 0))
            w  = int(data.get('x2', 200)) - x
            h  = int(data.get('y2', 300)) - y
        else:
            x, y, w, h = 0, 0, 200, 300

        # Normalize student name field — check both keys
        name = (data.get('student_name') or
                data.get('name') or
                'Unknown')

        roll = (data.get('roll_number') or
                data.get('roll') or '')

        return {
            'bench_id'    : bench_id,
            'x'           : x,
            'y'           : y,
            'w'           : w,
            'h'           : h,
            'name'        : name,
            'student_name': name,
            'roll_number' : roll,
            'status'      : data.get('status', 'ACTIVE'),
            'buffer'      : int(data.get('buffer', 20)),
            'aruco_id'    : data.get('aruco_id', -1),
        }

    def _create_defaults(self):
        """Default 3 zones dividing 1280x720 into equal strips."""
        self.zones = [
            self._normalize_zone('B1', {
                'x': 0, 'y': 50, 'w': 380, 'h': 620,
                'student_name': 'Unknown', 'roll_number': ''
            }),
            self._normalize_zone('B2', {
                'x': 400, 'y': 50, 'w': 380, 'h': 620,
                'student_name': 'Unknown', 'roll_number': ''
            }),
            self._normalize_zone('B3', {
                'x': 800, 'y': 50, 'w': 380, 'h': 620,
                'student_name': 'Unknown', 'roll_number': ''
            }),
        ]
        self.save_zones()
        print("[ZoneManager] Created default 3 zones")

    # ── Save ──────────────────────────────────────────────────────────────────

    def save_zones(self):
        """Save zones in dict format (compatible with aruco_scanner)."""
        output = {}
        for z in self.zones:
            bid = z['bench_id']
            output[bid] = {
                'x'           : z['x'],
                'y'           : z['y'],
                'w'           : z['w'],
                'h'           : z['h'],
                'aruco_id'    : z.get('aruco_id', -1),
                'student_name': z['student_name'],
                'roll_number' : z['roll_number'],
                'status'      : z['status'],
            }
        with open(self.zones_file, 'w') as f:
            json.dump(output, f, indent=2)

    # ── Core: assign person to bench ──────────────────────────────────────────

    def assign_zone(self, centroid_x, centroid_y, frame_width, frame_height):
        """
        Convert normalized centroid to pixels, find which bench zone it falls in.
        Returns bench_id string or None.
        """
        px = int(centroid_x * frame_width)
        py = int(centroid_y * frame_height)

        for zone in self.zones:
            if zone.get('status') == 'INACTIVE':
                continue

            buf = zone.get('buffer', 20)
            x1  = zone['x'] - buf
            y1  = zone['y'] - buf
            x2  = zone['x'] + zone['w'] + buf
            y2  = zone['y'] + zone['h'] + buf

            if x1 <= px <= x2 and y1 <= py <= y2:
                return zone['bench_id']

        return None

    # ── Getters ───────────────────────────────────────────────────────────────

    def get_active_zones(self):
        return [z for z in self.zones if z.get('status') != 'INACTIVE']

    def get_all_zones(self):
        return self.zones

    def get_zone_by_id(self, bench_id):
        """Returns zone dict in x,y,w,h format (for main.py overlay drawing)."""
        for zone in self.zones:
            if zone['bench_id'] == bench_id:
                return zone
        return None

    def get_student_name(self, bench_id):
        """Returns student name — checks both 'student_name' and 'name' fields."""
        zone = self.get_zone_by_id(bench_id)
        if zone:
            return (zone.get('student_name') or
                    zone.get('name') or 'Unknown')
        return 'Unknown'

    def get_roll_number(self, bench_id):
        zone = self.get_zone_by_id(bench_id)
        if zone:
            return zone.get('roll_number', '')
        return ''

    # ── Modifiers ─────────────────────────────────────────────────────────────

    def add_zone(self, zone_data):
        bench_id = zone_data.get('bench_id', 'B?')
        self.zones.append(self._normalize_zone(bench_id, zone_data))
        self.save_zones()
        print(f"[ZoneManager] Added zone {bench_id}")

    def update_zone(self, bench_id, new_data):
        for i, zone in enumerate(self.zones):
            if zone['bench_id'] == bench_id:
                updated = dict(zone)
                updated.update(new_data)
                self.zones[i] = self._normalize_zone(bench_id, updated)
                self.save_zones()
                return True
        return False

    def remove_zone(self, bench_id):
        for zone in self.zones:
            if zone['bench_id'] == bench_id:
                zone['status'] = 'INACTIVE'
                self.save_zones()
                return True
        return False

    def _calculate_overlap(self, z1, z2):
        x_overlap = max(0, min(z1['x']+z1['w'], z2['x']+z2['w']) - max(z1['x'], z2['x']))
        y_overlap = max(0, min(z1['y']+z1['h'], z2['y']+z2['h']) - max(z1['y'], z2['y']))
        area1 = z1['w'] * z1['h']
        return (x_overlap * y_overlap / area1) if area1 > 0 else 0
