# Decisions

The open questions from the project brief, and how they were resolved during the
initial build. Each is reversible; the reasoning matters more than the choice.

---

## 1. Mood scoring: heuristic vs. trained model

**Decided: a formalised heuristic, structured so a model can replace it.**

A trained regression is the stronger story, but only with labels. There is no labelled
dataset yet, so a model would be fitting noise and it would be impossible to tell a
scoring bug from a data problem.

What makes this more than "some hardcoded numbers":

- Every feature is normalised against **documented anchors** (`FeatureAnchor` in
  `scoring.py`) rather than magic constants scattered through the code.
- Weights are declared in one table and renormalised when a feature is missing, so a
  failed extraction doesn't silently drag a track toward "calm".
- `explain()` returns per-feature contributions, so any score can be interrogated.
- The **feature vector is persisted** alongside the score. Swapping in a trained model
  means replacing one function and running `scripts/rescore.py` — a pure database
  pass over cached vectors, no audio re-downloaded, no DSP re-run.

**Revisit when:** ~100 self-labelled tracks exist. Then fit a regression on the stored
vectors and compare against the heuristic on a held-out split.

---

## 2. Unmatched ISRCs

**Decided: fuzzy fallback, recorded and confidence-discounted.** Configurable via
`UNMATCHED_TRACK_POLICY=fuzzy|exclude`.

Excluding unmatched tracks silently shrinks the pool, and the user has no way to
understand why a song they own never plays. Instead:

1. Exact `filter[isrc]` lookup against Apple's catalog.
2. On a miss, search by title + artist, requiring an artist-name match and a track
   that actually has preview audio.
3. Store **which method matched** (`apple_catalog_map.match_method`).
4. Discount confidence for fuzzy matches (1.0 → 0.6), which lowers their weight during
   selection.

So a fuzzy match can still be picked, but loses to an exact match at the same distance.
Misses are **negatively cached** so Apple isn't re-queried for a track it doesn't carry.

The risk is real: a fuzzy match may be a live version, a remaster, or a cover, whose
actual energy differs from the track the user owns. The confidence discount is a hedge,
not a fix.

---

## 3. Repo structure

**Decided: monorepo** — `/server` and `/app`.

One GitHub URL for a resume, one place where the API schema and its TypeScript client
change together. `src/api/types.ts` mirrors `schemas.py` by hand; a monorepo makes
drifting between them a visible diff rather than a cross-repo mystery.

---

## 4. Selection: nearest track, or something softer?

**Decided: weighted sampling from the k nearest inside a widening window.**

Strictly-nearest is deterministic — land on 72 twice, get the same song twice, which
users read as a broken app. Pure randomness inside a band ignores that a track 1 point
away is a better answer than one 15 away.

So: widen the window (±5, 12, 25, 50, 100) until candidates exist, take the 8 nearest,
sample weighted by `1/(1+distance) × confidence`. Recently played ids are passed as
`exclude` so nudging the slider doesn't loop.

---

## 5. Playback

**Decided: three tiers, and always tell the user which one is playing.**

Streaming Spotify audio in-app is a licensing restriction, not a technical gap. The
App Remote SDK is the intended path but needs a native module and a custom dev client
— a hard requirement to impose on anyone who just wants to run the slider. So it's
resolved at runtime via `require`, and absent that, a `spotify:track:` deep link plays
the full song today with no native code. The Apple preview is the floor, and it's free:
that clip was already downloaded to score the track.

The fallback is surfaced in the UI ("30s preview", plus the reason) rather than
silently degrading, because a user who can't tell why they're hearing 30 seconds
assumes the app is broken.

---

## 6. Auth

**Decided: opaque server-issued bearer token; Spotify tokens never leave the server.**

The device runs PKCE and ships the code to the backend, which does the exchange, stores
the Spotify token pair, and returns a token of its own. The refresh dance stays
server-side.

This is deliberately not a production identity system — no expiry, no rotation, and the
Spotify tokens sit in plaintext in `users`. Both are noted in the README as things to
fix before real users.

---

## 7. Python version

**Decided: 3.11–3.12, pinned in `pyproject.toml`.**

The system Python here is 3.14, which the librosa/numba stack doesn't reliably support
yet. Pinning the range avoids a confusing wall of build errors for anyone cloning this.
