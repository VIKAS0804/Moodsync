# Decisions

The open questions from the project brief, and how they were resolved during the
initial build. Each is reversible; the reasoning matters more than the choice.

---

## 1. Mood scoring: heuristic vs. trained model

**Decided: a calibrated heuristic for now, structured so a model can replace it.**

> **Correction (2026-08-20).** The original version of this entry justified the
> heuristic by claiming "there is no labelled dataset yet, so a model would be fitting
> noise." **That was wrong.** The historical Spotify audio-features data still exists
> in public datasets (~114k tracks with danceability/energy/valence on Kaggle and
> HuggingFace). The endpoint is dead; the data isn't. A model can be trained on those
> labels and applied to our own library, which is what a comparable project
> ([VibeScape](https://github.com/chandankeelara/VibeScape)) does by fine-tuning
> MERT-v1-95M with a regression head. The honest reason we still ship a heuristic is
> **scope**, not the absence of labels.

What makes this more than "some hardcoded numbers":

- Every feature is normalised against **anchors measured from real audio**
  (`FeatureAnchor` in `scoring.py`), set to the p5/p95 of that feature over a real
  preview set rather than guessed.
- Weights are declared in one table and renormalised when a feature is missing, so a
  failed extraction doesn't silently drag a track toward "calm".
- The model is **evaluated**, not asserted: `scripts/calibrate.py` reports Spearman
  rank correlation against hand-labelled energy tiers. v1 scored +0.53, v2 scores
  +0.82.
- `explain()` returns per-feature contributions, so any score can be interrogated.
- The **feature vector is persisted** alongside the score. Swapping in a trained model
  means replacing one function and running `scripts/rescore.py` — a pure database
  pass over cached vectors, no audio re-downloaded, no DSP re-run.

**Revisit when:** there's appetite to add a training pipeline. Fit a regression on a
public audio-features dataset, group-split by artist to avoid leakage, and compare
against the heuristic on a held-out split.

### What calibration changed (v1 → v2)

The v1 anchors were guessed and sanity-checked on *synthesised* tones, which turned out
to prove nothing: synthetic WAVs decode through a different path than real AAC previews
and look nothing like commercially mastered audio. Measured against real clips, every
v1 anchor sat outside the real range — `rms_mean` topped out at 0.18 when real masters
reach 0.32, so every loud track pinned to 1.0 and the model could not tell pop from
metal (tier means 59.7 / 55.5 / 55.3).

The largest single fix was **removing tempo**, which had the heaviest weight (0.30) and
turned out to be uncorrelated with energy on 30-second previews. Dropping it moved ρ
from +0.53 to +0.73; re-anchoring and adding `spectral_flatness` took it to +0.82.

Lesson worth keeping: a DSP model can only be validated against real audio. The
synthetic test suite stayed green through both of the bugs that made the pipeline
useless in practice.

---

## 2. Unmatched ISRCs, and where previews come from

**Decided: fuzzy fallback, recorded and confidence-discounted.** Configurable via
`UNMATCHED_TRACK_POLICY=fuzzy|exclude` and `PREVIEW_SOURCE=auto|apple_music|itunes`.

There are three tiers, best first:

1. **Apple Music Catalog by ISRC** — exact; you know you analysed the same recording
   the user owns. Needs an Apple Developer account and an ES256 developer token.
2. **Apple Music text search** — fuzzy, still needs the token.
3. **iTunes Search API** — fuzzy, and needs *no credentials whatsoever*.

Tier 3 was added after seeing it in VibeScape, and it matters more than it sounds:
without it, nobody can run this project's core pipeline without an Apple Developer
account. With it, `git clone && pip install` is enough to score real music.

One trap worth recording, since it's easy to get wrong in both directions:
`itunes.apple.com/lookup?isrc=...` **does not work**. ISRC is not a supported `lookup`
parameter (`id`, `upc`, `isbn`, `amgArtistId` are). It doesn't error — it returns
`resultCount: 0`, so code that "looks up by ISRC" there silently matches nothing and
falls through to text search forever. Verified against real ISRCs from MusicBrainz.
Exact ISRC matching needs the real Apple Music Catalog API (`filter[isrc]`). So every
`ITunesClient` match is marked `fuzzy`, never `isrc`.

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

**Decided: one session row per device; the server keeps the Spotify refresh token.**

> **Correction.** The first version stored the session as a single
> `users.session_token` column. That made sessions mutually exclusive: every login
> overwrote the column, so signing in on a phone silently signed out the laptop
> and a working token would go dead with no explanation. It caused four separate
> "Invalid session" incidents during testing before the cause was obvious.
> `sessions` is now its own table, which is what VibeScape's `schema.sql` does —
> noticed early and not adopted, which was the mistake.

Each device gets its own row, `/auth/logout` deletes only the calling device's
session, `/auth/sessions` lists them, and `/auth/sessions/revoke-others` exists
for when you want the blunt instrument.

### Why the server still holds a Spotify token

The obvious simplification is to let each device do its own Spotify OAuth and
talk to Spotify directly, with the backend serving nothing but mood scores. It
would remove pairing codes entirely. It doesn't work here, for one reason:
`/sync` and analysis keep running long after the request returns — an hour on a
large library — so a *refresh* token has to live somewhere that isn't a device
that may be asleep or offline.

So the split is deliberate:

| Needs a token | Which token | Why |
|---|---|---|
| Library sync, analysis | server's refresh token | outlives any request |
| Web Playback SDK, Spotify Connect | short-lived token from `/auth/spotify/playback-token` | the SDK authenticates itself in the browser |

The "Spotify tokens never leave the server" line in the original entry was
already false once the Web Playback SDK landed — the SDK cannot work without a
client-side token. Better to state the split than to claim an invariant the code
doesn't keep.

Device pairing is not part of this design; it's an Expo Go workaround. In Expo Go
the OAuth redirect is `exp://<lan-ip>:8081/--/callback`, which embeds the dev
machine's IP and changes with the network. A standalone build gets a stable
`moodsync://callback` and can do PKCE on-device with no pairing at all.

Still not a production identity system: no session expiry, and the Spotify tokens
sit in plaintext in `users`. Both are in the README's known limits.

---

## 7. Python version

**Decided: 3.11–3.12, pinned in `pyproject.toml`.**

The system Python here is 3.14, which the librosa/numba stack doesn't reliably support
yet. Pinning the range avoids a confusing wall of build errors for anyone cloning this.


---

## 8. Which Spotify sources `/sync` can actually read

**Decided: Liked Songs, playlists, top tracks and recently played.**

> **Correction (2026-08-20).** An earlier version of this entry claimed playlist
> contents were unreadable — "the same restriction family as `audio-features`,
> no scope changes that". **That was wrong, and it was wrong in the most
> expensive way: it told a user their own data was permanently inaccessible.**
>
> `/playlists/{id}/tracks` is *deprecated* and answers `403 Forbidden`. The
> replacement is `/playlists/{id}/items`, and on it Spotify also renamed each
> element's nested object from `track` to `item` (the outer array is still
> `items`). Switching endpoint turned a 403 into 249 tracks from the very
> playlist I had declared unreachable.
>
> What made this easy to get wrong: the 403 body is just
> `{"error": {"status": 403, "message": "Forbidden"}}` — no mention of
> deprecation — and it reproduced on a playlist the user *owned*, which pointed
> convincingly at policy. `/me/playlists` also reports `tracks.total` as null,
> which looked like corroborating evidence of redaction.
>
> The lesson: "403 on an endpoint that should work" is at least as likely to be
> a moved endpoint as a permissions problem, and the way to tell them apart is
> to try the current URL, not to reason about policy. Credit to
> [VibeScape](https://github.com/chandankeelara/VibeScape), which documents the
> rename in `ingest/spotify_library.py`.

Sources, all de-duplicated by track id:

| Source | Endpoint | Notes |
|---|---|---|
| Liked Songs | `/me/tracks` | nested key `track` |
| Playlists | `/playlists/{id}/items` | nested key `item`; auto-discovered via `/me/playlists` |
| Top tracks | `/me/top/tracks` | needs `user-top-read` |
| Recently played | `/me/player/recently-played` | needs `user-read-recently-played` |

Listening history matters more than it looks: an account with no Liked Songs and
no playlists has nothing else to offer, and history needs no curation.

Two 403 variants still worth telling apart, because they look identical until
you read the body:

* `"Insufficient client scope"` — the token predates a scope the app now asks
  for. Signing in again fixes it.
* `"Forbidden"` — either policy **or a deprecated endpoint**. Check the endpoint
  first.

`/sync` reports per-source counts so an empty result is diagnosable rather than
just zero.


---

## 9. Transient failures must not be cached as answers

**Decided: distinguish "the catalog says no" from "the catalog didn't answer".**

Recorded because getting this wrong destroyed real data. The iTunes client
swallowed every failure and returned `None`; `resolve_catalog_mapping` writes
`None` as a permanent negative so the same lookup isn't repeated. Run that
against a 1,777-track library and iTunes starts throttling: **1,657 tracks were
written off as having no preview**, most of which had matched fine minutes
earlier. The negative cache — the thing that makes the pipeline efficient — made
the damage permanent.

Three changes:

1. `ITunesTransient` is raised for anything that isn't a real answer (timeouts,
   403/429, 5xx, unparseable bodies). Only a `200` with no good match counts as
   "not in the catalog" and gets cached.
2. Requests are throttled to one per 3 seconds process-wide, with retry and
   jittered backoff. The API is unauthenticated and undocumented; being polite
   is the only option.
3. Analysis reports `deferred` for a transient failure, leaving the catalog
   untouched so a later run can succeed.

The general lesson: a cache of negative results is only safe if "negative" and
"failed" are different types. If a function returns the same value for both, the
cache will eventually launder an outage into permanent data.

## 10. Unscoreable tracks need a terminal state

**Decided: the negative catalog row is terminal, and `pending_tracks` excludes it.**

"Tracks with no score yet" and "tracks worth trying" are not the same set. A
track no catalog carries will never get a score, so selecting purely on a missing
`mood_scores` row leaves it pending forever. A loop draining that queue never
finishes: an ad-hoc backfill re-processed the same rows ~97,000 times in 90
seconds and wrote **244,000** `analysis_jobs` rows before being killed.

`scripts/backfill.py` also stops when a batch makes no progress at all, on the
principle that if nothing scored *and* nothing reached a terminal state, the
problem is upstream and retrying harder won't fix it.


---

## 11. Where training labels come from

**Decided: the listener's own corrections, not a public dataset.**

[VibeScape](https://github.com/chandankeelara/VibeScape) fine-tunes MERT-v1-95M
on a public Spotify audio-features dump, predicting danceability/energy/valence
straight from waveforms. With a GPU and that corpus it's the stronger approach,
and it doesn't depend on hand-picked features.

This project sits differently, in two ways that matter:

1. **The features are already persisted.** Every scored track carries its
   feature vector, so a model over those vectors trains in seconds on a CPU and
   needs no new audio. Re-scoring is already a pure database pass.
2. **A public dataset has the wrong distribution.** Calibration here was tuned
   on 29 English-language tracks, and the first real library tested was mostly
   Telugu and film scores. A dump of Western pop wouldn't close that gap;
   corrections from the person whose library it is do, by construction.

So `POST /mood/label` records a human score, and `scripts/train_model.py` fits
Ridge and gradient boosting on those pairs, split **grouped by artist** (the same
guard VibeScape applies via `group_col: artists`). If the heuristic still wins on
the held-out split, nothing is saved and the script says so — the bar for
replacing a model that scores rho +0.82 is beating it, not merely existing.

A correction also overrides the model for that listener immediately. Anything
less would be dishonest: a score you've told the app is wrong should stop
deciding where the slider finds that track, not just what the label reads.

Saved models are JSON, not pickles: reviewable in a diff, carrying the metrics
that justified them, and loading one can't execute code.

## 12. What was not taken from VibeScape

Their newest revision adds a YouTube player, backed by `build_cookies_file.py`
which lifts cookies out of a logged-in browser session so yt-dlp can fetch audio.

**Not ported.** It's a way around YouTube's terms rather than a licensed audio
source, and this project already has licensed full-track playback via the
Spotify Web Playback SDK plus preview audio that Apple publishes for exactly
this purpose. Adding a third path that depends on scraped session cookies would
trade a legitimate integration for a fragile one.
