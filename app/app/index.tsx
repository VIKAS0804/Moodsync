import { Link } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { describeError } from '@/api/client';
import { useLogin, useMe, useMoodMatch, useSession } from '@/api/hooks';
import type { MoodMatch } from '@/api/types';
import { MoodSlider } from '@/components/MoodSlider';
import { NowPlaying } from '@/components/NowPlaying';
import { setSessionToken } from '@/auth/session';
import { moodTheme } from '@/lib/mood';
import { usePlayback } from '@/playback/usePlayback';

/** Keep the last few picks out of the pool so the slider doesn't loop. */
const RECENT_MEMORY = 5;

export default function MoodScreen() {
  const insets = useSafeAreaInsets();
  const [score, setScore] = useState(50);
  const [match, setMatch] = useState<MoodMatch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const recent = useRef<string[]>([]);

  const session = useSession();
  const me = useMe(Boolean(session.data));
  const login = useLogin();
  const moodMatch = useMoodMatch();
  const playback = usePlayback();

  const requestTrack = useCallback(
    async (target: number) => {
      setError(null);
      try {
        const result = await moodMatch.mutateAsync({ score: target, exclude: recent.current });
        recent.current = [result.track.spotify_track_id, ...recent.current].slice(
          0,
          RECENT_MEMORY,
        );
        setMatch(result);
        await playback.play(result);
      } catch (err) {
        setError(describeError(err));
        setMatch(null);
      }
    },
    [moodMatch, playback],
  );

  if (session.isLoading) {
    return (
      <View className="flex-1 items-center justify-center bg-ink-900">
        <ActivityIndicator color="#A78BFA" />
      </View>
    );
  }

  if (!session.data) {
    return <SignIn onLogin={() => login.mutate()} pending={login.isPending} error={login.error} />;
  }

  const theme = moodTheme(score);

  return (
    <ScrollView
      className="flex-1 bg-ink-900"
      contentContainerStyle={{
        paddingTop: insets.top + 16,
        paddingBottom: insets.bottom + 24,
        paddingHorizontal: 20,
        flexGrow: 1,
        justifyContent: 'space-between',
      }}
      style={{ backgroundColor: theme.background }}
    >
      <View className="flex-row items-center justify-between">
        <View>
          <Text className="text-lg font-bold text-slate-100">MoodSync</Text>
          <Text className="text-xs text-slate-500">
            {me.data ? `${me.data.scored_tracks} of ${me.data.library_size} tracks scored` : '…'}
          </Text>
        </View>
        <Link href="/library" asChild>
          <Pressable className="rounded-lg border border-ink-600 px-3 py-2 active:opacity-70">
            <Text className="text-xs text-slate-300">Library</Text>
          </Pressable>
        </Link>
      </View>

      <View className="my-8">
        <MoodSlider
          value={score}
          onChange={setScore}
          onCommit={requestTrack}
          disabled={moodMatch.isPending}
        />
      </View>

      <NowPlaying
        match={match}
        loading={moodMatch.isPending}
        error={error}
        route={playback.route}
        isPlaying={playback.isPlaying}
        degradedReason={playback.degradedReason}
        onSkip={() => requestTrack(score)}
        onTogglePlay={() => (playback.isPlaying ? playback.pause() : playback.resume())}
      />
    </ScrollView>
  );
}

function SignIn({
  onLogin,
  pending,
  error,
}: {
  onLogin: () => void;
  pending: boolean;
  error: unknown;
}) {
  const [demoPending, setDemoPending] = useState(false);
  const session = useSession();

  return (
    <View className="flex-1 items-center justify-center bg-ink-900 px-8">
      <Text className="text-4xl font-bold text-slate-100">MoodSync</Text>
      <Text className="mt-3 text-center text-slate-400">
        One slider, calm to hyper. It picks from your own library so you never have to
        search for a song again.
      </Text>

      <Pressable
        onPress={onLogin}
        disabled={pending}
        className="mt-10 w-full items-center rounded-xl bg-emerald-500 py-4 active:opacity-80"
        accessibilityRole="button"
      >
        <Text className="font-semibold text-emerald-950">
          {pending ? 'Connecting…' : 'Continue with Spotify'}
        </Text>
      </Pressable>

      <Pressable
        onPress={async () => {
          // Matches scripts/seed_demo.py, so the UI is usable with no API keys.
          setDemoPending(true);
          await setSessionToken('demo-session-token');
          await session.refetch();
          setDemoPending(false);
        }}
        className="mt-3 w-full items-center rounded-xl border border-ink-600 py-4 active:opacity-70"
        accessibilityRole="button"
      >
        <Text className="text-slate-300">
          {demoPending ? 'Loading…' : 'Try the demo library'}
        </Text>
      </Pressable>

      {error ? (
        <Text className="mt-6 text-center text-sm text-rose-400">{describeError(error)}</Text>
      ) : null}
    </View>
  );
}
