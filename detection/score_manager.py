# score_manager.py
# ARGUS — Member 1 — Swayam Phadtare
# Cumulative suspicion scoring — core innovation of ARGUS
# Updated based on real testing — optimized for 3-4 metre exam distance

import time
import json
import os

class ScoreManager:
    def __init__(self, config_file='config.json'):
        self.scores        = {}
        self.last_event    = {}
        self.last_decay    = {}
        self.alert_fired   = {}
        self.event_log     = []

        self.config        = self._load_config(config_file)
        self.threshold     = self.config.get('threshold', 30)
        self.decay_rate    = self.config.get('decay_rate', 1.0)
        self.decay_interval= self.config.get('decay_interval', 30)
        self.start_time = time.time()
        self.warmup_seconds = 3

    # ── Config ────────────────────────────────────────────────────
    def _load_config(self, path):
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
        default = {
            'mode'                    : 'STANDARD',
            'threshold'               : 30,
            'decay_rate'              : 1.0,
            'decay_interval'          : 30,
            'shoulder_clear_threshold': 10,
            'shoulder_slight'         : 15,
            'shoulder_clear'          : 22,
            'shoulder_sustained'      : 28,
            'head_offset_threshold'   : 0.22,
            'wrist_dist_threshold'    : 0.38,
            'wrist_velocity_threshold': 12,
            'motion_threshold'        : 0.08,
            'ml_confidence_threshold' : 0.65,
        }
        with open(path, 'w') as f:
            json.dump(default, f, indent=2)
        print(f"Created default config: STANDARD mode, threshold={default['threshold']}")
        return default

    # ── Score Helpers ─────────────────────────────────────────────
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
        elif pct >= 0.50: return 'WATCH'
        elif pct >= 0.25: return 'RISING'
        else:             return 'CLEAR'

    def get_status_color(self, bench_id):
        s = self.get_status(bench_id)
        return {
            'ALERT' : (0,   0,   255),
            'WATCH' : (0,   165, 255),
            'RISING': (0,   220, 255),
            'CLEAR' : (0,   255, 0  ),
        }[s]

    def check_alert(self, bench_id):
        return self.get_score(bench_id) >= self.threshold

    def get_top_benches(self, n=3):
        return sorted(
            self.scores.items(),
            key=lambda x: x[1],
            reverse=True
        )[:n]

    def get_event_log(self):
        return self.event_log

    # ── Core Update ───────────────────────────────────────────────
    def update(self, bench_id, features,
               ml_probability=0.0, motion_score=0.0):
        if time.time() - self.start_time < self.warmup_seconds:
            return 0.0, 'WARMUP'

        self._init_bench(bench_id)

        # Unpack features
        shoulder_angle   = features[0]
        head_offset_x    = features[1]
        left_wrist_dist  = features[3]
        right_wrist_dist = features[4]
        wrist_velocity   = features[5]

        c = self.config
        points   = 0.0
        behavior = 'NORMAL'
        reasons  = []

        # ── Rule 1: Shoulder Angle ──
        # Only flag if angle is clearly suspicious
        # Small angles (under 10 deg) are normal sitting posture
        if shoulder_angle >= c['shoulder_sustained']:
            points += 10
            behavior = 'SUSTAINED_TURN'
            reasons.append(f"SustainedTurn({shoulder_angle:.1f}deg)")

        elif shoulder_angle >= c['shoulder_clear']:
            points += 6
            behavior = 'CLEAR_TURN'
            reasons.append(f"ClearTurn({shoulder_angle:.1f}deg)")

        elif shoulder_angle >= c['shoulder_slight']:
            points += 2
            behavior = 'SLIGHT_TURN'
            reasons.append(f"SlightTurn({shoulder_angle:.1f}deg)")

        # ── Rule 2: Head offset ──
        # Only flag significant sideways head movement
        if head_offset_x > c['head_offset_threshold']:
            points += 3
            behavior = 'HEAD_TURN'
            reasons.append(f"HeadTurn({head_offset_x:.2f})")

        # ── Rule 3: Arm Extension ──
        # Wrist far from hip = arm extended toward neighbor
        arm_extended = (left_wrist_dist  > c['wrist_dist_threshold'] or
                        right_wrist_dist > c['wrist_dist_threshold'])
        if arm_extended:
            points += 5
            behavior = 'ARM_EXTENDED'
            reasons.append(f"ArmExt(L:{left_wrist_dist:.2f}"
                           f" R:{right_wrist_dist:.2f})")

        # ── Rule 4: Fast Wrist Movement ──
        # Sudden hand action — passing chit etc.
        if wrist_velocity > c['wrist_velocity_threshold']:
            points += 4
            behavior = 'FAST_WRIST'
            reasons.append(f"FastWrist({wrist_velocity:.1f})")

        # ── Rule 5: Zone Motion ──
        # Pixel-level motion in bench zone
        if motion_score > c['motion_threshold']:
            points += 2
            behavior = 'ZONE_MOTION'
            reasons.append(f"ZoneMotion({motion_score:.2f})")

        # ── Rule 6: Combined Cheating Pattern ──
        # Body turn + arm extension = strongest signal
        if (shoulder_angle >= c['shoulder_clear'] and arm_extended):
                # Reset individual points and use combined only
            points = 12
            behavior = 'COMBINED_CHEATING'
            reasons = ["COMBINED!"]

        # ── Rule 7: ML High Confidence ──
        if ml_probability > c['ml_confidence_threshold']:
            points *= 1.5
            reasons.append(f"MLx1.5({ml_probability:.2f})")

        # ── Add to cumulative score ──
        if points > 0:
            self.scores[bench_id] = min(
                self.scores[bench_id] + points,
                self.threshold
            )
            self.last_event[bench_id] = time.time()

            # Log event
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

    # ── Decay ─────────────────────────────────────────────────────
    def decay(self, bench_id):
        self._init_bench(bench_id)
        now     = time.time()
        elapsed = now - self.last_decay[bench_id]

        if elapsed >= self.decay_interval:
            self.scores[bench_id] = max(
                0.0,
                self.scores[bench_id] - self.decay_rate
            )
            self.last_decay[bench_id] = now

    def decay_all(self):
        for bench_id in list(self.scores.keys()):
            self.decay(bench_id)

    # ── Reset ─────────────────────────────────────────────────────
    def reset_score(self, bench_id, reason="Manual reset"):
        old = self.get_score(bench_id)
        self._init_bench(bench_id)
        self.scores[bench_id]      = 0.0
        self.alert_fired[bench_id] = False
        print(f"[RESET] {bench_id}: {old} → 0.0 | Reason: {reason}")


# ── Test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import cv2
    import sys
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from pose_detector    import PoseDetector
    from feature_extractor import FeatureExtractor
    from motion_zones     import MotionZones

    manager   = ScoreManager()
    detector  = PoseDetector()
    extractor = FeatureExtractor()
    motion    = MotionZones()

    zones = [
        {'bench_id':'B1','x1':0,   'y1':50,'x2':220,'y2':600},
        {'bench_id':'B2','x1':230, 'y1':50,'x2':550,'y2':600},
        {'bench_id':'B3','x1':560, 'y1':50,'x2':800,'y2':600},
    ]

    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    prev_time = time.time()

    print("=" * 60)
    print("ARGUS Score Manager — Live Test")
    print(f"Threshold : {manager.threshold} points")
    print(f"Decay     : -{manager.decay_rate} pt every "
          f"{manager.decay_interval}s")
    print("Sit inside B2 zone. Turn body sideways to trigger alert.")
    print("Press Q to quit.")
    print("=" * 60)

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        now           = time.time()
        frame_interval= now - prev_time
        prev_time     = now

        h, w = frame.shape[:2]

        # Motion detection
        motion_scores, frame = motion.detect(frame, zones)

        # Pose detection
        persons, frame = detector.detect(frame)

        # Decay all scores every loop
        manager.decay_all()

        # Process each detected person
        for person in persons:
            cx, cy = person['centroid']
            px = int(cx * w)
            py = int(cy * h)

            # Assign to bench
            assigned = None
            for zone in zones:
                if (zone['x1'] <= px <= zone['x2'] and
                        zone['y1'] <= py <= zone['y2']):
                    assigned = zone['bench_id']
                    break

            if assigned:
                # Get motion score for this bench
                m_score = motion_scores.get(assigned, 0.0)

                # Extract features
                features = extractor.extract(
                    person['landmarks'],
                    zone_motion_score=m_score,
                    frame_interval=max(frame_interval, 0.01)
                )

                # Update score
                points, behavior = manager.update(
                    assigned, features,
                    motion_score=m_score
                )

                # Draw assigned bench on person
                color = manager.get_status_color(assigned)
                cv2.circle(frame, (px, py), 14, color, -1)
                cv2.putText(frame,
                    f"{assigned}",
                    (px - 15, py - 20),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, color, 2)

                if points > 0:
                    print(f"\r[{time.strftime('%H:%M:%S')}] "
                          f"{assigned} +{points:.1f}pts "
                          f"→ Total:{manager.get_score(assigned):.1f} "
                          f"| {behavior}          ", end="")

        # Draw all zones with scores
        for zone in zones:
            bid    = zone['bench_id']
            score  = manager.get_score(bid)
            status = manager.get_status(bid)
            color  = manager.get_status_color(bid)

            # Zone rectangle
            cv2.rectangle(frame,
                (zone['x1'], zone['y1']),
                (zone['x2'], zone['y2']),
                color, 3)

            # Score bar background
            bar_x  = zone['x1'] + 5
            bar_y  = zone['y2'] - 30
            bar_w  = zone['x2'] - zone['x1'] - 10
            bar_h  = 15
            fill_w = int(bar_w * min(score / manager.threshold, 1.0))

            cv2.rectangle(frame,
                (bar_x, bar_y),
                (bar_x + bar_w, bar_y + bar_h),
                (50, 50, 50), -1)
            cv2.rectangle(frame,
                (bar_x, bar_y),
                (bar_x + fill_w, bar_y + bar_h),
                color, -1)

            # Score text
            cv2.putText(frame,
                f"{bid}: {score:.1f}/{manager.threshold}",
                (zone['x1'] + 5, zone['y1'] + 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)

            cv2.putText(frame,
                status,
                (zone['x1'] + 5, zone['y1'] + 55),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

        # Alert overlay
        alert_benches = [z['bench_id'] for z in zones
                         if manager.check_alert(z['bench_id'])]
        if alert_benches:
            overlay = frame.copy()
            cv2.rectangle(overlay, (0, 0), (w, 80),
                          (0, 0, 200), -1)
            cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, frame)
            cv2.putText(frame,
                f"ALERT! BENCH {', '.join(alert_benches)}",
                (w//2 - 200, 55),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.3, (255, 255, 255), 3)

        cv2.imshow('ARGUS Score Manager', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

    # Final event log
    print("\n\n" + "="*60)
    print("SESSION EVENT LOG")
    print("="*60)
    log = manager.get_event_log()
    if not log:
        print("No suspicious events detected.")
    else:
        for e in log[-15:]:
            print(f"{e['timestamp']} | {e['bench_id']:3s} | "
                  f"{e['behavior']:20s} | "
                  f"+{e['points_added']:5.1f}pts | "
                  f"Total:{e['cumulative_score']:5.1f} | "
                  f"{e['reasons']}")
    print("="*60)