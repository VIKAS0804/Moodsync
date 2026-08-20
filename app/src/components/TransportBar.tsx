import Slider from '@react-native-community/slider';
import { Pressable, Text, View } from 'react-native';

function clock(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '0:00';
  const total = Math.floor(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

interface Props {
  positionMs: number;
  durationMs: number;
  isPlaying: boolean;
  seekable: boolean;
  colour: string;
  onTogglePlay: () => void;
  onSeek: (positionMs: number) => void;
  onNudge: (deltaMs: number) => void;
}

/**
 * Play/pause, a scrubbable progress bar, and -10s/+10s.
 *
 * Disabled rather than hidden when playback isn't seekable (deep-link handoff to
 * the Spotify app), so the controls don't appear and vanish between tracks --
 * and so it's visible *that* transport is unavailable, not just missing.
 */
export function TransportBar({
  positionMs,
  durationMs,
  isPlaying,
  seekable,
  colour,
  onTogglePlay,
  onSeek,
  onNudge,
}: Props) {
  const dim = seekable ? colour : '#475569';
  const known = durationMs > 0;

  return (
    <View className="w-full">
      <Slider
        style={{ width: '100%', height: 34 }}
        minimumValue={0}
        maximumValue={known ? durationMs : 1}
        value={known ? Math.min(positionMs, durationMs) : 0}
        disabled={!seekable || !known}
        minimumTrackTintColor={dim}
        maximumTrackTintColor="#27334F"
        thumbTintColor={dim}
        onSlidingComplete={(value) => onSeek(value)}
        accessibilityLabel="Track position"
      />

      <View className="-mt-1 flex-row justify-between px-1">
        <Text className="font-mono text-[10px] text-slate-500">{clock(positionMs)}</Text>
        <Text className="font-mono text-[10px] text-slate-500">
          {known ? clock(durationMs) : '--:--'}
        </Text>
      </View>

      <View className="mt-2 flex-row items-center justify-center gap-4">
        <Pressable
          onPress={() => onNudge(-10_000)}
          disabled={!seekable}
          className="rounded-lg border border-ink-600 px-3 py-2 active:opacity-70"
          accessibilityRole="button"
          accessibilityLabel="Back 10 seconds"
        >
          <Text className={seekable ? 'text-xs text-slate-300' : 'text-xs text-slate-600'}>
            -10s
          </Text>
        </Pressable>

        <Pressable
          onPress={onTogglePlay}
          className="h-12 w-12 items-center justify-center rounded-full active:opacity-70"
          style={{ backgroundColor: `${colour}26`, borderColor: colour, borderWidth: 1 }}
          accessibilityRole="button"
          accessibilityLabel={isPlaying ? 'Pause' : 'Play'}
        >
          <Text style={{ color: colour }} className="text-base font-bold">
            {isPlaying ? '❙❙' : '▶'}
          </Text>
        </Pressable>

        <Pressable
          onPress={() => onNudge(10_000)}
          disabled={!seekable}
          className="rounded-lg border border-ink-600 px-3 py-2 active:opacity-70"
          accessibilityRole="button"
          accessibilityLabel="Forward 10 seconds"
        >
          <Text className={seekable ? 'text-xs text-slate-300' : 'text-xs text-slate-600'}>
            +10s
          </Text>
        </Pressable>
      </View>
    </View>
  );
}
