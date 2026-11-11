"""Unified cross-engine Animation Plan.

This is the single interchange format that both local rendering engines consume:

* the existing **Animation Engine** (2D procedural cut-out rig) — the plan is
  compiled to a v2 scene JSON document;
* the **Blender Engine** (optional, 3D) — the plan is compiled to a Blender
  Python script that builds rigs, poses, keyframes, camera and renders frames.

Both conversions are deterministic and validated against the same plan so a
scene authored once can be rendered by either executor.  The plan deliberately
does **not** reference any cloud provider.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Re-export the supported action catalogue so both engines agree on vocabulary.
from ..animation.motion import ACTIONS as SUPPORTED_ACTIONS
from ..animation.motion import UNSUPPORTED as UNSUPPORTED_ACTIONS
from ..animation.scene import _OBJECT_KINDS as OBJECT_KINDS

#: Engine identifiers understood by the dispatcher.
ENGINE_PROCEDURAL = "procedural"
ENGINE_BLENDER = "blender"
ENGINE_VOXEL = "voxel"
ENGINE_ASSET = "asset"
ENGINE_SKINNED = "skinned"
ENGINE_AUTO = "auto"
ENGINE_CHOICES = (
    ENGINE_PROCEDURAL,
    ENGINE_BLENDER,
    ENGINE_VOXEL,
    ENGINE_ASSET,
    ENGINE_SKINNED,
    ENGINE_AUTO,
)


class PlanError(ValueError):
    """Raised when an animation plan is invalid/unsupported."""


@dataclass
class PlanCharacter:
    id: str
    name: str = ""
    rig: str = "humanoid_v1"
    x: float = 0.5
    y: float = 1.0
    scale: float = 1.0
    facing: int = 1
    z: int = 20
    appear_at: float = 0.0
    palette: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanObject:
    id: str
    kind: str = "orb"
    z: int = 15
    appear_at: float = 0.0
    color: str = "#ffd257"
    #: Keyframes: {t, x, y, scale, rotation}. Only used before attach.
    keyframes: list[dict[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["keyframes"] = [dict(k) for k in self.keyframes]
        return data


@dataclass
class PlanAction:
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
class PlanEvent:
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
class PlanCameraKey:
    t: float
    x: float = 0.5
    y: float = 0.5
    zoom: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanVoiceLine:
    character: str
    text: str
    start: float
    voice: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnimationPlan:
    """Cross-engine normalized plan for one scene.

    All spatial values are stage-normalised (0..1) and all times are seconds,
    matching the Animation Engine coordinate system; the Blender adapter maps
    these onto its own world/eye space.
    """

    scene_id: str
    title: str = ""
    duration: float = 12.0
    fps: int = 30
    width: int = 1080
    height: int = 1920
    environment: str = ""
    characters: list[PlanCharacter] = field(default_factory=list)
    objects: list[PlanObject] = field(default_factory=list)
    actions: list[PlanAction] = field(default_factory=list)
    events: list[PlanEvent] = field(default_factory=list)
    camera: list[PlanCameraKey] = field(default_factory=list)
    voice_lines: list[PlanVoiceLine] = field(default_factory=list)
    contact_times: list[float] = field(default_factory=list)
    blink_times: list[float] = field(default_factory=list)
    seed: int = 20261023
    blend: float = 0.18
    voice_lang: str = "ru"
    voice_gain: float = 0.9
    #: Additional action names accepted by this plan (engine-specific), on top
    #: of the shared ACTIONS catalogue (e.g. voxel: bend/hold/fall/recover).
    extra_actions: set[str] = field(default_factory=set)
    #: Optional asset-based production reference ({character, environment, props,
    #: clips, shots}).  Populated by the asset pipeline; ignored by other engines.
    production: dict | None = None

    # ------------------------------------------------------------------ checks
    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.scene_id:
            errors.append("Не задан id плана.")
        if self.duration <= 0:
            errors.append(f"Некорректная длительность: {self.duration}.")
        if not self.characters:
            errors.append("В плане нет персонажей.")
        char_ids = {c.id for c in self.characters}
        for action in self.actions:
            if action.action in UNSUPPORTED_ACTIONS:
                errors.append(f"Действие «{action.action}» не поддерживается: {UNSUPPORTED_ACTIONS[action.action]}.")
            elif action.action not in SUPPORTED_ACTIONS and action.action not in self.extra_actions:
                errors.append(f"Неизвестное действие «{action.action}».")
            if action.character not in char_ids:
                errors.append(f"Действие ссылается на неизвестного персонажа: {action.character}.")
            if action.duration <= 0:
                errors.append(f"Некорректная длительность действия «{action.action}».")
            if action.start + action.duration > self.duration + 1e-6:
                errors.append(f"Действие «{action.action}» выходит за границу сцены.")
        if not self.camera:
            errors.append("Не задана камера (хотя бы один ключевой кадр).")
        for key in self.camera:
            if key.zoom <= 0:
                errors.append("Некорректный зум камеры.")
        for line in self.voice_lines:
            if not line.text.strip():
                errors.append("Есть пустая реплика.")
        return errors

    def ensure(self) -> "AnimationPlan":
        errors = self.validate()
        if errors:
            raise PlanError("План некорректн:\n- " + "\n- ".join(errors))
        return self

    # ---------------------------------------------------------- engine mapping
    def to_scene_dict(self) -> dict[str, Any]:
        """Compile to an Animation Engine v2 scene JSON object."""
        return {
            "version": 2,
            "id": self.scene_id,
            "title": self.title,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "duration": self.duration,
            "seed": self.seed,
            "blend": self.blend,
            "environment": self.environment,
            "background": {},
            "foreground": {},
            "characters": [c.to_dict() for c in self.characters],
            "objects": [o.to_dict() for o in self.objects],
            "actions": [a.to_dict() for a in self.actions],
            "events": [e.to_dict() for e in self.events],
            "camera": [k.to_dict() for k in self.camera],
            "blink_times": list(self.blink_times),
            "contact_times": list(self.contact_times),
            "voice": {
                "lang": self.voice_lang,
                "gain": self.voice_gain,
                "lines": [l.to_dict() for l in self.voice_lines],
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "title": self.title,
            "duration": self.duration,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "environment": self.environment,
            "characters": [c.to_dict() for c in self.characters],
            "objects": [o.to_dict() for o in self.objects],
            "actions": [a.to_dict() for a in self.actions],
            "events": [e.to_dict() for e in self.events],
            "camera": [k.to_dict() for k in self.camera],
            "voice_lines": [l.to_dict() for l in self.voice_lines],
            "contact_times": list(self.contact_times),
            "blink_times": list(self.blink_times),
            "seed": self.seed,
            "blend": self.blend,
            "voice_lang": self.voice_lang,
            "voice_gain": self.voice_gain,
            "extra_actions": sorted(self.extra_actions),
            "production": self.production,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnimationPlan":
        return cls(
            scene_id=str(data.get("scene_id", "")),
            title=str(data.get("title", "")),
            duration=float(data.get("duration", 0.0)),
            fps=int(data.get("fps", 30) or 30),
            width=int(data.get("width", 1080) or 1080),
            height=int(data.get("height", 1920) or 1920),
            environment=str(data.get("environment", "") or ""),
            characters=[PlanCharacter(**{k: v for k, v in c.items() if k in PlanCharacter.__dataclass_fields__})
                        for c in (data.get("characters") or [])],
            objects=[PlanObject(**{k: v for k, v in o.items() if k in PlanObject.__dataclass_fields__})
                     for o in (data.get("objects") or [])],
            actions=[PlanAction(**{k: v for k, v in a.items() if k in PlanAction.__dataclass_fields__})
                     for a in (data.get("actions") or [])],
            events=[PlanEvent(**{k: v for k, v in e.items() if k in PlanEvent.__dataclass_fields__})
                    for e in (data.get("events") or [])],
            camera=[PlanCameraKey(**{k: v for k, v in k.items() if k in PlanCameraKey.__dataclass_fields__})
                    for k in (data.get("camera") or [])],
            voice_lines=[PlanVoiceLine(**{k: v for k, v in l.items() if k in PlanVoiceLine.__dataclass_fields__})
                         for l in (data.get("voice_lines") or [])],
            contact_times=[float(x) for x in data.get("contact_times", [])],
            blink_times=[float(x) for x in data.get("blink_times", [])],
            seed=int(data.get("seed", 20261023)),
            blend=float(data.get("blend", 0.18)),
            voice_lang=str(data.get("voice_lang", "ru")),
            voice_gain=float(data.get("voice_gain", 0.9)),
            extra_actions=set(data.get("extra_actions", []) or []),
            production=data.get("production"),
        )
