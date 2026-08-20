#!/usr/bin/env python3
"""Re-run the scoring model over cached feature vectors.

The point of persisting `mood_scores.feature_vector`: changing weights in
`app/pipeline/scoring.py` is a pure database pass. No audio is re-downloaded and
no DSP is re-run, so tuning the model over a few hundred tracks takes seconds.

    python scripts/rescore.py --dry-run    # show what would change
    python scripts/rescore.py              # write the new scores
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import MoodScore, Track  # noqa: E402
from app.pipeline import scoring  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-seeded", action="store_true", help="Also rescore demo rows")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = db.execute(
            select(MoodScore, Track).join(Track, Track.id == MoodScore.track_id)
        ).all()
        changed = 0
        total_delta = 0

        for mood, track in rows:
            vector = mood.feature_vector or {}
            if vector.get("seeded") and not args.include_seeded:
                continue
            new_score = scoring.score_features(vector)
            if new_score == mood.score:
                continue
            delta = new_score - mood.score
            total_delta += abs(delta)
            changed += 1
            print(
                f"  {mood.score:>3} -> {new_score:>3} ({delta:+d})  "
                f"{track.artist} - {track.title}"
            )
            if not args.dry_run:
                mood.score = new_score
                mood.model_version = scoring.MODEL_VERSION
                mood.computed_at = datetime.now(UTC)

        if not args.dry_run:
            db.commit()

        verb = "would change" if args.dry_run else "changed"
        print(f"\n{changed}/{len(rows)} scores {verb} (model {scoring.MODEL_VERSION})")
        if changed:
            print(f"mean absolute shift: {total_delta / changed:.1f} points")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
