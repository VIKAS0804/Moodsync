"""Picking a track for a slider position.

Nearest-score wins, but strictly-nearest would make the slider deterministic:
land on 72 twice and you get the same song twice, which reads as broken. So the
query takes the k nearest candidates inside a widening window and samples one,
weighted by closeness and by match confidence.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AppleCatalogMap, MoodScore, Track, UserTrack

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


def _base_query(user_id: str):
    return (
        select(Track, MoodScore, AppleCatalogMap.preview_url)
        .join(MoodScore, MoodScore.track_id == Track.id)
        .join(UserTrack, UserTrack.track_id == Track.id)
        .outerjoin(AppleCatalogMap, AppleCatalogMap.isrc == Track.isrc)
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
    for window in WINDOW_STEPS:
        stmt = _base_query(user_id).where(
            MoodScore.score >= target - window, MoodScore.score <= target + window
        )
        if exclude:
            stmt = stmt.where(Track.spotify_track_id.notin_(exclude))
        rows = db.execute(stmt).all()
        if rows:
            found = [
                Candidate(
                    track=track,
                    score=mood.score,
                    confidence=mood.confidence,
                    model_version=mood.model_version,
                    preview_url=preview_url,
                )
                for track, mood, preview_url in rows
            ]
            found.sort(key=lambda c: abs(c.score - target))
            return found[:CANDIDATE_POOL]

    # Nothing anywhere in range: fall back to the whole scored library.
    stmt = _base_query(user_id)
    if exclude:
        stmt = stmt.where(Track.spotify_track_id.notin_(exclude))
    rows = db.execute(stmt).all()
    found = [
        Candidate(track, mood.score, mood.confidence, mood.model_version, preview)
        for track, mood, preview in rows
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
