/** Mirrors server/app/schemas.py. Keep the two in step. */

export type PlaybackMode = 'spotify_remote' | 'preview_fallback';

export interface TrackOut {
  id: string;
  spotify_track_id: string;
  spotify_uri: string;
  title: string;
  artist: string;
  album: string | null;
  artwork_url: string | null;
  duration_ms: number | null;
  isrc: string | null;
}

export interface MoodMatch {
  requested_score: number;
  mood_label: string;
  track: TrackOut;
  track_score: number;
  distance: number;
  confidence: number;
  model_version: string;
  preview_url: string | null;
  playback_mode: PlaybackMode;
  pool_size: number;
  latency_ms: number;
  /** "relative" = read as a percentile of this user's library; "absolute" = face value. */
  slider_mode: 'relative' | 'absolute';
  /** The absolute score actually searched for, after any mapping. */
  absolute_target: number;
  library_mean: number | null;
  library_stddev: number | null;
}

export interface AuthSession {
  session_token: string;
  user_id: string;
  display_name: string | null;
  product: string | null;
  has_premium: boolean;
  playback_mode: PlaybackMode;
}

export interface PairClaim {
  session_token: string;
  display_name: string | null;
  has_premium: boolean;
}

export interface Me {
  user_id: string;
  display_name: string | null;
  product: string | null;
  has_premium: boolean;
  library_size: number;
  scored_tracks: number;
  last_synced_at: string | null;
}

export interface SyncResult {
  tracks_seen: number;
  tracks_added: number;
  tracks_with_isrc: number;
  isrc_coverage: number;
  queued_for_analysis: number;
  already_scored: number;
}

export interface AnalysisStatus {
  total_tracks: number;
  scored: number;
  pending: number;
  failed: number;
  skipped_no_preview: number;
  coverage: number;
  score_histogram: Record<string, number>;
}

export interface SpotifyConfig {
  client_id: string;
  redirect_uri: string;
  scopes: string;
  authorize_endpoint: string;
}
