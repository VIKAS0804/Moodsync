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


@lru_cache
def get_settings() -> Settings:
    return Settings()
