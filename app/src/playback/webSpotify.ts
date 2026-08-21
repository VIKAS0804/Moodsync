/**
 * Spotify Web Playback SDK.
 *
 * This is the only route to *full-track playback with transport control* that
 * Spotify licenses to third parties. The SDK turns the page into a Spotify
 * playback device, so audio comes from Spotify's own player while play/pause,
 * seek and position all work locally — an actual music player rather than a
 * deep link that dumps the user into the Spotify app.
 *
 * Constraints worth knowing before relying on it:
 * - **Web only.** There is no React Native build. On a device the equivalent is
 *   the App Remote SDK, which needs a custom dev client.
 * - **Premium only.** Spotify refuses to create a player for free accounts.
 * - Needs a real Spotify access token in the browser, which is why
 *   `/auth/spotify/playback-token` exists.
 */

import { Platform } from 'react-native';

const SDK_URL = 'https://sdk.scdn.co/spotify-player.js';
const SDK_READY_TIMEOUT_MS = 15_000;

export interface WebPlayerState {
  positionMs: number;
  durationMs: number;
  paused: boolean;
  trackUri: string | null;
}

interface SpotifyPlayer {
  connect(): Promise<boolean>;
  disconnect(): void;
  addListener(event: string, cb: (payload: unknown) => void): boolean;
  getCurrentState(): Promise<unknown>;
  pause(): Promise<void>;
  resume(): Promise<void>;
  seek(positionMs: number): Promise<void>;
  setVolume(value: number): Promise<void>;
  /** Unblocks the SDK's audio element. Must be called from a user gesture. */
  activateElement?(): Promise<void>;
}

export const webPlaybackSupported = Platform.OS === 'web';

let sdkPromise: Promise<void> | null = null;
let player: SpotifyPlayer | null = null;
let deviceId: string | null = null;

/** Inject the SDK script once and resolve when it announces itself ready. */
function loadSdk(): Promise<void> {
  if (!webPlaybackSupported) return Promise.reject(new Error('web only'));
  if (sdkPromise) return sdkPromise;

  sdkPromise = new Promise<void>((resolve, reject) => {
    const w = window as unknown as Record<string, unknown>;
    if (w.Spotify) {
      resolve();
      return;
    }

    const timer = setTimeout(
      () => reject(new Error('Spotify SDK did not load (blocked or offline?)')),
      SDK_READY_TIMEOUT_MS,
    );

    // The SDK calls this global when it's ready; it must exist before the
    // script finishes parsing.
    w.onSpotifyWebPlaybackSDKReady = () => {
      clearTimeout(timer);
      resolve();
    };

    const existing = document.querySelector(`script[src="${SDK_URL}"]`);
    if (!existing) {
      const script = document.createElement('script');
      script.src = SDK_URL;
      script.async = true;
      script.onerror = () => {
        clearTimeout(timer);
        reject(new Error('Failed to fetch the Spotify SDK'));
      };
      document.head.appendChild(script);
    }
  });

  return sdkPromise;
}

/**
 * Create (once) and connect the player.
 *
 * `getToken` is called by the SDK whenever it needs a fresh token, so token
 * refresh is handled by re-asking the backend rather than reconnecting.
 */
export async function connectWebPlayer(
  getToken: () => Promise<string>,
  onStateChange?: (state: WebPlayerState | null) => void,
): Promise<string> {
  await loadSdk();
  if (deviceId && player) return deviceId;

  const w = window as unknown as { Spotify: { Player: new (opts: object) => SpotifyPlayer } };

  player = new w.Spotify.Player({
    name: 'MoodSync',
    volume: 0.8,
    getOAuthToken: (cb: (token: string) => void) => {
      getToken()
        .then(cb)
        .catch(() => undefined);
    },
  });

  const ready = new Promise<string>((resolve, reject) => {
    player!.addListener('ready', (payload) => {
      deviceId = (payload as { device_id: string }).device_id;
      resolve(deviceId);
    });
    // Spotify reports auth/account problems through these rather than throwing.
    for (const event of ['initialization_error', 'authentication_error', 'account_error']) {
      player!.addListener(event, (payload) => {
        reject(new Error((payload as { message?: string }).message ?? event));
      });
    }
  });

  player.addListener('player_state_changed', (payload) => {
    if (!onStateChange) return;
    if (!payload) {
      onStateChange(null);
      return;
    }
    const s = payload as {
      position: number;
      duration: number;
      paused: boolean;
      track_window?: { current_track?: { uri?: string } };
    };
    onStateChange({
      positionMs: s.position,
      durationMs: s.duration,
      paused: s.paused,
      trackUri: s.track_window?.current_track?.uri ?? null,
    });
  });

  await player.connect();
  return ready;
}

/**
 * Start a track on our device.
 *
 * The SDK can control playback but can't choose what plays, so this goes
 * through the Web API with `device_id` pointing at the page's player.
 */
export async function playTrackOnWebPlayer(uri: string, token: string): Promise<void> {
  if (!deviceId) throw new Error('Web player is not ready yet');
  const response = await fetch(
    `https://api.spotify.com/v1/me/player/play?device_id=${encodeURIComponent(deviceId)}`,
    {
      method: 'PUT',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ uris: [uri] }),
    },
  );
  if (!response.ok && response.status !== 204) {
    throw new Error(`Spotify refused playback (${response.status})`);
  }
}

export const webPlayerReady = () => Boolean(player && deviceId);

let elementActivated = false;

/**
 * Unblock the SDK's audio element.
 *
 * Browsers refuse to emit audio from an element that was never started by a
 * user gesture. Spotify's API will happily report `is_playing: true` while the
 * page stays silent, which looks exactly like playback being broken. Worse, the
 * slider commits on a 350ms debounce, so by the time `play()` runs the gesture
 * context is gone -- the call has to be made from the tap itself.
 *
 * Safe and cheap to call repeatedly; only the first one matters.
 */
export async function activateWebPlayerElement(): Promise<void> {
  if (elementActivated || !player?.activateElement) return;
  try {
    await player.activateElement();
    elementActivated = true;
  } catch {
    // Older SDK build, or already activated. Playback may still work.
  }
}

export async function webPlayerPause() {
  await player?.pause();
}
export async function webPlayerResume() {
  await player?.resume();
}
export async function webPlayerSeek(positionMs: number) {
  await player?.seek(Math.max(0, Math.round(positionMs)));
}

export async function webPlayerPosition(): Promise<WebPlayerState | null> {
  if (!player) return null;
  const raw = (await player.getCurrentState()) as {
    position: number;
    duration: number;
    paused: boolean;
    track_window?: { current_track?: { uri?: string } };
  } | null;
  if (!raw) return null;
  return {
    positionMs: raw.position,
    durationMs: raw.duration,
    paused: raw.paused,
    trackUri: raw.track_window?.current_track?.uri ?? null,
  };
}

export function disconnectWebPlayer() {
  player?.disconnect();
  player = null;
  deviceId = null;
}
