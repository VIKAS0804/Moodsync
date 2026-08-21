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
| No Apple developer account | Falls back to the public **iTunes Search API**, which needs no credentials at all. Text-matched rather than ISRC-exact, so those matches are confidence-discounted |
| Deriving mood | Local DSP feature extraction (librosa) → a weighted model calibrated against real audio → a 1–100 score |
| Cost of recomputing | Scores **and their feature vectors** are cached in PostgreSQL |
| Can't stream Spotify audio in-app | Licensing, not a technical gap. Playback is handed off to the Spotify app, with a preview fallback |
| `/playlists/{id}/tracks` now 403s | Deprecated and replaced by `/playlists/{id}/items`, where the nested key is `item` not `track`. The 403 body says only "Forbidden", so it's easy to misread as a permissions problem |

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
    clients/   spotify.py · apple_music.py · itunes.py · storage.py (S3)
    pipeline/  features.py (DSP) · scoring.py (model) · analyze.py
    routers/   auth.py · sync.py · mood.py
    models.py  SQLAlchemy ORM
    selection.py  slider position → track
  scripts/     phase1_pipeline.py · calibrate.py · seed_demo.py · rescore.py
               backfill.py · repair_catalog.py · dev_sync_schema.py
  tests/       60 tests
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
                      # press w for browser, or scan the QR with Expo Go
```

The app targets **SDK 54** deliberately: the App Store build of Expo Go is
pinned to SDK 54, so a newer SDK gives "Project is incompatible with this
version of Expo Go" on a phone with the latest Expo Go installed. Newer SDKs
need a development build, and on iOS that means an Apple Developer membership.

```bash
```

API docs are at `http://localhost:8000/docs`.

### Serving it to other devices on your network

uvicorn binds `127.0.0.1` by default, which only accepts connections from the
same machine. To let a phone (or a friend's laptop) on the same WiFi reach it:

```bash
.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Then it's at `http://<your-lan-ip>:8000` — `ipconfig getifaddr en0` on macOS.
The mobile app derives that address from the Expo connection automatically;
override with `EXPO_PUBLIC_API_URL` if needed.

Two caveats. `0.0.0.0` exposes the API to everyone on the network, session
tokens are bearer tokens over plain HTTP, and `demo-session-token` is a
published constant — fine on home WiFi, not on café or campus networks. And the
browser login won't work from another machine: Spotify only accepts HTTPS
redirect URIs or loopback *IP literals*, so `http://<lan-ip>:8000/...` is
rejected while `127.0.0.1` resolves to whichever machine opened the browser.
Use an HTTPS tunnel (cloudflared, ngrok) and register that URL if you need
remote sign-in.

### With real credentials

Fill in `server/.env`:

- **Spotify** — [developer dashboard](https://developer.spotify.com/dashboard). The app
  is a public client using PKCE, so the client secret is optional and server-side only.
  Redirect URIs must be HTTPS or the loopback **IP literal**; Spotify rejects
  `localhost`. Register whichever you'll use:

  | Signing in from | Register | Note |
  |---|---|---|
  | Browser login (`/auth/spotify/login`) | `http://127.0.0.1:8000/auth/spotify/callback` | Simplest; server-side flow |
  | The app on web | `http://127.0.0.1:8081/callback` | Open the app at `127.0.0.1:8081`, **not** `localhost` — different origins, and the PKCE verifier is per-origin |
  | The app in Expo Go | `exp://<lan-ip>:8081/--/callback` | Embeds the dev machine's IP, so it changes with the network. **Use device pairing instead** — see below |
  | Standalone build | `moodsync://callback` | Stable |

  The sign-in screen prints the exact redirect this build will use, and flags it when
  Spotify won't accept it.
- **Apple Music** *(optional)* — Apple Developer → Keys → enable MusicKit, download
  the `.p8` once. Only a *developer* token is needed; no Apple Music subscription, no
  user token. **Without it the pipeline still runs**, falling back to the
  credential-free iTunes Search API; set `PREVIEW_SOURCE` to pin one or the other.
  Apple Music buys you exact ISRC matching, i.e. certainty you analysed the same
  recording the user owns.

Then sign in with Spotify in the app and hit **Sync library**.

### Signing in on a phone

In-app Spotify sign-in is the awkward path on a device: Expo Go's redirect URI
embeds the dev machine's IP, so it has to be registered with Spotify and
re-registered whenever the network changes. The server-side browser login can't
stand in either — its redirect is a loopback literal, which on a phone's browser
means the phone.

So sign in on a computer and pair the phone:

1. Open `http://127.0.0.1:8000/auth/spotify/login` on the computer running the API
2. It shows a **6-digit code** (valid 5 minutes, single use)
3. Type it into the app's *"Signed in on your computer?"* box

Nothing to register, and it survives an IP change.

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
anchors set to the p5/p95 of that feature *measured over real preview clips*, then
combined with named weights:

| Feature | Anchors (0 → 1) | Weight |
|---|---|---|
| `rms_mean` | 0.0595 → 0.3185 | 0.22 |
| `onset_rate_hz` | 0.401 → 4.557 | 0.20 |
| `spectral_flatness` | 0.0 → 0.064 | 0.18 |
| `spectral_centroid_hz` | 509 → 3100 | 0.16 |
| `percussive_ratio` | 0.0278 → 0.4680 | 0.12 |
| `zero_crossing_rate` | 0.0321 → 0.1476 | 0.12 |

**Tempo is deliberately absent.** Beat tracking on 30-second previews is unreliable
enough to be actively harmful — calibration put Clair de Lune at 172 BPM and Killing
in the Name at 83, and mean detected tempo per energy tier came out 116/105/131/119/118,
i.e. uncorrelated with energy. It previously carried the *largest* weight. It's still
extracted and persisted; it's just not trusted by the scorer.

`spectral_flatness` (noisiness — distorted guitars, cymbals, noise-heavy EDM) is what
separates aggressive tracks from merely loud ones.

The weights being *data* means swapping in a trained model later only replaces
`score_features`, keeping the same feature vector and the same cached rows.

### Calibration

`scripts/calibrate.py` fetches 30 real previews (no credentials needed), extracts
features, and scores the model by Spearman rank correlation against five hand-labelled
energy tiers:

```bash
.venv/bin/python scripts/calibrate.py            # fetch + report
.venv/bin/python scripts/calibrate.py --no-fetch # reuse cached clips
```

| Model | ρ (tier vs score) | Tier mean scores 1→5 |
|---|---|---|
| v1 — guessed anchors, tempo-weighted | +0.53 | 28.0 · 40.8 · 59.7 · 55.5 · 55.3 |
| **v2 — calibrated, no tempo** | **+0.82** | 8.2 · 34.2 · 61.0 · 69.0 · 70.2 |

v1 couldn't order the top three tiers at all. v2 wins on 399 of 400 random half-splits,
and leave-one-out ρ stays within 0.802–0.858, so the gain isn't one lucky track.

**Honest limits:** 29 tracks, tiers labelled by hand, and tiers 4/5 remain nearly tied
(69.0 vs 70.2) — loudness-war mastering leaves a pop master and a metal master with
similar RMS. Treat this as "clearly better than v1", not as validated accuracy.

Because `mood_scores.feature_vector` is persisted, **re-scoring is a pure database
pass** — no audio is re-downloaded and no DSP re-runs:

```bash
.venv/bin/python scripts/rescore.py --dry-run
```

Ranges past an anchor **clamp rather than extrapolate**, so a 400 BPM tempo-octave
error can't outrank a clean 180.

## Playback

You cannot stream Spotify audio from your own audio stack — but you *can* host
Spotify's player. Four tiers, best first:

| Route | Full track | Seek | Requires |
|---|---|---|---|
| **Web Playback SDK** | yes | **yes** | Web + Premium |
| App Remote SDK | yes | yes | Custom dev client (native) |
| `spotify:` deep link | yes | no — Spotify takes the screen | Spotify app installed |
| 30s preview (`expo-audio`) | no | yes | nothing |

The Web Playback SDK is the only licensed route to full audio *we* control, so on
web with Premium MoodSync is a real player: play/pause, a scrubbable progress bar,
and ±10s. It turns the page into a Spotify playback device, which means it needs a
Spotify access token in the browser — hence `GET /auth/spotify/playback-token`, a
deliberate and documented exception to keeping tokens server-side. Everything else
still uses the server's copy.

A listener can also *choose*: **Auto / Full song / 30s**. Preview isn't only a
fallback — it keeps playback inside MoodSync instead of handing the screen to
Spotify, which is what you want while skimming for a mood.

The UI always says which route you're hearing, and the transport controls are
disabled rather than hidden when a route can't support them (deep link), so it's
visible *that* seeking is unavailable.

Each route owns a separate audio pipeline, so exactly one may be audible at a
time and every path that starts audio goes through a single `stopAll()` first.
Stopping the preview player says nothing about the Spotify web player, and vice
versa — skipping that step plays a 30-second clip over a full track. `play()`
also carries a generation counter, because it awaits several times and a fast
slider drag can put two calls in flight.

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
cd server && .venv/bin/python -m pytest -q     # 60 passed
cd app && npm run typecheck
```

The DSP tests synthesize audio rather than hitting any API, so they run offline. That
is also their limitation, and it hid two real bugs that only appeared on real music:

**1. The pipeline couldn't decode a single real track.** Every Apple/iTunes preview is
AAC, libsndfile can't decode AAC, and the synthetic tests used WAV — which decodes
through a completely different path. All 8 real previews failed with `AudioDecodeError`
while 39 tests stayed green. ffmpeg is now a hard dependency (`imageio-ffmpeg` ships a
binary via pip, so no system install is needed).

**2. The onset gate was backwards on real music.** Onsets were gated at `20 × rms`.
On real masters that ratio runs 12× (Skrillex) to 299× (Metallica) — *inversely*
related to energy, because loud compressed music has a low crest factor. The gate was
discarding the transients of the most energetic tracks: Skrillex came back with 0.03
onsets/sec. It's now an absolute flux floor (ambient peaks ~1.4, real music 7–25).

Both are why `scripts/calibrate.py` exists: correctness here has to be measured against
real audio, not synthetic signals.

## Measured on a real library

First end-to-end run against a real Spotify account (50 Liked Songs):

| Metric | Result |
|---|---|
| ISRC match rate | **100%** (50/50) |
| Analysis coverage | **92%** (46/50 scored) |
| Failed analyses | 0 |
| No preview in any catalog | 4 (8%) |
| Score range found | 20-80 |

That library contains nothing above 80, which is exactly the case
library-relative scoring exists for: slider 95 still returns its most intense
track instead of searching near 95 and finding nothing.

## Status

- [x] **Phase 1** — pipeline: ISRC → Apple preview → features → score
- [x] **Phase 2** — FastAPI + PostgreSQL caching
- [x] **Phase 3** — Expo slider UI against the real endpoint
- [x] **Phase 4** — Spotify OAuth + playback handoff (deep link; App Remote SDK needs a dev client)
- [x] **Phase 5** — preview fallback, error/loading states, background sync
- [ ] Measure real ISRC match rate over a full library
- [ ] Library-relative (z-scored) scores, so the whole slider is usable regardless
      of a user's taste
- [ ] Promote valence to a second axis (calm/tense as well as calm/hyper)
- [ ] Train a regression on a public audio-features dataset to replace the heuristic
- [ ] Auto-advance to the next track as the current one ends

## Notes / known limits

- ffmpeg is **required**, not optional: every real preview is AAC and libsndfile
  can't decode it. `imageio-ffmpeg` provides a binary as a normal pip dependency,
  so `pip install` is sufficient; a system ffmpeg on `PATH` is preferred if present.
- The scoring model is calibrated on 29 hand-labelled tracks. It reliably separates
  calm from energetic, but not "rock" from "metal".
- iTunes Search matches are **text** matches, not ISRC matches — a live version or
  cover can slip through. They're confidence-discounted, which lowers their odds
  during selection, but that's a hedge, not a fix. Configure Apple Music for exact
  ISRC matching.
- `/sync` runs analysis as a FastAPI background task, which dies with the
  process — a uvicorn reload abandons the rest of the run. For a real library use
  `python scripts/backfill.py`, which runs detached and resumes.
- The iTunes Search API is unauthenticated and throttles hard. Requests are
  spaced 3s apart, so a 1,700-track library takes ~80 minutes to analyse. Don't
  lower that without watching for 403s: a throttled response looks exactly like
  "no such track" unless you check the status.
- There are no migrations. `create_all()` only creates missing *tables*, so after
  a model change run `python scripts/dev_sync_schema.py --apply` to add new
  columns to an existing database. It is additive-only — Alembic is the real
  answer once the schema settles.
- OAuth tokens are stored in plaintext in `users`. They need envelope encryption
  (KMS) before this goes near real users.
- Session auth is an opaque bearer token, deliberately simple. It is not a
  production identity system.

## Stack

React Native · Expo · Expo Router · TypeScript · TanStack Query · Axios · NativeWind /
Tailwind · Python · FastAPI · SQLAlchemy · PostgreSQL · librosa · boto3 (AWS SDK) ·
Spotify Web API · Apple Music API
