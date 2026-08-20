"""Slider -> track selection."""

from __future__ import annotations

import random

from app import selection
from app.models import Track


def test_candidates_are_ordered_by_closeness(db, seeded_user):
    candidates = selection.candidates_near(db, seeded_user.id, 45)
    distances = [abs(c.score - 45) for c in candidates]
    assert distances == sorted(distances)
    assert distances[0] <= 5


def test_window_widens_when_nothing_is_nearby(db, seeded_user):
    """Seeded scores are 10 apart, so an extreme target still has to return something."""
    candidates = selection.candidates_near(db, seeded_user.id, 100)
    assert candidates
    assert candidates[0].score == 95


def test_excluded_tracks_are_never_returned(db, seeded_user):
    first = selection.candidates_near(db, seeded_user.id, 45)[0]
    excluded = selection.candidates_near(
        db, seeded_user.id, 45, exclude={first.track.spotify_track_id}
    )
    assert all(c.track.spotify_track_id != first.track.spotify_track_id for c in excluded)


def _candidate(score: int, confidence: float = 1.0) -> selection.Candidate:
    track = Track(spotify_track_id=f"c{score}", title=f"T{score}", artist="A")
    return selection.Candidate(track, score, confidence, "test", None)


def test_choose_prefers_closer_tracks_over_many_draws():
    candidates = [_candidate(50), _candidate(60), _candidate(70)]
    rng = random.Random(1234)
    picks = [selection.choose(candidates, 50, rng).score for _ in range(600)]

    # The exact match should dominate, without being the only thing ever picked.
    assert picks.count(50) > 300
    assert len(set(picks)) == 3
    assert picks.count(60) > picks.count(70)


def test_choose_discounts_low_confidence_fuzzy_matches():
    """Two equally close tracks: the exact-ISRC one should win more often."""
    candidates = [_candidate(50, confidence=1.0), _candidate(50, confidence=0.1)]
    rng = random.Random(99)
    picks = [selection.choose(candidates, 50, rng).confidence for _ in range(400)]
    assert picks.count(1.0) > 300


def test_slider_returns_variety_when_several_tracks_share_a_band(db, seeded_user):
    """Seeded scores sit 10 apart, so a target of 50 has two tracks within +/-5."""
    candidates = selection.candidates_near(db, seeded_user.id, 50)
    assert len(candidates) == 2

    rng = random.Random(7)
    picks = {selection.choose(candidates, 50, rng).score for _ in range(100)}
    assert picks == {45, 55}


def test_choose_on_empty_pool_returns_none():
    assert selection.choose([], 50) is None


def test_pool_size_counts_only_scored_library_tracks(db, seeded_user):
    assert selection.pool_size(db, seeded_user.id) == 10


def test_other_users_libraries_are_not_visible(db, seeded_user):
    assert selection.candidates_near(db, "some-other-user-id", 50) == []
