import { useRouter } from 'expo-router';
import { ActivityIndicator, Pressable, ScrollView, Text, View } from 'react-native';

import { describeError } from '@/api/client';
import { useLogout, useMe, useSession, useSyncLibrary, useSyncStatus } from '@/api/hooks';
import { moodColor } from '@/lib/mood';

export default function LibraryScreen() {
  const router = useRouter();
  const session = useSession();
  const me = useMe(Boolean(session.data));
  const sync = useSyncLibrary();
  const logout = useLogout();

  const status = useSyncStatus(Boolean(session.data), sync.isPending);
  const pending = status.data?.pending ?? 0;

  return (
    <ScrollView className="flex-1 bg-ink-900" contentContainerStyle={{ padding: 20 }}>
      <Text className="text-sm text-slate-400">Signed in as</Text>
      <Text className="text-xl font-semibold text-slate-100">
        {me.data?.display_name ?? '—'}
      </Text>
      <Text className="mt-1 text-xs text-slate-500">
        {me.data?.has_premium
          ? 'Premium — full tracks play through Spotify'
          : 'No Premium — playback falls back to 30s previews'}
      </Text>

      <View className="mt-6 rounded-2xl border border-ink-600 bg-ink-800/60 p-4">
        <Text className="text-sm font-semibold text-slate-200">Analysis coverage</Text>
        {status.isLoading ? (
          <ActivityIndicator className="mt-4" color="#A78BFA" />
        ) : status.data ? (
          <>
            <Text className="mt-2 text-3xl font-bold text-slate-100">
              {Math.round(status.data.coverage * 100)}%
            </Text>
            <Text className="text-xs text-slate-500">
              {status.data.scored} scored · {status.data.pending} pending ·{' '}
              {status.data.skipped_no_preview} no preview · {status.data.failed} failed
            </Text>

            <View className="mt-5 flex-row items-end gap-2" style={{ height: 90 }}>
              {Object.entries(status.data.score_histogram).map(([band, count]) => {
                const max = Math.max(...Object.values(status.data!.score_histogram), 1);
                const mid = Number(band.split('-')[0]) + 10;
                return (
                  <View key={band} className="flex-1 items-center">
                    <View
                      style={{
                        height: Math.max(3, (count / max) * 64),
                        backgroundColor: moodColor(mid),
                        width: '100%',
                        borderRadius: 4,
                      }}
                    />
                    <Text className="mt-1 text-[9px] text-slate-500">{band}</Text>
                    <Text className="text-[9px] text-slate-600">{count}</Text>
                  </View>
                );
              })}
            </View>
            <Text className="mt-3 text-[10px] text-slate-600">
              A healthy library has tracks across every band — gaps mean the slider has
              nothing to reach for at that mood.
            </Text>
          </>
        ) : null}
      </View>

      <Pressable
        onPress={() => sync.mutate(200)}
        disabled={sync.isPending}
        className="mt-6 items-center rounded-xl bg-emerald-500 py-4 active:opacity-80"
        accessibilityRole="button"
      >
        <Text className="font-semibold text-emerald-950">
          {sync.isPending ? 'Syncing…' : 'Sync library from Spotify'}
        </Text>
      </Pressable>

      {sync.data ? (
        <Text className="mt-3 text-xs text-slate-400">
          {sync.data.tracks_seen} tracks seen · {sync.data.tracks_added} new ·{' '}
          {Math.round(sync.data.isrc_coverage * 100)}% had an ISRC ·{' '}
          {sync.data.queued_for_analysis} queued for analysis
        </Text>
      ) : null}
      {sync.error ? (
        <Text className="mt-3 text-xs text-rose-400">{describeError(sync.error)}</Text>
      ) : null}
      {pending > 0 ? (
        <Text className="mt-3 text-xs text-amber-400">
          {pending} tracks still being analysed in the background.
        </Text>
      ) : null}

      <Pressable
        onPress={async () => {
          await logout.mutateAsync();
          router.replace('/');
        }}
        className="mt-10 items-center rounded-xl border border-ink-600 py-3 active:opacity-70"
        accessibilityRole="button"
      >
        <Text className="text-slate-400">Sign out</Text>
      </Pressable>
    </ScrollView>
  );
}
