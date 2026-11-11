"""Data-driven blocky scene tests (no Blender): validation, presets, JSON."""

from pathlib import Path

import pytest

from shortsfactory.generative.animation_pipeline import blocky_hero as bh
from shortsfactory.generative.animation_pipeline.skinned_hero import PLAN_ACTION_STATES
from shortsfactory.generative.blender_adapter import blender_available
from shortsfactory.generative.blocky_scene import (
    BlockyScene,
    ENVIRONMENT_PRESETS,
    _apply_mouth_skins,
    _distribute_dialogue,
    _expand_prop,
)

SCENES = Path(__file__).resolve().parents[1] / "scenes" / "blocky"


# ------------------------------------------------------------- mouth skins


def test_mouth_skins_assigned_to_speakers_only():
    steve = {"character": "steve", "origin": [0.0, 0.0, 0.0], "actions": []}
    alex = {"character": "alex", "origin": [0.6, 0.0, 0.0], "actions": []}
    actors = [steve, alex]
    dialogue = [
        {"character": "steve", "text": "Привет, Алекс!", "start": 0.5, "duration": 1.2},
    ]
    _distribute_dialogue(actors, dialogue)
    assert _apply_mouth_skins(actors, "ru") == 1
    mskin = steve["mouth_skin"]
    assert mskin["start"] == 0.5
    assert mskin["duration"] == 1.2
    assert "Алекс" in mskin["text"]
    assert mskin["lang"] == "ru"
    # молчавший актёр остаётся без mouth_skin — рот static closed корректен
    assert "mouth_skin" not in alex


def test_mouth_skins_cover_full_speech_window():
    steve = {"character": "steve", "origin": [0.0, 0.0, 0.0], "actions": []}
    actors = [steve]
    _distribute_dialogue(
        actors,
        [
            {"character": "steve", "text": "Раз.", "start": 1.0, "duration": 1.0},
            {"character": "steve", "text": "Два, три.", "start": 3.0, "duration": 1.5},
        ],
    )
    _apply_mouth_skins(actors, "ru")
    mskin = steve["mouth_skin"]
    assert mskin["start"] == 1.0
    # окно покрывает обе реплики до конца второй
    assert mskin["duration"] == 3.5


# ---------------------------------------------------------------- presets


def test_prop_presets_expand_to_named_boxes():
    boxes = _expand_prop({"preset": "tree", "x": -1.1, "y": 2.3})
    assert len(boxes) == 3
    assert all(b["name"] for b in boxes)
    door = _expand_prop({"preset": "door", "x": -0.6, "y": -1.27,
                         "open_frame": 150, "end_frame": 300})
    assert len(door) == 1
    d = door[0]
    assert d["origin_offset"] == [-0.45, 0.0, 0.0]     # hinge at the edge
    assert [f for f, _ in d["rot_track"]][:3] == [1, 150, 162]
    with pytest.raises(ValueError, match="пресет"):
        _expand_prop({"preset": "dragon", "x": 0.0, "y": 0.0})


def test_environment_presets_exist():
    assert {"forest", "meadow", "cave"} <= set(ENVIRONMENT_PRESETS)


# ------------------------------------------------------------- validation


def _base_scene() -> dict:
    return {
        "title": "Тест",
        "duration": 10.0,
        "actors": [
            {"character": "steve", "origin": [0.0, 0.9, 0.0], "duration": 10.0,
             "actions": [{"action": "idle", "start": 0.0, "duration": 5.0}]},
            {"character": "alex", "origin": [0.5, 0.9, 0.0],
             "actions": [{"action": "idle", "start": 0.0, "duration": 10.0}]},
        ],
        "camera_cuts": [{"frame": 1, "center": [0.0, 0.0, 0.9], "height": 3.0}],
    }


def test_valid_scene_passes():
    BlockyScene.from_dict(_base_scene()).validate()


def test_unknown_character_named():
    data = _base_scene()
    data["actors"][0]["character"] = "golum"
    with pytest.raises(ValueError, match="golum"):
        BlockyScene.from_dict(data).validate()


def test_unknown_action_named_with_available_list():
    data = _base_scene()
    data["actors"][0]["actions"] = [{"action": "fly", "start": 0.0, "duration": 2.0}]
    with pytest.raises(ValueError, match="fly"):
        BlockyScene.from_dict(data).validate()


def test_action_beyond_duration_rejected():
    data = _base_scene()
    data["actors"][0]["actions"] = [{"action": "idle", "start": 9.0, "duration": 3.0}]
    with pytest.raises(ValueError, match="длительности сцены"):
        BlockyScene.from_dict(data).validate()


def test_bad_interval_rejected():
    data = _base_scene()
    data["actors"][0]["actions"] = [{"action": "idle", "start": 2.0, "duration": 0.0}]
    with pytest.raises(ValueError, match="интервал"):
        BlockyScene.from_dict(data).validate()


def test_missing_referenced_prop_rejected():
    data = _base_scene()
    data["actors"][0]["actions"] = [
        {"action": "interact", "start": 1.0, "duration": 1.0, "params": {"prop": "Door"}}
    ]
    with pytest.raises(ValueError, match="Door"):
        BlockyScene.from_dict(data).validate()


def test_unknown_sfx_and_bad_cuts_rejected():
    data = _base_scene()
    data["sfx_events"] = [[1.0, "explosion"]]
    with pytest.raises(ValueError, match="explosion"):
        BlockyScene.from_dict(data).validate()
    data = _base_scene()
    data["camera_cuts"] = [{"frame": 50, "center": [0, 0, 1], "height": 2.0}]
    with pytest.raises(ValueError, match="кадра 1"):
        BlockyScene.from_dict(data).validate()
    data["camera_cuts"] = [{"frame": 1, "center": [0, 0, 1], "height": 2.0},
                           {"frame": 1, "center": [0, 0, 1], "height": 2.0}]
    with pytest.raises(ValueError, match="по возрастанию"):
        BlockyScene.from_dict(data).validate()


# ------------------------------------------------- the two authored scenes


@pytest.mark.parametrize("name", ["lost_diamond", "tricky_door"])
def test_authored_scenes_validate(name: str):
    scene = BlockyScene.load(SCENES / f"{name}.json")
    scene.validate()
    assert len(scene.actors) == 2
    assert scene.camera_cuts and scene.camera_cuts[0]["frame"] == 1
    # actions must only use the rig's supported verbs
    verbs = {a["action"] for actor in scene.actors for a in actor["actions"]}
    assert verbs <= set(PLAN_ACTION_STATES)


def test_authored_scenes_differ():
    s1 = BlockyScene.load(SCENES / "lost_diamond.json")
    s2 = BlockyScene.load(SCENES / "tricky_door.json")
    v1 = {a["action"] for actor in s1.actors for a in actor["actions"]}
    v2 = {a["action"] for actor in s2.actors for a in actor["actions"]}
    assert v1 != v2
    p1 = {p.get("name") for p in s1.props}
    p2 = {p.get("name") for p in s2.props}
    assert "Diamond" in p1 and "Diamond" not in p2
    assert "Door" in p2 and "Door" not in p1


def test_first_film_compatible_with_scene_format():
    """The hardcoded prototype must stay loadable through the data path."""
    scene = BlockyScene.from_dict(bh.first_film_scene())
    scene.validate()
    assert scene.duration == 10.0
    assert len(scene.camera_cuts) == 3


def test_load_missing_file_and_bad_json(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        BlockyScene.load(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        BlockyScene.load(bad)


# ------------------------------------------------- engine fixes (reference audit)


def test_prop_sync_shifts_tracks_to_action_start(tmp_path: Path):
    """Problem 1: a door must open exactly when the actor touches it."""
    data = _base_scene()
    data["actors"][0]["actions"] = [
        {"action": "idle", "start": 0.0, "duration": 5.0},
        {"action": "interact", "start": 5.0, "duration": 0.7},
    ]
    data["actors"][0]["duration"] = 10.0
    data["actors"][1]["duration"] = 10.0
    data["props"] = [{
        "name": "Door", "center": [1.0, -1.3, 1.0], "size": [0.9, 0.12, 2.0],
        "origin_offset": [-0.45, 0.0, 0.0],
        "rot_track": [[1, [0.0, 0.0, 0.0]], [13, [0.0, 0.0, 1.9]]],
        "sync": {"actor": "steve", "action": "interact", "offset": 0.2},
    }]
    scene = BlockyScene.from_dict(data)
    scene.validate()  # must not raise
    _, payload = bh.build_blocky_scene_payload(tmp_path, scene.actors, props=scene.props)
    door = next(p for p in payload["props"] if p["name"] == "Door")
    frames = [f for f, _ in door["rot_track"]]
    # interact starts at 5.0 s + 0.2 s offset => frame 156; track frame 1 -> 156
    assert frames[0] == 156
    assert frames[1] == 168  # 156 + 12


def test_prop_sync_unknown_action_rejected(tmp_path: Path):
    data = _base_scene()
    data["props"] = [{
        "name": "Door", "center": [1.0, -1.3, 1.0], "size": [0.9, 0.12, 2.0],
        "rot_track": [[1, [0.0, 0.0, 0.0]]],
        "sync": {"actor": "steve", "action": "open_sesame", "offset": 0.2},
    }]
    scene = BlockyScene.from_dict(data)
    with pytest.raises(ValueError, match="open_sesame"):
        scene.validate()
    with pytest.raises(ValueError, match="open_sesame"):
        bh.build_blocky_scene_payload(tmp_path, scene.actors, props=scene.props)


def test_actors_are_independent(tmp_path: Path):
    """Problem 2: one actor walking must never move the other actor's root."""
    data = _base_scene()
    data["actors"][0]["actions"] = [  # steve walks away
        {"action": "idle", "start": 0.0, "duration": 1.0},
        {"action": "walk", "start": 1.0, "duration": 3.0},
        {"action": "idle", "start": 4.0, "duration": 6.0},
    ]
    data["actors"][0]["duration"] = 10.0
    data["actors"][1]["duration"] = 10.0
    # alex just stands (idle is breathing-only, root must stay put)
    scene = BlockyScene.from_dict(data)
    path, payload = bh.build_blocky_scene_payload(tmp_path, scene.actors, scenario="indep")
    steve_roots = [f["steve:hips"][:3] for f in payload["frames"]]
    alex_roots = [f["alex:hips"][:3] for f in payload["frames"]]
    assert steve_roots[0][1] != steve_roots[-1][1]        # steve travelled
    assert max(r[1] for r in alex_roots) - min(r[1] for r in alex_roots) < 1e-6
    assert max(r[0] for r in alex_roots) - min(r[0] for r in alex_roots) < 1e-6


def test_umbrella_preset_moves_cap_and_handle_together(tmp_path: Path):
    data = _base_scene()
    data["props"] = [{
        "preset": "umbrella", "name": "Umbrella", "x": 0.4, "y": 0.3, "z": 2.2,
        "rel_track": [[1, [0.4, 0.3, 7.0]], [30, [0.4, 0.3, 2.2]]],
    }]
    scene = BlockyScene.from_dict(data)
    scene.validate()
    _, payload = bh.build_blocky_scene_payload(tmp_path, scene.actors, props=scene.props)
    cap = next(p for p in payload["props"] if p["name"] == "UmbrellaCap")
    handle = next(p for p in payload["props"] if p["name"] == "UmbrellaHandle")
    assert len(cap["pos_track"]) == len(handle["pos_track"]) == 2
    for (cf, cc), (hf, hc) in zip(cap["pos_track"], handle["pos_track"]):
        assert cf == hf
        assert hc[2] == pytest.approx(cc[2] - 0.93)


# ---------------------------------------------------------------- dialogue


def _dialogue_scene() -> dict:
    data = _base_scene()
    data["dialogue"] = [
        {"character": "steve", "text": "Привет, Алекс!", "start": 1.0, "duration": 1.4},
        {"character": "alex", "text": "Привет!", "start": 2.6, "duration": 1.0},
    ]
    return data


def test_dialogue_is_distributed_to_the_speaking_actor():
    scene = BlockyScene.from_dict(_dialogue_scene())
    scene.validate()
    steve = next(a for a in scene.actors if a["character"] == "steve")
    alex = next(a for a in scene.actors if a["character"] == "alex")
    assert len(scene.dialogue) == 2
    assert [v["text"] for v in steve["voice_lines"]] == ["Привет, Алекс!"]
    assert [v["text"] for v in alex["voice_lines"]] == ["Привет!"]
    assert steve["voice_lines"][0]["start"] == pytest.approx(1.0)


def test_dialogue_rejects_unknown_character():
    data = _dialogue_scene()
    data["dialogue"][0]["character"] = "milo"
    with pytest.raises(ValueError, match="Реплика #1: неизвестный персонаж «milo»"):
        BlockyScene.from_dict(data).validate()


def test_dialogue_rejects_empty_text_and_bad_interval():
    data = _dialogue_scene()
    data["dialogue"][0]["text"] = "   "
    with pytest.raises(ValueError, match="пустой текст"):
        BlockyScene.from_dict(data).validate()
    data = _dialogue_scene()
    data["dialogue"][0]["duration"] = 0.0
    with pytest.raises(ValueError, match="некорректный интервал"):
        BlockyScene.from_dict(data).validate()


def test_dialogue_rejects_line_beyond_scene():
    data = _dialogue_scene()
    data["dialogue"][1].update({"start": 9.5, "duration": 1.5})
    with pytest.raises(ValueError, match="заканчивается в 11.00 с"):
        BlockyScene.from_dict(data).validate()


def test_dialogue_rejects_overlapping_lines_of_one_character():
    data = _dialogue_scene()
    data["dialogue"][1] = {"character": "steve", "text": "Ещё раз", "start": 2.0,
                           "duration": 1.0}
    with pytest.raises(ValueError, match="пересекаются"):
        BlockyScene.from_dict(data).validate()


def test_subtitle_cues_are_named_and_written(tmp_path: Path):
    from shortsfactory.subtitles import write_srt

    scene = BlockyScene.from_dict(_dialogue_scene())
    cues = scene.subtitle_cues()
    assert [c.text for c in cues] == ["Steve: Привет, Алекс!", "Alex: Привет!"]
    assert [(c.start, c.end) for c in cues] == [(1.0, 2.4), (2.6, 3.6)]
    srt = write_srt(cues, tmp_path / "ru.srt").read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:02,400" in srt
    assert "Steve: Привет, Алекс!" in srt


def test_dialogue_moves_the_blocky_jaw(tmp_path: Path):
    scene = BlockyScene.from_dict(_dialogue_scene())
    scene.validate()
    quiet = BlockyScene.from_dict(_base_scene())
    _, loud_payload = bh.build_blocky_scene_payload(tmp_path / "loud", scene.actors)
    _, quiet_payload = bh.build_blocky_scene_payload(tmp_path / "quiet", quiet.actors)
    loud = [frame["steve:jaw"] for frame in loud_payload["frames"]]
    still = [frame["steve:jaw"] for frame in quiet_payload["frames"]]
    # before the first line (0.0–0.9 s) the face is identical to a silent scene
    for i in range(27):
        assert loud[i] == still[i], f"frame {i} already moved without a line"
    # mid-line the jaw really travels
    moved = [i for i in range(33, 66) if loud[i] != still[i]]
    assert len(moved) > 10, f"only {len(moved)} mouth frames moved"
    # dialogue never touches the body: hips stay identical throughout
    assert loud_payload["frames"][50]["steve:hips"] == quiet_payload["frames"][50]["steve:hips"]


# ---------------------------------------------------- acting & speech QC


def _duo(auto_act: bool = True, emotion: str | None = "happy") -> dict:
    return {
        "title": "auto acting",
        "duration": 6.0,
        "auto_act": auto_act,
        "actors": [
            {
                "character": "steve",
                "origin": [-0.45, 0.9, 0.0],
                "actions": [{"action": "idle", "start": 0.0, "duration": 6.0}],
            },
            {
                "character": "alex",
                "origin": [0.45, 0.9, 0.0],
                "actions": [{"action": "idle", "start": 0.0, "duration": 6.0}],
            },
        ],
        "dialogue": [
            {
                "character": "steve",
                "text": "Привет, как дела?",
                "start": 1.0,
                "duration": 1.5,
                "emotion": emotion,
            },
            {
                "character": "alex",
                "text": "Всё хорошо, спасибо.",
                "start": 3.0,
                "duration": 1.4,
            },
        ],
    }


def test_auto_act_adds_listener_reactions_facing_the_speaker():
    scene = BlockyScene.from_dict(_duo())
    by_name = {actor["character"]: actor for actor in scene.actors}
    alex = by_name["alex"]["actions"]
    nod = next(action for action in alex if action["action"] == "listen_nod")
    assert nod["start"] == 1.0
    assert nod["params"]["direction"] == -1.0  # Alex stands right of Steve
    steve = by_name["steve"]["actions"]
    listen = next(
        action
        for action in steve
        if action["action"] == "listen" and action["start"] == 3.0
    )
    assert listen["params"]["direction"] == 1.0


def test_auto_act_does_not_overwrite_authored_actions():
    data = _duo()
    data["actors"][1]["actions"] = [
        {"action": "idle", "start": 0.0, "duration": 0.5},
        {"action": "shrug", "start": 0.5, "duration": 2.2},
        {"action": "idle", "start": 2.7, "duration": 3.3},
    ]
    # both lines belong to Steve, so Alex is the listener twice
    data["dialogue"][1]["character"] = "steve"
    scene = BlockyScene.from_dict(data)
    alex = next(a for a in scene.actors if a["character"] == "alex")
    assert not [
        action
        for action in alex["actions"]
        if action["action"] == "listen_nod" and action["start"] < 2.7
    ], "the authored shrug was overwritten by an automatic reaction"
    assert any(
        action["action"] in {"listen_nod", "listen"} and action["start"] == 3.0
        for action in alex["actions"]
    )


def test_auto_act_is_off_by_default_and_leaves_actions_alone():
    scene = BlockyScene.from_dict(_duo(auto_act=False))
    for actor in scene.actors:
        assert [action["action"] for action in actor["actions"]] == ["idle"]
        assert actor["actions"][0]["duration"] == 6.0
        assert "emotion" not in (actor["actions"][0].get("params") or {})
    assert scene.auto_act is False


def test_line_emotion_reaches_the_pose_only_during_the_line():
    from shortsfactory.generative.animation_pipeline.skinned_hero import (
        timeline_from_actions,
    )

    scene = BlockyScene.from_dict(_duo())
    steve = next(a for a in scene.actors if a["character"] == "steve")
    beat = next(
        action
        for action in steve["actions"]
        if action["start"] == 1.0 and action["duration"] == 1.5
    )
    assert beat["params"]["emotion"] == "happy"
    frames = timeline_from_actions(
        steve["actions"], duration=6.0, fps=30
    ).sample_frames(30)
    assert frames[0].brow_lift == 0.0
    assert frames[45].brow_lift > 0.0  # 1.5 s — inside the line
    assert frames[150].brow_lift == 0.0  # 5.0 s — after it


def test_dialogue_qc_passes_on_a_clean_scene():
    report = BlockyScene.from_dict(_duo()).dialogue_qc()
    assert report["ok"] is True, report["issues"]
    assert len(report["lines"]) == 2
    assert all(line["voice"] and line["cue"] and line["visemes"] >= 2
               for line in report["lines"])


def test_dialogue_qc_flags_overlapping_subtitles_and_empty_text():
    data = _duo()
    data["dialogue"] = [
        {"character": "steve", "text": "Первая реплика.", "start": 1.0, "duration": 2.0},
        {"character": "alex", "text": "Вторая реплика.", "start": 2.0, "duration": 1.2},
        {"character": "alex", "text": "   ", "start": 4.0, "duration": 1.0},
    ]
    report = BlockyScene.from_dict(data).dialogue_qc()
    assert report["ok"] is False
    joined = " ".join(report["issues"])
    assert "накладываются" in joined
    assert "пустой текст" in joined


# ------------------------------------------------- integration (real render)


@pytest.mark.integration
@pytest.mark.skipif(not blender_available(), reason="Blender не найден (BLENDER_EXECUTABLE)")
@pytest.mark.parametrize("name", ["lost_diamond", "tricky_door"])
def test_authored_scenes_render(name: str, tmp_path: Path):
    report = BlockyScene.load(SCENES / f"{name}.json").render(tmp_path / name)
    assert report["status"] == "OK", report.get("error")
    diag = report["diagnostics"]
    assert diag["pose_error"]["passed"]
    assert diag["foot_contact"]["max_drift"] < 0.05
    assert diag["blank_frames"]["blank"] == 0


def test_glowing_cube_rests_on_ground_without_explicit_z():
    from shortsfactory.generative.blocky_scene import _expand_prop_boxes

    boxes = _expand_prop_boxes({"preset": "glowing_cube", "x": 0.3, "y": 1.0, "size": 0.34})
    body = next(b for b in boxes if not b.get("type"))
    assert body["center"][2] == pytest.approx(0.17)  # size / 2, не «демочные» 0.74
    # явный z сохраняется
    boxes2 = _expand_prop_boxes({"preset": "glowing_cube", "x": 0.0, "y": 0.0, "z": 1.2, "size": 0.3})
    assert next(b for b in boxes2 if not b.get("type"))["center"][2] == pytest.approx(1.2)


def test_key_and_chest_presets_expand_with_unique_part_names():
    from shortsfactory.generative.blocky_scene import _expand_prop_boxes

    key = _expand_prop_boxes({"preset": "key", "name": "Key", "x": 0.5, "y": 1.0})
    assert [b["name"] for b in key] == ["Key shaft", "Key bow"]
    assert all(b["center"][2] >= 0.0 for b in key)
    chest = _expand_prop_boxes({"preset": "chest", "name": "Chest", "x": 0.0, "y": 0.0})
    assert [b["name"] for b in chest] == ["Chest base", "Chest lid"]
