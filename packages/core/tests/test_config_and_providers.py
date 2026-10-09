"""Invariant tests for the single config source and provider registry (rule 7, I7, I10, I11; D18, D19)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from grademind_core.config import Env, LlmProvider, OcrProvider, Settings
from grademind_core.providers import ProviderDisabledError, ProviderRegistry

SECRET = "x" * 40


def make(**kw: object) -> Settings:
    base: dict[str, object] = {"env": Env.DEV, "jwt_secret": SECRET, "s3_access_key": "ak", "s3_secret_key": "sk"}
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def test_v1_defaults_match_d18_d19() -> None:
    s = make()
    assert s.ocr_providers_enabled == [OcrProvider.PADDLE_V6]
    assert s.ai_suggestions_enabled is False and s.auto_approve_enabled is False
    assert s.llm_providers_enabled == [] and s.llm_kill_switch is True


def test_invariant_I11_fake_provider_refused_outside_test() -> None:
    with pytest.raises(ValidationError, match="I11"):
        make(ocr_providers_enabled=[OcrProvider.FAKE])
    assert Settings(env=Env.TEST, ocr_providers_enabled=[OcrProvider.FAKE]).env == Env.TEST


def test_unlimited_ocr_cannot_be_enabled_in_v1() -> None:
    with pytest.raises(ValidationError, match="D18"):
        make(ocr_providers_enabled=[OcrProvider.PADDLE_V6, OcrProvider.UNLIMITED_OCR])


def test_invariant_I10_auto_approve_needs_benchmark() -> None:
    with pytest.raises(ValidationError, match="I10"):
        make(auto_approve_enabled=True)


def test_ai_suggestions_need_llm_and_kill_switch_off() -> None:
    with pytest.raises(ValidationError, match="D19"):
        make(ai_suggestions_enabled=True)


def test_jwt_secret_required_outside_tests() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(env=Env.PROD)


def test_storage_credentials_required_outside_tests() -> None:
    with pytest.raises(ValidationError, match="S3_ACCESS_KEY"):
        make(s3_access_key="")
    assert Settings(env=Env.TEST).s3_access_key.get_secret_value() == ""


def test_example_placeholder_secrets_are_refused() -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        make(jwt_secret="change-me-to-48-random-characters-change-me-to-48-random")
    with pytest.raises(ValidationError, match="placeholder"):
        make(s3_secret_key="change-me-too")


def test_invariant_I7_disabled_provider_factory_never_called() -> None:
    calls: list[str] = []
    reg = ProviderRegistry(make())
    reg.register_ocr(OcrProvider.TROCR_LINE, lambda: calls.append("trocr") or object())
    reg.register_ocr(OcrProvider.PADDLE_V6, lambda: calls.append("paddle") or object())
    reg.register_llm(LlmProvider.LOCAL_QWEN, lambda: calls.append("qwen") or object())
    with pytest.raises(ProviderDisabledError):
        reg.ocr(OcrProvider.TROCR_LINE)
    with pytest.raises(ProviderDisabledError, match="kill switch"):
        reg.llm(LlmProvider.LOCAL_QWEN)
    assert calls == []  # spy: disabled factories were never invoked
    reg.ocr(OcrProvider.PADDLE_V6)
    assert calls == ["paddle"]


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRADEMIND_JWT_SECRET", SECRET)
    monkeypatch.setenv("GRADEMIND_S3_ACCESS_KEY", "ak")
    monkeypatch.setenv("GRADEMIND_S3_SECRET_KEY", "sk")
    monkeypatch.setenv("GRADEMIND_OCR_PROVIDERS_ENABLED", '["paddle_v6", "trocr_line"]')
    assert Settings().ocr_providers_enabled == [OcrProvider.PADDLE_V6, OcrProvider.TROCR_LINE]


def test_user_email_is_normalised_to_lowercase() -> None:
    """Regression: login looks emails up in lowercase; mixed-case stored emails could never sign in."""
    from grademind_core.db.models import Role, User

    assert (
        User(email="  ExA@College-One.IN ", display_name="x", password_hash="h", role=Role.EXAMINER).email == "exa@college-one.in"
    )
