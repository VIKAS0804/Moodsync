"""DSP feature extraction over 30-second Apple Music preview clips.

This replaces Spotify's retired `audio-features` endpoint. Everything here is
computed locally with librosa; nothing is fetched from a "give me the energy of
this song" API, because no such API is available to new third-party apps.

Every real preview is AAC in an MP4 container, which libsndfile cannot decode,
so ffmpeg is a hard requirement rather than an optimisation -- without it this
module cannot process a single real track. `imageio-ffmpeg` ships a binary as a
pip dependency, so `pip install` alone is enough on any platform; a system
ffmpeg on PATH is used first if present.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 22_050
FEATURE_VERSION = "features-v1"

# Minimum absolute spectral-flux strength for a peak to count as an onset.
# Ambient/drone material peaks around 1.4; ordinary music runs 7-25.
# See `_onset_rate` for why this is absolute rather than RMS-relative.
ONSET_STRENGTH_FLOOR = 2.0

# Krumhansl-Schmuckler key profiles, used as a cheap major/minor (valence) proxy.
MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)


class AudioDecodeError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def ffmpeg_path() -> str | None:
    """Locate an ffmpeg binary: system PATH first, then the pip-installed one."""
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - absence is reported by the caller
        return None


def load_audio(path: str | Path, sr: int = SAMPLE_RATE) -> tuple[np.ndarray, int]:
    """Decode an audio file to mono float32 at `sr`, trying several backends."""
    import librosa

    path = Path(path)
    if not path.is_file():
        raise AudioDecodeError(f"no such audio file: {path}")

    try:
        return librosa.load(str(path), sr=sr, mono=True)
    except Exception as exc:  # noqa: BLE001 - any decoder failure falls through to ffmpeg
        log.debug("librosa could not decode %s directly (%s); trying ffmpeg", path.name, exc)

    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise AudioDecodeError(
            f"could not decode {path.name}: no ffmpeg available. Install the "
            "`imageio-ffmpeg` package or put ffmpeg on PATH."
        )
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
        result = subprocess.run(
            [
                ffmpeg, "-y", "-loglevel", "error",
                "-i", str(path), "-ac", "1", "-ar", str(sr), tmp.name,
            ],
            capture_output=True,
        )
        if result.returncode != 0:
            raise AudioDecodeError(f"ffmpeg failed on {path.name}: {result.stderr.decode()[:200]}")
        return librosa.load(tmp.name, sr=sr, mono=True)


def _tonal_valence(chroma: np.ndarray) -> float:
    """0 = clearly minor, 1 = clearly major.

    Correlates the average chroma vector against both key profiles at all 12
    rotations and reports how much the best major fit beats the best minor fit.
    """
    mean_chroma = chroma.mean(axis=1)
    if mean_chroma.sum() <= 0:
        return 0.5

    def best_corr(profile: np.ndarray) -> float:
        scores = [
            float(np.corrcoef(np.roll(mean_chroma, -shift), profile)[0, 1]) for shift in range(12)
        ]
        scores = [s for s in scores if not np.isnan(s)]
        return max(scores) if scores else 0.0

    major, minor = best_corr(MAJOR_PROFILE), best_corr(MINOR_PROFILE)
    # Map the (major - minor) difference, realistically about +/-0.4, onto 0..1.
    return float(np.clip(0.5 + (major - minor) * 1.25, 0.0, 1.0))


def _onset_rate(y: np.ndarray, sr: int, rms_mean: float, duration: float) -> float:
    """Onsets per second, counting only audible transients.

    librosa's onset detector normalises internally, so it is scale-invariant: on
    an ambient track it reports a high onset rate from inaudible spectral
    wobble, which would push exactly the calmest tracks up the slider. So peaks
    are gated on onset *strength*.

    The gate is an absolute floor on flux, not a multiple of the track's RMS.
    An RMS-relative gate was tried first and is actively wrong on real music:
    loud compressed masters have a low crest factor, so their flux-to-RMS ratio
    is *lower* than a sparse acoustic recording's. Measured over real previews
    that ratio ran 12x (Skrillex) to 299x (Metallica), i.e. inversely related to
    energy -- a relative gate silently discards the transients of the loudest,
    most energetic tracks.

    Absolute flux separates cleanly instead: genuine ambient peaks around 1.4
    while ordinary music runs 7-25. Validated on 8 real clips; worth re-checking
    against a larger labelled set.
    """
    import librosa

    if duration <= 0:
        return 0.0

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    if onset_env.size == 0:
        return 0.0

    frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, units="frames")
    frames = [
        f for f in frames if f < onset_env.size and onset_env[f] >= ONSET_STRENGTH_FLOOR
    ]
    return float(len(frames)) / duration


def extract_features(path: str | Path) -> dict[str, Any]:
    """Return the raw feature vector for one clip.

    Raw units on purpose -- normalisation lives in `scoring` so the numbers stay
    interpretable and a future trained model can consume the same vector.
    """
    import librosa

    y, sr = load_audio(path)
    if y.size == 0:
        raise AudioDecodeError(f"decoded zero samples from {path}")

    # Trim leading/trailing silence so short fades don't drag the energy down.
    y_trimmed, _ = librosa.effects.trim(y, top_db=40)
    if y_trimmed.size > sr:  # keep at least a second
        y = y_trimmed

    # beat_track returns 0.0 when it finds no periodic pulse at all (drones,
    # ambient). That's left as-is on purpose: the tempo anchor clamps it to the
    # calm end of the scale, which is the correct reading for beatless audio.
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.atleast_1d(tempo)[0])

    rms = librosa.feature.rms(y=y)[0]
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.85)[0]
    flatness = librosa.feature.spectral_flatness(y=y)[0]
    zcr = librosa.feature.zero_crossing_rate(y)[0]
    contrast = librosa.feature.spectral_contrast(y=y, sr=sr)

    duration = float(len(y)) / sr
    onset_rate = _onset_rate(y, sr, float(np.mean(rms)), duration)

    # Harmonic/percussive split: percussive share tracks "driving" vs "floaty".
    harmonic, percussive = librosa.effects.hpss(y)
    h_energy = float(np.sum(harmonic**2))
    p_energy = float(np.sum(percussive**2))
    percussive_ratio = p_energy / (h_energy + p_energy) if (h_energy + p_energy) > 0 else 0.5

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)

    return {
        "tempo_bpm": round(tempo, 3),
        "rms_mean": round(float(np.mean(rms)), 6),
        "rms_std": round(float(np.std(rms)), 6),
        "dynamic_range": round(float(np.max(rms) - np.min(rms)), 6),
        "spectral_centroid_hz": round(float(np.mean(centroid)), 3),
        "spectral_rolloff_hz": round(float(np.mean(rolloff)), 3),
        "spectral_flatness": round(float(np.mean(flatness)), 6),
        "spectral_contrast": round(float(np.mean(contrast)), 4),
        "zero_crossing_rate": round(float(np.mean(zcr)), 6),
        "onset_rate_hz": round(onset_rate, 4),
        "percussive_ratio": round(percussive_ratio, 4),
        "tonal_valence": round(_tonal_valence(chroma), 4),
        "duration_s": round(duration, 2),
        "feature_version": FEATURE_VERSION,
    }
