"""Apple Music Catalog API client.

This is the workaround at the centre of the project: Apple's catalog still
returns 30-second preview URLs with only a *developer* token (ES256 JWT signed
with a .p8 key) -- no user subscription, no MusicKit user token. That preview
audio is what the DSP pipeline analyses.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import jwt

from app.config import Settings

log = logging.getLogger(__name__)

API_BASE = "https://api.music.apple.com/v1"
TOKEN_TTL_SECONDS = 60 * 60 * 12  # Apple allows up to 6 months; short is safer.


class AppleMusicError(RuntimeError):
    pass


@dataclass(slots=True)
class AppleMatch:
    apple_catalog_id: str
    preview_url: str | None
    title: str
    artist: str
    isrc: str | None
    match_method: str  # "isrc" | "fuzzy"


class DeveloperTokenProvider:
    """Signs and caches the ES256 developer token."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._token: str | None = None
        self._expires_at: float = 0.0

    def _private_key(self) -> str:
        if self.settings.apple_private_key:
            # Env vars flatten newlines; restore them so PyJWT can parse the PEM.
            return self.settings.apple_private_key.replace("\\n", "\n")
        path = Path(self.settings.apple_private_key_path).expanduser()
        if not path.is_file():
            raise AppleMusicError(f"Apple private key not found at {path}")
        return path.read_text()

    def token(self) -> str:
        now = time.time()
        if self._token and now < self._expires_at - 60:
            return self._token
        if not self.settings.apple_music_configured:
            raise AppleMusicError(
                "Apple Music is not configured "
                "(APPLE_TEAM_ID / APPLE_KEY_ID / APPLE_PRIVATE_KEY_PATH)"
            )
        issued = int(now)
        expires = issued + TOKEN_TTL_SECONDS
        self._token = jwt.encode(
            {"iss": self.settings.apple_team_id, "iat": issued, "exp": expires},
            self._private_key(),
            algorithm="ES256",
            headers={"kid": self.settings.apple_key_id, "alg": "ES256"},
        )
        self._expires_at = expires
        return self._token


class AppleMusicClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.tokens = DeveloperTokenProvider(settings)
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> AppleMusicClient:
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
            raise RuntimeError("AppleMusicClient must be used as an async context manager")
        return self._client

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = await self.client.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {self.tokens.token()}"},
            params=params or {},
        )
        if resp.status_code == 404:
            return {}
        if resp.status_code >= 400:
            raise AppleMusicError(f"Apple Music {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    @staticmethod
    def _to_match(song: dict[str, Any], method: str) -> AppleMatch:
        attrs = song.get("attributes", {})
        previews = attrs.get("previews") or []
        return AppleMatch(
            apple_catalog_id=song.get("id", ""),
            preview_url=previews[0].get("url") if previews else None,
            title=attrs.get("name", ""),
            artist=attrs.get("artistName", ""),
            isrc=attrs.get("isrc"),
            match_method=method,
        )

    async def find_by_isrc(self, isrc: str) -> AppleMatch | None:
        """Exact match. `filter[isrc]` is the whole reason this approach works."""
        storefront = self.settings.apple_storefront
        payload = await self._get(f"/catalog/{storefront}/songs", {"filter[isrc]": isrc})
        songs = payload.get("data") or []
        if not songs:
            return None
        # Prefer a result that actually has preview audio; a match without one is useless.
        with_preview = [s for s in songs if (s.get("attributes", {}).get("previews"))]
        return self._to_match((with_preview or songs)[0], "isrc")

    async def find_by_search(self, title: str, artist: str) -> AppleMatch | None:
        """Lower-confidence fallback for tracks whose ISRC isn't in Apple's catalog."""
        storefront = self.settings.apple_storefront
        payload = await self._get(
            f"/catalog/{storefront}/search",
            {"term": f"{title} {artist}", "types": "songs", "limit": 5},
        )
        songs = ((payload.get("results") or {}).get("songs") or {}).get("data") or []
        if not songs:
            return None
        target_artist = artist.lower().split(",")[0].strip()
        for song in songs:
            attrs = song.get("attributes", {})
            if not attrs.get("previews"):
                continue
            if target_artist and target_artist in attrs.get("artistName", "").lower():
                return self._to_match(song, "fuzzy")
        first_with_preview = next(
            (s for s in songs if s.get("attributes", {}).get("previews")), None
        )
        return self._to_match(first_with_preview, "fuzzy") if first_with_preview else None

    async def download_preview(self, preview_url: str) -> bytes:
        resp = await self.client.get(preview_url, follow_redirects=True, timeout=60.0)
        if resp.status_code >= 400:
            raise AppleMusicError(f"preview download failed: {resp.status_code}")
        return resp.content
