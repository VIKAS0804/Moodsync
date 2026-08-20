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
 *  2. `spotify_remote` full track via the App Remote SDK on device. Needs a
 *                    custom dev client, so absent in Expo Go.
 *  3. `spotify_deep_link` full track, but Spotify takes the screen and we lose
 *                    transport control.
 *  4. `preview`      30s clip in-app, fully seekable.
 */

import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from 'expo-audio';
import { useCallback, useEffect, useRef, useState } from 'react';

import { api } from '@/api/client';
import type { MoodMatch } from '@/api/types';
import { pauseSpotify, playViaSpotify } from '@/playback/spotify';
import {
  connectWebPlayer,
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

export function usePlayback() {
  const playerRef = useRef<AudioPlayer | null>(null);
  const [state, setState] = useState<PlaybackState>(INITIAL);
  const routeRef = useRef<PlaybackRoute>('none');

  const setRoute = (next: Partial<PlaybackState>) => {
    if (next.route) routeRef.current = next.route;
    setState((prev) => ({ ...prev, ...next }));
  };

  useEffect(() => {
    // Keep playing when the phone is on silent -- this is a music app.
    setAudioModeAsync({ playsInSilentMode: true }).catch(() => undefined);
    return () => {
      playerRef.current?.remove();
      playerRef.current = null;
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
      }
    }, 500);
    return () => clearInterval(id);
  }, []);

  const stopPreview = useCallback(() => {
    playerRef.current?.pause();
    playerRef.current?.remove();
    playerRef.current = null;
  }, []);

  const playPreview = useCallback(
    (url: string, reason: string | null) => {
      stopPreview();
      const player = createAudioPlayer({ uri: url });
      playerRef.current = player;
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
    [stopPreview],
  );

  /** Full track in-page with transport control. Returns false if unavailable. */
  const tryWebPlayer = useCallback(async (match: MoodMatch): Promise<boolean> => {
    if (!webPlaybackSupported) return false;
    try {
      const token = await fetchPlaybackToken();
      if (!webPlayerReady()) {
        await connectWebPlayer(fetchPlaybackToken, (s) => {
          if (!s) return;
          setState((prev) => ({
            ...prev,
            positionMs: s.positionMs,
            durationMs: s.durationMs,
            isPlaying: !s.paused,
          }));
        });
      }
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
    } catch {
      // Premium missing, SDK blocked, or token refused -- fall through quietly.
      return false;
    }
  }, []);

  const play = useCallback(
    async (match: MoodMatch, preference: PlaybackPreference = 'auto') => {
      stopPreview();

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

      const wantsFull = preference === 'full' || match.playback_mode === 'spotify_remote';

      if (wantsFull && (await tryWebPlayer(match))) return;

      if (wantsFull) {
        const result = await playViaSpotify(match.track.spotify_uri);
        if (result !== 'failed') {
          setRoute({
            route: result === 'remote' ? 'spotify_remote' : 'spotify_deep_link',
            isPlaying: true,
            // A deep link hands the screen to Spotify; we can't observe it.
            seekable: result === 'remote',
            degradedReason:
              result === 'deep_link'
                ? 'Playing in the Spotify app — come back to keep sliding.'
                : null,
            positionMs: 0,
            durationMs: match.track.duration_ms ?? 0,
          });
          return;
        }
      }

      const reason =
        preference === 'full'
          ? "Couldn't reach Spotify for the full track — playing a 30s preview."
          : match.playback_mode === 'spotify_remote'
            ? 'Full playback needs the Spotify app or a browser — playing a 30s preview.'
            : 'Spotify Premium required for full tracks — playing a 30s preview.';

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
    [playPreview, stopPreview, tryWebPlayer],
  );

  const pause = useCallback(async () => {
    const route = routeRef.current;
    if (route === 'preview') playerRef.current?.pause();
    else if (route === 'spotify_web') await webPlayerPause();
    else if (route === 'spotify_remote') await pauseSpotify();
    setState((prev) => ({ ...prev, isPlaying: false }));
  }, []);

  const resume = useCallback(async () => {
    const route = routeRef.current;
    if (route === 'preview') playerRef.current?.play();
    else if (route === 'spotify_web') await webPlayerResume();
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
