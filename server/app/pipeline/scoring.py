"""Feature vector -> 1-100 mood score (calm ... hyper).

A *formalised heuristic*, not a black box: each feature is squashed to 0..1
against documented anchors, then combined with named weights. The weights and
anchors are data, not code, so swapping in a trained model later means
replacing `score_features` while keeping the same feature vector and the same
cached rows (see `MODEL_VERSION`). Persisted feature vectors make a re-score a
pure database pass -- no audio is re-downloaded when the model changes.

Calibration (see `scripts/calibrate.py`)
----------------------------------------
Anchors and weights are fitted to 29 real preview clips hand-labelled into five
coarse energy tiers, scored by Spearman rank correlation between tier and
output. v1 (guessed anchors, tempo-dominated) reached rho +0.53 and could not
order the top three tiers at all -- their mean scores were 59.7 / 55.5 / 55.3.
v2 reaches **rho +0.82** with monotonic tier means (8.2 / 34.3 / 60.9 / 69.0 /
70.0). It wins on 399 of 400 random half-splits, and leave-one-out rho stays
within 0.802-0.858, so the gain is not one lucky track.

Honest limits: 29 tracks, tiers labelled by hand, and tiers 4 and 5 remain
nearly tied (69.0 vs 70.0) -- loudness-war mastering leaves a pop master and a
metal master with similar RMS. Treat these numbers as "clearly better than v1",
not as validated accuracy. A model trained on a public audio-features dataset
is the real fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MODEL_VERSION = "heuristic-v2"

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


# Anchors are the p5/p95 of each feature measured over real preview clips
# (`scripts/calibrate.py`), not guessed. The first version of this table was
# guessed and every anchor sat outside the real range -- `rms_mean` topped out
# at 0.18 when real masters reach 0.32, so every loud track pinned to 1.0 and
# the model could not tell pop from metal.
#
# Notable absence: tempo. Beat tracking on 30-second previews is unreliable
# enough to be actively harmful here -- it put Clair de Lune at 172 BPM and
# Killing in the Name at 83, and mean detected tempo per energy tier came out
# 116/105/131/119/118, i.e. uncorrelated with energy. It carried the largest
# weight (0.30). Dropping it moved rank correlation from +0.53 to +0.73.
# `tempo_bpm` is still extracted and persisted; it is simply not trusted here.
#
# Notable addition: spectral_flatness, a noisiness measure. Distorted guitars,
# cymbals and noise-heavy EDM are flat-spectrum; clean pop is not. It is what
# finally separates the aggressive tiers from the merely loud ones.
ANCHORS: tuple[FeatureAnchor, ...] = (
    FeatureAnchor("rms_mean", 0.0595, 0.3185, weight=0.22),
    FeatureAnchor("onset_rate_hz", 0.401, 4.557, weight=0.20),
    FeatureAnchor("spectral_flatness", 0.0, 0.064, weight=0.18),
    FeatureAnchor("spectral_centroid_hz", 509.3, 3100.0, weight=0.16),
    FeatureAnchor("percussive_ratio", 0.0278, 0.4680, weight=0.12),
    FeatureAnchor("zero_crossing_rate", 0.0321, 0.1476, weight=0.12),
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
