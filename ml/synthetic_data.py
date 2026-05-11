"""
ARGUS — File 6: ml/synthetic_data.py
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

Generates 3000-row synthetic training dataset with 7 features.
No camera or recording needed — pure math-based simulation.
Output: ml/training_data.csv
Split: 50% normal (label=0), 50% cheating (label=1)

Run: py -3.11 synthetic_data.py (from inside ml/ folder)
"""

import numpy as np
import pandas as pd
import os

# ── Reproducibility ─────────────────────────────────────────────────────────
np.random.seed(42)

TOTAL_ROWS      = 3000
HALF            = TOTAL_ROWS // 2          # 1500 normal, 1500 cheating
OUTPUT_FILE     = "training_data.csv"

# ── Config thresholds (must match config.json exactly) ───────────────────────
SHOULDER_SLIGHT     = 15.0
SHOULDER_CLEAR      = 22.0
SHOULDER_SUSTAINED  = 28.0
HEAD_OFFSET_THRESH  = 0.22
WRIST_DIST_THRESH   = 0.38
WRIST_VEL_THRESH    = 12.0
MOTION_THRESH       = 0.20


# ════════════════════════════════════════════════════════════════════════════
# HELPER — clipped gaussian sampler
# ════════════════════════════════════════════════════════════════════════════

def sample(mean, std, low, high, size):
    """Draw samples from a gaussian, hard-clipped to [low, high]."""
    values = np.random.normal(mean, std, size)
    return np.clip(values, low, high)


# ════════════════════════════════════════════════════════════════════════════
# CLASS 0 — NORMAL BEHAVIOUR (1500 rows)
# A student sitting still, facing forward, arms on desk.
# ════════════════════════════════════════════════════════════════════════════

def generate_normal(n):
    """
    Normal sitting profile:
      - shoulder_angle  : small random variation, well below 15°
      - head_offset_x   : nose close to shoulder midpoint
      - head_offset_y   : nose slightly above shoulders (natural posture)
      - left/right wrist_dist : arms resting on desk — short distance to hip
      - wrist_velocity_avg    : near-zero movement
      - zone_motion_score     : background noise only
    """

    # shoulder_angle: 0–12 degrees (natural micro-shifts while writing)
    shoulder_angle = sample(mean=5.0, std=3.5, low=0.0, high=12.0, size=n)

    # head_offset_x: nose roughly centred over shoulders
    head_offset_x = sample(mean=0.05, std=0.04, low=0.0, high=0.18, size=n)

    # head_offset_y: slight downward tilt (looking at paper)
    head_offset_y = sample(mean=0.10, std=0.04, low=0.0, high=0.20, size=n)

    # wrist distances: arms on desk, close to body
    left_wrist_dist  = sample(mean=0.18, std=0.07, low=0.05, high=0.35, size=n)
    right_wrist_dist = sample(mean=0.18, std=0.07, low=0.05, high=0.35, size=n)

    # wrist velocity: writing is slow, controlled
    wrist_velocity_avg = sample(mean=3.0, std=2.5, low=0.0, high=10.0, size=n)

    # zone motion: only air-conditioning / curtain background noise
    zone_motion_score = sample(mean=0.05, std=0.04, low=0.0, high=0.18, size=n)

    label = np.zeros(n, dtype=int)

    return pd.DataFrame({
        "shoulder_angle":     shoulder_angle,
        "head_offset_x":      head_offset_x,
        "head_offset_y":      head_offset_y,
        "left_wrist_dist":    left_wrist_dist,
        "right_wrist_dist":   right_wrist_dist,
        "wrist_velocity_avg": wrist_velocity_avg,
        "zone_motion_score":  zone_motion_score,
        "label":              label
    })


# ════════════════════════════════════════════════════════════════════════════
# CLASS 1 — CHEATING BEHAVIOUR (1500 rows)
# Mixed subtypes to teach the model ALL cheating patterns, not just one.
# ════════════════════════════════════════════════════════════════════════════

def generate_cheating(n):
    """
    Four realistic cheating sub-types, evenly split:
      A — Body turn (shoulder rotation toward neighbour)
      B — Head turn only (trying to look sideways without moving body)
      C — Arm extension (passing/receiving chit — wrist far from body)
      D — Combined (body + arm — strongest signal, triggers +12 in scorer)
    """

    per_type = n // 4
    remainder = n - per_type * 4   # handle if n not divisible by 4

    frames = []

    # ── Sub-type A: Body Turn ────────────────────────────────────────────────
    # Clear to sustained shoulder rotation. Head may follow slightly.
    na = per_type
    frames.append(pd.DataFrame({
        "shoulder_angle":     sample(24.0, 6.0,  SHOULDER_CLEAR, 90.0,  na),
        "head_offset_x":      sample(0.18, 0.06, 0.10, 0.40,            na),
        "head_offset_y":      sample(0.10, 0.04, 0.0,  0.20,            na),
        "left_wrist_dist":    sample(0.20, 0.07, 0.05, 0.36,            na),
        "right_wrist_dist":   sample(0.20, 0.07, 0.05, 0.36,            na),
        "wrist_velocity_avg": sample(5.0,  3.0,  0.0,  11.0,            na),
        "zone_motion_score":  sample(0.22, 0.07, MOTION_THRESH, 0.60,   na),
        "label": np.ones(na, dtype=int)
    }))

    # ── Sub-type B: Head Turn Only ───────────────────────────────────────────
    # Shoulder stays relatively forward; head/nose swings sideways.
    nb = per_type
    frames.append(pd.DataFrame({
        "shoulder_angle":     sample(10.0, 4.0,  0.0,  SHOULDER_CLEAR,  nb),
        "head_offset_x":      sample(0.28, 0.07, HEAD_OFFSET_THRESH, 0.55, nb),
        "head_offset_y":      sample(0.10, 0.04, 0.0,  0.20,            nb),
        "left_wrist_dist":    sample(0.18, 0.06, 0.05, 0.36,            nb),
        "right_wrist_dist":   sample(0.18, 0.06, 0.05, 0.36,            nb),
        "wrist_velocity_avg": sample(4.0,  2.5,  0.0,  11.0,            nb),
        "zone_motion_score":  sample(0.15, 0.06, 0.0,  0.35,            nb),
        "label": np.ones(nb, dtype=int)
    }))

    # ── Sub-type C: Arm Extension (chit passing) ─────────────────────────────
    # Wrist shoots out far from hip. Fast movement.
    nc = per_type
    frames.append(pd.DataFrame({
        "shoulder_angle":     sample(8.0,  4.0,  0.0,  18.0,            nc),
        "head_offset_x":      sample(0.08, 0.04, 0.0,  0.20,            nc),
        "head_offset_y":      sample(0.10, 0.04, 0.0,  0.20,            nc),
        "left_wrist_dist":    sample(0.50, 0.12, WRIST_DIST_THRESH, 1.0, nc),
        "right_wrist_dist":   sample(0.50, 0.12, WRIST_DIST_THRESH, 1.0, nc),
        "wrist_velocity_avg": sample(16.0, 5.0,  WRIST_VEL_THRESH, 40.0, nc),
        "zone_motion_score":  sample(0.25, 0.08, MOTION_THRESH, 0.65,   nc),
        "label": np.ones(nc, dtype=int)
    }))

    # ── Sub-type D: Combined (Body Turn + Arm Extension) ─────────────────────
    # Strongest cheating signal — matches the +12 combined scoring rule.
    nd = per_type + remainder
    frames.append(pd.DataFrame({
        "shoulder_angle":     sample(30.0, 8.0,  SHOULDER_SUSTAINED, 90.0, nd),
        "head_offset_x":      sample(0.30, 0.08, HEAD_OFFSET_THRESH, 0.60, nd),
        "head_offset_y":      sample(0.10, 0.04, 0.0,  0.20,              nd),
        "left_wrist_dist":    sample(0.55, 0.12, WRIST_DIST_THRESH, 1.0,  nd),
        "right_wrist_dist":   sample(0.55, 0.12, WRIST_DIST_THRESH, 1.0,  nd),
        "wrist_velocity_avg": sample(18.0, 5.0,  WRIST_VEL_THRESH, 45.0, nd),
        "zone_motion_score":  sample(0.35, 0.10, MOTION_THRESH, 0.80,     nd),
        "label": np.ones(nd, dtype=int)
    }))

    return pd.concat(frames, ignore_index=True)


# ════════════════════════════════════════════════════════════════════════════
# MAIN — assemble, shuffle, save
# ════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 55)
    print("  ARGUS — File 6: Synthetic Data Generator")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("=" * 55)

    print(f"\n[1/4] Generating {HALF} normal rows  (label=0)...")
    df_normal   = generate_normal(HALF)

    print(f"[2/4] Generating {HALF} cheating rows (label=1)...")
    df_cheating = generate_cheating(HALF)

    print("[3/4] Merging and shuffling dataset...")
    df = pd.concat([df_normal, df_cheating], ignore_index=True)
    df = df.sample(frac=1, random_state=42).reset_index(drop=True)

    # ── Round to 4 decimal places (matches feature_extractor precision) ──
    feature_cols = [
        "shoulder_angle", "head_offset_x", "head_offset_y",
        "left_wrist_dist", "right_wrist_dist",
        "wrist_velocity_avg", "zone_motion_score"
    ]
    df[feature_cols] = df[feature_cols].round(4)

    # ── Save ────────────────────────────────────────────────────────────────
    print(f"[4/4] Saving to {OUTPUT_FILE}...")
    df.to_csv(OUTPUT_FILE, index=False)

    # ── Verification report ─────────────────────────────────────────────────
    print("\n" + "─" * 55)
    print("  DATASET VERIFICATION REPORT")
    print("─" * 55)
    print(f"  Total rows       : {len(df)}")
    print(f"  Normal  (label=0): {(df['label'] == 0).sum()}")
    print(f"  Cheating(label=1): {(df['label'] == 1).sum()}")
    print(f"  Columns          : {list(df.columns)}")
    print(f"  Output file      : {os.path.abspath(OUTPUT_FILE)}")
    print("\n  Feature ranges:")
    for col in feature_cols:
        print(f"    {col:<22} min={df[col].min():.4f}  max={df[col].max():.4f}  mean={df[col].mean():.4f}")

    print("\n  Label distribution:")
    print(df['label'].value_counts().to_string())

    print("\n  Sample (5 rows):")
    print(df.head(5).to_string(index=False))

    print("\n" + "=" * 55)
    print("  SUCCESS — training_data.csv ready for File 7")
    print("=" * 55)


if __name__ == "__main__":
    main()
