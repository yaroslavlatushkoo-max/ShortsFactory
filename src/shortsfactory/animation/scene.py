"""Machine-readable scene description and its validation.

A scene is a plain JSON document: characters, environment, objects, actions,
attachment events and a timeline. ``load_scene`` parses it and rejects anything
the engine cannot actually render, so an invalid scene never silently becomes a
broken video.

Two schema versions are accepted:

* ``version: 1`` — the original Animation Engine v1 scene (no attachment
  events, no action dependencies).
* ``version: 2`` — adds ``events`` (attach/detach/transfer), action ``after``
  dependencies and named character looks.

Coordinate system (shared by every element):

* ``x`` — stage-normalised horizontal position, 0 = left edge, 1 = right edge.
* ``y`` — stage-normalised vertical position, 0 = top, 1 = bottom. For
  characters it marks the ground line where the feet stand.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .environments import environment_bounds, environment_names
from .looks import LookError, look_exists, load_look
from .motion import ACTIONS, POSE_NAMES, UNSUPPORTED, ease, lerp
from .paths import normalize_path
from .rig import DEFAULT_PALETTE, RIG_POINTS, RIGS

SUPPORTED_VERSION = 2
SUPPORTED_VERSIONS = (1, 2)

#: Action names implemented by the engine.
SUPPORTED_ACTIONS: tuple[str, ...] = tuple(sorted(ACTIONS))

#: Action names that exist in the wider world but are *not* implemented here.
UNSUPPORTED_ACTIONS: dict[str, str] = dict(UNSUPPORTED)

_OBJECT_KINDS = ("orb", "crate", "book", "lamp", "gem", "cup", "spring", "confetti",
                 "magnet", "snake", "feather", "clock")
_EVENT_KINDS = ("attach", "detach", "transfer")


class SceneError(ValueError):
    """Raised when a scene description is missing data or uses unsupported features."""


@dataclass
class Character:
    id: str
    name: str = ""
    rig: str = "humanoid_v1"
    look: str = ""
    x: float = 0.5
    y: float = 1.0
    scale: float = 1.0
    facing: int = 1
    z: int = 20
    appear_at: float = 0.0
    palette: dict[str, str] = field(default_factory=dict)

    def colors(self) -> dict[str, str]:
        palette = dict(DEFAULT_PALETTE)
        palette.update(self.palette or {})
        return palette

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SceneObject:
    id: str
    kind: str = "orb"
    z: int = 15
    appear_at: float = 0.0
    color: str = "#ffd257"
    keyframes: list[dict[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ActionItem:
    character: str
    action: str
    start: float
    duration: float
    params: dict[str, Any] = field(default_factory=dict)
    id: str = ""
    after: list[str] = field(default_factory=list)
    gap: float = 0.05

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not self.id:
            data.pop("id", None)
        if not self.after:
            data.pop("after", None)
        return data


@dataclass
class AttachmentEvent:
    """A typed timeline event that changes who holds an object.

    ``attach`` parents the object to a rig point; ``transfer`` moves it from
    ``character`` to ``target`` atomically; ``detach`` releases it and lets it
    continue with a constant velocity (deterministic, no physics).
    """

    t: float
    kind: str
    object: str
    character: str = ""
    target: str = ""
    point: str = "hand_r"
    offset: tuple[float, float] = (0.0, 0.0)
    rotation: float = 0.0
    scale: float = 0.0
    velocity: tuple[float, float] = (0.0, 0.0)
    spin: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["offset"] = list(self.offset)
        data["velocity"] = list(self.velocity)
        return data


@dataclass
class CameraKey:
    t: float
    x: float = 0.5
    y: float = 0.5
    zoom: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VoiceLine:
    character: str
    text: str
    start: float
    voice: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnimationScene:
    id: str
    title: str = ""
    fps: int = 30
    width: int = 1080
    height: int = 1920
    duration: float = 12.0
    version: int = SUPPORTED_VERSION
    seed: int = 20261023
    blend: float = 0.18
    environment: str = ""
    background: dict[str, Any] = field(default_factory=dict)
    foreground: dict[str, Any] = field(default_factory=dict)
    characters: list[Character] = field(default_factory=list)
    objects: list[SceneObject] = field(default_factory=list)
    actions: list[ActionItem] = field(default_factory=list)
    events: list[AttachmentEvent] = field(default_factory=list)
    camera: list[CameraKey] = field(default_factory=list)
    blink_times: list[float] = field(default_factory=list)
    contact_times: list[float] = field(default_factory=list)
    voice_lang: str = "ru"
    voice_gain: float = 0.9
    voice_lines: list[VoiceLine] = field(default_factory=list)
    #: Problems found while resolving ``after`` dependencies (reported once).
    timeline_errors: list[str] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------ helpers
    def character(self, character_id: str) -> Character | None:
        for item in self.characters:
            if item.id == character_id:
                return item
        return None

    def scene_object(self, object_id: str) -> SceneObject | None:
        for item in self.objects:
            if item.id == object_id:
                return item
        return None

    def frame_count(self) -> int:
        return max(1, round(self.duration * self.fps))

    def actions_for(self, character_id: str) -> list[ActionItem]:
        return sorted(
            (item for item in self.actions if item.character == character_id),
            key=lambda item: item.start,
        )

    def events_for(self, object_id: str) -> list[AttachmentEvent]:
        return sorted((item for item in self.events if item.object == object_id), key=lambda item: item.t)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "id": self.id,
            "title": self.title,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "duration": self.duration,
            "seed": self.seed,
            "blend": self.blend,
            "environment": self.environment,
            "background": self.background,
            "foreground": self.foreground,
            "characters": [item.to_dict() for item in self.characters],
            "objects": [item.to_dict() for item in self.objects],
            "actions": [item.to_dict() for item in self.actions],
            "events": [item.to_dict() for item in self.events],
            "camera": [item.to_dict() for item in self.camera],
            "blink_times": list(self.blink_times),
            "contact_times": list(self.contact_times),
            "voice": {
                "lang": self.voice_lang,
                "gain": self.voice_gain,
                "lines": [item.to_dict() for item in self.voice_lines],
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnimationScene":
        if not isinstance(data, dict):
            raise SceneError("Описание сцены должно быть объектом JSON.")
        voice = data.get("voice") or {}
        if not isinstance(voice, dict):
            raise SceneError("Блок voice должен быть объектом JSON.")
        scene = cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            fps=int(data.get("fps", 30) or 0),
            width=int(data.get("width", 1080) or 0),
            height=int(data.get("height", 1920) or 0),
            duration=float(data.get("duration", 0.0) or 0.0),
            version=int(data.get("version", SUPPORTED_VERSION) or 0),
            seed=int(data.get("seed", 20261023) or 0),
            blend=float(data.get("blend", 0.18) or 0.0),
            environment=str(data.get("environment", "") or ""),
            background=dict(data.get("background") or {}),
            foreground=dict(data.get("foreground") or {}),
            blink_times=[float(item) for item in data.get("blink_times", [])],
            contact_times=[float(item) for item in data.get("contact_times", [])],
            voice_lang=str(voice.get("lang", "ru")),
            voice_gain=float(voice.get("gain", 0.9)),
        )
        for item in data.get("characters", []) or []:
            scene.characters.append(
                Character(
                    id=str(item.get("id", "")),
                    name=str(item.get("name", "")),
                    rig=str(item.get("rig", "humanoid_v1")),
                    look=str(item.get("look", "")),
                    x=float(item.get("x", 0.5)),
                    y=float(item.get("y", 1.0)),
                    scale=float(item.get("scale", 1.0)),
                    facing=int(item.get("facing", 1)),
                    z=int(item.get("z", 20)),
                    appear_at=float(item.get("appear_at", 0.0)),
                    palette={str(k): str(v) for k, v in (item.get("palette") or {}).items()},
                )
            )
        for item in data.get("objects", []) or []:
            scene.objects.append(
                SceneObject(
                    id=str(item.get("id", "")),
                    kind=str(item.get("kind", "orb")),
                    z=int(item.get("z", 15)),
                    appear_at=float(item.get("appear_at", 0.0)),
                    color=str(item.get("color", "#ffd257")),
                    keyframes=[
                        {
                            "t": float(frame.get("t", 0.0)),
                            "x": float(frame.get("x", 0.5)),
                            "y": float(frame.get("y", 0.5)),
                            "scale": float(frame.get("scale", 1.0)),
                            "rotation": float(frame.get("rotation", 0.0)),
                        }
                        for frame in (item.get("keyframes") or [])
                    ],
                )
            )
        for item in data.get("actions", []) or []:
            scene.actions.append(
                ActionItem(
                    character=str(item.get("character", "")),
                    action=str(item.get("action", "")),
                    start=float(item.get("start", 0.0)),
                    duration=float(item.get("duration", 0.0)),
                    params=dict(item.get("params") or {}),
                    id=str(item.get("id", "")),
                    after=[str(value) for value in (item.get("after") or [])],
                    gap=float(item.get("gap", 0.05)),
                )
            )
        for item in data.get("events", []) or []:
            offset = item.get("offset") or (0.0, 0.0)
            velocity = item.get("velocity") or (0.0, 0.0)
            scene.events.append(
                AttachmentEvent(
                    t=float(item.get("t", 0.0)),
                    kind=str(item.get("kind", "")),
                    object=str(item.get("object", "")),
                    character=str(item.get("character", "")),
                    target=str(item.get("target", "")),
                    point=str(item.get("point", "hand_r")),
                    offset=(float(offset[0]), float(offset[1])),
                    rotation=float(item.get("rotation", 0.0)),
                    scale=float(item.get("scale", 0.0)),
                    velocity=(float(velocity[0]), float(velocity[1])),
                    spin=float(item.get("spin", 0.0)),
                )
            )
        for item in data.get("camera", []) or []:
            scene.camera.append(
                CameraKey(
                    t=float(item.get("t", 0.0)),
                    x=float(item.get("x", 0.5)),
                    y=float(item.get("y", 0.5)),
                    zoom=float(item.get("zoom", 1.0)),
                )
            )
        for item in voice.get("lines", []) or []:
            scene.voice_lines.append(
                VoiceLine(
                    character=str(item.get("character", "")),
                    text=str(item.get("text", "")),
                    start=float(item.get("start", 0.0)),
                    voice=str(item.get("voice", "")),
                )
            )
        scene.timeline_errors = scene.resolve_dependencies()
        return scene

    # ----------------------------------------------------------- dependencies
    def resolve_dependencies(self) -> list[str]:
        """Resolve ``after`` links into absolute start times (mutates actions)."""
        errors: list[str] = []
        by_id: dict[str, ActionItem] = {}
        for item in self.actions:
            if not item.id:
                continue
            if item.id in by_id:
                errors.append(f"Повторяющийся id действия: {item.id}.")
            by_id[item.id] = item

        resolved: set[str] = set()
        visiting: set[str] = set()

        def resolve(item: ActionItem) -> float:
            if not item.after:
                resolved.add(item.id)
                return item.start
            if item.id in resolved:
                return item.start
            if item.id in visiting:
                errors.append(f"Циклическая зависимость действий вокруг «{item.id}».")
                return item.start
            visiting.add(item.id)
            ends: list[float] = []
            for dependency in item.after:
                target = by_id.get(dependency)
                if target is None:
                    errors.append(
                        f"Действие «{item.action}» у {item.character} ссылается на "
                        f"неизвестное действие «{dependency}»."
                    )
                    continue
                if target is item:
                    errors.append(f"Действие «{item.id}» зависит от самого себя.")
                    continue
                resolve(target)
                ends.append(target.start + target.duration)
            visiting.discard(item.id)
            if ends:
                item.start = max(ends) + max(0.0, item.gap)
            resolved.add(item.id)
            return item.start

        for item in self.actions:
            resolve(item)
        return errors

    # --------------------------------------------------------------- validation
    def validate(self) -> list[str]:
        errors: list[str] = list(self.timeline_errors)
        if self.version not in SUPPORTED_VERSIONS:
            errors.append(
                f"Неподдерживаемая версия сцены: {self.version} "
                f"(поддерживаются {', '.join(str(v) for v in SUPPORTED_VERSIONS)})."
            )
        if not self.id:
            errors.append("Не задан id сцены.")
        if self.fps <= 0:
            errors.append(f"Некорректный fps: {self.fps}.")
        if self.width <= 0 or self.height <= 0:
            errors.append(f"Некорректное разрешение: {self.width}x{self.height}.")
        if self.duration <= 0:
            errors.append(f"Некорректная длительность: {self.duration}.")
        if not self.characters:
            errors.append("В сцене нет персонажей.")
        if self.environment:
            if self.environment not in environment_names():
                errors.append(
                    f"Неизвестное окружение «{self.environment}». "
                    f"Доступны: {', '.join(environment_names())}."
                )
            else:
                low, high = environment_bounds(self.environment)
                for character in self.characters:
                    if not low <= character.x <= high:
                        errors.append(
                            f"Персонаж {character.id} вне границ окружения "
                            f"({character.x:.2f} не в {low:.2f}..{high:.2f})."
                        )

        ids: set[str] = set()
        for character in self.characters:
            if not character.id:
                errors.append("Персонаж без id.")
            elif character.id in ids:
                errors.append(f"Повторяющийся id персонажа: {character.id}.")
            ids.add(character.id)
            if character.rig not in RIGS:
                errors.append(f"Неизвестный риг «{character.rig}» у персонажа {character.id}.")
            if character.look and not look_exists(character.look):
                errors.append(f"Не найден файл внешности «{character.look}» у персонажа {character.id}.")
            elif character.look:
                try:
                    load_look(character.look)
                except LookError as exc:
                    errors.append(f"Внешность персонажа {character.id}: {exc}")
            if character.scale <= 0:
                errors.append(f"Некорректный масштаб у персонажа {character.id}.")
            if not 0.0 <= character.x <= 1.0 or not 0.0 <= character.y <= 1.0:
                errors.append(f"Позиция персонажа {character.id} вне сцены: ({character.x}, {character.y}).")
            if character.facing not in (1, -1):
                errors.append(f"Некорректное направление у персонажа {character.id}: {character.facing}.")
            if character.appear_at < 0 or character.appear_at > self.duration + 1e-6:
                errors.append(f"Некорректное время появления персонажа {character.id}.")

        object_ids: set[str] = set()
        for scene_object in self.objects:
            if not scene_object.id:
                errors.append("Объект без id.")
            elif scene_object.id in object_ids:
                errors.append(f"Повторяющийся id объекта: {scene_object.id}.")
            object_ids.add(scene_object.id)
            if scene_object.kind not in _OBJECT_KINDS:
                errors.append(f"Неизвестный тип объекта «{scene_object.kind}».")
            if not scene_object.keyframes:
                errors.append(f"У объекта {scene_object.id} нет ключевых кадров.")
            times = [frame["t"] for frame in scene_object.keyframes]
            if times != sorted(times):
                errors.append(f"Ключевые кадры объекта {scene_object.id} идут не по времени.")
            for frame in scene_object.keyframes:
                if frame["t"] < -1e-6 or frame["t"] > self.duration + 1e-6:
                    errors.append(f"Ключевой кадр объекта {scene_object.id} вне длительности: t={frame['t']}.")
                if frame["scale"] <= 0:
                    errors.append(f"Некорректный масштаб ключевого кадра объекта {scene_object.id}.")

        action_ids: set[str] = set()
        for action in self.actions:
            if action.character not in ids:
                errors.append(f"Действие ссылается на неизвестного персонажа: {action.character}.")
            if action.id:
                if action.id in action_ids:
                    errors.append(f"Повторяющийся id действия: {action.id}.")
                action_ids.add(action.id)
            if action.after and self.version < 2:
                errors.append(
                    f"Действие «{action.action}» использует after, но это требует version: 2."
                )
            if action.action in UNSUPPORTED_ACTIONS:
                errors.append(
                    f"Действие «{action.action}» не поддерживается: {UNSUPPORTED_ACTIONS[action.action]}."
                )
            elif action.action not in ACTIONS:
                errors.append(f"Неизвестное действие: «{action.action}».")
            if action.duration <= 0:
                errors.append(f"Некорректная длительность действия «{action.action}» у {action.character}.")
            if action.start < -1e-6:
                errors.append(f"Отрицательное время начала действия «{action.action}».")
            if action.start + action.duration > self.duration + 1e-6:
                errors.append(
                    f"Действие «{action.action}» у {action.character} выходит за границу сцены "
                    f"({action.start + action.duration:.2f} > {self.duration:.2f})."
                )
            target_object = action.params.get("object")
            if target_object and target_object not in object_ids:
                errors.append(
                    f"Действие «{action.action}» ссылается на неизвестный объект «{target_object}»."
                )
            pose = action.params.get("pose")
            if pose and pose not in POSE_NAMES:
                errors.append(f"Неизвестная поза «{pose}» в действии «{action.action}».")
            if action.action in {"walk", "run", "enter", "exit"} and "to_x" in action.params:
                value = float(action.params["to_x"])
                low, high = (-0.2, 1.2) if action.action in {"enter", "exit"} else (0.0, 1.0)
                if not low <= value <= high:
                    errors.append(f"Цель движения вне сцены: {value}.")
            raw_path = action.params.get("path")
            if raw_path:
                try:
                    path = normalize_path(raw_path)
                except (TypeError, ValueError) as exc:
                    errors.append(f"Некорректная траектория в действии «{action.action}»: {exc}.")
                else:
                    target = action.params.get("to_x")
                    end_point = path["points"][-1]
                    if target is not None and abs(float(target) - end_point["x"]) > 1e-3:
                        errors.append(
                            f"Конец траектории {end_point['x']:.4f} не совпадает с to_x={float(target):.4f}."
                        )
                    if not 0.0 <= end_point["y"] <= 1.0:
                        errors.append(f"Конец траектории по y вне сцены: {end_point['y']:.4f}.")

        errors.extend(self._validate_events(ids, object_ids))

        if not self.camera:
            errors.append("Не задана камера (хотя бы один ключевой кадр).")
        camera_times = [key.t for key in self.camera]
        if camera_times != sorted(camera_times):
            errors.append("Ключевые кадры камеры идут не по времени.")
        for key in self.camera:
            if key.t < -1e-6 or key.t > self.duration + 1e-6:
                errors.append(f"Ключевой кадр камеры вне длительности: t={key.t}.")
            if key.zoom <= 0:
                errors.append(f"Некорректный зум камеры: {key.zoom}.")

        for moment in self.contact_times:
            if moment < -1e-6 or moment > self.duration + 1e-6:
                errors.append(f"Момент контактного листа вне сцены: t={moment}.")

        if self.voice_lines:
            if self.voice_lang not in ("ru", "en"):
                errors.append(f"Некорректный язык озвучки: {self.voice_lang} (ожидается ru или en).")
            if not 0.0 < self.voice_gain <= 2.0:
                errors.append(f"Некорректное усиление озвучки: {self.voice_gain}.")
            for index, line in enumerate(self.voice_lines, 1):
                if line.character not in ids:
                    errors.append(f"Реплика {index} ссылается на неизвестного персонажа: {line.character}.")
                if not line.text.strip():
                    errors.append(f"Реплика {index} пустая.")
                if line.start < -1e-6 or line.start > self.duration + 1e-6:
                    errors.append(f"Реплика {index} начинается вне сцены: t={line.start}.")
            by_character: dict[str, list[float]] = {}
            for line in self.voice_lines:
                by_character.setdefault(line.character, []).append(line.start)
            for character_id, starts in by_character.items():
                ordered = sorted(starts)
                for previous, current in zip(ordered, ordered[1:]):
                    if current - previous < 0.2:
                        errors.append(
                            f"Реплики персонажа {character_id} начинаются почти одновременно "
                            f"({previous:.2f} и {current:.2f} с)."
                        )
        return errors

    def _validate_events(self, character_ids: set[str], object_ids: set[str]) -> list[str]:
        errors: list[str] = []
        if self.events and self.version < 2:
            errors.append("Блок events требует version: 2.")
        holders: dict[str, str] = {}
        last_time: dict[str, float] = {}
        for index, event in enumerate(sorted(self.events, key=lambda item: item.t), 1):
            label = f"Событие {index} ({event.kind})"
            if event.kind not in _EVENT_KINDS:
                errors.append(f"{label}: неизвестный тип события.")
                continue
            if event.object not in object_ids:
                errors.append(f"{label}: неизвестный объект «{event.object}».")
                continue
            if event.t < -1e-6 or event.t > self.duration + 1e-6:
                errors.append(f"{label}: время вне сцены t={event.t}.")
            if event.object in last_time and abs(event.t - last_time[event.object]) < 1e-6:
                errors.append(
                    f"{label}: два события объекта «{event.object}» в один момент t={event.t:.2f}."
                )
            last_time[event.object] = event.t
            holder = holders.get(event.object, "")
            if event.character and event.character not in character_ids:
                errors.append(f"{label}: неизвестный персонаж «{event.character}».")
            if event.kind == "attach":
                if holder:
                    errors.append(
                        f"{label}: объект «{event.object}» уже удерживается ({holder}) и не может быть взят снова."
                    )
                if not event.character:
                    errors.append(f"{label}: не указан персонаж, который берёт объект.")
                    continue
                if event.point not in RIG_POINTS:
                    errors.append(f"{label}: неизвестная точка рига «{event.point}».")
                scene_object = self.scene_object(event.object)
                if scene_object and event.t < scene_object.appear_at - 1e-6:
                    errors.append(f"{label}: объект ещё не появился (appear_at={scene_object.appear_at}).")
                if event.scale < 0:
                    errors.append(f"{label}: отрицательный масштаб.")
                holders[event.object] = event.character
            elif event.kind == "detach":
                if not holder:
                    errors.append(f"{label}: нельзя отпустить объект «{event.object}», он не удерживается.")
                    continue
                if event.character and event.character != holder:
                    errors.append(
                        f"{label}: объект удерживает {holder}, а отпускает {event.character}."
                    )
                if max(abs(value) for value in event.velocity) > 2.0:
                    errors.append(f"{label}: слишком большая скорость отпускания {event.velocity}.")
                holders[event.object] = ""
            else:  # transfer
                if not holder:
                    errors.append(f"{label}: передача объекта «{event.object}», который не удерживается.")
                    continue
                if event.character and event.character != holder:
                    errors.append(
                        f"{label}: объект удерживает {holder}, а передаёт {event.character}."
                    )
                if not event.target:
                    errors.append(f"{label}: не указан получатель передачи.")
                    continue
                if event.target == holder:
                    errors.append(f"{label}: персонаж {holder} передаёт объект сам себе.")
                if event.target not in character_ids:
                    errors.append(f"{label}: неизвестный получатель «{event.target}».")
                if event.point not in RIG_POINTS:
                    errors.append(f"{label}: неизвестная точка рига «{event.point}».")
                holders[event.object] = event.target
        return errors


# --------------------------------------------------------------- object sampling


def sample_object(scene_object: SceneObject, t: float) -> dict[str, float]:
    """Interpolate an object's transform (position, scale, rotation) at time ``t``."""
    frames = scene_object.keyframes
    if not frames:
        return {"x": 0.5, "y": 0.5, "scale": 1.0, "rotation": 0.0, "visible": 0.0}
    visible = 0.0 if t < scene_object.appear_at else 1.0
    if t <= frames[0]["t"]:
        first = dict(frames[0])
        first["visible"] = visible
        return first
    if t >= frames[-1]["t"]:
        last = dict(frames[-1])
        last["visible"] = visible
        return last
    for index in range(len(frames) - 1):
        a, b = frames[index], frames[index + 1]
        if a["t"] <= t <= b["t"]:
            span = b["t"] - a["t"] or 1.0
            u = ease((t - a["t"]) / span, "smooth")
            result = {key: lerp(a[key], b[key], u) for key in ("x", "y", "scale", "rotation")}
            result["visible"] = visible
            return result
    last = dict(frames[-1])
    last["visible"] = visible
    return last


def load_scene(path: str | Path) -> AnimationScene:
    """Load and validate a scene JSON, raising :class:`SceneError` on any problem."""
    source = Path(path)
    if not source.exists():
        raise SceneError(f"Файл сцены не найден: {source}")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SceneError(f"Не удалось прочитать сцену {source}: {exc}") from exc
    scene = AnimationScene.from_dict(data)
    errors = scene.validate()
    if errors:
        raise SceneError("Сцена некорректна:\n- " + "\n- ".join(errors))
    return scene
