# MoodSync

A mood-adaptive music player. One slider, 1–100, calm to hyper. Move it and it plays a
song from **your own** Spotify library that matches — no searching, no typing, no
scrolling through playlists. Built for driving and the gym, where searching a phone is
impractical or unsafe.

Mood is continuous, not a playlist. A slider is a more honest interface for "how do I
want to feel right now" than a folder of pre-made playlists.

---

## Why this isn't a Spotify wrapper

Spotify used to expose an `audio-features` endpoint (energy, valence, tempo,
danceability) that would have made mood-scoring a single API call.

**On 2024-11-27, Spotify restricted `audio-features`, `audio-analysis` and
`recommendations` for all new third-party apps.** Only apps with a quota extension
already pending kept access. There's no waitlist and no sign it's coming back.

So the core of this project is a **mood-scoring pipeline built from scratch**:

| Problem | How MoodSync solves it |
|---|---|
| No audio features from Spotify | Apple Music's Catalog API still serves 30-second preview clips with only a *developer* token — no user subscription needed |
| Linking the two catalogs | Spotify still exposes **ISRC** on the standard track object; Apple's catalog is queryable by `filter[isrc]` |
| Deriving mood | Local DSP feature extraction (librosa) → a documented weighted model → a 1–100 score |
| Cost of recomputing | Scores **and their feature vectors** are cached in PostgreSQL |
| Can't stream Spotify audio in-app | Licensing, not a technical gap. Playback is handed off to the Spotify app, with a preview fallback |

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  MOBILE (React Native · Expo Router · NativeWind · TS)       │
│  Mood slider · Spotify OAuth (PKCE) · playback handoff       │
└────────────────────────────┬─────────────────────────────────┘
                             │ REST (Axios + TanStack Query)
┌────────────────────────────▼─────────────────────────────────┐
│  BACKEND (FastAPI · SQLAlchemy)                              │
│  GET /mood/{score}  ·  POST /sync  ·  /auth/spotify/*        │
└──────────┬──────────────────────────────┬────────────────────┘
           │                              │
┌──────────▼─────────────┐   ┌────────────▼───────────────────┐
│  MOOD PIPELINE          │   │  CATALOG MATCHING              │
│  Apple preview → librosa│   │  Spotify ISRC → Apple catalog  │
│  features → 1–100 score │   │  id + preview URL (cached)     │
└──────────┬─────────────┘   └────────────┬───────────────────┘
           │                              │
┌──────────▼──────────────────────────────▼───────────────────┐
│  POSTGRESQL — tracks · mood_scores · apple_catalog_map        │
│  (+ S3 via boto3 for cached preview clips)                    │
└───────────────────────────────────────────────────────────────┘
```

## Repo layout

```
server/        FastAPI backend + the mood-analysis pipeline
  app/
    clients/   spotify.py · apple_music.py · storage.py (S3)
    pipeline/  features.py (DSP) · scoring.py (model) · analyze.py
    routers/   auth.py · sync.py · mood.py
    models.py  SQLAlchemy ORM
    selection.py  slider position → track
  scripts/     phase1_pipeline.py · seed_demo.py · rescore.py
  tests/       39 tests
app/           Expo mobile app
  app/         expo-router screens
  src/         api · auth · components · playback · lib
docs/          DECISIONS.md
```

## Quick start

The fastest path needs **no Spotify or Apple credentials** — a seed script creates a
demo library spread across the whole 1–100 range so the slider is immediately usable.

```bash
# 1. Database
docker compose up -d db          # or use a local postgres

# 2. Backend
cd server
uv venv --python 3.12 .venv && uv pip install -e ".[dev]" --python .venv/bin/python
cp .env.example .env
.venv/bin/python scripts/seed_demo.py
.venv/bin/python -m uvicorn app.main:app --reload --port 8000

# 3. Try it without the app
curl -H "Authorization: Bearer demo-session-token" localhost:8000/mood/85

# 4. Mobile app
cd ../app
npm install
npx expo start        # then tap "Try the demo library"
```

API docs are at `http://localhost:8000/docs`.

### With real credentials

Fill in `server/.env`:

- **Spotify** — [developer dashboard](https://developer.spotify.com/dashboard), add
  redirect URI `moodsync://callback`. The app is a public client using PKCE, so the
  client secret is optional and server-side only.
- **Apple Music** — Apple Developer → Keys → enable MusicKit, download the `.p8` once.
  Only a *developer* token is needed; no Apple Music subscription, no user token.

Then sign in with Spotify in the app and hit **Sync library**.

## The mood pipeline

Prove it end-to-end before anything else — this is the project's core risk:

```bash
cd server
.venv/bin/python scripts/phase1_pipeline.py \
  --search "Weightless|Marconi Union" "Bangarang|Skrillex" --explain
```

For each track: ISRC → Apple catalog → download 30s preview → extract features →
score → print the per-feature contribution breakdown.

### Features extracted

`tempo_bpm`, `rms_mean`, `rms_std`, `dynamic_range`, `spectral_centroid_hz`,
`spectral_rolloff_hz`, `spectral_flatness`, `spectral_contrast`,
`zero_crossing_rate`, `onset_rate_hz`, `percussive_ratio`, `tonal_valence`.

`tonal_valence` correlates the average chroma vector against Krumhansl-Schmuckler
major and minor key profiles at all 12 rotations — a cheap major/minor proxy standing
in for Spotify's `valence`.

### Scoring

A **formalised heuristic**, not a black box. Each feature is squashed to 0–1 against
documented anchors, then combined with named weights:

| Feature | Anchors (0 → 1) | Weight |
|---|---|---|
| `tempo_bpm` | 60 → 180 | 0.30 |
| `rms_mean` | 0.010 → 0.180 | 0.22 |
| `onset_rate_hz` | 0.5 → 7.0 | 0.18 |
| `spectral_centroid_hz` | 800 → 4500 | 0.15 |
| `percussive_ratio` | 0.15 → 0.70 | 0.10 |
| `tonal_valence` | 0 → 1 | 0.05 |

Two reasons it's a heuristic rather than a trained model: there's no labelled dataset
yet, so a regression would be fitting noise; and the weights being *data* means
swapping in a trained model later only replaces `score_features`, keeping the same
feature vector and the same cached rows.

Because `mood_scores.feature_vector` is persisted, **re-scoring is a pure database
pass** — no audio is re-downloaded and no DSP re-runs:

```bash
.venv/bin/python scripts/rescore.py --dry-run
```

Ranges past an anchor **clamp rather than extrapolate**, so a 400 BPM tempo-octave
error can't outrank a clean 180.

## Playback

You cannot stream Spotify audio inside your own app. Three tiers, degrading
gracefully:

1. **App Remote SDK** — sends playback *commands* to the installed Spotify app. Needs
   a native module and a custom dev client, so it's resolved at runtime and simply
   reports unavailable in Expo Go.
2. **Deep link** to `spotify:track:<id>` — works today with no native module, plays
   the full track, hands the user to the Spotify UI.
3. **30s Apple preview** via `expo-audio` — always available, because that clip was
   already downloaded to score the track.

The UI always says which one you're hearing rather than silently degrading.

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Status of DB, Apple/Spotify config, cache backend |
| `GET /auth/spotify/config` | Client id / redirect / scopes, defined once server-side |
| `POST /auth/spotify/callback` | PKCE code exchange → opaque MoodSync session token |
| `GET /auth/me` | Profile + library coverage |
| `POST /sync` | Pull library, queue background analysis |
| `GET /sync/status` | Coverage, failures, score histogram |
| `GET /mood/{score}` | **The slider endpoint.** Best track for a mood, 1–100 |

`/mood/{score}` accepts repeated `exclude=` params (recently played ids). Selection
takes the k nearest candidates in a widening window and samples one, weighted by
closeness and match confidence — strictly-nearest would return the same song every
time you landed on 72, which reads as broken.

## Tests

```bash
cd server && .venv/bin/python -m pytest -q     # 39 passed
cd app && npm run typecheck
```

The DSP tests synthesize audio rather than hitting any API: a quiet drone and a
170 BPM percussive loop must land on opposite ends of the slider (they score **5** and
**62**).

That test caught a real bug. librosa's onset detector normalises internally, so it's
scale-invariant — on a near-silent ambient clip it reported *more* onsets (93) than a
loud percussive one (56), which would have pushed exactly the calmest tracks up the
slider. Onsets are now gated on strength relative to the clip's own RMS; see
`_onset_rate` in `server/app/pipeline/features.py`.

## Status

- [x] **Phase 1** — pipeline: ISRC → Apple preview → features → score
- [x] **Phase 2** — FastAPI + PostgreSQL caching
- [x] **Phase 3** — Expo slider UI against the real endpoint
- [x] **Phase 4** — Spotify OAuth + playback handoff (deep link; App Remote SDK needs a dev client)
- [x] **Phase 5** — preview fallback, error/loading states, background sync
- [ ] Measure real ISRC match rate over a full library
- [ ] Label ~100 tracks and fit a regression to replace the heuristic
- [ ] Auto-advance to the next track as the current one ends

## Notes / known limits

- Apple previews are AAC in an MP4 container. libsndfile can't decode AAC, so
  `load_audio` walks soundfile → audioread (CoreAudio on macOS) → ffmpeg. **Install
  ffmpeg on Linux/Docker.**
- OAuth tokens are stored in plaintext in `users`. They need envelope encryption
  (KMS) before this goes near real users.
- `ONSET_STRENGTH_FLOOR` was tuned on synthetic signals; it should be revisited
  against a labelled set of real clips.
- Session auth is an opaque bearer token, deliberately simple. It is not a
  production identity system.

## Stack

React Native · Expo · Expo Router · TypeScript · TanStack Query · Axios · NativeWind /
Tailwind · Python · FastAPI · SQLAlchemy · PostgreSQL · librosa · boto3 (AWS SDK) ·
Spotify Web API · Apple Music API
