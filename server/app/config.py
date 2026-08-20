"""Runtime configuration, loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- App ---
    environment: str = "development"
    api_prefix: str = ""
    cors_origins: str = "*"

    # --- Database ---
    # Postgres in every real environment; sqlite is only used by the test suite.
    database_url: str = "postgresql+psycopg://moodsync:moodsync@localhost:5432/moodsync"

    # --- Spotify (Authorization Code + PKCE, public client: no secret on device) ---
    spotify_client_id: str = ""
    spotify_client_secret: str = ""  # only needed for confidential-client flows
    spotify_redirect_uri: str = "moodsync://callback"
    # Redirect for the server-side browser login (GET /auth/spotify/login).
    # Spotify requires HTTPS or a loopback *IP literal* -- "localhost" is
    # rejected, so this must be 127.0.0.1.
    spotify_web_redirect_uri: str = "http://127.0.0.1:8000/auth/spotify/callback"

    # --- Apple Music (developer token is signed locally with an ES256 .p8 key) ---
    apple_team_id: str = ""
    apple_key_id: str = ""
    apple_private_key_path: str = ""
    apple_private_key: str = ""  # inline PEM alternative, for container deploys
    apple_storefront: str = "us"

    # --- AWS (preview-clip cache) ---
    aws_region: str = "us-east-1"
    s3_preview_bucket: str = ""
    s3_endpoint_url: str = ""  # set for LocalStack / MinIO

    # --- Pipeline ---
    preview_cache_dir: str = ".cache/previews"
    analysis_concurrency: int = 4
    # Tracks with no ISRC match on Apple Music: "exclude" or "fuzzy"
    unmatched_track_policy: str = "fuzzy"
    # Where preview clips come from:
    #   auto        - Apple Music catalog if configured, else iTunes Search
    #   apple_music - exact ISRC matching only (needs a developer token)
    #   itunes      - credential-free text search only
    preview_source: str = "auto"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def apple_music_configured(self) -> bool:
        has_key = bool(self.apple_private_key or self.apple_private_key_path)
        return bool(self.apple_team_id and self.apple_key_id and has_key)

    @property
    def s3_configured(self) -> bool:
        return bool(self.s3_preview_bucket)

    @property
    def use_apple_music(self) -> bool:
        """Exact ISRC matching, when a developer token is available."""
        if self.preview_source == "itunes":
            return False
        return self.apple_music_configured

    @property
    def use_itunes_fallback(self) -> bool:
        """Credential-free previews. The pipeline can run with no Apple account."""
        if self.preview_source == "apple_music":
            return False
        return self.preview_source == "itunes" or not self.apple_music_configured

    @property
    def analysis_available(self) -> bool:
        return self.use_apple_music or self.use_itunes_fallback


@lru_cache
def get_settings() -> Settings:
    return Settings()
