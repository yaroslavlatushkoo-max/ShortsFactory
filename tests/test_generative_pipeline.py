"""Planner, cinematic, references, and pipeline-level tests (Mission 3/4)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from shortsfactory.config import Config
from shortsfactory.generative.cinematic import plan_cinematic
from shortsfactory.generative.models import ScenePlan
from shortsfactory.generative.pipeline import run_ai_create
from shortsfactory.generative.planner import plan_story
from shortsfactory.generative.presets import build_presets, get_preset
from shortsfactory.generative.references import (
    build_character_bibles,
    build_environment_bibles,
    build_manifest,
    build_prompt,
    build_style_bible,
)


def _root() -> Path:
    # tests live in D:\ShortsFactory\tests
    return Path(__file__).resolve().parents[1]


def test_presets_registry():
    presets = build_presets()
    assert set(presets) >= {
        "cinematic3d", "stylized_film", "premium2d",
        "realistic_env", "painterly_fantasy", "documentary",
    }
    assert get_preset("cinematic3d").id == "cinematic3d"
    try:
        get_preset("nope")
        pytest.fail("should raise")
    except KeyError:
        pass


def test_planner_deterministic_fallback():
    scenes = plan_story("Scene one.\n\nScene two, longer.", llm=None)
    assert scenes and all(isinstance(s, ScenePlan) for s in scenes)


def test_planner_rejects_empty():
    try:
        plan_story("   ", llm=None)
        pytest.fail("expected ValueError")
    except ValueError:
        pass


def _scenes():
    return [
        ScenePlan(idx=0, location="forest", characters=["Milo"], dialogue="Hello there!",
                  action_summary="walks", emotion="joy", duration=5.0),
        ScenePlan(idx=1, location="forest", characters=["Milo", "Nina"],
                  action_summary="explore the wide clearing", emotion="neutral", duration=6.0),
    ]


def test_cinematic_meaningful_and_deterministic():
    preset = get_preset("cinematic3d")
    shots = plan_cinematic(_scenes(), preset)
    assert [s.shot_type for s in shots] == ["medium", "wide"]  # short dialogue -> medium, wide goal -> wide
    assert shots[0].mood == "warm and uplifted"
    again = plan_cinematic(_scenes(), preset)
    assert [s.to_dict() for s in shots] == [s.to_dict() for s in again]


def test_character_bible_stable_across_scenes():
    chars = build_character_bibles(_scenes())
    assert chars["Milo"].to_dict() == chars["Milo"].to_dict()
    assert "Milo" in chars and "Nina" in chars


def test_manifest_and_prompt_build():
    preset = get_preset("cinematic3d")
    scenes = _scenes()
    chars = build_character_bibles(scenes)
    envs = build_environment_bibles(scenes)
    style = build_style_bible(preset)
    shots = plan_cinematic(scenes, preset)
    prompt = build_prompt(scenes[0], shots[0], style, chars, envs)
    assert "Milo" in prompt and "vertical 9:16" in prompt
    manifest = build_manifest(scenes, chars, envs, style, ["p1", "p2"])
    assert manifest["continuity"]["identity_guarantee"].startswith("NOT")


def test_pipeline_planning_artifacts_without_backend(tmp_path: Path):
    cfg = Config(root=_root())
    res = run_ai_create(
        "A cat walks into a forest and meets a bird.",
        tmp_path,
        cfg,
        mode="generative",
        provider="disabled",
    )
    assert res.status == "WARNING"
    assert res.ran_generative is False
    assert not res.video
    assert (tmp_path / "scenes.json").exists()
    assert (tmp_path / "cinematic_plan.json").exists()
    assert (tmp_path / "character_bible.json").exists()
    assert (tmp_path / "scene_continuity_manifest.json").exists()


def _disable_text_provider(monkeypatch) -> None:
    # Deterministic/speedy: never call an LLM during these pipeline tests.
    import shortsfactory.generative.pipeline as pl

    monkeypatch.setattr(pl, "_resolve_text_provider", lambda *a, **k: None)


_DEMO = (
    "Майя гуляет по лесу и находит светящийся камень. Она поднимает его, и "
    "вокруг просыпаются огоньки. Появляется Хранитель леса и кланяется ей. Она "
    "кланяется в ответ и кладёт камень на пень. Свет возвращается."
)


def test_pipeline_dry_run_plans_without_paid_calls(tmp_path: Path, monkeypatch):
    _disable_text_provider(monkeypatch)
    cfg = Config(root=_root())
    res = run_ai_create(
        _DEMO, tmp_path, cfg,
        mode="generative", provider="disabled", dry_run=True,
    )
    assert res.status == "PLANNED"
    assert res.ran_generative is False
    assert not res.video
    manifest = json.loads((tmp_path / "scene_continuity_manifest.json").read_text(encoding="utf-8"))
    for key in ("stages", "providers", "cost", "limits", "timestamps"):
        assert key in manifest, f"manifest missing key {key}"
    # no secret values may leak into the manifest
    blob = json.dumps(manifest, ensure_ascii=False)
    for key, value in os.environ.items():
        if value and ("KEY" in key.upper() or "SECRET" in key.upper() or "TOKEN" in key.upper()):
            assert value not in blob, f"secret leaked: {key}"


def test_pipeline_dry_run_limits_generations(tmp_path: Path, monkeypatch):
    _disable_text_provider(monkeypatch)
    cfg = Config(root=_root())
    res = run_ai_create(
        _DEMO, tmp_path, cfg,
        mode="generative", provider="disabled", dry_run=True,
        max_generations=2,
    )
    assert res.status == "PLANNED"
    # deterministic fallback yields 1 scene, so the cap on 2 collapses to 1/1
    assert any("1/1" in note for note in res.notes), res.notes
    assert res.manifest_path is not None and Path(res.manifest_path).exists()
    manifest = json.loads((tmp_path / "scene_continuity_manifest.json").read_text(encoding="utf-8"))
    assert manifest["limits"].get("planned_generations") == 1
    # the cap of 2 collapses to the single planned scene
    assert manifest["limits"].get("max_generations") == 1


def test_kling_video_model_override_reaches_request(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from shortsfactory.generative.backend import create_backend
    from shortsfactory.generative.models import SceneGenerationSpec

    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = dict(json or {})
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"data": {"task_id": "t1", "status": "submitted"}},
        )

    monkeypatch.setattr("shortsfactory.generative.providers.kling.requests.post", fake_post)

    text_backend = create_backend("kling", env={"KLING_API_KEY": "fake-key"})
    text_backend.apply_model_override("kling-v3.0-t2v-custom")
    text_spec = SceneGenerationSpec(scene_idx=0, prompt="a flying cat", duration=5, reference_images=[])
    text_backend.submit(text_spec)
    assert captured["url"].endswith("/v1/videos/text2video")
    assert captured["body"]["model_name"] == "kling-v3.0-t2v-custom"

    # image-to-video path must use KLING_IMAGE_MODEL env, not the t2v override.
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"dummy")
    img_backend = create_backend(
        "kling",
        env={"KLING_API_KEY": "fake-key", "KLING_IMAGE_MODEL": "kling-v3.0-i2v-custom"},
    )
    img_spec = SceneGenerationSpec(scene_idx=1, prompt="x", duration=5, reference_images=[ref])
    img_backend.submit(img_spec)
    assert captured["url"].endswith("/v1/videos/image2video")
    assert captured["body"]["model_name"] == "kling-v3.0-i2v-custom"


def test_pipeline_dry_run_full_vs_limited_cost(tmp_path: Path, monkeypatch):
    _disable_text_provider(monkeypatch)
    cfg = Config(root=_root())
    story = "Первая сцена: герой идёт по лесу.\n\nВторая сцена: герой встречает спутника."
    res = run_ai_create(
        story, tmp_path, cfg,
        mode="generative", provider="kling", dry_run=True,
        max_generations=1,
    )
    assert res.status == "PLANNED"
    assert res.cost is not None and res.cost_limited is not None
    # limited run (1 generation) must be cheaper than the full 2-scene plan
    assert res.cost_limited.estimated_usd < res.cost.estimated_usd
    assert any("лимит" in note for note in res.notes), res.notes
