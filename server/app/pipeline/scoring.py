"""Feature vector -> 1-100 mood score (calm ... hyper).

Deliberately a *formalised heuristic*, not a black box: each feature is squashed
to 0..1 against documented anchors, then combined with named weights. Two
reasons that matters here --

1. There is no labelled dataset yet, so a regression model would be fitting
   noise. Hand-tuned anchors at least encode real acoustic intuition.
2. The weights and anchors are data, not code, so swapping in a trained model
   later means replacing `score_features` while keeping the same feature vector
   and the same cached rows (see `MODEL_VERSION`).

Persisted feature vectors make a re-score a pure database pass -- no audio is
re-downloaded when the model changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MODEL_VERSION = "heuristic-v1"

MIN_SCORE = 1
MAX_SCORE = 100


@dataclass(frozen=True, slots=True)
class FeatureAnchor:
    """Maps a raw feature onto 0..1 via a linear ramp between `low` and `high`."""

    key: str
    low: float
    high: float
    weight: float
    invert: bool = False

    def normalise(self, value: float) -> float:
        if self.high == self.low:
            return 0.5
        t = (value - self.low) / (self.high - self.low)
        t = min(1.0, max(0.0, t))
        return 1.0 - t if self.invert else t


# Anchors chosen from typical values over popular-music preview clips:
# 60 BPM is a ballad, 180 BPM is drum & bass; centroid ~800 Hz is muddy/warm,
# ~4.5 kHz is bright and cymbal-heavy; onset rate ~0.5/s is sparse, ~7/s is dense.
ANCHORS: tuple[FeatureAnchor, ...] = (
    FeatureAnchor("tempo_bpm", 60.0, 180.0, weight=0.30),
    FeatureAnchor("rms_mean", 0.010, 0.180, weight=0.22),
    FeatureAnchor("onset_rate_hz", 0.5, 7.0, weight=0.18),
    FeatureAnchor("spectral_centroid_hz", 800.0, 4500.0, weight=0.15),
    FeatureAnchor("percussive_ratio", 0.15, 0.70, weight=0.10),
    FeatureAnchor("tonal_valence", 0.0, 1.0, weight=0.05),
)

_TOTAL_WEIGHT = sum(a.weight for a in ANCHORS)


def energy_index(features: dict[str, Any]) -> float:
    """Weighted 0..1 arousal index. The score is just this, rescaled."""
    total = 0.0
    used_weight = 0.0
    for anchor in ANCHORS:
        raw = features.get(anchor.key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        total += anchor.normalise(value) * anchor.weight
        used_weight += anchor.weight
    if used_weight == 0:
        return 0.5
    # Renormalise so a missing feature doesn't silently drag the score down.
    return total / used_weight


def score_features(features: dict[str, Any]) -> int:
    """Map a feature vector onto the 1-100 slider scale."""
    index = energy_index(features)
    score = MIN_SCORE + index * (MAX_SCORE - MIN_SCORE)
    return int(round(min(float(MAX_SCORE), max(float(MIN_SCORE), score))))


def explain(features: dict[str, Any]) -> dict[str, Any]:
    """Per-feature contribution breakdown -- used by the analysis CLI and /debug."""
    contributions = {}
    for anchor in ANCHORS:
        raw = features.get(anchor.key)
        if raw is None:
            continue
        normalised = anchor.normalise(float(raw))
        contributions[anchor.key] = {
            "raw": float(raw),
            "normalised": round(normalised, 4),
            "weight": anchor.weight,
            "contribution": round(normalised * anchor.weight / _TOTAL_WEIGHT, 4),
        }
    return {
        "model_version": MODEL_VERSION,
        "score": score_features(features),
        "energy_index": round(energy_index(features), 4),
        "contributions": contributions,
    }


def confidence_for(match_method: str) -> float:
    """An exact ISRC match is the same recording; a fuzzy match may not be."""
    return {"isrc": 1.0, "fuzzy": 0.6}.get(match_method, 0.5)


def describe_mood(score: int) -> str:
    """Human-readable band, shown under the slider in the app."""
    if score <= 20:
        return "Calm"
    if score <= 40:
        return "Mellow"
    if score <= 60:
        return "Steady"
    if score <= 80:
        return "Energetic"
    return "Hyper"
