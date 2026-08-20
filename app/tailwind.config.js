/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./app/**/*.{js,jsx,ts,tsx}', './src/**/*.{js,jsx,ts,tsx}'],
  presets: [require('nativewind/preset')],
  // Must be 'class', not Tailwind's default 'media'. NativeWind's web runtime
  // installs a MutationObserver that *sets* the colour scheme, and setting it
  // throws when the mode is 'media' ("Cannot manually set color scheme").
  // Nothing here uses `dark:` variants anyway -- the palette is hardcoded dark.
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Dark base: the app is built for driving and the gym, where a bright
        // screen is actively unhelpful.
        ink: {
          900: '#0B1020',
          800: '#121A2F',
          700: '#1B2540',
          600: '#27334F',
        },
        // Mood ramp, calm -> hyper. Mirrors scoring.describe_mood() on the server.
        mood: {
          calm: '#38BDF8',
          mellow: '#22D3EE',
          steady: '#A78BFA',
          energetic: '#FB923C',
          hyper: '#F43F5E',
        },
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
};
