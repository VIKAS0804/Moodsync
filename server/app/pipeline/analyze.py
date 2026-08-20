"""End-to-end analysis: Spotify track -> ISRC -> Apple preview -> features -> score.

This is the "core risk" path from phase 1 of the brief. It never runs on a
request; `/sync` queues it as a background task and `/mood/{score}` only ever
reads cached rows.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.apple_music import AppleMatch, AppleMusicClient
from app.clients.itunes import ITunesClient
from app.clients.storage import PreviewCache
from app.config import Settings, get_settings
from app.models import AnalysisJob, AppleCatalogMap, MoodScore, Track
from app.pipeline import features as feat
from app.pipeline import scoring

log = logging.getLogger(__name__)


@dataclass(slots=True)
class AnalysisResult:
    track_id: str
    title: str
    artist: str
    status: str  # "scored" | "skipped" | "failed"
    score: int | None = None
    match_method: str | None = None
    error: str | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def resolve_catalog_mapping(
    db: Session,
    apple: AppleMusicClient | None,
    track: Track,
    settings: Settings,
    itunes: ITunesClient | None = None,
) -> AppleCatalogMap | None:
    """Find (and cache) the preview source for a track. One lookup per ISRC, ever.

    Tiers, best first:
      1. Apple Music catalog by ISRC -- exact, same recording, needs a token.
      2. Apple Music text search -- fuzzy, still needs a token.
      3. iTunes Search -- fuzzy, needs no credentials at all, so the pipeline
         works for anyone who cloned the repo without an Apple developer account.
    """
    if track.isrc:
        cached = db.get(AppleCatalogMap, track.isrc)
        if cached is not None:
            return cached if cached.preview_url else None

    match: AppleMatch | None = None
    if apple is not None and settings.use_apple_music:
        if track.isrc:
            match = await apple.find_by_isrc(track.isrc)
        if match is None and settings.unmatched_track_policy == "fuzzy":
            match = await apple.find_by_search(track.title, track.artist)

    if match is None and itunes is not None and settings.unmatched_track_policy == "fuzzy":
        found = await itunes.find(track.title, track.artist)
        if found is not None:
            match = AppleMatch(
                apple_catalog_id=found.apple_catalog_id,
                preview_url=found.preview_url,
                title=found.title,
                artist=found.artist,
                isrc=None,  # iTunes Search does not expose ISRC
                match_method="fuzzy",
            )

    key = track.isrc or f"spotify:{track.spotify_track_id}"
    row = db.get(AppleCatalogMap, key)
    if row is None:
        row = AppleCatalogMap(isrc=key)
        db.add(row)

    row.storefront = settings.apple_storefront
    row.matched_at = _utcnow()
    if match is None:
        # Negative cache: don't re-query Apple for a track it doesn't carry.
        row.apple_catalog_id = None
        row.preview_url = None
        row.match_method = "none"
        db.commit()
        return None

    row.apple_catalog_id = match.apple_catalog_id
    row.preview_url = match.preview_url
    row.match_method = match.match_method
    db.commit()
    return row if row.preview_url else None


async def analyze_track(
    db: Session,
    track: Track,
    apple: AppleMusicClient | None,
    cache: PreviewCache,
    settings: Settings | None = None,
    itunes: ITunesClient | None = None,
) -> AnalysisResult:
    settings = settings or get_settings()
    job = AnalysisJob(track_id=track.id, status="running")
    db.add(job)
    db.commit()

    def finish(status: str, error: str | None = None) -> None:
        job.status = status
        job.error = error
        job.finished_at = _utcnow()
        db.commit()

    try:
        mapping = await resolve_catalog_mapping(db, apple, track, settings, itunes)
        if mapping is None or not mapping.preview_url:
            finish("skipped", "no preview available from any catalog")
            return AnalysisResult(
                track.id, track.title, track.artist, "skipped", error="no preview available"
            )

        cache_key = track.isrc or track.spotify_track_id
        audio = cache.get(cache_key)
        if audio is None:
            downloader = apple or itunes
            if downloader is None:
                raise RuntimeError("no catalog client available to fetch the preview")
            audio = await downloader.download_preview(mapping.preview_url)
            cache.put(cache_key, audio)

        path = cache.local_path(cache_key, audio)
        # librosa is blocking and CPU-bound; keep it off the event loop.
        vector = await asyncio.to_thread(feat.extract_features, path)

        score = scoring.score_features(vector)
        confidence = scoring.confidence_for(mapping.match_method)

        row = db.get(MoodScore, track.id)
        if row is None:
            row = MoodScore(track_id=track.id)
            db.add(row)
        row.score = score
        row.confidence = confidence
        row.model_version = scoring.MODEL_VERSION
        row.feature_vector = vector
        row.computed_at = _utcnow()
        db.commit()

        finish("done")
        return AnalysisResult(
            track.id, track.title, track.artist, "scored", score, mapping.match_method
        )
    except Exception as exc:  # noqa: BLE001 - one bad track must not stop the batch
        db.rollback()
        log.exception("analysis failed for %s - %s", track.artist, track.title)
        finish("failed", str(exc)[:500])
        return AnalysisResult(
            track.id, track.title, track.artist, "failed", error=str(exc)[:300]
        )


def pending_tracks(db: Session, limit: int = 100, user_id: str | None = None) -> list[Track]:
    """Tracks in the library that have no cached score yet."""
    stmt = (
        select(Track)
        .outerjoin(MoodScore, MoodScore.track_id == Track.id)
        .where(MoodScore.track_id.is_(None))
        .limit(limit)
    )
    if user_id:
        from app.models import UserTrack

        stmt = stmt.join(UserTrack, UserTrack.track_id == Track.id).where(
            UserTrack.user_id == user_id
        )
    return list(db.execute(stmt).scalars().all())


async def analyze_many(
    track_ids: list[str],
    settings: Settings | None = None,
) -> list[AnalysisResult]:
    """Analyse a batch with bounded concurrency.

    Takes ids rather than ORM instances: each task opens its own Session, since
    a single Session must not be shared across concurrently running tasks.
    """
    settings = settings or get_settings()
    if not track_ids:
        return []

    from app.db import SessionLocal

    cache = PreviewCache(settings)
    semaphore = asyncio.Semaphore(max(1, settings.analysis_concurrency))

    async with AsyncExitStack() as stack:
        apple = (
            await stack.enter_async_context(AppleMusicClient(settings))
            if settings.use_apple_music
            else None
        )
        itunes = (
            await stack.enter_async_context(ITunesClient(storefront=settings.apple_storefront))
            if settings.use_itunes_fallback
            else None
        )
        if apple is None and itunes is None:
            log.warning("no preview source configured; nothing to analyse")
            return []

        async def run(track_id: str) -> AnalysisResult:
            async with semaphore:
                session = SessionLocal()
                try:
                    track = session.get(Track, track_id)
                    if track is None:
                        return AnalysisResult(track_id, "", "", "failed", error="track not found")
                    return await analyze_track(session, track, apple, cache, settings, itunes)
                finally:
                    session.close()

        return list(await asyncio.gather(*(run(tid) for tid in track_ids)))
