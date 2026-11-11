"""Text/voice provider behaviour tests (Mission 4 §10) — no network, no keys.

Mock tests verify interface handling, not real-API success.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from shortsfactory.generative.http_utils import (
    ApiAuthError,
    ApiRateLimitedError,
    request_with_retry,
)
from shortsfactory.generative.text import GeminiTextProvider
from shortsfactory.generative.voice import ElevenLabsVoiceProvider


def _resp(json_data=None, content=b"", status=200, headers=None, text=""):
    body = text if text else (str(json_data) if json_data is not None else "")
    return SimpleNamespace(
        json=lambda: json_data if json_data is not None else {},
        content=content,
        status_code=status,
        headers=headers or {},
        text=body,
    )


def _gemini(env=None):
    return GeminiTextProvider(env={**({"GEMINI_API_KEY": "test-key"} if env is None else env)})


def test_gemini_generate_text_ok(monkeypatch):
    def fake_request(*a, **k):
        return _resp({"candidates": [{"content": {"parts": [{"text": "hello"}]}}]})
    monkeypatch.setattr("shortsfactory.generative.text.request_with_retry", fake_request)
    assert _gemini().generate_text("hi") == "hello"


def test_gemini_structured_valid(monkeypatch):
    def fake_request(*a, **k):
        return _resp({"candidates": [{"content": {"parts": [{"text": '{"characters":["Milo"]}'}]}}]})
    monkeypatch.setattr("shortsfactory.generative.text.request_with_retry", fake_request)
    out = _gemini().generate_structured("split", schema={"required": ["characters"]})
    assert out == {"characters": ["Milo"]}


def test_gemini_structured_malformed_retries_then_fails(monkeypatch):
    def fake_request(*a, **k):
        return _resp({"candidates": [{"content": {"parts": [{"text": "not json"}]}}]})
    monkeypatch.setattr("shortsfactory.generative.text.request_with_retry", fake_request)
    with pytest.raises(ValueError):
        _gemini().generate_structured("split", attempts=2)


def test_gemini_auth_error_propagates(monkeypatch):
    def fake_request(*a, **k):
        raise ApiAuthError("401 unauthorized", status=401)
    monkeypatch.setattr("shortsfactory.generative.text.request_with_retry", fake_request)
    with pytest.raises(ApiAuthError):
        _gemini().generate_text("hi")


def test_gemini_requires_key(monkeypatch):
    # The provider reads the live process environment, so isolate the test from
    # any real Gemini key that may be present on the host.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    prov = GeminiTextProvider(env={})
    assert not prov.is_configured()
    with pytest.raises(Exception) as exc:
        prov.generate_text("hi")
    assert "не настроен" in str(exc.value)


# ---- http_utils retry/backoff ----

class _SeqSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self._requested = []

    def request(self, method, url, **kwargs):
        self._requested.append(url)
        if not self._responses:
            return _resp(status=200, json_data={"ok": True})
        return self._responses.pop(0)


def test_http_retries_on_429_then_succeeds():
    session = _SeqSession([
        _resp(status=429, headers={"Retry-After": "0"}, text="slow down"),
        _resp(status=200, json_data={"ok": True}),
    ])
    resp = request_with_retry("GET", "http://x", session=session, attempts=3, base_delay=0.0)
    assert resp.status_code == 200
    assert len(session._requested) == 2


def test_http_retries_on_5xx_then_succeeds():
    session = _SeqSession([
        _resp(status=503, text="boom"),
        _resp(status=200, json_data={"ok": True}),
    ])
    resp = request_with_retry("GET", "http://x", session=session, attempts=3, base_delay=0.0)
    assert resp.status_code == 200
    assert len(session._requested) == 2


def test_http_auth_error_not_retried():
    session = _SeqSession([_resp(status=401, text="nope")])
    with pytest.raises(ApiAuthError):
        request_with_retry("GET", "http://x", session=session, attempts=3, base_delay=0.0)
    assert len(session._requested) == 1


def test_http_rate_limit_exhausted_raises():
    session = _SeqSession([
        _resp(status=429, headers={"Retry-After": "0"}, text="slow"),
        _resp(status=429, headers={"Retry-After": "0"}, text="slow"),
    ])
    with pytest.raises(ApiRateLimitedError):
        request_with_retry("GET", "http://x", session=session, attempts=2, base_delay=0.0)


# ---- voice ----

def test_elevenlabs_synthesize_writes_file(monkeypatch, tmp_path):
    def fake_request(*a, **k):
        return _resp(content=b"audio-bytes-123")
    monkeypatch.setattr("shortsfactory.generative.voice.request_with_retry", fake_request)
    prov = ElevenLabsVoiceProvider(env={"ELEVENLABS_API_KEY": "k"})
    out = tmp_path / "clip.mp3"
    path, meta = prov.synthesize("Привет", out_path=out)
    assert path.read_bytes() == b"audio-bytes-123"
    assert meta["engine"] == "elevenlabs"


def test_elevenlabs_requires_key(monkeypatch):
    # live env lookup: hide any real key that may exist in the host environment
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    prov = ElevenLabsVoiceProvider(env={})
    assert not prov.is_configured()
    with pytest.raises(Exception) as exc:
        prov.synthesize("hi", out_path=Path("x.mp3"))
    assert "не настроен" in str(exc.value)
