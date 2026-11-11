"""Unit tests for the public API layer (stage E1)."""
from __future__ import annotations

import json

import pytest

from shortsfactory import api
from shortsfactory.generative.blocky_story import fallback_storyboard


def test_save_story_writes_json(tmp_path):
    story = fallback_storyboard("Тест", scenes=2, lang="ru")
    p = api.save_story(story, tmp_path)
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["title"]


def test_generate_story_streams_via_callback(monkeypatch):
    seen = []

    def fake_generate(idea, *, scenes, lang, stream, on_token, attempts):
        assert idea == "тема"
        assert scenes == 3
        assert lang == "ru"
        assert stream is True
        if on_token:
            on_token("Привет")
            on_token(", мир!")
        return fallback_storyboard("тема", scenes=3, lang="ru")

    monkeypatch.setattr(api, "generate_storyboard", fake_generate)
    story = api.generate_story("тема", scenes=3, lang="ru",
                               stream=True, on_token=seen.append)
    assert "".join(seen) == "Привет, мир!"
    assert story.get("title")


def test_generate_story_flags_stream_default_false(monkeypatch):
    calls = {}

    def fake_generate(idea, *, scenes, lang, stream, on_token, attempts):
        calls["stream"] = stream
        return fallback_storyboard("тема", scenes=1, lang="ru")

    monkeypatch.setattr(api, "generate_storyboard", fake_generate)
    api.generate_story("тема")
    assert calls["stream"] is False


def test_render_scene_clip_rejects_unknown_engine():
    plan = api.ScenePlan(index=1, actor="steve", text="hi",
                         duration_s=5.0)
    with pytest.raises(ValueError):
        api.render_scene_clip(plan, "out", engine="voxel")


def test_render_job_dispatch_blocky_calls_clip(monkeypatch, tmp_path):
    job = api.RenderJob(
        scene=api.ScenePlan(index=3, actor="alex", text="hi",
                            duration_s=5.0),
        engine="blocky", fps=12, width=480, height=720, samples=8,
    )
    called = {}

    def fake_clip(plan, out_dir, *, fps, width, height, samples, timeout,
                  use_cache):
        called["plan"] = plan
        called["fps"] = fps
        called["use_cache"] = use_cache
        return tmp_path / f"scene_{plan.index:02d}_final.mp4"

    monkeypatch.setattr(api, "render_scene_clip", fake_clip)
    out = api.render_job(job, tmp_path, timeout=123, use_cache=False)
    assert called["plan"].index == 3
    assert called["fps"] == 12
    assert called["use_cache"] is False
    assert out == tmp_path / "scene_03_final.mp4"


def test_render_job_not_implemented_engines_raise():
    plan = api.ScenePlan(index=1, actor="steve", text="hi",
                         duration_s=5.0)
    for engine in ("voxel", "procedural", "skinned"):
        job = api.RenderJob(scene=plan, engine=engine)
        with pytest.raises(NotImplementedError):
            api.render_job(job, "out")


def test_render_job_unknown_engine_raises_value_error():
    plan = api.ScenePlan(index=1, actor="steve", text="hi",
                         duration_s=5.0)
    job = api.RenderJob(scene=plan, engine="magic")
    with pytest.raises(ValueError):
        api.render_job(job, "out")
