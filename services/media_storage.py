"""Production media storage abstraction.

The application can continue using local storage for development, while
production deployments can use any S3-compatible object store (AWS S3,
Cloudflare R2, Backblaze B2 S3 API, MinIO, etc.).

This module is intentionally provider-neutral. It does not enable billing or
require cloud credentials unless MEDIA_STORAGE=s3 is explicitly selected.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


class MediaStorageError(RuntimeError):
    pass


OBJECT_STORAGE_PROVIDERS = {"s3", "r2", "b2"}


def object_storage_configured() -> bool:
    """Return whether the configured S3-compatible backend has all credentials it needs."""
    provider = os.environ.get("MEDIA_STORAGE", "local").strip().lower()
    if provider not in OBJECT_STORAGE_PROVIDERS:
        return False
    required = ("MEDIA_S3_BUCKET", "MEDIA_S3_ACCESS_KEY", "MEDIA_S3_SECRET_KEY")
    return all(os.environ.get(key, "").strip() for key in required)


class LocalMediaStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root != path and self.root not in path.parents:
            raise MediaStorageError("Invalid media key")
        return path

    def save(self, fileobj: BinaryIO, key: str) -> str:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fileobj.save(str(path))
        return key

    def exists(self, key: str) -> bool:
        return self.path_for(key).is_file()

    def delete(self, key: str) -> None:
        try:
            self.path_for(key).unlink()
        except FileNotFoundError:
            pass

    def presigned_get_url(self, key: str, expires: int = 300) -> str:
        raise MediaStorageError("Presigned URLs are only available for object storage.")

    def open(self, key: str):
        path = self.path_for(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path


class S3MediaStorage:
    def __init__(self):
        try:
            import boto3
        except ImportError as exc:
            raise MediaStorageError("boto3 is required when MEDIA_STORAGE=s3") from exc
        self.bucket = os.environ.get("MEDIA_S3_BUCKET", "").strip()
        if not self.bucket:
            raise MediaStorageError("MEDIA_S3_BUCKET is required when MEDIA_STORAGE=s3")
        self.client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("MEDIA_S3_ENDPOINT", "").strip() or None,
            region_name=os.environ.get("MEDIA_S3_REGION", "auto").strip() or None,
            aws_access_key_id=os.environ.get("MEDIA_S3_ACCESS_KEY", "").strip() or None,
            aws_secret_access_key=os.environ.get("MEDIA_S3_SECRET_KEY", "").strip() or None,
        )
        self.prefix = os.environ.get("MEDIA_S3_PREFIX", "university-connect").strip("/")

    def key(self, key: str) -> str:
        clean = key.lstrip("/")
        return f"{self.prefix}/{clean}" if self.prefix else clean

    def upload_path(self, local_path: str | Path, key: str, content_type: str | None = None) -> str:
        extra = {"ContentType": content_type} if content_type else {}
        self.client.upload_file(str(local_path), self.bucket, self.key(key), ExtraArgs=extra)
        return key

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.key(key))
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self.key(key))

    def presigned_get_url(self, key: str, expires: int = 300) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self.key(key)},
            ExpiresIn=max(60, min(int(expires), 3600)),
        )

    def download_to(self, key: str, local_path: str | Path) -> Path:
        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, self.key(key), str(target))
        return target


def build_media_storage():
    provider = os.environ.get("MEDIA_STORAGE", "local").strip().lower()
    if provider == "auto":
        provider = "s3" if object_storage_configured() else "local"
    if provider in OBJECT_STORAGE_PROVIDERS:
        return S3MediaStorage()
    return LocalMediaStorage(os.environ.get("MEDIA_LOCAL_ROOT", "private_media/posts"))
