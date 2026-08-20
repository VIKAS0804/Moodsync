import { Pressable, Text, View } from 'react-native';

import type { PlaybackPreference } from '@/playback/usePlayback';

const OPTIONS: { value: PlaybackPreference; label: string; hint: string }[] = [
  { value: 'auto', label: 'Auto', hint: 'Full track if your account allows it' },
  { value: 'full', label: 'Full song', hint: 'Play through the Spotify app' },
  { value: 'preview', label: '30s', hint: 'Stay in MoodSync, quick to skim' },
];

interface Props {
  value: PlaybackPreference;
  onChange: (next: PlaybackPreference) => void;
  /** Shown when the account can't do full tracks, so "Full song" isn't a lie. */
  hasPremium: boolean;
}

export function PlaybackToggle({ value, onChange, hasPremium }: Props) {
  const active = OPTIONS.find((o) => o.value === value);

  return (
    <View className="w-full">
      <View className="flex-row rounded-xl border border-ink-600 bg-ink-800/60 p-1">
        {OPTIONS.map((option) => {
          const selected = option.value === value;
          return (
            <Pressable
              key={option.value}
              onPress={() => onChange(option.value)}
              className={`flex-1 items-center rounded-lg py-2 ${
                selected ? 'bg-ink-600' : ''
              } active:opacity-70`}
              accessibilityRole="button"
              accessibilityState={{ selected }}
            >
              <Text
                className={`text-xs font-semibold ${
                  selected ? 'text-slate-100' : 'text-slate-500'
                }`}
              >
                {option.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <Text className="mt-1 text-center text-[10px] text-slate-600">
        {value === 'full' && !hasPremium
          ? 'Full tracks need Spotify Premium — this will fall back to a preview'
          : active?.hint}
      </Text>
    </View>
  );
}
