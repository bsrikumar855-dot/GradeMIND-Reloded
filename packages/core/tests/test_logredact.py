"""D26.4: redaction of secrets in log records and structured log fields."""

from __future__ import annotations

import logging

import pytest

from grademind_core.logredact import MASK, install, redact_obj, redact_text

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMiLCJyb2xlIjoiYWRtaW4ifQ.c2lnbmF0dXJlLXZhbHVlLWhlcmU"


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("Authorization: Bearer abc.def-ghi_123", "abc.def-ghi_123"),
        ("auth header was basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        (f"token in url ?x={JWT}", JWT),
        ('{"email": "a@b.in", "password": "hunter2 with spaces"}', "hunter2 with spaces"),
        ("password=hunter2&email=a@b.in", "hunter2"),
        ("api_key: sk-live-123456", "sk-live-123456"),
        ("GRADEMIND_JWT_SECRET=s3cr3t-value", "s3cr3t-value"),
        ("https://minio/x?X-Amz-Credential=AKIA%2F1&X-Amz-Signature=deadbeef", "deadbeef"),
        ("{'token': 'abc123'}", "abc123"),
    ],
)
def test_redact_text(raw: str, secret: str) -> None:
    out = redact_text(raw)
    assert secret not in out and MASK in out


def test_redact_text_leaves_ordinary_text_alone() -> None:
    s = "upload rejected: unsupported_type (request_id=abc123, size=1024)"
    assert redact_text(s) == s


def test_redact_obj_by_key_and_value() -> None:
    obj = {
        "Authorization": "Bearer x",
        "headers": {"cookie": "gm_session=abc", "x-request-id": "r1"},
        "detail": [{"msg": f"bad token {JWT}"}],
        "access_token": "abc",
        "user": "a@b.in",
    }
    out = redact_obj(obj)
    assert out["Authorization"] == MASK and out["headers"]["cookie"] == MASK and out["access_token"] == MASK
    assert out["headers"]["x-request-id"] == "r1" and out["user"] == "a@b.in"
    assert JWT not in str(out)


def test_installed_factory_redacts_every_logger_and_tracebacks(caplog: pytest.LogCaptureFixture) -> None:
    install()
    install()  # idempotent
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("uvicorn.error").info("client sent Authorization: Bearer %s", JWT)
        logging.getLogger("some.library").warning("password=%s", "hunter2")
        try:
            raise RuntimeError(f"login failed for password: hunter2 token={JWT}")
        except RuntimeError:
            logging.getLogger("grademind.api").exception("unexpected")
    text = caplog.text
    assert "hunter2" not in text and JWT not in text and text.count(MASK) >= 3
    assert "RuntimeError" in text  # the traceback is kept, only the secrets are masked
