#!/usr/bin/env python3
"""Calibrate the scoring model against real preview audio.

The first version of `scoring.ANCHORS` was guessed, then sanity-checked on
synthesised tones. That was not good enough: synthetic WAVs decode through a
different path than real AAC previews, and their loudness/tempo statistics look
nothing like commercially mastered music. On real audio the guessed anchors
saturated (every loud master pinned `rms_mean` to 1.0) and the rankings came out
wrong.

This script fetches real previews via the credential-free iTunes Search API,
extracts features, and reports:

  1. the actual percentile distribution of every feature, which is what the
     anchors should be set from (p5 -> p95);
  2. Spearman rank correlation between the model's score and a coarse
     hand-labelled energy tier, which is the closest thing to ground truth
     available without a proper labelled set.

    python scripts/calibrate.py                 # fetch, extract, report
    python scripts/calibrate.py --no-fetch      # reuse the cached clips

Cached clips live in PREVIEW_CACHE_DIR, so re-runs are fast and offline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.clients.itunes import ITunesClient  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.pipeline import features as feat  # noqa: E402
from app.pipeline import scoring  # noqa: E402

# Coarse energy tiers, 1 = most calm, 5 = most intense. These are deliberately
# broad: the point is rank correlation, not pretending to know that a track is
# "a 73". Chosen to span genres and production eras so the distribution isn't
# dominated by one mastering style.
CALIBRATION_SET: list[tuple[str, str, int]] = [
    # tier 1 - ambient / solo classical
    ("Weightless", "Marconi Union", 1),
    ("Gymnopedie No. 1", "Erik Satie", 1),
    ("Clair de Lune", "Claude Debussy", 1),
    ("Spiegel im Spiegel", "Arvo Part", 1),
    ("An Ending Ascent", "Brian Eno", 1),
    ("Avril 14th", "Aphex Twin", 1),
    # tier 2 - slow / sparse songwriting
    ("Re: Stacks", "Bon Iver", 2),
    ("The Night We Met", "Lord Huron", 2),
    ("Skinny Love", "Bon Iver", 2),
    ("Nightswimming", "R.E.M.", 2),
    ("Teardrop", "Massive Attack", 2),
    ("Bloom", "The Paper Kites", 2),
    # tier 3 - mid-tempo pop
    ("Sunflower", "Post Malone Swae Lee", 3),
    ("Redbone", "Childish Gambino", 3),
    ("Electric Feel", "MGMT", 3),
    ("Dreams", "Fleetwood Mac", 3),
    ("Get Lucky", "Daft Punk", 3),
    ("Blinding Lights", "The Weeknd", 3),
    # tier 4 - driving rock / dance
    ("Mr. Brightside", "The Killers", 4),
    ("Don't Stop Me Now", "Queen", 4),
    ("Take On Me", "a-ha", 4),
    ("One More Time", "Daft Punk", 4),
    ("Titanium", "David Guetta Sia", 4),
    ("Seven Nation Army", "The White Stripes", 4),
    # tier 5 - metal / aggressive EDM
    ("Master of Puppets", "Metallica", 5),
    ("Scary Monsters and Nice Sprites", "Skrillex", 5),
    ("Killing in the Name", "Rage Against the Machine", 5),
    ("Chop Suey", "System of a Down", 5),
    ("Raining Blood", "Slayer", 5),
    ("Bangarang", "Skrillex Sirah", 5),
]

FEATURE_KEYS = [
    "tempo_bpm",
    "rms_mean",
    "onset_rate_hz",
    "spectral_centroid_hz",
    "percussive_ratio",
    "tonal_valence",
    "spectral_flatness",
    "zero_crossing_rate",
    "dynamic_range",
]


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q / 100.0 * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _spearman(a: list[float], b: list[float]) -> float:
    """Rank correlation, with average ranks for ties."""

    def ranks(xs: list[float]) -> list[float]:
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    if n < 2:
        return 0.0
    mean_a, mean_b = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((ra[i] - mean_a) * (rb[i] - mean_b) for i in range(n))
    den_a = sum((r - mean_a) ** 2 for r in ra) ** 0.5
    den_b = sum((r - mean_b) ** 2 for r in rb) ** 0.5
    return num / (den_a * den_b) if den_a and den_b else 0.0


async def gather_clips(fetch: bool) -> list[tuple[str, str, int, Path]]:
    settings = get_settings()
    cache_dir = Path(settings.preview_cache_dir) / "calibration"
    cache_dir.mkdir(parents=True, exist_ok=True)

    out: list[tuple[str, str, int, Path]] = []
    async with ITunesClient() as itunes:
        for title, artist, tier in CALIBRATION_SET:
            slug = "".join(c if c.isalnum() else "_" for c in f"{artist}-{title}")[:80]
            path = cache_dir / f"{slug}.m4a"

            if not path.exists():
                if not fetch:
                    print(f"  missing (skipped): {artist} - {title}")
                    continue
                match = await itunes.find(title, artist)
                if match is None or not match.preview_url:
                    print(f"  no preview: {artist} - {title}")
                    continue
                path.write_bytes(await itunes.download_preview(match.preview_url))
                print(f"  fetched  t{tier}  {match.artist} - {match.title}")
            out.append((title, artist, tier, path))
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-fetch", action="store_true", help="Use cached clips only")
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args()

    print("=== fetching previews (iTunes Search, no credentials) ===")
    clips = await gather_clips(fetch=not args.no_fetch)
    if len(clips) < 5:
        print("Not enough clips to calibrate.", file=sys.stderr)
        return 1

    print(f"\n=== extracting features from {len(clips)} clips ===")
    rows = []
    for title, artist, tier, path in clips:
        try:
            vector = await asyncio.to_thread(feat.extract_features, path)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED {artist} - {title}: {exc}")
            continue
        rows.append(
            {
                "title": title,
                "artist": artist,
                "tier": tier,
                "score": scoring.score_features(vector),
                **{k: vector.get(k) for k in FEATURE_KEYS},
            }
        )

    print(f"\n=== feature distribution over {len(rows)} real clips ===")
    print(f"{'feature':<24} {'p5':>10} {'p25':>10} {'p50':>10} {'p75':>10} {'p95':>10}")
    print("-" * 78)
    distribution = {}
    for key in FEATURE_KEYS:
        values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        if not values:
            continue
        pcts = {q: _percentile(values, q) for q in (5, 25, 50, 75, 95)}
        distribution[key] = pcts
        print(
            f"{key:<24} {pcts[5]:>10.4f} {pcts[25]:>10.4f} {pcts[50]:>10.4f} "
            f"{pcts[75]:>10.4f} {pcts[95]:>10.4f}"
        )

    print("\n=== per-tier mean score (should increase monotonically) ===")
    for tier in sorted({r["tier"] for r in rows}):
        tier_rows = [r for r in rows if r["tier"] == tier]
        mean_score = statistics.fmean(r["score"] for r in tier_rows)
        mean_tempo = statistics.fmean(r["tempo_bpm"] for r in tier_rows)
        print(
            f"  tier {tier}: n={len(tier_rows):>2}  mean score {mean_score:>5.1f}"
            f"   mean tempo {mean_tempo:>6.1f}"
        )

    rho = _spearman([float(r["tier"]) for r in rows], [float(r["score"]) for r in rows])
    print(f"\nSpearman rho (tier vs score): {rho:+.3f}")
    print("  1.0 = perfect ordering, 0 = no relationship")

    print("\n=== worst-ranked tracks (biggest tier/score disagreement) ===")
    scores = [float(r["score"]) for r in rows]
    lo, hi = min(scores), max(scores)
    span = (hi - lo) or 1.0
    for r in rows:
        r["expected"] = (r["tier"] - 1) / 4.0 * 100.0
        r["actual_norm"] = (r["score"] - lo) / span * 100.0
        r["err"] = abs(r["expected"] - r["actual_norm"])
    for r in sorted(rows, key=lambda x: -x["err"])[:8]:
        print(
            f"  err {r['err']:>5.1f}  tier {r['tier']}  score {r['score']:>3}  "
            f"tempo {r['tempo_bpm']:>6.1f}  rms {r['rms_mean']:.4f}  "
            f"{r['artist'][:20]} - {r['title'][:28]}"
        )

    if args.as_json:
        Path("calibration_report.json").write_text(
            json.dumps({"rows": rows, "distribution": distribution, "spearman": rho}, indent=2)
        )
        print("\nwrote calibration_report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
