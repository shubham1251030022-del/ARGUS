"""
ARGUS — Merge Real Data + Retrain
Merges real_normal.csv with synthetic data and retrains classifier.pkl

Run AFTER:
    1. py -3.11 ml/synthetic_data.py
    2. py -3.11 ml/collect_real_normal.py

Then:
    py -3.11 ml/merge_and_train.py
"""

import os
import sys
import pandas as pd
import numpy as np

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT     = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _ROOT)

SYNTHETIC_CSV   = os.path.join(_THIS_DIR, "training_data.csv")
REAL_NORMAL_CSV = os.path.join(_THIS_DIR, "real_normal.csv")
MODEL_OUT       = os.path.join(_THIS_DIR, "classifier.pkl")

FEATURE_KEYS = [
    "shoulder_angle", "head_offset_x", "head_offset_y",
    "left_wrist_dist", "right_wrist_dist",
    "wrist_velocity_avg", "zone_motion_score"
]

def main():
    print("=" * 55)
    print("  ARGUS — Merge & Retrain")
    print("=" * 55)

    # ── Load synthetic data ────────────────────────────────────
    if not os.path.exists(SYNTHETIC_CSV):
        print("[ERROR] training_data.csv not found.")
        print("  Run: py -3.11 ml/synthetic_data.py first")
        sys.exit(1)

    df_syn = pd.read_csv(SYNTHETIC_CSV)
    print(f"\n[1] Synthetic data: {len(df_syn)} samples")
    print(f"    Labels: {df_syn['label'].value_counts().to_dict()}")

    # ── Load real normal data ──────────────────────────────────
    if not os.path.exists(REAL_NORMAL_CSV):
        print("[ERROR] real_normal.csv not found.")
        print("  Run: py -3.11 ml/collect_real_normal.py first")
        sys.exit(1)

    df_real = pd.read_csv(REAL_NORMAL_CSV)
    print(f"\n[2] Real normal data: {len(df_real)} samples")

    # ── Drop synthetic normal samples (replace with real) ──────
    # Keep only non-normal synthetic samples
    df_syn_nonormal = df_syn[df_syn['label'] != 0]
    print(f"\n[3] Dropping synthetic normal samples ({len(df_syn) - len(df_syn_nonormal)} removed)")
    print(f"    Keeping {len(df_syn_nonormal)} synthetic cheating/stretch samples")

    # ── Combine ────────────────────────────────────────────────
    df_combined = pd.concat([df_syn_nonormal, df_real], ignore_index=True)
    df_combined = df_combined.sample(frac=1, random_state=42).reset_index(drop=True)

    print(f"\n[4] Combined dataset: {len(df_combined)} samples")
    print(f"    Labels: {df_combined['label'].value_counts().to_dict()}")

    # ── Train ──────────────────────────────────────────────────
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_score
    import pickle

    X = df_combined[FEATURE_KEYS].values
    y = df_combined['label'].values

    print("\n[5] Training Random Forest (100 estimators)...")
    clf = RandomForestClassifier(
        n_estimators=100,
        max_depth=8,          # limit depth to prevent overfit
        min_samples_leaf=5,   # require 5 samples per leaf
        random_state=42,
        n_jobs=-1
    )
    clf.fit(X, y)

    # Cross-validation
    cv_scores = cross_val_score(clf, X, y, cv=5, scoring='accuracy')
    print(f"    CV accuracy: {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")

    if cv_scores.mean() > 0.98:
        print("    [WARN] CV accuracy very high — may still be overfit")
        print("    Consider collecting more diverse normal samples")
    else:
        print("    [OK] Accuracy looks realistic")

    # ── Save model ─────────────────────────────────────────────
    model_data = {
        "model"         : clf,
        "feature_keys"  : FEATURE_KEYS,
        "version"       : "2.0",
        "train_accuracy": round(cv_scores.mean(), 4),
        "n_samples"     : len(df_combined),
        "real_samples"  : len(df_real),
    }
    with open(MODEL_OUT, 'wb') as f:
        pickle.dump(model_data, f)

    print(f"\n[6] Model saved → {MODEL_OUT}")
    print(f"    Version: 2.0 | CV: {cv_scores.mean():.3f}")
    print("\n  Restart main.py to load new model.")
    print("=" * 55)

if __name__ == "__main__":
    main()
