/**
 * Session storage + the Spotify PKCE login flow.
 *
 * The device never sees a Spotify token: it does the PKCE dance, ships the
 * authorization code to the backend, and gets back an opaque MoodSync session
 * token. SecureStore isn't available on web, so that falls back to localStorage.
 */

import * as AuthSession from 'expo-auth-session';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

import type { AuthSession as MoodSyncSession, SpotifyConfig } from '@/api/types';

const SESSION_KEY = 'moodsync.session_token';

let cachedToken: string | null | undefined;

const webStorage = {
  getItem: (key: string) =>
    typeof localStorage === 'undefined' ? null : localStorage.getItem(key),
  setItem: (key: string, value: string) => {
    if (typeof localStorage !== 'undefined') localStorage.setItem(key, value);
  },
  removeItem: (key: string) => {
    if (typeof localStorage !== 'undefined') localStorage.removeItem(key);
  },
};

export async function getSessionToken(): Promise<string | null> {
  if (cachedToken !== undefined) return cachedToken;
  cachedToken =
    Platform.OS === 'web'
      ? webStorage.getItem(SESSION_KEY)
      : await SecureStore.getItemAsync(SESSION_KEY);
  return cachedToken;
}

export async function setSessionToken(token: string): Promise<void> {
  cachedToken = token;
  if (Platform.OS === 'web') {
    webStorage.setItem(SESSION_KEY, token);
  } else {
    await SecureStore.setItemAsync(SESSION_KEY, token);
  }
}

export async function clearSession(): Promise<void> {
  cachedToken = null;
  if (Platform.OS === 'web') {
    webStorage.removeItem(SESSION_KEY);
  } else {
    await SecureStore.deleteItemAsync(SESSION_KEY);
  }
}

/**
 * The redirect URI this build will hand Spotify.
 *
 * Spotify rejects any redirect it hasn't been told about, with
 * "redirect_uri: Not matching configuration". In Expo Go this resolves to
 * `exp://<lan-ip>:8081/--/callback`, which contains the dev machine's IP and so
 * changes with the network — it has to be registered in the Spotify dashboard
 * and re-registered when the IP moves. A standalone build gets the stable
 * `moodsync://callback` instead. Exported so the UI can show the exact string
 * to paste rather than making the user guess it.
 */
export function spotifyRedirectUri(): string {
  return AuthSession.makeRedirectUri({ scheme: 'moodsync', path: 'callback' });
}

/**
 * Run Authorization Code + PKCE against Spotify, then exchange the code
 * server-side. Returns the MoodSync session, or null if the user cancelled.
 */
export async function loginWithSpotify(
  config: SpotifyConfig,
  exchange: (code: string, verifier: string, redirectUri: string) => Promise<MoodSyncSession>,
): Promise<MoodSyncSession | null> {
  const redirectUri = spotifyRedirectUri();

  const request = new AuthSession.AuthRequest({
    clientId: config.client_id,
    scopes: config.scopes.split(' ').filter(Boolean),
    redirectUri,
    responseType: AuthSession.ResponseType.Code,
    usePKCE: true,
  });

  const result = await request.promptAsync({ authorizationEndpoint: config.authorize_endpoint });

  if (result.type !== 'success' || !result.params.code) {
    if (result.type === 'error') {
      throw new Error(result.params.error_description ?? 'Spotify sign-in failed.');
    }
    return null;
  }
  if (!request.codeVerifier) {
    throw new Error('PKCE verifier missing; cannot complete sign-in.');
  }

  const session = await exchange(result.params.code, request.codeVerifier, redirectUri);
  await setSessionToken(session.session_token);
  return session;
}
