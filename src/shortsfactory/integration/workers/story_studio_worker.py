"""Standalone Story Studio worker.

Runs with the Story Studio Python environment (or any Python with pydantic).
Receives a JSON story spec on stdin, returns a validated Story Studio Project
and its validation summary on stdout.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def main() -> int:
    payload = json.load(sys.stdin)
    sys.path.insert(0, payload["story_src"])
    try:
        from story_studio.models import (  # type: ignore
            Action,
            ActionType,
            ActorSceneState,
            CameraPlan,
            Character,
            CharacterAppearance,
            DialogueLine,
            FramingGuide,
            GlobalAudioTrack,
            Location,
            NormalizedPoint,
            Project,
            Prop,
            PropSceneState,
            Scene,
            SceneWorldState,
            ValidationSummary,
            VoiceProfile,
        )
        from story_studio.validation import validate_project  # type: ignore
    except Exception as exc:  # pragma: no cover - environment failure
        print(f"Story Studio import failed: {exc}", file=sys.stderr)
        return 3

    spec = payload["spec"]
    try:
        characters = []
        for item in spec["characters"]:
            appearance = CharacterAppearance(
                silhouette=item["appearance"]["silhouette"],
                color_palette=item["appearance"]["color_palette"],
                clothing=item["appearance"]["clothing"],
                distinctive_features=item["appearance"].get("distinctive_features", []),
                facial_structure=item["appearance"].get("facial_structure", "мягкие черты"),
            )
            voice = VoiceProfile(
                timbre=item["voice_profile"]["timbre"],
                pace=item["voice_profile"].get("pace", "medium"),
                pitch=item["voice_profile"].get("pitch", "medium"),
                speech_quirks=item["voice_profile"].get("speech_quirks", []),
            )
            characters.append(
                Character(
                    id=item["id"],
                    name=item["name"],
                    role=item["role"],
                    appearance=appearance,
                    personality=item["personality"],
                    default_emotion=item["default_emotion"],
                    voice_profile=voice,
                    movement_style=item["movement_style"],
                    height_relative=item.get("height_relative", 1.0),
                    allowed_actions=list(ActionType),
                    visual_consistency_rules=[
                        "Сохранять цветовую палитру и силуэт.",
                        "Не менять одежду между сценами.",
                    ],
                )
            )

        location_spec = spec["location"]
        location = Location(
            id=location_spec["id"],
            name=location_spec["name"],
            description=location_spec["description"],
            lighting=location_spec["lighting"],
            atmosphere=location_spec["atmosphere"],
            spatial_zones=location_spec["spatial_zones"],
            prop_ids=location_spec.get("prop_ids", []),
        )

        props = []
        if spec.get("prop"):
            p = spec["prop"]
            props.append(
                Prop(
                    id=p["id"],
                    name=p["name"],
                    description=p["description"],
                    visual_attributes=p.get("visual_attributes", []),
                    interactive=p.get("interactive", True),
                    initial_location_id=p["initial_location_id"],
                    initial_owner_id=p.get("initial_owner_id"),
                    initial_state=p.get("initial_state", "resting"),
                )
            )

        actions = []
        for item in spec["actions"]:
            start = float(item["start_time"])
            end = float(item["end_time"])
            actions.append(
                Action(
                    id=item["id"],
                    actor_id=item["actor_id"],
                    action_type=ActionType(item["action_type"]),
                    start_time=start,
                    end_time=end,
                    duration=round(end - start, 3),
                    parameters=item.get("parameters", {}),
                    target_id=item.get("target_id"),
                    preconditions=item.get("preconditions", []),
                    dependencies=item.get("dependencies", []),
                    emotion=item.get("emotion", "focused"),
                    gaze_target=item.get("gaze_target", "forward"),
                    completion_condition=item.get("completion_condition", "action completed"),
                )
            )

        dialogue = []
        for item in spec.get("dialogue", []):
            dialogue.append(
                DialogueLine(
                    id=item["id"],
                    character_id=item["character_id"],
                    text=item["text"],
                    language=item.get("language", "ru"),
                    start_time=float(item["start_time"]),
                    end_time=float(item["end_time"]),
                    delivery=item.get("delivery", "warm"),
                    emotion=item.get("emotion", "happy"),
                    emphasis=item.get("emphasis", []),
                    pause_after=item.get("pause_after", 0.15),
                    expected_duration=max(0.1, float(item["end_time"]) - float(item["start_time"])),
                )
            )

        initial = SceneWorldState(**spec["initial_state"])
        ending = SceneWorldState(**spec["ending_state"])
        subject_positions = {
            actor.character_id: NormalizedPoint(x=actor.position.x, y=actor.position.y)
            for actor in initial.actors
        }
        framing = FramingGuide(
            aspect_ratio="9:16",
            subject_ids=list(subject_positions),
            subject_positions=subject_positions,
            headroom_note="Оба персонажа в вертикальном кадре, головы ниже верхней безопасной зоны.",
        )
        camera = CameraPlan(
            id="cam_main_1",
            shot_type="two-shot",
            framing=framing,
            camera_motion="static",
            focus_target=characters[0].id,
            start_time=0.0,
            end_time=float(spec["duration"]),
            transition_in="fade_in",
            transition_out="fade_out",
            composition_notes="Общий вертикальный план мастерской, оба персонажа в безопасных полях.",
        )
        scene = Scene(
            id="scene_handoff_1",
            title=spec["title"],
            order=1,
            location_id=location.id,
            start_time=0.0,
            end_time=float(spec["duration"]),
            duration=float(spec["duration"]),
            dramatic_purpose="Показать находку, передачу и благодарность в одной непрерывной сцене.",
            initial_state=initial,
            actions=actions,
            dialogue=dialogue,
            camera_plan=[camera],
            audio_events=[],
            ending_state=ending,
            continuity_requirements=[
                "Шар остаётся в руках владельца при передаче.",
                "Персонажи не телепортируются между зонами.",
            ],
        )
        project = Project(
            project_id="proj_handoff_story",
            title=spec["title"],
            logline=spec["logline"][:200],
            synopsis=spec["synopsis"],
            original_idea=spec["synopsis"],
            language=spec.get("language", "ru"),
            target_duration_seconds=float(spec["duration"]),
            target_fps=30,
            resolution="1080x1920",
            aspect_ratio="9:16",
            genre=spec.get("genre", "drama"),
            tone="тёплый",
            target_audience="подростки и взрослые",
            is_valid_draft=True,
            characters=characters,
            locations=[location],
            props=props,
            scenes=[scene],
            global_audio=GlobalAudioTrack(
                music_style="тёплая акустическая тема",
                tempo_bpm=96,
                ambient_bed="тихий гул мастерской",
                dynamic_arc="мягкий подъём к благодарности",
                loudness_target_lufs=-16,
            ),
            validation_summary=ValidationSummary(
                is_valid=True,
                total_scenes=1,
                total_actions=len(actions),
                total_dialogue_lines=len(dialogue),
                total_duration_seconds=float(spec["duration"]),
                error_count=0,
                warning_count=0,
                info_count=0,
                issues=[],
            ),
        )
        summary = validate_project(project)
        result = {
            "project": project.model_dump(mode="json"),
            "validation": summary.model_dump(mode="json"),
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
