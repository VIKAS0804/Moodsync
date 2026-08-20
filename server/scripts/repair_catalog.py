#!/usr/bin/env python3
"""Undo damage from a throttled analysis run.

The iTunes client used to return `None` for *any* failure, including HTTP 403 /
429. `resolve_catalog_mapping` records "no match" as a permanent negative, so a
rate-limited run wrote "this track has no preview" for a whole library -- 1,657
tracks in one real incident, most of which had matched fine minutes earlier. The
client now raises `ITunesTransient` for anything that isn't a real answer, but
rows already written have to be cleared for those tracks to be retried.

Also prunes redundant `analysis_jobs`. A driver loop with no terminal state left
244,000 rows, nearly all "skipped" duplicates of the same handful of tracks.

    python scripts/repair_catalog.py                   # report only
    python scripts/repair_catalog.py --apply           # clear negatives + prune jobs
    python scripts/repair_catalog.py --apply --keep-jobs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select, text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import AnalysisJob, AppleCatalogMap  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually change the database")
    parser.add_argument("--keep-jobs", action="store_true", help="Don't prune analysis_jobs")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        negatives = db.execute(
            select(func.count())
            .select_from(AppleCatalogMap)
            .where(AppleCatalogMap.match_method == "none")
        ).scalar_one()
        with_preview = db.execute(
            select(func.count())
            .select_from(AppleCatalogMap)
            .where(AppleCatalogMap.preview_url.is_not(None))
        ).scalar_one()
        jobs = db.execute(select(func.count()).select_from(AnalysisJob)).scalar_one()
        job_breakdown = db.execute(
            select(AnalysisJob.status, func.count()).group_by(AnalysisJob.status)
        ).all()

        print("before:")
        print(f"  catalog rows with a preview : {with_preview}")
        print(f"  catalog rows marked 'none'  : {negatives}")
        print(f"  analysis_jobs               : {jobs}")
        for status, count in sorted(job_breakdown):
            print(f"      {status:<10} {count}")

        if not args.apply:
            print("\nRe-run with --apply to clear the negatives and prune jobs.")
            return 0

        # Clearing the row (not just the flag) puts these tracks back in the
        # queue: pending_tracks excludes terminal rows, and absence is retryable.
        removed = db.execute(
            delete(AppleCatalogMap).where(AppleCatalogMap.match_method == "none")
        ).rowcount
        print(f"\ncleared {removed} negative catalog rows -- those tracks are retryable again")

        if not args.keep_jobs:
            # Keep the audit trail meaningful: one row per track per outcome is
            # useful, 244k duplicates are not.
            pruned = db.execute(
                delete(AnalysisJob).where(AnalysisJob.status.in_(["skipped", "running"]))
            ).rowcount
            print(f"pruned {pruned} redundant analysis_jobs rows")

        db.commit()

        if db.bind.dialect.name == "postgresql":
            # Reclaim the space those 244k rows were holding.
            db.commit()
            with db.bind.connect() as conn:
                conn.execution_options(isolation_level="AUTOCOMMIT")
                conn.execute(text("VACUUM ANALYZE analysis_jobs"))
            print("vacuumed analysis_jobs")

        remaining_negatives = db.execute(
            select(func.count())
            .select_from(AppleCatalogMap)
            .where(AppleCatalogMap.match_method == "none")
        ).scalar_one()
        remaining_jobs = db.execute(select(func.count()).select_from(AnalysisJob)).scalar_one()
        print("\nafter:")
        print(f"  catalog rows marked 'none'  : {remaining_negatives}")
        print(f"  analysis_jobs               : {remaining_jobs}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
