"""DSP extraction, exercised on synthesised audio.

No network and no API credentials: the point is to prove the librosa path runs
and that acoustically calm vs. frantic signals land on opposite ends of the
slider. Real preview clips are covered by `scripts/phase1_pipeline.py`.
"""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from app.pipeline import features as feat
from app.pipeline import scoring

SR = 22_050
DURATION = 10.0


def _write(tmp_path, name: str, samples: np.ndarray):
    path = tmp_path / name
    sf.write(path, samples.astype(np.float32), SR)
    return path


def _calm_pad() -> np.ndarray:
    """Quiet, slow, low-frequency: a sustained A2/E3 drone with a slow swell."""
    t = np.linspace(0, DURATION, int(SR * DURATION), endpoint=False)
    tone = np.sin(2 * np.pi * 110 * t) + 0.6 * np.sin(2 * np.pi * 164.81 * t)
    swell = 0.5 + 0.5 * np.sin(2 * np.pi * 0.1 * t)  # 6 BPM amplitude drift
    return 0.05 * tone * swell


def _frantic_beat() -> np.ndarray:
    """Loud, bright, dense: 170 BPM noise-burst percussion plus a high tone."""
    n = int(SR * DURATION)
    t = np.linspace(0, DURATION, n, endpoint=False)
    rng = np.random.default_rng(0)

    signal = 0.15 * np.sin(2 * np.pi * 2200 * t)
    beat_period = 60.0 / 170.0
    for onset in np.arange(0, DURATION, beat_period / 2):  # eighth notes at 170 BPM
        start = int(onset * SR)
        burst = int(0.03 * SR)
        if start + burst >= n:
            break
        envelope = np.exp(-np.linspace(0, 8, burst))
        signal[start : start + burst] += 0.9 * rng.standard_normal(burst) * envelope
    return np.clip(signal, -1.0, 1.0)


@pytest.fixture(scope="module")
def calm_features(tmp_path_factory):
    path = _write(tmp_path_factory.mktemp("audio"), "calm.wav", _calm_pad())
    return feat.extract_features(path)


@pytest.fixture(scope="module")
def frantic_features(tmp_path_factory):
    path = _write(tmp_path_factory.mktemp("audio"), "frantic.wav", _frantic_beat())
    return feat.extract_features(path)


def test_extractor_returns_the_full_feature_vector(calm_features):
    expected = {
        "tempo_bpm",
        "rms_mean",
        "rms_std",
        "dynamic_range",
        "spectral_centroid_hz",
        "spectral_rolloff_hz",
        "spectral_flatness",
        "spectral_contrast",
        "zero_crossing_rate",
        "onset_rate_hz",
        "percussive_ratio",
        "tonal_valence",
        "duration_s",
    }
    assert expected.issubset(calm_features.keys())
    assert calm_features["feature_version"] == feat.FEATURE_VERSION
    assert all(
        isinstance(calm_features[k], (int, float)) for k in expected
    ), "features must be JSON-serialisable numbers, not numpy scalars"


def test_features_separate_calm_from_frantic_audio(calm_features, frantic_features):
    assert frantic_features["rms_mean"] > calm_features["rms_mean"]
    assert frantic_features["spectral_centroid_hz"] > calm_features["spectral_centroid_hz"]
    assert frantic_features["onset_rate_hz"] > calm_features["onset_rate_hz"]


def test_scores_land_on_opposite_ends_of_the_slider(calm_features, frantic_features):
    calm_score = scoring.score_features(calm_features)
    frantic_score = scoring.score_features(frantic_features)
    assert calm_score < frantic_score
    # The whole product breaks if these aren't meaningfully far apart.
    assert frantic_score - calm_score > 30


def test_missing_file_raises_a_clear_error(tmp_path):
    with pytest.raises(feat.AudioDecodeError, match="no such audio file"):
        feat.extract_features(tmp_path / "nope.wav")


def test_silence_does_not_crash_the_extractor(tmp_path):
    path = _write(tmp_path, "silence.wav", np.zeros(int(SR * 3)))
    vector = feat.extract_features(path)
    assert vector["rms_mean"] == pytest.approx(0.0, abs=1e-6)
    assert scoring.MIN_SCORE <= scoring.score_features(vector) <= scoring.MAX_SCORE
