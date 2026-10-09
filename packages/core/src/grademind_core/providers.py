"""Provider registry (spec rule 7 / I7). The ONLY way to obtain an OCR or LLM provider.

Factories are registered by name; `get()` checks the single config source first and raises `ProviderDisabledError` for a disabled
provider **without invoking its factory**, so a disabled provider's SDK client is never even constructed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from grademind_core.config import LlmProvider, OcrProvider, Settings


class ProviderDisabledError(RuntimeError):
    pass


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ocr: dict[OcrProvider, Callable[[], Any]] = {}
        self._llm: dict[LlmProvider, Callable[[], Any]] = {}

    def register_ocr(self, name: OcrProvider, factory: Callable[[], Any]) -> None:
        self._ocr[name] = factory

    def register_llm(self, name: LlmProvider, factory: Callable[[], Any]) -> None:
        self._llm[name] = factory

    def ocr(self, name: OcrProvider) -> Any:
        if name not in self._settings.ocr_providers_enabled:
            raise ProviderDisabledError(f"OCR provider {name} is disabled (single config source)")
        return self._ocr[name]()

    def llm(self, name: LlmProvider) -> Any:
        if self._settings.llm_kill_switch:
            raise ProviderDisabledError("LLM kill switch is on")
        if name not in self._settings.llm_providers_enabled:
            raise ProviderDisabledError(f"LLM provider {name} is disabled (single config source)")
        return self._llm[name]()
