"""Spotify OAuth.

Two ways in, both Authorization Code + PKCE, both ending in an opaque MoodSync
session token so the Spotify tokens never leave the server:

* `POST /auth/spotify/callback` -- the mobile app does PKCE on-device and posts
  the code here. Public client, so no client secret is involved.
* `GET /auth/spotify/login` -- the server does the whole dance and renders the
  session token in the browser. This exists because registering an Expo Go
  redirect URI (`exp://<lan-ip>:8081`) with Spotify is painful and the IP moves,
  so signing in from a laptop is the quickest route to real data.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clients.spotify import SpotifyClient, SpotifyError
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user, settings_dep, spotify_access_token
from app.models import MoodScore, User, UserTrack
from app.schemas import (
    AuthCallbackRequest,
    AuthSessionResponse,
    MeResponse,
    PairClaimRequest,
    PairClaimResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Everything needed to read the library and drive the App Remote SDK.
# user-top-read / user-read-recently-played matter more than they look: an
# account with no Liked Songs and no playlists has nothing else to sync, and
# listening history needs no curation.
SCOPES = (
    "user-read-email user-read-private user-library-read "
    "playlist-read-private playlist-read-collaborative "
    "user-top-read user-read-recently-played "
    "user-read-playback-state user-modify-playback-state streaming"
)


@router.get("/spotify/config")
def spotify_config(settings: Settings = Depends(settings_dep)) -> dict[str, str]:
    """Client id / redirect / scopes, so they live in one place instead of two."""
    if not settings.spotify_client_id:
        raise HTTPException(
            status_code=503,
            detail=(
                "SPOTIFY_CLIENT_ID is not configured. Create a free app at "
                "https://developer.spotify.com/dashboard and put its client id in "
                "server/.env -- a Spotify account alone isn't enough."
            ),
        )
    return {
        "client_id": settings.spotify_client_id,
        "redirect_uri": settings.spotify_redirect_uri,
        "scopes": SCOPES,
        "authorize_endpoint": "https://accounts.spotify.com/authorize",
    }


# --------------------------------------------------------------------------
# Browser login.
#
# The mobile app does PKCE on-device and POSTs the code to the endpoint below.
# That requires a redirect URI Spotify will accept, which in Expo Go means an
# `exp://<lan-ip>:8081` URL that changes with the network -- painful to register.
#
# This pair does the same dance server-side so you can sign in from a laptop
# browser and get a session token immediately. Register exactly one redirect URI
# for it: http://127.0.0.1:8000/auth/spotify/callback
# --------------------------------------------------------------------------

# state -> (code_verifier, created_at). In-process and single-node on purpose;
# this is a login handshake measured in seconds, not session storage.
_PENDING: dict[str, tuple[str, float]] = {}
_PENDING_TTL_SECONDS = 600


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(96)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _remember(state: str, verifier: str) -> None:
    now = time.time()
    for key, (_, created) in list(_PENDING.items()):
        if now - created > _PENDING_TTL_SECONDS:
            _PENDING.pop(key, None)
    _PENDING[state] = (verifier, now)


def _page(title: str, body: str, *, ok: bool = True) -> HTMLResponse:
    colour = "#34d399" if ok else "#fb7185"
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8">
<title>MoodSync — {title}</title></head>
<body style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
background:#0B1020;color:#e2e8f0;padding:48px;line-height:1.6">
<h2 style="color:{colour};margin-top:0">{title}</h2>{body}
</body></html>""",
        status_code=200 if ok else 400,
    )


@router.get("/spotify/login")
def spotify_login(settings: Settings = Depends(settings_dep)):
    """Kick off the browser login. Open this URL directly in a browser."""
    if not settings.spotify_client_id:
        return _page(
            "Spotify isn't configured",
            "<p>Set <code>SPOTIFY_CLIENT_ID</code> in <code>server/.env</code>.</p>"
            '<p>Create a free app at <a style="color:#38bdf8" '
            'href="https://developer.spotify.com/dashboard">developer.spotify.com/dashboard</a>'
            " — a Spotify account by itself is not enough.</p>",
            ok=False,
        )

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    _remember(state, verifier)

    url = SpotifyClient.authorize_url(
        client_id=settings.spotify_client_id,
        redirect_uri=settings.spotify_web_redirect_uri,
        code_challenge=challenge,
        scopes=SCOPES,
    )
    return RedirectResponse(f"{url}&state={state}")


@router.get("/spotify/callback")
async def spotify_callback_browser(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
):
    """Where Spotify sends the browser back. Exchanges the code for a session."""
    if error:
        return _page("Spotify declined", f"<p><code>{error}</code></p>", ok=False)
    if not code or not state:
        return _page("Missing code or state", "<p>Start again at /auth/spotify/login</p>", ok=False)

    entry = _PENDING.pop(state, None)
    if entry is None:
        return _page(
            "Unknown or expired login",
            "<p>That state value isn't one we issued (or it expired). "
            "Start again at <code>/auth/spotify/login</code>.</p>",
            ok=False,
        )

    session = await _establish_session(
        db=db,
        settings=settings,
        code=code,
        code_verifier=entry[0],
        redirect_uri=settings.spotify_web_redirect_uri,
    )

    pairing_code = _issue_pairing_code(session.session_token)

    return _page(
        f"Signed in as {session.display_name}",
        f"""
<p>Spotify plan: <b>{session.product or "unknown"}</b> &middot;
playback mode: <b>{session.playback_mode}</b></p>

<p>To use this on your phone, open MoodSync in Expo Go and enter this code
(valid 5 minutes, single use):</p>
<pre style="background:#1B2540;padding:18px;border-radius:8px;font-size:34px;
letter-spacing:8px;text-align:center;color:#34d399;margin:0 0 18px">{pairing_code}</pre>

<p>Session token (if you'd rather paste it):</p>
<pre style="background:#1B2540;padding:14px;border-radius:8px;overflow-x:auto"
>{session.session_token}</pre>
<p>Pull in your real library, then analyse it:</p>
<pre style="background:#1B2540;padding:14px;border-radius:8px;overflow-x:auto"
>curl -X POST localhost:8000/sync \\
  -H "Authorization: Bearer {session.session_token}" \\
  -H "Content-Type: application/json" \\
  -d '{{"max_tracks": 50, "analyze": true}}'

curl localhost:8000/sync/status -H "Authorization: Bearer {session.session_token}"
curl localhost:8000/mood/80     -H "Authorization: Bearer {session.session_token}"</pre>
<p style="color:#94a3b8">Analysis runs in the background — watch the server log,
and poll <code>/sync/status</code> until <code>pending</code> hits 0.</p>""",
    )


async def _establish_session(
    *,
    db: Session,
    settings: Settings,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> AuthSessionResponse:
    """Exchange an authorization code, upsert the user, issue a session token.

    Shared by the mobile POST callback and the browser GET callback so there is
    one place where Spotify tokens are persisted.
    """
    async with SpotifyClient() as client:
        try:
            tokens = await client.exchange_code(
                code=code,
                code_verifier=code_verifier,
                client_id=settings.spotify_client_id,
                redirect_uri=redirect_uri,
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


@router.post("/spotify/callback", response_model=AuthSessionResponse)
async def spotify_callback(
    payload: AuthCallbackRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> AuthSessionResponse:
    if not settings.spotify_client_id:
        raise HTTPException(status_code=503, detail="SPOTIFY_CLIENT_ID is not configured")

    return await _establish_session(
        db=db,
        settings=settings,
        code=payload.code,
        code_verifier=payload.code_verifier,
        redirect_uri=payload.redirect_uri or settings.spotify_redirect_uri,
    )


# --------------------------------------------------------------------------
# Device pairing.
#
# Signing in from inside the app on a phone is the awkward case. Spotify needs a
# redirect URI it recognises, and in Expo Go that's `exp://<lan-ip>:8081/--/...`
# -- it embeds the dev machine's IP, so it has to be registered and then
# re-registered every time the network changes. The server-side browser login
# can't help directly either: its redirect is a loopback literal, which on a
# phone's browser means the phone itself.
#
# So: sign in on a laptop, then pair the phone with a short code. Six digits
# typed on a phone beats a 43-character bearer token, and nothing needs
# registering.
# --------------------------------------------------------------------------

# code -> (session_token, created_at). Single-node and in-process, like the PKCE
# handshake store: this is a handoff measured in minutes, not a session.
_PAIRING: dict[str, tuple[str, float]] = {}
_PAIRING_TTL_SECONDS = 300


def _sweep_pairings(now: float) -> None:
    for code, (_, created) in list(_PAIRING.items()):
        if now - created > _PAIRING_TTL_SECONDS:
            _PAIRING.pop(code, None)


def _issue_pairing_code(session_token: str) -> str:
    now = time.time()
    _sweep_pairings(now)
    # Six digits: enough to make guessing pointless within a 5-minute window,
    # short enough to type. secrets, not random, since it authorises a session.
    for _ in range(20):
        code = f"{secrets.randbelow(1_000_000):06d}"
        if code not in _PAIRING:
            _PAIRING[code] = (session_token, now)
            return code
    raise HTTPException(status_code=503, detail="Could not allocate a pairing code")


@router.post("/pair/claim", response_model=PairClaimResponse)
def claim_pairing_code(payload: PairClaimRequest, db: Session = Depends(get_db)):
    """Exchange a pairing code for the session token it was issued for."""
    now = time.time()
    _sweep_pairings(now)

    # Single use: pop before validating anything else.
    entry = _PAIRING.pop(payload.code.strip(), None)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail="That code isn't valid (or has expired). Sign in again on your computer.",
        )

    session_token = entry[0]
    user = db.execute(
        select(User).where(User.session_token == session_token)
    ).scalar_one_or_none()
    if user is None:
        # The session was revoked between pairing and claiming.
        raise HTTPException(status_code=404, detail="That session is no longer valid")

    return PairClaimResponse(
        session_token=session_token,
        display_name=user.display_name,
        has_premium=user.has_premium,
    )


@router.get("/spotify/playback-token")
async def spotify_playback_token(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    """Hand the client a short-lived Spotify access token for the Web Playback SDK.

    This is a deliberate exception to "Spotify tokens never leave the server".
    The Web Playback SDK runs in the browser and turns the page into a Spotify
    playback device; it authenticates itself, so there is no way to keep the
    token server-side and still get full-track playback with seeking. Everything
    else -- library reads, sync, analysis -- continues to use the server copy.

    Refreshed on demand, so the client gets a token that is valid now rather than
    whatever was stored at login.
    """
    if not user.has_premium:
        raise HTTPException(
            status_code=403,
            detail=(
                "The Spotify Web Playback SDK requires Premium. "
                "Free accounts can still play 30-second previews."
            ),
        )

    access_token = await spotify_access_token(user, db, settings)
    expires_at = user.token_expires_at
    expires_in = 3600
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        expires_in = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))

    return {
        "access_token": access_token,
        "expires_in": expires_in,
        "product": user.product,
    }


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
