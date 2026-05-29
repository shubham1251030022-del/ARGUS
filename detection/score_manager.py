# score_manager.py — ARGUS Member 1 Swayam Phadtare
# v5 — CAMERA-AWARE scoring
#
# KEY INSIGHT from real measurement:
#   Camera is positioned to the SIDE → shoulder_angle always ~90° for EVERYONE
#   Shoulder angle is USELESS as a feature in this setup → completely disabled
#
# Active features for detection:
#   - wrist_dist (horizontal) — LWrist_H: 0.04 normal, >0.5 suspicious
#   - head_offset_x          — normal ~0.15, turning head >0.40
#   - wrist_velocity         — normal ~5, sudden movement >18
#   - zone_motion            — background motion
#
# Normal sitting measured values:
#   LWrist_H ≈ 0.04  RWrist_H ≈ 0.27  (safe threshold: 0.50)
#   head_offset_x ≈ 0.15              (safe threshold: 0.40)

import time
import os

class ScoreManager:
    def __init__(self, config_file=None):
        self.scores         = {}
        self.last_event     = {}
        self.last_decay     = {}
        self.alert_fired    = {}
        self.event_log      = []
        self.threshold      = 100
        self.decay_rate     = 8.0   # FIX: was 5 — faster drop when sitting still
        self.decay_interval = 4     # FIX: was 6 — decay every 4s not 6s
        self.start_time     = time.time()
        self.warmup_seconds = 8

    def _init_bench(self, bench_id):
        if bench_id not in self.scores:
            self.scores[bench_id]      = 0.0
            self.last_event[bench_id]  = time.time()
            self.last_decay[bench_id]  = time.time()
            self.alert_fired[bench_id] = False

    def get_score(self, bench_id):
        return round(self.scores.get(bench_id, 0.0), 2)

    def get_all_scores(self):
        return {b: round(s, 2) for b, s in self.scores.items()}

    def get_status(self, bench_id):
        pct = self.get_score(bench_id) / self.threshold
        if   pct >= 1.0:  return 'ALERT'
        elif pct >= 0.60: return 'WATCH'
        elif pct >= 0.30: return 'RISING'
        else:             return 'CLEAR'

    def get_status_color(self, bench_id):
        return {
            'ALERT' : (0,   0,   255),
            'WATCH' : (0,   165, 255),
            'RISING': (0,   220, 255),
            'CLEAR' : (0,   255, 0  ),
        }[self.get_status(bench_id)]

    def check_alert(self, bench_id):
        return self.get_score(bench_id) >= self.threshold

    def get_top_benches(self, n=3):
        return sorted(self.scores.items(), key=lambda x: x[1], reverse=True)[:n]

    def get_event_log(self):
        return self.event_log

    def update(self, bench_id, features, ml_probability=0.0, motion_score=0.0):
        if time.time() - self.start_time < self.warmup_seconds:
            return 0.0, 'WARMUP'

        self._init_bench(bench_id)

        # shoulder_angle = features[0] — DISABLED (always 90° from side camera)
        head_offset_x    = features[1]
        left_wrist_dist  = features[3]   # horizontal only
        right_wrist_dist = features[4]   # horizontal only
        wrist_velocity   = features[5]

        # ── Thresholds based on REAL measured values ─────────────────────────
        # Normal:  LWrist_H≈0.04, RWrist_H≈0.27, head≈0.15
        # Measured safe margins added on top of normal values

        head_suspicious = head_offset_x    >= 0.40   # normal~0.15, threshold 0.40
        arm_extended    = (left_wrist_dist  >= 0.55 or
                           right_wrist_dist >= 0.55)  # normal max~0.27, threshold 0.55
        fast_wrist      = wrist_velocity   >= 18     # normal~5, threshold 18
        zone_motion     = motion_score     >= 0.15

        points   = 0.0
        behavior = 'NORMAL'
        reasons  = []

        # ── Scoring patterns (shoulder angle excluded entirely) ───────────────

        # Pattern 1: Head turn + arm reach → strongest signal without shoulder
        if head_suspicious and arm_extended:
            points   = 10
            behavior = 'HEAD_ARM_CHEAT'
            reasons  = [f"HeadTurn({head_offset_x:.2f}) + "
                        f"ArmReach(L:{left_wrist_dist:.2f} R:{right_wrist_dist:.2f})"]

        # Pattern 2: Fast hand + arm extended → passing chit
        elif fast_wrist and arm_extended:
            points   = 10
            behavior = 'FAST_REACH'
            reasons  = [f"FastWrist({wrist_velocity:.1f}) + ArmReach"]

        # Pattern 3: Head turn + fast wrist → looking + grabbing
        elif head_suspicious and fast_wrist:
            points   = 8
            behavior = 'HEAD_FAST'
            reasons  = [f"HeadTurn({head_offset_x:.2f}) + FastWrist({wrist_velocity:.1f})"]

        # Pattern 4: Zone motion + arm extended
        elif zone_motion and arm_extended:
            points   = 7
            behavior = 'MOTION_REACH'
            reasons  = [f"ZoneMotion({motion_score:.2f}) + ArmReach"]

        # Pattern 5: Zone motion + head turn
        elif zone_motion and head_suspicious:
            points   = 6
            behavior = 'MOTION_HEAD'
            reasons  = [f"ZoneMotion({motion_score:.2f}) + HeadTurn({head_offset_x:.2f})"]

        # Single features → 0 points always

        if points > 0:
            self.scores[bench_id] = min(
                self.scores[bench_id] + points,
                self.threshold
            )
            self.last_event[bench_id] = time.time()
            self.event_log.append({
                'timestamp'       : time.strftime('%H:%M:%S'),
                'bench_id'        : bench_id,
                'behavior'        : behavior,
                'reasons'         : ', '.join(reasons),
                'points_added'    : round(points, 2),
                'cumulative_score': self.get_score(bench_id),
                'ml_confidence'   : round(ml_probability, 3),
                'motion_score'    : round(motion_score, 3),
            })

        return round(points, 2), behavior

    def decay(self, bench_id):
        self._init_bench(bench_id)
        now = time.time()
        if now - self.last_decay[bench_id] >= self.decay_interval:
            self.scores[bench_id] = max(
                0.0, self.scores[bench_id] - self.decay_rate
            )
            self.last_decay[bench_id] = now

    def decay_all(self):
        for bench_id in list(self.scores.keys()):
            self.decay(bench_id)

    def reset_score(self, bench_id, reason="Manual reset"):
        old = self.get_score(bench_id)
        self._init_bench(bench_id)
        self.scores[bench_id]      = 0.0
        self.alert_fired[bench_id] = False
        print(f"[RESET] {bench_id}: {old} → 0.0 | {reason}")
