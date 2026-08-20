import Slider from '@react-native-community/slider';
import { useEffect, useRef, useState } from 'react';
import { Text, View } from 'react-native';

import { moodColor, moodTheme } from '@/lib/mood';

interface Props {
  value: number;
  onChange: (score: number) => void;
  /** Fired once the user settles on a value -- this is what triggers a fetch. */
  onCommit: (score: number) => void;
  disabled?: boolean;
}

/**
 * The whole interface, really: one 1-100 track from calm to hyper.
 *
 * Dragging updates the label continuously but only commits after a short pause.
 * Without that debounce a single drag from 20 to 80 would fire ~60 requests and
 * start ~60 songs.
 */
export function MoodSlider({ value, onChange, onCommit, disabled }: Props) {
  const [dragging, setDragging] = useState(false);
  const commitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (commitTimer.current) clearTimeout(commitTimer.current);
    },
    [],
  );

  const scheduleCommit = (score: number) => {
    if (commitTimer.current) clearTimeout(commitTimer.current);
    commitTimer.current = setTimeout(() => onCommit(score), 350);
  };

  const theme = moodTheme(value);
  const color = moodColor(value);

  return (
    <View className="w-full items-center">
      <Text
        className="font-mono text-8xl font-bold tabular-nums"
        style={{ color }}
        accessibilityLiveRegion="polite"
      >
        {Math.round(value)}
      </Text>
      <Text className="mt-1 text-2xl font-semibold" style={{ color }}>
        {theme.label}
      </Text>
      <Text className="mt-1 text-sm text-slate-400">{theme.hint}</Text>

      <Slider
        style={{ width: '100%', height: 64, marginTop: 28 }}
        minimumValue={1}
        maximumValue={100}
        step={1}
        value={value}
        disabled={disabled}
        minimumTrackTintColor={color}
        maximumTrackTintColor="#27334F"
        thumbTintColor={color}
        onSlidingStart={() => setDragging(true)}
        onValueChange={(next) => {
          onChange(next);
          if (dragging) scheduleCommit(next);
        }}
        onSlidingComplete={(next) => {
          setDragging(false);
          onChange(next);
          scheduleCommit(next);
        }}
        accessibilityLabel="Mood slider, 1 is calm and 100 is hyper"
      />

      <View className="w-full flex-row justify-between px-1">
        <Text className="text-xs uppercase tracking-widest text-slate-500">Calm</Text>
        <Text className="text-xs uppercase tracking-widest text-slate-500">Hyper</Text>
      </View>
    </View>
  );
}
