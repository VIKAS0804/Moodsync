import { Link } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { API_BASE_URL, describeError } from '@/api/client';
import { useLogin, useMe, useMoodMatch, useSession } from '@/api/hooks';
import type { MoodMatch } from '@/api/types';
import { MoodSlider } from '@/components/MoodSlider';
import { PlaybackToggle } from '@/components/PlaybackToggle';
import { NowPlaying } from '@/components/NowPlaying';
import { redirectUriProblem, setSessionToken, spotifyRedirectUri } from '@/auth/session';
import { moodTheme } from '@/lib/mood';
import { usePlayback, type PlaybackPreference } from '@/playback/usePlayback';

/** Keep the last few picks out of the pool so the slider doesn't loop. */
const RECENT_MEMORY = 5;

export default function MoodScreen() {
  const insets = useSafeAreaInsets();
  const [score, setScore] = useState(50);
  const [match, setMatch] = useState<MoodMatch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preference, setPreference] = useState<PlaybackPreference>('auto');
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
        await playback.play(result, preference);
      } catch (err) {
        setError(describeError(err));
        setMatch(null);
      }
    },
    [moodMatch, playback, preference],
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

      <View className="mb-3">
        <PlaybackToggle
          value={preference}
          onChange={(next) => {
            setPreference(next);
            // Re-route the current track immediately so the choice is audible.
            if (match) playback.play(match, next);
          }}
          hasPremium={Boolean(me.data?.has_premium)}
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
  const [showPaste, setShowPaste] = useState(false);
  const [token, setToken] = useState('');
  const session = useSession();
  const redirectUri = spotifyRedirectUri();
  const redirectProblem = redirectUriProblem(redirectUri);

  const useToken = async (value: string) => {
    await setSessionToken(value.trim());
    await session.refetch();
  };

  return (
    <ScrollView
      className="flex-1 bg-ink-900"
      contentContainerStyle={{ flexGrow: 1, justifyContent: 'center', padding: 32 }}
      keyboardShouldPersistTaps="handled"
    >
      <Text className="text-center text-4xl font-bold text-slate-100">MoodSync</Text>
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

      {/* Say this before the user round-trips to Spotify and gets a bare
          "redirect_uri: Not matching configuration" with no explanation. */}
      {redirectProblem ? (
        <View className="mt-3 w-full rounded-xl border border-amber-900/60 bg-amber-950/20 p-3">
          <Text className="text-[11px] text-amber-300">{redirectProblem}</Text>
          <Text selectable className="mt-2 font-mono text-[10px] text-slate-400">
            {redirectUri}
          </Text>
        </View>
      ) : null}

      <Pressable
        onPress={async () => {
          // Matches scripts/seed_demo.py, so the UI is usable with no API keys.
          setDemoPending(true);
          await useToken('demo-session-token');
          setDemoPending(false);
        }}
        className="mt-3 w-full items-center rounded-xl border border-ink-600 py-4 active:opacity-70"
        accessibilityRole="button"
      >
        <Text className="text-slate-300">
          {demoPending ? 'Loading…' : 'Try the demo library'}
        </Text>
      </Pressable>

      {/*
        In-app Spotify sign-in needs a redirect URI Spotify will accept, which in
        Expo Go is an exp:// URL containing a LAN IP that changes with the
        network. Signing in at <api>/auth/spotify/login instead does the whole
        flow server-side and prints a session token; this is where it goes.
      */}
      <Pressable onPress={() => setShowPaste((v) => !v)} className="mt-6 py-2">
        <Text className="text-center text-xs text-slate-500">
          {showPaste ? 'Hide' : 'Already signed in on the web? Paste a session token'}
        </Text>
      </Pressable>

      {showPaste ? (
        <View className="w-full">
          <TextInput
            value={token}
            onChangeText={setToken}
            placeholder="session token"
            placeholderTextColor="#64748b"
            autoCapitalize="none"
            autoCorrect={false}
            className="w-full rounded-xl border border-ink-600 bg-ink-800 px-4 py-3 font-mono text-sm text-slate-100"
          />
          <Pressable
            onPress={() => useToken(token)}
            disabled={!token.trim()}
            className="mt-2 w-full items-center rounded-xl border border-slate-500 py-3 active:opacity-70"
            accessibilityRole="button"
          >
            <Text className={token.trim() ? 'text-slate-200' : 'text-slate-600'}>Use token</Text>
          </Pressable>
          <Text className="mt-2 text-center text-[10px] text-slate-600">
            Get one from {API_BASE_URL}/auth/spotify/login
          </Text>
        </View>
      ) : null}

      {error ? (
        <View className="mt-6 w-full rounded-xl border border-rose-900/60 bg-rose-950/30 p-4">
          <Text className="text-center text-sm text-rose-300">{describeError(error)}</Text>
          {/*
            Spotify rejects unregistered redirects with "Not matching
            configuration". Showing the exact value beats making the user
            reverse-engineer it, since in Expo Go it embeds the dev machine's IP.
          */}
          <Text className="mt-3 text-[10px] text-slate-400">
            Add this exact redirect URI in your Spotify dashboard (Settings →
            Redirect URIs), or use the web login above instead:
          </Text>
          <Text selectable className="mt-1 font-mono text-[11px] text-amber-300">
            {redirectUri}
          </Text>
        </View>
      ) : null}
    </ScrollView>
  );
}
