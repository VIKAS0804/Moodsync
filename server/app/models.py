"""ORM models.

Shape follows the project brief: tracks -> mood_scores (1:1 cached score) and a
separate isrc -> Apple Music catalog mapping so the catalog lookup happens once
per track rather than once per request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# JSONB on Postgres, plain JSON on sqlite (tests).
JSONVariant = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """A Spotify-authenticated user and their (refreshable) token pair.

    NOTE: tokens are stored in plaintext here. Before this goes anywhere near
    real users they need envelope encryption (e.g. AWS KMS) at rest.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    spotify_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    product: Mapped[str | None] = mapped_column(String(32))  # "premium" | "free"

    access_token: Mapped[str | None] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Opaque bearer token the mobile app sends back to this API, so the Spotify
    # access token never has to leave the server.
    session_token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Cached distribution of this user's own scored library, used to map a
    # slider position onto their taste rather than onto the absolute scale.
    # See app/selection.py for why this matters.
    score_mean: Mapped[float | None] = mapped_column(Float)
    score_stddev: Mapped[float | None] = mapped_column(Float)
    score_count: Mapped[int] = mapped_column(Integer, default=0)
    score_stats_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    library: Mapped[list[UserTrack]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def has_premium(self) -> bool:
        return self.product == "premium"


class Track(Base):
    """A track in some user's library, keyed by Spotify id, joined on ISRC."""

    __tablename__ = "tracks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    spotify_track_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    isrc: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(512))
    artist: Mapped[str] = mapped_column(String(512))
    album: Mapped[str | None] = mapped_column(String(512))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    artwork_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    mood: Mapped[MoodScore | None] = relationship(
        back_populates="track", uselist=False, cascade="all, delete-orphan"
    )
    owners: Mapped[list[UserTrack]] = relationship(
        back_populates="track", cascade="all, delete-orphan"
    )


class UserTrack(Base):
    """Library membership. A track row is shared; ownership is per user."""

    __tablename__ = "user_tracks"
    __table_args__ = (UniqueConstraint("user_id", "track_id", name="uq_user_track"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(32), default="saved")  # saved | playlist
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped[User] = relationship(back_populates="library")
    track: Mapped[Track] = relationship(back_populates="owners")


class MoodScore(Base):
    """Cached 1-100 mood score plus the feature vector it was derived from.

    Keeping the raw features means a scoring-model change can be replayed over
    the whole catalog without re-downloading and re-analysing any audio.
    """

    __tablename__ = "mood_scores"

    track_id: Mapped[str] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[int] = mapped_column(Integer, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    model_version: Mapped[str] = mapped_column(String(32), default="heuristic-v1")
    feature_vector: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    track: Mapped[Track] = relationship(back_populates="mood")


class MoodLabel(Base):
    """A listener's correction of a track's score.

    Two jobs at once. It overrides the model for that listener immediately, so
    a wrong score is fixable rather than something to live with. And it is a
    *label*: (feature_vector, human score) pairs are exactly what's needed to
    train a model, and they come from the population that matters -- this
    user's own library, whatever genres that contains.

    One row per user per track; a later correction replaces the earlier one.
    """

    __tablename__ = "mood_labels"
    __table_args__ = (UniqueConstraint("user_id", "track_id", name="uq_user_track_label"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), index=True)
    score: Mapped[int] = mapped_column(Integer)
    # What the model said when the human disagreed, so drift is measurable.
    model_score: Mapped[int | None] = mapped_column(Integer)
    model_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AppleCatalogMap(Base):
    """ISRC -> Apple Music catalog id + 30s preview URL. One lookup per track, ever."""

    __tablename__ = "apple_catalog_map"

    isrc: Mapped[str] = mapped_column(String(32), primary_key=True)
    apple_catalog_id: Mapped[str | None] = mapped_column(String(64))
    preview_url: Mapped[str | None] = mapped_column(Text)
    storefront: Mapped[str] = mapped_column(String(8), default="us")
    # "isrc" (exact) | "fuzzy" (title+artist fallback) | "none" (negative cache)
    match_method: Mapped[str] = mapped_column(String(16), default="isrc")
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AnalysisJob(Base):
    """Audit trail for background analysis so failures are debuggable, not silent."""

    __tablename__ = "analysis_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_mood_scores_score_track", MoodScore.score, MoodScore.track_id)
