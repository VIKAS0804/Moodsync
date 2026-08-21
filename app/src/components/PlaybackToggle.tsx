import { Platform, Pressable, Text, View } from 'react-native';

import { appRemoteAvailable } from '@/playback/spotify';
import type { PlaybackPreference } from '@/playback/usePlayback';

/**
 * Can a full track play without leaving the app? Web can (Playback SDK); a
 * phone can only with a dev build, so in Expo Go "Full song" means handing the
 * track to Spotify and losing the slider. Saying so beats surprising anyone.
 */
const fullStaysInApp = () => Platform.OS === 'web' || appRemoteAvailable();

const OPTIONS: { value: PlaybackPreference; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'full', label: 'Full song' },
  { value: 'preview', label: '30s' },
];

function hintFor(value: PlaybackPreference, hasPremium: boolean): string {
  const inApp = fullStaysInApp();
  switch (value) {
    case 'auto':
      return inApp
        ? 'Full track if your account allows it'
        : 'Previews here, so the slider stays usable';
    case 'full':
      if (!hasPremium) return 'Full tracks need Premium — this falls back to a preview';
      return inApp ? 'Plays here, with seeking' : 'Opens the Spotify app — you leave MoodSync';
    case 'preview':
      return 'Stay in MoodSync, quick to skim';
  }
}

interface Props {
  value: PlaybackPreference;
  onChange: (next: PlaybackPreference) => void;
  /** Shown when the account can't do full tracks, so "Full song" isn't a lie. */
  hasPremium: boolean;
}

export function PlaybackToggle({ value, onChange, hasPremium }: Props) {

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
        {hintFor(value, hasPremium)}
      </Text>
    </View>
  );
}
