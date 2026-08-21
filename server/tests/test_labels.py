"""Human corrections: immediate override, and training labels."""

from __future__ import annotations

from sqlalchemy import select

from app import selection
from app.models import MoodLabel, MoodScore, Track, UserTrack

AUTH = {"Authorization": "Bearer test-session-token"}


def _first_track(db, user):
    return db.execute(
        select(Track).join(UserTrack, UserTrack.track_id == Track.id).where(
            UserTrack.user_id == user.id
        )
    ).scalars().first()


def test_label_records_the_human_score_and_what_the_model_said(client, db, seeded_user):
    track = _first_track(db, seeded_user)
    before = db.get(MoodScore, track.id).score

    body = client.post(
        "/mood/label", json={"track_id": track.id, "score": 99}, headers=AUTH
    ).json()

    assert body["score"] == 99
    assert body["model_score"] == before
    assert body["total_labels"] == 1
    # The model's own output must survive, or retraining loses its baseline.
    assert db.get(MoodScore, track.id).score == before


def test_correction_changes_where_the_slider_finds_the_track(client, db, seeded_user):
    """A fixed score must move the track, not just relabel it."""
    track = _first_track(db, seeded_user)  # seeded scores start at 5
    client.post("/mood/label", json={"track_id": track.id, "score": 95}, headers=AUTH)

    near_top = selection.candidates_near(db, seeded_user.id, 95)
    assert track.id in {c.track.id for c in near_top}

    picked = next(c for c in near_top if c.track.id == track.id)
    assert picked.score == 95
    assert picked.model_version == "human"


def test_a_second_correction_replaces_the_first(client, db, seeded_user):
    track = _first_track(db, seeded_user)
    client.post("/mood/label", json={"track_id": track.id, "score": 80}, headers=AUTH)
    body = client.post(
        "/mood/label", json={"track_id": track.id, "score": 20}, headers=AUTH
    ).json()

    assert body["score"] == 20
    assert body["total_labels"] == 1, "one row per user per track"
    labels = db.execute(select(MoodLabel).where(MoodLabel.track_id == track.id)).scalars().all()
    assert len(labels) == 1


def test_original_model_score_is_kept_across_repeated_corrections(client, db, seeded_user):
    track = _first_track(db, seeded_user)
    original = db.get(MoodScore, track.id).score
    client.post("/mood/label", json={"track_id": track.id, "score": 80}, headers=AUTH)
    body = client.post(
        "/mood/label", json={"track_id": track.id, "score": 20}, headers=AUTH
    ).json()
    assert body["model_score"] == original


def test_withdrawing_a_correction_restores_the_model(client, db, seeded_user):
    track = _first_track(db, seeded_user)
    model_score = db.get(MoodScore, track.id).score
    client.post("/mood/label", json={"track_id": track.id, "score": 95}, headers=AUTH)

    assert client.delete(f"/mood/label/{track.id}", headers=AUTH).status_code == 204

    found = selection.candidates_near(db, seeded_user.id, model_score)
    picked = next(c for c in found if c.track.id == track.id)
    assert picked.score == model_score
    assert picked.model_version != "human"


def test_labels_shift_the_library_distribution(client, db, seeded_user):
    """Relative slider mapping must follow corrections, not stale model scores."""
    before = seeded_user.score_mean
    for track in db.execute(
        select(Track).join(UserTrack, UserTrack.track_id == Track.id).where(
            UserTrack.user_id == seeded_user.id
        ).limit(5)
    ).scalars():
        client.post("/mood/label", json={"track_id": track.id, "score": 100}, headers=AUTH)

    db.refresh(seeded_user)
    assert seeded_user.score_mean > (before or 0)


def test_cannot_label_a_track_outside_your_library(client, db, seeded_user):
    orphan = Track(spotify_track_id="not-mine", title="X", artist="Y", isrc="ORPHAN01")
    db.add(orphan)
    db.commit()

    response = client.post(
        "/mood/label", json={"track_id": orphan.id, "score": 50}, headers=AUTH
    )
    assert response.status_code == 404


def test_label_rejects_out_of_range_scores(client, db, seeded_user):
    track = _first_track(db, seeded_user)
    for bad in (0, 101, -5):
        response = client.post(
            "/mood/label", json={"track_id": track.id, "score": bad}, headers=AUTH
        )
        assert response.status_code == 422


def test_labelling_requires_auth(client, db, seeded_user):
    track = _first_track(db, seeded_user)
    assert client.post("/mood/label", json={"track_id": track.id, "score": 50}).status_code == 401
