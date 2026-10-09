"""Single configuration source (spec rule 7, I7/I10/I11; owner decisions D18/D19).

Every feature flag, provider enable/disable switch and kill switch lives here and nowhere else. Values come from the
environment (prefix GRADEMIND_), never from code defaults that differ per environment. Invariants are validated at
startup, so a misconfigured process refuses to start rather than running in a state the spec forbids.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class OcrProvider(StrEnum):
    PADDLE_V6 = "paddle_v6"  # D18: v1 primary reader
    TROCR_LINE = "trocr_line"  # D18: kept behind a disabled flag
    UNLIMITED_OCR = "unlimited_ocr"  # D18: excluded from v1
    FAKE = "fake"  # I11: tests only


class LlmProvider(StrEnum):
    LOCAL_QWEN = "local_qwen"
    FAKE = "fake"  # I11: tests only


EXCLUDED_IN_V1 = {OcrProvider.UNLIMITED_OCR}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GRADEMIND_", env_nested_delimiter="__", extra="forbid")

    env: Env = Env.DEV

    # --- product flags (D19) ---
    ai_suggestions_enabled: bool = False
    auto_approve_enabled: bool = False  # I10: off until a calibration benchmark proves the bound
    auto_approve_benchmark_run_id: str | None = None

    # --- provider registry (rule 7: one source; disabled providers are never called) ---
    ocr_providers_enabled: list[OcrProvider] = Field(default_factory=lambda: [OcrProvider.PADDLE_V6])
    llm_providers_enabled: list[LlmProvider] = Field(default_factory=list)
    llm_kill_switch: bool = True

    # --- infrastructure (secrets from the environment only) ---
    database_url: str = "postgresql+psycopg://grademind:grademind@localhost:5432/grademind"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint_url: str = "http://localhost:9000"  # as reached by api/worker (inside compose: http://minio:9000)
    s3_public_endpoint_url: str | None = None  # as reached by the browser; signed URLs are signed for this host
    s3_region: str = "us-east-1"
    s3_bucket: str = "grademind"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    signed_url_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    jwt_secret: SecretStr = SecretStr("")
    jwt_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    ocr_service_url: str = "http://localhost:8800"
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)

    # --- jobs (spec §15) ---
    job_heartbeat_seconds: float = Field(default=30, gt=0)  # a running stage renews its lease this often (D26)
    job_max_missed_heartbeats: int = Field(default=4, ge=2)  # reclaimable only after this many missed heartbeats
    sse_poll_seconds: float = Field(default=1.0, gt=0, le=10)

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        problems: list[str] = []
        if self.env != Env.TEST:
            if OcrProvider.FAKE in self.ocr_providers_enabled or LlmProvider.FAKE in self.llm_providers_enabled:
                problems.append("fake providers are allowed only when GRADEMIND_ENV=test (I11)")
            if not self.jwt_secret.get_secret_value() or len(self.jwt_secret.get_secret_value()) < 32:
                problems.append("GRADEMIND_JWT_SECRET must be set (>= 32 chars) outside tests")
            if not self.s3_access_key.get_secret_value() or not self.s3_secret_key.get_secret_value():
                problems.append("GRADEMIND_S3_ACCESS_KEY and GRADEMIND_S3_SECRET_KEY must be set outside tests")
            if any("change-me" in v.get_secret_value() for v in (self.jwt_secret, self.s3_secret_key)):
                problems.append("a secret still has its .env.example placeholder value (change-me...)")
        excluded = EXCLUDED_IN_V1.intersection(self.ocr_providers_enabled)
        if excluded:
            problems.append(f"providers excluded from v1 by D18 cannot be enabled: {sorted(excluded)}")
        if self.auto_approve_enabled and not self.auto_approve_benchmark_run_id:
            problems.append("AUTO_APPROVE_ENABLED requires GRADEMIND_AUTO_APPROVE_BENCHMARK_RUN_ID (I10)")
        if self.ai_suggestions_enabled and (self.llm_kill_switch or not self.llm_providers_enabled):
            problems.append("AI_SUGGESTIONS_ENABLED requires an enabled LLM provider and llm_kill_switch=false (D19)")
        if problems:
            raise ValueError("; ".join(problems))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
