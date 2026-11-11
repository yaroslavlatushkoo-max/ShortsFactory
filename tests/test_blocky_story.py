"""D10: text -> storyboard, normalization and the deterministic fallback."""

import json

import pytest

from shortsfactory.generative import blocky_story as story
from shortsfactory.generative.blocky_scene import BlockyScene
from shortsfactory.llm import LLMError

IDEA = "Стив находит странный куб в пещере"


def test_fallback_storyboard_is_renderable():
    board = story.fallback_storyboard(IDEA, scenes=3)
    assert board["title"] == IDEA
    assert len(board["scenes"]) == 3
    assert story.check_storyboard(board) == []
    for scene in board["scenes"]:
        BlockyScene.from_dict(scene).validate()
        assert scene["auto_act"] is True and scene["auto_camera"] is True
        assert scene["dialogue"]


def test_normalize_scene_repairs_and_drops_unknown_values():
    scene = story.normalize_scene(
        {
            "title": "Куб",
            "actors": [
                {"character": "steve", "origin": [9.0, 1.0]},
                {"character": "nobody", "actions": []},
            ],
            "dialogue": [
                {
                    "character": "steve",
                    "text": "Это что?",
                    "start": 0.5,
                    "emotion": "curious",
                },
                {"character": "nobody", "text": "Я тут.", "start": 1.0},
            ],
            "props": [{"preset": "tree", "x": 1.0, "y": 2.0}, {"preset": "dragon"}],
            "sfx_events": [[0.5, "sparkle"], [0.7, "explosion"]],
        }
    )
    assert scene["duration"] >= 6.0
    assert scene["lang"] == "ru"
    assert scene["auto_act"] is True and scene["auto_camera"] is True
    assert 1080 == scene["width"] and 1920 == scene["height"]
    assert [actor["character"] for actor in scene["actors"]] == ["steve"]
    assert actor_origin(scene) == [2.4, 1.0, 0.0]
    assert scene["actors"][0]["actions"], "idle must be added to a silent actor"
    assert len(scene["dialogue"]) == 1
    assert scene["dialogue"][0]["text"] == "Это что?"
    assert len(scene["props"]) == 1
    assert scene["props"][0]["preset"] == "tree"
    assert len(scene["sfx_events"]) == 1
    assert scene["sfx_events"][0][1] == "sparkle"


def actor_origin(scene):
    return scene["actors"][0]["origin"]


def test_parse_storyboard_rejects_unusable_answers():
    with pytest.raises(story.StoryError):
        story.parse_storyboard([])
    with pytest.raises(story.StoryError):
        story.parse_storyboard({"scenes": []})
    # пустой объект больше не ошибка: repair-loop собирает минимальный idle-фильм
    board = story.parse_storyboard({})
    assert board["scenes"] and board["scenes"][0]["actors"]


def test_check_storyboard_allows_silent_scenes():
    """B-17: немая сцена (комедия действия) — валидный фильм, а не дефект."""
    board = story.fallback_storyboard(IDEA, scenes=1)
    board["scenes"][0]["dialogue"] = []
    problems = story.check_storyboard(board)
    assert not any("реплики" in problem for problem in problems), problems


def test_generate_storyboard_falls_back_when_the_model_fails(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, config):
            pass

        def generate_json(self, *args, **kwargs):
            calls.append(kwargs)
            raise LLMError("Ollama недоступна")

    monkeypatch.setattr(story, "OllamaClient", FakeClient)
    board = story.generate_storyboard(IDEA, config=object(), attempts=2)
    assert board["source"] == "fallback"
    assert board["idea"] == IDEA
    assert len(calls) == 2
    assert story.check_storyboard(board) == []


def test_generate_storyboard_accepts_a_model_answer(monkeypatch):
    answer = {
        "title": "Куб",
        "scenes": [
            {
                "title": "Находка",
                "duration": 6.0,
                "environment": "cave",
                "actors": [
                    {"character": "steve", "origin": [-0.6, 0.95]},
                    {"character": "alex", "origin": [0.6, 0.95]},
                ],
                "dialogue": [
                    {
                        "character": "steve",
                        "text": "Смотри!",
                        "start": 1.0,
                        "duration": 1.5,
                        "emotion": "surprised",
                    }
                ],
                "props": [{"preset": "glowing_cube", "x": 0.0, "y": 1.4}],
            }
        ],
    }

    class FakeClient:
        def __init__(self, config):
            pass

        def generate_json(self, *args, **kwargs):
            return answer

    monkeypatch.setattr(story, "OllamaClient", FakeClient)
    board = story.generate_storyboard(IDEA, config=object())
    assert board["source"] == "ollama"
    assert len(board["scenes"]) == 1
    assert board["scenes"][0]["environment"] == "cave"
    assert story.check_storyboard(board) == []


def test_prompt_names_the_idea_and_the_schema():
    prompt = story.build_story_prompt(IDEA, scenes=2, seconds=12)
    assert IDEA in prompt
    assert "scenes" in prompt and "dialogue" in prompt
    assert "2" in prompt


def test_storyboard_and_scene_files_round_trip(tmp_path):
    board = story.fallback_storyboard(IDEA, scenes=2)
    path = story.write_storyboard(board, tmp_path / "board.json")
    assert path.exists()
    again = story.load_storyboard(path)
    assert again["title"] == board["title"]
    assert len(again["scenes"]) == 2

    paths = story.scene_files(board, tmp_path / "scenes")
    assert len(paths) == 2
    for scene_path in paths:
        data = json.loads(scene_path.read_text(encoding="utf-8"))
        BlockyScene.from_dict(data).validate()
    single = story.load_storyboard(paths[0])
    assert single["title"] == board["scenes"][0]["title"]


def _authored_board(lang: str = "ru") -> dict:
    return {
        "title": "Дверь",
        "lang": lang,
        "scenes": [
            {
                "title": "Сцена",
                "duration": 6.0,
                "lang": lang,
                "auto_act": False,
                "auto_camera": False,
                "actors": [
                    {
                        "character": "steve",
                        "origin": [-0.6, 0.9],
                        "duration": 6.0,
                        "actions": [
                            {"action": "grab", "start": 0.0, "duration": 1.0},
                            {"action": "hold", "start": 1.0, "duration": 4.5},
                        ],
                    },
                    {
                        "character": "alex",
                        "origin": [0.7, 0.9],
                        "duration": 6.0,
                        "actions": [{"action": "idle", "start": 0.0, "duration": 6.0}],
                    },
                ],
                "dialogue": [
                    {"character": "steve", "text": "Смотри.", "start": 0.5, "duration": 1.5}
                ],
                "camera_cuts": [
                    {"frame": 1, "center": [0.05, 0.0, 0.95], "height": 4.2},
                    {
                        "frame": 120,
                        "center": [-0.6, 0.25, 1.05],
                        "height": 2.0,
                        "move": {"to_frame": 150, "center": [-0.5, 0.3, 1.1], "height": 2.2},
                    },
                ],
                "props": [
                    {
                        "preset": "glowing_cube",
                        "x": -0.6,
                        "y": 0.35,
                        "carry": {
                            "character": "steve",
                            "bone": "hand_R",
                            "start": 0.0,
                            "release": 5.5,
                            "grip": [0.0, 0.0, 0.16],
                        },
                    }
                ],
            }
        ],
    }


def test_authored_camera_carry_and_flags_survive_round_trip(tmp_path):
    path = story.write_storyboard(_authored_board(), tmp_path / "board.json")
    board = story.load_storyboard(path)
    scene = board["scenes"][0]
    assert scene["auto_act"] is False and scene["auto_camera"] is False
    assert [cut["frame"] for cut in scene["camera_cuts"]] == [1, 120]
    assert scene["camera_cuts"][1]["move"]["to_frame"] == 150
    carry = scene["props"][0]["carry"]
    assert carry["bone"] == "hand_R" and carry["release"] == 5.5
    assert carry["grip"] == [0.0, 0.0, 0.16]
    BlockyScene.from_dict(scene).validate()


def test_english_storyboard_keeps_its_language(tmp_path):
    path = story.write_storyboard(_authored_board("en"), tmp_path / "en.json")
    board = story.load_storyboard(path)
    assert board["lang"] == "en"
    assert all(scene["lang"] == "en" for scene in board["scenes"])


# ------------------------------------------------- F2 staging (автопостановка)


def _stage_scene() -> dict:
    return {
        "title": "t",
        "duration": 9.0,
        "fps": 30,
        "actors": [
            {"character": "steve", "origin": [-0.35, 0.9, 0.0], "duration": 9.0,
             "actions": [
                 {"action": "idle", "start": 0.0, "duration": 4.0},
                 {"action": "grab", "start": 4.0, "duration": 1.0},
                 {"action": "hold", "start": 5.0, "duration": 4.0},
             ]},
            {"character": "alex", "origin": [0.35, 0.9, 0.0], "duration": 9.0,
             "actions": [{"action": "idle", "start": 0.0, "duration": 9.0}]},
        ],
        "props": [
            {"preset": "key", "x": 2.0, "y": 2.5},
            {"preset": "door", "x": -0.10, "y": 2.2, "state": "closed"},
        ],
    }


def test_stage_interactions_binds_walks_and_carries():
    from shortsfactory.generative.blocky_story import stage_interactions

    scene = _stage_scene()
    adjustments = stage_interactions(scene)
    key = next(p for p in scene["props"] if p["preset"] == "key")
    # grab привязан к единственному берущему предмету
    grab = scene["actors"][0]["actions"][2]  # idle, walk, grab, hold
    assert grab["params"]["prop"] == key["name"]
    # F2.5: предмет НЕ двигается — персонаж подходит
    assert (key["x"], key["y"]) == (2.0, 2.5)
    walk = scene["actors"][0]["actions"][1]
    assert walk["action"] == "walk"
    assert walk["start"] < grab["start"]
    # walk нацелен на предмет (локальная цель близка к (2.35, 1.6))
    tx, ty = walk["params"]["target"]
    assert abs(tx - 2.35) < 1.0 and abs(ty - 1.6) < 1.0
    # carry добавлен на новый (сдвинутый) start grab
    assert key["carry"]["character"] == "steve"
    assert key["carry"]["start"] == pytest.approx(grab["start"])
    assert len(adjustments) >= 3


def test_stage_interactions_opens_door_on_interact():
    from shortsfactory.generative.blocky_story import stage_interactions

    scene = _stage_scene()
    scene["actors"][0]["actions"] = [
        {"action": "idle", "start": 0.0, "duration": 2.0},
        {"action": "interact", "start": 2.0, "duration": 1.0,
         "params": {"prop": "door#2"}},
    ]
    adjustments = stage_interactions(scene)
    door = next(p for p in scene["props"] if p["preset"] == "door")
    assert door.get("open_frame") == int(round((2.0 + 0.35) * 30))
    assert any("откроется" in a for a in adjustments)


def test_ensure_door_event_honours_idea_promise():
    from shortsfactory.generative.blocky_story import ensure_door_event

    scene = _stage_scene()
    for prop in scene["props"]:
        prop.pop("open_frame", None)
    log = ensure_door_event([scene], "Стив находит ключ и открывает дверь")
    door = next(p for p in scene["props"] if p["preset"] == "door")
    assert door.get("open_frame"), log
    assert log and "откро" in log[0] or "откроется" in log[0]


def test_actions_vocabulary_covers_carry_verbs():
    from shortsfactory.generative.blocky_story import ACTIONS, EXTRA_ACTIONS
    from shortsfactory.generative.animation_pipeline.skinned_hero import (
        PLAN_ACTION_STATES,
    )

    assert set(ACTIONS) | set(EXTRA_ACTIONS) == set(PLAN_ACTION_STATES)
    assert {"reach", "pickup", "interact", "use"} <= set(ACTIONS) | set(EXTRA_ACTIONS)


SILENT_BOARD = {
    "title": "Немое кино",
    "scenes": [
        {"title": "Сцена 1", "duration": 4.0},
        {"title": "Сцена 2", "duration": 4.0},
    ],
}


def test_parse_storyboard_rejects_silent_llm_answer():
    # идея задана — фильм обязан быть говорливым (диналог — часть истории)
    with pytest.raises(story.StoryError, match="немая"):
        story.parse_storyboard(SILENT_BOARD, idea="Дима находит карту")


def test_parse_storyboard_allows_silent_authored_board():
    # авторская (--storyboard) раскадровка может быть немой осознанно
    board = story.parse_storyboard(SILENT_BOARD, idea="")
    assert board["lang"] == "ru"
    assert all(not scene.get("dialogue") for scene in board["scenes"])


def test_parse_storyboard_accepts_talkative_idea():
    data = {
        "title": "Находка",
        "scenes": [
            {
                "title": "Лес",
                "duration": 5.0,
                "actors": [
                    {"character": "steve", "origin": [-0.6, 0.95, 0.0]},
                    {"character": "alex", "origin": [0.6, 0.95, 0.0]},
                ],
                "dialogue": [
                    {"character": "steve", "text": "Смотри, карта.",
                     "start": 0.6, "duration": 1.5},
                ],
            }
        ],
    }
    board = story.parse_storyboard(data, idea="Дима находит карту")
    assert board["scenes"][0]["dialogue"]


def test_generate_storyboard_retries_with_feedback(monkeypatch):
    prompts = []
    good = {
        "title": "Куб",
        "scenes": [
            {
                "title": "Находка",
                "duration": 6.0,
                "environment": "cave",
                "actors": [
                    {"character": "steve", "origin": [-0.6, 0.95]},
                    {"character": "alex", "origin": [0.6, 0.95]},
                ],
                "dialogue": [
                    {
                        "character": "steve",
                        "text": "Смотри!",
                        "start": 1.0,
                        "duration": 1.5,
                        "emotion": "surprised",
                    }
                ],
                "props": [{"preset": "glowing_cube", "x": 0.0, "y": 1.4}],
            }
        ],
    }

    class FlakyClient:
        def __init__(self, config):
            pass

        def generate_json(self, prompt, **kwargs):
            prompts.append(prompt)
            if len(prompts) == 1:
                return {"scenes": []}
            return good

    monkeypatch.setattr(story, "OllamaClient", FlakyClient)
    board = story.generate_storyboard(IDEA, config=object(), attempts=2)
    assert board["source"] == "ollama"
    assert board.get("retries") == 1
    assert len(prompts) == 2
    # повтор обязан содержать причину отказа — иначе модель повторяет ошибку
    assert "отклонена проверкой" in prompts[1]
    assert "нет ни одной сцены" in prompts[1]


def test_generate_storyboard_logs_failed_attempts_on_fallback(monkeypatch):
    class FailClient:
        def __init__(self, config):
            pass

        def generate_json(self, *args, **kwargs):
            return {"scenes": []}

    monkeypatch.setattr(story, "OllamaClient", FailClient)
    board = story.generate_storyboard(IDEA, config=object(), attempts=2)
    assert board["source"] == "fallback"
    assert board["attempts_failed"]
    assert "нет ни одной сцены" in board["attempts_failed"][0]


def test_fallback_storyboard_is_deterministic_and_valid():
    first = story.fallback_storyboard("странный камень")
    second = story.fallback_storyboard("странный камень")
    assert first == second  # детерминизм: тот же запрос — тот же фильм
    assert story.check_storyboard(first) == []
    envs = {scene["environment"] for scene in first["scenes"]}
    assert envs <= {"forest", "meadow", "cave"}
    for scene in first["scenes"]:
        for event in scene.get("sfx_events") or []:
            assert event[1] in ("sparkle", "boing", "pop")
    other = story.fallback_storyboard("пираты находят клад", scenes=4)
    assert story.check_storyboard(other) == []
    assert len(other["scenes"]) == 4
