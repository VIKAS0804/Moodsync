"""The server-side browser login (GET /auth/spotify/login -> GET callback)."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from app.config import Settings
from app.deps import settings_dep
from app.main import app
from app.routers import auth


@pytest.fixture
def configured_spotify():
    """Pretend Spotify credentials are present, without touching the real env."""

    def _override() -> Settings:
        return Settings(
            spotify_client_id="test-client-id",
            spotify_web_redirect_uri="http://127.0.0.1:8000/auth/spotify/callback",
        )

    app.dependency_overrides[settings_dep] = _override
    yield
    app.dependency_overrides.pop(settings_dep, None)


@pytest.fixture
def unconfigured_spotify():
    """No client id. The test env sets one, so it has to be overridden away."""

    app.dependency_overrides[settings_dep] = lambda: Settings(spotify_client_id="")
    yield
    app.dependency_overrides.pop(settings_dep, None)


def test_login_explains_itself_when_unconfigured(client, unconfigured_spotify):
    response = client.get("/auth/spotify/login", follow_redirects=False)
    assert response.status_code == 400
    body = response.text
    assert "SPOTIFY_CLIENT_ID" in body
    # The whole point of the page: an account is not the same as a developer app.
    assert "developer.spotify.com" in body


def test_login_redirects_to_spotify_with_pkce(client, configured_spotify):
    response = client.get("/auth/spotify/login", follow_redirects=False)
    assert response.status_code in (302, 307)

    target = urlparse(response.headers["location"])
    assert target.netloc == "accounts.spotify.com"
    params = parse_qs(target.query)

    assert params["client_id"] == ["test-client-id"]
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == ["http://127.0.0.1:8000/auth/spotify/callback"]
    assert "user-library-read" in params["scope"][0]
    assert params["state"][0]
    assert params["code_challenge"][0]


def test_issued_state_maps_to_a_verifier_matching_the_challenge(client, configured_spotify):
    response = client.get("/auth/spotify/login", follow_redirects=False)
    params = parse_qs(urlparse(response.headers["location"]).query)
    state, challenge = params["state"][0], params["code_challenge"][0]

    assert state in auth._PENDING
    verifier = auth._PENDING[state][0]

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert expected == challenge, "challenge must be S256(verifier) or Spotify rejects it"


def test_callback_rejects_unknown_state(client):
    response = client.get("/auth/spotify/callback", params={"code": "x", "state": "never-issued"})
    assert response.status_code == 400
    assert "expired" in response.text.lower() or "isn't one we issued" in response.text


def test_callback_surfaces_spotify_denial(client):
    response = client.get("/auth/spotify/callback", params={"error": "access_denied"})
    assert response.status_code == 400
    assert "access_denied" in response.text


def test_callback_requires_both_code_and_state(client):
    assert client.get("/auth/spotify/callback").status_code == 400
    assert client.get("/auth/spotify/callback", params={"code": "only-code"}).status_code == 400


def test_state_is_single_use(client, configured_spotify):
    """A replayed callback must not re-authenticate."""
    response = client.get("/auth/spotify/login", follow_redirects=False)
    state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]

    # Consume it. The exchange fails (no real Spotify), but the state is spent.
    client.get("/auth/spotify/callback", params={"code": "bogus", "state": state})
    assert state not in auth._PENDING

    second = client.get("/auth/spotify/callback", params={"code": "bogus", "state": state})
    assert second.status_code == 400


def test_expired_handshakes_are_evicted(client, configured_spotify, monkeypatch):
    auth._PENDING.clear()

    # Pin the clock, record an entry, then advance past the TTL. A new login
    # sweeps stale entries, so an abandoned handshake can't leak forever.
    monkeypatch.setattr(auth.time, "time", lambda: 1_000.0)
    auth._remember("stale-state", "verifier")
    assert "stale-state" in auth._PENDING

    monkeypatch.setattr(auth.time, "time", lambda: 1_000.0 + auth._PENDING_TTL_SECONDS + 1)
    client.get("/auth/spotify/login", follow_redirects=False)
    assert "stale-state" not in auth._PENDING
