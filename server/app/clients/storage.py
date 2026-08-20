"""Preview-clip cache.

Previews are re-fetchable, but re-downloading 30s of audio for every re-scoring
run is wasteful and hammers Apple's CDN. Clips land in S3 when a bucket is
configured, and on local disk otherwise, so the pipeline runs the same way on a
laptop and in AWS.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import Settings

log = logging.getLogger(__name__)


class PreviewCache:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.local_dir = Path(settings.preview_cache_dir)
        self._s3 = None

    @property
    def backend(self) -> str:
        return "s3" if self.settings.s3_configured else "local"

    def _client(self):
        if self._s3 is None:
            import boto3  # imported lazily so local runs don't pay the import cost

            kwargs = {"region_name": self.settings.aws_region}
            if self.settings.s3_endpoint_url:
                kwargs["endpoint_url"] = self.settings.s3_endpoint_url
            self._s3 = boto3.client("s3", **kwargs)
        return self._s3

    @staticmethod
    def _key(isrc: str) -> str:
        return f"previews/{isrc}.m4a"

    def get(self, isrc: str) -> bytes | None:
        if self.settings.s3_configured:
            from botocore.exceptions import ClientError

            try:
                obj = self._client().get_object(
                    Bucket=self.settings.s3_preview_bucket, Key=self._key(isrc)
                )
                return obj["Body"].read()
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in ("NoSuchKey", "404"):
                    return None
                log.warning("S3 preview fetch failed for %s: %s", isrc, code)
                return None
        path = self.local_dir / f"{isrc}.m4a"
        return path.read_bytes() if path.is_file() else None

    def put(self, isrc: str, data: bytes) -> str:
        if self.settings.s3_configured:
            self._client().put_object(
                Bucket=self.settings.s3_preview_bucket,
                Key=self._key(isrc),
                Body=data,
                ContentType="audio/mp4",
            )
            return f"s3://{self.settings.s3_preview_bucket}/{self._key(isrc)}"
        self.local_dir.mkdir(parents=True, exist_ok=True)
        path = self.local_dir / f"{isrc}.m4a"
        path.write_bytes(data)
        return str(path)

    def local_path(self, isrc: str, data: bytes) -> Path:
        """librosa needs a real file on disk; materialise the clip in a temp/local dir."""
        self.local_dir.mkdir(parents=True, exist_ok=True)
        path = self.local_dir / f"{isrc}.m4a"
        if not path.is_file():
            path.write_bytes(data)
        return path
