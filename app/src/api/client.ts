import axios, { AxiosError } from 'axios';
import Constants from 'expo-constants';
import { Platform } from 'react-native';

import { getSessionToken, clearSession } from '@/auth/session';

/**
 * Resolve the backend URL.
 *
 * A physical device can't reach `localhost` -- that's the phone itself -- so in
 * development we fall back to the host machine's LAN IP, which Expo already
 * knows because it served the bundle from there.
 */
function resolveBaseUrl(): string {
  const configured = process.env.EXPO_PUBLIC_API_URL;
  if (configured) return configured.replace(/\/$/, '');

  const hostUri =
    Constants.expoConfig?.hostUri ??
    (Constants.expoGoConfig as { debuggerHost?: string } | undefined)?.debuggerHost;
  const lanHost = hostUri?.split(':')[0];
  if (lanHost) return `http://${lanHost}:8000`;

  // Android emulator maps the host loopback to 10.0.2.2.
  return Platform.OS === 'android' ? 'http://10.0.2.2:8000' : 'http://localhost:8000';
}

export const API_BASE_URL = resolveBaseUrl();

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.request.use(async (config) => {
  const token = await getSessionToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    // A dead session token should log the user out rather than leaving the UI
    // stuck retrying a request that can never succeed.
    if (error.response?.status === 401) {
      await clearSession();
    }
    return Promise.reject(error);
  },
);

/** Turn an axios failure into something worth showing a user. */
export function describeError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: string } | undefined)?.detail;
    if (detail) return detail;
    if (error.code === 'ECONNABORTED') return 'The server took too long to respond.';
    if (!error.response) return `Can't reach the backend at ${API_BASE_URL}.`;
    return `Request failed (${error.response.status}).`;
  }
  return error instanceof Error ? error.message : 'Something went wrong.';
}
