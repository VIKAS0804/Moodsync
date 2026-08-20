"""Library sync + background analysis kick-off."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clients.spotify import SpotifyClient, SpotifyError, SpotifyTrack
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user, settings_dep, spotify_access_token
from app.models import AnalysisJob, MoodScore, Track, User, UserTrack
from app.pipeline import analyze as pipeline
from app.schemas import AnalysisStatusResponse, SyncRequest, SyncResponse

log = logging.getLogger(__name__)
router = APIRouter(tags=["sync"])


def _upsert_track(db: Session, item: SpotifyTrack) -> Track:
    track = db.execute(
        select(Track).where(Track.spotify_track_id == item.spotify_track_id)
    ).scalar_one_or_none()
    if track is None:
        track = Track(spotify_track_id=item.spotify_track_id)
        db.add(track)
    track.title = item.title
    track.artist = item.artist
    track.album = item.album
    track.duration_ms = item.duration_ms
    track.artwork_url = item.artwork_url
    if item.isrc:
        track.isrc = item.isrc
    return track


async def _run_analysis(track_ids: list[str], settings: Settings) -> None:
    """Background entrypoint. Never raises into the request path."""
    try:
        results = await pipeline.analyze_many(track_ids, settings)
        scored = sum(1 for r in results if r.status == "scored")
        log.info("analysis batch complete: %d/%d scored", scored, len(results))
    except Exception:  # noqa: BLE001
        log.exception("background analysis batch failed")


@router.post("/sync", response_model=SyncResponse)
async def sync_library(
    payload: SyncRequest,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> SyncResponse:
    """Pull the user's saved tracks (and any named playlists), then queue scoring."""
    token = await spotify_access_token(user, db, settings)

    async with SpotifyClient(access_token=token) as client:
        try:
            items = await client.get_saved_tracks(max_tracks=payload.max_tracks)
            for playlist_id in payload.playlist_ids:
                items.extend(
                    await client.get_playlist_tracks(playlist_id, max_tracks=payload.max_tracks)
                )
        except SpotifyError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc

    seen: dict[str, SpotifyTrack] = {i.spotify_track_id: i for i in items}
    existing_ids = set(
        db.execute(
            select(UserTrack.track_id).where(UserTrack.user_id == user.id)
        ).scalars().all()
    )

    added = 0
    track_rows: list[Track] = []
    for item in seen.values():
        track = _upsert_track(db, item)
        db.flush()
        track_rows.append(track)
        if track.id not in existing_ids:
            db.add(UserTrack(user_id=user.id, track_id=track.id))
            added += 1

    user.last_synced_at = datetime.now(UTC)
    db.commit()

    with_isrc = sum(1 for t in track_rows if t.isrc)
    scored_ids = set(
        db.execute(
            select(MoodScore.track_id).where(
                MoodScore.track_id.in_([t.id for t in track_rows] or [""])
            )
        ).scalars().all()
    )
    unscored = [t.id for t in track_rows if t.id not in scored_ids]

    queued = 0
    if payload.analyze and unscored:
        if not settings.apple_music_configured:
            log.warning("skipping analysis: Apple Music credentials are not configured")
        else:
            queued = len(unscored)
            background.add_task(_run_analysis, unscored, settings)

    return SyncResponse(
        tracks_seen=len(seen),
        tracks_added=added,
        tracks_with_isrc=with_isrc,
        isrc_coverage=round(with_isrc / len(track_rows), 4) if track_rows else 0.0,
        queued_for_analysis=queued,
        already_scored=len(scored_ids),
    )


@router.get("/sync/status", response_model=AnalysisStatusResponse)
def analysis_status(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> AnalysisStatusResponse:
    """Coverage + score distribution, so the app can show real sync progress."""
    total = db.execute(
        select(func.count()).select_from(UserTrack).where(UserTrack.user_id == user.id)
    ).scalar_one()

    scored_rows = db.execute(
        select(MoodScore.score)
        .join(UserTrack, UserTrack.track_id == MoodScore.track_id)
        .where(UserTrack.user_id == user.id)
    ).scalars().all()

    job_counts = dict(
        db.execute(
            select(AnalysisJob.status, func.count())
            .join(UserTrack, UserTrack.track_id == AnalysisJob.track_id)
            .where(UserTrack.user_id == user.id)
            .group_by(AnalysisJob.status)
        ).all()
    )

    histogram = {f"{lo}-{lo + 19}": 0 for lo in range(1, 100, 20)}
    buckets = list(histogram.keys())
    for score in scored_rows:
        idx = min(len(buckets) - 1, max(0, (score - 1) // 20))
        histogram[buckets[idx]] += 1

    return AnalysisStatusResponse(
        total_tracks=int(total),
        scored=len(scored_rows),
        pending=max(0, int(total) - len(scored_rows)),
        failed=int(job_counts.get("failed", 0)),
        skipped_no_preview=int(job_counts.get("skipped", 0)),
        coverage=round(len(scored_rows) / int(total), 4) if total else 0.0,
        score_histogram=histogram,
    )
