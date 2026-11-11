"""Tests for Animation Engine v1/v2/v3.

Covers the capabilities that are actually implemented: scene loading and
validation, timeline/keyframe checks, object transforms, independent body-part
animation, duration/FPS, determinism, invalid input handling and MP4 rendering.

The v2 sections cover attachment/parenting, timeline dependencies, coordinated
poses, character look assets, storyboard compilation and the no-teleport rule.
The v3 section covers the pilot storyboard, SVG art assets and the animated
project integration (storyboard -> render -> SRT/report copies).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from shortsfactory.animation import (
    AnimationEngine,
    AnimationScene,
    LookError,
    SceneError,
    StoryboardError,
    compile_storyboard,
    list_looks,
    load_look,
    load_scene,
    load_storyboard,
)
from shortsfactory.animation import rig
from shortsfactory.animation.motion import ACTIONS, POSES, UNSUPPORTED, evaluate, pose_angles
from shortsfactory.animation.renderer import AnimationRenderer
from shortsfactory.animation.scene import sample_object
from shortsfactory.jobs import JobError, JobService
from shortsfactory.models import Plan, Project, VISUAL_MODES


def _scene_dict(**overrides):
    """A minimal but valid scene (small size so tests stay fast)."""
    data = {
        "version": 1,
        "id": "test_scene",
        "title": "test",
        "fps": 12,
        "width": 180,
        "height": 320,
        "duration": 2.0,
        "blend": 0.15,
        "characters": [
            {"id": "a", "x": 0.2, "y": 0.9, "facing": 1, "z": 20},
            {"id": "b", "x": 0.8, "y": 0.9, "facing": -1, "z": 25},
        ],
        "objects": [
            {
                "id": "orb",
                "kind": "orb",
                "z": 12,
                "appear_at": 0.0,
                "keyframes": [
                    {"t": 0.0, "x": 0.5, "y": 0.6, "scale": 1.0},
                    {"t": 2.0, "x": 0.7, "y": 0.3, "scale": 1.5},
                ],
            }
        ],
        "actions": [
            {"character": "a", "action": "walk", "start": 0.0, "duration": 1.2, "params": {"to_x": 0.6}},
            {"character": "a", "action": "wave", "start": 1.2, "duration": 0.8, "params": {}},
            {"character": "b", "action": "react", "start": 0.4, "duration": 1.0, "params": {}},
            {"character": "b", "action": "idle", "start": 1.4, "duration": 0.6, "params": {}},
        ],
        "camera": [
            {"t": 0.0, "x": 0.5, "y": 0.5, "zoom": 1.0},
            {"t": 2.0, "x": 0.5, "y": 0.5, "zoom": 1.1},
        ],
        "blink_times": [0.5, 1.5],
    }
    data.update(overrides)
    return data


def _scene(**overrides) -> AnimationScene:
    return AnimationScene.from_dict(_scene_dict(**overrides))


# ------------------------------------------------------------------ loading


def test_pilot_scene_file_is_valid(config):
    scene = load_scene(config.root / "animation" / "scenes" / "pilot_room.json")
    assert scene.id == "pilot_room_v1"
    assert scene.duration == 13.0
    assert scene.fps == 30
    assert len(scene.characters) == 2
    assert {item.action for item in scene.actions} >= {"walk", "reach", "react", "point", "wave"}
    assert len(scene.contact_times) >= 6
    assert all(0.0 <= moment <= scene.duration for moment in scene.contact_times)
    assert len(scene.voice_lines) == 3


def test_valid_scene_passes_validation():
    assert _scene().validate() == []


def test_missing_file_raises(tmp_path):
    with pytest.raises(SceneError):
        load_scene(tmp_path / "does_not_exist.json")


def test_broken_json_raises(tmp_path):
    path = tmp_path / "scene.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SceneError):
        load_scene(path)


# --------------------------------------------------------------- validation


def test_unknown_action_rejected():
    scene = _scene(actions=[{"character": "a", "action": "teleport", "start": 0, "duration": 1}])
    errors = scene.validate()
    assert any("Неизвестное действие" in message for message in errors)


def test_unsupported_action_message_is_explicit():
    for name in ("swim", "fly", "climb"):
        scene = _scene(actions=[{"character": "a", "action": name, "start": 0, "duration": 1}])
        errors = scene.validate()
        assert any("не поддерживается" in message for message in errors)
        assert name in UNSUPPORTED


def test_run_jump_and_dance_are_supported_actions():
    for name in ("run", "jump", "dance"):
        scene = _scene(actions=[{"character": "a", "action": name, "start": 0, "duration": 1}])
        assert scene.validate() == []
        assert name in ACTIONS
        assert name not in UNSUPPORTED


def test_action_referencing_unknown_character_rejected():
    scene = _scene(actions=[{"character": "ghost", "action": "idle", "start": 0, "duration": 1}])
    assert any("неизвестного персонажа" in message for message in scene.validate())


def test_action_beyond_scene_duration_rejected():
    scene = _scene(actions=[{"character": "a", "action": "idle", "start": 1.5, "duration": 2.0}])
    assert any("выходит за границу" in message for message in scene.validate())


def test_duplicate_character_id_rejected():
    scene = _scene(
        characters=[{"id": "a", "x": 0.2, "y": 0.9}, {"id": "a", "x": 0.5, "y": 0.9}]
    )
    assert any("Повторяющийся id персонажа" in message for message in scene.validate())


def test_character_position_out_of_range_rejected():
    scene = _scene(characters=[{"id": "a", "x": 1.4, "y": 0.9}])
    assert any("вне сцены" in message for message in scene.validate())


def test_unsorted_object_keyframes_rejected():
    scene = _scene(
        objects=[
            {
                "id": "orb",
                "kind": "orb",
                "keyframes": [{"t": 1.0, "x": 0.5, "y": 0.5}, {"t": 0.5, "x": 0.4, "y": 0.4}],
            }
        ]
    )
    assert any("не по времени" in message for message in scene.validate())


def test_keyframe_beyond_duration_rejected():
    scene = _scene(
        objects=[
            {
                "id": "orb",
                "kind": "orb",
                "keyframes": [{"t": 0.0, "x": 0.5, "y": 0.5}, {"t": 9.0, "x": 0.4, "y": 0.4}],
            }
        ]
    )
    assert any("вне длительности" in message for message in scene.validate())


def test_unsorted_camera_keyframes_rejected():
    scene = _scene(camera=[{"t": 1.0}, {"t": 0.2}])
    assert any("камеры идут не по времени" in message for message in scene.validate())


def test_wrong_version_rejected():
    scene = _scene(version=99)
    assert any("Неподдерживаемая версия" in message for message in scene.validate())


def test_contact_sheet_moment_outside_scene_rejected():
    scene = _scene(duration=2.0, contact_times=[0.5, 4.0])
    assert any("контактного листа" in message for message in scene.validate())


# ------------------------------------------------------- timeline & objects


def test_frame_count_matches_duration_and_fps():
    scene = _scene(fps=30, duration=13.0)
    assert scene.frame_count() == 390
    assert _scene(fps=10, duration=1.5).frame_count() == 15


def test_object_transform_interpolates_between_keyframes():
    scene = _scene()
    orb = scene.objects[0]
    start = sample_object(orb, 0.0)
    middle = sample_object(orb, 1.0)
    end = sample_object(orb, 2.0)
    assert start["x"] == pytest.approx(0.5)
    assert end["x"] == pytest.approx(0.7)
    assert start["x"] < middle["x"] < end["x"]
    assert middle["y"] < start["y"]  # moves upward
    assert end["scale"] > start["scale"]


def test_object_respects_appear_time():
    scene = _scene(
        objects=[
            {
                "id": "orb",
                "kind": "orb",
                "appear_at": 1.0,
                "keyframes": [{"t": 0.0, "x": 0.5, "y": 0.5}, {"t": 2.0, "x": 0.5, "y": 0.5}],
            }
        ]
    )
    orb = scene.objects[0]
    assert sample_object(orb, 0.5)["visible"] == 0.0
    assert sample_object(orb, 1.5)["visible"] == 1.0


# ----------------------------------------------------- independent animation


def test_walking_moves_character_and_swings_limbs_independently():
    scene = _scene()
    renderer = AnimationRenderer(scene)
    milo = scene.character("a")
    assert milo is not None

    early = renderer.character_pose(milo, 0.3)
    # 0.9 s is 3/4 of the 1.2 s walk: the stride is at its widest, so the legs
    # cannot coincide by construction (1.0 s lands exactly on a zero crossing).
    later = renderer.character_pose(milo, 0.9)

    angles_early, x_early = early[0], early[1]
    angles_later, x_later = later[0], later[1]

    assert x_later > x_early  # he walks forward
    assert angles_early["leg_l"] != pytest.approx(angles_later["leg_l"], abs=1e-3)
    # left and right legs are out of phase
    assert angles_later["leg_l"] != pytest.approx(angles_later["leg_r"], abs=1e-3)
    # arms swing opposite to legs
    assert angles_later["arm_l"] != pytest.approx(angles_later["leg_l"], abs=1e-3)


def test_parts_move_independently_of_the_camera():
    scene = _scene()
    renderer = AnimationRenderer(scene)
    frames = [renderer.frame(index / scene.fps, camera=False) for index in range(scene.frame_count())]
    first = frames[0].tobytes()
    assert any(frame.tobytes() != first for frame in frames[1:])


def test_face_blinks_and_mouth_opens():
    scene = _scene()
    renderer = AnimationRenderer(scene)
    nina = scene.character("b")
    assert nina is not None
    open_face = renderer.character_pose(nina, 0.45)[4]
    react_face = renderer.character_pose(nina, 0.9)[4]
    assert open_face["blink"] == pytest.approx(1.0)
    assert react_face["mouth"] > 0.2
    blink_face = renderer.character_pose(nina, 0.55)[4]
    assert blink_face["blink"] < 1.0


def test_camera_and_limbs_change_separately():
    scene = _scene()
    renderer = AnimationRenderer(scene)
    assert renderer.camera_at(0.0).zoom < renderer.camera_at(2.0).zoom


# ----------------------------------------------------------- determinism


def test_rendering_is_deterministic():
    scene = _scene()
    first = AnimationRenderer(scene).frame(0.7).tobytes()
    second = AnimationRenderer(scene).frame(0.7).tobytes()
    assert first == second


def test_loaded_scene_roundtrips_through_dict():
    scene = _scene()
    again = AnimationScene.from_dict(json.loads(json.dumps(scene.to_dict())))
    assert again.to_dict() == scene.to_dict()


# --------------------------------------------------------------- integration


@pytest.mark.filterwarnings("ignore")
def test_render_produces_valid_mp4(config, tmp_path):
    scene_path = tmp_path / "scene.json"
    scene_path.write_text(json.dumps(_scene_dict()), encoding="utf-8")
    engine = AnimationEngine(config)
    out_dir = tmp_path / "out"

    result = engine.render(scene_path, out_dir)

    video = Path(result.video)
    assert video.exists() and video.stat().st_size > 1000
    assert result.fps == 12
    assert result.width == 180 and result.height == 320
    assert result.frames == 24
    assert result.codecs.get("video") == "h264"
    assert result.codecs.get("audio") == "aac"
    assert result.decode_ok is True
    assert result.motion_checks["limbs_move_without_camera"] is True
    assert Path(result.contact_sheet).exists()
    assert Path(result.characters).exists()
    assert Path(result.scene_copy).exists()
    report = Path(result.report).read_text(encoding="utf-8")
    assert "Полное декодирование FFmpeg: успешно" in report
    document = json.loads(Path(result.report_json).read_text(encoding="utf-8"))
    assert document["verified"]["decode_full"] is True
    assert all(item["identical"] for item in document["verified"]["determinism"])


def test_motion_check_detects_real_limb_movement(config, tmp_path):
    scene_path = tmp_path / "scene.json"
    scene_path.write_text(json.dumps(_scene_dict()), encoding="utf-8")
    scene = load_scene(scene_path)
    motion = AnimationEngine(config).verify_motion(scene)
    assert motion["limbs_move_without_camera"] is True
    assert all(value > motion["frozen_camera_threshold"] for value in motion["frozen_camera_diffs"])


def test_motion_evidence_reports_joint_travel_and_moving_object(config, tmp_path):
    scene_path = tmp_path / "scene.json"
    scene_path.write_text(json.dumps(_scene_dict()), encoding="utf-8")
    scene = load_scene(scene_path)
    motion = AnimationEngine(config).verify_motion(scene)

    character = next(item for item in motion["characters"] if item["id"] == "a")
    assert character["root_travel_px"] >= motion["root_travel_threshold_px"]
    assert character["max_limb_travel_px"] >= motion["limb_travel_threshold_px"]
    assert {"foot_l", "foot_r"} <= set(character["moving_limbs"])
    assert motion["character_walks"] is True
    assert motion["objects_moved_px"]["orb"] > 0


def test_scene_contains_several_distinct_actions():
    scene = _scene()
    actions = {item.action for item in scene.actions}
    assert len(actions) >= 3
    assert {"walk", "wave", "react"} <= actions


# ------------------------------------------------------------ voice and lipsync


def test_voice_validation_rejects_unknown_character():
    scene = _scene(voice={"lang": "ru", "lines": [
        {"character": "ghost", "text": "привет", "start": 0.0}
    ]})
    assert any("неизвестного персонажа" in message for message in scene.validate())


def test_voice_validation_rejects_empty_text_and_language():
    scene = _scene(voice={"lang": "de", "lines": [{"character": "a", "text": "   ", "start": 0.0}]})
    errors = scene.validate()
    assert any("Некорректный язык озвучки" in message for message in errors)
    assert any("пустая" in message for message in errors)


def test_voice_validation_rejects_overlapping_lines_of_one_character():
    scene = _scene(voice={"lang": "ru", "lines": [
        {"character": "a", "text": "раз", "start": 0.2},
        {"character": "a", "text": "два", "start": 0.3},
    ]})
    assert any("почти одновременно" in message for message in scene.validate())


def test_mouth_follows_loudness_envelope():
    from shortsfactory.animation.voice import VoiceLineTrack

    envelope = np.array([0.0, 0.0, 1.0, 0.6, 0.0, 0.0])
    line = VoiceLineTrack(
        character="a", text="hi", start=1.0, duration=0.12, source="x.wav", envelope=envelope
    )
    assert line.mouth(0.5)[1] is False
    assert line.mouth(1.0)[0] < 0.2          # silent lead-in
    assert line.mouth(1.04)[0] > 0.8         # loud peak
    assert line.mouth(1.13)[1] is False      # line is over
    for t in (1.0, 1.02, 1.04, 1.06):
        assert 0.0 <= line.mouth(t)[0] <= 1.0


def test_lip_sync_integration_uses_real_tts(config, tmp_path):
    """Synthesizes one line with Piper and checks the mouth follows the WAV."""
    from shortsfactory.animation.voice import build_voice_track

    scene = _scene(
        duration=3.0,
        voice={"lang": "ru", "gain": 0.9, "lines": [
            {"character": "a", "text": "Привет, это проверка рта.", "start": 0.3}
        ]},
        actions=[
            {"character": "a", "action": "idle", "start": 0.0, "duration": 3.0, "params": {}},
            {"character": "b", "action": "idle", "start": 0.0, "duration": 3.0, "params": {}},
        ],
        objects=[],
        camera=[{"t": 0.0}, {"t": 3.0}],
    )
    track = build_voice_track(scene, config, tmp_path / "audio")
    assert track is not None and track.path.exists()
    assert len(track.lines) == 1
    line = track.lines[0]
    assert line.duration > 0.2

    engine = AnimationEngine(config)
    report = engine.verify_lip_sync(scene, track)
    assert report["enabled"] is True
    assert report["verified"] is True
    renderer = AnimationRenderer(scene, config, voice=track)
    mouth_mid = renderer.character_pose(scene.characters[0], line.start + line.duration / 2)
    assert mouth_mid[4]["mouth"] >= 0.0


# ------------------------------------------------------------- visual modes


def test_visual_modes_are_defined():
    assert VISUAL_MODES == ["images", "animated"]


def test_plan_defaults_to_images_mode():
    plan = Plan.from_dict({"project_id": "x", "topic": "t"})
    assert plan.visual_mode == "images"
    assert Plan.from_dict({"visual_mode": "animated"}).visual_mode == "animated"


# ------------------------------------------------------- v2 attachment events


def _v2_dict(**overrides):
    data = _scene_dict(**overrides)
    data["version"] = 2
    data.setdefault("events", [])
    return data


def _v2_scene(**overrides) -> AnimationScene:
    return AnimationScene.from_dict(_v2_dict(**overrides))


def _reach_actions(duration: float = 2.0):
    return [
        {"character": "a", "action": "reach", "start": 0.0, "duration": duration,
         "params": {"intensity": 0.5}},
        {"character": "b", "action": "reach", "start": 0.0, "duration": duration,
         "params": {"intensity": 0.5}},
    ]


def test_v1_scene_still_validates_without_events():
    scene = _scene()
    assert scene.version == 1
    assert scene.events == []
    assert scene.validate() == []


def test_attach_event_is_valid_and_requires_version_2():
    events = [{"t": 0.4, "kind": "attach", "object": "orb", "character": "a", "point": "hand_r"}]
    assert _v2_scene(events=events).validate() == []
    old = _scene(events=events)
    assert any("требует version: 2" in message for message in old.validate())


def test_attach_rejects_unknown_rig_point():
    scene = _v2_scene(events=[
        {"t": 0.4, "kind": "attach", "object": "orb", "character": "a", "point": "tail"},
    ])
    assert any("неизвестная точка рига" in message for message in scene.validate())


def test_attach_rejects_unknown_character_and_object():
    scene = _v2_scene(events=[
        {"t": 0.4, "kind": "attach", "object": "ghost", "character": "a", "point": "hand_r"},
        {"t": 0.5, "kind": "attach", "object": "orb", "character": "nobody", "point": "hand_r"},
    ])
    messages = scene.validate()
    assert any("неизвестный объект" in message for message in messages)
    assert any("неизвестный персонаж" in message for message in messages)


def test_attach_rejects_object_that_is_already_held():
    scene = _v2_scene(events=[
        {"t": 0.3, "kind": "attach", "object": "orb", "character": "a", "point": "hand_r"},
        {"t": 0.6, "kind": "attach", "object": "orb", "character": "b", "point": "hand_l"},
    ])
    assert any("уже удерживается" in message for message in scene.validate())


def test_detach_without_attach_is_rejected():
    scene = _v2_scene(events=[
        {"t": 0.5, "kind": "detach", "object": "orb", "character": "a"},
    ])
    assert any("нельзя отпустить" in message for message in scene.validate())


def test_transfer_to_self_and_unknown_target_are_rejected():
    scene = _v2_scene(events=[
        {"t": 0.3, "kind": "attach", "object": "orb", "character": "a", "point": "hand_r"},
        {"t": 0.6, "kind": "transfer", "object": "orb", "character": "a", "target": "a"},
        {"t": 0.9, "kind": "transfer", "object": "orb", "character": "a", "target": "ghost"},
    ])
    messages = scene.validate()
    assert any("сам себе" in message for message in messages)
    assert any("неизвестный получатель" in message for message in messages)


def test_two_events_for_one_object_at_same_time_are_rejected():
    scene = _v2_scene(events=[
        {"t": 0.4, "kind": "attach", "object": "orb", "character": "a", "point": "hand_r"},
        {"t": 0.4, "kind": "detach", "object": "orb", "character": "a"},
    ])
    assert any("в один момент" in message for message in scene.validate())


def test_held_object_stays_at_constant_distance_from_the_hand():
    scene = _v2_scene(
        actions=_reach_actions(),
        events=[{"t": 0.3, "kind": "attach", "object": "orb", "character": "a",
                 "point": "hand_r", "offset": [0.0, 0.02]}],
    )
    assert scene.validate() == []
    renderer = AnimationRenderer(scene, None)
    character = scene.characters[0]
    height = renderer.character_height_px(character)
    distances = []
    for moment in (0.4, 0.7, 1.0, 1.3, 1.6, 1.9):
        parts, _face, _x, _facing = renderer.character_parts(character, moment)
        tip, _angle = rig.attachment_transform(parts, "hand_r", height)
        state = renderer.object_transform(scene.objects[0], moment)
        distances.append(math.hypot(state["x"] * renderer.sw - tip[0],
                                    state["y"] * renderer.sh - tip[1]))
    assert max(distances) - min(distances) < 1.0
    assert abs(distances[0] - 0.02 * height) < 1.0


def test_transfer_switches_holder_without_a_large_jump():
    scene = _v2_scene(
        actions=_reach_actions(),
        events=[
            {"t": 0.3, "kind": "attach", "object": "orb", "character": "a", "point": "hand_r"},
            {"t": 1.0, "kind": "transfer", "object": "orb", "character": "a",
             "target": "b", "point": "hand_r"},
        ],
    )
    assert scene.validate() == []
    renderer = AnimationRenderer(scene, None)
    scene_object = scene.objects[0]
    assert renderer.holder_at(scene_object, 0.9) == "a"
    assert renderer.holder_at(scene_object, 1.1) == "b"
    before = renderer.object_transform(scene_object, 0.999)
    after = renderer.object_transform(scene_object, 1.001)
    jump = math.hypot((after["x"] - before["x"]) * renderer.sw,
                      (after["y"] - before["y"]) * renderer.sh) / 1.25
    assert jump < 160.0


def test_released_object_continues_with_the_declared_velocity():
    scene = _v2_scene(
        actions=_reach_actions(),
        events=[
            {"t": 0.3, "kind": "attach", "object": "orb", "character": "a", "point": "hand_r"},
            {"t": 1.0, "kind": "detach", "object": "orb", "character": "a",
             "velocity": [0.1, 0.2], "spin": 90.0},
        ],
    )
    assert scene.validate() == []
    renderer = AnimationRenderer(scene, None)
    scene_object = scene.objects[0]
    base = renderer.object_transform(scene_object, 1.0)
    later = renderer.object_transform(scene_object, 1.5)
    assert renderer.holder_at(scene_object, 1.2) == ""
    assert later["x"] == pytest.approx(base["x"] + 0.1 * 0.5, abs=1e-6)
    assert later["y"] == pytest.approx(base["y"] + 0.2 * 0.5, abs=1e-6)
    assert later["rotation"] == pytest.approx(base["rotation"] + 90.0 * 0.5, abs=1e-6)


def test_idle_character_keeps_the_last_walk_position():
    scene = _scene(
        objects=[],
        actions=[{"character": "a", "action": "walk", "start": 0.0, "duration": 1.2,
                  "params": {"to_x": 0.6}}],
        camera=[{"t": 0.0}, {"t": 2.0}],
    )
    renderer = AnimationRenderer(scene, None)
    assert renderer.character_pose(scene.characters[0], 1.8)[1] == pytest.approx(0.6)


# ------------------------------------------------------------ v2 timeline


def test_dependencies_shift_start_after_the_previous_action():
    scene = _v2_scene(actions=[
        {"id": "walk", "character": "a", "action": "walk", "start": 0.0, "duration": 1.0,
         "params": {"to_x": 0.6}},
        {"id": "wave", "character": "a", "action": "wave", "start": 0.0, "duration": 0.5,
         "params": {}, "after": ["walk"], "gap": 0.1},
    ])
    assert scene.timeline_errors == []
    assert scene.actions[1].start == pytest.approx(1.1)


def test_dependency_on_unknown_action_is_reported():
    scene = _v2_scene(actions=[
        {"id": "wave", "character": "a", "action": "wave", "start": 0.0, "duration": 0.5,
         "params": {}, "after": ["missing"]},
    ])
    assert any("неизвестное действие" in message for message in scene.timeline_errors)


def test_dependency_cycle_is_reported():
    scene = _v2_scene(actions=[
        {"id": "one", "character": "a", "action": "wave", "start": 0.0, "duration": 0.4,
         "params": {}, "after": ["two"]},
        {"id": "two", "character": "a", "action": "idle", "start": 0.0, "duration": 0.4,
         "params": {}, "after": ["one"]},
    ])
    assert any("Циклическая" in message for message in scene.timeline_errors)


def test_duplicate_action_id_is_reported():
    scene = _v2_scene(actions=[
        {"id": "same", "character": "a", "action": "wave", "start": 0.0, "duration": 0.4,
         "params": {}},
        {"id": "same", "character": "a", "action": "idle", "start": 0.5, "duration": 0.4,
         "params": {}},
    ])
    assert any("Повторяющийся id действия" in message for message in scene.timeline_errors)


# ------------------------------------------------------------- v2 pose library


def test_reach_pose_moves_the_whole_body_not_only_the_arm():
    neutral = pose_angles("neutral")
    reach = pose_angles("reach_forward")
    for joint in ("torso", "arm_r", "forearm_r", "hand_r", "leg_r"):
        assert abs(reach[joint] - neutral.get(joint, 0.0)) > 0.05
    relaxed = pose_angles("reach_forward", 0.0)
    for joint, value in neutral.items():
        assert relaxed[joint] == pytest.approx(value)


def test_pose_transition_starts_smoothly_and_stop_returns_to_neutral():
    start = evaluate("reach", 0.0, {}, 0.2).angles
    just_after = evaluate("reach", 0.02, {}, 0.2).angles
    assert abs(just_after["arm_r"] - start["arm_r"]) < 0.05
    stopped = evaluate("stop", 1.0, {}, 0.2).angles
    assert stopped["arm_r"] == pytest.approx(POSES["neutral"]["arm_r"], abs=0.01)


def test_expression_actions_carry_their_expression():
    assert evaluate("surprise", 0.5, {}, 0.2).expression == "surprise"
    assert evaluate("joy", 0.5, {}, 0.2).expression == "joy"
    assert evaluate("confused", 0.5, {}, 0.2).expression == "confused"
    assert evaluate("annoyed", 0.5, {}, 0.2).expression == "annoyed"


# --------------------------------------------------------------- v2 look assets


def test_shipped_looks_are_distinct_and_loadable():
    assert {"milo", "nina"} <= set(list_looks())
    milo = load_look("milo")
    nina = load_look("nina")
    assert milo["palette"]["shirt"] != nina["palette"]["shirt"]
    assert milo["hair"]["style"] != nina["hair"]["style"]
    assert milo["face"] != nina["face"]
    assert milo["proportions"] != nina["proportions"]


def test_malformed_look_is_rejected(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({
        "version": 1, "id": "broken", "hair": {"style": "mohawk"},
        "palette": {"skin": "red"},
    }), encoding="utf-8")
    with pytest.raises(LookError) as error:
        load_look(str(path))
    assert "mohawk" in str(error.value)


def test_scene_rejects_missing_look_file():
    scene = _v2_scene(characters=[
        {"id": "a", "x": 0.2, "look": "does_not_exist"},
    ])
    assert any("Не найден файл внешности" in message for message in scene.validate())


# ------------------------------------------------------------- v2 storyboard


def _pilot_storyboard(config):
    return json.loads(
        (config.root / "animation" / "storyboards" / "pilot_room_v2.json").read_text(encoding="utf-8")
    )


def test_pilot_storyboard_compiles_into_a_valid_scene(config):
    compiled = load_storyboard(config.root / "animation" / "storyboards" / "pilot_room_v2.json")
    assert compiled["version"] == 2
    assert compiled["duration"] == 17.0
    assert len(compiled["actions"]) == 16
    kinds = [event["kind"] for event in compiled["events"]]
    assert kinds.count("attach") == 1
    assert kinds.count("transfer") == 1
    assert kinds.count("detach") == 1
    assert len(compiled["voice"]["lines"]) == 3
    scene = AnimationScene.from_dict(compiled)
    assert scene.validate() == []


def test_storyboard_rejects_unknown_verb(config):
    data = _pilot_storyboard(config)
    data["beats"][0]["do"] = "backflip"
    with pytest.raises(StoryboardError) as error:
        compile_storyboard(data)
    assert "неизвестное действие" in str(error.value)
    assert "walk_to" in str(error.value)


def test_storyboard_rejects_known_but_unsupported_verb(config):
    data = _pilot_storyboard(config)
    data["beats"][0]["do"] = "climb"
    with pytest.raises(StoryboardError) as error:
        compile_storyboard(data)
    assert "не поддерживается" in str(error.value)


def test_storyboard_rejects_unknown_and_cyclic_dependencies(config):
    data = _pilot_storyboard(config)
    data["beats"][0]["after"] = ["ghost"]
    with pytest.raises(StoryboardError) as error:
        compile_storyboard(data)
    assert "неизвестный бит" in str(error.value)

    data = _pilot_storyboard(config)
    data["beats"][0]["after"] = [data["beats"][1]["id"]]
    data["beats"][1]["after"] = [data["beats"][0]["id"]]
    with pytest.raises(StoryboardError) as error:
        compile_storyboard(data)
    assert "Циклическая" in str(error.value)


# --------------------------------------------------------- v2 end-to-end MP4


def test_v2_mp4_with_attachment_is_rendered_and_verified(config, tmp_path):
    data = _v2_dict(
        actions=_reach_actions(),
        events=[
            {"t": 0.2, "kind": "attach", "object": "orb", "character": "a", "point": "hand_r"},
            {"t": 0.8, "kind": "transfer", "object": "orb", "character": "a",
             "target": "b", "point": "hand_r"},
            {"t": 1.5, "kind": "detach", "object": "orb", "character": "b",
             "velocity": [0.1, 0.2]},
        ],
        contact_times=[0.4, 0.9, 1.6],
        camera=[{"t": 0.0, "x": 0.5, "y": 0.5, "zoom": 1.0},
                {"t": 2.0, "x": 0.5, "y": 0.5, "zoom": 1.1}],
    )
    scene_path = tmp_path / "v2_scene.json"
    scene_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    result = AnimationEngine(config).render(scene_path, tmp_path / "out")

    assert Path(result.video).exists()
    assert result.decode_ok is True
    assert result.attachment_checks["enabled"] is True
    assert result.attachment_checks["verified"] is True
    holds = result.attachment_checks["objects"][0]["holds"]
    assert len(holds) == 2
    assert all(hold["follows_hand"] for hold in holds)
    assert result.attachment_checks["objects"][0]["transfer_jump_px"] < 160.0
    report = Path(result.report).read_text(encoding="utf-8")
    assert "Привязка объектов" in report


# -------------------------------------------------------- v3 animated project


def _mini_animated_storyboard() -> dict:
    return {
        "version": 1,
        "scene": {
            "id": "mini_v3",
            "title": "Мини-проверка v3",
            "duration": 2.5,
            "fps": 30,
            "width": 540,
            "height": 960,
            "seed": 7,
            "blend": 0.15,
            "environment": "room",
        },
        "characters": [
            {"id": "milo", "name": "Мило", "look": "milo", "x": 0.30, "y": 1.0,
             "scale": 1.0, "facing": 1, "z": 20},
            {"id": "nina", "name": "Нина", "look": "nina", "x": 0.70, "y": 1.0,
             "scale": 0.98, "facing": -1, "z": 22},
        ],
        "objects": [],
        "beats": [
            {"id": "m_walk", "actor": "milo", "do": "walk_to", "x": 0.40,
             "duration": 1.0, "cycles": 2.0, "stride": 0.5},
            {"id": "m_line", "actor": "milo", "do": "say",
             "text": "Привет, Нина!", "duration": 1.2, "start": 1.1},
            {"id": "n_joy", "actor": "nina", "do": "joy", "duration": 1.0, "start": 1.2},
        ],
        "camera": [
            {"t": 0.0, "x": 0.50, "y": 0.52, "zoom": 1.04},
            {"t": 2.5, "x": 0.50, "y": 0.52, "zoom": 1.04},
        ],
        "blink_times": [0.8, 1.9],
        "contact_times": [0.6, 1.6],
        "voice": {"lang": "ru", "gain": 0.9},
    }


def test_pilot_v3_storyboard_compiles_with_the_new_actions(config):
    compiled = load_storyboard(config.root / "animation" / "storyboards" / "pilot_room_v3.json")
    assert compiled["version"] == 2
    assert compiled["duration"] == 19.0
    assert compiled["environment"] == "room"
    actions = {action["action"] for action in compiled["actions"]}
    assert {"look_around", "shrug", "laugh", "stumble"} <= actions
    scene = AnimationScene.from_dict(compiled)
    assert scene.validate() == []


def test_animated_project_renders_storyboard_and_writes_subtitles(config, tmp_path, monkeypatch):
    storyboard_path = tmp_path / "storyboard.json"
    storyboard_path.write_text(
        json.dumps(_mini_animated_storyboard(), ensure_ascii=False), encoding="utf-8"
    )
    workspace = tmp_path / "workspace"
    output = tmp_path / "output"
    workspace.mkdir()
    output.mkdir()
    project = Project(
        id="p",
        topic="Мини-проверка v3",
        profile="science",
        languages=["ru"],
        duration=2.5,
        status="QUEUED",
        stage="PLANNING",
        workspace=workspace.as_posix(),
        output=output.as_posix(),
        command="create --visual-mode animated",
        created_at="",
        updated_at="",
        params={},
    )
    plan = Plan(
        project_id="p",
        topic="Мини-проверка v3",
        topic_en="",
        profile="science",
        languages=["ru"],
        duration=2.5,
        style="scientific",
        pace="medium",
        visual_density="medium",
        visual_mode="animated",
        animation_storyboard=storyboard_path.as_posix(),
    )

    seen: dict = {}

    def fake_render(self, scene_path, out_dir):
        seen["scene"] = json.loads(Path(scene_path).read_text(encoding="utf-8"))
        out_dir = Path(out_dir)
        video = out_dir / "pilot.mp4"
        report = out_dir / "report.json"
        contact = out_dir / "contact_sheet.jpg"
        video.write_bytes(b"fake-mp4")
        report.write_text(json.dumps({"verified": {"decode_full": True}}), encoding="utf-8")
        contact.write_bytes(b"fake-jpg")
        return SimpleNamespace(
            video=video.as_posix(),
            report_json=report.as_posix(),
            contact_sheet=contact.as_posix(),
            decode_ok=True,
            motion_checks={"limbs_move_without_camera": True},
            attachment_checks={"enabled": False},
            lip_sync={"enabled": True, "lines": [{"start": 1.1, "duration": 1.2}]},
            frames=75,
            size_bytes=video.stat().st_size,
        )

    monkeypatch.setattr("shortsfactory.jobs.AnimationEngine.render", fake_render)
    service = JobService.__new__(JobService)
    service.config = config

    details = service._render_animated(project, plan)

    assert "RU: 2.5" in details
    assert seen["scene"]["id"] == "mini_v3"
    assert (workspace / "animation" / "scene.compiled.json").exists()
    assert (workspace / "animation" / "storyboard.source.json").exists()
    srt = (workspace / "subtitles" / "ru.srt").read_text(encoding="utf-8")
    assert "Привет, Нина!" in srt
    assert "00:00:01,100 --> 00:00:02,300" in srt
    videos = list((output / "RU").glob("RU_*.mp4"))
    assert len(videos) == 1
    assert videos[0].read_bytes() == b"fake-mp4"
    assert (output / "reports" / "animation_ru.json").exists()
    assert project.params["animation"]["duration"] == 2.5


# -------------------------------------------------------- v4 QC verification


def test_segments_intersect_unit():
    from shortsfactory.animation.engine import _segments_intersect

    assert _segments_intersect((0.0, 0.0), (1.0, 1.0), (0.0, 1.0), (1.0, 0.0)) is True
    assert _segments_intersect((0.0, 0.0), (2.0, 0.0), (1.0, -1.0), (1.0, 1.0)) is True
    assert _segments_intersect((0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)) is False
    assert _segments_intersect((0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)) is False


def test_verify_framing_flags_outside_hand(config):
    scene = _scene(
        characters=[{"id": "a", "x": 0.18, "y": 0.9, "facing": 1, "z": 20}],
        camera=[
            {"t": 0.0, "x": 0.9, "y": 0.55, "zoom": 1.8},
            {"t": 2.0, "x": 0.9, "y": 0.55, "zoom": 1.8},
        ],
    )
    report = AnimationEngine(config).verify_framing(scene)
    assert report["issues"]
    assert any("вне кадра" in issue for issue in report["issues"])


def test_pilot_v4_storyboard_is_clean_and_compiles(config):
    compiled = load_storyboard(config.root / "animation" / "storyboards" / "pilot_room_v4.json")
    assert compiled["duration"] == 20.0
    texts = [line["text"] for line in compiled["voice"]["lines"]]
    assert len(texts) == 5
    assert all("@" not in text and "#" not in text for text in texts)
    kinds = [event["kind"] for event in compiled["events"]]
    assert kinds.count("attach") == 2
    assert kinds.count("transfer") == 2
    assert kinds.count("detach") == 1


def test_pilot_v4_pose_quality_is_clean(config):
    import tempfile

    compiled = load_storyboard(config.root / "animation" / "storyboards" / "pilot_room_v4.json")
    path = Path(tempfile.mkdtemp()) / "scene_v4.json"
    path.write_text(json.dumps(compiled, ensure_ascii=False), encoding="utf-8")
    scene = load_scene(path)
    report = AnimationEngine(config).verify_pose_quality(scene)
    assert report["enabled"] is True
    assert report["issues"] == []


def test_pilot_v4_transfers_are_smooth(config):
    """Handoffs must not teleport the object (transfer jump < 160 px)."""
    import tempfile

    compiled = load_storyboard(config.root / "animation" / "storyboards" / "pilot_room_v4.json")
    path = Path(tempfile.mkdtemp()) / "scene_v4.json"
    path.write_text(json.dumps(compiled, ensure_ascii=False), encoding="utf-8")
    scene = load_scene(path)
    report = AnimationEngine(config).verify_attachment(scene)
    assert report["verified"] is True
    for obj in report["objects"]:
        assert obj["transfer_jump_px"] <= 160.0
        assert obj["detach_jump_px"] <= 40.0


