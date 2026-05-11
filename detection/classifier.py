"""
ARGUS — File 8: detection/classifier.py
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

Loads classifier.pkl (trained by File 7) and provides real-time
suspicion probability 0.0–1.0 from the 7 features produced by
feature_extractor.py (File 2).

Usage by main.py (File 12):
    from classifier import ARGUSClassifier
    clf = ARGUSClassifier()
    prob = clf.predict(features_dict)   # returns float 0.0–1.0

No camera, no MediaPipe here — pure inference layer.
"""

import os
import sys
import joblib
import numpy as np

# ── Path to model (ml/ is one level up from detection/) ─────────────────────
_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))
_MODEL_PATH = os.path.join(_THIS_DIR, "..", "ml", "classifier.pkl")

# ── Feature order MUST match train_model.py exactly ─────────────────────────
FEATURE_ORDER = [
    "shoulder_angle",
    "head_offset_x",
    "head_offset_y",
    "left_wrist_dist",
    "right_wrist_dist",
    "wrist_velocity_avg",
    "zone_motion_score"
]


class ARGUSClassifier:
    """
    Real-time cheating suspicion classifier for ARGUS.

    Wraps the trained Random Forest model.
    Called once per frame by main.py after feature_extractor
    returns a features dict for each detected person.
    """

    def __init__(self, model_path: str = None):
        self.model_path  = model_path or _MODEL_PATH
        self.model       = None
        self.feature_cols = None
        self.version     = None
        self.is_loaded   = False
        self._load()

    # ── Load ─────────────────────────────────────────────────────────────────

    def _load(self):
        """Load classifier.pkl bundle from ml/ folder."""
        if not os.path.exists(self.model_path):
            print(f"[CLASSIFIER] WARNING: classifier.pkl not found at:")
            print(f"             {self.model_path}")
            print(f"[CLASSIFIER] Run ml/train_model.py first.")
            print(f"[CLASSIFIER] Classifier disabled — all probabilities = 0.0")
            self.is_loaded = False
            return

        try:
            bundle = joblib.load(self.model_path)

            # Support both bundle dict and raw model (backwards compat)
            if isinstance(bundle, dict):
                self.model        = bundle["model"]
                self.feature_cols = bundle.get("feature_cols", FEATURE_ORDER)
                self.version      = bundle.get("version", "unknown")
                trained_acc       = bundle.get("accuracy", None)
            else:
                self.model        = bundle
                self.feature_cols = FEATURE_ORDER
                self.version      = "legacy"
                trained_acc       = None

            self.is_loaded = True
            acc_str = f"{trained_acc*100:.2f}%" if trained_acc else "unknown"
            print(f"[CLASSIFIER] Loaded classifier.pkl  "
                  f"version={self.version}  train_accuracy={acc_str}")

        except Exception as e:
            print(f"[CLASSIFIER] ERROR loading model: {e}")
            self.is_loaded = False

    # ── Core inference ────────────────────────────────────────────────────────

    def predict(self, features: dict) -> float:
        """
        Given a features dict from feature_extractor.py,
        return cheating suspicion probability 0.0–1.0.

        Args:
            features (dict): Must contain all 7 FEATURE_ORDER keys.
                             Missing keys default to 0.0 (safe fallback).

        Returns:
            float: 0.0 = definitely normal, 1.0 = definitely cheating.
                   Returns 0.0 if model not loaded or features invalid.
        """
        if not self.is_loaded:
            return 0.0

        try:
            # Build feature vector in exact training order
            vector = []
            for col in self.feature_cols:
                val = features.get(col, 0.0)
                # Clamp to valid float — reject None / NaN
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    val = 0.0
                vector.append(float(val))

            X = np.array(vector).reshape(1, -1)
            prob = self.model.predict_proba(X)[0][1]   # P(cheating)
            return round(float(prob), 4)

        except Exception as e:
            print(f"[CLASSIFIER] predict() error: {e}")
            return 0.0

    def predict_batch(self, features_list: list) -> list:
        """
        Batch inference for multiple persons in one frame.

        Args:
            features_list: list of feature dicts (one per detected person)

        Returns:
            list of float probabilities, same order as input.
        """
        return [self.predict(f) for f in features_list]

    # ── Status ────────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        """Returns True if model is loaded and ready."""
        return self.is_loaded

    def status(self) -> dict:
        """Returns a status dict for dashboard / logging."""
        return {
            "loaded"   : self.is_loaded,
            "version"  : self.version,
            "features" : self.feature_cols,
            "model_path": self.model_path
        }


# ════════════════════════════════════════════════════════════════════════════
# STANDALONE TEST — run this file directly to verify classifier works
# py -3.11 classifier.py  (from inside detection/ folder)
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 55)
    print("  ARGUS — File 8: Classifier Test")
    print("  VIT Pune | CSAIML-E | Group 01")
    print("=" * 55)

    clf = ARGUSClassifier()

    if not clf.is_ready():
        print("\n[TEST] Classifier not loaded. Exiting.")
        sys.exit(1)

    print(f"\n[TEST] Classifier status: {clf.status()}\n")

    # ── Test cases — should match expected labels ────────────────────────────
    test_cases = [
        {
            "name": "Normal student — writing quietly",
            "features": {
                "shoulder_angle"    : 4.5,
                "head_offset_x"     : 0.04,
                "head_offset_y"     : 0.09,
                "left_wrist_dist"   : 0.18,
                "right_wrist_dist"  : 0.17,
                "wrist_velocity_avg": 2.8,
                "zone_motion_score" : 0.04
            },
            "expected": 0   # normal
        },
        {
            "name": "Slight shoulder turn — borderline",
            "features": {
                "shoulder_angle"    : 16.0,
                "head_offset_x"     : 0.12,
                "head_offset_y"     : 0.09,
                "left_wrist_dist"   : 0.20,
                "right_wrist_dist"  : 0.19,
                "wrist_velocity_avg": 4.5,
                "zone_motion_score" : 0.10
            },
            "expected": None   # borderline — could be either
        },
        {
            "name": "Clear body turn — cheating",
            "features": {
                "shoulder_angle"    : 30.0,
                "head_offset_x"     : 0.28,
                "head_offset_y"     : 0.10,
                "left_wrist_dist"   : 0.25,
                "right_wrist_dist"  : 0.24,
                "wrist_velocity_avg": 6.0,
                "zone_motion_score" : 0.30
            },
            "expected": 1   # cheating
        },
        {
            "name": "Arm extension — chit passing",
            "features": {
                "shoulder_angle"    : 8.0,
                "head_offset_x"     : 0.07,
                "head_offset_y"     : 0.10,
                "left_wrist_dist"   : 0.62,
                "right_wrist_dist"  : 0.58,
                "wrist_velocity_avg": 18.5,
                "zone_motion_score" : 0.28
            },
            "expected": 1   # cheating
        },
        {
            "name": "Combined — body + arm (worst case)",
            "features": {
                "shoulder_angle"    : 38.0,
                "head_offset_x"     : 0.35,
                "head_offset_y"     : 0.10,
                "left_wrist_dist"   : 0.70,
                "right_wrist_dist"  : 0.68,
                "wrist_velocity_avg": 22.0,
                "zone_motion_score" : 0.45
            },
            "expected": 1   # cheating
        },
        {
            "name": "Missing features (fallback test)",
            "features": {
                "shoulder_angle": 5.0
                # all other keys missing — should default to 0.0 safely
            },
            "expected": 0   # should be normal (missing = 0.0 = normal posture)
        },
    ]

    # ── Run tests ────────────────────────────────────────────────────────────
    print("─" * 55)
    print(f"  {'Test Case':<38} {'Prob':>6}  {'Result'}")
    print("─" * 55)

    all_pass = True
    for tc in test_cases:
        prob     = clf.predict(tc["features"])
        pred     = 1 if prob >= 0.65 else 0
        expected = tc["expected"]

        if expected is None:
            verdict = "BORDERLINE"
        elif pred == expected:
            verdict = "✓ PASS"
        else:
            verdict = "✗ FAIL"
            all_pass = False

        print(f"  {tc['name']:<38} {prob:>6.4f}  {verdict}")

    print("─" * 55)
    print(f"\n  Batch test (all 5 main cases at once):")
    batch_features = [tc["features"] for tc in test_cases[:5]]
    batch_probs    = clf.predict_batch(batch_features)
    print(f"  Probabilities: {batch_probs}")

    print("\n" + "=" * 55)
    if all_pass:
        print("  ALL TESTS PASSED — File 8 ready for File 12 (main.py)")
    else:
        print("  SOME TESTS FAILED — check classifier.pkl quality")
    print("=" * 55)
