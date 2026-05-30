# score_manager.py — ARGUS v9 — Time-Based Continuous Scoring
# Member 1: Swayam Phadtare | VIT Pune CSAIML-E Group 01
#
# HOW IT WORKS:
#   Score builds at X pts/SECOND while behavior is active (not per frame)
#   Score decays at 5 pts/second when behavior stops
#   Alert fires when score >= 100 AND sustained for 2s (in main.py)
#
# EXAMPLE — head turn 3 seconds (innocent stretch):
#   Score builds: 3s × 20pts/s = 60 pts → YELLOW WARNING visible on dashboard
#   Student stops: 60pts decays at 5/s → clears in 12s of normal sitting
#   Score never hit 100 → NO ALERT ✓
#
# EXAMPLE — head turn 5 seconds (cheating):
#   Score builds: 5s × 20pts/s = 100 pts → hits threshold
#   Sustained 2 more seconds → ALERT fires at ~7s total ✓
#
# EXAMPLE — arm reach 6 seconds:
#   6s × 18pts/s = 108 pts → hits 100 at ~5.5s → alert at ~7.5s ✓
#
# MEASURED NORMAL VALUES:
#   head_offset_x ≈ 0.15  | threshold 0.45 (3× normal)
#   wrist_dist_H  ≈ 0.27  | threshold 0.60 (well above normal)
#   wrist_velocity ≈ 5-15 | threshold 22

import time

class ScoreManager:
    def __init__(self, config_file=None):
        self.scores      = {}
        self.last_event  = {}
        self.last_decay  = {}
        self.alert_fired = {}
        self.last_update = {}    # for time-delta calculation
        self.event_log   = []

        self.threshold      = 100
        # Fast decay: 5pts per second when student is clean
        # 60pt score from 3s stretch clears in ~12s of normal sitting
        self.decay_rate     = 5.0
        self.decay_interval = 1    # decay called every 1 second
        self.start_time     = time.time()
        self.warmup_seconds = 10

    def _init_bench(self, bench_id):
        if bench_id not in self.scores:
            self.scores[bench_id]      = 0.0
            self.last_event[bench_id]  = time.time()
            self.last_decay[bench_id]  = time.time()
            self.last_update[bench_id] = time.time()
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

        # shoulder_angle DISABLED — camera at side = always ~90°
        head_offset_x    = features[1]
        left_wrist_dist  = features[3]   # horizontal only
        right_wrist_dist = features[4]   # horizontal only
        wrist_velocity   = features[5]

        now = time.time()
        # Time delta since last update — FPS-independent scoring
        dt = min(now - self.last_update.get(bench_id, now), 0.5)
        self.last_update[bench_id] = now

        # ── Feature flags ──────────────────────────────────────────────────
        head_turn  = head_offset_x    >= 0.45
        arm_reach  = (left_wrist_dist  >= 0.60 or
                      right_wrist_dist >= 0.60)
        fast_wrist = wrist_velocity   >= 22

        points   = 0.0
        behavior = 'NORMAL'
        reasons  = []

        # ── TIME-BASED CONTINUOUS SCORING ──────────────────────────────────
        # Points = rate × dt (seconds elapsed since last frame)
        # This makes scoring FPS-independent

        if head_turn and arm_reach:
            # Both together — fastest path to alert (~3.3s to reach 100)
            # Example: 3.3s sustained → ALERT with 2s wait = alert at ~5.3s
            points   = 30.0 * dt
            behavior = 'HEAD_ARM_CHEAT'
            reasons  = [f"HEAD({head_offset_x:.2f}) + "
                        f"ARM(L:{left_wrist_dist:.2f} R:{right_wrist_dist:.2f})"]

        elif head_turn:
            # Head turn alone → 20pts/s → hits 100 at 5s → alert at 7s
            # Brief glance (2-3s) → 40-60pts → warning but NO alert
            points   = 20.0 * dt
            behavior = 'HEAD_GLANCE'
            reasons  = [f"HEAD({head_offset_x:.2f})"]

        elif arm_reach:
            # Arm reach alone → 18pts/s → hits 100 at 5.5s → alert at 7.5s
            # Quick stretch (3s) → 54pts → watch but NO alert
            points   = 18.0 * dt
            behavior = 'ARM_REACH'
            reasons  = [f"ARM(L:{left_wrist_dist:.2f} R:{right_wrist_dist:.2f})"]

        # ── INSTANTANEOUS EVENTS (fast wrist) ──────────────────────────────
        # These are sudden movements — no time-based, score per occurrence
        if fast_wrist:
            if arm_reach:
                # Sudden grab/pass while arm extended
                points  += 20.0
                behavior = 'FAST_REACH'
                reasons.append(f"SUDDEN_REACH VEL({wrist_velocity:.1f})")
            elif head_turn:
                # Quick grab while looking at neighbor
                points  += 15.0
                if behavior == 'NORMAL':
                    behavior = 'HEAD_FAST'
                reasons.append(f"VEL({wrist_velocity:.1f})")

        if points > 0:
            self.scores[bench_id] = min(
                self.scores[bench_id] + points,
                self.threshold
            )
            self.last_event[bench_id] = now

            # Log only significant events (not every tiny frame update)
            if points >= 1.0:
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
        """Decay score when called. Called every 1s from main.py."""
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
        print(f"[RESET] {bench_id}: {old:.1f} → 0.0 | {reason}")
