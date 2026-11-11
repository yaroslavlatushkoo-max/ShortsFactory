"""Environment handling / key-mapping tests (Mission 4.5).

These cover the Gemini/Veo key-sharing bug, .env precedence, live env lookup,
and the guarantee that provider descriptions never leak secret values.
Everything runs with fake keys only — nothing is ever sent to a real API.
"""

from __future__ import annotations

import json
import os

from shortsfactory.generative.backend import _load_dotenv
from shortsfactory.generative.registry import registry
from shortsfactory.generative.text import GeminiTextProvider

_PROVIDER_KEYS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "KLING_ACCESS_KEY",
    "KLING_SECRET_KEY",
    "FAL_KEY",
    "FAL_API_KEY",
    "OPENAI_API_KEY",
    "ELEVENLABS_API_KEY",
)


def _clear_provider_keys(monkeypatch) -> None:
    for key in _PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_env_priority_real_env_wins(monkeypatch, tmp_path):
    # Reset the module-level once-only guard so the loader actually runs.
    monkeypatch.setattr(_load_dotenv, "_loaded", False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FAL_KEY=fromfile\nMY_TEST_ONLY_KEY=filevalue\n", encoding="utf-8"
    )
    monkeypatch.setenv("FAL_KEY", "fromenv")
    _load_dotenv(tmp_path)
    try:
        # Real exported environment wins over .env content...
        assert os.environ["FAL_KEY"] == "fromenv"
        # ...but variables that are only in .env get loaded from it.
        assert os.environ["MY_TEST_ONLY_KEY"] == "filevalue"
    finally:
        monkeypatch.delenv("FAL_KEY", raising=False)
        monkeypatch.delenv("MY_TEST_ONLY_KEY", raising=False)


def test_gemini_and_veo_share_same_key(monkeypatch):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "sk-fake-value")
    gemini = registry().get_provider("gemini")
    veo = registry().get_provider("veo")
    assert gemini is not None and veo is not None
    assert gemini.is_configured() is True
    assert veo.is_configured() is True
    for provider in (gemini, veo):
        text = json.dumps(provider.describe(), ensure_ascii=False)
        assert "sk-fake-value" not in text
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_missing_keys_not_configured(monkeypatch):
    _clear_provider_keys(monkeypatch)
    assert GeminiTextProvider(env={}).is_configured() is False
    # The registered gemini provider must read the same (now-cleared) environment.
    gemini = registry().get_provider("gemini")
    assert gemini is not None
    assert gemini.is_configured() is False


def test_live_env_lookup(monkeypatch):
    _clear_provider_keys(monkeypatch)
    provider = GeminiTextProvider(env={})
    assert provider.is_configured() is False
    monkeypatch.setenv("GEMINI_API_KEY", "x" * 20)
    assert provider.is_configured() is True
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert provider.is_configured() is False
