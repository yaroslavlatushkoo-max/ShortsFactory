"""F2.5: walk_to (подход персонажа к предмету) и отказ от придвигания предметов.

Тесты:
- timeline_from_actions движет root к локальной цели (params["target"] = [dx, dy]);
- без target — прежнее поведение (walk вперёд);
- маленькое расстояние не падает (fallback на idle);
- stage_interactions НЕ двигает предмет, а добавляет walk с target перед grab/pickup;
- если предмет уже в зоне досягаемости — walk не вставляется.
"""
import math

import pytest

from shortsfactory.generative.animation_pipeline.skinned_hero import timeline_from_actions
from shortsfactory.generative.blocky_story import stage_interactions

FPS = 30


def _last_hips(actions: list[dict], duration: float):
    tl = timeline_from_actions(actions, duration=duration, fps=FPS)
    return list(tl.sample_frames(FPS)[-1].root)


def _first_hips(actions: list[dict], duration: float):
    tl = timeline_from_actions(actions, duration=duration, fps=FPS)
    return list(tl.sample_frames(FPS)[0].root)


def test_walk_with_target_moves_toward_local_point():
    actions = [
        {"action": "walk", "start": 0.0, "duration": 2.5,
         "params": {"target": [3.0, 0.0]}},
    ]
    first = _first_hips(actions, 3.0)
    last = _last_hips(actions, 3.0)
    assert last[0] > first[0] + 1.5, f"root должен сместиться по X: {first} -> {last}"
    # цель 3.0; из-за lead/stop примерно достигаем окрестности
    assert abs(last[0] - 3.0) < 1.2, f"root.x должен быть близок к 3.0: {last}"


def test_walk_with_target_keeps_y_axis():
    actions = [
        {"action": "walk", "start": 0.0, "duration": 2.5,
         "params": {"target": [0.0, 2.0]}},
    ]
    first = _first_hips(actions, 3.0)
    last = _last_hips(actions, 3.0)
    assert abs(last[0] - first[0]) < 0.5, f"движение идёт по Y: {first} -> {last}"
    assert last[1] > first[1] + 0.8


def test_walk_without_target_still_walks_forward():
    actions = [
        {"action": "walk", "start": 0.0, "duration": 2.5},
    ]
    first = _first_hips(actions, 3.0)
    last = _last_hips(actions, 3.0)
    assert last[0] > first[0] + 0.5


def test_walk_tiny_target_falls_back_to_idle():
    actions = [
        {"action": "walk", "start": 0.0, "duration": 1.5,
         "params": {"target": [0.01, 0.0]}},
    ]
    # не должно падать; root почти не меняется
    last = _last_hips(actions, 2.0)
    assert abs(last[0]) < 0.6


def _scene_with_far_prop():
    return {
        "title": "t",
        "duration": 6.0,
        "environment": "cave",
        "ambience": "cave",
        "actors": [
            {
                "character": "steve",
                "origin": [0.0, 0.0, 0.0],
                "actions": [
                    {"action": "idle", "start": 0.0, "duration": 1.0},
                    {"action": "grab", "start": 2.0, "duration": 1.0},
                ],
            }
        ],
        "props": [
            {"preset": "glowing_cube", "name": "cube", "x": 4.0, "y": 0.0},
        ],
    }


def test_stage_interactions_moves_actor_not_prop():
    scene = _scene_with_far_prop()
    adjustments = stage_interactions(scene)
    cube = next(p for p in scene["props"] if p["name"] == "cube")
    # предмет НЕ двигается (никаких придвиганий)
    assert cube["x"] == 4.0 and cube["y"] == 0.0
    # у актёра появился walk с target, нацеленным на предмет
    walk = next(
        (a for a in scene["actors"][0]["actions"] if a.get("action") == "walk"), None
    )
    assert walk is not None, f"ожидался walk, adjustments={adjustments}"
    target = walk["params"]["target"]
    assert abs(target[0] - 4.0) < 1.0
    assert abs(target[1] - 0.0) < 1.0
    # grab остался на месте (порядок не сломан)
    verbs = [a["action"] for a in scene["actors"][0]["actions"]]
    assert verbs.index("walk") < verbs.index("grab")
    assert any("придвинут" not in a for a in adjustments)


def test_stage_interactions_no_walk_when_close():
    scene = _scene_with_far_prop()
    scene["props"][0]["x"] = 0.3
    scene["props"][0]["y"] = 0.0
    stage_interactions(scene)
    verbs = [a["action"] for a in scene["actors"][0]["actions"]]
    assert "walk" not in verbs


# ------------------------------------------------- F3: авто-режиссура камеры


def _two_actor_scene():
    return {
        "title": "t",
        "duration": 6.0,
        "fps": 30,
        "actors": [
            {"character": "steve", "origin": [-0.6, 0.9, 0.0]},
            {"character": "alex", "origin": [0.7, 0.9, 0.0]},
        ],
        "props": [],
        "dialogue": [
            {"character": "steve", "text": "Привет.", "start": 1.0, "duration": 1.5},
            {"character": "alex", "text": "И тебе.", "start": 3.0, "duration": 1.5},
        ],
    }


def _prop_action_scene():
    return {
        "title": "t",
        "duration": 6.0,
        "fps": 30,
        "actors": [
            {"character": "steve", "origin": [0.0, 0.9, 0.0],
             "actions": [
                 {"action": "idle", "start": 0.0, "duration": 1.0},
                 {"action": "grab", "start": 2.0, "duration": 1.0,
                  "params": {"prop": "cube"}},
             ]},
        ],
        "props": [
            {"preset": "glowing_cube", "name": "cube", "x": 4.0, "y": 0.0},
        ],
    }


def test_plan_scene_shots_two_actors_never_crop_head():
    from shortsfactory.generative.blocky_camera import plan_scene_shots, validate_shot_list

    scene = _two_actor_scene()
    cuts = plan_scene_shots(scene["dialogue"], scene["actors"], scene["props"],
                            duration=6.0, fps=30)
    assert cuts, "план не должен быть пуст"
    assert cuts[0]["frame"] == 1
    assert validate_shot_list(cuts, 6.0, 30) == []
    for cut in cuts:
        h = float(cut["height"])
        cz = float(cut["center"][2])
        assert cz - h / 2.0 <= 0.05, f"пол уходит из кадра: {cut}"
        assert cz + h / 2.0 >= 1.81 + 0.15, f"голова обрезается: {cut}"


def test_plan_scene_shots_uses_actor_positions():
    from shortsfactory.generative.blocky_camera import plan_scene_shots

    scene = _two_actor_scene()
    cuts = plan_scene_shots(scene["dialogue"], scene["actors"], scene["props"],
                            duration=6.0, fps=30)
    # средняя точка двух героев ≈ 0.05; план должен быть около неё
    assert any(abs(float(c["center"][0]) - 0.05) < 0.8 for c in cuts)


def test_plan_scene_shots_focuses_on_far_prop():
    from shortsfactory.generative.blocky_camera import plan_scene_shots

    scene = _prop_action_scene()
    cuts = plan_scene_shots([], scene["actors"], scene["props"],
                            duration=6.0, fps=30)
    # событие grab на кубе (4,0) даёт план, смотрящий на куб
    prop_cuts = [c for c in cuts if float(c["center"][0]) > 2.5]
    assert prop_cuts, f"ожидался план на предмет, cuts={cuts}"
    assert any(c.get("name") == "insert" for c in prop_cuts)
