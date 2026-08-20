import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type {
  AnalysisStatus,
  AuthSession,
  Me,
  MoodMatch,
  SpotifyConfig,
  SyncResult,
} from '@/api/types';
import { clearSession, getSessionToken, loginWithSpotify } from '@/auth/session';

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
