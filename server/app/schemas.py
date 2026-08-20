"""Pydantic request/response models. These are the app's contract with the API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    environment: str
    database: str
    apple_music_configured: bool
    preview_source: str
    spotify_configured: bool
    preview_cache: str


# ----------------------------------------------------------------------- auth


class AuthCallbackRequest(BaseModel):
    code: str = Field(..., description="Authorization code from the Spotify redirect")
    code_verifier: str = Field(..., description="PKCE verifier generated on the device")
    redirect_uri: str | None = None


class AuthSessionResponse(BaseModel):
    session_token: str
    user_id: str
    display_name: str | None
    product: str | None
    has_premium: bool
    playback_mode: str  # "spotify_remote" | "preview_fallback"


class MeResponse(BaseModel):
    user_id: str
    display_name: str | None
    product: str | None
    has_premium: bool
    library_size: int
    scored_tracks: int
    last_synced_at: datetime | None


# ----------------------------------------------------------------------- sync


class SyncRequest(BaseModel):
    max_tracks: int = Field(200, ge=1, le=2000)
    playlist_ids: list[str] = Field(default_factory=list)
    analyze: bool = True


class SyncResponse(BaseModel):
    tracks_seen: int
    tracks_added: int
    tracks_with_isrc: int
    isrc_coverage: float
    queued_for_analysis: int
    already_scored: int


# ----------------------------------------------------------------------- mood


class TrackOut(BaseModel):
    id: str
    spotify_track_id: str
    spotify_uri: str
    title: str
    artist: str
    album: str | None = None
    artwork_url: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None


class MoodMatchResponse(BaseModel):
    requested_score: int
    mood_label: str
    track: TrackOut
    track_score: int
    distance: int
    confidence: float
    model_version: str
    preview_url: str | None = None
    playback_mode: str
    pool_size: int
    latency_ms: float
    # "relative" = the slider was read as a percentile of this user's own
    # library and mapped through their mean/sd; "absolute" = taken at face
    # value (small library, or ?absolute=true).
    slider_mode: str = "absolute"
    # The absolute score actually searched for, after any mapping.
    absolute_target: int = 0
    library_mean: float | None = None
    library_stddev: float | None = None


class AnalysisStatusResponse(BaseModel):
    total_tracks: int
    scored: int
    pending: int
    failed: int
    skipped_no_preview: int
    coverage: float
    score_histogram: dict[str, int]
