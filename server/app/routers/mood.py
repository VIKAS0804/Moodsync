"""GET /mood/{score} -- the endpoint the slider hits."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import selection
from app.db import get_db
from app.deps import get_current_user
from app.models import MoodLabel, MoodScore, Track, User, UserTrack
from app.pipeline import scoring
from app.schemas import (
    MoodLabelRequest,
    MoodLabelResponse,
    MoodMatchResponse,
    TrackOut,
)

router = APIRouter(prefix="/mood", tags=["mood"])


@router.post("/label", response_model=MoodLabelResponse)
def label_track(
    payload: MoodLabelRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MoodLabelResponse:
    """Record that a listener disagrees with a track's score.

    Takes effect immediately for that listener -- `selection` searches on the
    corrected value, so a fixed score also changes where the slider finds the
    track, not just what it displays.

    It is also training data. (feature_vector, human score) pairs from a user's
    own library are the labels a model actually needs, and they come from the
    genres that user listens to rather than whatever a public dataset covers.
    """
    track = db.get(Track, payload.track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Unknown track")

    owned = db.execute(
        select(UserTrack).where(
            UserTrack.user_id == user.id, UserTrack.track_id == track.id
        )
    ).scalar_one_or_none()
    if owned is None:
        raise HTTPException(status_code=404, detail="That track isn't in your library")

    mood = db.get(MoodScore, track.id)
    label = db.execute(
        select(MoodLabel).where(
            MoodLabel.user_id == user.id, MoodLabel.track_id == track.id
        )
    ).scalar_one_or_none()

    if label is None:
        label = MoodLabel(user_id=user.id, track_id=track.id)
        db.add(label)
        # Only capture what the model thought the first time, so repeated
        # nudges don't overwrite the original disagreement.
        label.model_score = mood.score if mood else None
        label.model_version = mood.model_version if mood else None

    label.score = payload.score
    label.updated_at = datetime.now(UTC)
    db.commit()

    # The library distribution just changed, so the slider mapping must follow.
    selection.refresh_score_stats(db, user)

    return MoodLabelResponse(
        track_id=track.id,
        score=payload.score,
        model_score=label.model_score,
        mood_label=scoring.describe_mood(payload.score),
        total_labels=int(
            db.execute(
                select(func.count()).select_from(MoodLabel).where(MoodLabel.user_id == user.id)
            ).scalar_one()
        ),
    )


@router.delete("/label/{track_id}", status_code=204)
def unlabel_track(
    track_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Withdraw a correction and fall back to the model."""
    label = db.execute(
        select(MoodLabel).where(MoodLabel.user_id == user.id, MoodLabel.track_id == track_id)
    ).scalar_one_or_none()
    if label is not None:
        db.delete(label)
        db.commit()
        selection.refresh_score_stats(db, user)


@router.get("/{score}", response_model=MoodMatchResponse)
def match_mood(
    score: int = Path(..., ge=1, le=100, description="Slider position, 1 = calm, 100 = hyper"),
    exclude: list[str] = Query(
        default_factory=list,
        description="Spotify track ids to skip -- pass the last few so the slider doesn't repeat",
    ),
    absolute: bool = Query(
        False,
        description=(
            "Read the slider on the absolute 1-100 scale instead of as a "
            "percentile of the caller's own library"
        ),
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MoodMatchResponse:
    started = time.perf_counter()

    # A slider position means "this far up *my* library" unless told otherwise.
    target, slider_mode = selection.resolve_target(user, score, absolute=absolute)

    candidates = selection.candidates_near(db, user.id, target, exclude=set(exclude))
    chosen = selection.choose(candidates, target)
    if chosen is None:
        raise HTTPException(
            status_code=404,
            detail="No scored tracks yet. Run POST /sync and wait for analysis to finish.",
        )

    track = chosen.track
    return MoodMatchResponse(
        requested_score=score,
        mood_label=scoring.describe_mood(score),
        track=TrackOut(
            id=track.id,
            spotify_track_id=track.spotify_track_id,
            spotify_uri=f"spotify:track:{track.spotify_track_id}",
            title=track.title,
            artist=track.artist,
            album=track.album,
            artwork_url=track.artwork_url,
            duration_ms=track.duration_ms,
            isrc=track.isrc,
        ),
        track_score=chosen.score,
        distance=abs(chosen.score - target),
        slider_mode=slider_mode,
        absolute_target=target,
        library_mean=round(user.score_mean, 1) if user.score_mean is not None else None,
        library_stddev=round(user.score_stddev, 1) if user.score_stddev is not None else None,
        confidence=chosen.confidence,
        model_version=chosen.model_version,
        # Always returned: the app needs it whenever App Remote isn't available.
        preview_url=chosen.preview_url,
        playback_mode="spotify_remote" if user.has_premium else "preview_fallback",
        pool_size=selection.pool_size(db, user.id),
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )
