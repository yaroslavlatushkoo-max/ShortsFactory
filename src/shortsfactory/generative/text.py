"""Text / structured-output providers (Mission 4 §4A).

Gemini (Google AI Studio REST), generic OpenAI-compatible chat, and local
Ollama. All are optional: the planner keeps a deterministic rule-based fallback
so LLM is never required.
"""

from __future__ import annotations

import os
from typing import Any

from .http_utils import request_with_retry
from .registry import Capability, TextProvider, register_provider

_GEMINI_GENERATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@register_provider
class GeminiTextProvider(TextProvider):
    id = "gemini"
    display_name = "Google Gemini"
    provider_type = "cloud"
    paid = True
    key_env_names = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    default_model = "gemini-2.0-flash"
    supported_capabilities = frozenset(
        {Capability.TEXT_GENERATION, Capability.STORY_PLANNING, Capability.STRUCTURED_OUTPUT}
    )

    def __init__(self, env: dict[str, str] | None = None) -> None:
        super().__init__(env)
        self._model = (self._env_value("GEMINI_TEXT_MODEL")
                       or os.environ.get("GEMINI_TEXT_MODEL")
                       or self.default_model)

    def models_for(self, capability: Capability) -> list[str]:
        return [self._model]

    def constraints_for(self, capability: Capability) -> dict[str, Any]:
        return {"max_tokens": 8192, "json_schema": capability is Capability.STRUCTURED_OUTPUT}

    def fee_free_check(self, capability: Capability) -> bool:
        # A plain auth ping to a model endpoint is not a free, guaranteed call.
        return False

    def _api_key(self) -> str:
        for k in self.key_env_names:
            value = self._env_value(k)
            if value:
                return value
        return ""

    def generate_text(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        if not self._api_key():
            from .backend import BackendNotConfigured

            raise BackendNotConfigured(
                f"Провайдер «{self.id}» не настроен: нет ключа {self.key_env_names}"
            )
        generation_config: dict[str, Any] = {"temperature": temperature} if temperature is not None else {}
        if max_tokens:
            generation_config["maxOutputTokens"] = max_tokens
        contents: list[dict[str, Any]] = [{"parts": [{"text": prompt}]}]
        payload: dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if generation_config:
            payload["generationConfig"] = generation_config
        response = request_with_retry(
            "POST",
            _GEMINI_GENERATE.format(model=self._model),
            headers={
                "x-goog-api-key": self._api_key(),
                "Content-Type": "application/json",
            },
            json=payload,
        )
        data = response.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"Неожиданный ответ Gemini: {str(data)[:300]}") from None
        return text

    def generate_structured(
        self,
        prompt: str,
        *,
        system: str = "",
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        attempts: int = 3,
        on_error: Any = None,
    ) -> dict[str, Any]:
        # Force JSON mode in the request, then validate via base helper.
        import json

        if not self._api_key():
            from .backend import BackendNotConfigured

            raise BackendNotConfigured(
                f"Провайдер «{self.id}» не настроен: нет ключа {self.key_env_names}"
            )
        generation_config: dict[str, Any] = {"responseMimeType": "application/json"}
        if temperature is not None:
            generation_config["temperature"] = temperature
        contents: list[dict[str, Any]] = [{"parts": [{"text": prompt}]}]
        payload: dict[str, Any] = {"contents": contents, "generationConfig": generation_config}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            response = request_with_retry(
                "POST",
                _GEMINI_GENERATE.format(model=self._model),
                headers={
                    "x-goog-api-key": self._api_key(),
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            data = response.json()
            try:
                raw = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(raw)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = exc
                if on_error:
                    on_error(prompt, exc)
                continue
            if not isinstance(parsed, dict):
                last_error = ValueError("Ответ Gemini — не JSON-объект")
                if on_error:
                    on_error(prompt, last_error)
                continue
            if schema:
                from .registry import _matches_schema

                if not _matches_schema(parsed, schema):
                    last_error = ValueError("Ответ Gemini не соответствует схеме")
                    if on_error:
                        on_error(prompt, last_error)
                    continue
            return parsed
        raise ValueError(f"Gemini не вернул корректный JSON после {attempts} попыток: {last_error}")


@register_provider
class OllamaTextProvider(TextProvider):
    id = "ollama"
    display_name = "Ollama (local)"
    provider_type = "local"
    paid = False
    key_env_names = ()
    default_model = "qwen3:4b"
    supported_capabilities = frozenset(
        {Capability.TEXT_GENERATION, Capability.STORY_PLANNING, Capability.STRUCTURED_OUTPUT}
    )

    def __init__(self, env: dict[str, str] | None = None, config: Any = None) -> None:
        super().__init__(env)
        self._config = config
        self._client: Any = None

    def _get_client(self):
        if self._client is None:
            from ..llm import OllamaClient

            if self._config is None:
                from ..config import load_config

                self._config = load_config()
            self._client = OllamaClient(self._config)
            base = self._env_value("OLLAMA_BASE_URL")
            if base:
                self._client.base_url = base.rstrip("/")
            text_model = self._env_value("OLLAMA_TEXT_MODEL")
            if text_model:
                self._client.model = text_model
        return self._client

    def is_configured(self) -> bool:
        try:
            return bool(self._get_client().available())
        except Exception:
            return False

    def models_for(self, capability: Capability) -> list[str]:
        try:
            return self._get_client().models()
        except Exception:
            return [self.default_model]

    def fee_free_check(self, capability: Capability) -> bool:
        return True  # local; listing models costs nothing

    def check_availability(self):
        from .registry import AvailabilityResult

        import time as _time

        started = _time.monotonic()
        try:
            ok = bool(self._get_client().available())
        except Exception as exc:
            ok = False
            detail = mask_local(exc)
        else:
            detail = "Ollama доступна" if ok else "Ollama недоступна"
        return AvailabilityResult(
            provider=self.id,
            ok=ok,
            mode="api" if ok else "config",
            cost_incurred=False,
            detail=detail,
            checked_at=_time.strftime("%Y-%m-%dT%H:%M:%S"),
            duration_ms=int((_time.monotonic() - started) * 1000),
            last_status="ok" if ok else "warning",
        )

    def generate_text(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        client = self._get_client()
        return client.generate(
            prompt,
            system=system,
            format_json=False,
            temperature=temperature,
            num_predict=max_tokens,
        )

    def generate_structured(
        self,
        prompt: str,
        *,
        system: str = "",
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        attempts: int = 3,
        on_error: Any = None,
    ) -> dict[str, Any]:
        client = self._get_client()
        return client.generate_json(
            prompt,
            system=system,
            temperature=temperature,
        )


def mask_local(exc: Exception) -> str:
    return str(exc)[:200]


@register_provider
class OpenAICompatibleTextProvider(TextProvider):
    """Generic OpenAI-compatible chat completions (works with local vLLM/LM
    Studio / Ollama's OpenAI endpoint as well as any cloud service via
    OPENAI_BASE_URL + OPENAI_API_KEY)."""

    id = "openai_compat"
    display_name = "OpenAI-compatible"
    provider_type = "cloud"
    paid = True
    key_env_names = ("OPENAI_API_KEY",)
    default_model = ""
    supported_capabilities = frozenset(
        {Capability.TEXT_GENERATION, Capability.STORY_PLANNING, Capability.STRUCTURED_OUTPUT}
    )

    def __init__(self, env: dict[str, str] | None = None) -> None:
        super().__init__(env)
        self._base = (self._env_value("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self._model = self._env_value("OPENAI_MODEL") or self.default_model or "gpt-4o-mini"

    def is_configured(self) -> bool:
        if self._base and "api.openai.com" in self._base:
            return bool(self._env_value("OPENAI_API_KEY"))
        return bool(self._base)  # local/private endpoint may not need a key

    def models_for(self, capability: Capability) -> list[str]:
        return [self._model]

    def fee_free_check(self, capability: Capability) -> bool:
        return "api.openai.com" not in self._base

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self._env_value("OPENAI_API_KEY")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def generate_text(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {"model": self._model, "messages": messages}
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens:
            body["max_tokens"] = max_tokens
        response = request_with_retry(
            "POST",
            f"{self._base}/chat/completions",
            headers=self._headers(),
            json=body,
        )
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"Неожиданный ответ API: {str(data)[:300]}") from None

    def generate_structured(
        self,
        prompt: str,
        *,
        system: str = "",
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        attempts: int = 3,
        on_error: Any = None,
    ) -> dict[str, Any]:
        import json

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {"model": self._model, "messages": messages,
                                "response_format": {"type": "json_object"}}
        if temperature is not None:
            body["temperature"] = temperature
        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            response = request_with_retry(
                "POST",
                f"{self._base}/chat/completions",
                headers=self._headers(),
                json=body,
            )
            data = response.json()
            try:
                raw = data["choices"][0]["message"]["content"]
                parsed = json.loads(raw)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = exc
                if on_error:
                    on_error(prompt, exc)
                continue
            if not isinstance(parsed, dict):
                last_error = ValueError("Ответ — не JSON-объект")
                if on_error:
                    on_error(prompt, last_error)
                continue
            if schema:
                from .registry import _matches_schema

                if not _matches_schema(parsed, schema):
                    last_error = ValueError("Ответ не соответствует схеме")
                    if on_error:
                        on_error(prompt, last_error)
                    continue
            return parsed
        raise ValueError(f"API не вернул корректный JSON после {attempts} попыток: {last_error}")
