"""iTunes Search client -- the credential-free preview tier."""

from __future__ import annotations

import httpx
import pytest

from app.clients import itunes as itunes_module
from app.clients.itunes import ITunesClient, ITunesTransient, _normalise


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    """Strip the rate limiter and backoff so the suite isn't paced by them."""
    monkeypatch.setattr(itunes_module._throttle, "min_interval", 0.0)

    async def instant(_seconds):
        return None

    monkeypatch.setattr(itunes_module.asyncio, "sleep", instant)


def _song(track_id, name, artist, preview="https://example.test/p.m4a"):
    return {
        "trackId": track_id,
        "trackName": name,
        "artistName": artist,
        "previewUrl": preview,
        "artworkUrl100": "https://example.test/a/100x100bb.jpg",
        "primaryGenreName": "Rock",
    }


def _client(handler) -> ITunesClient:
    return ITunesClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Bohemian Rhapsody (Remastered 2011)", "Bohemian Rhapsody"),
        ("Song - Radio Edit", "Song"),
        ("Track (feat. Someone)", "Track"),
        ("Plain Title", "Plain Title"),
    ],
)
def test_normalise_strips_release_noise(raw, expected):
    assert _normalise(raw) == expected


async def test_find_returns_best_matching_song():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "results": [
                    _song(1, "Mr. Brightside", "Some Cover Band"),
                    _song(2, "Mr. Brightside", "The Killers"),
                ]
            },
        )

    async with _client(handler) as itunes:
        match = await itunes.find("Mr. Brightside", "The Killers")

    assert match is not None
    assert match.apple_catalog_id == "2"
    assert match.artist == "The Killers"
    # Artist agreement is weighted above title, because a cover has the right
    # title and the wrong audio -- which would silently corrupt the score.


async def test_results_without_preview_audio_are_skipped():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "results": [
                    _song(1, "Exact Title", "Exact Artist", preview=None),
                    _song(2, "Exact Title", "Exact Artist"),
                ]
            },
        )

    async with _client(handler) as itunes:
        match = await itunes.find("Exact Title", "Exact Artist")

    assert match is not None and match.apple_catalog_id == "2"


async def test_weak_matches_are_rejected_rather_than_guessed():
    def handler(request):
        return httpx.Response(200, json={"results": [_song(9, "Totally Other", "Nobody At All")]})

    async with _client(handler) as itunes:
        assert await itunes.find("Mr. Brightside", "The Killers") is None


async def test_empty_results_return_none():
    def handler(request):
        return httpx.Response(200, json={"results": []})

    async with _client(handler) as itunes:
        assert await itunes.find("Nothing", "Nobody") is None


async def test_network_failure_raises_rather_than_reporting_no_match():
    """A failure that reached nobody must not look like "this track doesn't exist".

    Returning None here is what poisoned a real catalog: 1,657 tracks were
    cached as having no preview because iTunes was rate-limiting, and the
    negative cache made that permanent.
    """

    def handler(request):
        raise httpx.ConnectError("boom")

    async with _client(handler) as itunes:
        with pytest.raises(ITunesTransient):
            await itunes.find("Some Song", "Some Artist")


@pytest.mark.parametrize("status", [403, 429, 500, 503])
async def test_throttling_and_server_errors_are_transient(status):
    def handler(request):
        return httpx.Response(status, json={})

    async with _client(handler) as itunes:
        with pytest.raises(ITunesTransient):
            await itunes.find("Some Song", "Some Artist")


async def test_transient_failures_are_retried_before_giving_up():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"results": [_song(7, "Title", "Artist")]})

    async with _client(handler) as itunes:
        match = await itunes.find("Title", "Artist")

    assert match is not None and attempts["n"] == 3


async def test_a_real_empty_result_is_not_transient():
    """200 with no results genuinely means "not in the catalog"; cache that."""

    def handler(request):
        return httpx.Response(200, json={"results": []})

    async with _client(handler) as itunes:
        assert await itunes.find("Nothing", "Nobody") is None


async def test_artwork_is_upgraded_from_thumbnail_size():
    def handler(request):
        return httpx.Response(200, json={"results": [_song(3, "Title", "Artist")]})

    async with _client(handler) as itunes:
        match = await itunes.find("Title", "Artist")

    assert match is not None
    assert "600x600bb" in match.artwork_url


async def test_matches_are_marked_fuzzy_never_exact():
    """iTunes Search has no ISRC lookup, so no match here is ever exact.

    `itunes.apple.com/lookup?isrc=` is not a supported parameter -- it returns
    resultCount 0 rather than an error, which makes it easy to believe it works.
    Confidence must reflect that these are text matches.
    """
    def handler(request):
        return httpx.Response(200, json={"results": [_song(4, "Title", "Artist")]})

    async with _client(handler) as itunes:
        match = await itunes.find("Title", "Artist")

    assert match is not None and match.match_method == "fuzzy"


async def test_search_sends_song_scoped_query_params():
    seen = {}

    def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"results": []})

    async with _client(handler) as itunes:
        await itunes.find("Title", "Artist")

    assert seen["entity"] == "song"
    assert seen["media"] == "music"
