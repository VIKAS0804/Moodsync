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

/**
 * Try to play a full track through Spotify. Returns how it was handled so the
 * caller can decide whether to fall back to the preview.
 */
export async function playViaSpotify(uri: string): Promise<'remote' | 'deep_link' | 'failed'> {
  if (Platform.OS === 'web') return 'failed';

  const remote = loadRemote();
  if (remote) {
    try {
      if (!(await remote.isConnectedAsync())) {
        await remote.connectRemote();
      }
      await remote.playUri(uri);
      return 'remote';
    } catch {
      // Fall through to the deep link rather than failing outright.
    }
  }

  // Attempt the open rather than asking permission first. `openURL` is not
  // subject to the LSApplicationQueriesSchemes restriction that makes
  // `canOpenURL` lie inside Expo Go, and it fails cleanly if Spotify is absent.
  try {
    await Linking.openURL(uri);
    return 'deep_link';
  } catch {
    return 'failed';
  }
}

export async function pauseSpotify(): Promise<void> {
  await loadRemote()?.pause();
}
