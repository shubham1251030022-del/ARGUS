# score_manager.py — ARGUS Member 1 Swayam Phadtare
# v3 — Combined-only scoring: single features never trigger alert alone

import time, json, os

class ScoreManager:
    def __init__(self, config_file='config.json'):
        self.scores         = {}
        self.last_event     = {}
        self.last_decay     = {}
        self.alert_fired    = {}
        self.event_log      = []
        self.threshold      = 100
        self.decay_rate     = 5.0
        self.decay_interval = 6
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
        return {'ALERT':(0,0,255),'WATCH':(0,165,255),
                'RISING':(0,220,255),'CLEAR':(0,255,0)}[self.get_status(bench_id)]

    def check_alert(self, bench_id):
        return self.get_score(bench_id) >= self.threshold

    def get_top_benches(self, n=3):
        return sorted(self.scores.items(), key=lambda x:x[1], reverse=True)[:n]

    def get_event_log(self):
        return self.event_log

    def update(self, bench_id, features, ml_probability=0.0, motion_score=0.0):
        if time.time() - self.start_time < self.warmup_seconds:
            return 0.0, 'WARMUP'

        self._init_bench(bench_id)

        shoulder_angle   = features[0]
        head_offset_x    = features[1]
        left_wrist_dist  = features[3]
        right_wrist_dist = features[4]
        wrist_velocity   = features[5]

        points   = 0.0
        behavior = 'NORMAL'
        reasons  = []

        # Individual flags — raised thresholds for 2-3m distance
        shoulder_suspicious = shoulder_angle   >= 38    # clear body turn
        head_suspicious     = head_offset_x    >= 0.32  # clear head turn
        arm_extended        = (left_wrist_dist  >= 0.50 or
                               right_wrist_dist >= 0.50) # arm reaching out
        fast_wrist          = wrist_velocity   >= 18    # sudden hand move
        zone_motion         = motion_score     >= 0.12

        # ── ONLY combined patterns score points ──────────────────
        # Single feature alone = 0 points (prevents false alerts)

        # Pattern 1: Body turn + arm reaching → strongest cheating signal
        if shoulder_suspicious and arm_extended:
            points   = 12
            behavior = 'COMBINED_CHEAT'
            reasons  = [f"ShoulderTurn({shoulder_angle:.0f}) + ArmReach"]

        # Pattern 2: Head turn + body turn → looking at neighbor
        elif shoulder_suspicious and head_suspicious:
            points   = 10
            behavior = 'HEAD_BODY_TURN'
            reasons  = [f"HeadTurn + ShoulderTurn({shoulder_angle:.0f})"]

        # Pattern 3: Fast hand + arm extended → passing chit
        elif fast_wrist and arm_extended:
            points   = 10
            behavior = 'FAST_REACH'
            reasons  = [f"FastWrist({wrist_velocity:.0f}) + ArmReach"]

        # Pattern 4: All three together → highest confidence
        elif shoulder_suspicious and arm_extended and head_suspicious:
            points   = 15
            behavior = 'FULL_CHEAT'
            reasons  = ["All3: Shoulder+Arm+Head"]

        # Pattern 5: Motion + body turn → sustained suspicious movement
        elif zone_motion and shoulder_suspicious:
            points   = 8
            behavior = 'MOTION_TURN'
            reasons  = [f"ZoneMotion + ShoulderTurn({shoulder_angle:.0f})"]

        # Single features → 0 points (explicitly ignored)
        # Normal: writing, reading, leaning forward, scratching head

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
