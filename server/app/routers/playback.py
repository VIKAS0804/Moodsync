"""Remote control of the user's Spotify players (Spotify Connect).

Why this exists
---------------
On a phone the only in-app route to a full track is the App Remote SDK, which
needs a custom dev client. Without it, "play a full track" meant deep-linking
into the Spotify app -- which plays the song but hands over the screen, and the
slider is the entire product. Doing that on every track is unusable.

But `user-modify-playback-state` lets the *server* drive any device the account
has registered, which is what Spotify Connect is. So MoodSync can keep the
slider on screen and treat the Spotify app as a speaker: change track, pause,
seek, read position. No native module, no dev build.

The catch is that Spotify only lists devices that are awake. A phone's Spotify
app registers while it's running and drops off later, so the first play may still
need a nudge to wake it -- after that, control is remote.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.clients.spotify import API_BASE
from app.config import Settings
from app.db import get_db
from app.deps import get_current_user, settings_dep, spotify_access_token
from app.models import User
from app.schemas import (
    PlaybackDevicesResponse,
    PlaybackSeekRequest,
    PlaybackStartRequest,
    PlaybackStateResponse,
    SpotifyDevice,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/playback", tags=["playback"])


async def _spotify(
    method: str, path: str, token: str, *, params: dict | None = None, json: Any = None
) -> tuple[int, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.request(
            method,
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            params=params,
            json=json,
        )
    if response.status_code == 204 or not response.content:
        return response.status_code, None
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


def _explain(status: int) -> str:
    """Spotify's playback errors are terse and each needs a different action."""
    return {
        403: (
            "Spotify refused playback. Full-track control needs Premium, and the "
            "account must not be playing somewhere it won't yield."
        ),
        404: (
            "No active Spotify device. Open Spotify on this phone once so it "
            "registers, then try again."
        ),
        429: "Spotify is rate-limiting playback control; wait a moment.",
    }.get(status, f"Spotify returned {status}.")


@router.get("/devices", response_model=PlaybackDevicesResponse)
async def list_devices(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> PlaybackDevicesResponse:
    """Devices Spotify currently knows about. Only awake ones appear."""
    token = await spotify_access_token(user, db, settings)
    status, payload = await _spotify("GET", "/me/player/devices", token)
    if status >= 400:
        raise HTTPException(status_code=status, detail=_explain(status))

    devices = [
        SpotifyDevice(
            id=d.get("id") or "",
            name=d.get("name") or "Unknown",
            type=d.get("type") or "Unknown",
            is_active=bool(d.get("is_active")),
            volume_percent=d.get("volume_percent"),
        )
        for d in (payload or {}).get("devices", [])
        if d.get("id")
    ]
    return PlaybackDevicesResponse(devices=devices)


@router.post("/play", status_code=204)
async def start_playback(
    payload: PlaybackStartRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> None:
    """Play a track on a Connect device, without the app losing the screen."""
    token = await spotify_access_token(user, db, settings)

    device_id = payload.device_id
    if not device_id:
        # Prefer the active device; fall back to the only one available.
        status, devices = await _spotify("GET", "/me/player/devices", token)
        found = [d for d in (devices or {}).get("devices", []) if d.get("id")]
        active = next((d for d in found if d.get("is_active")), None)
        chosen = active or (found[0] if len(found) == 1 else None)
        if chosen is None:
            raise HTTPException(status_code=404, detail=_explain(404))
        device_id = chosen["id"]

    status, body = await _spotify(
        "PUT",
        "/me/player/play",
        token,
        params={"device_id": device_id},
        json={"uris": [payload.uri]},
    )
    if status >= 400:
        log.warning("playback start failed (%s): %s", status, body)
        raise HTTPException(status_code=status, detail=_explain(status))


@router.post("/pause", status_code=204)
async def pause_playback(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> None:
    token = await spotify_access_token(user, db, settings)
    status, _ = await _spotify("PUT", "/me/player/pause", token)
    # 403 here usually means "already paused", which isn't worth failing on.
    if status >= 400 and status not in (403, 404):
        raise HTTPException(status_code=status, detail=_explain(status))


@router.post("/resume", status_code=204)
async def resume_playback(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> None:
    token = await spotify_access_token(user, db, settings)
    status, _ = await _spotify("PUT", "/me/player/play", token)
    if status >= 400 and status != 403:
        raise HTTPException(status_code=status, detail=_explain(status))


@router.post("/seek", status_code=204)
async def seek_playback(
    payload: PlaybackSeekRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> None:
    token = await spotify_access_token(user, db, settings)
    status, _ = await _spotify(
        "PUT", "/me/player/seek", token, params={"position_ms": payload.position_ms}
    )
    if status >= 400:
        raise HTTPException(status_code=status, detail=_explain(status))


@router.get("/state", response_model=PlaybackStateResponse)
async def playback_state(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> PlaybackStateResponse:
    """Current position and track, so the app can drive a progress bar.

    Spotify answers 204 with an empty body when nothing is playing, which is not
    an error -- it's the normal idle state.
    """
    token = await spotify_access_token(user, db, settings)
    status, payload = await _spotify("GET", "/me/player", token)
    if status == 204 or not payload:
        return PlaybackStateResponse(is_playing=False)
    if status >= 400:
        raise HTTPException(status_code=status, detail=_explain(status))

    item = payload.get("item") or {}
    device = payload.get("device") or {}
    return PlaybackStateResponse(
        is_playing=bool(payload.get("is_playing")),
        position_ms=int(payload.get("progress_ms") or 0),
        duration_ms=int(item.get("duration_ms") or 0),
        track_uri=item.get("uri"),
        device_name=device.get("name"),
    )
