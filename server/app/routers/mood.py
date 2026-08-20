"""GET /mood/{score} -- the endpoint the slider hits."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app import selection
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.pipeline import scoring
from app.schemas import MoodMatchResponse, TrackOut

router = APIRouter(prefix="/mood", tags=["mood"])


@router.get("/{score}", response_model=MoodMatchResponse)
def match_mood(
    score: int = Path(..., ge=1, le=100, description="Slider position, 1 = calm, 100 = hyper"),
    exclude: list[str] = Query(
        default_factory=list,
        description="Spotify track ids to skip -- pass the last few so the slider doesn't repeat",
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MoodMatchResponse:
    started = time.perf_counter()

    candidates = selection.candidates_near(db, user.id, score, exclude=set(exclude))
    chosen = selection.choose(candidates, score)
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
        distance=abs(chosen.score - score),
        confidence=chosen.confidence,
        model_version=chosen.model_version,
        # Always returned: the app needs it whenever App Remote isn't available.
        preview_url=chosen.preview_url,
        playback_mode="spotify_remote" if user.has_premium else "preview_fallback",
        pool_size=selection.pool_size(db, user.id),
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )
