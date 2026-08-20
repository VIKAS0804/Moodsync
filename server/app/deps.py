"""Shared FastAPI dependencies."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.spotify import SpotifyClient, SpotifyError
from app.config import Settings, get_settings
from app.db import get_db
from app.models import User

log = logging.getLogger(__name__)


def settings_dep() -> Settings:
    return get_settings()


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the opaque session token issued by /auth/spotify/callback."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    user = db.execute(select(User).where(User.session_token == token)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    return user


async def spotify_access_token(user: User, db: Session, settings: Settings) -> str:
    """Return a valid Spotify access token, refreshing it if it's close to expiry."""
    now = datetime.now(UTC)
    expires_at = user.token_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if user.access_token and expires_at and expires_at - now > timedelta(seconds=60):
        return user.access_token

    if not user.refresh_token:
        raise HTTPException(status_code=401, detail="Spotify session expired; sign in again")

    async with SpotifyClient() as client:
        try:
            tokens = await client.refresh_token(
                refresh_token=user.refresh_token,
                client_id=settings.spotify_client_id,
                client_secret=settings.spotify_client_secret or None,
            )
        except SpotifyError as exc:
            raise HTTPException(status_code=401, detail=f"Spotify refresh failed: {exc}") from exc

    user.access_token = tokens.access_token
    user.refresh_token = tokens.refresh_token or user.refresh_token
    user.token_expires_at = now + timedelta(seconds=tokens.expires_in)
    db.commit()
    return user.access_token
