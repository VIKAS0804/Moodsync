/**
 * Full-track playback by remote-controlling Spotify (Spotify Connect).
 *
 * On a phone the App Remote SDK needs a custom dev client, so the only other
 * route to a full track was deep-linking into the Spotify app — which plays the
 * song but takes the screen, and the slider is the whole product. Doing that per
 * track is unusable.
 *
 * `user-modify-playback-state` lets the backend drive any device the account has
 * awake, so MoodSync can stay on screen and treat the Spotify app as a speaker:
 * change track, pause, seek, read position.
 *
 * The one rough edge is Spotify's: it only lists devices that are awake. A
 * phone's Spotify app registers while running and drops off later, so the first
 * play may still need one deep link to wake it. After that, control is remote.
 */

import { api } from '@/api/client';

export interface ConnectState {
  isPlaying: boolean;
  positionMs: number;
  durationMs: number;
  trackUri: string | null;
  deviceName: string | null;
}

export interface ConnectDevice {
  id: string;
  name: string;
  type: string;
  is_active: boolean;
}

/** Awake devices. Empty means Spotify has to be opened once first. */
export async function connectDevices(): Promise<ConnectDevice[]> {
  const response = await api.get<{ devices: ConnectDevice[] }>('/playback/devices');
  return response.data.devices ?? [];
}

export async function connectHasDevice(): Promise<boolean> {
  try {
    return (await connectDevices()).length > 0;
  } catch {
    return false;
  }
}

/** Start a track. Throws with Spotify's reason so the UI can explain itself. */
export async function connectPlay(uri: string, deviceId?: string): Promise<void> {
  await api.post('/playback/play', { uri, device_id: deviceId ?? null });
}

export async function connectPause(): Promise<void> {
  await api.post('/playback/pause');
}

export async function connectResume(): Promise<void> {
  await api.post('/playback/resume');
}

export async function connectSeek(positionMs: number): Promise<void> {
  await api.post('/playback/seek', { position_ms: Math.max(0, Math.round(positionMs)) });
}

export async function connectState(): Promise<ConnectState | null> {
  try {
    const { data } = await api.get<{
      is_playing: boolean;
      position_ms: number;
      duration_ms: number;
      track_uri: string | null;
      device_name: string | null;
    }>('/playback/state');
    return {
      isPlaying: data.is_playing,
      positionMs: data.position_ms,
      durationMs: data.duration_ms,
      trackUri: data.track_uri,
      deviceName: data.device_name,
    };
  } catch {
    return null;
  }
}
