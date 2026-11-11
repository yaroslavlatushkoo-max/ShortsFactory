"""Story Studio Project -> Animation Lab motion plan -> Factory storyboard.

The Lab owns geometry, interpolation, interactions and its validation.  This
module only translates the already-validated semantic project into the Lab input
format, runs the Lab in its own environment, and projects the resulting plan
back onto the Factory storyboard vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..animation.paths import path_from_lab_segment
from .contracts import IntegrationError, IntegrationPaths, run_json_worker, write_json
from .text_story import CHARACTERS


STORY_TO_LAB_ACTION: dict[str, str] = {
    "idle": "idle",
    "walk": "walk",
    "run": "run",
    "turn": "turn",
    "look_at": "turn",
    "look_around": "turn",
    "point": "reach",
    "wave": "reach",
    "reach": "reach",
    "pick_up": "reach",
    "put_down": "reach",
    "give": "reach",
    "receive": "reach",
    "jump": "jump",
    "land": "land",
    "crouch": "sit",
    "sit": "sit",
    "stand": "stand",
    "stumble": "step_back",
    "dodge": "step_back",
    "clap": "react",
    "laugh": "react",
    "cry": "react",
    "dance": "react",
    "enter": "walk",
    "exit": "walk",
    "speak": "speak",
    "wait": "idle",
    "stop": "idle",
    "react": "react",
}

LAB_TO_FACTORY_VERB: dict[str, str] = {
    "look_at": "look_around",
    "look_around": "look_around",
    "walk": "walk_to",
    "run": "run_to",
    "enter": "enter",
    "exit": "exit",
    "turn": "turn_to",
    "pick_up": "take",
    "give": "give",
    "receive": "reach",
    "speak": "say",
    "react": "react",
    "idle": "idle",
    "wait": "idle",
}

_REACTIONS: dict[str, str] = {
    "surprise_lean_back": "surprise",
    "curious_tilt": "confused",
    "amazed_gasp": "surprise",
    "alarmed_jolt": "annoyed",
    "relief_sigh": "joy",
    "determined_nod": "react",
    "proud_chest_up": "joy",
}


def _position_for(character_id: str, zone: str) -> tuple[float, float]:
    if zone == "center_floor":
        return (0.30, 0.65) if character_id == "char_milo" else (0.70, 0.65)
    if zone == "left_workbench":
        return (0.33, 0.65)
    if zone == "right_shelf":
        return (0.78, 0.65)
    if zone == "entrance":
        return (0.16, 0.65) if character_id == "char_milo" else (0.84, 0.65)
    return (0.5, 0.65)


def _character_state(
    character_id: str,
    position: tuple[float, float],
    *,
    action: str = "idle",
    emotion: str = "neutral",
    rotation: float = 0.0,
) -> dict[str, Any]:
    return {
        "character_id": character_id,
        "position": {"x": position[0], "y": position[1]},
        "rotation": rotation,
        "root_velocity": {"x": 0.0, "y": 0.0},
        "scale": 1.0,
        "emotion": _lab_emotion(emotion),
        "gaze_target": None,
        "current_action": action,
        "pose_id": None,
        "visibility": "visible",
    }


def _lab_emotion(emotion: str) -> str:
    mapping = {
        "curious": "curious",
        "happy": "happy",
        "surprised": "surprised",
        "sad": "sad",
        "angry": "angry",
        "afraid": "afraid",
        "focused": "neutral",
        "neutral": "neutral",
    }
    return mapping.get(emotion, "neutral")


def _emotion_for_action(action: dict[str, Any]) -> str:
    return str(action.get("emotion") or "neutral")


def _actor_initial(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    scene = project["scenes"][0]
    return {actor["character_id"]: actor for actor in scene["initial_state"]["actors"]}


def _trajectory_for(
    profile: str,
    start_pos: tuple[float, float],
    end_pos: tuple[float, float],
    *,
    moving: bool,
) -> dict[str, Any]:
    if not moving:
        return {"kind": "hold", "control_points": [], "easing": "linear", "max_speed": 0.5}
    sx, sy = start_pos
    ex, ey = end_pos
    profile = profile if profile in {"linear", "bezier3", "waypoints", "arc"} else "linear"
    if profile == "bezier3":
        controls = [
            {"x": round(sx + (ex - sx) * 0.30, 4), "y": round(max(0.05, sy - 0.055), 4)},
            {"x": round(sx + (ex - sx) * 0.70, 4), "y": round(max(0.05, ey - 0.055), 4)},
        ]
    elif profile == "waypoints":
        controls = [
            {"x": round(sx + (ex - sx) * 0.33, 4), "y": round(max(0.05, sy - 0.040), 4)},
            {"x": round(sx + (ex - sx) * 0.66, 4), "y": round(max(0.05, ey - 0.070), 4)},
        ]
    elif profile == "arc":
        controls = [{"x": round((sx + ex) / 2, 4), "y": round(max(0.05, min(sy, ey) - 0.10), 4)}]
    else:
        controls = []
    return {
        "kind": profile,
        "control_points": controls,
        "easing": "ease_in_out",
        "max_speed": 0.5,
    }


def build_lab_scene(
    project: dict[str, Any],
    *,
    motion_profile: str = "linear",
) -> dict[str, Any]:
    """Translate a validated Story Studio Project into a Lab SceneDescription."""
    scene = project["scenes"][0]
    actors = _actor_initial(project)
    duration = float(scene["end_time"])
    characters: dict[str, Any] = {}
    for character in project["characters"]:
        actor = actors[character["id"]]
        pos = (float(actor["position"]["x"]), float(actor["position"]["y"]))
        characters[character["id"]] = _character_state(
            character["id"],
            pos,
            emotion=str(actor.get("emotion") or "neutral"),
        )

    props: dict[str, Any] = {}
    for prop in scene["initial_state"]["props"]:
        props[prop["prop_id"]] = {
            "prop_id": prop["prop_id"],
            "position": {
                "x": float(prop["position"]["x"]),
                "y": float(prop["position"]["y"]),
            },
            "rotation": 0.0,
            "owner_id": prop.get("owner_id"),
            "attachment_point": "hand_r" if prop.get("owner_id") else None,
            "velocity": {"x": 0.0, "y": 0.0},
            "state": "free" if not prop.get("owner_id") else "attached",
            "glowing": prop["prop_id"] == "prop_orb",
        }

    zones: dict[str, str] = {
        actor["character_id"]: str(actor["zone"]) for actor in scene["initial_state"]["actors"]
    }
    actions = sorted(scene["actions"], key=lambda item: (float(item["start_time"]), item["id"]))
    segments: list[dict[str, Any]] = []
    for action in actions:
        actor_id = action["actor_id"]
        start = float(action["start_time"])
        end = float(action["end_time"])
        story_type = action["action_type"]
        lab_type = STORY_TO_LAB_ACTION.get(story_type)
        if lab_type is None:
            raise IntegrationError(
                f"Действие Story Studio «{story_type}» не имеет соответствия в Animation Lab."
            )
        start_pos = _position_for(actor_id, zones.get(actor_id, "center_floor"))
        end_pos = start_pos
        params = action.get("parameters") or {}
        if story_type in {"walk", "run"}:
            target_zone = str(params.get("to_zone") or zones.get(actor_id, "center_floor"))
            end_pos = _position_for(actor_id, target_zone)
            zones[actor_id] = target_zone
        elif story_type == "enter":
            target_zone = str(params.get("entry_zone") or "center_floor")
            end_pos = _position_for(actor_id, target_zone)
            zones[actor_id] = target_zone
        elif story_type == "exit":
            target_zone = str(params.get("exit_zone") or "entrance")
            end_pos = _position_for(actor_id, "entrance")
            zones[actor_id] = target_zone
        moving = abs(end_pos[0] - start_pos[0]) > 1e-6
        start_state = _character_state(
            actor_id,
            start_pos,
            action=story_type,
            emotion=_emotion_for_action(action),
            rotation=0.0,
        )
        end_state = _character_state(
            actor_id,
            end_pos,
            action=story_type,
            emotion=_emotion_for_action(action),
            rotation=0.0,
        )
        segments.append(
            {
                "id": f"seg_{action['id']}",
                "character_id": actor_id,
                "action_type": lab_type,
                "start_time": start,
                "end_time": end,
                "start_state": start_state,
                "end_state": end_state,
                "trajectory": _trajectory_for(motion_profile, start_pos, end_pos, moving=moving),
                "easing": "ease_in_out",
                "constraints": {
                    "max_speed": 0.5,
                    "max_acceleration": 2.0,
                    "keep_in_bounds": True,
                },
                "dependencies": [],
            }
        )

    # Add idle bridges for temporal gaps.  This makes Lab's strict state machine
    # explicit about transitions instead of relying on a silent resync.
    by_actor: dict[str, list[dict[str, Any]]] = {}
    for segment in segments:
        by_actor.setdefault(segment["character_id"], []).append(segment)
    bridges: list[dict[str, Any]] = []
    for actor_id, actor_segments in by_actor.items():
        actor_segments.sort(key=lambda item: item["start_time"])
        # Lab's reaction state is not allowed to transition directly into
        # speaking.  A tiny idle beat is a faithful, explicit transition and
        # keeps the Factory durations almost unchanged.
        for previous, following in zip(actor_segments, actor_segments[1:]):
            if (
                float(following["start_time"]) - float(previous["end_time"]) <= 0.02
                and previous["action_type"] in {"react", "reach"}
                and following["action_type"] in {"react", "speak"}
            ):
                new_end = round(float(following["start_time"]) - 0.05, 3)
                if new_end > float(previous["start_time"]) + 0.05:
                    previous["end_time"] = new_end
        for previous, following in zip(actor_segments, actor_segments[1:]):
            gap = float(following["start_time"]) - float(previous["end_time"])
            if gap <= 0.02:
                continue
            pos = (
                float(previous["end_state"]["position"]["x"]),
                float(previous["end_state"]["position"]["y"]),
            )
            state = _character_state(actor_id, pos, action="idle", emotion="neutral")
            bridges.append(
                {
                    "id": f"seg_idle_{actor_id}_{len(bridges) + 1}",
                    "character_id": actor_id,
                    "action_type": "idle",
                    "start_time": float(previous["end_time"]),
                    "end_time": float(following["start_time"]),
                    "start_state": state,
                    "end_state": state,
                    "trajectory": {
                        "kind": "hold",
                        "control_points": [],
                        "easing": "linear",
                        "max_speed": 0.5,
                    },
                    "easing": "linear",
                    "constraints": {
                        "max_speed": 0.5,
                        "max_acceleration": 2.0,
                        "keep_in_bounds": True,
                    },
                    "dependencies": [],
                }
            )
    segments.extend(bridges)
    segments.sort(key=lambda item: (float(item["start_time"]), item["id"]))

    interactions: list[dict[str, Any]] = []
    for action in actions:
        params = action.get("parameters") or {}
        if action["action_type"] == "pick_up":
            prop_id = action.get("target_id")
            if not prop_id:
                continue
            interactions.append(
                {
                    "id": f"int_{action['id']}",
                    "kind": "pickup",
                    "time": round(float(action["end_time"]) - 0.1, 3),
                    "actor_id": action["actor_id"],
                    "prop_id": prop_id,
                    "receiver_id": None,
                    "attachment_point": "hand_r",
                    "preconditions": [],
                    "expected_prop_state_after": "attached",
                    "expected_owner_after": action["actor_id"],
                }
            )
        elif action["action_type"] == "give":
            prop_id = params.get("prop_id")
            receiver_id = params.get("receiver_id") or action.get("target_id")
            if not prop_id or not receiver_id:
                continue
            interactions.append(
                {
                    "id": f"int_{action['id']}",
                    "kind": "transfer",
                    "time": round(
                        float(action["start_time"])
                        + 0.25 * (float(action["end_time"]) - float(action["start_time"])),
                        3,
                    ),
                    "actor_id": action["actor_id"],
                    "prop_id": prop_id,
                    "receiver_id": receiver_id,
                    "attachment_point": "hand_r",
                    "preconditions": [],
                    "expected_prop_state_after": "attached",
                    "expected_owner_after": receiver_id,
                }
            )
        elif action["action_type"] == "put_down":
            prop_id = action.get("target_id")
            if prop_id:
                interactions.append(
                    {
                        "id": f"int_{action['id']}",
                        "kind": "detach",
                        "time": round(float(action["end_time"]) - 0.1, 3),
                        "actor_id": action["actor_id"],
                        "prop_id": prop_id,
                        "receiver_id": None,
                        "attachment_point": "hand_r",
                        "preconditions": [],
                        "expected_prop_state_after": "released",
                        "expected_owner_after": None,
                    }
                )
    interactions.sort(key=lambda item: (float(item["time"]), item["id"]))

    return {
        "schema_version": "1.0.0",
        "name": f"Integrated: {project['title']}",
        "fps": int(project.get("target_fps", 30)),
        "duration_seconds": duration,
        "characters": characters,
        "props": props,
        "motion_segments": segments,
        "interactions": interactions,
        "poses": [],
    }


@dataclass
class MotionResult:
    lab_scene: dict[str, Any]
    plan: dict[str, Any]
    validation: dict[str, Any]
    lab_scene_path: Path
    plan_path: Path
    validation_path: Path
    build_log: dict[str, Any]


class AnimationPlanAdapter:
    """Owns execution of the Animation Lab worker."""

    def __init__(self, paths: IntegrationPaths):
        self.paths = paths
        self.worker = Path(__file__).parent / "workers" / "animation_lab_worker.py"

    def build(
        self,
        project: dict[str, Any],
        out_dir: Path,
        *,
        motion_profile: str = "linear",
    ) -> MotionResult:
        scene = build_lab_scene(project, motion_profile=motion_profile)
        scene_path = write_json(out_dir / "lab_scene.json", scene)
        payload = {
            "lab_src": str(self.paths.animation_lab_root / "src"),
            "scene": scene,
        }
        result = run_json_worker(
            self.paths.lab_python,
            self.worker,
            payload,
            cwd=self.paths.animation_lab_root,
            timeout=180,
        )
        plan = result["plan"]
        validation = result["validation"]
        plan_path = write_json(out_dir / "motion_plan.json", plan)
        validation_path = write_json(out_dir / "validation.json", validation)
        write_json(out_dir / "lab_scene.json", scene)
        return MotionResult(
            lab_scene=scene,
            plan=plan,
            validation=validation,
            lab_scene_path=scene_path,
            plan_path=plan_path,
            validation_path=validation_path,
            build_log=result.get("build_log", {}),
        )


def _find_interaction(
    plan: dict[str, Any], action: dict[str, Any], kinds: set[str]
) -> dict[str, Any] | None:
    for interaction in plan.get("interactions", []):
        if (
            interaction.get("kind") in kinds
            and interaction.get("actor_id") == action["actor_id"]
            and interaction.get("prop_id") in {None, action.get("target_id"), (action.get("parameters") or {}).get("prop_id")}
        ):
            return interaction
    return None


def _dialogue_text(project: dict[str, Any], dialogue_id: str) -> str:
    for scene in project["scenes"]:
        for line in scene.get("dialogue", []):
            if line["id"] == dialogue_id:
                return str(line["text"])
    raise IntegrationError(f"Реплика {dialogue_id} не найдена в Project.")


def build_factory_storyboard(
    project: dict[str, Any],
    plan: dict[str, Any],
    *,
    seed: int = 20261026,
) -> dict[str, Any]:
    """Project the validated Lab plan onto Factory storyboard version 1."""
    scene = project["scenes"][0]
    segments = {segment["id"]: segment for segment in plan["motion_segments"]}
    beats: list[dict[str, Any]] = []
    gesture_index = 0
    for action in sorted(scene["actions"], key=lambda item: (float(item["start_time"]), item["id"])):
        segment = segments.get(f"seg_{action['id']}")
        if segment is None:
            raise IntegrationError(f"Lab не вернул сегмент для действия {action['id']}.")
        story_type = action["action_type"]
        params = action.get("parameters") or {}
        start = float(segment["start_time"])
        duration = float(segment["end_time"]) - start
        if duration <= 0:
            continue
        beat: dict[str, Any] = {
            "id": f"b_{action['id']}",
            "actor": _factory_character_id(project, action["actor_id"]),
            "do": LAB_TO_FACTORY_VERB.get(story_type),
            "start": round(start, 3),
            "duration": round(duration, 3),
            "intensity": _intensity(params),
        }
        if beat["do"] is None:
            raise IntegrationError(
                f"Действие {story_type} не поддерживается рендерером ShortsFactory."
            )
        if story_type in {"walk", "run", "enter", "exit"}:
            beat["x"] = round(float(segment["end_state"]["position"]["x"]), 4)
            beat["cycles"] = max(1.0, round(duration * (2.4 if story_type == "run" else 1.6), 2))
            beat["stride"] = 0.82 if story_type == "run" else 0.55
            beat["path"] = path_from_lab_segment(
                segment["start_state"]["position"],
                segment["end_state"]["position"],
                segment.get("trajectory"),
            )
        if story_type == "turn":
            beat["facing"] = int(params.get("to_facing", -1))
        if story_type in {"pick_up", "give", "receive", "put_down"}:
            object_id = params.get("prop_id") or action.get("target_id")
            if object_id:
                beat["object"] = _factory_object_id(project, object_id)
        if story_type == "receive":
            # Nina faces the giver; her left hand is the one on the giver's side.
            beat["point"] = "hand_l"
        if story_type == "pick_up":
            interaction = _find_interaction(plan, action, {"pickup", "attach"})
            beat["attach_at"] = (
                max(0.1, min(0.95, (float(interaction["time"]) - start) / duration))
                if interaction
                else 0.75
            )
        if story_type == "give":
            interaction = _find_interaction(plan, action, {"transfer", "handoff"})
            receiver = params.get("receiver_id") or action.get("target_id")
            if receiver:
                beat["to"] = _factory_character_id(project, receiver)
            beat["transfer_at"] = (
                max(0.1, min(0.95, (float(interaction["time"]) - start) / duration))
                if interaction
                else 0.6
            )
        if story_type in {"react", "clap", "laugh", "cry"}:
            style = str(params.get("reaction_style") or "")
            beat["do"] = _REACTIONS.get(style, "react")
        if story_type == "speak":
            dialogue_id = params.get("dialogue_id")
            if not dialogue_id:
                raise IntegrationError(f"Действие {action['id']} не содержит dialogue_id.")
            beat["text"] = _dialogue_text(project, str(dialogue_id))
            beat["do"] = "say"
        beats.append(beat)
        if story_type == "speak":
            # Factory's `say` is a voice channel, not a body action.  Add a
            # matching gesture so the mouth/body picture changes during speech.
            # Vary it between consecutive lines; otherwise two lines with the
            # same wave pose can produce an almost frozen interval.
            gesture = ("clap", "joy", "wave")[gesture_index % 3]
            gesture_index += 1
            beats.append(
                {
                    "id": f"g_{action['id']}",
                    "actor": beat["actor"],
                    "do": gesture,
                    "start": round(start, 3),
                    "duration": round(duration, 3),
                    "intensity": 1.0,
                }
            )

    if not beats:
        raise IntegrationError("Storyboard не содержит ни одного действия.")

    story_characters = {character["id"]: character for character in project["characters"]}
    characters = []
    for character_id, state in plan["characters"].items():
        story_character = story_characters.get(character_id)
        if story_character is None:
            raise IntegrationError(f"Lab вернул неизвестного персонажа {character_id}.")
        look = story_character.get("look") if "look" in story_character else None
        if not look:
            look = next(
                (item["look"] for item in CHARACTERS.values() if item["id"] == character_id),
                None,
            )
        if look not in {"milo", "nina"}:
            raise IntegrationError(f"Нет художественного образа Factory для {character_id}.")
        characters.append(
            {
                "id": character_id.removeprefix("char_"),
                "name": story_character["name"],
                "look": look,
                "x": round(float(state["position"]["x"]), 4),
                "y": 1.0,
                "scale": 0.85 if look == "milo" else 0.84,
                "facing": 1 if look == "milo" else -1,
                "z": 20 if look == "milo" else 22,
            }
        )

    objects = []
    for prop in scene["initial_state"]["props"]:
        lab_prop = plan["props"].get(prop["prop_id"])
        if lab_prop is None:
            continue
        story_prop = next((p for p in project.get("props", []) if p["id"] == prop["prop_id"]), None)
        objects.append(
            {
                "id": prop["prop_id"].removeprefix("prop_"),
                "kind": (story_prop or {}).get("factory_kind", "orb"),
                "z": 15,
                "appear_at": 0.0,
                "color": (story_prop or {}).get("color", "#ffd257"),
                "keyframes": [
                    {
                        "t": 0.0,
                        "x": round(float(lab_prop["position"]["x"]), 4),
                        "y": 0.60,
                        "scale": 1.0,
                        "rotation": 0.0,
                    },
                    {
                        "t": float(plan["duration_seconds"]),
                        "x": round(float(lab_prop["position"]["x"]), 4),
                        "y": 0.60,
                        "scale": 1.0,
                        "rotation": 0.0,
                    },
                ],
            }
        )

    duration = float(plan["duration_seconds"])
    uniform_times = [duration * 0.12, duration * 0.34, duration * 0.56, duration * 0.78]
    speech_times = [
        round(float(beat["start"]), 2)
        for beat in beats
        if beat.get("do") == "say"
    ]
    exit_times = [
        round(float(beat["start"]), 2)
        for beat in beats
        if beat.get("do") in {"exit", "walk_to"} and beat.get("x") in {0.16, 0.84}
    ]
    review_times = sorted(
        {
            round(t, 2)
            for t in (
                *uniform_times,
                *[round(float(item["time"]), 3) for item in plan.get("interactions", [])],
                *(speech_times[:1] + speech_times[-1:]),
                *(exit_times[:1]),
            )
            if 0.0 <= t < duration
        }
    )[:9]
    return {
        "version": 1,
        "scene": {
            "id": "integrated_handoff",
            "title": project["title"],
            "duration": float(plan["duration_seconds"]),
            "fps": int(project.get("target_fps", 30)),
            "width": 1080,
            "height": 1920,
            "seed": seed,
            "blend": 0.16,
            "environment": "room",
            "background": {"table_x": 0.5, "table_y": 0.66},
        },
        "characters": characters,
        "objects": objects,
        "beats": beats,
        "camera": [
            {"t": 0.0, "x": 0.5, "y": 0.52, "zoom": 1.02},
            {"t": round(float(plan["duration_seconds"]) / 2, 2), "x": 0.5, "y": 0.52, "zoom": 1.02},
            {"t": float(plan["duration_seconds"]), "x": 0.5, "y": 0.52, "zoom": 1.02},
        ],
        "blink_times": [
            round(t, 1)
            for t in (1.1, 2.8, 4.4, 6.0, 7.4, 8.6)
            if t < float(plan["duration_seconds"])
        ],
        "contact_times": review_times,
        "voice": {"lang": "ru", "gain": 0.9},
    }


def _intensity(params: dict[str, Any]) -> float:
    value = params.get("intensity", 0.9)
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    mapping = {"glance": 0.4, "focused": 0.75, "wide_eyed": 0.95, "squint": 0.55}
    return mapping.get(str(value), 0.9)


def _factory_character_id(project: dict[str, Any], story_id: str) -> str:
    character = next((item for item in project["characters"] if item["id"] == story_id), None)
    if character is None:
        raise IntegrationError(f"Story Studio ссылается на неизвестного персонажа {story_id}.")
    look = character.get("look")
    if look not in {"milo", "nina"}:
        if story_id == "char_milo":
            return "milo"
        if story_id == "char_nina":
            return "nina"
        raise IntegrationError(f"Нет Factory-идентификатора для {story_id}.")
    return look


def _factory_object_id(project: dict[str, Any], story_id: str) -> str:
    prop = next((item for item in project.get("props", []) if item["id"] == story_id), None)
    if prop is None:
        raise IntegrationError(f"Story Studio ссылается на неизвестный предмет {story_id}.")
    return story_id.removeprefix("prop_")
