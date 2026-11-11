"""Unified multi-provider AI registry (Mission 4).

ShortsFactory owns the pipeline; providers are interchangeable tools. A provider
declares the capabilities it actually supports (text, structured output,
video, image-to-video, TTS, ...) and a *safe* description (never leaks keys).

The Mission 3 ``VideoBackend`` interface is kept intact and is wrapped into the
registry via :class:`VideoBackendAIProvider` so existing pipeline code still
works unchanged. Availability checks never trigger paid generation.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------- capabilities

class Capability(str, Enum):
    TEXT_GENERATION = "text_generation"
    STORY_PLANNING = "story_planning"
    STRUCTURED_OUTPUT = "structured_output"
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"
    IMAGE_TO_VIDEO = "image_to_video"
    TEXT_TO_SPEECH = "text_to_speech"
    SPEECH_TO_TEXT = "speech_to_text"
    VIDEO_ANALYSIS = "video_analysis"


CAPABILITY_LABELS: dict[Capability, str] = {
    Capability.TEXT_GENERATION: "text",
    Capability.STORY_PLANNING: "story planning",
    Capability.STRUCTURED_OUTPUT: "structured JSON",
    Capability.IMAGE_GENERATION: "image generation",
    Capability.VIDEO_GENERATION: "text-to-video",
    Capability.IMAGE_TO_VIDEO: "image-to-video",
    Capability.TEXT_TO_SPEECH: "text-to-speech",
    Capability.SPEECH_TO_TEXT: "speech-to-text",
    Capability.VIDEO_ANALYSIS: "video analysis",
}


@dataclass
class CapabilityReport:
    capability: Capability
    models: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)        # accepted input kinds
    constraints: dict[str, Any] = field(default_factory=dict)
    key_names: list[str] = field(default_factory=list)
    configured: bool = False
    provider_type: str = "cloud"
    paid: bool = False
    fee_free_check: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability.value,
            "models": self.models,
            "inputs": self.inputs,
            "constraints": self.constraints,
            "key_names": self.key_names,
            "configured": self.configured,
            "provider_type": self.provider_type,
            "paid": self.paid,
            "fee_free_check": self.fee_free_check,
        }


@dataclass
class AvailabilityResult:
    provider: str
    ok: bool
    mode: str = "config"          # "config" (key present) | "api" (network call)
    cost_incurred: bool = False
    detail: str = ""
    checked_at: str = ""
    duration_ms: int = 0
    last_status: str = "unknown"  # ok | warning | error

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "ok": self.ok,
            "mode": self.mode,
            "cost_incurred": self.cost_incurred,
            "detail": self.detail,
            "checked_at": self.checked_at,
            "duration_ms": self.duration_ms,
            "status": self.last_status,
        }


_ALWAYS_SECRET_ENV = {
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "KLING_API_KEY", "KLING_ACCESS_KEY",
    "KLING_SECRET_KEY", "FAL_KEY", "FAL_API_KEY", "ELEVENLABS_API_KEY",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "REPLICATE_API_TOKEN",
    "RUNPOD_API_KEY", "HF_TOKEN", "HUGGINGFACE_TOKEN",
}


def mask_secrets(text: str, env: dict[str, str] | None = None) -> str:
    """Redact known secret values from free-form text (logs/errors/reports).

    Masks any env value (length >= 6) under a secret-like key name, plus the
    well-known secret variable names.
    """
    env = dict(os.environ if env is None else env)
    result = text or ""
    secret_values: list[str] = []
    for key, value in env.items():
        upper = key.upper()
        if not value or len(value) < 6:
            continue
        if "KEY" in upper or "TOKEN" in upper or "SECRET" in upper or upper in _ALWAYS_SECRET_ENV:
            secret_values.append(value)
    for value in sorted(set(secret_values), key=len, reverse=True):
        if value in result:
            result = result.replace(value, "[REDACTED]")
    return result


# ------------------------------------------------------------ base provider

class AIProvider(ABC):
    """Base for every registered provider (text, voice, video, ...)."""

    id: str = ""
    display_name: str = ""
    provider_type: str = "cloud"          # "local" | "cloud"
    paid: bool = False                    # generation costs money
    key_env_names: tuple[str, ...] = ()
    default_model: str = ""
    supported_capabilities: frozenset[Capability] = frozenset()

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self.env = dict(os.environ if env is None else env)

    # ---- configuration (never reveal actual key values) ----

    def key_names(self) -> list[str]:
        return list(self.key_env_names)

    def _env_value(self, key: str) -> str:
        """Live lookup: instance env snapshot first, then the process env.

        Providers are constructed once at import time, but ``.env`` is loaded
        into ``os.environ`` later (CLI startup / pipeline), so we must consult
        the process env too. Real exported env vars always win over ``.env``.
        """
        return self.env.get(key) or os.environ.get(key) or ""

    def is_configured(self) -> bool:
        return any(self._env_value(k) for k in self.key_env_names)

    def _configured_key_name(self) -> str:
        for k in self.key_env_names:
            if self._env_value(k):
                return k
        return ""

    # ---- capabilities ----

    def capability_reports(self) -> list[CapabilityReport]:
        reports: list[CapabilityReport] = []
        for cap in self.supported_capabilities:
            reports.append(
                CapabilityReport(
                    capability=cap,
                    models=self.models_for(cap),
                    inputs=self.inputs_for(cap),
                    constraints=self.constraints_for(cap),
                    key_names=self.key_names(),
                    configured=self.is_configured(),
                    provider_type=self.provider_type,
                    paid=self.paid,
                    fee_free_check=self.fee_free_check(cap),
                )
            )
        return reports

    def models_for(self, capability: Capability) -> list[str]:
        return [self.default_model] if self.default_model else []

    def inputs_for(self, capability: Capability) -> list[str]:
        return ["text"]

    def constraints_for(self, capability: Capability) -> dict[str, Any]:
        return {}

    def fee_free_check(self, capability: Capability) -> bool:
        return True

    # ---- availability (free preflight; never costs money) ----

    def check_availability(self) -> AvailabilityResult:
        started = time.monotonic()
        configured = self.is_configured()
        result = AvailabilityResult(
            provider=self.id,
            ok=configured,
            mode="config",
            cost_incurred=False,
            detail=("Key present" if configured else "No key configured"),
            checked_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            duration_ms=int((time.monotonic() - started) * 1000),
            last_status="ok" if configured else "unknown",
        )
        return result

    # ---- safe description (for CLI / reports) ----

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.display_name,
            "type": self.provider_type,
            "paid": self.paid,
            "configured": self.is_configured(),
            "configured_key": self._configured_key_name() or "",
            "capabilities": [r.to_dict() for r in self.capability_reports()],
        }


# ------------------------------------------------------------ text interface

class TextProvider(AIProvider):
    """Providers that generate text / structured JSON."""

    @abstractmethod
    def generate_text(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        ...

    def generate_structured(
        self,
        prompt: str,
        *,
        system: str = "",
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        attempts: int = 3,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> dict[str, Any]:
        """Ask for JSON and validate. Retries with backoff on malformed/out-of-schema output.

        Never retries on provider-side transport errors (those are handled by
        the request wrapper). ``attempts`` bounds only schema validation retries.
        """
        import json
        import time as _time

        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            raw = self.generate_text(prompt, system=system, temperature=temperature)
            try:
                data = json.loads(_extract_json_object(raw))
            except (ValueError, TypeError) as exc:
                last_error = exc
                if on_error:
                    on_error(prompt, exc)
                if attempt < attempts - 1:
                    _time.sleep(min(2.0 * (2 ** attempt), 6.0))
                continue
            if schema and not _matches_schema(data, schema):
                last_error = ValueError(
                    "JSON не соответствует схеме: " + ", ".join(sorted(_missing_keys(data, schema)))
                )
                if on_error:
                    on_error(prompt, last_error)
                if attempt < attempts - 1:
                    _time.sleep(min(2.0 * (2 ** attempt), 6.0))
                continue
            return data
        raise ValueError(f"Модель не вернула корректный JSON после {attempts} попыток: {last_error}")


def _extract_json_object(text: str) -> str:
    import re

    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Нет JSON-объекта в ответе модели")
    return text[start:end + 1]


def _matches_schema(data: Any, schema: dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    required = schema.get("required", [])
    for key in required:
        if key not in data:
            return False
    return True


def _missing_keys(data: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    required = schema.get("required", [])
    return [k for k in required if k not in data]


# ------------------------------------------------------------ voice interface

class VoiceProvider(AIProvider):
    """Providers that synthesize speech."""

    @abstractmethod
    def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        lang: str = "ru",
        out_path: Path,
        params: dict[str, Any] | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        """Return (audio_path, metadata). Raises on error/timeout."""
        ...


# ---------------------------------------------------------------- registry

class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, AIProvider] = {}

    def register_provider(self, provider: AIProvider) -> AIProvider:
        self._providers[provider.id] = provider
        return provider

    # -- registration decorator for classes --
    def register(self, cls: type) -> type:
        self._providers[cls.id] = cls()
        return cls

    def list_providers(self) -> list[AIProvider]:
        return sorted(self._providers.values(), key=lambda p: (p.provider_type, p.id))

    def get_provider(self, provider_id: str) -> AIProvider | None:
        return self._providers.get(provider_id)

    def providers_for_capability(
        self, capability: Capability, only_configured: bool = False
    ) -> list[AIProvider]:
        out = []
        for p in self._providers.values():
            if capability in p.supported_capabilities:
                if only_configured and not p.is_configured():
                    continue
                out.append(p)
        return out

    def resolve(
        self, capability: Capability, preferred: str = "", only_configured: bool = False
    ) -> AIProvider | None:
        """Pick a provider for a capability. ``preferred`` wins if it supports it."""
        if preferred:
            p = self._providers.get(preferred)
            if p and capability in p.supported_capabilities:
                if not only_configured or p.is_configured():
                    return p
        pool = self.providers_for_capability(capability, only_configured=only_configured)
        return pool[0] if pool else None

    def describe_all(self) -> list[dict[str, Any]]:
        return [p.describe() for p in self.list_providers()]


_REGISTRY = ProviderRegistry()


def registry() -> ProviderRegistry:
    return _REGISTRY


def register_provider(cls: type) -> type:
    _REGISTRY.register(cls)
    return cls
