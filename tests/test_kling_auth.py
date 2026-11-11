"""Kling auth tests (Mission 5 §...): modern bearer API key vs legacy AK/SK.

Mock-only — no real paid request is ever sent.
"""

from __future__ import annotations

import json

import pytest

from shortsfactory.generative.models import SceneGenerationSpec
from shortsfactory.generative.providers.kling import KlingBackend


def _spec() -> SceneGenerationSpec:
    return SceneGenerationSpec(scene_idx=0, prompt="тест", duration=5, width=1080, height=1920)


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}
        self.text = json.dumps(data) if data is not None else ""

    def json(self):
        return self._data


class _FakeRequests:
    def __init__(self, create_data=None):
        self.create_data = create_data or {"data": {"task_id": "task-1"}}
        self.posts = []
        self.gets = []

    def post(self, url, **kw):
        self.posts.append((url, kw))
        return _Resp(200, self.create_data)

    def get(self, url, **kw):
        self.gets.append((url, kw))
        return _Resp(200, {"data": {"task_status": "succeed", "task_result": {"videos": [{"url": "https://cdn.example/v.mp4"}]}}})


def test_kling_modern_api_key_uses_bearer_header(monkeypatch):
    fake = _FakeRequests()
    monkeypatch.setattr("shortsfactory.generative.providers.kling.requests", fake)
    backend = KlingBackend(env={"KLING_API_KEY": "secret-bearer-token"})
    assert backend.is_configured()
    job = backend.submit(_spec())
    assert job.job_id == "task-1"
    url, kw = fake.posts[0]
    assert "text2video" in url
    assert kw["headers"]["Authorization"] == "Bearer secret-bearer-token"
    assert "alg" not in kw["headers"]["Authorization"]  # no JWT header
    assert kw["json"]["model_name"] == "kling-v3.0-t2v"


def test_kling_official_env_alias_also_works(monkeypatch):
    fake = _FakeRequests()
    monkeypatch.setattr("shortsfactory.generative.providers.kling.requests", fake)
    backend = KlingBackend(env={"KLINGAI_API_KEY": "alias-key"})
    assert backend.is_configured()
    backend.submit(_spec())
    assert fake.posts[0][1]["headers"]["Authorization"] == "Bearer alias-key"


def test_kling_legacy_aksk_still_signed(monkeypatch):
    fake = _FakeRequests()
    monkeypatch.setattr("shortsfactory.generative.providers.kling.requests", fake)
    backend = KlingBackend(env={"KLING_ACCESS_KEY": "ak-1", "KLING_SECRET_KEY": "sk-1"})
    assert backend.is_configured()
    backend.submit(_spec())
    auth = fake.posts[0][1]["headers"]["Authorization"]
    assert auth.startswith("Bearer eyJ")  # JWT (base64url header)


def test_kling_requires_some_credential():
    backend = KlingBackend(env={"GEMINI_API_KEY": "unrelated"})
    assert not backend.is_configured()
    with pytest.raises(Exception) as excinfo:
        backend.submit(_spec())
    assert "KLING_API_KEY" in str(excinfo.value)


def test_kling_registry_key_names_include_modern_and_legacy():
    from shortsfactory.generative.video import _PROVIDER_KEYS

    assert "KLING_API_KEY" in _PROVIDER_KEYS["kling"]
    assert "KLINGAI_API_KEY" in _PROVIDER_KEYS["kling"]
    assert "KLING_ACCESS_KEY" in _PROVIDER_KEYS["kling"]


def test_kling_wrapper_never_leaks_secret(monkeypatch):
    from shortsfactory.generative.video import VideoBackendAIProvider

    secret = "k-secret-abcdef-123456"
    p = VideoBackendAIProvider(
        KlingBackend(env={"KLING_API_KEY": secret}),
        env={"KLING_API_KEY": secret},
    )
    assert p.is_configured()
    blob = json.dumps(p.describe(), ensure_ascii=False)
    assert secret not in blob
    assert "KLING_API_KEY" in blob  # variable NAME is shown, never the value


def test_kling_image2video_endpoint_selected(monkeypatch):
    fake = _FakeRequests()
    monkeypatch.setattr("shortsfactory.generative.providers.kling.requests", fake)
    backend = KlingBackend(env={"KLING_API_KEY": "bearer"})
    spec = _spec()
    spec.reference_images = ["C:/tmp/ref.png"]
    backend.submit(spec)
    url, kw = fake.posts[0]
    assert "image2video" in url
    assert kw["json"]["model_name"] == "kling-v3.0-i2v"
