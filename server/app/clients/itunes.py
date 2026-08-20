"""iTunes Search API client -- preview lookup with no credentials at all.

The Apple Music Catalog API (`app.clients.apple_music`) is the accurate path:
it matches on `filter[isrc]`, so you know you analysed the same recording the
user owns. It needs an Apple Developer account and an ES256-signed developer
token, which is a real barrier to just running this project.

The public iTunes Search API needs no token, no account, and no key, and still
returns 30-second `previewUrl`s. The catch is that it has **no ISRC lookup**:
`itunes.apple.com/lookup` only accepts `id`, `upc`, `isbn`, `amgArtistId` and
friends. Passing `isrc=` is not an error -- it silently returns
`resultCount: 0`, which makes it an easy thing to believe is working. So every
match here is a text search, and is marked `fuzzy` confidence accordingly.

Use this as a fallback tier when Apple Music isn't configured.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"

# Strip parenthetical noise ("(Remastered 2011)", "- Radio Edit") that pushes
# the text search toward the wrong recording.
_NOISE = re.compile(
    r"\s*[-(\[]\s*(remaster(ed)?|radio edit|single version|deluxe|bonus|live|"
    r"explicit|clean|feat\.?|featuring)\b.*$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ITunesMatch:
    apple_catalog_id: str
    preview_url: str | None
    title: str
    artist: str
    artwork_url: str | None
    genre: str | None
    match_method: str = "fuzzy"


def _normalise(text: str) -> str:
    return _NOISE.sub("", text or "").strip()


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 1}


class ITunesClient:
    def __init__(self, client: httpx.AsyncClient | None = None, storefront: str = "US"):
        self.storefront = storefront
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> ITunesClient:
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
            raise RuntimeError("ITunesClient must be used as an async context manager")
        return self._client

    @staticmethod
    def _to_match(song: dict[str, Any]) -> ITunesMatch:
        artwork = song.get("artworkUrl100") or song.get("artworkUrl60")
        if artwork:
            # The API returns 100px art; ask for something usable on a phone.
            artwork = artwork.replace("100x100bb", "600x600bb")
        return ITunesMatch(
            apple_catalog_id=str(song.get("trackId") or ""),
            preview_url=song.get("previewUrl"),
            title=song.get("trackName", ""),
            artist=song.get("artistName", ""),
            artwork_url=artwork,
            genre=song.get("primaryGenreName"),
        )

    def _score_candidate(self, song: dict[str, Any], title: str, artist: str) -> float:
        """How well does a result match what we asked for? 0..1."""
        want_title, want_artist = _tokens(title), _tokens(artist)
        got_title, got_artist = _tokens(song.get("trackName", "")), _tokens(
            song.get("artistName", "")
        )
        if not want_title:
            return 0.0

        title_overlap = len(want_title & got_title) / len(want_title)
        artist_overlap = (
            len(want_artist & got_artist) / len(want_artist) if want_artist else 0.5
        )
        # Artist agreement matters more: a cover has the right title and the
        # wrong sound, which is exactly the failure mode that corrupts scoring.
        return 0.4 * title_overlap + 0.6 * artist_overlap

    async def find(
        self, title: str, artist: str, limit: int = 10, min_score: float = 0.5
    ) -> ITunesMatch | None:
        """Best text match that actually has preview audio."""
        term = f"{_normalise(title)} {_normalise(artist)}".strip()
        if not term:
            return None

        try:
            resp = await self.client.get(
                SEARCH_URL,
                params={
                    "term": term,
                    "media": "music",
                    "entity": "song",
                    "limit": limit,
                    "country": self.storefront,
                },
            )
            resp.raise_for_status()
            # iTunes serves JSON as text/javascript, so don't trust the content type.
            results = resp.json().get("results", [])
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("iTunes search failed for %r: %s", term, exc)
            return None

        best: tuple[float, dict[str, Any]] | None = None
        for song in results:
            if not song.get("previewUrl"):
                continue  # a match with no audio is useless to us
            score = self._score_candidate(song, title, artist)
            if best is None or score > best[0]:
                best = (score, song)

        if best is None or best[0] < min_score:
            if best is not None:
                log.debug("iTunes best match for %r scored %.2f, below floor", term, best[0])
            return None
        return self._to_match(best[1])

    async def lookup_by_id(self, apple_track_id: str) -> ITunesMatch | None:
        """Re-fetch a known track. Preview URLs expire, ids don't."""
        try:
            resp = await self.client.get(LOOKUP_URL, params={"id": apple_track_id})
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("iTunes lookup failed for %s: %s", apple_track_id, exc)
            return None
        return self._to_match(results[0]) if results else None

    async def download_preview(self, preview_url: str) -> bytes:
        resp = await self.client.get(preview_url, follow_redirects=True, timeout=60.0)
        resp.raise_for_status()
        return resp.content
