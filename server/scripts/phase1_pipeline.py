#!/usr/bin/env python3
"""Phase 1 proof: ISRC -> Apple Music preview -> DSP features -> 1-100 mood score.

Runs the whole analysis path outside FastAPI and outside Postgres, so the core
risk of the project can be validated before any of the app exists.

    # score a few tracks by ISRC
    python scripts/phase1_pipeline.py --isrc USUM71703861 GBAHS1600463

    # or by title/artist, when you don't have ISRCs handy
    python scripts/phase1_pipeline.py --search "Weightless|Marconi Union" "Bangarang|Skrillex"

    # or pull straight from your own Spotify library (needs a user token)
    python scripts/phase1_pipeline.py --spotify-token "$SPOTIFY_TOKEN" --limit 20

Add --explain to see each feature's contribution to the score.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.clients.apple_music import AppleMatch, AppleMusicClient  # noqa: E402
from app.clients.itunes import ITunesClient  # noqa: E402
from app.clients.spotify import SpotifyClient  # noqa: E402
from app.clients.storage import PreviewCache  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.pipeline import features as feat  # noqa: E402
from app.pipeline import scoring  # noqa: E402


async def gather_targets(args: argparse.Namespace) -> list[tuple[str, str, str | None]]:
    """Return (title, artist, isrc) triples from whichever input mode was used."""
    targets: list[tuple[str, str, str | None]] = []

    for isrc in args.isrc:
        targets.append((isrc, "", isrc))

    for pair in args.search:
        title, _, artist = pair.partition("|")
        targets.append((title.strip(), artist.strip(), None))

    if args.spotify_token:
        async with SpotifyClient(access_token=args.spotify_token) as spotify:
            saved = await spotify.get_saved_tracks(max_tracks=args.limit)
        for track in saved:
            targets.append((track.title, track.artist, track.isrc))

    return targets


async def resolve(
    apple: AppleMusicClient | None,
    itunes: ITunesClient | None,
    title: str,
    artist: str,
    isrc: str | None,
) -> AppleMatch | None:
    """Same tier order as the real pipeline: exact ISRC, then text search."""
    if apple is not None:
        if isrc:
            match = await apple.find_by_isrc(isrc)
            if match:
                return match
        if title:
            match = await apple.find_by_search(title, artist)
            if match:
                return match

    if itunes is not None and title:
        found = await itunes.find(title, artist)
        if found is not None:
            return AppleMatch(
                apple_catalog_id=found.apple_catalog_id,
                preview_url=found.preview_url,
                title=found.title,
                artist=found.artist,
                isrc=None,
                match_method="fuzzy",
            )
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--isrc", nargs="*", default=[], help="ISRC codes to analyse")
    parser.add_argument("--search", nargs="*", default=[], help='"Title|Artist" pairs')
    parser.add_argument("--spotify-token", help="Spotify user access token, to read your library")
    parser.add_argument("--limit", type=int, default=20, help="Max tracks from Spotify")
    parser.add_argument("--explain", action="store_true", help="Show per-feature contributions")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.analysis_available:
        print(
            "No preview source available. Either configure Apple Music\n"
            "(APPLE_TEAM_ID / APPLE_KEY_ID / APPLE_PRIVATE_KEY_PATH) or set\n"
            "PREVIEW_SOURCE=auto to use the credential-free iTunes fallback.",
            file=sys.stderr,
        )
        return 2

    source = "Apple Music catalog" if settings.use_apple_music else "iTunes Search"
    print(f"preview source: {source}", file=sys.stderr)
    if not settings.use_apple_music:
        print(
            "  note: iTunes Search has no ISRC lookup, so matches are text-based\n"
            "  and could be a live version or cover. Configure Apple Music for\n"
            "  exact ISRC matching.",
            file=sys.stderr,
        )

    targets = await gather_targets(args)
    if not targets:
        parser.print_help()
        return 1

    cache = PreviewCache(settings)
    rows = []
    matched = 0
    started = time.perf_counter()

    async with AsyncExitStack() as stack:
        apple = (
            await stack.enter_async_context(AppleMusicClient(settings))
            if settings.use_apple_music
            else None
        )
        itunes = (
            await stack.enter_async_context(ITunesClient(storefront=settings.apple_storefront))
            if settings.use_itunes_fallback
            else None
        )
        for title, artist, isrc in targets:
            label = f"{title} - {artist}" if artist else title
            match = await resolve(apple, itunes, title, artist, isrc)
            if match is None or not match.preview_url:
                print(f"  ..  {label}: no preview found", file=sys.stderr)
                rows.append({"label": label, "status": "no_preview"})
                continue

            matched += 1
            key = match.isrc or isrc or match.apple_catalog_id
            audio = cache.get(key)
            if audio is None:
                downloader = apple or itunes
                audio = await downloader.download_preview(match.preview_url)
                cache.put(key, audio)

            t0 = time.perf_counter()
            vector = await asyncio.to_thread(feat.extract_features, cache.local_path(key, audio))
            elapsed_ms = (time.perf_counter() - t0) * 1000

            score = scoring.score_features(vector)
            row = {
                "label": f"{match.title} - {match.artist}",
                "status": "scored",
                "isrc": match.isrc,
                "match_method": match.match_method,
                "score": score,
                "mood": scoring.describe_mood(score),
                "tempo_bpm": vector["tempo_bpm"],
                "rms_mean": vector["rms_mean"],
                "onset_rate_hz": vector["onset_rate_hz"],
                "analysis_ms": round(elapsed_ms, 1),
            }
            if args.explain:
                row["explanation"] = scoring.explain(vector)
            rows.append(row)

            print(
                f"  {score:>3}  {scoring.describe_mood(score):<10} "
                f"{row['tempo_bpm']:>6.1f} BPM  {elapsed_ms:>6.0f} ms  "
                f"[{match.match_method}]  {row['label']}"
            )

    total_s = time.perf_counter() - started
    scored = [r for r in rows if r.get("status") == "scored"]

    if args.as_json:
        print(json.dumps(rows, indent=2))
    else:
        print("\n--- summary ---")
        print(f"  tracks attempted : {len(targets)}")
        print(f"  catalog matched  : {matched} ({matched / len(targets):.0%})")
        print(f"  scored           : {len(scored)}")
        if scored:
            times = [r["analysis_ms"] for r in scored]
            print(f"  mean analysis    : {sum(times) / len(times):.0f} ms/track")
            print(f"  score range      : {min(r['score'] for r in scored)}"
                  f"-{max(r['score'] for r in scored)}")
        print(f"  wall clock       : {total_s:.1f}s")

    return 0 if scored else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
