"""Tests for the free-local hybrid animation pipeline (Mission 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shortsfactory.generative.animation_plan import AnimationPlan, PlanError
from shortsfactory.generative.engine_dispatcher import render_plan
from shortsfactory.generative.freeanim import build_plan_for_idea
from shortsfactory.generative.blender_adapter import (
    build_blender_script,
    blender_available,
)

IDEA = "Майя находит светящийся камень в сумеречном лесу"


def _small_plan(duration: float = 4.0) -> AnimationPlan:
    return build_plan_for_idea(IDEA, title="Тест", duration=duration, width=180, height=320)


# ------------------------------------------------------------------- plan


def test_plan_is_valid_and_deterministic():
    a = _small_plan()
    b = _small_plan()
    assert a.ensure() is a
    assert a.to_dict() == b.to_dict()
    # must contain real pose-changing actions, not just camera movement
    actions = {act.action for act in a.actions}
    assert {"walk", "crouch", "pickup", "react", "joy"} <= actions
    for act in a.actions:
        assert act.duration > 0
        assert act.start + act.duration <= a.duration + 1e-6


def test_plan_compiles_to_valid_scene():
    from shortsfactory.animation.scene import load_scene
    import tempfile
    plan = _small_plan()
    scene_dict = plan.to_scene_dict()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "scene.json"
        p.write_text(json.dumps(scene_dict, ensure_ascii=False), encoding="utf-8")
        scene = load_scene(p)  # raises SceneError if invalid
        assert scene.environment == "forest"
        assert scene.character("maya") is not None
        assert scene.scene_object("stone") is not None
        assert len(scene.camera) >= 3
        zooms = [k.zoom for k in scene.camera]
        assert max(zooms) > 1.2  # a real wide→close transition


def test_plan_rejects_unknown_action():
    plan = _small_plan()
    plan.actions[1].action = "fly"  # unsupported
    with pytest.raises(PlanError):
        plan.ensure()


# ----------------------------------------------------------------- render


def test_render_plan_procedural_small(config, tmp_path, needs_voices):
    plan = _small_plan(duration=4.0)
    res = render_plan(plan, config, tmp_path, engine="procedural")
    assert res.status == "OK"
    assert res.engine_used == "procedural"
    video = Path(res.video)
    assert video.exists() and video.stat().st_size > 0
    anim = res.animation_result
    assert anim is not None
    assert anim.motion_checks["limbs_move_without_camera"] is True
    assert anim.motion_checks["character_walks"] is True
    assert anim.attachment_checks["verified"] is True
    assert anim.decode_ok


def test_dispatch_falls_back_when_no_blender(config, tmp_path, monkeypatch, needs_voices):
    import shortsfactory.generative.engine_dispatcher as disp
    monkeypatch.setattr(disp, "blender_available", lambda: False)
    plan = _small_plan(duration=4.0)
    res = render_plan(plan, config, tmp_path, engine="auto")
    assert res.engine_used == "procedural"
    assert res.status == "OK"


def test_dispatch_explicit_blender_unavailable_partial(config, tmp_path, monkeypatch, needs_voices):
    import shortsfactory.generative.engine_dispatcher as disp
    monkeypatch.setattr(disp, "blender_available", lambda: False)
    plan = _small_plan(duration=4.0)
    res = render_plan(plan, config, tmp_path, engine="blender")
    assert res.status == "PARTIAL"
    assert res.video == ""


def test_blender_script_generated_but_not_run(tmp_path):
    plan = _small_plan()
    script = build_blender_script(plan, tmp_path)
    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert "import bpy" in text
    assert "BLENDER_OK" in text
    assert "set_action" in text


def test_blender_available_flag_returns_bool():
    assert blender_available() in (True, False)


# ----------------------------------------------------------------- pipeline


def test_free_create_end_to_end_small(config, tmp_path, monkeypatch, needs_voices):
    import shortsfactory.generative.freeanim as free
    monkeypatch.setattr(free, "_expand_idea", lambda cfg, idea, model="": idea)
    # use a small plan for speed
    monkeypatch.setattr(free, "build_plan_for_idea",
                        lambda idea, **kw: _small_plan(duration=4.0))
    result = free.run_free_create(IDEA, tmp_path, config, engine="procedural", duration=4.0)
    assert result.status in {"READY", "PARTIAL"} or result.status == "WARNING"
    video = Path(result.video)
    assert video.exists() and video.stat().st_size > 0
    assert Path(result.plan_path).exists()
    assert Path(result.subtitles_srt).exists()
    assert Path(result.subtitles_ass).exists()
    assert Path(result.output_dir, "free_report.json").exists()
    assert result.qc.decode_ok is True
    assert result.qc.motion_ok is True
    assert result.qc.props_interacted is True
    assert result.qc.attachment_verified is True
    assert result.qc.subtitles_parsed is True
    assert result.mode == "free_local"


def test_free_create_rejects_nonempty_without_overwrite(config, tmp_path, needs_voices):
    (tmp_path / "lock").write_text("x", encoding="utf-8")
    import shortsfactory.generative.freeanim as free
    with pytest.raises(FileExistsError):
        free.run_free_create(IDEA, tmp_path, config)
    result = free.run_free_create(IDEA, tmp_path, config, overwrite=True, duration=4.0)
    assert result.video


def test_free_create_resume_reuses_render(config, tmp_path, monkeypatch, needs_voices):
    import shortsfactory.generative.freeanim as free
    monkeypatch.setattr(free, "_expand_idea", lambda cfg, idea, model="": idea)
    monkeypatch.setattr(free, "build_plan_for_idea",
                        lambda idea, **kw: _small_plan(duration=4.0))
    first = free.run_free_create(IDEA, tmp_path, config, engine="procedural", duration=4.0)
    video1 = first.video
    second = free.run_free_create(IDEA, tmp_path, config, engine="procedural",
                                  duration=4.0, resume=True)
    assert second.video == video1
    assert any("resume" in n.lower() for n in second.notes)


def test_free_local_never_creates_cloud_backend(config, tmp_path, monkeypatch, needs_voices):
    import shortsfactory.generative.freeanim as free
    import shortsfactory.generative.backend as backend
    monkeypatch.setattr(free, "_expand_idea", lambda cfg, idea, model="": idea)
    monkeypatch.setattr(free, "build_plan_for_idea",
                        lambda idea, **kw: _small_plan(duration=4.0))
    def _boom(*a, **k):
        raise AssertionError("free_local должен избегать облачных бэкендов")
    monkeypatch.setattr(backend, "create_backend", _boom)
    result = free.run_free_create(IDEA, tmp_path, config, engine="procedural", duration=4.0)
    assert result.video
