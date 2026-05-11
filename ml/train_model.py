"""
ARGUS — File 7: ml/train_model.py
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

Trains a Random Forest classifier on training_data.csv (File 6 output).
Target: 85%+ accuracy on test set.
Output: ml/classifier.pkl  (loaded by File 8 - classifier.py)

Run: py -3.11 train_model.py  (from inside ml/ folder)
Requires: training_data.csv in same folder
"""

import numpy as np
import pandas as pd
import joblib
import os
import sys

from sklearn.ensemble           import RandomForestClassifier
from sklearn.model_selection    import train_test_split, cross_val_score, StratifiedKFold
from sklearn.metrics            import (accuracy_score, precision_score,
                                        recall_score, f1_score,
                                        confusion_matrix, classification_report)
from sklearn.preprocessing      import StandardScaler

# ── Paths ────────────────────────────────────────────────────────────────────
DATA_FILE       = "training_data.csv"
MODEL_FILE      = "classifier.pkl"
ACCURACY_TARGET = 0.85

# ── Feature columns (must match feature_extractor.py output order) ───────────
FEATURE_COLS = [
    "shoulder_angle",
    "head_offset_x",
    "head_offset_y",
    "left_wrist_dist",
    "right_wrist_dist",
    "wrist_velocity_avg",
    "zone_motion_score"
]
LABEL_COL = "label"


# ════════════════════════════════════════════════════════════════════════════
# 1. LOAD DATA
# ════════════════════════════════════════════════════════════════════════════

def load_data():
    if not os.path.exists(DATA_FILE):
        print(f"\n[ERROR] {DATA_FILE} not found.")
        print("  Run synthetic_data.py first to generate the dataset.")
        sys.exit(1)

    df = pd.read_csv(DATA_FILE)

    # Validate columns
    missing = [c for c in FEATURE_COLS + [LABEL_COL] if c not in df.columns]
    if missing:
        print(f"\n[ERROR] Missing columns in CSV: {missing}")
        sys.exit(1)

    print(f"  Loaded  : {len(df)} rows, {len(FEATURE_COLS)} features")
    print(f"  Label=0 : {(df[LABEL_COL] == 0).sum()} (normal)")
    print(f"  Label=1 : {(df[LABEL_COL] == 1).sum()} (cheating)")

    X = df[FEATURE_COLS].values
    y = df[LABEL_COL].values
    return X, y


# ════════════════════════════════════════════════════════════════════════════
# 2. TRAIN / EVALUATE
# ════════════════════════════════════════════════════════════════════════════

def train_and_evaluate(X, y):
    # ── 80/20 stratified split ───────────────────────────────────────────────
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )
    print(f"\n  Train set : {len(X_train)} rows")
    print(f"  Test set  : {len(X_test)}  rows")

    # ── Random Forest — tuned for ARGUS ─────────────────────────────────────
    # n_estimators=200  : more trees → more stable predictions
    # max_depth=12       : deep enough to learn patterns, avoids overfit
    # min_samples_leaf=2 : prevents single-sample leaves (noise reduction)
    # class_weight=balanced: handles any mild imbalance
    # n_jobs=-1          : use all CPU cores
    model = RandomForestClassifier(
        n_estimators    = 200,
        max_depth       = 12,
        min_samples_split = 4,
        min_samples_leaf  = 2,
        max_features    = "sqrt",
        class_weight    = "balanced",
        random_state    = 42,
        n_jobs          = -1
    )

    print("\n  Training Random Forest (200 trees)...")
    model.fit(X_train, y_train)

    # ── Test set predictions ─────────────────────────────────────────────────
    y_pred      = model.predict(X_test)
    y_prob      = model.predict_proba(X_test)[:, 1]   # cheating probability

    acc  = accuracy_score (y_test, y_pred)
    prec = precision_score(y_test, y_pred)
    rec  = recall_score   (y_test, y_pred)
    f1   = f1_score       (y_test, y_pred)
    cm   = confusion_matrix(y_test, y_pred)

    # ── 5-fold cross-validation on full dataset ──────────────────────────────
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(model, X, y, cv=cv, scoring="accuracy", n_jobs=-1)

    return model, {
        "accuracy"  : acc,
        "precision" : prec,
        "recall"    : rec,
        "f1"        : f1,
        "cm"        : cm,
        "cv_scores" : cv_scores,
        "y_test"    : y_test,
        "y_pred"    : y_pred,
        "X_test"    : X_test
    }


# ════════════════════════════════════════════════════════════════════════════
# 3. PRINT REPORT
# ════════════════════════════════════════════════════════════════════════════

def print_report(metrics, model):
    acc = metrics["accuracy"]
    cm  = metrics["cm"]
    cv  = metrics["cv_scores"]

    print("\n" + "=" * 55)
    print("  MODEL EVALUATION REPORT")
    print("=" * 55)

    # ── Core metrics ─────────────────────────────────────────────────────────
    status = "✓ PASSED" if acc >= ACCURACY_TARGET else "✗ BELOW TARGET"
    print(f"\n  Accuracy     : {acc*100:.2f}%   [{status}]")
    print(f"  Precision    : {metrics['precision']*100:.2f}%")
    print(f"  Recall       : {metrics['recall']*100:.2f}%")
    print(f"  F1 Score     : {metrics['f1']*100:.2f}%")

    # ── Cross-validation ─────────────────────────────────────────────────────
    print(f"\n  5-Fold CV Accuracy:")
    for i, s in enumerate(cv, 1):
        print(f"    Fold {i}: {s*100:.2f}%")
    print(f"    Mean : {cv.mean()*100:.2f}%  ±{cv.std()*100:.2f}%")

    # ── Confusion matrix ─────────────────────────────────────────────────────
    tn, fp, fn, tp = cm.ravel()
    print(f"\n  Confusion Matrix:")
    print(f"    {'':15} Predicted Normal  Predicted Cheat")
    print(f"    {'Actual Normal':<15} {tn:<18} {fp}")
    print(f"    {'Actual Cheat':<15} {fn:<18} {tp}")
    print(f"\n  True Negatives  (normal correctly ignored) : {tn}")
    print(f"  True Positives  (cheating correctly caught) : {tp}")
    print(f"  False Positives (innocent flagged)          : {fp}")
    print(f"  False Negatives (cheating missed)           : {fn}")

    # ── Feature importances ──────────────────────────────────────────────────
    importances = model.feature_importances_
    fi_pairs = sorted(zip(FEATURE_COLS, importances), key=lambda x: x[1], reverse=True)
    print(f"\n  Feature Importances (ranked):")
    for feat, imp in fi_pairs:
        bar = "█" * int(imp * 50)
        print(f"    {feat:<22} {imp:.4f}  {bar}")

    # ── Classification report ────────────────────────────────────────────────
    print(f"\n  Full Classification Report:")
    print(classification_report(
        metrics["y_test"], metrics["y_pred"],
        target_names=["Normal (0)", "Cheating (1)"]
    ))


# ════════════════════════════════════════════════════════════════════════════
# 4. SAVE MODEL
# ════════════════════════════════════════════════════════════════════════════

def save_model(model, accuracy):
    """
    Save model + metadata bundle.
    File 8 (classifier.py) loads this bundle to get both the model
    and the feature column order it was trained on.
    """
    bundle = {
        "model"        : model,
        "feature_cols" : FEATURE_COLS,
        "accuracy"     : accuracy,
        "version"      : "1.0",
        "project"      : "ARGUS"
    }
    joblib.dump(bundle, MODEL_FILE)
    size_kb = os.path.getsize(MODEL_FILE) / 1024
    print(f"\n  Model saved : {os.path.abspath(MODEL_FILE)}")
    print(f"  File size   : {size_kb:.1f} KB")


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 55)
    print("  ARGUS — File 7: Model Trainer")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("=" * 55)

    # 1. Load
    print("\n[1/4] Loading training_data.csv...")
    X, y = load_data()

    # 2. Train
    print("\n[2/4] Training and evaluating model...")
    model, metrics = train_and_evaluate(X, y)

    # 3. Report
    print("\n[3/4] Generating evaluation report...")
    print_report(metrics, model)

    # 4. Save
    acc = metrics["accuracy"]
    if acc >= ACCURACY_TARGET:
        print("\n[4/4] Saving classifier.pkl...")
        save_model(model, acc)
        print("\n" + "=" * 55)
        print("  SUCCESS — classifier.pkl ready for File 8")
        print("=" * 55)
    else:
        print(f"\n[4/4] SKIPPED — accuracy {acc*100:.2f}% below target {ACCURACY_TARGET*100:.0f}%")
        print("  Check training_data.csv quality and re-run.")
        sys.exit(1)


if __name__ == "__main__":
    main()
