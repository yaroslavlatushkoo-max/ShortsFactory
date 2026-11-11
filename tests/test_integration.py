"""Integration tests: text -> Story Studio -> Lab -> Factory storyboard/QC."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from shortsfactory.animation.paths import path_metrics, position_at
from shortsfactory.animation.storyboard import compile_storyboard
from shortsfactory.animation.scene import AnimationScene
from shortsfactory.animation.renderer import AnimationRenderer
from shortsfactory.animation.engine import AnimationEngine
from shortsfactory.integration.animation_plan import (
    AnimationPlanAdapter,
    build_factory_storyboard,
    build_lab_scene,
)
from shortsfactory.integration.contracts import IntegrationError, IntegrationPaths, run_json_worker
from shortsfactory.integration.factory_adapter import FactoryResult, ShortsFactoryAdapter
from shortsfactory.integration.pipeline import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARNING,
    build_qc_report,
)
from shortsfactory.integration.story_adapter import StoryResult, StoryStudioAdapter
from shortsfactory.integration.text_story import parse_story_text

TEXT = (
    "Мило замечает светящийся шар на столе, подходит к нему, берёт его "
    "и передаёт Нине. Нина удивляется, благодарит Мило, после чего они вместе уходят."
)


@pytest.fixture(scope="module")
def paths() -> IntegrationPaths:
    discovered = IntegrationPaths.discover()
    try:
        discovered.check()
    except IntegrationError as exc:
        pytest.skip(f"Внешние компоненты недоступны: {exc}")
    return discovered


@pytest.fixture(scope="module")
def story(paths: IntegrationPaths, tmp_path_factory: pytest.TempPathFactory) -> StoryResult:
    out = tmp_path_factory.mktemp("story")
    return StoryStudioAdapter(paths).create(TEXT, out)


@pytest.fixture(scope="module")
def motion(paths: IntegrationPaths, story: StoryResult, tmp_path_factory: pytest.TempPathFactory):
    out = tmp_path_factory.mktemp("motion")
    return AnimationPlanAdapter(paths).build(story.project, out)


@pytest.fixture(scope="module")
def storyboard(story: StoryResult, motion):
    return build_factory_storyboard(story.project, motion.plan)


def test_text_parser_preserves_entities_timings_and_dialogue() -> None:
    spec = parse_story_text(TEXT)
    assert [character["id"] for character in spec["characters"]] == ["char_milo", "char_nina"]
    assert spec["prop"]["id"] == "prop_orb"
    assert [action["action_type"] for action in spec["actions"]] == [
        "look_at",
        "look_at",
        "walk",
        "walk",
        "pick_up",
        "walk",
        "give",
        "receive",
        "react",
        "speak",
        "react",
        "exit",
        "exit",
    ]
    assert spec["dialogue"][0]["character_id"] == "char_nina"
    assert spec["dialogue"][0]["text"] == "Спасибо тебе!"
    assert spec["actions"][0]["start_time"] == 0.0
    assert spec["actions"][0]["end_time"] == 0.8
    assert spec["duration"] >= 9.0


def test_story_studio_adapter_returns_valid_project(story: StoryResult) -> None:
    assert story.project["schema_version"] == "1.0.0"
    assert story.validation["error_count"] == 0
    assert story.validation["total_scenes"] == 1
    assert story.validation["total_actions"] == 13
    assert story.project["scenes"][0]["id"] == "scene_handoff_1"
    assert story.project_path.is_file()


def test_lab_plan_has_reachable_handoff_and_passes_validation(motion) -> None:
    assert motion.validation["status"] == "pass"
    assert len(motion.plan["motion_segments"]) >= 13
    kinds = [interaction["kind"] for interaction in motion.plan["interactions"]]
    assert "pickup" in kinds
    assert "transfer" in kinds
    transfer = next(item for item in motion.plan["interactions"] if item["kind"] == "transfer")
    assert transfer["actor_id"] == "char_milo"
    assert transfer["receiver_id"] == "char_nina"
    assert transfer["expected_owner_after"] == "char_nina"
    assert motion.plan_path.is_file()


def test_factory_storyboard_preserves_ids_objects_timings_and_voice(story: StoryResult, motion, storyboard) -> None:
    assert {character["id"] for character in storyboard["characters"]} == {"milo", "nina"}
    assert {item["id"] for item in storyboard["objects"]} == {"orb"}
    assert storyboard["scene"]["duration"] == pytest.approx(
        story.project["scenes"][0]["end_time"], abs=0.001
    )
    beats = {beat["id"]: beat for beat in storyboard["beats"]}
    assert beats["b_act_milo_pick_up_5"]["object"] == "orb"
    assert beats["b_act_milo_give_7"]["object"] == "orb"
    assert beats["b_act_milo_give_7"]["to"] == "nina"
    assert "attach_at" in beats["b_act_milo_pick_up_5"]
    assert "transfer_at" in beats["b_act_milo_give_7"]
    assert beats["b_act_nina_speak_10"]["text"] == "Спасибо тебе!"
    assert beats["b_act_nina_speak_10"]["actor"] == "nina"
    compiled = compile_storyboard(storyboard)
    events = compiled["events"]
    assert any(event["kind"] == "attach" and event["object"] == "orb" for event in events)
    assert any(event["kind"] == "transfer" and event["target"] == "nina" for event in events)
    assert any(line["character"] == "nina" for line in compiled["voice"]["lines"])


def test_lab_plan_is_deterministic(paths: IntegrationPaths, story: StoryResult, motion) -> None:
    second = AnimationPlanAdapter(paths).build(
        copy.deepcopy(story.project), motion.plan_path.parent / "second"
    )
    assert second.plan["motion_segments"] == motion.plan["motion_segments"]
    assert second.plan["interactions"] == motion.plan["interactions"]
    assert second.plan["keyframes"] == motion.plan["keyframes"]


def test_unknown_action_is_rejected(story: StoryResult) -> None:
    broken = copy.deepcopy(story.project)
    broken["scenes"][0]["actions"][0]["action_type"] = "teleport"
    with pytest.raises(IntegrationError, match="не имеет соответствия"):
        build_lab_scene(broken)


def test_unsupported_factory_action_is_rejected(story: StoryResult, motion) -> None:
    broken = copy.deepcopy(story.project)
    broken["scenes"][0]["actions"][0]["action_type"] = "dance"
    with pytest.raises(IntegrationError, match="не поддерживается"):
        build_factory_storyboard(broken, motion.plan)


def test_unknown_character_in_text_is_rejected() -> None:
    with pytest.raises(IntegrationError, match="не найден"):
        parse_story_text("Аркадий квантует пространство и исчезает.")


def test_unreachable_prop_is_reported_by_lab(paths: IntegrationPaths, story: StoryResult) -> None:
    scene = build_lab_scene(story.project)
    scene["props"]["prop_orb"]["position"] = {"x": 0.95, "y": 0.60}
    result = run_json_worker(
        paths.lab_python,
        Path(__file__).resolve().parents[1]
        / "src"
        / "shortsfactory"
        / "integration"
        / "workers"
        / "animation_lab_worker.py",
        {"lab_src": str(paths.animation_lab_root / "src"), "scene": scene},
        cwd=paths.animation_lab_root,
    )
    codes = {issue["code"] for issue in result["validation"]["issues"]}
    assert "TARGET_UNREACHABLE" in codes
    assert result["validation"]["status"] == "fail"


def test_factory_adapter_reports_compile_failure(tmp_path: Path) -> None:
    broken = {
        "version": 1,
        "scene": {"id": "broken", "duration": 1.0, "fps": 30, "width": 1080, "height": 1920},
        "characters": [{"id": "milo", "look": "milo"}],
        "objects": [],
        "beats": [{"actor": "milo", "do": "unknown_verb", "duration": 0.5}],
        "camera": [{"t": 0.0, "x": 0.5, "y": 0.5, "zoom": 1.0}],
    }
    with pytest.raises(IntegrationError, match="storyboard"):
        ShortsFactoryAdapter(None).render(broken, tmp_path / "factory")
    assert (tmp_path / "factory" / "storyboard.source.json").is_file()


def _fake_factory(tmp_path: Path, *, attachment: bool, silhouette: dict | None = None) -> FactoryResult:
    verified = {
        "decode_full": True,
        "determinism": [{"t": 1.0, "identical": True}],
        "motion": {"limbs_move_without_camera": True},
        "attachment": {"enabled": True, "verified": attachment, "objects": []},
        "framing": {"ok": True},
        "pose_quality": {"enabled": True, "issues": []},
        "lip_sync": {"ok": True},
    }
    if silhouette is not None:
        verified["silhouette_intersections"] = silhouette
    report = {
        "verified": verified,
        "ffprobe": {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]},
        "voice": {"lines": 1},
    }
    result = {
        "decode_ok": True,
        "width": 1080,
        "height": 1920,
        "frames": 276,
        "duration": 9.2,
        "size_bytes": 1000,
        "codecs": {"video": "h264", "audio": "aac"},
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return FactoryResult(
        storyboard_path=tmp_path / "storyboard.source.json",
        scene_path=tmp_path / "scene.compiled.json",
        video=tmp_path / "pilot.mp4",
        contact_sheet=None,
        report_json=report_path,
        report_md=tmp_path / "report.md",
        result=result,
        report=report,
    )


def test_qc_report_fails_when_attachment_is_broken(
    story: StoryResult, motion, storyboard, tmp_path: Path
) -> None:
    report = build_qc_report(
        output_dir=tmp_path,
        story=story,
        motion=motion,
        storyboard=storyboard,
        factory=_fake_factory(tmp_path, attachment=False),
        source_text=TEXT,
    )
    checks = {item["id"]: item["status"] for item in report["checks"]}
    assert checks["interactions"] == STATUS_FAIL
    assert report["status"] == STATUS_FAIL


def test_qc_report_is_warning_when_some_checks_are_not_checked(
    story: StoryResult, motion, storyboard, tmp_path: Path
) -> None:
    report = build_qc_report(
        output_dir=tmp_path,
        story=story,
        motion=motion,
        storyboard=storyboard,
        factory=_fake_factory(tmp_path, attachment=True),
        source_text=TEXT,
    )
    checks = {item["id"]: item["status"] for item in report["checks"]}
    assert checks["interactions"] == STATUS_PASS
    assert checks["silhouette_intersections"] == "NOT_CHECKED"
    assert report["status"] == STATUS_WARNING
    assert report["artifacts"]["video"].endswith("pilot.mp4")


def test_path_interpolation_endpoints_and_metrics() -> None:
    linear = {
        "kind": "linear",
        "points": [{"x": 0.1, "y": 0.65}, {"x": 0.9, "y": 0.65}],
        "easing": "linear",
    }
    assert position_at(linear, 0.0)["x"] == pytest.approx(0.1)
    assert position_at(linear, 1.0)["x"] == pytest.approx(0.9)
    assert path_metrics(linear)["curvature_ratio"] == pytest.approx(1.0)

    arc = {
        "kind": "arc",
        "points": [{"x": 0.2, "y": 0.65}, {"x": 0.8, "y": 0.65}],
        "easing": "ease_in_out",
        "arc_height": 0.1,
    }
    middle = position_at(arc, 0.5)
    assert middle["x"] == pytest.approx(0.5, abs=1e-3)
    assert middle["y"] < 0.65
    metrics = path_metrics(arc)
    assert metrics["kind"] == "arc"
    assert metrics["max_deviation"] > 0
    assert metrics["endpoint_error"] < 1e-6


def test_storyboard_carries_arc_paths_into_factory_actions(
    paths: IntegrationPaths, story: StoryResult, tmp_path: Path
) -> None:
    arc_motion = AnimationPlanAdapter(paths).build(
        copy.deepcopy(story.project), tmp_path / "motion_arc", motion_profile="arc"
    )
    storyboard = build_factory_storyboard(story.project, arc_motion.plan)
    compiled = compile_storyboard(storyboard)
    actions = [action for action in compiled["actions"] if action["params"].get("path")]
    assert len(actions) >= 5
    for action in actions:
        path = action["params"]["path"]
        assert path["kind"] == "arc"
        end = position_at(path, 1.0)
        assert end["x"] == pytest.approx(action["params"]["to_x"], abs=1e-3)


def test_qc_maps_real_silhouette_failure(
    story: StoryResult, motion, storyboard, tmp_path: Path
) -> None:
    report = build_qc_report(
        output_dir=tmp_path,
        story=story,
        motion=motion,
        storyboard=storyboard,
        factory=_fake_factory(
            tmp_path,
            attachment=True,
            silhouette={
                "enabled": True,
                "status": "FAIL",
                "checked_frames": 10,
                "failure_count": 1,
                "warning_count": 0,
                "issues": [{"kind": "character_character", "timestamp": 1.0}],
            },
        ),
        source_text=TEXT,
    )
    checks = {item["id"]: item["status"] for item in report["checks"]}
    assert checks["silhouette_intersections"] == STATUS_FAIL
    assert report["status"] == STATUS_FAIL


@pytest.mark.parametrize(
    ("text", "profile"),
    [
        (TEXT, "linear"),
        (
            "Мило замечает светящийся шар на столе, подходит к нему, берёт его "
            "и плавно передаёт Нине по дуге. Нина удивляется, благодарит Мило, "
            "после чего они вместе уходят.",
            "arc",
        ),
        ("Мило замечает светящийся шар на столе и берёт его.", "linear"),
        (
            "Мило замечает светящийся шар на столе, обходит стол по точкам и берёт его.",
            "waypoints",
        ),
        (
            "Мило замечает светящийся шар, берёт его и передаёт Нине. Нина удивляется "
            "и говорит: «Какой красивый шар!» Мило говорит: «Я нашёл его для тебя». "
            "Они уходят.",
            "linear",
        ),
        (
            "Мило замечает светящийся шар, берёт его и передаёт Нине. Нина удивляется, "
            "благодарит и возвращает шар Мило. Они уходят.",
            "linear",
        ),
    ],
)
def test_scenario_adapters_end_to_end(
    paths: IntegrationPaths, tmp_path: Path, text: str, profile: str
) -> None:
    story = StoryStudioAdapter(paths).create(text, tmp_path / "story")
    assert story.validation["error_count"] == 0
    motion = AnimationPlanAdapter(paths).build(
        story.project, tmp_path / "motion", motion_profile=profile
    )
    assert motion.validation["status"] in {"pass", "warnings"}
    storyboard = build_factory_storyboard(story.project, motion.plan)
    compiled = compile_storyboard(storyboard)
    assert compiled["actions"]
    assert compiled["events"] or not any(
        action["action"] in {"take", "give"} for action in compiled["actions"]
    )


def test_speech_lines_add_body_gesture_beats(storyboard) -> None:
    says = [beat for beat in storyboard["beats"] if beat["do"] == "say"]
    gestures = [beat for beat in storyboard["beats"] if beat["id"].startswith("g_")]
    assert len(says) == 1
    assert len(gestures) == len(says)
    assert {beat["do"] for beat in gestures} <= {"clap", "joy", "wave"}
    compiled = compile_storyboard(storyboard)
    gesture_actions = {action["id"] for action in compiled["actions"]}
    for gesture in gestures:
        assert gesture["id"] in gesture_actions


def test_waypoints_are_carried_into_factory_paths(
    paths: IntegrationPaths, story: StoryResult, tmp_path: Path
) -> None:
    way_motion = AnimationPlanAdapter(paths).build(
        copy.deepcopy(story.project), tmp_path / "motion_way", motion_profile="waypoints"
    )
    way_storyboard = build_factory_storyboard(story.project, way_motion.plan)
    compiled = compile_storyboard(way_storyboard)
    paths_in_actions = [
        action["params"]["path"]
        for action in compiled["actions"]
        if action["params"].get("path")
    ]
    assert len(paths_in_actions) >= 5
    assert {path["kind"] for path in paths_in_actions} == {"waypoints"}
    for path in paths_in_actions:
        metrics = path_metrics(path)
        assert metrics["max_deviation"] > 0
        assert metrics["endpoint_error"] < 1e-6


def test_engine_silhouette_detector_flags_overlapping_characters() -> None:
    data = {
        "id": "silhouette_probe",
        "title": "silhouette probe",
        "duration": 1.0,
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "seed": 1,
        "blend": 0.16,
        "environment": "room",
        "background": {},
        "version": 2,
        "characters": [
            {"id": "milo", "name": "Мило", "look": "milo", "x": 0.5, "y": 1.0},
            {"id": "nina", "name": "Нина", "look": "nina", "x": 0.5, "y": 1.0},
        ],
        "objects": [],
        "actions": [
            {"id": "a1", "character": "milo", "action": "idle", "start": 0.0, "duration": 1.0}
        ],
        "camera": [{"t": 0.0, "x": 0.5, "y": 0.5, "zoom": 1.0}],
        "voice": {"lang": "ru", "gain": 0.9, "lines": []},
    }
    scene = AnimationScene.from_dict(data)
    renderer = AnimationRenderer(scene)
    report = AnimationEngine(None).verify_silhouette_intersections(scene, renderer)
    assert report["enabled"] is True
    assert report["checked_frames"] > 0
    assert report["status"] == "FAIL"
    assert report["failure_count"] >= 1


def test_qc_trajectory_integration_fails_when_path_is_missing(
    story: StoryResult, motion, storyboard, tmp_path: Path
) -> None:
    broken = copy.deepcopy(storyboard)
    for beat in broken["beats"]:
        beat.pop("path", None)
    report = build_qc_report(
        output_dir=tmp_path,
        story=story,
        motion=motion,
        storyboard=broken,
        factory=_fake_factory(tmp_path, attachment=True),
        source_text=TEXT,
    )
    checks = {item["id"]: item["status"] for item in report["checks"]}
    assert checks["trajectory_integration"] == STATUS_FAIL

