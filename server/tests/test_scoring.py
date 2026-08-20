"""The scoring model is the heart of the project, so pin its behaviour down."""

from __future__ import annotations

import pytest

from app.pipeline import scoring

# Values roughly at the p5 / p95 ends of the real calibration distribution.
CALM = {
    "tempo_bpm": 62.0,
    "rms_mean": 0.060,
    "onset_rate_hz": 0.4,
    "spectral_flatness": 0.001,
    "spectral_centroid_hz": 520.0,
    "percussive_ratio": 0.03,
    "zero_crossing_rate": 0.033,
    "tonal_valence": 0.4,
}

HYPER = {
    "tempo_bpm": 176.0,
    "rms_mean": 0.315,
    "onset_rate_hz": 4.5,
    "spectral_flatness": 0.063,
    "spectral_centroid_hz": 3080.0,
    "percussive_ratio": 0.46,
    "zero_crossing_rate": 0.146,
    "tonal_valence": 0.8,
}


def test_score_is_within_slider_range():
    for features in (CALM, HYPER, {}):
        score = scoring.score_features(features)
        assert scoring.MIN_SCORE <= score <= scoring.MAX_SCORE


def test_calm_scores_far_below_hyper():
    assert scoring.score_features(CALM) < 25
    assert scoring.score_features(HYPER) > 75


def test_tempo_does_not_influence_the_score():
    """Beat tracking on 30s previews is too unreliable to trust.

    Calibration put Clair de Lune at 172 BPM and Killing in the Name at 83, and
    mean detected tempo per energy tier was uncorrelated with energy. Tempo is
    still extracted and persisted, but it must not move the score.
    """
    scores = {scoring.score_features({**CALM, "tempo_bpm": bpm}) for bpm in (0, 60, 120, 400)}
    assert len(scores) == 1


def test_score_rises_with_energy_features():
    scores = [
        scoring.score_features({**CALM, "rms_mean": rms})
        for rms in (0.06, 0.12, 0.20, 0.28, 0.32)
    ]
    assert scores == sorted(scores)
    assert scores[-1] > scores[0]


def test_features_outside_anchor_range_are_clamped_not_extrapolated():
    # Past an anchor the ramp saturates rather than extrapolating, so a wild
    # reading cannot outrank a legitimately extreme one.
    assert scoring.score_features({**HYPER, "rms_mean": 99.0}) == scoring.score_features(
        {**HYPER, "rms_mean": 0.3185}
    )
    assert scoring.score_features({**HYPER, "rms_mean": 99.0}) <= scoring.MAX_SCORE
    assert scoring.score_features({**CALM, "rms_mean": -10.0}) >= scoring.MIN_SCORE


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
