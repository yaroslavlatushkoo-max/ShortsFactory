from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from .config import Config
from .logging_setup import get_logger


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise LLMError("Модель вернула пустой ответ")
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    # маленькие модели иногда отвечают голым массивом сцен вместо объекта
    if text.startswith("["):
        start, end = text.find("["), text.rfind("]")
        wrapped = True
    else:
        start, end = text.find("{"), text.rfind("}")
        wrapped = False
    if start < 0 or end <= start:
        raise LLMError("В ответе модели нет JSON-объекта")
    candidate = text[start:end + 1]
    if wrapped:
        candidate = '{"scenes": ' + candidate + "}"
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        # второй шанс: висячая запятая перед } или ] — частая ошибка 7B-моделей
        fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            data = json.loads(fixed)
        except json.JSONDecodeError:
            raise LLMError(f"Некорректный JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("JSON должен быть объектом")
    return data


class OllamaClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.base_url = str(config.get("llm", "base_url", "http://127.0.0.1:11434")).rstrip("/")
        self.model = str(config.get("llm", "model", "qwen2.5:7b"))
        self.timeout = float(config.get("llm", "timeout", 300))
        self.session = requests.Session()
        self.logger = get_logger()
        self._models: list[str] | None = None

    def models(self) -> list[str]:
        if self._models is not None:
            return self._models
        try:
            response = self.session.get(f"{self.base_url}/api/tags", timeout=8)
            response.raise_for_status()
            data = response.json()
            self._models = [item.get("name", "") for item in data.get("models", []) if item.get("name")]
        except (requests.RequestException, ValueError):
            self._models = []
        return self._models

    def available(self) -> bool:
        return bool(self.models())

    def resolve_model(self) -> str:
        models = self.models()
        if not models:
            raise LLMError(
                f"Ollama недоступна по адресу {self.base_url}. Запустите `ollama serve` "
                f"или отключите LLM в config.toml."
            )
        if self.model in models:
            return self.model
        fallbacks = self.config.get("llm", "fallback_models", []) or []
        for name in fallbacks:
            if name in models:
                self.logger.warning("Модель %s не найдена, использую %s", self.model, name)
                return str(name)
        raise LLMError(
            f"Модель {self.model} не найдена. Доступны: {', '.join(models)}. "
            f"Выполните `ollama pull {self.model}`."
        )

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        format_json: bool = True,
        temperature: float | None = None,
        num_predict: int | None = None,
        model: str | None = None,
    ) -> str:
        chosen = model or self.resolve_model()
        options: dict[str, Any] = {
            "temperature": self.config.get("llm", "temperature", 0.55) if temperature is None else temperature,
            "num_ctx": int(self.config.get("llm", "num_ctx", 6144)),
            "num_predict": int(num_predict or self.config.get("llm", "num_predict", 1100)),
        }
        payload: dict[str, Any] = {
            "model": chosen,
            "prompt": prompt,
            "stream": False,
            "options": options,
            "keep_alive": self.config.get("llm", "keep_alive", "30m"),
        }
        if system:
            payload["system"] = system
        if format_json:
            payload["format"] = "json"
        last_error: Exception | None = None
        for attempt in range(2):
            started = time.monotonic()
            try:
                response = self.session.post(
                    f"{self.base_url}/api/generate", json=payload, timeout=self.timeout
                )
                if response.status_code >= 400:
                    body = response.text[:1000]
                    raise LLMError(f"Ollama HTTP {response.status_code}: {body}")
                data = response.json()
                text = data.get("response", "")
                elapsed = time.monotonic() - started
                eval_count = data.get("eval_count", 0)
                self.logger.info(
                    "LLM %s: %.1f с, %s токенов, ответ %s символов",
                    chosen, elapsed, eval_count, len(text or ""),
                )
                if not isinstance(text, str) or not text.strip():
                    raise LLMError("Ollama вернула пустой текст (возможно, модель ушла в reasoning)")
                return text
            except (requests.RequestException, ValueError, LLMError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(2)
                    continue
        raise LLMError(str(last_error))

    def generate_stream(
        self,
        prompt: str,
        *,
        system: str = "",
        format_json: bool = False,
        temperature: float | None = None,
        num_predict: int | None = None,
        model: str | None = None,
    ):
        """Stream tokens from Ollama (POST /api/generate, stream=True).

        Yields plain-text chunks as they arrive.  Raises LLMError on HTTP or
        JSON errors (a partial stream is allowed to finish before raising).
        """
        chosen = model or self.resolve_model()
        options: dict[str, Any] = {
            "temperature": self.config.get("llm", "temperature", 0.55) if temperature is None else temperature,
            "num_ctx": int(self.config.get("llm", "num_ctx", 6144)),
            "num_predict": int(num_predict or self.config.get("llm", "num_predict", 1100)),
        }
        payload: dict[str, Any] = {
            "model": chosen,
            "prompt": prompt,
            "stream": True,
            "options": options,
            "keep_alive": self.config.get("llm", "keep_alive", "30m"),
        }
        if system:
            payload["system"] = system
        if format_json:
            payload["format"] = "json"
        try:
            response = self.session.post(
                f"{self.base_url}/api/generate", json=payload,
                timeout=self.timeout, stream=True,
            )
            if response.status_code >= 400:
                body = response.text[:1000]
                raise LLMError(f"Ollama HTTP {response.status_code}: {body}")
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LLMError(f"Сбой потока Ollama: {exc}") from exc
                chunk = data.get("response")
                if isinstance(chunk, str) and chunk:
                    yield chunk
                if data.get("done"):
                    return
        except requests.RequestException as exc:
            raise LLMError(f"Ollama недоступна: {exc}") from exc

    def generate_json(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        text = self.generate(prompt, format_json=True, **kwargs)
        return extract_json(text)

    def status(self) -> dict[str, Any]:
        models = self.models()
        return {
            "available": bool(models),
            "base_url": self.base_url,
            "configured_model": self.model,
            "model_present": self.model in models,
            "models": models,
        }
