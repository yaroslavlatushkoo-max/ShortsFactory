"""C7–C9: director of photography for blocky scenes."""

from __future__ import annotations

import pytest

from shortsfactory.generative.blocky_camera import (
    MIN_SHOT_SECONDS,
    SHOT_PRESETS,
    plan_shot_list,
    shot_summary,
    validate_shot_list,
)
from shortsfactory.generative.blocky_scene import BlockyScene


def _actors():
    return [
        {"character": "steve", "origin": [-0.6, 0.95, 0.0]},
        {"character": "alex", "origin": [0.6, 0.95, 0.0]},
    ]


def _dialogue():
    return [
        {"character": "steve", "text": "Первый", "start": 0.8, "duration": 2.0,
         "emotion": "confused"},
        {"character": "alex", "text": "Второй", "start": 3.2, "duration": 2.2},
        {"character": "steve", "text": "Третий", "start": 5.8, "duration": 2.2,
         "emotion": "surprised"},
        {"character": "alex", "text": "Четвёртый", "start": 8.4, "duration": 2.0},
    ]


def test_shot_presets_are_ordered_framings():
    assert SHOT_PRESETS["establishing"]["height"] > SHOT_PRESETS["wide"]["height"]
    assert SHOT_PRESETS["wide"]["height"] > SHOT_PRESETS["medium"]["height"]
    assert SHOT_PRESETS["medium"]["height"] > SHOT_PRESETS["close_up"]["height"]
    assert SHOT_PRESETS["insert"]["height"] == min(
        preset["height"] for preset in SHOT_PRESETS.values()
    )
    assert SHOT_PRESETS["over_shoulder"]["yaw"] > 0


def test_plan_has_establishing_cuts_reactions_and_final_wide():
    shots = plan_shot_list(_dialogue(), _actors(), 14.0, fps=30)
    assert shots[0]["frame"] == 1
    assert shots[0]["name"] == "establishing"
    frames = [shot["frame"] for shot in shots]
    assert frames == sorted(frames) and len(frames) == len(set(frames))
    names = [shot["name"] for shot in shots]
    assert "close_up" in names, "эмоциональная реплика не получила крупный план"
    assert "reaction" in names, "слушатель не получил реакцию"
    assert names[-1] == "wide"
    assert shots[-1]["frame"] > 8.4 * 30
    assert validate_shot_list(shots, 14.0, 30) == []


def test_every_move_stays_inside_its_own_shot():
    shots = plan_shot_list(_dialogue(), _actors(), 14.0, fps=30)
    for index, shot in enumerate(shots):
        move = shot.get("move")
        if not move:
            continue
        end = shots[index + 1]["frame"] - 1 if index + 1 < len(shots) else 420
        assert shot["frame"] < move["to_frame"] <= end, (index, shot)


def test_no_dialogue_gives_one_push_in_shot():
    shots = plan_shot_list([], _actors(), 6.0, fps=30)
    assert len(shots) == 1
    assert shots[0]["frame"] == 1 and shots[0]["name"] == "wide"
    assert shots[0].get("move"), "без диалога камера должна хотя бы наезжать"
    assert validate_shot_list(shots, 6.0, 30) == []


def test_shot_list_rejects_broken_plans():
    shots = [
        {"frame": 5, "center": [0, 0, 1], "height": 1.5},
        {"frame": 6, "center": [0, 0], "height": 0.0},
        {"frame": 4, "center": [0, 0, 1], "height": 1.5,
         "move": {"to_frame": 4, "height": 1.2}},
    ]
    issues = validate_shot_list(shots, 10.0, 30)
    text = " ".join(issues)
    assert "а не с 1" in text
    assert "короче" in text
    assert "center" in text
    assert "высота" in text
    assert validate_shot_list([], 10.0, 30) == ["Список планов пуст."]


def test_min_shot_constant_is_used_by_the_validator():
    frame = int(round(MIN_SHOT_SECONDS * 30))
    shots = [
        {"frame": 1, "center": [0, 0, 1], "height": 2},
        {"frame": 1 + max(1, frame // 3), "center": [0, 0, 1], "height": 2},
    ]
    assert any("короче" in issue for issue in validate_shot_list(shots, 10.0, 30))

def _scene_dict(**extra):
    data = {
        "title": "Камера",
        "duration": 10.0,
        "fps": 30,
        "actors": [
            {"character": "steve", "origin": [-0.6, 0.9, 0.0],
             "duration": 10.0, "actions": [{"action": "idle", "start": 0.0,
                                            "duration": 10.0}]},
            {"character": "alex", "origin": [0.6, 0.9, 0.0],
             "duration": 10.0, "actions": [{"action": "idle", "start": 0.0,
                                            "duration": 10.0}]},
        ],
        "dialogue": [
            {"character": "steve", "text": "Привет", "start": 1.0,
             "duration": 1.5, "emotion": "happy"},
            {"character": "alex", "text": "Привет", "start": 3.0,
             "duration": 1.5},
        ],
    }
    data.update(extra)
    return data


def test_auto_camera_fills_an_empty_cut_list():
    scene = BlockyScene.from_dict(_scene_dict(auto_camera=True))
    assert scene.auto_camera is True
    assert scene.camera_cuts
    assert scene.camera_cuts[0]["frame"] == 1
    assert validate_shot_list(scene.camera_cuts, scene.duration, scene.fps) == []
    scene.validate()


def test_auto_camera_keeps_authored_cuts():
    authored = [{"frame": 1, "center": [0.0, 0.0, 1.0], "height": 2.4}]
    scene = BlockyScene.from_dict(_scene_dict(auto_camera=True, camera_cuts=authored))
    assert scene.camera_cuts == authored


def test_auto_camera_is_off_by_default():
    scene = BlockyScene.from_dict(_scene_dict())
    assert scene.auto_camera is False
    assert scene.camera_cuts == []


def test_shot_summary_reports_lengths():
    shots = plan_shot_list(_dialogue(), _actors(), 14.0, fps=30)
    rows = shot_summary(shots, 30)
    assert len(rows) == len(shots)
    assert rows[0]["length_s"] == pytest.approx(
        (shots[1]["frame"] - shots[0]["frame"]) / 30, abs=1e-3
    )
    assert rows[-1]["length_s"] is None  # the last shot runs to the end
    assert all(row["height"] > 0 for row in rows)
