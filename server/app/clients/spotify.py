"""Spotify Web API client.

Only endpoints that survived the 2024-11-27 third-party restrictions are used:
OAuth, profile, and library/track reads. `audio-features`, `audio-analysis` and
`recommendations` are gone for new apps -- ISRC from the standard track object
is what replaces them here.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

ACCOUNTS_BASE = "https://accounts.spotify.com"
API_BASE = "https://api.spotify.com/v1"


class SpotifyError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Spotify API {status}: {message}")
        self.status = status
        self.message = message


@dataclass(slots=True)
class SpotifyTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int
    scope: str = ""
    token_type: str = "Bearer"


@dataclass(slots=True)
class SpotifyTrack:
    spotify_track_id: str
    title: str
    artist: str
    album: str | None
    isrc: str | None
    duration_ms: int | None
    artwork_url: str | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> SpotifyTrack | None:
        # Local files and unavailable tracks come back without an id.
        if not item or not item.get("id"):
            return None
        images = (item.get("album") or {}).get("images") or []
        return cls(
            spotify_track_id=item["id"],
            title=item.get("name", "Unknown"),
            artist=", ".join(a["name"] for a in item.get("artists", []) if a.get("name"))
            or "Unknown",
            album=(item.get("album") or {}).get("name"),
            isrc=(item.get("external_ids") or {}).get("isrc"),
            duration_ms=item.get("duration_ms"),
            artwork_url=images[0]["url"] if images else None,
        )


class SpotifyClient:
    """Thin async wrapper. Construct per request; it owns no global state."""

    def __init__(self, access_token: str | None = None, client: httpx.AsyncClient | None = None):
        self.access_token = access_token
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> SpotifyClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("SpotifyClient must be used as an async context manager")
        return self._client

    # ------------------------------------------------------------------ OAuth

    @staticmethod
    def authorize_url(client_id: str, redirect_uri: str, code_challenge: str, scopes: str) -> str:
        params = httpx.QueryParams(
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "code_challenge_method": "S256",
                "code_challenge": code_challenge,
                "scope": scopes,
            }
        )
        return f"{ACCOUNTS_BASE}/authorize?{params}"

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        client_id: str,
        redirect_uri: str,
        client_secret: str | None = None,
    ) -> SpotifyTokens:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        return await self._token_request(data, client_id, client_secret)

    async def refresh_token(
        self, *, refresh_token: str, client_id: str, client_secret: str | None = None
    ) -> SpotifyTokens:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        tokens = await self._token_request(data, client_id, client_secret)
        # Spotify may omit refresh_token on refresh; keep the existing one.
        if not tokens.refresh_token:
            tokens.refresh_token = refresh_token
        return tokens

    async def _token_request(
        self, data: dict[str, str], client_id: str, client_secret: str | None
    ) -> SpotifyTokens:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if client_secret:
            basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            headers["Authorization"] = f"Basic {basic}"
        resp = await self.client.post(f"{ACCOUNTS_BASE}/api/token", data=data, headers=headers)
        if resp.status_code >= 400:
            raise SpotifyError(resp.status_code, resp.text)
        payload = resp.json()
        return SpotifyTokens(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_in=payload.get("expires_in", 3600),
            scope=payload.get("scope", ""),
        )

    # ------------------------------------------------------------------- API

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        if not self.access_token:
            raise SpotifyError(401, "no access token")
        resp = await self.client.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {self.access_token}"},
            params={k: v for k, v in params.items() if v is not None},
        )
        if resp.status_code >= 400:
            raise SpotifyError(resp.status_code, resp.text)
        return resp.json()

    async def get_me(self) -> dict[str, Any]:
        return await self._get("/me")

    async def get_saved_tracks(self, limit: int = 50, max_tracks: int = 500) -> list[SpotifyTrack]:
        """Walk /me/tracks. The full track object already carries external_ids.isrc."""
        tracks: list[SpotifyTrack] = []
        offset = 0
        while len(tracks) < max_tracks:
            page = await self._get("/me/tracks", limit=min(limit, 50), offset=offset)
            items = page.get("items", [])
            if not items:
                break
            for item in items:
                parsed = SpotifyTrack.from_api(item.get("track") or {})
                if parsed:
                    tracks.append(parsed)
            if not page.get("next"):
                break
            offset += len(items)
        return tracks[:max_tracks]

    async def get_playlist_tracks(
        self, playlist_id: str, max_tracks: int = 500
    ) -> list[SpotifyTrack]:
        tracks: list[SpotifyTrack] = []
        offset = 0
        while len(tracks) < max_tracks:
            page = await self._get(
                f"/playlists/{playlist_id}/tracks", limit=100, offset=offset, market="from_token"
            )
            items = page.get("items", [])
            if not items:
                break
            for item in items:
                parsed = SpotifyTrack.from_api(item.get("track") or {})
                if parsed:
                    tracks.append(parsed)
            if not page.get("next"):
                break
            offset += len(items)
        return tracks[:max_tracks]

    async def get_tracks(self, track_ids: list[str]) -> list[SpotifyTrack]:
        """Batch lookup (50 per call) -- used to backfill ISRCs."""
        out: list[SpotifyTrack] = []
        for i in range(0, len(track_ids), 50):
            chunk = track_ids[i : i + 50]
            payload = await self._get("/tracks", ids=",".join(chunk))
            for item in payload.get("tracks", []) or []:
                parsed = SpotifyTrack.from_api(item or {})
                if parsed:
                    out.append(parsed)
        return out
