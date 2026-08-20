#!/usr/bin/env python3
"""Seed a demo user + scored library so the mobile app is developable offline.

The app's whole UX depends on there being a spread of scores across 1-100. This
creates that spread without needing Spotify or Apple Music credentials, which
means phase 3 (the slider UI) isn't blocked on phase 1 finishing.

    python scripts/seed_demo.py           # prints the session token to use
    python scripts/seed_demo.py --reset   # wipe and recreate
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import AppleCatalogMap, MoodScore, Track, User, UserTrack  # noqa: E402
from app.pipeline import scoring  # noqa: E402

DEMO_USER_ID = "moodsync-demo"
DEMO_SESSION_TOKEN = "demo-session-token"

# (title, artist, mood score) -- hand-placed to cover the full slider range.
DEMO_TRACKS = [
    ("Weightless", "Marconi Union", 4),
    ("Gymnopedie No. 1", "Erik Satie", 8),
    ("Nightswimming", "R.E.M.", 15),
    ("Re: Stacks", "Bon Iver", 18),
    ("Teardrop", "Massive Attack", 26),
    ("Redbone", "Childish Gambino", 33),
    ("Lost in Japan", "Shawn Mendes", 41),
    ("Sunflower", "Post Malone", 47),
    ("Electric Feel", "MGMT", 54),
    ("Take On Me", "a-ha", 61),
    ("Blinding Lights", "The Weeknd", 68),
    ("Mr. Brightside", "The Killers", 74),
    ("Don't Stop Me Now", "Queen", 79),
    ("Uptown Funk", "Mark Ronson", 83),
    ("Seven Nation Army", "The White Stripes", 87),
    ("One More Time", "Daft Punk", 90),
    ("Titanium", "David Guetta", 93),
    ("Bangarang", "Skrillex", 96),
    ("Killing In The Name", "Rage Against The Machine", 98),
    ("Master of Puppets", "Metallica", 100),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Delete existing demo data first")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        user = db.execute(
            select(User).where(User.spotify_user_id == DEMO_USER_ID)
        ).scalar_one_or_none()

        if args.reset and user is not None:
            db.execute(delete(UserTrack).where(UserTrack.user_id == user.id))
            db.delete(user)
            db.commit()
            user = None

        if user is None:
            user = User(
                spotify_user_id=DEMO_USER_ID,
                display_name="MoodSync Demo",
                product="premium",
                session_token=DEMO_SESSION_TOKEN,
            )
            db.add(user)
            db.commit()
        else:
            user.session_token = user.session_token or secrets.token_urlsafe(32)
            db.commit()

        created = 0
        for index, (title, artist, score) in enumerate(DEMO_TRACKS):
            spotify_id = f"demo{index:04d}"
            isrc = f"DEMO0000{index:04d}"
            track = db.execute(
                select(Track).where(Track.spotify_track_id == spotify_id)
            ).scalar_one_or_none()
            if track is None:
                track = Track(spotify_track_id=spotify_id)
                db.add(track)
                created += 1
            track.title = title
            track.artist = artist
            track.album = "MoodSync Demo Library"
            track.isrc = isrc
            track.duration_ms = 210_000
            db.flush()

            mood = db.get(MoodScore, track.id) or MoodScore(track_id=track.id)
            mood.score = score
            mood.confidence = 1.0
            mood.model_version = "seed-demo"
            # Plausible features so /mood responses look like real analysed rows.
            mood.feature_vector = {
                "tempo_bpm": round(60 + (score / 100) * 120, 2),
                "rms_mean": round(0.01 + (score / 100) * 0.17, 4),
                "onset_rate_hz": round(0.5 + (score / 100) * 6.5, 3),
                "spectral_centroid_hz": round(800 + (score / 100) * 3700, 1),
                "percussive_ratio": round(0.15 + (score / 100) * 0.55, 3),
                "tonal_valence": 0.5,
                "seeded": True,
            }
            db.add(mood)

            if db.get(AppleCatalogMap, isrc) is None:
                db.add(
                    AppleCatalogMap(
                        isrc=isrc,
                        apple_catalog_id=f"demo-{index}",
                        preview_url=None,
                        match_method="isrc",
                    )
                )

            link = db.execute(
                select(UserTrack).where(
                    UserTrack.user_id == user.id, UserTrack.track_id == track.id
                )
            ).scalar_one_or_none()
            if link is None:
                db.add(UserTrack(user_id=user.id, track_id=track.id, source="demo"))

        db.commit()

        print(f"Seeded {len(DEMO_TRACKS)} tracks ({created} new) for user {user.display_name}")
        print(f"\n  session token : {user.session_token}")
        print(f"  user id       : {user.id}")
        print("\nTry it:")
        print(f'  curl -H "Authorization: Bearer {user.session_token}" localhost:8000/mood/85')
        print(
            "\nMood bands: "
            + ", ".join(
                f"{s}={scoring.describe_mood(s)}" for s in (10, 30, 50, 70, 95)
            )
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
