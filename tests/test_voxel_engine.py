"""Tests for the Voxel Animation Engine v1 (mock-based, no heavy Blender runs)."""


import pytest

from shortsfactory.generative.animation_plan import (
    ENGINE_CHOICES,
    ENGINE_PROCEDURAL,
    ENGINE_VOXEL,
    AnimationPlan,
    PlanAction,
    PlanError,
)
from shortsfactory.generative.voxel_engine import build_voxel_script
from shortsfactory.generative.voxel_engine import voxel_character, voxel_interactions
from shortsfactory.generative.voxel_engine.voxel_validation import validate_voxel_plan

IDEA = "Майя находит светящийся куб в лесу"


def _small_plan(duration: float = 6.0) -> AnimationPlan:
    from shortsfactory.generative.freeanim import build_plan_for_idea
    return build_plan_for_idea(IDEA, duration=duration, width=180, height=320)


def test_engine_choices_include_voxel():
    assert ENGINE_VOXEL in ENGINE_CHOICES
    assert ENGINE_PROCEDURAL in ENGINE_CHOICES


def test_voxel_script_generates_and_compiles(tmp_path):
    plan = _small_plan()
    script = build_voxel_script(plan, tmp_path)
    assert script.exists()
    src = script.read_text(encoding="utf-8")
    assert "add_cube" in src
    assert "set_action" in src
    assert "VOXEL_OK" in src
    # must be valid Python (syntactically) so Blender can load it
    compile(src, str(script), "exec")


def test_character_builder_contains_rig_limbs():
    src = voxel_character.build_hero_source(char_name="TestHero", px=0.0, py=1.0)
    for token in ("head", "torso", "arm_l", "arm_r", "leg_l", "leg_r", "eye_l", "mouth", "HERO['root']"):
        assert token in src


def test_character_mouth_is_a_thread():
    src = voxel_character.build_hero_source(char_name="TestHero", px=0.0, py=1.0)
    mouth_lines = [ln for ln in src.splitlines() if "_mouth" in ln]
    assert len(mouth_lines) >= 5   # several small segments form the thread
    assert "hero_mouth" in src     # dedicated lip colour, not hair


def test_interaction_source_contains_attach_parenting():
    plan = _small_plan()
    obj = plan.objects[0]
    ev = plan.events[0]
    src = voxel_interactions.interaction_source([obj], [ev], fps=30)
    assert "parent = HERO['arm_r']" in src
    assert "matrix_parent_inverse" in src


def test_voxel_validation_catches_unknown_action(tmp_path):
    plan = _small_plan()
    plan.actions.append(PlanAction(character="maya", action="fly", start=0.0, duration=1.0))
    with pytest.raises(PlanError):
        build_voxel_script(plan, tmp_path)


def test_voxel_validation_catches_bad_attach_point():
    plan = _small_plan()
    # mutate an attach event to a bad point
    plan.events[0].point = "elbow"
    errors = validate_voxel_plan(plan)
    assert any("точка захвата" in e for e in errors)


def test_dispatcher_voxel_fails_cleanly_without_blender(monkeypatch):
    from shortsfactory.generative import engine_dispatcher
    from shortsfactory.config import load_config
    from pathlib import Path
    monkeypatch.setattr(engine_dispatcher, "blender_available", lambda: False)
    res = engine_dispatcher.render_plan(_small_plan(), load_config(Path(".")), Path("x"), engine="voxel")
    assert res.status == "PARTIAL"
    assert res.engine_used == ENGINE_VOXEL


def test_dispatcher_voxel_passes_video(monkeypatch):
    from shortsfactory.generative import engine_dispatcher
    from shortsfactory.generative.voxel_engine import VoxelRenderResult
    from shortsfactory.config import load_config
    from pathlib import Path
    monkeypatch.setattr(engine_dispatcher, "blender_available", lambda: True)
    monkeypatch.setattr(
        engine_dispatcher,
        "render_voxel",
        lambda plan, out: VoxelRenderResult(status="OK", video="out/final.mp4"),
    )
    res = engine_dispatcher.render_plan(_small_plan(), load_config(Path(".")), Path("out"), engine="voxel")
    assert res.status == "OK"
    assert res.video == "out/final.mp4"


def test_procedural_still_works(tmp_path, needs_voices):
    # adding voxel must not break procedural routing (real small render)
    from shortsfactory.generative import engine_dispatcher
    from shortsfactory.config import load_config
    from pathlib import Path
    plan = _small_plan(duration=4.0)
    res = engine_dispatcher.render_plan(plan, load_config(Path(".")), tmp_path, engine="procedural")
    assert res.status == "OK"
    assert res.engine_used == ENGINE_PROCEDURAL
    assert Path(res.video).exists()


def test_quality_plan_valid_and_compiles(tmp_path):
    from shortsfactory.generative.voxel_engine.quality_plan import build_quality_cube_plan
    plan = build_quality_cube_plan(duration=9.0, width=360, height=640)
    plan.ensure()
    script = build_voxel_script(plan, tmp_path)
    compile(script.read_text(encoding="utf-8"), str(script), "exec")


def test_quality_plan_has_expected_action_rhythm():
    from shortsfactory.generative.voxel_engine.quality_plan import build_quality_cube_plan
    plan = build_quality_cube_plan(duration=9.0)
    actions = [a.action for a in plan.actions]
    for expected in ("idle", "turn", "walk", "bend", "pickup", "react", "hold"):
        assert expected in actions
    # attach event exists
    assert any(e.kind == "attach" for e in plan.events)


def test_camera_source_is_staged_and_tracks():
    from shortsfactory.generative.voxel_engine import voxel_camera
    src = voxel_camera.camera_source([], fps=30, px=0.0, py=3.0)
    assert "SHOTS" in src
    assert "_cam_setup()" in src
    assert "sensor_fit = 'VERTICAL'" in src
    assert "TRACK_TO" in src


def test_rig_new_proportions():
    from shortsfactory.generative.voxel_engine.voxel_rig import HIP_Z, RIG_PART_BOXES
    assert HIP_Z == pytest.approx(0.82)
    assert RIG_PART_BOXES["leg_l"][2] >= 0.75  # legs reach ground
    assert RIG_PART_BOXES["arm_l"][2] >= 0.5


def test_asset_catalog_loads_and_validates():
    from shortsfactory.generative.voxel_engine import AssetCatalog
    cat = AssetCatalog.load()
    assert cat.validate() == []
    assert "hero_v1" in cat.ids("characters")
    assert "pickup" in cat.ids("clips")
    assert "three_quarter" in cat.ids("shots")


def test_asset_catalog_validation_rejects_missing_asset():
    from shortsfactory.generative.voxel_engine import AssetCatalog
    cat = AssetCatalog.load()
    errors = cat.validate_refs({"character": "nobody", "environment": "forest_blocky",
                               "props": ["cube"], "clips": ["walk"], "shots": ["wide"]})
    assert any("nobody" in e for e in errors)


def test_asset_plan_builds_and_passes_reference_validation(tmp_path):
    from shortsfactory.generative.voxel_engine import AssetCatalog, build_asset_plan
    from shortsfactory.generative.voxel_engine.asset_pipeline import validate_asset_plan
    cat = AssetCatalog.load()
    p = build_asset_plan(duration=9.0, width=360, height=640)
    assert validate_asset_plan(p, cat) == []
    assert p.production is not None


def test_asset_plan_rejects_unknown_clip():
    from shortsfactory.generative.voxel_engine import AssetCatalog, build_asset_plan
    from shortsfactory.generative.voxel_engine.asset_pipeline import validate_asset_plan
    from shortsfactory.generative.animation_plan import PlanAction
    cat = AssetCatalog.load()
    p = build_asset_plan(duration=9.0)
    p.actions.append(PlanAction(character="hero", action="moonwalk", start=0.0, duration=0.5))
    errors = validate_asset_plan(p, cat)
    assert any("moonwalk" in e for e in errors)


def test_asset_plan_script_compiles(tmp_path):
    from shortsfactory.generative.voxel_engine import AssetCatalog, build_asset_plan, build_voxel_script
    from shortsfactory.generative.voxel_engine.asset_pipeline import _shots_table
    cat = AssetCatalog.load()
    p = build_asset_plan(duration=9.0, width=360, height=640)
    script = build_voxel_script(p, tmp_path, shots=_shots_table(cat))
    compile(script.read_text(encoding="utf-8"), str(script), "exec")


def test_dispatcher_asset_routes(monkeypatch):
    from shortsfactory.generative import engine_dispatcher
    from shortsfactory.generative.voxel_engine import VoxelRenderResult
    from shortsfactory.generative.voxel_engine.asset_pipeline import build_asset_plan
    from shortsfactory.config import load_config
    from pathlib import Path
    monkeypatch.setattr(engine_dispatcher, "blender_available", lambda: True)
    monkeypatch.setattr(engine_dispatcher, "render_asset",
                        lambda plan, out_dir, catalog=None: VoxelRenderResult(status="OK", video="out/final.mp4", engine="asset"))
    res = engine_dispatcher.render_plan(build_asset_plan(duration=9.0), load_config(Path(".")), Path("out"), engine="asset")
    assert res.status == "OK"
    assert res.engine_used == "asset"
    assert res.video == "out/final.mp4"
