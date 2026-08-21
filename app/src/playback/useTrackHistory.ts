/**
 * Play history, so Previous means "the song I just heard".
 *
 * Without this, "previous" could only re-roll a new track at a nearby mood,
 * which is not what a back button means to anyone. Keeping a list also lets
 * Next walk *forward* again after going back, instead of discarding the tracks
 * you skipped past.
 *
 * The list doubles as the exclusion set sent to `/mood/{score}`: the recently
 * played ids are exactly what shouldn't come back while nudging the slider.
 */

import { useCallback, useRef, useState } from 'react';

import type { MoodMatch } from '@/api/types';

/** How many ids to exclude from matching. Beyond this the slider gets starved. */
const EXCLUDE_WINDOW = 5;
/** Cap the stack; nobody scrolls back further than this. */
const MAX_HISTORY = 50;

export function useTrackHistory() {
  const items = useRef<MoodMatch[]>([]);
  const position = useRef(-1);
  // Mirrored into state purely so the transport buttons can enable/disable.
  const [bounds, setBounds] = useState({ canGoBack: false, canGoForward: false });

  const sync = useCallback(() => {
    setBounds({
      canGoBack: position.current > 0,
      canGoForward: position.current >= 0 && position.current < items.current.length - 1,
    });
  }, []);

  /** Record a freshly fetched track, dropping anything we'd navigated past. */
  const push = useCallback(
    (match: MoodMatch) => {
      if (position.current < items.current.length - 1) {
        items.current = items.current.slice(0, position.current + 1);
      }
      items.current.push(match);
      if (items.current.length > MAX_HISTORY) {
        items.current = items.current.slice(-MAX_HISTORY);
      }
      position.current = items.current.length - 1;
      sync();
    },
    [sync],
  );

  const back = useCallback((): MoodMatch | null => {
    if (position.current <= 0) return null;
    position.current -= 1;
    sync();
    return items.current[position.current];
  }, [sync]);

  /** Forward through history if we've gone back; null means "fetch a new one". */
  const forward = useCallback((): MoodMatch | null => {
    if (position.current < 0 || position.current >= items.current.length - 1) return null;
    position.current += 1;
    sync();
    return items.current[position.current];
  }, [sync]);

  const recentIds = useCallback(
    () =>
      items.current
        .slice(-EXCLUDE_WINDOW)
        .map((m) => m.track.spotify_track_id)
        .reverse(),
    [],
  );

  const clear = useCallback(() => {
    items.current = [];
    position.current = -1;
    sync();
  }, [sync]);

  return { push, back, forward, recentIds, clear, ...bounds };
}
