"""The scoring model is the heart of the project, so pin its behaviour down."""

from __future__ import annotations

import pytest

from app.pipeline import scoring

CALM = {
    "tempo_bpm": 62.0,
    "rms_mean": 0.012,
    "onset_rate_hz": 0.6,
    "spectral_centroid_hz": 950.0,
    "percussive_ratio": 0.18,
    "tonal_valence": 0.4,
}

HYPER = {
    "tempo_bpm": 176.0,
    "rms_mean": 0.170,
    "onset_rate_hz": 6.8,
    "spectral_centroid_hz": 4300.0,
    "percussive_ratio": 0.68,
    "tonal_valence": 0.8,
}


def test_score_is_within_slider_range():
    for features in (CALM, HYPER, {}):
        score = scoring.score_features(features)
        assert scoring.MIN_SCORE <= score <= scoring.MAX_SCORE


def test_calm_scores_far_below_hyper():
    assert scoring.score_features(CALM) < 25
    assert scoring.score_features(HYPER) > 75


def test_score_rises_monotonically_with_tempo():
    scores = [
        scoring.score_features({**CALM, "tempo_bpm": bpm}) for bpm in (60, 90, 120, 150, 180)
    ]
    assert scores == sorted(scores)
    assert scores[-1] > scores[0]


def test_features_outside_anchor_range_are_clamped_not_extrapolated():
    # A 400 BPM reading (usually a tempo-octave error) must not score above a
    # clean 180 BPM: past the anchor, the ramp saturates instead of extrapolating.
    assert scoring.score_features({**HYPER, "tempo_bpm": 400.0}) == scoring.score_features(
        {**HYPER, "tempo_bpm": 180.0}
    )
    assert scoring.score_features({**HYPER, "rms_mean": 99.0}) <= scoring.MAX_SCORE
    assert scoring.score_features({**CALM, "tempo_bpm": -10.0}) >= scoring.MIN_SCORE


def test_missing_features_renormalise_instead_of_dragging_score_down():
    """A track missing one feature should score like its remaining features imply."""
    partial = {k: v for k, v in HYPER.items() if k != "spectral_centroid_hz"}
    assert scoring.score_features(partial) > 70


def test_empty_feature_vector_lands_mid_scale():
    assert 40 <= scoring.score_features({}) <= 60


def test_non_numeric_values_are_ignored():
    assert scoring.score_features({**CALM, "tempo_bpm": "not a number"}) < 30


@pytest.mark.parametrize(
    ("score", "label"),
    [(1, "Calm"), (20, "Calm"), (35, "Mellow"), (55, "Steady"), (75, "Energetic"), (100, "Hyper")],
)
def test_mood_labels_cover_the_whole_range(score, label):
    assert scoring.describe_mood(score) == label


def test_explain_contributions_sum_to_energy_index():
    result = scoring.explain(HYPER)
    total = sum(c["contribution"] for c in result["contributions"].values())
    assert result["energy_index"] == pytest.approx(total, abs=0.01)
    assert result["score"] == scoring.score_features(HYPER)


def test_isrc_match_is_more_confident_than_fuzzy():
    assert scoring.confidence_for("isrc") > scoring.confidence_for("fuzzy")
