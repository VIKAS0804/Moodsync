#!/usr/bin/env python3
"""Seed a demo user + scored library so the app is usable with no credentials.

The app's whole UX depends on there being a spread of scores across 1-100. This
creates that spread without needing Spotify or Apple Music credentials, which
means the slider UI isn't blocked on the analysis pipeline finishing.

By default it also fetches a real 30-second preview URL and album art for each
demo track from the credential-free iTunes Search API, so the demo library
actually *plays* and shows artwork. Without that the app can only display track
names -- the fake Spotify ids can't be deep-linked and there'd be no audio to
fall back to, which makes a perfectly working slider look broken.

    python scripts/seed_demo.py             # seed + fetch previews
    python scripts/seed_demo.py --offline   # skip the network, no audio
    python scripts/seed_demo.py --reset     # wipe and recreate
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402

from app.clients.itunes import ITunesClient  # noqa: E402
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


async def fetch_previews() -> dict[tuple[str, str], tuple[str | None, str | None]]:
    """Look up a real preview URL + artwork for each demo track.

    Returns {(title, artist): (preview_url, artwork_url)}. Failures are skipped
    rather than fatal -- a demo library with no audio still exercises the slider.
    """
    found: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    async with ITunesClient() as itunes:
        for title, artist, _ in DEMO_TRACKS:
            try:
                match = await itunes.find(title, artist)
            except Exception as exc:  # noqa: BLE001
                print(f"  preview lookup failed for {artist} - {title}: {exc}")
                continue
            if match and match.preview_url:
                found[(title, artist)] = (match.preview_url, match.artwork_url)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Delete existing demo data first")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip preview lookup; the demo library will have no playable audio",
    )
    args = parser.parse_args()

    previews: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    if not args.offline:
        print(f"Fetching previews for {len(DEMO_TRACKS)} tracks (iTunes, no credentials)...")
        previews = asyncio.run(fetch_previews())
        print(f"  got audio for {len(previews)}/{len(DEMO_TRACKS)}\n")

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
                # Not "premium" on purpose: these are synthetic Spotify ids, so
                # handoff to the Spotify app could never work. Reporting
                # preview_fallback sends the app straight to the audio it can
                # actually play instead of failing a deep link first.
                product="free",
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
            preview_url, artwork_url = previews.get((title, artist), (None, None))

            track.title = title
            track.artist = artist
            track.album = "MoodSync Demo Library"
            track.isrc = isrc
            track.duration_ms = 210_000
            track.artwork_url = artwork_url or track.artwork_url
            db.flush()

            mood = db.get(MoodScore, track.id) or MoodScore(track_id=track.id)
            mood.score = score
            mood.confidence = 1.0
            mood.model_version = "seed-demo"
            # Plausible features so /mood responses look like real analysed rows.
            # Interpolated across the anchor ranges the live model actually uses.
            frac = score / 100
            mood.feature_vector = {
                "tempo_bpm": round(60 + frac * 120, 2),
                "rms_mean": round(0.0595 + frac * 0.259, 4),
                "onset_rate_hz": round(0.401 + frac * 4.156, 3),
                "spectral_flatness": round(frac * 0.064, 5),
                "spectral_centroid_hz": round(509 + frac * 2591, 1),
                "percussive_ratio": round(0.0278 + frac * 0.440, 3),
                "zero_crossing_rate": round(0.0321 + frac * 0.1155, 4),
                "tonal_valence": 0.5,
                "seeded": True,
            }
            db.add(mood)

            # /mood joins this table for the preview URL, which is what the app
            # plays when Spotify handoff isn't available -- always the case for
            # these synthetic ids.
            catalog = db.get(AppleCatalogMap, isrc)
            if catalog is None:
                catalog = AppleCatalogMap(isrc=isrc, apple_catalog_id=f"demo-{index}")
                db.add(catalog)
            catalog.match_method = "fuzzy" if preview_url else "none"
            if preview_url:
                catalog.preview_url = preview_url

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
