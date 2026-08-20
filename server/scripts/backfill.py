#!/usr/bin/env python3
"""Analyse a library in batches, outside the request path and outside uvicorn.

`/sync` queues analysis as a FastAPI background task, which dies with the
process -- so a reload mid-run silently abandons the rest of the library. This
runs detached and is resumable: it only ever picks up tracks that still have a
chance of being scored.

It also has the guard the ad-hoc version of this script lacked. That one looped
on `pending_tracks` until empty, but tracks with no preview anywhere never get a
score and so never leave the queue: it re-processed the same rows ~97,000 times
in 90 seconds and wrote 244,000 job records. Two things prevent that here --
`pending_tracks` now excludes terminal rows, and this stops as soon as a batch
makes no progress.

    python scripts/backfill.py --status         # what's left, no work
    python scripts/backfill.py                  # run it
    python scripts/backfill.py --limit 200      # cap the work
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app import selection  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import AppleCatalogMap, MoodScore, User, UserTrack  # noqa: E402
from app.pipeline.analyze import analyze_many, pending_tracks  # noqa: E402

BATCH = 25


def _counts(db, user_id: str) -> dict[str, int]:
    total = db.execute(
        select(func.count()).select_from(UserTrack).where(UserTrack.user_id == user_id)
    ).scalar_one()
    scored = db.execute(
        select(func.count())
        .select_from(MoodScore)
        .join(UserTrack, UserTrack.track_id == MoodScore.track_id)
        .where(UserTrack.user_id == user_id)
    ).scalar_one()
    no_preview = db.execute(
        select(func.count())
        .select_from(AppleCatalogMap)
        .where(AppleCatalogMap.match_method == "none")
    ).scalar_one()
    return {"total": int(total), "scored": int(scored), "no_preview": int(no_preview)}


async def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Report and exit")
    parser.add_argument("--limit", type=int, default=0, help="Max tracks this run (0 = all)")
    parser.add_argument("--user", help="Spotify user id (defaults to the first non-demo user)")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.analysis_available:
        print("No preview source configured.", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        query = select(User)
        if args.user:
            query = query.where(User.spotify_user_id == args.user)
        else:
            query = query.where(User.spotify_user_id != "moodsync-demo")
        user = db.execute(query).scalars().first()
        if user is None:
            print("No such user.", file=sys.stderr)
            return 1

        counts = _counts(db, user.id)
        queued = len(pending_tracks(db, limit=100_000, user_id=user.id))
        print(
            f"{user.display_name}: {counts['total']} tracks, {counts['scored']} scored, "
            f"{counts['no_preview']} with no preview anywhere, {queued} still to try"
        )
        if args.status:
            return 0
        if queued == 0:
            print("Nothing to do.")
            return 0

        started = time.time()
        processed = 0
        scored_total = 0

        while True:
            if args.limit and processed >= args.limit:
                print(f"Reached --limit {args.limit}.")
                break

            size = BATCH if not args.limit else min(BATCH, args.limit - processed)
            ids = [t.id for t in pending_tracks(db, limit=size, user_id=user.id)]
            if not ids:
                print("Queue empty.")
                break

            results = await analyze_many(ids, settings)
            processed += len(results)

            tally: dict[str, int] = {}
            for r in results:
                tally[r.status] = tally.get(r.status, 0) + 1
            scored_total += tally.get("scored", 0)

            elapsed = time.time() - started
            rate = processed / elapsed if elapsed else 0
            print(
                f"[{elapsed:6.0f}s] {processed:>5} done | "
                + " ".join(f"{k}={v}" for k, v in sorted(tally.items()))
                + f" | {rate:.2f} tracks/s",
                flush=True,
            )

            # Guards. Progress means scored or terminal; anything else and
            # retrying harder won't help.
            if tally.get("scored", 0) == 0 and tally.get("skipped", 0) == 0:
                print(
                    "No progress in this batch — stopping rather than spinning. "
                    "Deferred tracks stay queued; try again later.",
                    file=sys.stderr,
                )
                break
            # A batch that mostly *errors* is a bug or an outage, not work to
            # grind through. One scored track shouldn't license 24 failures.
            failed = tally.get("failed", 0)
            if failed > len(results) / 2:
                print(
                    f"{failed}/{len(results)} failed in one batch — stopping. "
                    "Check the analysis_jobs error column.",
                    file=sys.stderr,
                )
                break

            db.expire_all()

        selection.refresh_score_stats(db, user)
        final = _counts(db, user.id)
        print(
            f"\nDone in {(time.time() - started) / 60:.1f} min. "
            f"Scored {scored_total} this run; {final['scored']}/{final['total']} total "
            f"({final['scored'] / final['total']:.0%} coverage)."
        )
        return 0
    finally:
        db.close()


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
