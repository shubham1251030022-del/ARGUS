"""
ARGUS — File 6: ml/synthetic_data.py  [REBUILT v2]
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

Rebuilt with REAL measured values at 2-3 meter camera distance.

Real measurements collected May 13, 2026:
  Normal  : wrist_L=0.22-0.66, velocity=0-0.74,  head_x=0.06-0.36
  Stretch : wrist_L=0.51-6.54, velocity=0-4.93,  head_x=0-2.03
  Cheat   : wrist_L=0.49-19.4, velocity=0-7.0,   head_x=0-1.85

Key insight: stretch and cheat overlap in single-frame values.
Difference is DURATION — handled by 8-second sustained timer in main.py.
ML model trained to catch PEAK cheating values (sustained extremes).

Run: py -3.11 synthetic_data.py  (from ml/ folder)
"""

import numpy as np
import pandas as pd
import os

np.random.seed(42)

TOTAL_ROWS = 3000
HALF       = TOTAL_ROWS // 2
OUTPUT_FILE = "training_data.csv"

# ── Real measured feature ranges ─────────────────────────────────────────────
# shoulder_angle is always 90.0 at 2-3m — not useful for classification
# Focus on wrist_dist, velocity, head_offset which show real differences

def sample(mean, std, low, high, size):
    return np.clip(np.random.normal(mean, std, size), low, high)


# ════════════════════════════════════════════════════════════════════════════
# CLASS 0 — NORMAL (1500 rows)
# Real measured: wrist_L=0.22-0.66, velocity=0-0.74, head_x=0.06-0.36
# ════════════════════════════════════════════════════════════════════════════

def generate_normal(n):
    return pd.DataFrame({
        "shoulder_angle"    : np.full(n, 90.0),           # always 90 at distance
        "head_offset_x"     : sample(0.15, 0.08, 0.0,  0.36, n),
        "head_offset_y"     : sample(0.40, 0.15, 0.1,  0.75, n),
        "left_wrist_dist"   : sample(0.42, 0.12, 0.2,  0.70, n),
        "right_wrist_dist"  : sample(0.32, 0.10, 0.1,  0.55, n),
        "wrist_velocity_avg": sample(0.20, 0.18, 0.0,  0.74, n),
        "zone_motion_score" : sample(0.03, 0.03, 0.0,  0.10, n),
        "label"             : np.zeros(n, dtype=int)
    })


# ════════════════════════════════════════════════════════════════════════════
# CLASS 1 — CHEATING (1500 rows)
# Sustained extreme values — what the 8-second timer will catch
# 4 sub-types based on real cheating patterns
# ════════════════════════════════════════════════════════════════════════════

def generate_cheating(n):
    per_type = n // 4
    remainder = n - per_type * 4
    frames = []

    # ── Type A: Sustained head turn (looking at neighbor's paper) ────────────
    # head_x spikes to 0.5-2.0 sustained
    na = per_type
    frames.append(pd.DataFrame({
        "shoulder_angle"    : np.full(na, 90.0),
        "head_offset_x"     : sample(1.0,  0.4,  0.5,  2.0,  na),
        "head_offset_y"     : sample(0.5,  0.15, 0.2,  0.8,  na),
        "left_wrist_dist"   : sample(0.5,  0.15, 0.3,  0.85, na),
        "right_wrist_dist"  : sample(0.4,  0.12, 0.2,  0.70, na),
        "wrist_velocity_avg": sample(0.3,  0.20, 0.0,  0.8,  na),
        "zone_motion_score" : sample(0.15, 0.08, 0.05, 0.35, na),
        "label"             : np.ones(na, dtype=int)
    }))

    # ── Type B: Arm extension — passing chit (sustained reach) ───────────────
    # wrist_L spikes to 3.0-15.0 sustained with high velocity
    nb = per_type
    frames.append(pd.DataFrame({
        "shoulder_angle"    : np.full(nb, 90.0),
        "head_offset_x"     : sample(0.3,  0.20, 0.0,  0.8,  nb),
        "head_offset_y"     : sample(0.5,  0.15, 0.2,  0.8,  nb),
        "left_wrist_dist"   : sample(7.0,  3.0,  3.0,  15.0, nb),
        "right_wrist_dist"  : sample(5.0,  2.5,  2.0,  12.0, nb),
        "wrist_velocity_avg": sample(4.0,  1.5,  2.0,  7.0,  nb),
        "zone_motion_score" : sample(0.25, 0.10, 0.1,  0.45, nb),
        "label"             : np.ones(nb, dtype=int)
    }))

    # ── Type C: Combined — head turn + arm reach ──────────────────────────────
    nc = per_type
    frames.append(pd.DataFrame({
        "shoulder_angle"    : np.full(nc, 90.0),
        "head_offset_x"     : sample(1.2,  0.4,  0.5,  2.0,  nc),
        "head_offset_y"     : sample(0.5,  0.15, 0.2,  0.8,  nc),
        "left_wrist_dist"   : sample(8.0,  4.0,  3.0,  19.0, nc),
        "right_wrist_dist"  : sample(7.0,  3.5,  2.0,  15.0, nc),
        "wrist_velocity_avg": sample(4.5,  1.5,  2.0,  7.0,  nc),
        "zone_motion_score" : sample(0.30, 0.10, 0.1,  0.50, nc),
        "label"             : np.ones(nc, dtype=int)
    }))

    # ── Type D: Sustained high velocity — constant fidgeting/signaling ────────
    nd = per_type + remainder
    frames.append(pd.DataFrame({
        "shoulder_angle"    : np.full(nd, 90.0),
        "head_offset_x"     : sample(0.8,  0.3,  0.3,  1.5,  nd),
        "head_offset_y"     : sample(0.5,  0.15, 0.2,  0.8,  nd),
        "left_wrist_dist"   : sample(4.0,  2.0,  1.5,  10.0, nd),
        "right_wrist_dist"  : sample(3.5,  1.5,  1.0,  8.0,  nd),
        "wrist_velocity_avg": sample(3.5,  1.5,  2.0,  7.0,  nd),
        "zone_motion_score" : sample(0.20, 0.10, 0.08, 0.40, nd),
        "label"             : np.ones(nd, dtype=int)
    }))

    return pd.concat(frames, ignore_index=True)


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 55)
    print("  ARGUS — File 6 v2: Synthetic Data Generator")
    print("  Real 2-3m camera measurements")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("=" * 55)

    print(f"\n[1/4] Generating {HALF} normal rows...")
    df_normal   = generate_normal(HALF)

    print(f"[2/4] Generating {HALF} cheating rows...")
    df_cheating = generate_cheating(HALF)

    print("[3/4] Merging and shuffling...")
    df = pd.concat([df_normal, df_cheating], ignore_index=True)
    df = df.sample(frac=1, random_state=42).reset_index(drop=True)

    feature_cols = [
        "shoulder_angle", "head_offset_x", "head_offset_y",
        "left_wrist_dist", "right_wrist_dist",
        "wrist_velocity_avg", "zone_motion_score"
    ]
    df[feature_cols] = df[feature_cols].round(4)

    print(f"[4/4] Saving to {OUTPUT_FILE}...")
    df.to_csv(OUTPUT_FILE, index=False)

    print("\n" + "─" * 55)
    print("  VERIFICATION REPORT")
    print("─" * 55)
    print(f"  Total rows       : {len(df)}")
    print(f"  Normal  (label=0): {(df['label']==0).sum()}")
    print(f"  Cheating(label=1): {(df['label']==1).sum()}")
    print("\n  Feature ranges:")
    for col in feature_cols:
        print(f"    {col:<22} "
              f"min={df[col].min():.3f} "
              f"max={df[col].max():.3f} "
              f"mean={df[col].mean():.3f}")
    print("\n" + "=" * 55)
    print("  SUCCESS — training_data.csv ready for train_model.py")
    print("=" * 55)


if __name__ == "__main__":
    main()
