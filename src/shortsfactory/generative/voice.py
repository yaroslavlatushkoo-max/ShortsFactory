"""Voice / text-to-speech providers (Mission 4 §4C).

Local Piper remains the default fallback (free, offline). ElevenLabs is the
cloud option (paid — only used when explicitly configured with a key).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .http_utils import request_with_retry
from .registry import Capability, VoiceProvider, register_provider

_ELEVEN_TTS = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


@register_provider
class PiperVoiceProvider(VoiceProvider):
    id = "piper"
    display_name = "Piper (local)"
    provider_type = "local"
    paid = False
    key_env_names = ()
    default_model = "piper"
    supported_capabilities = frozenset({Capability.TEXT_TO_SPEECH})

    def __init__(self, env: dict[str, str] | None = None, config: Any = None) -> None:
        super().__init__(env)
        self._config = config
        self._service: Any = None

    def _get_service(self):
        if self._service is None:
            from ..tts import TTSService

            if self._config is None:
                from ..config import load_config

                self._config = load_config()
            self._service = TTSService(self._config)
        return self._service

    def is_configured(self) -> bool:
        service = self._get_service()
        try:
            service.voice_path("ru")
            return True
        except Exception:
            return False

    def fee_free_check(self, capability: Capability) -> bool:
        return True

    def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        lang: str = "ru",
        out_path: Path,
        params: dict[str, Any] | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        service = self._get_service()
        wav, duration = service.synthesize_line(text, lang, voice_override=voice)
        # Copy into the requested output location (fallback to the cache file).
        if Path(wav).resolve() != out_path.resolve():
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(Path(wav).read_bytes())
        return out_path, {"format": "wav", "duration_s": duration, "engine": "piper"}


@register_provider
class ElevenLabsVoiceProvider(VoiceProvider):
    id = "elevenlabs"
    display_name = "ElevenLabs"
    provider_type = "cloud"
    paid = True
    key_env_names = ("ELEVENLABS_API_KEY",)
    default_model = "eleven_multilingual_v2"
    supported_capabilities = frozenset({Capability.TEXT_TO_SPEECH})

    def __init__(self, env: dict[str, str] | None = None) -> None:
        super().__init__(env)
        self._voice_id = self.env.get("ELEVENLABS_VOICE_ID") or "21m00Tcm4TlvDq8ikWAM"  # default voice

    def models_for(self, capability: Capability) -> list[str]:
        return [self.default_model]

    def fee_free_check(self, capability: Capability) -> bool:
        return False  # a synthesis call is a paid call

    def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        lang: str = "ru",
        out_path: Path,
        params: dict[str, Any] | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        key = self.env.get("ELEVENLABS_API_KEY")
        if not key:
            from .backend import BackendNotConfigured

            raise BackendNotConfigured(
                "Провайдер «elevenlabs» не настроен: нет ключа ELEVENLABS_API_KEY"
            )
        voice_id = voice or self._voice_id
        body: dict[str, Any] = {
            "text": text,
            "model_id": self.default_model,
        }
        if params:
            body["voice_settings"] = params
        response = request_with_retry(
            "POST",
            _ELEVEN_TTS.format(voice_id=voice_id),
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json=body,
            timeout=90.0,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(response.content)
        # Rough duration estimate is not reliable from bytes; leave unknown.
        return out_path, {"format": "mp3", "engine": "elevenlabs", "voice_id": voice_id}
