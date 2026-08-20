/** Mood presentation. Labels mirror `scoring.describe_mood()` on the server. */

export interface MoodTheme {
  label: string;
  color: string;
  background: string;
  hint: string;
}

const BANDS: { max: number; theme: MoodTheme }[] = [
  {
    max: 20,
    theme: {
      label: 'Calm',
      color: '#38BDF8',
      background: '#0B1020',
      hint: 'Slow, quiet, barely a pulse',
    },
  },
  {
    max: 40,
    theme: {
      label: 'Mellow',
      color: '#22D3EE',
      background: '#0C1426',
      hint: 'Easy tempo, soft edges',
    },
  },
  {
    max: 60,
    theme: {
      label: 'Steady',
      color: '#A78BFA',
      background: '#0F1430',
      hint: 'Mid-tempo, keeps moving',
    },
  },
  {
    max: 80,
    theme: {
      label: 'Energetic',
      color: '#FB923C',
      background: '#160F1F',
      hint: 'Driving, bright, up-front',
    },
  },
  {
    max: 100,
    theme: {
      label: 'Hyper',
      color: '#F43F5E',
      background: '#1A0B16',
      hint: 'Fast, loud, relentless',
    },
  },
];

export function moodTheme(score: number): MoodTheme {
  const clamped = Math.min(100, Math.max(1, Math.round(score)));
  return (BANDS.find((band) => clamped <= band.max) ?? BANDS[BANDS.length - 1]).theme;
}

/** Interpolate the ramp so the colour shifts continuously, not in five jumps. */
export function moodColor(score: number): string {
  const stops = [
    { at: 1, rgb: [56, 189, 248] },
    { at: 30, rgb: [34, 211, 238] },
    { at: 50, rgb: [167, 139, 250] },
    { at: 75, rgb: [251, 146, 60] },
    { at: 100, rgb: [244, 63, 94] },
  ];
  const value = Math.min(100, Math.max(1, score));

  let lower = stops[0];
  let upper = stops[stops.length - 1];
  for (let i = 0; i < stops.length - 1; i += 1) {
    if (value >= stops[i].at && value <= stops[i + 1].at) {
      lower = stops[i];
      upper = stops[i + 1];
      break;
    }
  }

  const span = upper.at - lower.at || 1;
  const t = (value - lower.at) / span;
  const channel = (index: number) =>
    Math.round(lower.rgb[index] + (upper.rgb[index] - lower.rgb[index]) * t);

  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}
