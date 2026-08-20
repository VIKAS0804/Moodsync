"""Library-relative slider mapping.

The problem being solved: mood scores are absolute, but a real library isn't
spread across 1-100. Somebody who only listens to mellow indie might have
everything between 20 and 45, so on the absolute scale the top half of their
slider returns nothing new and their most energetic track is unreachable.
"""

from __future__ import annotations

from app import selection
from app.models import MoodScore, Track, User, UserTrack


def _library(db, scores: list[int], spotify_user_id="narrow", token="narrow-token") -> User:
    user = User(spotify_user_id=spotify_user_id, product="free", session_token=token)
    db.add(user)
    db.commit()
    for i, score in enumerate(scores):
        track = Track(
            spotify_track_id=f"{spotify_user_id}-{i}",
            title=f"T{i}",
            artist="A",
            isrc=f"{spotify_user_id[:4].upper()}{i:08d}",
        )
        db.add(track)
        db.flush()
        db.add(MoodScore(track_id=track.id, score=score, confidence=1.0, model_version="t"))
        db.add(UserTrack(user_id=user.id, track_id=track.id))
    db.commit()
    selection.refresh_score_stats(db, user)
    return user


def test_stats_are_cached_on_the_user(db):
    user = _library(db, [20, 25, 30, 35, 40, 45, 30, 35])
    assert user.score_count == 8
    assert 30 <= user.score_mean <= 34
    assert user.score_stddev > 0
    assert user.score_stats_at is not None


def test_slider_50_lands_on_the_library_mean(db):
    user = _library(db, [20, 25, 30, 35, 40, 45, 30, 35])
    target, mode = selection.resolve_target(user, 50)
    assert mode == "relative"
    assert abs(target - user.score_mean) <= 1


def test_a_narrow_library_still_uses_the_whole_slider(db):
    """The core win: mellow-only library, but slider 100 still reaches its top."""
    user = _library(db, [20, 22, 25, 28, 30, 33, 38, 42, 45])

    low, _ = selection.resolve_target(user, 5)
    high, _ = selection.resolve_target(user, 95)

    # On the absolute scale, sliding to 95 would look for a 95 and find nothing
    # near it. Mapped, it aims near the top of what this user actually owns.
    assert low < user.score_mean < high
    assert high <= 60, "should not aim far outside a library that tops out at 45"
    assert high >= 40, "should still reach this user's most energetic material"


def test_relative_targets_increase_monotonically(db):
    user = _library(db, [20, 25, 30, 35, 40, 45, 30, 35])
    targets = [selection.resolve_target(user, s)[0] for s in (1, 20, 40, 60, 80, 100)]
    assert targets == sorted(targets)


def test_targets_are_clamped_to_the_slider_range(db):
    user = _library(db, [48, 49, 50, 51, 52, 50, 49, 51])  # tiny sd
    for slider in (1, 100):
        target, _ = selection.resolve_target(user, slider)
        assert 1 <= target <= 100


def test_small_libraries_fall_back_to_absolute(db):
    """With 3 tracks a mean/sd is noise, so don't pretend to normalise."""
    user = _library(db, [30, 50, 70], spotify_user_id="tiny", token="tiny-token")
    assert user.score_count < selection.MIN_TRACKS_FOR_RELATIVE
    target, mode = selection.resolve_target(user, 90)
    assert mode == "absolute"
    assert target == 90


def test_zero_variance_library_falls_back_to_absolute(db):
    user = _library(db, [50] * 10, spotify_user_id="flat", token="flat-token")
    target, mode = selection.resolve_target(user, 80)
    assert mode == "absolute"
    assert target == 80


def test_absolute_flag_bypasses_the_mapping(db):
    user = _library(db, [20, 25, 30, 35, 40, 45, 30, 35])
    target, mode = selection.resolve_target(user, 90, absolute=True)
    assert (target, mode) == (90, "absolute")


def test_endpoint_reports_the_mapping_it_used(client, db):
    _library(db, [20, 22, 25, 28, 30, 33, 38, 42, 45], token="test-session-token")

    body = client.get("/mood/95", headers={"Authorization": "Bearer test-session-token"}).json()
    assert body["slider_mode"] == "relative"
    assert body["requested_score"] == 95
    # The absolute score searched for is not the raw slider value.
    assert body["absolute_target"] != 95
    assert body["library_mean"] is not None
    assert body["track_score"] <= 45  # can only ever return a track it owns


def test_endpoint_absolute_query_param(client, db):
    _library(db, [20, 22, 25, 28, 30, 33, 38, 42, 45], token="test-session-token")

    body = client.get(
        "/mood/95",
        params={"absolute": "true"},
        headers={"Authorization": "Bearer test-session-token"},
    ).json()
    assert body["slider_mode"] == "absolute"
    assert body["absolute_target"] == 95
