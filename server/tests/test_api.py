"""HTTP contract tests -- this is what the mobile app codes against."""

from __future__ import annotations


def test_health_reports_dependency_status(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "apple_music_configured" in body


def test_mood_requires_authentication(client):
    assert client.get("/mood/50").status_code == 401


def test_mood_rejects_out_of_range_scores(client, auth_headers, seeded_user):
    assert client.get("/mood/0", headers=auth_headers).status_code == 422
    assert client.get("/mood/101", headers=auth_headers).status_code == 422


def test_mood_returns_a_nearby_track(client, auth_headers, seeded_user):
    response = client.get("/mood/45", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["requested_score"] == 45
    assert body["distance"] <= 5
    assert body["track"]["spotify_uri"].startswith("spotify:track:")
    assert body["pool_size"] == 10
    assert body["playback_mode"] == "spotify_remote"  # seeded user is premium
    assert body["latency_ms"] >= 0


def test_mood_honours_exclude_so_the_slider_does_not_repeat(client, auth_headers, seeded_user):
    first = client.get("/mood/45", headers=auth_headers).json()["track"]["spotify_track_id"]
    second = client.get(
        "/mood/45", params={"exclude": [first]}, headers=auth_headers
    ).json()["track"]["spotify_track_id"]
    assert second != first


def test_mood_404s_on_an_empty_library(client, auth_headers, db):
    from app.models import Session as DeviceSession
    from app.models import User

    user = User(spotify_user_id="empty", product="free")
    db.add(user)
    db.flush()
    db.add(DeviceSession(token="test-session-token", user_id=user.id))
    db.commit()

    response = client.get("/mood/50", headers=auth_headers)
    assert response.status_code == 404
    assert "sync" in response.json()["detail"].lower()


def test_free_account_is_told_to_use_preview_fallback(client, auth_headers, db, seeded_user):
    seeded_user.product = "free"
    db.commit()

    body = client.get("/mood/45", headers=auth_headers).json()
    assert body["playback_mode"] == "preview_fallback"
    assert body["preview_url"]  # the app needs this to play anything at all


def test_me_reports_library_coverage(client, auth_headers, seeded_user):
    body = client.get("/auth/me", headers=auth_headers).json()
    assert body["library_size"] == 10
    assert body["scored_tracks"] == 10
    assert body["has_premium"] is True


def test_sync_status_buckets_scores(client, auth_headers, seeded_user):
    body = client.get("/sync/status", headers=auth_headers).json()
    assert body["total_tracks"] == 10
    assert body["scored"] == 10
    assert body["coverage"] == 1.0
    assert sum(body["score_histogram"].values()) == 10


def test_logout_invalidates_the_session(client, auth_headers, seeded_user):
    assert client.post("/auth/logout", headers=auth_headers).status_code == 204
    assert client.get("/mood/45", headers=auth_headers).status_code == 401
