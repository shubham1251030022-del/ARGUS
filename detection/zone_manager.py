"""
ARGUS — detection/zone_manager.py  [v4 — Nearest-zone fallback]
Member 3: Sanhita Potdar | VIT Pune CSAIML-E Group 01

v4 fixes:
  1. assign_zone() now has a nearest-zone fallback:
     If centroid doesn't fall inside any zone (even with buffer),
     find the zone whose centre_x is nearest to the centroid's x.
     Only uses fallback if person is within 150px of a zone boundary.
     This handles the case where self-calibrating zones are slightly
     off by a few pixels — person still gets assigned correctly.

  2. Fallback prints a [ZONE-NEAR] message (not ZONE-MISS) so you can
     distinguish "slightly outside zone" from "completely off screen".

  3. All v3 fixes retained (reload_zones, both name fields, absolute path).
"""

import json
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


class ZoneManager:

    def __init__(self, zones_file=None):
        self.zones_file = zones_file or os.path.join(_THIS_DIR, "zones.json")
        self.zones = []
        self.load_zones()

    def reload_zones(self):
        self.zones = []
        self.load_zones()
        print(f"[ZoneManager] Reloaded — {len(self.zones)} zones")

    def load_zones(self):
        if os.path.exists(self.zones_file):
            try:
                with open(self.zones_file, "r") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    self.zones = [self._normalize_zone(k, v) for k, v in raw.items()]
                elif isinstance(raw, list):
                    self.zones = [self._normalize_zone(
                        z.get("bench_id", z.get("bench", "B?")), z) for z in raw]
                print(f"[ZoneManager] Loaded {len(self.zones)} zones")
            except Exception as e:
                print(f"[ZoneManager] Load error: {e} — using defaults")
                self._create_defaults()
        else:
            self._create_defaults()

    def _normalize_zone(self, bench_id, data):
        if "x" in data and "w" in data:
            x, y, w, h = int(data["x"]), int(data["y"]), int(data["w"]), int(data["h"])
        elif "x1" in data:
            x = int(data["x1"]); y = int(data["y1"])
            w = int(data["x2"]) - x; h = int(data["y2"]) - y
        else:
            x, y, w, h = 0, 0, 200, 300

        name = (data.get("student_name") or data.get("name") or
                data.get("candidate_name") or "Unknown")
        roll = data.get("roll_number") or data.get("roll") or ""

        return {
            "bench_id"    : bench_id,
            "x": x, "y": y, "w": w, "h": h,
            "name"        : name,
            "student_name": name,
            "roll_number" : roll,
            "status"      : data.get("status", "ACTIVE"),
            "buffer"      : int(data.get("buffer", 30)),   # increased buffer
            "aruco_id"    : data.get("aruco_id", -1),
        }

    def _create_defaults(self):
        self.zones = [
            self._normalize_zone("B1", {"x": 20,  "y": 80, "w": 400, "h": 580,
                                        "student_name": "Unknown"}),
            self._normalize_zone("B2", {"x": 440, "y": 80, "w": 400, "h": 580,
                                        "student_name": "Unknown"}),
            self._normalize_zone("B3", {"x": 860, "y": 80, "w": 400, "h": 580,
                                        "student_name": "Unknown"}),
        ]
        self.save_zones()
        print("[ZoneManager] Created default 3 zones")

    def save_zones(self):
        output = {}
        for z in self.zones:
            bid = z["bench_id"]
            output[bid] = {
                "x": z["x"], "y": z["y"], "w": z["w"], "h": z["h"],
                "aruco_id"    : z.get("aruco_id", -1),
                "student_name": z["student_name"],
                "roll_number" : z["roll_number"],
                "status"      : z["status"],
            }
        with open(self.zones_file, "w") as f:
            json.dump(output, f, indent=2)

    def assign_zone(self, cx, cy, frame_w, frame_h):
        """
        Assign a person centroid (normalised 0-1) to a bench zone.

        Priority:
          1. Exact match — centroid inside zone + buffer
          2. Nearest-zone fallback — centroid within 150px of closest zone
             horizontally. Handles slight calibration offsets gracefully.
        """
        px = int(cx * frame_w)
        py = int(cy * frame_h)
        active = [z for z in self.zones if z.get("status") != "INACTIVE"]

        # ── Pass 1: exact match with buffer ───────────────────────────────────
        for zone in active:
            buf = zone.get("buffer", 30)
            if (zone["x"] - buf <= px <= zone["x"] + zone["w"] + buf and
                    zone["y"] - buf <= py <= zone["y"] + zone["h"] + buf):
                return zone["bench_id"]

        # ── Pass 2: nearest-zone fallback ─────────────────────────────────────
        # Find zone whose horizontal centre is closest to person's x
        # Only assign if person is within 150px of the zone boundary
        NEAR_THRESHOLD = 150   # px

        best_zone = None
        best_dist = float("inf")

        for zone in active:
            zone_cx = zone["x"] + zone["w"] // 2
            # Horizontal distance from person to nearest zone edge
            if px < zone["x"]:
                h_dist = zone["x"] - px
            elif px > zone["x"] + zone["w"]:
                h_dist = px - (zone["x"] + zone["w"])
            else:
                h_dist = 0   # horizontally inside zone

            # Vertical distance
            if py < zone["y"]:
                v_dist = zone["y"] - py
            elif py > zone["y"] + zone["h"]:
                v_dist = py - (zone["y"] + zone["h"])
            else:
                v_dist = 0

            total_dist = max(h_dist, v_dist)   # Chebyshev distance
            if total_dist < best_dist:
                best_dist = total_dist
                best_zone = zone

        if best_zone and best_dist <= NEAR_THRESHOLD:
            print(f"  [ZONE-NEAR] px={px} py={py} → {best_zone['bench_id']} "
                  f"(dist={best_dist}px — slightly outside zone, assigned to nearest)")
            return best_zone["bench_id"]

        return None   # truly outside all zones

    def get_active_zones(self):
        return [z for z in self.zones if z.get("status") != "INACTIVE"]

    def get_all_zones(self):
        return self.zones

    def get_zone_by_id(self, bench_id):
        for z in self.zones:
            if z["bench_id"] == bench_id:
                return z
        return None

    def get_student_name(self, bench_id):
        z = self.get_zone_by_id(bench_id)
        return (z.get("student_name") or z.get("name") or "Unknown") if z else "Unknown"

    def get_roll_number(self, bench_id):
        z = self.get_zone_by_id(bench_id)
        return z.get("roll_number", "") if z else ""

    def add_zone(self, zone_data):
        bid = zone_data.get("bench_id", "B?")
        self.zones.append(self._normalize_zone(bid, zone_data))
        self.save_zones()

    def update_zone(self, bench_id, new_data):
        for i, z in enumerate(self.zones):
            if z["bench_id"] == bench_id:
                updated = dict(z); updated.update(new_data)
                self.zones[i] = self._normalize_zone(bench_id, updated)
                self.save_zones()
                return True
        return False

    def remove_zone(self, bench_id):
        for z in self.zones:
            if z["bench_id"] == bench_id:
                z["status"] = "INACTIVE"
                self.save_zones()
                return True
        return False
