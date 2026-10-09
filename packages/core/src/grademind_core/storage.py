"""The single storage module (spec §16; Phase 1 step 6). The only place that imports the S3 client SDK.

- Objects are stored under server-generated UUID keys (`<kind>/<uuid>`). No user input ever reaches a key, so there is no
  path traversal by construction, and keys are re-validated on every read.
- Storage keys and endpoints are never returned by the API. Images are served via short-lived presigned GET URLs,
  signed for the public endpoint the browser can reach (`s3_public_endpoint_url`), with a TTL from the config source.
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from enum import StrEnum
from typing import BinaryIO
from urllib.parse import urlsplit

from minio import Minio
from minio.error import S3Error

from grademind_core.config import Settings


class ObjectKind(StrEnum):
    SUBMISSION_SOURCE = "submission-source"
    PAPER_SOURCE = "paper-source"
    PAGE_THUMB = "page-thumb"
    PAGE_IMAGE = "page-image"
    LINE_CROP = "line-crop"


_KEY = re.compile(r"^(" + "|".join(re.escape(k.value) for k in ObjectKind) + r")/[0-9a-f]{32}$")


class InvalidObjectKeyError(ValueError):
    pass


def new_key(kind: ObjectKind) -> str:
    return f"{kind.value}/{uuid.uuid4().hex}"


def check_key(key: str) -> str:
    if not _KEY.fullmatch(key):
        raise InvalidObjectKeyError("not a storage key issued by this module")
    return key


def _client(endpoint_url: str, settings: Settings) -> Minio:
    u = urlsplit(endpoint_url)
    if u.scheme not in ("http", "https") or not u.netloc or u.path not in ("", "/"):
        raise ValueError(f"S3 endpoint must be scheme://host[:port], got {endpoint_url!r}")
    return Minio(
        u.netloc,
        access_key=settings.s3_access_key.get_secret_value(),
        secret_key=settings.s3_secret_key.get_secret_value(),
        secure=u.scheme == "https",
        region=settings.s3_region,  # a fixed region means presigning never makes a network call
    )


class ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._ttl = timedelta(seconds=settings.signed_url_ttl_seconds)
        self._internal = _client(settings.s3_endpoint_url, settings)
        self._public = _client(settings.s3_public_endpoint_url or settings.s3_endpoint_url, settings)

    def ensure_bucket(self) -> None:
        if not self._internal.bucket_exists(bucket_name=self._bucket):
            self._internal.make_bucket(bucket_name=self._bucket)

    def put(self, kind: ObjectKind, data: BinaryIO, size: int, content_type: str) -> str:
        key = new_key(kind)
        self._internal.put_object(bucket_name=self._bucket, object_name=key, data=data, length=size, content_type=content_type)
        return key

    def get_bytes(self, key: str) -> bytes:
        resp = self._internal.get_object(bucket_name=self._bucket, object_name=check_key(key))
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def exists(self, key: str) -> bool:
        try:
            self._internal.stat_object(bucket_name=self._bucket, object_name=check_key(key))
            return True
        except S3Error as e:
            if e.code in ("NoSuchKey", "NoSuchObject"):
                return False
            raise

    def signed_url(self, key: str) -> str:
        return self._public.presigned_get_object(bucket_name=self._bucket, object_name=check_key(key), expires=self._ttl)

    def health(self) -> bool:
        return self._internal.bucket_exists(bucket_name=self._bucket)
