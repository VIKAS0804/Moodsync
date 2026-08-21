/**
 * OS-level media controls via the Media Session API.
 *
 * Ported from VibeScape, and a bigger deal for this app than it looks: MoodSync
 * is built for driving and the gym, where the phone is in a pocket or a mount.
 * Registering here puts the track on the lock screen, in the notification
 * shade, and on Bluetooth / steering-wheel / AirPods buttons — so skipping or
 * pausing doesn't require looking at the screen at all.
 *
 * Web-only (`navigator.mediaSession`). On a native build the equivalent comes
 * from the audio session itself, so this is a no-op there rather than a
 * different implementation.
 */

import { Platform } from 'react-native';

export interface MediaSessionTrack {
  title: string;
  artist: string;
  album?: string | null;
  artworkUrl?: string | null;
}

export interface MediaSessionHandlers {
  onPlay?: () => void;
  onPause?: () => void;
  /** "Next" means "another track at this mood", which is the app's real verb. */
  onNextTrack?: () => void;
  onSeekBackward?: () => void;
  onSeekForward?: () => void;
  onSeekTo?: (positionMs: number) => void;
}

interface MediaSessionLike {
  metadata: unknown;
  playbackState: string;
  setActionHandler(action: string, handler: ((details?: unknown) => void) | null): void;
  setPositionState?(state: { duration: number; position: number; playbackRate: number }): void;
}

function session(): MediaSessionLike | null {
  if (Platform.OS !== 'web' || typeof navigator === 'undefined') return null;
  return (navigator as unknown as { mediaSession?: MediaSessionLike }).mediaSession ?? null;
}

export const mediaSessionSupported = () => session() !== null;

export function setMediaSessionTrack(track: MediaSessionTrack | null): void {
  const ms = session();
  if (!ms) return;

  if (!track) {
    ms.metadata = null;
    return;
  }

  const MediaMetadata = (window as unknown as { MediaMetadata?: new (init: object) => unknown })
    .MediaMetadata;
  if (!MediaMetadata) return;

  ms.metadata = new MediaMetadata({
    title: track.title,
    artist: track.artist,
    album: track.album ?? 'MoodSync',
    // Several sizes because different surfaces pick different ones; the iTunes
    // artwork URL is already requested at 600px.
    artwork: track.artworkUrl
      ? [
          { src: track.artworkUrl, sizes: '512x512', type: 'image/jpeg' },
          { src: track.artworkUrl, sizes: '256x256', type: 'image/jpeg' },
        ]
      : [],
  });
}

export function setMediaSessionPlaying(isPlaying: boolean): void {
  const ms = session();
  if (ms) ms.playbackState = isPlaying ? 'playing' : 'paused';
}

/** Tell the OS where we are, so scrubbers outside the app track correctly. */
export function setMediaSessionPosition(positionMs: number, durationMs: number): void {
  const ms = session();
  if (!ms?.setPositionState || durationMs <= 0) return;
  try {
    ms.setPositionState({
      duration: durationMs / 1000,
      position: Math.min(positionMs, durationMs) / 1000,
      playbackRate: 1,
    });
  } catch {
    // Chrome throws if position > duration during a track change; harmless.
  }
}

export function registerMediaSessionHandlers(handlers: MediaSessionHandlers): () => void {
  const ms = session();
  if (!ms) return () => undefined;

  const bind = (action: string, handler?: (details?: unknown) => void) => {
    try {
      ms.setActionHandler(action, handler ?? null);
    } catch {
      // Unsupported actions throw rather than no-op in some browsers.
    }
  };

  bind('play', handlers.onPlay);
  bind('pause', handlers.onPause);
  bind('nexttrack', handlers.onNextTrack);
  bind('seekbackward', handlers.onSeekBackward);
  bind('seekforward', handlers.onSeekForward);
  bind('seekto', (details) => {
    const seekTime = (details as { seekTime?: number } | undefined)?.seekTime;
    if (typeof seekTime === 'number') handlers.onSeekTo?.(seekTime * 1000);
  });

  return () => {
    for (const action of [
      'play',
      'pause',
      'nexttrack',
      'seekbackward',
      'seekforward',
      'seekto',
    ]) {
      bind(action, undefined);
    }
  };
}
