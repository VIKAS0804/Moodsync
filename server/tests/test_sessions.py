"""Multiple signed-in devices.

The bug this replaces: `users.session_token` was a single column, so every login
overwrote it. Signing in on a phone silently signed out the laptop, which in
practice meant a working token going dead with no explanation.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import Session as DeviceSession
from app.models import User

AUTH = {"Authorization": "Bearer test-session-token"}


def _add_session(db, user, token, label):
    db.add(DeviceSession(token=token, user_id=user.id, device_label=label))
    db.commit()


def test_two_devices_can_be_signed_in_at_once(client, db, seeded_user):
    _add_session(db, seeded_user, "phone-token", "phone")

    # Both tokens work, independently.
    assert client.get("/auth/me", headers=AUTH).status_code == 200
    assert (
        client.get("/auth/me", headers={"Authorization": "Bearer phone-token"}).status_code == 200
    )


def test_signing_out_one_device_leaves_the_other(client, db, seeded_user):
    _add_session(db, seeded_user, "phone-token", "phone")

    assert client.post("/auth/logout", headers=AUTH).status_code == 204

    # The signed-out device is gone...
    assert client.get("/auth/me", headers=AUTH).status_code == 401
    # ...and the other one is untouched. This is the whole point.
    assert (
        client.get("/auth/me", headers={"Authorization": "Bearer phone-token"}).status_code == 200
    )


def test_sessions_are_listed_with_the_current_one_marked(client, db, seeded_user):
    _add_session(db, seeded_user, "phone-token", "phone")

    body = client.get("/auth/sessions", headers=AUTH).json()
    labels = {s["device_label"] for s in body["sessions"]}
    assert labels == {"tests", "phone"}
    current = [s for s in body["sessions"] if s["is_current"]]
    assert len(current) == 1 and current[0]["device_label"] == "tests"


def test_revoking_others_keeps_only_this_device(client, db, seeded_user):
    _add_session(db, seeded_user, "phone-token", "phone")
    _add_session(db, seeded_user, "tablet-token", "tablet")

    assert client.post("/auth/sessions/revoke-others", headers=AUTH).status_code == 204

    remaining = db.execute(
        select(DeviceSession).where(DeviceSession.user_id == seeded_user.id)
    ).scalars().all()
    assert [r.token for r in remaining] == ["test-session-token"]


def test_using_a_session_records_activity(client, db, seeded_user):
    before = db.get(DeviceSession, "test-session-token").last_used_at
    client.get("/auth/me", headers=AUTH)
    db.expire_all()
    assert db.get(DeviceSession, "test-session-token").last_used_at >= before


def test_deleting_a_user_removes_their_sessions(client, db, seeded_user):
    _add_session(db, seeded_user, "phone-token", "phone")
    db.delete(db.get(User, seeded_user.id))
    db.commit()
    assert db.execute(select(func.count()).select_from(DeviceSession)).scalar_one() == 0


def test_an_unknown_token_is_rejected(client):
    assert client.get("/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401
