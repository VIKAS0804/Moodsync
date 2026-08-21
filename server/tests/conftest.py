"""Test bootstrap.

DATABASE_URL is set before anything under `app.` is imported, because
`app.db` builds its engine at import time from the cached settings.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "moodsync_test.sqlite3"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test-client-id")
os.environ.setdefault("PREVIEW_CACHE_DIR", str(Path(tempfile.gettempdir()) / "moodsync_previews"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AppleCatalogMap, MoodScore, Track, User, UserTrack  # noqa: E402
from app.models import Session as DeviceSession  # noqa: E402

SESSION_TOKEN = "test-session-token"


@pytest.fixture(autouse=True)
def fresh_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {SESSION_TOKEN}"}


@pytest.fixture
def seeded_user(db):
    """A premium user with tracks spread evenly across the 1-100 range."""
    user = User(
        spotify_user_id="test-user",
        display_name="Test User",
        product="premium",
    )
    db.add(user)
    db.flush()
    db.add(DeviceSession(token=SESSION_TOKEN, user_id=user.id, device_label="tests"))
    db.commit()

    for index, score in enumerate(range(5, 100, 10)):  # 5, 15, ... 95
        isrc = f"TEST{index:08d}"
        track = Track(
            spotify_track_id=f"spotify{index}",
            isrc=isrc,
            title=f"Track {index}",
            artist=f"Artist {index}",
            album="Test Album",
            duration_ms=200_000,
        )
        db.add(track)
        db.flush()
        db.add(
            MoodScore(
                track_id=track.id,
                score=score,
                confidence=1.0,
                model_version="test",
                feature_vector={"tempo_bpm": 60 + score, "rms_mean": 0.05},
            )
        )
        db.add(
            AppleCatalogMap(
                isrc=isrc,
                apple_catalog_id=f"apple{index}",
                preview_url=f"https://example.test/{isrc}.m4a",
                match_method="isrc",
            )
        )
        db.add(UserTrack(user_id=user.id, track_id=track.id))

    db.commit()
    return user
