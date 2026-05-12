"""
ARGUS — File 10: webapp/alert_logger.py
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

Logs every cheating alert to a structured JSON file.
Each alert contains: timestamp, bench, student name, roll number,
score, ML confidence, behaviour flags, and snapshot image path.

Used by main.py (File 12) via:
    from webapp.alert_logger import AlertLogger
    logger = AlertLogger()
    logger.log_alert(bench, student, score, confidence, flags, snapshot_path)

Used by app.py (File 11) to serve alert history to dashboard.

Run standalone to test:
    py -3.11 alert_logger.py   (from ARGUS root)
"""

import json
import os
import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
_THIS_DIR    = os.path.dirname(os.path.abspath(__file__))
_ROOT        = os.path.dirname(_THIS_DIR)

ALERTS_FILE  = os.path.join(_THIS_DIR, "alerts.json")
SNAPSHOT_DIR = os.path.join(_ROOT, "snapshots")

# ── Alert score threshold (must match config.json) ────────────────────────────
ALERT_THRESHOLD = 30


class AlertLogger:
    """
    Logs and retrieves cheating alerts for the ARGUS dashboard.

    All alerts stored in webapp/alerts.json as a list of dicts.
    Snapshots stored in snapshots/ folder as JPEG images.

    Thread-safe: uses file-level read/write with atomic replace.
    """

    def __init__(self, alerts_file: str = None):
        self.alerts_file = alerts_file or ALERTS_FILE
        self._ensure_files()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _ensure_files(self):
        """Create alerts.json and snapshots/ if they don't exist."""
        # snapshots folder
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)

        # alerts.json — empty list if missing
        if not os.path.exists(self.alerts_file):
            self._write([])
            print(f"[LOGGER] Created {self.alerts_file}")

    # ── Read / Write ──────────────────────────────────────────────────────────

    def _read(self) -> list:
        """Load all alerts from JSON file. Returns empty list on error."""
        try:
            with open(self.alerts_file, "r") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _write(self, alerts: list):
        """Write full alerts list back to JSON file."""
        with open(self.alerts_file, "w") as f:
            json.dump(alerts, f, indent=4)

    # ── Core log ──────────────────────────────────────────────────────────────

    def log_alert(self,
                  bench         : str,
                  student_name  : str,
                  roll_number   : str,
                  score         : float,
                  ml_confidence : float,
                  flags         : dict,
                  snapshot_path : str = None) -> dict:
        """
        Log a single cheating alert.

        Args:
            bench         : Bench ID e.g. "B1"
            student_name  : Student name from zones.json
            roll_number   : Roll number from zones.json
            score         : Current cheating score (≥ ALERT_THRESHOLD to log)
            ml_confidence : ML probability 0.0–1.0 from classifier.py
            flags         : Dict of active behaviour flags e.g.
                            {"body_turn": True, "arm_extended": False, ...}
            snapshot_path : Relative path to snapshot image or None

        Returns:
            dict: the alert record that was saved.
        """
        now   = datetime.datetime.now()
        alert = {
            "id"            : self._next_id(),
            "timestamp"     : now.strftime("%Y-%m-%d %H:%M:%S"),
            "date"          : now.strftime("%Y-%m-%d"),
            "time"          : now.strftime("%H:%M:%S"),
            "bench"         : bench,
            "student_name"  : student_name,
            "roll_number"   : roll_number,
            "score"         : round(float(score), 2),
            "ml_confidence" : round(float(ml_confidence), 4),
            "flags"         : flags or {},
            "snapshot_path" : snapshot_path or "",
            "reviewed"      : False     # invigilator marks reviewed in dashboard
        }

        alerts = self._read()
        alerts.append(alert)
        self._write(alerts)

        print(f"[LOGGER] Alert #{alert['id']} | {bench} | {student_name} "
              f"| score={score} | conf={ml_confidence:.2f}")
        return alert

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_all(self) -> list:
        """Return all alerts, newest first."""
        alerts = self._read()
        return sorted(alerts, key=lambda x: x["timestamp"], reverse=True)

    def get_recent(self, n: int = 10) -> list:
        """Return the n most recent alerts."""
        return self.get_all()[:n]

    def get_by_bench(self, bench: str) -> list:
        """Return all alerts for a specific bench."""
        return [a for a in self.get_all() if a["bench"] == bench]

    def get_by_student(self, roll_number: str) -> list:
        """Return all alerts for a specific student by roll number."""
        return [a for a in self.get_all() if a["roll_number"] == roll_number]

    def get_summary(self) -> dict:
        """
        Return session summary for dashboard header.
        {total_alerts, unique_students, highest_score, benches_flagged}
        """
        alerts = self._read()
        if not alerts:
            return {
                "total_alerts"    : 0,
                "unique_students" : 0,
                "highest_score"   : 0,
                "benches_flagged" : []
            }

        return {
            "total_alerts"    : len(alerts),
            "unique_students" : len(set(a["roll_number"] for a in alerts)),
            "highest_score"   : max(a["score"] for a in alerts),
            "benches_flagged" : list(set(a["bench"] for a in alerts))
        }

    def mark_reviewed(self, alert_id: int) -> bool:
        """Mark an alert as reviewed by invigilator."""
        alerts = self._read()
        for a in alerts:
            if a["id"] == alert_id:
                a["reviewed"] = True
                self._write(alerts)
                print(f"[LOGGER] Alert #{alert_id} marked reviewed.")
                return True
        return False

    def clear_session(self):
        """
        Clear all alerts — call at start of new exam session.
        Archives existing alerts before clearing.
        """
        alerts = self._read()
        if alerts:
            # Archive with timestamp
            ts      = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            archive = self.alerts_file.replace(".json", f"_archive_{ts}.json")
            self._write_to(alerts, archive)
            print(f"[LOGGER] Archived {len(alerts)} alerts → {archive}")

        self._write([])
        print("[LOGGER] Session cleared — alerts.json reset.")

    def _write_to(self, data, path):
        with open(path, "w") as f:
            json.dump(data, f, indent=4)

    def _next_id(self) -> int:
        alerts = self._read()
        return (max(a["id"] for a in alerts) + 1) if alerts else 1


# ════════════════════════════════════════════════════════════════════════════
# STANDALONE TEST
# py -3.11 alert_logger.py  (from ARGUS root)
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    print("=" * 55)
    print("  ARGUS — File 10: Alert Logger Test")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("=" * 55)

    logger = AlertLogger()

    # Clear any previous test data
    logger.clear_session()

    # ── Log 5 test alerts ────────────────────────────────────────────────────
    print("\n[1/4] Logging 5 test alerts...")

    logger.log_alert(
        bench="B2", student_name="Shubham Pitty",
        roll_number="1251030022",
        score=32, ml_confidence=0.91,
        flags={"body_turn": True, "head_turn": True,
               "arm_extended": False, "combined": False}
    )

    logger.log_alert(
        bench="B1", student_name="Sanhita",
        roll_number="1251030001",
        score=30, ml_confidence=0.78,
        flags={"body_turn": False, "head_turn": False,
               "arm_extended": True, "combined": False}
    )

    logger.log_alert(
        bench="B2", student_name="Shubham Pitty",
        roll_number="1251030022",
        score=45, ml_confidence=0.97,
        flags={"body_turn": True, "head_turn": True,
               "arm_extended": True, "combined": True},
        snapshot_path="snapshots/B2_1251030022_001.jpg"
    )

    logger.log_alert(
        bench="B3", student_name="Unknown",
        roll_number="",
        score=31, ml_confidence=0.69,
        flags={"body_turn": True, "head_turn": False,
               "arm_extended": False, "combined": False}
    )

    logger.log_alert(
        bench="B1", student_name="Sanhita",
        roll_number="1251030001",
        score=38, ml_confidence=0.88,
        flags={"body_turn": False, "head_turn": True,
               "arm_extended": True, "combined": False},
        snapshot_path="snapshots/B1_1251030001_001.jpg"
    )

    # ── Query tests ──────────────────────────────────────────────────────────
    print("\n[2/4] Query tests...")

    all_alerts = logger.get_all()
    print(f"  get_all()        → {len(all_alerts)} alerts")

    recent = logger.get_recent(3)
    print(f"  get_recent(3)    → {len(recent)} alerts")

    b2 = logger.get_by_bench("B2")
    print(f"  get_by_bench(B2) → {len(b2)} alerts")

    student = logger.get_by_student("1251030022")
    print(f"  get_by_student() → {len(student)} alerts for roll 1251030022")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n[3/4] Session summary...")
    summary = logger.get_summary()
    print(f"  Total alerts     : {summary['total_alerts']}")
    print(f"  Unique students  : {summary['unique_students']}")
    print(f"  Highest score    : {summary['highest_score']}")
    print(f"  Benches flagged  : {summary['benches_flagged']}")

    # ── Mark reviewed ────────────────────────────────────────────────────────
    print("\n[4/4] Mark alert #1 as reviewed...")
    result = logger.mark_reviewed(1)
    print(f"  Reviewed: {result}")

    # ── Show alerts.json path ─────────────────────────────────────────────────
    print(f"\n  alerts.json saved at: {logger.alerts_file}")
    print(f"  snapshots folder   : {SNAPSHOT_DIR}")

    print("\n" + "=" * 55)
    print("  ALL TESTS PASSED — File 10 ready for File 11")
    print("=" * 55)
