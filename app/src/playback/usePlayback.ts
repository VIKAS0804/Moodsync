/**
 * Playback controller.
 *
 * Picks the best available route for a matched track, reports which one it used,
 * and exposes real transport controls (position, duration, seek) so the UI can
 * be an actual player rather than a fire-and-forget trigger.
 *
 * Routes, best first:
 *  1. `spotify_web`  full track, seekable. Web + Premium, via the Web Playback
 *                    SDK. The only licensed route to full audio we control.
 *  2. `spotify_connect` full track on a Spotify device, driven through the Web
 *                    API from our backend. Keeps the slider on screen on a
 *                    phone, which the deep link cannot.
 *  3. `spotify_remote` full track via the App Remote SDK on device. Needs a
 *                    custom dev client, so absent in Expo Go.
 *  3. `spotify_deep_link` full track, but Spotify takes the screen and we lose
 *                    transport control.
 *  4. `preview`      30s clip in-app, fully seekable.
 */

import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from 'expo-audio';
import { useCallback, useEffect, useRef, useState } from 'react';

import { api } from '@/api/client';
import type { MoodMatch } from '@/api/types';
import {
  appRemoteAvailable,
  openInSpotifyApp,
  pauseSpotify,
  playViaAppRemote,
} from '@/playback/spotify';
import {
  connectHasDevice,
  connectPause,
  connectPlay,
  connectResume,
  connectSeek,
  connectState,
} from '@/playback/spotifyConnect';
import {
  activateWebPlayerElement,
  connectWebPlayer,
  disconnectWebPlayer,
  playTrackOnWebPlayer,
  webPlaybackSupported,
  webPlayerPause,
  webPlayerPosition,
  webPlayerReady,
  webPlayerResume,
  webPlayerSeek,
} from '@/playback/webSpotify';

export type PlaybackRoute =
  | 'spotify_web'
  | 'spotify_connect'
  | 'spotify_remote'
  | 'spotify_deep_link'
  | 'preview'
  | 'none';

/**
 * What the listener asked for, as opposed to what's available.
 *
 * - `auto`    full track when the account and platform allow it, else preview
 * - `full`    always try Spotify; fall back only if it genuinely can't play
 * - `preview` always the 30s clip, even on Premium
 *
 * `preview` isn't only a fallback: it stays in MoodSync rather than handing the
 * screen to Spotify, so nudging the slider stays quick.
 */
export type PlaybackPreference = 'auto' | 'full' | 'preview';

export interface PlaybackState {
  route: PlaybackRoute;
  isPlaying: boolean;
  /** Set when we wanted Spotify but had to settle for the preview. */
  degradedReason: string | null;
  positionMs: number;
  durationMs: number;
  /** False for deep-link playback, where we can't observe or control anything. */
  seekable: boolean;
}

const INITIAL: PlaybackState = {
  route: 'none',
  isPlaying: false,
  degradedReason: null,
  positionMs: 0,
  durationMs: 0,
  seekable: false,
};

async function fetchPlaybackToken(): Promise<string> {
  const response = await api.get<{ access_token: string }>('/auth/spotify/playback-token');
  return response.data.access_token;
}

export interface PlaybackOptions {
  /**
   * Called when a track finishes on its own, for auto-advance. Held in a ref so
   * a changing callback doesn't force the audio listeners to be torn down and
   * rebuilt mid-track.
   */
  onEnded?: () => void;
}

export function usePlayback(options: PlaybackOptions = {}) {
  const playerRef = useRef<AudioPlayer | null>(null);
  const onEndedRef = useRef(options.onEnded);
  onEndedRef.current = options.onEnded;
  const [state, setState] = useState<PlaybackState>(INITIAL);
  const routeRef = useRef<PlaybackRoute>('none');
  /**
   * Bumped on every play request. `play` awaits several times, so a fast slider
   * drag can have two calls in flight; whichever started last wins and the
   * older one must abandon rather than also start audio.
   */
  const epochRef = useRef(0);
  /** Last seen web-player progress, for telling "ended" from "paused". */
  const webProgressRef = useRef({ playing: false, positionMs: 0 });
  /** Same idea for Spotify Connect, which is polled rather than pushed. */
  const connectProgressRef = useRef({ playing: false, positionMs: 0 });

  const setRoute = (next: Partial<PlaybackState>) => {
    if (next.route) routeRef.current = next.route;
    setState((prev) => ({ ...prev, ...next }));
  };

  useEffect(() => {
    // Keep playing when the phone is on silent -- this is a music app.
    // Silent mode: it's a music app. Background: the phone is in a pocket or a
    // car mount, so locking the screen must not stop playback.
    setAudioModeAsync({
      playsInSilentMode: true,
      shouldPlayInBackground: true,
    }).catch(() => undefined);
    return () => {
      playerRef.current?.remove();
      playerRef.current = null;
      // Don't leave a "MoodSync" device registered in Spotify after we're gone.
      disconnectWebPlayer();
    };
  }, []);

  // One ticker drives the progress bar for whichever route is active.
  useEffect(() => {
    const id = setInterval(async () => {
      const route = routeRef.current;
      if (route === 'preview') {
        const p = playerRef.current;
        if (!p) return;
        setState((prev) => ({
          ...prev,
          positionMs: Math.round((p.currentTime ?? 0) * 1000),
          durationMs: Math.round((p.duration ?? 0) * 1000),
          isPlaying: p.playing ?? prev.isPlaying,
        }));
      } else if (route === 'spotify_web') {
        const s = await webPlayerPosition();
        if (!s) return;
        setState((prev) => ({
          ...prev,
          positionMs: s.positionMs,
          durationMs: s.durationMs,
          isPlaying: !s.paused,
        }));
      } else if (route === 'spotify_connect') {
        const s = await connectState();
        if (!s) return;
        // Ending shows up as stopped with the position reset, same as the web
        // SDK; require prior progress so a manual pause isn't mistaken for it.
        const previous = connectProgressRef.current;
        if (!s.isPlaying && s.positionMs === 0 && previous.playing && previous.positionMs > 1000) {
          connectProgressRef.current = { playing: false, positionMs: 0 };
          onEndedRef.current?.();
          return;
        }
        connectProgressRef.current = { playing: s.isPlaying, positionMs: s.positionMs };
        setState((prev) => ({
          ...prev,
          positionMs: s.positionMs,
          durationMs: s.durationMs || prev.durationMs,
          isPlaying: s.isPlaying,
        }));
      }
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const stopPreview = useCallback(() => {
    playerRef.current?.pause();
    playerRef.current?.remove();
    playerRef.current = null;
  }, []);

  /**
   * Silence every route before starting another one.
   *
   * Each route owns a different audio pipeline, so stopping one says nothing
   * about the others: tearing down the preview player left the Spotify web
   * player streaming, and switching Full song -> 30s played both at once.
   * Anything that starts audio must come through here first.
   *
   * Pausing the web player is safe even when we're about to start a new track
   * on it, and cheap -- it's a local SDK call, not a network round trip.
   */
  const stopAll = useCallback(async () => {
    stopPreview();

    if (webPlayerReady()) {
      try {
        await webPlayerPause();
      } catch {
        // Already paused, or the device went away. Nothing to recover.
      }
    }

    if (routeRef.current === 'spotify_remote') {
      try {
        await pauseSpotify();
      } catch {
        // App Remote not connected.
      }
    }

    if (routeRef.current === 'spotify_connect') {
      try {
        await connectPause();
      } catch {
        // Device went away, or already paused.
      }
    }
    // Deep-link playback is owned by the Spotify app; we can't stop it, which
    // is exactly why that route reports itself as not seekable.
  }, [stopPreview]);

  const playPreview = useCallback(
    (url: string, reason: string | null) => {
      const player = createAudioPlayer({ uri: url });
      playerRef.current = player;
      // didJustFinish is the only reliable end signal: polling position against
      // duration races the final buffer and can fire twice or not at all.
      player.addListener('playbackStatusUpdate', (status) => {
        if (status?.didJustFinish) onEndedRef.current?.();
      });
      player.play();
      setRoute({
        route: 'preview',
        isPlaying: true,
        degradedReason: reason,
        positionMs: 0,
        durationMs: 0,
        seekable: true,
      });
    },
    [],
  );

  /** Last reason the web player declined, so the UI can say something useful. */
  const webErrorRef = useRef<string | null>(null);

  /** Full track in-page with transport control. Returns false if unavailable. */
  const tryWebPlayer = useCallback(async (match: MoodMatch): Promise<boolean> => {
    if (!webPlaybackSupported) return false;
    webErrorRef.current = null;
    try {
      const token = await fetchPlaybackToken();
      if (!webPlayerReady()) {
        await connectWebPlayer(fetchPlaybackToken, (s) => {
          if (!s) return;
          // A finished track surfaces as paused at position 0, which is
          // otherwise indistinguishable from a manual pause -- so require that
          // we were playing and had actually got somewhere first.
          const wasPlaying = webProgressRef.current.playing;
          const hadProgress = webProgressRef.current.positionMs > 1000;
          if (s.paused && s.positionMs === 0 && wasPlaying && hadProgress) {
            webProgressRef.current = { playing: false, positionMs: 0 };
            onEndedRef.current?.();
            return;
          }
          webProgressRef.current = { playing: !s.paused, positionMs: s.positionMs };
          setState((prev) => ({
            ...prev,
            positionMs: s.positionMs,
            durationMs: s.durationMs,
            isPlaying: !s.paused,
          }));
        });
      }
      // Must happen before playback or the browser keeps the element muted;
      // Spotify still reports is_playing, so the page looks broken instead.
      await activateWebPlayerElement();
      await playTrackOnWebPlayer(match.track.spotify_uri, token);
      setRoute({
        route: 'spotify_web',
        isPlaying: true,
        degradedReason: null,
        positionMs: 0,
        durationMs: match.track.duration_ms ?? 0,
        seekable: true,
      });
      return true;
    } catch (err) {
      // Record it: silently degrading here made this undiagnosable, and the
      // causes (no Premium, SDK blocked, token refused, device gone) need
      // different responses from the listener.
      webErrorRef.current = err instanceof Error ? err.message : String(err);
      return false;
    }
  }, []);

  const play = useCallback(
    async (match: MoodMatch, preference: PlaybackPreference = 'auto') => {
      const epoch = ++epochRef.current;
      const superseded = () => epoch !== epochRef.current;

      // Exactly one route may be audible at a time.
      await stopAll();
      if (superseded()) return;

      if (preference === 'preview') {
        if (match.preview_url) {
          playPreview(match.preview_url, null);
          return;
        }
        setRoute({
          route: 'none',
          isPlaying: false,
          degradedReason: 'No preview clip for this track. Try Full song.',
          seekable: false,
        });
        return;
      }

      // Full playback that keeps the listener *here*: the web SDK, App Remote on
      // a dev build, or Spotify Connect driving an already-awake device.
      // `auto` may only ever use these.
      const connectReady = !webPlaybackSupported && (await connectHasDevice());
      if (superseded()) return;
      const inAppFullAvailable = webPlaybackSupported || appRemoteAvailable() || connectReady;
      const wantsFull =
        preference === 'full' ||
        (match.playback_mode === 'spotify_remote' && inAppFullAvailable);

      if (wantsFull) {
        const started = await tryWebPlayer(match);
        if (superseded()) {
          // A newer request is running; don't leave this track audible.
          if (started) await webPlayerPause().catch(() => undefined);
          return;
        }
        if (started) return;

        if (await playViaAppRemote(match.track.spotify_uri)) {
          if (superseded()) return;
          setRoute({
            route: 'spotify_remote',
            isPlaying: true,
            seekable: true,
            degradedReason: null,
            positionMs: 0,
            durationMs: match.track.duration_ms ?? 0,
          });
          return;
        }

        // Remote-control an awake Spotify device. Full track, transport
        // controls, and crucially the slider stays on screen.
        if (connectReady) {
          try {
            await connectPlay(match.track.spotify_uri);
            if (superseded()) return;
            connectProgressRef.current = { playing: true, positionMs: 0 };
            setRoute({
              route: 'spotify_connect',
              isPlaying: true,
              seekable: true,
              degradedReason: null,
              positionMs: 0,
              durationMs: match.track.duration_ms ?? 0,
            });
            return;
          } catch {
            // Device went to sleep between the check and the play call.
          }
        }
      }

      // Leaving the app is only ever an explicit choice. On `auto` we would
      // rather play 30 seconds in-app than throw the listener into Spotify,
      // where the slider -- the entire product -- is unreachable.
      if (preference === 'full' && (await openInSpotifyApp(match.track.spotify_uri))) {
        if (superseded()) return;
        setRoute({
          route: 'spotify_deep_link',
          isPlaying: true,
          // Spotify owns playback now; we can neither observe nor control it.
          seekable: false,
          degradedReason: 'Playing in the Spotify app — come back to keep sliding.',
          positionMs: 0,
          durationMs: match.track.duration_ms ?? 0,
        });
        return;
      }

      // Be specific about *why*, because the three causes need different
      // actions from the listener and "couldn't reach Spotify" implies a
      // network fault when usually nothing is wrong.
      let reason: string;
      if (!match.preview_url) {
        reason = 'No audio available for this track.';
      } else if (match.playback_mode !== 'spotify_remote' && preference !== 'full') {
        reason = 'Spotify Premium required for full tracks — playing a 30s preview.';
      } else if (webPlaybackSupported) {
        reason = webErrorRef.current
          ? `Spotify: ${webErrorRef.current} — playing a 30s preview.`
          : "Spotify wouldn't start the full track — playing a 30s preview.";
      } else {
        // Not a failure: on a phone this is the better default, because the
        // alternative is leaving the app for every track.
        reason =
          'Playing 30s previews so you can keep sliding. ' +
          'Pick Full song to hand a track to the Spotify app.';
      }

      if (match.preview_url) {
        playPreview(match.preview_url, reason);
        return;
      }

      setRoute({
        route: 'none',
        isPlaying: false,
        degradedReason: 'No playable audio for this track.',
        seekable: false,
      });
    },
    [playPreview, stopAll, tryWebPlayer],
  );

  const pause = useCallback(async () => {
    const route = routeRef.current;
    if (route === 'preview') playerRef.current?.pause();
    else if (route === 'spotify_web') await webPlayerPause();
    else if (route === 'spotify_connect') await connectPause().catch(() => undefined);
    else if (route === 'spotify_remote') await pauseSpotify();
    setState((prev) => ({ ...prev, isPlaying: false }));
  }, []);

  const resume = useCallback(async () => {
    const route = routeRef.current;
    if (route === 'preview') playerRef.current?.play();
    else if (route === 'spotify_web') await webPlayerResume();
    else if (route === 'spotify_connect') await connectResume().catch(() => undefined);
    else return; // deep link / nothing to resume
    setState((prev) => ({ ...prev, isPlaying: true }));
  }, []);

  /** Absolute seek, in milliseconds. */
  const seekTo = useCallback(async (positionMs: number) => {
    const route = routeRef.current;
    const clamped = Math.max(0, positionMs);
    if (route === 'preview') {
      await playerRef.current?.seekTo(clamped / 1000);
    } else if (route === 'spotify_web') {
      await webPlayerSeek(clamped);
    } else if (route === 'spotify_connect') {
      await connectSeek(clamped).catch(() => undefined);
    } else {
      return; // deep link / nothing playing
    }
    setState((prev) => ({ ...prev, positionMs: clamped }));
  }, []);

  /** Relative seek, for the -10s / +10s buttons. */
  const nudge = useCallback(
    (deltaMs: number) => seekTo(state.positionMs + deltaMs),
    [seekTo, state.positionMs],
  );

  return { ...state, play, pause, resume, seekTo, nudge };
}
