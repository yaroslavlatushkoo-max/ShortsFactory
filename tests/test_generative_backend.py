"""Backend interface and provider tests (Mission 3 §13)."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from shortsfactory.generative.backend import (
    BackendNotConfigured,
    create_backend,
)
from shortsfactory.generative.models import SceneGenerationSpec


def _spec(scene_idx: int = 0) -> SceneGenerationSpec:
    return SceneGenerationSpec(scene_idx=scene_idx, prompt="a cat", duration=5.0)


def test_disabled_backend_refuses_work():
    b = create_backend("disabled")
    assert not b.is_configured()
    assert b.capabilities.max_duration_s == 0.0
    with pytest.raises(BackendNotConfigured):
        b.submit(_spec())


@pytest.mark.parametrize("name,key", [
    ("veo", "GEMINI_API_KEY"),
    ("kling", "KLING_ACCESS_KEY"),
    ("fal", "FAL_KEY"),
])
def test_providers_not_configured_without_keys(name, key):
    b = create_backend(name, env={})
    assert b.is_configured() is False


def test_provider_configured_when_key_present():
    assert create_backend("veo", env={"GEMINI_API_KEY": "k"}).is_configured()
    assert create_backend(
        "kling", env={"KLING_ACCESS_KEY": "a", "KLING_SECRET_KEY": "s"}
    ).is_configured()
    assert create_backend("fal", env={"FAL_KEY": "k"}).is_configured()


def test_cost_estimate_uses_per_second():
    b = create_backend("veo", env={"GEMINI_API_KEY": "k"})
    cost = b.estimate_cost([_spec(0), _spec(1)])
    assert cost is not None and cost.estimated_usd is not None
    # 2 scenes * 5s * $0.05 = $0.50
    assert cost.estimated_usd == pytest.approx(0.5, abs=1e-3)


def test_unknown_provider_raises():
    with pytest.raises(Exception):
        create_backend("nope", env={})


def test_veo_inline_video(tmp_path: Path, monkeypatch):
    b = create_backend("veo", env={"GEMINI_API_KEY": "k"})
    fake_mp4 = b"AVI\x00fake-video-bytes"

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "candidates": [
                    {"content": {"parts": [{"inlineData": {"data": base64.b64encode(fake_mp4).decode()}}]}}
                ]
            }

    monkeypatch.setattr("shortsfactory.generative.providers.veo.requests.post", lambda *a, **k: FakeResponse())
    job = b.run_to_completion(_spec(), tmp_path, poll_interval=0.01, poll_timeout=5)
    assert job.status == "done"
    assert Path(job.media).read_bytes() == fake_mp4


def test_fal_queue_done(tmp_path: Path, monkeypatch):
    b = create_backend("fal", env={"FAL_KEY": "k"})
    fake_mp4 = b"mp4-fake"

    class FakeResponse:
        def __init__(self, data, content=b""):
            self._data = data
            self._content = content
            self.status_code = 200

        def json(self):
            return self._data

        @property
        def content(self):
            return self._content

    def fake_get(url, **kw):
        if "/status" in url:
            return FakeResponse({"status": "COMPLETED", "response_url": "http://x/result"})
        if url.endswith("/result"):
            return FakeResponse({"video": {"url": "http://x/vid"}})
        return FakeResponse({}, content=fake_mp4)

    def fake_post(*a, **k):
        return FakeResponse({"request_id": "r1"})

    monkeypatch.setattr("shortsfactory.generative.providers.fal.requests.post", fake_post)
    monkeypatch.setattr("shortsfactory.generative.providers.fal.requests.get", fake_get)
    job = b.run_to_completion(_spec(), tmp_path, poll_interval=0.01, poll_timeout=5)
    assert job.status == "done"
    assert Path(job.media).read_bytes() == fake_mp4


def test_timeout_raises(tmp_path: Path, monkeypatch):
    b = create_backend("fal", env={"FAL_KEY": "k"})

    class Running:
        status_code = 200

        def json(self):
            return {"status": "IN_PROGRESS"}

    monkeypatch.setattr("shortsfactory.generative.providers.fal.requests.post", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {"request_id": "r1"}))
    monkeypatch.setattr("shortsfactory.generative.providers.fal.requests.get", lambda *a, **k: Running())
    with pytest.raises(Exception, match="Таймаут"):
        b.run_to_completion(_spec(), tmp_path, poll_interval=0.01, poll_timeout=0.05)
