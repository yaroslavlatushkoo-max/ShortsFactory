"""Structured scene description -> executable animation timeline.

This is the first honest bridge between "what happens in the scene" and the
rig. It is deliberately *not* an AI showrunner: the input is a small typed
JSON document with explicit beats, and every verb maps to one implemented
action. Unknown verbs fail with a Russian explanation instead of being
silently dropped.

Example beat::

    {"id": "m_take", "actor": "milo", "do": "take", "object": "orb",
     "duration": 1.2, "after": ["m_stop"]}

Compilation produces a normal ``version: 2`` scene, which can be inspected,
edited by hand and rendered with ``animate --scene``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .motion import UNSUPPORTED
from .paths import normalize_path
from .rig import RIG_POINTS

SUPPORTED_STORYBOARD_VERSION = 1

#: Verbs accepted in a storyboard and the action they compile to.
VERB_ACTIONS: dict[str, str] = {
    "idle": "idle",
    "walk_to": "walk",
    "run_to": "run",
    "enter": "enter",
    "exit": "exit",
    "stop": "stop",
    "turn_to": "turn",
    "reach": "reach",
    "take": "reach",
    "offer": "offer",
    "give": "offer",
    "carry": "carry",
    "drop": "offer",
    "point": "point",
    "wave": "wave",
    "react": "react",
    "surprise": "surprise",
    "joy": "joy",
    "confused": "confused",
    "annoyed": "annoyed",
    "jump": "jump",
    "land": "land",
    "crouch": "crouch",
    "sit": "sit",
    "stand": "stand",
    "pickup": "pickup",
    "putdown": "putdown",
    "look_around": "look_around",
    "shrug": "shrug",
    "clap": "clap",
    "laugh": "laugh",
    "cry": "cry",
    "stumble": "stumble",
    "dodge": "dodge",
    "dance": "dance",
    "fight": "fight",
}

#: Storyboard-only verbs that create voice lines instead of body actions.
VOICE_VERBS = ("say",)


class StoryboardError(ValueError):
    """Raised when a storyboard cannot be compiled into a valid scene."""


def _require(value: Any, message: str) -> Any:
    if value is None or value == "":
        raise StoryboardError(message)
    return value


def _beat_start(
    beat: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    resolved: dict[str, dict[str, Any]],
    visiting: set[str],
) -> tuple[float, float]:
    """Resolve a beat start from its ``after`` list; returns (start, end)."""
    beat_id = str(beat.get("id", ""))
    if beat_id in resolved:
        entry = resolved[beat_id]
        return float(entry["start"]), float(entry["end"])
    if beat_id in visiting:
        raise StoryboardError(f"Циклическая зависимость битов вокруг «{beat_id}».")
    visiting.add(beat_id)
    duration = float(beat.get("duration", 0.0) or 0.0)
    after = [str(item) for item in (beat.get("after") or [])]
    if after:
        ends = []
        for dependency in after:
            target = by_id.get(dependency)
            if target is None:
                raise StoryboardError(
                    f"Бит «{beat_id}» ссылается на неизвестный бит «{dependency}»."
                )
            ends.append(_beat_start(target, by_id, resolved, visiting)[1])
        start = max(ends) + float(beat.get("gap", 0.05))
    else:
        start = float(beat.get("start", 0.0) or 0.0)
    if duration < 0:
        raise StoryboardError(f"Бит «{beat_id}» имеет отрицательную длительность.")
    end = start + duration
    visiting.discard(beat_id)
    resolved[beat_id] = {"start": start, "end": end}
    return start, end


def compile_storyboard(data: dict[str, Any]) -> dict[str, Any]:
    """Compile a structured storyboard into a validated ``AnimationScene`` dict."""
    if not isinstance(data, dict):
        raise StoryboardError("Сториборд должен быть объектом JSON.")
    version = int(data.get("version", SUPPORTED_STORYBOARD_VERSION) or 0)
    if version != SUPPORTED_STORYBOARD_VERSION:
        raise StoryboardError(
            f"Неподдерживаемая версия сториборда: {version} "
            f"(ожидается {SUPPORTED_STORYBOARD_VERSION})."
        )

    scene_spec = dict(data.get("scene") or {})
    if not scene_spec.get("id"):
        raise StoryboardError("В блоке scene не задан id.")
    duration = float(scene_spec.get("duration", 0.0) or 0.0)
    if duration <= 0:
        raise StoryboardError("В блоке scene не задана положительная duration.")

    characters = list(data.get("characters") or [])
    objects = list(data.get("objects") or [])
    if not characters:
        raise StoryboardError("В сториборде нет персонажей.")
    character_ids = {str(item.get("id", "")) for item in characters}
    object_ids = {str(item.get("id", "")) for item in objects}

    beats = list(data.get("beats") or [])
    if not beats:
        raise StoryboardError("В сториборде нет битов (beats).")
    by_id: dict[str, dict[str, Any]] = {}
    for beat in beats:
        beat_id = str(beat.get("id", ""))
        if not beat_id:
            raise StoryboardError("Бит без id.")
        if beat_id in by_id:
            raise StoryboardError(f"Повторяющийся id бита: {beat_id}.")
        by_id[beat_id] = beat

    resolved: dict[str, dict[str, Any]] = {}
    starts: dict[str, tuple[float, float]] = {}
    for beat in beats:
        starts[str(beat["id"])] = _beat_start(beat, by_id, resolved, set())

    actions: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    voice_lines: list[dict[str, Any]] = []

    for beat in beats:
        beat_id = str(beat["id"])
        verb = str(beat.get("do", ""))
        start, end = starts[beat_id]
        actor = str(beat.get("actor", ""))
        if verb in VOICE_VERBS:
            if actor not in character_ids:
                raise StoryboardError(f"Бит «{beat_id}»: неизвестный персонаж «{actor}».")
            text = str(beat.get("text", "")).strip()
            if not text:
                raise StoryboardError(f"Бит «{beat_id}»: пустая реплика.")
            voice_lines.append({"character": actor, "text": text, "start": round(start, 3)})
            continue
        if verb not in VERB_ACTIONS:
            supported = ", ".join(sorted(list(VERB_ACTIONS) + list(VOICE_VERBS)))
            if verb in UNSUPPORTED:
                raise StoryboardError(
                    f"Бит «{beat_id}»: действие «{verb}» не поддерживается — {UNSUPPORTED[verb]}."
                )
            raise StoryboardError(
                f"Бит «{beat_id}»: неизвестное действие «{verb}». "
                f"Поддерживаются: {supported}."
            )
        if actor not in character_ids:
            raise StoryboardError(f"Бит «{beat_id}»: неизвестный персонаж «{actor}».")
        if end > duration + 1e-6:
            raise StoryboardError(
                f"Бит «{beat_id}» выходит за границу сцены ({end:.2f} > {duration:.2f})."
            )
        duration_beat = float(beat.get("duration", 0.0) or 0.0)
        if duration_beat <= 0:
            raise StoryboardError(f"Бит «{beat_id}»: длительность должна быть больше нуля.")

        params: dict[str, Any] = {"intensity": float(beat.get("intensity", 1.0))}
        action_name = VERB_ACTIONS[verb]
        if action_name in {"walk", "run", "enter", "exit"}:
            if "x" not in beat:
                raise StoryboardError(f"Бит «{beat_id}»: {verb} требует координату x.")
            params["to_x"] = float(beat["x"])
            default_cycles = 2.4 if action_name == "run" else 1.6
            params["cycles"] = float(beat.get("cycles", max(1.0, duration_beat * default_cycles)))
            params["stride"] = float(beat.get("stride", 0.82 if action_name == "run" else 0.55))
            if beat.get("path"):
                path = normalize_path(beat["path"])
                end_point = path["points"][-1]
                if abs(end_point["x"] - params["to_x"]) > 1e-3:
                    raise StoryboardError(
                        f"Бит «{beat_id}»: конечная точка path.x={end_point['x']:.4f} "
                        f"не совпадает с x={params['to_x']:.4f}."
                    )
                params["path"] = path
        elif beat.get("path"):
            raise StoryboardError(
                f"Бит «{beat_id}»: path поддерживается только для walk_to/run_to/enter/exit."
            )
        if action_name == "turn":
            params["to_facing"] = int(beat.get("facing", -1))
        if verb in ("point", "reach", "take", "give", "drop") and beat.get("object"):
            if str(beat["object"]) not in object_ids:
                raise StoryboardError(
                    f"Бит «{beat_id}»: неизвестный объект «{beat['object']}»."
                )
            params["object"] = str(beat["object"])

        actions.append(
            {
                "id": beat_id,
                "character": actor,
                "action": action_name,
                "start": round(start, 3),
                "duration": round(duration_beat, 3),
                "params": params,
                "after": [str(item) for item in (beat.get("after") or [])],
                "gap": float(beat.get("gap", 0.05)),
            }
        )

        point = str(beat.get("point", "hand_r"))
        if point not in RIG_POINTS:
            raise StoryboardError(
                f"Бит «{beat_id}»: неизвестная точка рига «{point}»."
            )
        offset = beat.get("offset") or [0.0, 0.015]
        if verb == "take":
            object_id = str(beat.get("object", ""))
            if not object_id:
                raise StoryboardError(f"Бит «{beat_id}»: take требует object.")
            attach_at = start + duration_beat * float(beat.get("attach_at", 0.80))
            events.append(
                {
                    "t": round(attach_at, 3),
                    "kind": "attach",
                    "object": object_id,
                    "character": actor,
                    "point": point,
                    "offset": [float(offset[0]), float(offset[1])],
                    "rotation": float(beat.get("rotation", 0.0)),
                    "scale": float(beat.get("scale", 0.0)),
                }
            )
        elif verb == "give":
            object_id = str(beat.get("object", ""))
            target = str(beat.get("to", ""))
            if not object_id:
                raise StoryboardError(f"Бит «{beat_id}»: give требует object.")
            if target not in character_ids:
                raise StoryboardError(f"Бит «{beat_id}»: неизвестный получатель «{target}».")
            events.append(
                {
                    "t": round(start + duration_beat * float(beat.get("transfer_at", 0.90)), 3),
                    "kind": "transfer",
                    "object": object_id,
                    "character": actor,
                    "target": target,
                    "point": point,
                    "offset": [float(offset[0]), float(offset[1])],
                    "rotation": float(beat.get("rotation", 0.0)),
                }
            )
        elif verb == "drop":
            object_id = str(beat.get("object", ""))
            if not object_id:
                raise StoryboardError(f"Бит «{beat_id}»: drop требует object.")
            velocity = beat.get("velocity") or [0.0, 0.0]
            events.append(
                {
                    "t": round(start + duration_beat * float(beat.get("detach_at", 0.30)), 3),
                    "kind": "detach",
                    "object": object_id,
                    "character": actor,
                    "velocity": [float(velocity[0]), float(velocity[1])],
                    "spin": float(beat.get("spin", 0.0)),
                }
            )

    compiled: dict[str, Any] = {
        "version": 2,
        "id": str(scene_spec["id"]),
        "title": str(scene_spec.get("title", "")),
        "fps": int(scene_spec.get("fps", 30)),
        "width": int(scene_spec.get("width", 1080)),
        "height": int(scene_spec.get("height", 1920)),
        "duration": duration,
        "seed": int(scene_spec.get("seed", 20261023)),
        "blend": float(scene_spec.get("blend", 0.18)),
        "background": dict(scene_spec.get("background") or {}),
        "foreground": dict(scene_spec.get("foreground") or {}),
        "environment": str(scene_spec.get("environment", "") or ""),
        "characters": characters,
        "objects": objects,
        "actions": actions,
        "events": events,
        "camera": list(data.get("camera") or []),
        "blink_times": list(data.get("blink_times") or []),
        "contact_times": list(data.get("contact_times") or []),
        "voice": {
            "lang": str((data.get("voice") or {}).get("lang", "ru")),
            "gain": float((data.get("voice") or {}).get("gain", 0.9)),
            "lines": voice_lines,
        },
    }
    if not compiled["camera"]:
        raise StoryboardError("В сториборде не задана камера (camera).")
    for index, key in enumerate(compiled["camera"]):
        animated = float(key.get("t", 0.0))
        if animated < 0 or animated > duration + 1e-6:
            raise StoryboardError(f"Ключевой кадр камеры {index + 1} вне сцены: t={animated}.")
    return compiled


def load_storyboard(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        raise StoryboardError(f"Файл сториборда не найден: {source}")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StoryboardError(f"Не удалось прочитать сториборд {source}: {exc}") from exc
    return compile_storyboard(data)
