"""Data structures for the generative video pipeline (Mission 3).

These models are intentionally decoupled from frame creation: the story and
cinematic layers produce plain, validateable structures that any backend
(procedural, generative, hybrid) can consume.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _asdict(value: Any) -> dict:
    return asdict(value)


@dataclass
class StylePreset:
    """A preset maps to real backend parameters (art direction, light, color,
    materials, environment, camera motion, visual density, limits)."""

    id: str
    name: str
    art_direction: str
    lighting: str
    color_palette: str
    materials: str
    environment: str
    camera_motion: str
    visual_density: str
    negative_prompt: str = ""
    limits: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StylePreset":
        return cls(**data)


@dataclass
class CharacterBible:
    """Stable, reusable description of a character (cross-scene consistency)."""

    id: str
    name: str
    age: str = ""
    species: str = "human"
    appearance: dict[str, Any] = field(default_factory=dict)  # skin/hair/eyes/build
    clothing: dict[str, Any] = field(default_factory=dict)
    features: list[str] = field(default_factory=list)
    voice_hint: str = ""
    reference: str = ""  # path to an optional reference image

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CharacterBible":
        return cls(**data)


@dataclass
class EnvironmentBible:
    id: str
    name: str
    description: str = ""
    palette: list[str] = field(default_factory=list)
    lighting: str = ""
    props: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EnvironmentBible":
        return cls(**data)


@dataclass
class VisualStyleBible:
    preset_id: str
    art_direction: str = ""
    lighting: str = ""
    color_palette: str = ""
    materials: str = ""
    environment: str = ""
    camera_motion: str = ""
    visual_density: str = ""
    negative_prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScenePlan:
    """Story-intelligence level: what happens, who is involved, why."""

    idx: int
    location: str
    characters: list[str] = field(default_factory=list)
    action_summary: str = ""
    dialogue: str = ""
    emotion: str = ""
    visual_goal: str = ""
    duration: float = 5.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CinematicShot:
    """Cinematic-planning level: how the scene is framed and moved."""

    scene_idx: int
    shot_type: str = "medium"  # wide | medium | close
    angle: str = "eye"  # eye | low | high | dutch
    camera_position: str = "frontal"
    camera_motion: str = "static"
    composition: str = ""
    depth: str = ""
    lighting: str = ""
    palette: str = ""
    mood: str = ""
    character_action: str = ""
    key_visual_events: list[str] = field(default_factory=list)
    transition: str = "cut"  # cut | fade | dissolve

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BackendCapabilities:
    supports_t2v: bool = False
    supports_image_to_video: bool = False
    supports_first_last_frame: bool = False
    supports_character_ref: bool = False
    native_audio: bool = False
    camera_control: bool = False
    max_duration_s: float = 0.0
    resolutions: list[str] = field(default_factory=list)
    aspects: list[str] = field(default_factory=list)
    cost_per_second_usd: float | None = None
    supports_negative_prompt: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SceneGenerationSpec:
    """Everything one scene clip needs: prompt, refs, framing."""

    scene_idx: int
    prompt: str = ""
    negative_prompt: str = ""
    duration: float = 5.0
    width: int = 1080
    height: int = 1920
    first_frame: str = ""  # optional path
    last_frame: str = ""  # optional path
    reference_images: list[str] = field(default_factory=list)
    seed: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GenerationJob:
    """A submitted backend job (opaque to the pipeline, opaque to providers)."""

    job_id: str
    provider: str
    scene_idx: int
    status: str = "pending"  # pending | running | done | error | cancelled
    detail: str = ""
    media: str = ""  # result clip path once fetched
    cost_usd: float | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CostEstimate:
    estimated_usd: float | None = None
    currency: str = "USD"
    provider: str = ""
    units: str = "seconds"
    breakdown: dict[str, Any] = field(default_factory=dict)
    source: str = "provider pricing"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QCMetrics:
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    streams_ok: bool = False
    decode_ok: bool = False
    flicker: float = 0.0  # normalized mean |ΔL| between consecutive sampled frames
    file_bytes: int = 0
    scenes_count: int = 0
    clips_ok: int = 0
    resolution_consistent: bool = True
    notes: list[str] = field(default_factory=list)
    manual_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AiCreateResult:
    mode: str = "generative"
    status: str = "OK"  # OK | WARNING | ERROR
    provider: str = ""
    output_dir: str = ""
    video: str = ""
    contact_sheet: str = ""
    scenes_path: str = ""
    cinematic_path: str = ""
    bibles_path: str = ""
    manifest_path: str = ""
    qc_json: str = ""
    qc_md: str = ""
    clips: list[str] = field(default_factory=list)
    cost: CostEstimate | None = None
    cost_limited: CostEstimate | None = None
    notes: list[str] = field(default_factory=list)
    error: str = ""
    ran_generative: bool = False

    def to_dict(self) -> dict[str, Any]:
        def _s(value: Any) -> str:
            return str(value) if value else ""

        return {
            "mode": self.mode,
            "status": self.status,
            "provider": self.provider,
            "output_dir": _s(self.output_dir),
            "video": _s(self.video),
            "contact_sheet": _s(self.contact_sheet),
            "scenes_path": _s(self.scenes_path),
            "cinematic_path": _s(self.cinematic_path),
            "bibles_path": _s(self.bibles_path),
            "manifest_path": _s(self.manifest_path),
            "qc_json": _s(self.qc_json),
            "qc_md": _s(self.qc_md),
            "clips": self.clips,
            "cost": self.cost.to_dict() if self.cost else None,
            "cost_limited": self.cost_limited.to_dict() if self.cost_limited else None,
            "notes": self.notes,
            "error": self.error,
            "ran_generative": self.ran_generative,
        }
