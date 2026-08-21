"""Picking a track for a slider position.

Two problems to solve.

**Determinism.** Strictly-nearest would make the slider predictable: land on 72
twice and you get the same song twice, which reads as broken. So the query takes
the k nearest candidates inside a widening window and samples one, weighted by
closeness and by match confidence.

**Absolute scores don't fit a personal library.** Mood scores are absolute, so
somebody whose library is all mellow indie might have everything between 20 and
45 -- the top half of their slider would return nothing new, and the bottom half
would be crowded. Their "most energetic track" is a 45, and the slider should be
able to reach it at 100.

So a slider position is read as a *percentile of your own library*: it's mapped
through the user's cached mean/sd into their absolute score space before the
search runs. Slider 50 means "middle of my library", 85 means "near the top of
mine". Absolute scores are still what's stored and compared -- only the target
moves. Pass `absolute=True` to skip the mapping.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AppleCatalogMap, MoodLabel, MoodScore, Track, User, UserTrack

# Below this, a mean/sd is noise and the absolute scale is the safer default.
MIN_TRACKS_FOR_RELATIVE = 8
# Slider points per standard deviation. Slider 50 is the library mean, so 25
# makes the full 1-100 travel span roughly +/-2 sd -- about 95% of a normal
# library. Smaller values (VibeScape uses the equivalent of 15) push the slider
# ends past 3 sd, where there is nothing to find and everything clamps to the
# single most extreme track.
RELATIVE_POINTS_PER_SD = 25.0

# Start tight, widen only if the library is thin around that mood.
WINDOW_STEPS = (5, 12, 25, 50, 100)
CANDIDATE_POOL = 8


@dataclass(slots=True)
class Candidate:
    track: Track
    score: int
    confidence: float
    model_version: str
    preview_url: str | None


def library_scores(db: Session, user_id: str) -> list[int]:
    """Scores as this listener sees them, corrections included."""
    stmt = (
        select(func.coalesce(MoodLabel.score, MoodScore.score))
        .select_from(MoodScore)
        .join(UserTrack, UserTrack.track_id == MoodScore.track_id)
        .outerjoin(
            MoodLabel,
            (MoodLabel.track_id == MoodScore.track_id) & (MoodLabel.user_id == user_id),
        )
        .where(UserTrack.user_id == user_id)
    )
    return list(db.execute(stmt).scalars().all())


def refresh_score_stats(db: Session, user: User) -> None:
    """Recompute and cache the user's score distribution.

    Computed in Python rather than SQL because `stddev_samp` isn't available on
    sqlite, and a library is hundreds of rows, not millions.
    """
    scores = library_scores(db, user.id)
    user.score_count = len(scores)
    user.score_stats_at = datetime.now(UTC)
    if len(scores) >= 2:
        user.score_mean = float(statistics.fmean(scores))
        user.score_stddev = float(statistics.stdev(scores))
    else:
        user.score_mean = float(scores[0]) if scores else None
        user.score_stddev = None
    db.commit()


def resolve_target(user: User, slider: int, absolute: bool = False) -> tuple[int, str]:
    """Map a slider position to an absolute score to search for.

    Returns (absolute_target, mode) where mode is "relative" or "absolute".
    """
    if absolute:
        return slider, "absolute"
    if (
        user.score_mean is None
        or user.score_stddev is None
        or user.score_count < MIN_TRACKS_FOR_RELATIVE
        or user.score_stddev < 1e-6
    ):
        # Not enough of a distribution to normalise against; don't invent one.
        return slider, "absolute"

    # Invert the z-score: slider 50 -> mean, and every RELATIVE_POINTS_PER_SD
    # points away from 50 is one standard deviation of this user's library.
    z = (slider - 50) / RELATIVE_POINTS_PER_SD
    target = user.score_mean + z * user.score_stddev
    return int(round(min(100.0, max(1.0, target)))), "relative"


def effective_score(user_id: str):
    """A listener's own correction beats the model, for that listener only.

    Selection has to search on the score the user actually believes, otherwise
    correcting a track changes what it *says* but not where the slider finds it.
    """
    return func.coalesce(MoodLabel.score, MoodScore.score)


def _base_query(user_id: str):
    return (
        select(Track, MoodScore, AppleCatalogMap.preview_url, MoodLabel.score)
        .join(MoodScore, MoodScore.track_id == Track.id)
        .join(UserTrack, UserTrack.track_id == Track.id)
        .outerjoin(AppleCatalogMap, AppleCatalogMap.isrc == Track.isrc)
        .outerjoin(
            MoodLabel,
            (MoodLabel.track_id == Track.id) & (MoodLabel.user_id == user_id),
        )
        .where(UserTrack.user_id == user_id)
    )


def pool_size(db: Session, user_id: str) -> int:
    stmt = (
        select(func.count(func.distinct(Track.id)))
        .select_from(Track)
        .join(MoodScore, MoodScore.track_id == Track.id)
        .join(UserTrack, UserTrack.track_id == Track.id)
        .where(UserTrack.user_id == user_id)
    )
    return int(db.execute(stmt).scalar_one() or 0)


def candidates_near(
    db: Session, user_id: str, target: int, exclude: set[str] | None = None
) -> list[Candidate]:
    """Widen the mood window until we find something, then return the k nearest."""
    exclude = exclude or set()
    effective = effective_score(user_id)
    for window in WINDOW_STEPS:
        stmt = _base_query(user_id).where(
            effective >= target - window, effective <= target + window
        )
        if exclude:
            stmt = stmt.where(Track.spotify_track_id.notin_(exclude))
        rows = db.execute(stmt).all()
        if rows:
            found = [
                Candidate(
                    track=track,
                    score=label if label is not None else mood.score,
                    confidence=mood.confidence,
                    # A human correction is not the model's opinion any more.
                    model_version="human" if label is not None else mood.model_version,
                    preview_url=preview_url,
                )
                for track, mood, preview_url, label in rows
            ]
            found.sort(key=lambda c: abs(c.score - target))
            return found[:CANDIDATE_POOL]

    # Nothing anywhere in range: fall back to the whole scored library.
    stmt = _base_query(user_id)
    if exclude:
        stmt = stmt.where(Track.spotify_track_id.notin_(exclude))
    rows = db.execute(stmt).all()
    found = [
        Candidate(
            track,
            label if label is not None else mood.score,
            mood.confidence,
            "human" if label is not None else mood.model_version,
            preview,
        )
        for track, mood, preview, label in rows
    ]
    found.sort(key=lambda c: abs(c.score - target))
    return found[:CANDIDATE_POOL]


def choose(
    candidates: list[Candidate], target: int, rng: random.Random | None = None
) -> Candidate | None:
    """Weighted sample: closer + higher-confidence tracks win more often."""
    if not candidates:
        return None
    rng = rng or random
    weights = []
    for c in candidates:
        distance = abs(c.score - target)
        # 1/(1+d) decays fast enough that a 20-point-away track is a rare pick.
        weights.append((1.0 / (1.0 + distance)) * max(0.1, c.confidence))
    return rng.choices(candidates, weights=weights, k=1)[0]
