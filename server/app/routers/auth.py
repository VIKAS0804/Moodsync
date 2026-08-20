"""Spotify OAuth.

The mobile app is a public client, so it runs Authorization Code + PKCE and
never holds a client secret. It ships the code + verifier here; the server does
the token exchange, keeps the Spotify tokens, and hands back an opaque session
token of its own.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clients.spotify import SpotifyClient, SpotifyError
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user, settings_dep
from app.models import MoodScore, User, UserTrack
from app.schemas import AuthCallbackRequest, AuthSessionResponse, MeResponse

router = APIRouter(prefix="/auth", tags=["auth"])

# Everything needed to read the library and drive the App Remote SDK.
SCOPES = (
    "user-read-email user-read-private user-library-read "
    "playlist-read-private playlist-read-collaborative "
    "user-read-playback-state user-modify-playback-state streaming"
)


@router.get("/spotify/config")
def spotify_config(settings: Settings = Depends(settings_dep)) -> dict[str, str]:
    """Client id / redirect / scopes, so they live in one place instead of two."""
    if not settings.spotify_client_id:
        raise HTTPException(status_code=503, detail="SPOTIFY_CLIENT_ID is not configured")
    return {
        "client_id": settings.spotify_client_id,
        "redirect_uri": settings.spotify_redirect_uri,
        "scopes": SCOPES,
        "authorize_endpoint": "https://accounts.spotify.com/authorize",
    }


@router.post("/spotify/callback", response_model=AuthSessionResponse)
async def spotify_callback(
    payload: AuthCallbackRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> AuthSessionResponse:
    if not settings.spotify_client_id:
        raise HTTPException(status_code=503, detail="SPOTIFY_CLIENT_ID is not configured")

    async with SpotifyClient() as client:
        try:
            tokens = await client.exchange_code(
                code=payload.code,
                code_verifier=payload.code_verifier,
                client_id=settings.spotify_client_id,
                redirect_uri=payload.redirect_uri or settings.spotify_redirect_uri,
                client_secret=settings.spotify_client_secret or None,
            )
        except SpotifyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        client.access_token = tokens.access_token
        profile = await client.get_me()

    spotify_user_id = profile["id"]
    user = db.execute(
        select(User).where(User.spotify_user_id == spotify_user_id)
    ).scalar_one_or_none()
    if user is None:
        user = User(spotify_user_id=spotify_user_id)
        db.add(user)

    user.display_name = profile.get("display_name") or spotify_user_id
    user.product = profile.get("product")
    user.access_token = tokens.access_token
    user.refresh_token = tokens.refresh_token or user.refresh_token
    user.token_expires_at = datetime.now(UTC) + timedelta(seconds=tokens.expires_in)
    user.session_token = secrets.token_urlsafe(32)
    db.commit()
    db.refresh(user)

    return AuthSessionResponse(
        session_token=user.session_token,
        user_id=user.id,
        display_name=user.display_name,
        product=user.product,
        has_premium=user.has_premium,
        playback_mode="spotify_remote" if user.has_premium else "preview_fallback",
    )


@router.post("/logout", status_code=204)
def logout(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> None:
    user.session_token = None
    db.commit()


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MeResponse:
    library_size = db.execute(
        select(func.count()).select_from(UserTrack).where(UserTrack.user_id == user.id)
    ).scalar_one()
    scored = db.execute(
        select(func.count())
        .select_from(MoodScore)
        .join(UserTrack, UserTrack.track_id == MoodScore.track_id)
        .where(UserTrack.user_id == user.id)
    ).scalar_one()

    return MeResponse(
        user_id=user.id,
        display_name=user.display_name,
        product=user.product,
        has_premium=user.has_premium,
        library_size=int(library_size),
        scored_tracks=int(scored),
        last_synced_at=user.last_synced_at,
    )
