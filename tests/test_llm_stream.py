"""Unit tests for OllamaClient.generate_stream (streaming tokens)."""
from __future__ import annotations

import json

import pytest

from shortsfactory.llm import LLMError, OllamaClient


class _FakeStreamResponse:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines
        self.text = ""

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=False):
        for item in self._lines:
            yield item


class _FakeSession:
    def __init__(self, lines):
        self.lines = lines
        self.posted = None

    def post(self, url, json=None, timeout=None, stream=False):
        self.posted = (url, json, stream)
        return _FakeStreamResponse(self.lines)


def _make_client(lines):
    cfg = type("Cfg", (), {"get": lambda self, *a, **k: {
        "llm.base_url": "http://127.0.0.1:11434",
        "llm.model": "qwen2.5:7b",
        "llm.timeout": 5,
        "llm.temperature": 0.55,
        "llm.num_ctx": 6144,
        "llm.num_predict": 1100,
        "llm.keep_alive": "30m",
    }.get(".".join(a[:2]), k.get("default", None))})()
    client = OllamaClient(cfg)
    client._models = ["qwen2.5:7b"]
    client.session = _FakeSession(lines)
    return client


def test_generate_stream_yields_chunks_and_uses_stream_true():
    client = _make_client([
        json.dumps({"response": "Привет", "done": False}),
        json.dumps({"response": ", мир", "done": False}),
        json.dumps({"response": "", "done": True}),
    ])
    chunks = list(client.generate_stream("prompt"))
    assert chunks == ["Привет", ", мир"]
    url, payload, stream = client.session.posted
    assert url.endswith("/api/generate")
    assert payload["stream"] is True
    assert stream is True
    assert payload["prompt"] == "prompt"


def test_generate_stream_raises_on_http_error():
    class _Err(_FakeStreamResponse):
        status_code = 500
        text = "boom"

    class _Sess:
        def post(self, url, json=None, timeout=None, stream=False):
            return _Err([])

    client = _make_client([])
    client.session = _Sess()
    with pytest.raises(LLMError):
        list(client.generate_stream("x"))


def test_generate_stream_raises_on_bad_ndjson():
    client = _make_client(["not-json"])
    with pytest.raises(LLMError):
        list(client.generate_stream("x"))
