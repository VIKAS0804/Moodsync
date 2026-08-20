/**
 * Playback controller.
 *
 * Picks the best available route for a matched track and reports which one it
 * used, so the UI can be honest about whether the user is hearing the full song
 * or a 30-second preview.
 */

import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from 'expo-audio';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { MoodMatch } from '@/api/types';
import { pauseSpotify, playViaSpotify } from '@/playback/spotify';

export type PlaybackRoute = 'spotify_remote' | 'spotify_deep_link' | 'preview' | 'none';

export interface PlaybackState {
  route: PlaybackRoute;
  isPlaying: boolean;
  /** Set when we wanted Spotify but had to settle for the preview. */
  degradedReason: string | null;
}

export function usePlayback() {
  const playerRef = useRef<AudioPlayer | null>(null);
  const [state, setState] = useState<PlaybackState>({
    route: 'none',
    isPlaying: false,
    degradedReason: null,
  });

  useEffect(() => {
    // Keep playing when the phone is on silent -- this is a music app.
    setAudioModeAsync({ playsInSilentMode: true }).catch(() => undefined);
    return () => {
      playerRef.current?.remove();
      playerRef.current = null;
    };
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
      setState({ route: 'preview', isPlaying: true, degradedReason: reason });
    },
    [stopPreview],
  );

  const play = useCallback(
    async (match: MoodMatch) => {
      stopPreview();

      if (match.playback_mode === 'spotify_remote') {
        const result = await playViaSpotify(match.track.spotify_uri);
        if (result !== 'failed') {
          setState({
            route: result === 'remote' ? 'spotify_remote' : 'spotify_deep_link',
            isPlaying: true,
            degradedReason: null,
          });
          return;
        }
      }

      const reason =
        match.playback_mode === 'spotify_remote'
          ? 'Spotify app not available - playing a 30s preview.'
          : 'Spotify Premium required for full tracks - playing a 30s preview.';

      if (match.preview_url) {
        playPreview(match.preview_url, reason);
        return;
      }

      setState({
        route: 'none',
        isPlaying: false,
        degradedReason: 'No playable audio for this track.',
      });
    },
    [playPreview, stopPreview],
  );

  const pause = useCallback(async () => {
    if (state.route === 'preview') {
      playerRef.current?.pause();
    } else if (state.route === 'spotify_remote') {
      await pauseSpotify();
    }
    setState((prev) => ({ ...prev, isPlaying: false }));
  }, [state.route]);

  const resume = useCallback(() => {
    if (state.route === 'preview') {
      playerRef.current?.play();
      setState((prev) => ({ ...prev, isPlaying: true }));
    }
  }, [state.route]);

  return { ...state, play, pause, resume };
}
