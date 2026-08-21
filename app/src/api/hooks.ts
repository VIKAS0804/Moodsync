import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type {
  AnalysisStatus,
  AuthSession,
  Me,
  MoodMatch,
  PairClaim,
  SpotifyConfig,
  SyncResult,
} from '@/api/types';
import { clearSession, getSessionToken, loginWithSpotify, setSessionToken } from '@/auth/session';

export const queryKeys = {
  session: ['session'] as const,
  me: ['me'] as const,
  spotifyConfig: ['spotify-config'] as const,
  syncStatus: ['sync-status'] as const,
  mood: (score: number) => ['mood', score] as const,
};

export function useSession() {
  return useQuery({
    queryKey: queryKeys.session,
    queryFn: getSessionToken,
    staleTime: Infinity,
  });
}

export function useMe(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: async () => (await api.get<Me>('/auth/me')).data,
    enabled,
    retry: false,
  });
}

export function useSyncStatus(enabled: boolean, poll = false) {
  return useQuery({
    queryKey: queryKeys.syncStatus,
    queryFn: async () => (await api.get<AnalysisStatus>('/sync/status')).data,
    enabled,
    // Analysis runs in the background, so poll while tracks are still pending.
    refetchInterval: poll ? 3000 : false,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const config = (await api.get<SpotifyConfig>('/auth/spotify/config')).data;
      return loginWithSpotify(config, async (code, codeVerifier, redirectUri) => {
        const response = await api.post<AuthSession>('/auth/spotify/callback', {
          code,
          code_verifier: codeVerifier,
          redirect_uri: redirectUri,
        });
        return response.data;
      });
    },
    onSuccess: () => queryClient.invalidateQueries(),
  });
}

/**
 * Pair this device using a code shown by the browser login.
 *
 * Avoids the mobile redirect problem entirely: in Expo Go the Spotify redirect
 * is exp://<lan-ip>:8081/--/callback, which embeds the dev machine's IP and so
 * must be registered and re-registered as the network changes. Signing in on a
 * laptop and typing six digits needs no registration at all.
 */
export function usePairDevice() {
  const queryClient = useQueryClient();
  return useMutation<PairClaim, Error, string>({
    mutationFn: async (code) => {
      const response = await api.post<PairClaim>('/auth/pair/claim', { code: code.trim() });
      await setSessionToken(response.data.session_token);
      return response.data;
    },
    onSuccess: () => queryClient.invalidateQueries(),
  });
}

/**
 * Correct a track's score.
 *
 * Ported from VibeScape's manual override, and it does double duty: the
 * correction wins over the model for this listener straight away, and it's a
 * training label for scripts/train_model.py -- drawn from this user's own
 * genres rather than a public dataset's.
 */
export function useLabelTrack() {
  const queryClient = useQueryClient();
  return useMutation<
    { score: number; model_score: number | null; mood_label: string; total_labels: number },
    Error,
    { trackId: string; score: number }
  >({
    mutationFn: async ({ trackId, score }) => {
      const response = await api.post('/mood/label', { track_id: trackId, score });
      return response.data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.me }),
  });
}

/**
 * Mint a pairing code from the session this device already has.
 *
 * The alternative -- re-running the browser login -- rotates the session token
 * and signs out whatever was already using it. Pairing a phone shouldn't log
 * out the laptop.
 */
export function useCreatePairingCode() {
  return useMutation<{ code: string; expires_in: number }, Error, void>({
    mutationFn: async () => (await api.post('/auth/pair/new')).data,
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await api.post('/auth/logout').catch(() => undefined);
      await clearSession();
    },
    onSuccess: () => queryClient.invalidateQueries(),
  });
}

export function useSyncLibrary() {
  const queryClient = useQueryClient();
  // Variables are typed explicitly: a defaulted parameter would make TanStack
  // infer `void` and reject `sync.mutate(200)`.
  return useMutation<SyncResult, Error, number>({
    mutationFn: async (maxTracks) =>
      (await api.post<SyncResult>('/sync', { max_tracks: maxTracks, analyze: true })).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.syncStatus });
      queryClient.invalidateQueries({ queryKey: queryKeys.me });
    },
  });
}

/**
 * Fetch the track for a slider position.
 *
 * `exclude` carries the recently-played ids so nudging the slider back and
 * forth doesn't keep returning the same song. Results are deliberately never
 * cached: asking for mood 60 twice should be allowed to give two songs.
 */
export function useMoodMatch() {
  return useMutation({
    mutationFn: async ({ score, exclude }: { score: number; exclude: string[] }) => {
      const response = await api.get<MoodMatch>(`/mood/${score}`, {
        params: { exclude },
        paramsSerializer: {
          // FastAPI expects repeated keys (?exclude=a&exclude=b), not a[]=…
          indexes: null,
        },
      });
      return response.data;
    },
  });
}
