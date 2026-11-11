"""Unit tests for the AnimationLab pre-render QC adapter (stage E3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from shortsfactory.generative.animlab_qc import (
    build_scene_description,
    fsm_check,
    run_pre_render_qc,
)

ANIMLAB_SRC = Path("D:/ShortsFactory/Animation_Intelligence_Lab/src")

pytestmark = pytest.mark.skipif(
    not (ANIMLAB_SRC / "animation_lab").is_dir(),
    reason="animation_lab не найден (D:/ShortsFactory/Animation_Intelligence_Lab/src)",
)


@pytest.fixture(autouse=True)
def _animlab_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(ANIMLAB_SRC))


def _probe_description():
    return dict(
        name="probe",
        fps=12,
        duration_seconds=3.0,
        characters={
            "steve": {"position": [0.30, 0.50], "rotation": 0.0},
            "alex": {"position": [0.70, 0.50], "rotation": 0.0},
        },
        props={
            "cube": {"position": [0.34, 0.50], "owner_id": "steve",
                     "state": "attached", "attachment_point": "hand_r"},
        },
        segments=[
            dict(id="s1", character_id="steve", action_type="idle",
                 start_time=0.0, end_time=3.0,
                 start={"position": [0.30, 0.50]},
                 end={"position": [0.30, 0.50]}),
            dict(id="s2", character_id="alex", action_type="idle",
                 start_time=0.0, end_time=3.0,
                 start={"position": [0.70, 0.50]},
                 end={"position": [0.70, 0.50]}),
        ],
        interactions=[],
    )


def test_build_scene_description_validates_refs():
    scene = build_scene_description(**_probe_description())
    assert scene.name == "probe"
    assert scene.fps == 12
    assert set(scene.characters) == {"steve", "alex"}
    assert scene.props["cube"].owner_id == "steve"


def test_run_pre_render_qc_passes_for_valid_scene():
    rep = run_pre_render_qc(_probe_description(), fps=12)
    assert rep["status"] in ("pass", "warn")
    assert rep["frames"] > 0
    assert rep["plan_validation"] == "pass"


def test_run_pre_render_qc_detects_overlap():
    desc = _probe_description()
    # сдвигаем Алекс (и его сегмент) вплотную к Стиву
    desc["characters"]["alex"]["position"] = [0.33, 0.50]
    desc["segments"][1]["start"]["position"] = [0.33, 0.50]
    desc["segments"][1]["end"]["position"] = [0.33, 0.50]
    rep = run_pre_render_qc(desc, fps=12)
    codes = {i["code"] for i in rep["issues"]}
    assert "character_overlap" in codes or "characters_too_close" in codes
    assert rep["status"] == "fail"


def test_fsm_check_valid_sequence():
    assert fsm_check([("idle", 0.0), ("walking", 1.0),
                      ("idle", 2.0)]) == []


def test_fsm_check_catches_illegal_transition():
    problems = fsm_check([("idle", 0.0), ("falling", 1.0)])
    assert problems and "falling" in problems[0]
