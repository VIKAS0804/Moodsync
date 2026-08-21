/**
 * Spotify playback handoff.
 *
 * You cannot stream Spotify audio inside your own app -- that's a licensing
 * restriction, not a technical gap. Two escape hatches exist, in preference
 * order:
 *
 * 1. App Remote SDK: your app sends playback *commands* to the installed
 *    Spotify app. Needs a native module and a custom dev client, so it is
 *    loaded optionally here and simply reports unavailable in Expo Go.
 * 2. Deep link to `spotify:track:<id>`: works today with no native module at
 *    all, plays the full track, but hands the user over to the Spotify UI.
 *
 * Anything below that falls through to the 30s preview player.
 */

import { Linking, Platform } from 'react-native';

export type SpotifyRemoteStatus = 'connected' | 'unavailable' | 'not_installed';

interface SpotifyRemoteModule {
  isConnectedAsync(): Promise<boolean>;
  connectRemote(): Promise<void>;
  playUri(uri: string): Promise<void>;
  pause(): Promise<void>;
  resume(): Promise<void>;
}

/**
 * `react-native-spotify-remote` is intentionally not a dependency: adding it
 * forces every contributor into a native build just to run the slider. Resolve
 * it at runtime so a dev client that *does* include it gets full playback.
 */
function loadRemote(): SpotifyRemoteModule | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod = require('react-native-spotify-remote');
    return (mod?.remote ?? null) as SpotifyRemoteModule | null;
  } catch {
    return null;
  }
}

export const spotifyRemoteAvailable = (): boolean => loadRemote() !== null;

/**
 * Whether iOS will even answer "is Spotify installed?".
 *
 * `canOpenURL` only reports truthfully for schemes listed in the app's
 * `LSApplicationQueriesSchemes`. We declare `spotify` in app.json, but **Expo Go
 * runs under its own Info.plist**, so in Expo Go the answer is always false --
 * whether or not Spotify is installed. Treating that as "not installed" is what
 * made full playback report "couldn't reach Spotify" on a phone that had it.
 *
 * So `canOpenURL` is only ever used as a positive signal, never as a veto.
 */
export async function isSpotifyInstalled(): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  try {
    return await Linking.canOpenURL('spotify:track:0');
  } catch {
    return false;
  }
}

/** Whether full playback can happen *without* leaving the app. */
export const appRemoteAvailable = () => Platform.OS !== 'web' && loadRemote() !== null;

/**
 * Play a full track through the App Remote SDK, in-app.
 *
 * Returns false when the native module isn't present, which is always the case
 * in Expo Go. Kept separate from the deep link on purpose: one keeps the
 * listener here, the other doesn't, and they are not interchangeable.
 */
export async function playViaAppRemote(uri: string): Promise<boolean> {
  const remote = loadRemote();
  if (!remote) return false;
  try {
    if (!(await remote.isConnectedAsync())) {
      await remote.connectRemote();
    }
    await remote.playUri(uri);
    return true;
  } catch {
    return false;
  }
}

/**
 * Hand the track to the Spotify app.
 *
 * This *leaves MoodSync*, which is why it is never used automatically: the whole
 * product is nudging a slider mid-song, and you can't do that from inside
 * Spotify. Only an explicit "Full song" request may end up here.
 *
 * Attempts the open rather than asking permission first -- `openURL` isn't
 * subject to the LSApplicationQueriesSchemes restriction that makes
 * `canOpenURL` lie inside Expo Go, and it fails cleanly if Spotify is absent.
 */
export async function openInSpotifyApp(uri: string): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  try {
    await Linking.openURL(uri);
    return true;
  } catch {
    return false;
  }
}

export async function pauseSpotify(): Promise<void> {
  await loadRemote()?.pause();
}
