import { ActivityIndicator, Image, Pressable, Text, View } from 'react-native';

import type { MoodMatch } from '@/api/types';
import { TransportBar } from '@/components/TransportBar';
import { moodColor } from '@/lib/mood';
import type { PlaybackRoute } from '@/playback/usePlayback';

const ROUTE_LABEL: Record<PlaybackRoute, string> = {
  spotify_web: 'Full track',
  spotify_remote: 'Full track via Spotify',
  spotify_deep_link: 'Opened in Spotify',
  preview: '30s preview',
  none: 'Not playing',
};

interface Props {
  match: MoodMatch | null;
  loading: boolean;
  error: string | null;
  route: PlaybackRoute;
  isPlaying: boolean;
  degradedReason: string | null;
  positionMs: number;
  durationMs: number;
  seekable: boolean;
  onSkip: () => void;
  onTogglePlay: () => void;
  onSeek: (positionMs: number) => void;
  onNudge: (deltaMs: number) => void;
}

export function NowPlaying({
  match,
  loading,
  error,
  route,
  isPlaying,
  degradedReason,
  positionMs,
  durationMs,
  seekable,
  onSkip,
  onTogglePlay,
  onSeek,
  onNudge,
}: Props) {
  if (loading && !match) {
    return (
      <View className="h-44 items-center justify-center">
        <ActivityIndicator color="#A78BFA" />
        <Text className="mt-3 text-slate-400">Finding a track…</Text>
      </View>
    );
  }

  if (error) {
    return (
      <View className="h-44 items-center justify-center rounded-2xl border border-rose-900/60 bg-rose-950/30 px-5">
        <Text className="text-center text-rose-300">{error}</Text>
      </View>
    );
  }

  if (!match) {
    return (
      <View className="h-44 items-center justify-center rounded-2xl border border-ink-600 bg-ink-800/50 px-5">
        <Text className="text-center text-slate-400">
          Move the slider to pick a mood.
        </Text>
      </View>
    );
  }

  const color = moodColor(match.track_score);

  return (
    <View className="w-full rounded-2xl border border-ink-600 bg-ink-800/60 p-4">
      <View className="flex-row items-center">
        {match.track.artwork_url ? (
          <Image
            source={{ uri: match.track.artwork_url }}
            className="h-16 w-16 rounded-lg"
            accessibilityIgnoresInvertColors
          />
        ) : (
          <View
            className="h-16 w-16 items-center justify-center rounded-lg"
            style={{ backgroundColor: `${color}22` }}
          >
            <Text className="font-mono text-xl font-bold" style={{ color }}>
              {match.track_score}
            </Text>
          </View>
        )}

        <View className="ml-3 flex-1">
          <Text className="text-base font-semibold text-slate-100" numberOfLines={1}>
            {match.track.title}
          </Text>
          <Text className="text-sm text-slate-400" numberOfLines={1}>
            {match.track.artist}
          </Text>
          <Text className="mt-1 text-xs text-slate-500">
            mood {match.track_score} · {match.distance} off · {ROUTE_LABEL[route]}
          </Text>
        </View>
      </View>

      {degradedReason ? (
        <Text className="mt-3 text-xs text-amber-400">{degradedReason}</Text>
      ) : null}

      <View className="mt-3">
        <TransportBar
          positionMs={positionMs}
          durationMs={durationMs}
          isPlaying={isPlaying}
          seekable={seekable}
          colour={color}
          onTogglePlay={onTogglePlay}
          onSeek={onSeek}
          onNudge={onNudge}
        />
      </View>

      <Pressable
        onPress={onSkip}
        className="mt-3 items-center rounded-xl border border-ink-600 bg-ink-700/60 py-3 active:opacity-70"
        accessibilityRole="button"
      >
        <Text className="font-semibold text-slate-300">Another like this</Text>
      </Pressable>

      <Text className="mt-3 text-center text-[10px] text-slate-600">
        {match.pool_size} scored tracks · {match.model_version} · {Math.round(match.latency_ms)}ms
      </Text>
    </View>
  );
}
