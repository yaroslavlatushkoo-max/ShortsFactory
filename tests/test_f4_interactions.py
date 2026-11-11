"""F4: физическая передача предметов (handoff) и строгая валидация действий.

Тесты:
- два персонажа поочерёдно хватают один предмет -> авто-постановка transfer
  (рукопожатие), сцена остаётся валидной;
- если получатель далеко -> добавляется walk-подход;
- без второго grab transfer не создаётся;
- B-18: неизвестные действия/параметры и битые ссылки на пропсы попадают
  в reject log, а не удаляются молча; битые ссылки снимаются с действия.
"""
from __future__ import annotations

import math


from shortsfactory.generative.animation_pipeline.skinned_hero import (
    encode_frames,
    stance_windows,
    timeline_from_actions,
)
from shortsfactory.generative.blocky_scene import BlockyScene
from shortsfactory.generative.blocky_story import parse_storyboard, stage_interactions


def _handoff_scene(*, dist: float = 1.3, grab_at: float = 4.0) -> dict:
    return {
        "title": "handoff",
        "duration": 7.0,
        "environment": "cave",
        "ambience": "cave",
        "actors": [
            {
                "character": "steve",
                "origin": [-0.6, 0.95, 0.0],
                "actions": [
                    {"action": "idle", "start": 0.0, "duration": 2.0},
                    {"action": "grab", "start": 2.0, "duration": 1.0,
                     "params": {"prop": "key"}},
                    {"action": "hold", "start": 3.0, "duration": 4.0,
                     "params": {"prop": "key"}},
                ],
            },
            {
                "character": "alex",
                "origin": [0.7 + (dist - 1.3), 0.95, 0.0],
                "actions": [
                    {"action": "idle", "start": 0.0, "duration": grab_at},
                    {"action": "grab", "start": grab_at, "duration": 1.0,
                     "params": {"prop": "key"}},
                ],
            },
        ],
        "props": [
            {"preset": "key", "x": -0.6, "y": 0.9, "name": "key"},
        ],
    }


def test_handoff_is_planned_for_two_grabbers():
    scene = _handoff_scene()
    adjustments = stage_interactions(scene)
    key = next(p for p in scene["props"] if p["name"] == "key")
    transfer = key["carry"]["transfer"]
    assert transfer["character"] == "alex"
    assert transfer["bone"] == "hand_L"
    assert float(transfer["start"]) >= float(key["carry"]["start"])
    assert any("рукопожатие" in a for a in adjustments)
    # сцена остаётся валидной (validate не падает)
    BlockyScene.from_dict(scene).validate()


def test_handoff_keeps_prop_in_givers_hand_until_contact():
    scene = _handoff_scene()
    stage_interactions(scene)
    key = next(p for p in scene["props"] if p["name"] == "key")
    transfer = key["carry"]["transfer"]
    # передача не начинается раньше первичного захвата Стива
    assert float(transfer["start"]) >= float(key["carry"]["start"])
    # и происходит до конца сцены (контакт в пределах фильма)
    assert float(transfer["start"]) < 7.0
    assert transfer["gap"] == 0.7


def test_handoff_adds_walk_when_taker_is_far():
    scene = _handoff_scene(dist=2.6)
    adjustments = stage_interactions(scene)
    alex = next(a for a in scene["actors"] if a["character"] == "alex")
    verbs = [a["action"] for a in alex["actions"]]
    assert "walk" in verbs
    assert any("подходит" in a for a in adjustments)
    BlockyScene.from_dict(scene).validate()


def test_handoff_without_transfer_when_single_grabber():
    scene = _handoff_scene()
    scene["actors"][1]["actions"] = [
        {"action": "idle", "start": 0.0, "duration": 7.0}
    ]
    stage_interactions(scene)
    key = next(p for p in scene["props"] if p["name"] == "key")
    assert "transfer" not in key.get("carry", {})


def test_continued_carry_adds_hold_action():
    """Если персонаж продолжает нести предмет в следующей сцене и у него нет
    действия с руками, prep добавляет ему hold — иначе validate_carry падает."""
    from shortsfactory.generative.blocky_continuity import (
        WorldState,
        prepare_scene_continuity,
        snapshot_final_state,
    )

    world = WorldState()
    s1 = {
        "title": "s1",
        "duration": 4.0,
        "environment": "forest",
        "actors": [
            {"character": "steve", "origin": [-0.6, 0.95, 0.0],
             "actions": [
                 {"action": "grab", "start": 0.5, "duration": 1.0,
                  "params": {"prop": "key"}},
             ]},
        ],
        "props": [
            {"preset": "key", "x": -0.6, "y": 0.9, "name": "key",
             "carry": {"character": "steve", "bone": "hand_R",
                       "start": 0.5, "release": -1.0}},
        ],
    }
    p1, _ = prepare_scene_continuity(s1, world, scene_index=1)
    snapshot_final_state(p1, world, scene_index=1)
    assert world.carried["key"] == "steve"

    s2 = {
        "title": "s2",
        "duration": 4.0,
        "environment": "forest",
        "actors": [
            {"character": "steve", "origin": [-0.6, 0.95, 0.0],
             "actions": [
                 {"action": "idle", "start": 0.0, "duration": 4.0},
             ]},
        ],
        "props": [
            {"preset": "key", "x": -0.6, "y": 0.9, "name": "key"},
        ],
    }
    p2, issues = prepare_scene_continuity(s2, world, scene_index=2)
    assert not issues
    key = next(p for p in p2["props"] if p["name"] == "key")
    assert key["carry"]["character"] == "steve"
    steve = next(a for a in p2["actors"] if a["character"] == "steve")
    verbs = [a["action"] for a in steve["actions"]]
    assert "hold" in verbs
    # сцена остаётся валидной
    BlockyScene.from_dict(p2).validate()


def test_handoff_hold_plus_grab_assigns_carry_to_giver_and_transfer_to_taker():
    scene = {
        "title": "handoff-hold",
        "duration": 7.0,
        "environment": "forest",
        "ambience": "forest",
        "actors": [
            {
                "character": "steve",
                "origin": [-0.6, 0.95, 0.0],
                "actions": [
                    {"action": "hold", "start": 0.0, "duration": 5.0,
                     "params": {"prop": "cube"}},
                    {"action": "release", "start": 5.0, "duration": 1.0,
                     "params": {"prop": "cube"}},
                ],
            },
            {
                "character": "alex",
                "origin": [0.6, 0.95, 0.0],
                "actions": [
                    {"action": "grab", "start": 4.6, "duration": 1.0,
                     "params": {"prop": "cube"}},
                ],
            },
        ],
        "props": [
            {"preset": "glowing_cube", "x": -0.6, "y": 0.9,
             "name": "cube"},
        ],
    }
    adjustments = stage_interactions(scene)
    cube = next(p for p in scene["props"] if p["name"] == "cube")
    carry = cube["carry"]
    assert carry["character"] == "steve"  # передающий держит
    assert float(carry["release"]) >= 4.6
    transfer = carry["transfer"]
    assert transfer["character"] == "alex"
    assert transfer["bone"] == "hand_L"
    assert float(transfer["start"]) >= 4.25
    assert float(transfer["start"]) < 4.7
    assert transfer["gap"] == 0.7
    assert any("рукопожатие" in a for a in adjustments)
    BlockyScene.from_dict(scene).validate()


def test_handoff_replaces_excessive_walks_with_single_approach():
    scene = {
        "title": "t",
        "duration": 8.0,
        "environment": "cave",
        "ambience": "cave",
        "actors": [
            {"character": "steve", "origin": [-0.6, 0.95, 0.0],
             "actions": [
                 {"action": "hold", "start": 0.0, "duration": 5.0,
                  "params": {"prop": "cube"}},
                 {"action": "release", "start": 5.0, "duration": 1.0,
                  "params": {"prop": "cube"}},
             ]},
            {"character": "alex", "origin": [0.6, 0.95, 0.0],
             "actions": [
                 {"action": "walk", "start": 1.0, "duration": 1.0,
                  "params": {"target": [-1.2, 0.0]}},
                 {"action": "walk", "start": 2.2, "duration": 1.0,
                  "params": {"target": [-1.2, 0.0]}},
                 {"action": "walk", "start": 3.4, "duration": 1.0,
                  "params": {"target": [-1.2, 0.0]}},
                 {"action": "grab", "start": 5.0, "duration": 1.0,
                  "params": {"prop": "cube"}},
             ]},
        ],
        "props": [{"preset": "glowing_cube", "name": "cube", "x": -0.6}],
    }
    stage_interactions(scene)
    alex = next(a for a in scene["actors"] if a["character"] == "alex")
    walks = [a for a in alex["actions"] if a["action"] == "walk"]
    # избыточные walk вычищены — не больше одного подхода до передачи
    assert len(walks) <= 1
    # подход не заканчивается в точке передающего: дистанция до него
    # должна быть ≥ ~0.9 м (остановка с вытянутой рукой)
    for w in walks:
        tgt = w.get("params", {}).get("target") or [0.0, 0.0]
        assert abs(float(tgt[0])) < 1.2
    cube = next(p for p in scene["props"] if p["name"] == "cube")
    tr = cube["carry"]["transfer"]
    assert tr["character"] == "alex"
    BlockyScene.from_dict(scene).validate()


def test_b18_rejects_unknown_params_and_props():
    raw = {
        "title": "t",
        "lang": "ru",
        "scenes": [
            {
                "title": "s1",
                "duration": 6.0,
                "actors": [
                    {"character": "steve", "origin": [-0.6, 0.95, 0.0],
                     "actions": [
                         {"action": "grab", "start": 1.0, "duration": 1.0,
                          "params": {"prop": "missing_prop", "magic": 1}},
                         {"action": "dance", "start": 2.0, "duration": 1.0},
                     ]},
                ],
                "props": [],
            }
        ],
    }
    board = parse_storyboard(raw, lang="ru", idea="")
    scene = board["scenes"][0]
    adjust_text = " ".join(scene["adjustments"])
    assert "missing_prop" in adjust_text
    assert ("отсутствует" in adjust_text) or ("не существует" in adjust_text)
    assert "magic" in adjust_text
    assert "dance" in adjust_text
    steve = scene["actors"][0]
    grab = next(a for a in steve["actions"] if a["action"] == "grab")
    assert "prop" not in grab.get("params", {})


def test_walk_feet_do_not_slide():
    """F4: стопы при ходьбе не должны проскальзывать (IK не подтягивает
    стопу к бедру, когда нога «не дотягивается»)."""
    actions = [
        {"action": "idle", "start": 0.0, "duration": 2.0},
        {"action": "walk", "start": 2.05, "duration": 2.872,
         "params": {"target": [0.6, -2.45]}},
        {"action": "idle", "start": 4.922, "duration": 4.0},
    ]
    tl = timeline_from_actions(actions, duration=9.0, fps=30, yaw=0.0)
    poses = tl.sample_frames(30)
    windows = stance_windows(poses, 30)
    assert windows
    encoded = encode_frames(poses)

    max_drift = 0.0
    for w in windows:
        side = w["side"]
        key = f"foot_{side.upper()}"
        for i in range(w["start"], w["end"] + 1, 2):
            v = encoded[i][key]
            max_drift = max(
                max_drift,
                math.hypot(v[0] - w["pos"][0], v[1] - w["pos"][1]),
            )
    assert max_drift < 0.02
