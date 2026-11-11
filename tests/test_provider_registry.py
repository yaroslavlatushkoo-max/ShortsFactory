"""Unified AI provider registry tests (Mission 4 §10)."""

from __future__ import annotations

import json


from shortsfactory.generative.registry import (
    AIProvider,
    Capability,
    ProviderRegistry,
    mask_secrets,
    registry,
)
from shortsfactory.generative.text import GeminiTextProvider


def _provider_ids() -> set[str]:
    return {p.id for p in registry().list_providers()}


def test_registry_contains_core_providers():
    ids = _provider_ids()
    assert {"gemini", "ollama", "openai_compat", "piper", "elevenlabs",
            "veo", "kling", "fal"} <= ids


def test_capability_discovery():
    video = {p.id for p in registry().providers_for_capability(Capability.VIDEO_GENERATION)}
    assert {"veo", "kling", "fal"} <= video
    voice = {p.id for p in registry().providers_for_capability(Capability.TEXT_TO_SPEECH)}
    assert {"piper", "elevenlabs"} <= voice
    text = {p.id for p in registry().providers_for_capability(Capability.TEXT_GENERATION)}
    assert {"gemini", "ollama", "openai_compat"} <= text


class _FakeTextProvider(AIProvider):
    """Deterministic stub so tests never depend on the host environment's keys."""

    supported_capabilities = frozenset({Capability.TEXT_GENERATION})

    def __init__(self, pid: str, configured: bool) -> None:
        self._pid = pid
        self._configured = configured
        super().__init__(env={})

    # -- AIProvider chain --
    @property
    def id(self) -> str:
        return self._pid

    @property
    def display_name(self) -> str:
        return self._pid

    @property
    def provider_type(self) -> str:
        return "cloud"

    @property
    def paid(self) -> bool:
        return True

    @property
    def key_env_names(self):
        return ("X_FAKE_KEY",)

    def is_configured(self) -> bool:
        return self._configured


def test_resolve_preferred_and_fallback():
    a = _FakeTextProvider("gemini", configured=False)
    b = _FakeTextProvider("ollama", configured=True)

    reg = ProviderRegistry()
    reg.register_provider(a)
    reg.register_provider(b)
    # Configured-only resolution skips the preferred provider when it is unconfigured.
    assert reg.resolve(Capability.TEXT_GENERATION, preferred="gemini",
                       only_configured=True).id == "ollama"
    # Preferred wins when configuration is not required.
    assert reg.resolve(Capability.TEXT_GENERATION, preferred="gemini",
                       only_configured=False).id == "gemini"
    # With nothing configured, configured-only resolution returns None.
    reg_none = ProviderRegistry()
    reg_none.register_provider(a)
    assert reg_none.resolve(Capability.TEXT_GENERATION, preferred="gemini",
                            only_configured=True) is None


def test_describe_never_leaks_secret_value():
    secret = "super-secret-value-xyz9876"
    prov = GeminiTextProvider(env={"GEMINI_API_KEY": secret})
    text = json.dumps(prov.describe(), ensure_ascii=False)
    assert secret not in text


def test_mask_secrets_redacts_values():
    env = {"GEMINI_API_KEY": "k-secret-abcdef", "OPENAI_API_KEY": "o-secret-123456"}
    out = mask_secrets("key=k-secret-abcdef and o-secret-123456", env)
    assert "k-secret-abcdef" not in out
    assert "o-secret-123456" not in out
    assert "[REDACTED]" in out


def test_mask_secrets_ignores_empty_values():
    assert "nothing" in mask_secrets("nothing", {"GEMINI_API_KEY": ""})
