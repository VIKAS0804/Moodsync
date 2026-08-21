#!/usr/bin/env python3
"""Fit a scoring model on human corrections, and compare it to the heuristic.

Why this shape rather than VibeScape's
-------------------------------------
VibeScape fine-tunes MERT-v1-95M on a public audio-features dataset: audio in,
danceability/energy/valence out. That's the stronger approach given a GPU and a
labelled corpus, and it learns straight from waveforms rather than from features
somebody chose by hand.

This project is in a different position. The DSP features are already extracted
and **persisted** for every scored track, so a model over those vectors trains in
seconds on a CPU with no new downloads. And the labels come from the listener
correcting their own library, which means they cover the genres that library
actually contains -- a public dataset skews Western pop, which is precisely the
gap flagged when calibration was tuned on 29 English-language tracks.

Ridge and gradient boosting, compared against the heuristic on the same
grouped split. Nothing here is deep learning; with a few hundred labels it
shouldn't be.

    python scripts/train_model.py --report          # what data exists
    python scripts/train_model.py                   # train + evaluate
    python scripts/train_model.py --save            # write the winner to disk
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import MoodLabel, MoodScore, Track  # noqa: E402
from app.pipeline import scoring  # noqa: E402

MODEL_PATH = Path("app/pipeline/models/learned-v1.json")
# Below this, a fit is memorising rather than learning. The heuristic already
# scores rho +0.82; beating it needs more than a handful of corrections.
MIN_LABELS = 40


def load_dataset(db):
    """(feature dict, human score, artist) for every corrected track."""
    rows = db.execute(
        select(MoodLabel, MoodScore, Track)
        .join(MoodScore, MoodScore.track_id == MoodLabel.track_id)
        .join(Track, Track.id == MoodLabel.track_id)
    ).all()

    samples = []
    for label, mood, track in rows:
        vector = mood.feature_vector or {}
        if vector.get("seeded"):
            continue  # synthetic demo rows teach nothing
        samples.append(
            {
                "features": vector,
                "y": float(label.score),
                "heuristic": float(mood.score),
                # Grouping key: the same artist in train and test inflates
                # everything, which is why VibeScape groups on `artists` too.
                "group": (track.artist or "").split(",")[0].strip().lower(),
                "title": f"{track.artist} - {track.title}",
            }
        )
    return samples


def matrix(samples):
    """Feature matrix in a fixed, recorded order."""
    names = [a.key for a in scoring.ANCHORS]
    X = [[float(s["features"].get(n) or 0.0) for n in names] for s in samples]
    y = [s["y"] for s in samples]
    return names, X, y


def grouped_split(samples, holdout=0.25, seed=42):
    """Split by artist so no artist appears on both sides."""
    import random

    groups = sorted({s["group"] for s in samples})
    rng = random.Random(seed)
    rng.shuffle(groups)
    n_test = max(1, int(len(groups) * holdout))
    test_groups = set(groups[:n_test])
    train = [i for i, s in enumerate(samples) if s["group"] not in test_groups]
    test = [i for i, s in enumerate(samples) if s["group"] in test_groups]
    return train, test


def mae(pred, actual):
    return statistics.fmean(abs(p - a) for p, a in zip(pred, actual, strict=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="Describe the labels and exit")
    parser.add_argument("--save", action="store_true", help="Persist the winning model")
    parser.add_argument("--min-labels", type=int, default=MIN_LABELS)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        samples = load_dataset(db)
        print(f"{len(samples)} labelled tracks from {len({s['group'] for s in samples})} artists")

        if samples:
            deltas = [s["y"] - s["heuristic"] for s in samples]
            print(
                f"  human vs heuristic: mean delta {statistics.fmean(deltas):+.1f}, "
                f"MAE {statistics.fmean(abs(d) for d in deltas):.1f}"
            )
            worst = sorted(samples, key=lambda s: -abs(s["y"] - s["heuristic"]))[:5]
            print("  biggest disagreements:")
            for s in worst:
                print(f"    model {s['heuristic']:>5.0f} -> you {s['y']:>3.0f}  {s['title'][:52]}")

        if args.report:
            return 0

        if len(samples) < args.min_labels:
            print(
                f"\nNeed at least {args.min_labels} labels to train something honest "
                f"({len(samples)} so far). Correct more tracks in the app -- the "
                "biggest disagreements above are the most informative ones.",
                file=sys.stderr,
            )
            return 1

        names, X, y = matrix(samples)
        train_idx, test_idx = grouped_split(samples)
        Xtr = [X[i] for i in train_idx]
        ytr = [y[i] for i in train_idx]
        Xte = [X[i] for i in test_idx]
        yte = [y[i] for i in test_idx]
        print(f"\ngrouped split: {len(Xtr)} train / {len(Xte)} test (disjoint artists)")

        from sklearn.ensemble import GradientBoostingRegressor
        from sklearn.linear_model import RidgeCV
        from sklearn.preprocessing import StandardScaler

        scaler = StandardScaler().fit(Xtr)
        Xtr_s, Xte_s = scaler.transform(Xtr), scaler.transform(Xte)

        baseline = mae([samples[i]["heuristic"] for i in test_idx], yte)
        print(f"\n  {'model':<22} {'test MAE':>9}")
        print("  " + "-" * 33)
        print(f"  {'heuristic (current)':<22} {baseline:>9.2f}")

        results = {}
        ridge = RidgeCV(alphas=[0.1, 1.0, 10.0, 100.0]).fit(Xtr_s, ytr)
        results["ridge"] = (mae(ridge.predict(Xte_s), yte), ridge, True)
        print(f"  {'ridge':<22} {results['ridge'][0]:>9.2f}")

        gbm = GradientBoostingRegressor(random_state=42, n_estimators=200, max_depth=2)
        gbm.fit(Xtr, ytr)
        results["gbm"] = (mae(gbm.predict(Xte), yte), gbm, False)
        print(f"  {'gradient boosting':<22} {results['gbm'][0]:>9.2f}")

        best_name = min(results, key=lambda k: results[k][0])
        best_mae, best_model, scaled = results[best_name]

        if best_mae >= baseline:
            print(
                f"\nThe heuristic still wins ({baseline:.2f} vs {best_mae:.2f}). "
                "Keeping it -- more labels would help more than a different model."
            )
            return 0

        print(f"\n{best_name} beats the heuristic: {best_mae:.2f} vs {baseline:.2f} MAE")

        if not args.save:
            print("Re-run with --save to persist it.")
            return 0

        if best_name != "ridge":
            print(
                "Only the linear model is serialisable to JSON right now; "
                "re-run when ridge wins, or add pickle support deliberately.",
                file=sys.stderr,
            )
            return 1

        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        MODEL_PATH.write_text(
            json.dumps(
                {
                    "model_version": "learned-v1",
                    "kind": "ridge",
                    "feature_names": names,
                    "mean": scaler.mean_.tolist(),
                    "scale": scaler.scale_.tolist(),
                    "coef": best_model.coef_.tolist(),
                    "intercept": float(best_model.intercept_),
                    "trained_on": len(Xtr),
                    "test_mae": round(best_mae, 3),
                    "heuristic_mae": round(baseline, 3),
                },
                indent=2,
            )
        )
        print(f"wrote {MODEL_PATH}")
        print("Apply it with: python scripts/rescore.py --dry-run")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
