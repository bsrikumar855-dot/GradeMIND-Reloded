"""Storage against a real S3-compatible server (MinIO). Skipped unless GRADEMIND_TEST_S3_ENDPOINT is set; CI starts MinIO."""

from __future__ import annotations

import io
import os
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from grademind_core.config import Env, Settings
from grademind_core.storage import InvalidObjectKeyError, ObjectKind, ObjectStore, check_key

ENDPOINT = os.environ.get("GRADEMIND_TEST_S3_ENDPOINT")
pytestmark = pytest.mark.skipif(not ENDPOINT, reason="GRADEMIND_TEST_S3_ENDPOINT not set (storage tests need MinIO)")


def settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "env": Env.TEST,
        "s3_endpoint_url": ENDPOINT,
        "s3_access_key": os.environ.get("GRADEMIND_TEST_S3_ACCESS_KEY", ""),
        "s3_secret_key": os.environ.get("GRADEMIND_TEST_S3_SECRET_KEY", ""),
        "s3_bucket": "gm-test",
        "signed_url_ttl_seconds": 120,
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def store() -> ObjectStore:
    s = ObjectStore(settings())
    s.ensure_bucket()
    s.ensure_bucket()  # idempotent
    return s


def fetch(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - test-controlled http URL
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def test_put_uses_uuid_keys_and_round_trips(store: ObjectStore) -> None:
    data = b"%PDF-1.7 test"
    k1 = store.put(ObjectKind.SUBMISSION_SOURCE, io.BytesIO(data), len(data), "application/pdf")
    k2 = store.put(ObjectKind.SUBMISSION_SOURCE, io.BytesIO(data), len(data), "application/pdf")
    assert k1 != k2 and check_key(k1) == k1 and k1.startswith("submission-source/")
    assert store.get_bytes(k1) == data and store.exists(k1)
    assert not store.exists("page-image/" + "0" * 32)


def test_signed_url_works_is_short_lived_and_unforgeable(store: ObjectStore) -> None:
    data = b"\x89PNG\r\n\x1a\nimage"
    key = store.put(ObjectKind.PAGE_IMAGE, io.BytesIO(data), len(data), "image/png")
    url = store.signed_url(key)
    assert fetch(url) == (200, data)
    assert parse_qs(urlsplit(url).query)["X-Amz-Expires"] == ["120"]
    plain = urlsplit(url)._replace(query="").geturl()
    assert fetch(plain)[0] == 403  # the bucket is private: no signature, no object
    q = parse_qs(urlsplit(url).query)
    tampered = url.replace(q["X-Amz-Signature"][0], "0" * 64)
    assert fetch(tampered)[0] == 403
    other = store.put(ObjectKind.PAGE_IMAGE, io.BytesIO(b"x"), 1, "image/png")
    assert fetch(url.replace(key, other))[0] == 403  # a signature is bound to one key


def test_signed_url_is_signed_for_the_public_host() -> None:
    s = ObjectStore(settings(s3_public_endpoint_url="https://files.example.edu"))
    url = s.signed_url("page-image/" + "a" * 32)  # presigning is local; no network call
    assert urlsplit(url).netloc == "files.example.edu" and urlsplit(url).scheme == "https"


@pytest.mark.parametrize(
    "key",
    ["../etc/passwd", "submission-source/../../x", "submission-source/" + "A" * 32, "other/" + "a" * 32, "", "page-image/"],
)
def test_keys_not_issued_by_the_module_are_refused(store: ObjectStore, key: str) -> None:
    with pytest.raises(InvalidObjectKeyError):
        store.get_bytes(key)
    with pytest.raises(InvalidObjectKeyError):
        store.signed_url(key)
