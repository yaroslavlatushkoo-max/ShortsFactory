"""models.py — strict Pydantic data models for Animation Intelligence Lab.

All coordinates are **normalized** (``[0, 1]`` in both axes, origin at the
top-left corner, y grows downward).  All times are **seconds**.  Rotations
are **degrees** with positive = clockwise on screen.  Units are documented in
docs/KINEMATICS.md and echoed in the exported JSON via ``units`` metadata.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SCHEMA_VERSION: str = "1.0.0"

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*$")


def _check_id(value: str) -> str:
    if not _ID_RE.match(value):
        raise ValueError(
            f"invalid identifier {value!r}: must match ^[A-Za-z][A-Za-z0-9_\\-]*$"
        )
    return value


class LabBaseModel(BaseModel):
    """Common configuration for every model: strict, immutable-ish, no extras."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

UnitVector2 = tuple[float, float]


class Vec2Field(LabBaseModel):
    """A point or vector in normalized scene space (unit: scene, [0,1])."""

    x: float
    y: float

    @field_validator("x", "y")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("coordinates must be finite")
        return v

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    @classmethod
    def of(cls, t: tuple[float, float]) -> "Vec2Field":
        return cls(x=t[0], y=t[1])


class RotationLimits(LabBaseModel):
    """Allowed rotation interval for a joint, in degrees, min < max."""

    min_deg: float = -180.0
    max_deg: float = 180.0

    @model_validator(mode="after")
    def _ordered(self) -> "RotationLimits":
        if self.min_deg >= self.max_deg:
            raise ValueError(
                f"min_deg ({self.min_deg}) must be < max_deg ({self.max_deg})"
            )
        return self

    def contains(self, angle: float) -> bool:
        return self.min_deg <= angle <= self.max_deg


# ---------------------------------------------------------------------------
# Characters, joints, poses
# ---------------------------------------------------------------------------

EmotionKind = Literal[
    "neutral", "happy", "sad", "surprised", "angry", "afraid", "curious",
]
VisibilityKind = Literal["visible", "hidden", "faded"]
PropStateKind = Literal[
    "free", "approaching", "attached", "transferring", "released",
]


class CharacterState(LabBaseModel):
    """Snapshot of a character at one instant of time.

    * ``position`` — normalized scene coordinates, unitless ([0,1] both axes).
    * ``rotation`` — degrees, clockwise on screen.
    * ``root_velocity`` — normalized scene units per second.
    """

    character_id: Annotated[str, Field(min_length=1)]
    position: Vec2Field
    rotation: float = 0.0
    root_velocity: Vec2Field = Vec2Field(x=0.0, y=0.0)
    scale: Annotated[float, Field(gt=0.0)] = 1.0
    emotion: EmotionKind = "neutral"
    gaze_target: Optional[Vec2Field] = None
    current_action: Optional[str] = None
    pose_id: Optional[str] = None
    visibility: VisibilityKind = "visible"

    @field_validator("character_id")
    @classmethod
    def _id_ok(cls, v: str) -> str:
        return _check_id(v)


class Joint(LabBaseModel):
    """A single skeleton joint.

    ``local_position`` is in the *parent joint space*, normalized relative to
    the character's bounding box (unitless, typically within [0,1]).
    ``rotation`` is degrees.  ``length`` (optional) is the distance to the
    next joint in the chain, in the same normalized units.
    """

    joint_id: Annotated[str, Field(min_length=1)]
    parent_id: Optional[str] = None
    local_position: Vec2Field
    rotation: float = 0.0
    limits: RotationLimits = RotationLimits()
    length: Optional[Annotated[float, Field(gt=0.0)]] = None

    @field_validator("joint_id", "parent_id")
    @classmethod
    def _id_ok(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _check_id(v)


class Pose(LabBaseModel):
    """A key pose: joint local rotations + root placement for one character."""

    pose_id: Annotated[str, Field(min_length=1)]
    character_id: Annotated[str, Field(min_length=1)]
    joint_transforms: dict[str, float] = Field(default_factory=dict)
    root_position: Vec2Field = Vec2Field(x=0.0, y=0.0)
    root_rotation: float = 0.0
    tags: tuple[str, ...] = ()

    @field_validator("pose_id", "character_id")
    @classmethod
    def _id_ok(cls, v: str) -> str:
        return _check_id(v)


# ---------------------------------------------------------------------------
# Trajectories and segments
# ---------------------------------------------------------------------------

TrajectoryKind = Literal[
    "linear", "bezier3", "waypoints", "hold", "arc",
]


class TrajectorySpec(LabBaseModel):
    """Trajectory definition attached to a motion segment.

    * ``kind="linear"`` — eased straight line from start to end state.
    * ``kind="bezier3"`` — cubic Bézier through ``control_points`` (1 or 2
      controls, first segment start, last segment end).
    * ``kind="waypoints"`` — pass through ``control_points`` in order with
      per-leg easing.
    * ``kind="hold"`` — stay at ``start`` position for the whole segment.
    """

    kind: TrajectoryKind = "linear"
    control_points: tuple[Vec2Field, ...] = ()
    easing: Literal["linear", "ease_in", "ease_out", "ease_in_out"] = "linear"
    max_speed: Optional[Annotated[float, Field(gt=0.0)]] = None

    @model_validator(mode="after")
    def _controls_ok(self) -> "TrajectorySpec":
        if self.kind == "bezier3" and len(self.control_points) not in (1, 2):
            raise ValueError("bezier3 needs 1 or 2 control points")
        if self.kind == "waypoints" and len(self.control_points) < 1:
            raise ValueError("waypoints needs at least 1 control point")
        return self


class MotionConstraints(LabBaseModel):
    max_speed: Annotated[float, Field(gt=0.0)] = 0.5
    max_acceleration: Annotated[float, Field(gt=0.0)] = 2.0
    keep_in_bounds: bool = True


class MotionSegment(LabBaseModel):
    """A timed slice of one character's motion.

    ``start_state``/``end_state`` carry full :class:`CharacterState` snapshots;
    the segment evaluates the trajectory between them for ``t`` in
    ``[start_time, end_time]``.  ``end_state`` of a segment and ``start_state``
    of the following one must agree (continuity is validated separately and
    reported as a diagnostic when broken).
    """

    id: Annotated[str, Field(min_length=1)]
    character_id: Annotated[str, Field(min_length=1)]
    action_type: Literal[
        "idle", "walk", "run", "turn", "reach", "hold", "speak",
        "react", "jump", "fall", "land", "sit", "stand", "step_back",
        "handoff",
    ]
    start_time: Annotated[float, Field(ge=0.0)]
    end_time: Annotated[float, Field(gt=0.0)]
    start_state: CharacterState
    end_state: CharacterState
    trajectory: TrajectorySpec = TrajectorySpec()
    easing: Literal["linear", "ease_in", "ease_out", "ease_in_out"] = "linear"
    constraints: MotionConstraints = MotionConstraints()
    dependencies: tuple[str, ...] = ()

    @field_validator("id", "character_id")
    @classmethod
    def _id_ok(cls, v: str) -> str:
        return _check_id(v)

    @model_validator(mode="after")
    def _time_ok(self) -> "MotionSegment":
        if self.end_time <= self.start_time:
            raise ValueError(
                f"segment {self.id!r}: end_time ({self.end_time}) must be > "
                f"start_time ({self.start_time})"
            )
        return self

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


# ---------------------------------------------------------------------------
# Props and interactions
# ---------------------------------------------------------------------------

class PropState(LabBaseModel):
    """State of a prop (item) at one instant.

    ``position`` is normalized; ``velocity`` is normalized units/second.
    ``attachment_point`` names a rig anchor (e.g. ``hand_r``) when owned.
    """

    prop_id: Annotated[str, Field(min_length=1)]
    position: Vec2Field
    rotation: float = 0.0
    owner_id: Optional[str] = None
    attachment_point: Optional[str] = None
    velocity: Vec2Field = Vec2Field(x=0.0, y=0.0)
    state: PropStateKind = "free"
    glowing: bool = False

    @field_validator("prop_id", "owner_id")
    @classmethod
    def _id_ok(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _check_id(v)

    @model_validator(mode="after")
    def _owner_consistent(self) -> "PropState":
        attached = self.state in ("attached", "transferring")
        if attached and self.owner_id is None:
            raise ValueError(
                f"prop {self.prop_id!r}: state {self.state!r} requires owner_id"
            )
        if self.state == "free" and self.owner_id is not None:
            raise ValueError(
                f"prop {self.prop_id!r}: state 'free' cannot have an owner"
            )
        return self


InteractionKind = Literal[
    "pickup", "attach", "transfer", "detach", "release", "handoff",
]


class InteractionPrecondition(LabBaseModel):
    """A single precondition check attached to an interaction event."""

    check: Literal[
        "actor_holds_prop",
        "actor_near_prop",
        "prop_is_free",
        "receiver_defined",
        "owner_is_actor",
        "prop_in_scene",
    ]
    description: str = ""


class Interaction(LabBaseModel):
    """A timed interaction event (pickup / attach / transfer / detach / release).

    Preconditions are checked by :mod:`animation_lab.interaction` at plan
    build time; a violated precondition raises
    :class:`animation_lab.interaction.InteractionError` — the planner never
    silently repairs broken data.
    """

    id: Annotated[str, Field(min_length=1)]
    kind: InteractionKind
    time: Annotated[float, Field(ge=0.0)]
    actor_id: Annotated[str, Field(min_length=1)]
    prop_id: Annotated[str, Field(min_length=1)]
    receiver_id: Optional[str] = None
    attachment_point: Optional[str] = None
    preconditions: tuple[InteractionPrecondition, ...] = ()
    expected_prop_state_after: PropStateKind
    expected_owner_after: Optional[str] = None

    @field_validator("id", "actor_id", "prop_id", "receiver_id")
    @classmethod
    def _id_ok(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _check_id(v)

    @model_validator(mode="after")
    def _receiver_needed(self) -> "Interaction":
        if self.kind == "transfer" and self.receiver_id is None:
            raise ValueError(f"interaction {self.id!r}: transfer requires receiver_id")
        return self


# ---------------------------------------------------------------------------
# Keyframes and plan
# ---------------------------------------------------------------------------

class Keyframe(LabBaseModel):
    """A sampled full-scene snapshot at a specific time."""

    time: Annotated[float, Field(ge=0.0)]
    label: Optional[str] = None
    characters: dict[str, CharacterState] = Field(default_factory=dict)
    props: dict[str, PropState] = Field(default_factory=dict)


class ValidationIssue(LabBaseModel):
    severity: Literal["info", "warning", "error", "critical"]
    code: str
    message: str
    time: Optional[float] = None
    subject_id: Optional[str] = None
    subject_kind: Optional[Literal["character", "prop", "segment", "interaction"]] = None
    related_events: tuple[str, ...] = ()
    suggestion: str = ""


class ValidationReport(LabBaseModel):
    status: Literal["pass", "warnings", "fail"]
    issues: tuple[ValidationIssue, ...] = ()
    checked_at_plan_version: str = SCHEMA_VERSION

    @property
    def has_errors(self) -> bool:
        return any(i.severity in ("error", "critical") for i in self.issues)


class SceneDescription(LabBaseModel):
    """Top-level input document describing a scene and its actions."""

    schema_version: str = SCHEMA_VERSION
    name: str = "untitled"
    fps: Annotated[int, Field(ge=1, le=240)] = 30
    duration_seconds: Annotated[float, Field(gt=0.0)]
    characters: dict[str, CharacterState] = Field(default_factory=dict)
    props: dict[str, PropState] = Field(default_factory=dict)
    motion_segments: tuple[MotionSegment, ...] = ()
    interactions: tuple[Interaction, ...] = ()
    poses: tuple[Pose, ...] = ()

    @field_validator("duration_seconds")
    @classmethod
    def _positive(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError("duration_seconds must be a positive finite number")
        return v

    @model_validator(mode="after")
    def _check_references(self) -> "SceneDescription":
        # segment -> character references
        for seg in self.motion_segments:
            if seg.character_id not in self.characters:
                raise ValueError(
                    f"segment {seg.id!r}: unknown character {seg.character_id!r}"
                )
            if seg.end_time > self.duration_seconds + 1e-9:
                raise ValueError(
                    f"segment {seg.id!r}: end_time {seg.end_time} exceeds "
                    f"duration_seconds {self.duration_seconds}"
                )
        # interaction references
        for inter in self.interactions:
            if inter.actor_id not in self.characters:
                raise ValueError(
                    f"interaction {inter.id!r}: unknown actor {inter.actor_id!r}"
                )
            if inter.receiver_id and inter.receiver_id not in self.characters:
                raise ValueError(
                    f"interaction {inter.id!r}: unknown receiver {inter.receiver_id!r}"
                )
            if inter.prop_id not in self.props:
                raise ValueError(
                    f"interaction {inter.id!r}: unknown prop {inter.prop_id!r}"
                )
            if inter.time > self.duration_seconds + 1e-9:
                raise ValueError(
                    f"interaction {inter.id!r}: time {inter.time} exceeds duration"
                )
        # prop initial owners
        for prop_id, prop in self.props.items():
            if prop.owner_id is not None and prop.owner_id not in self.characters:
                raise ValueError(
                    f"prop {prop_id!r}: unknown owner {prop.owner_id!r}"
                )
        # pose references
        for pose in self.poses:
            if pose.character_id not in self.characters:
                raise ValueError(
                    f"pose {pose.pose_id!r}: unknown character {pose.character_id!r}"
                )
        return self


class MotionPlan(LabBaseModel):
    """Fully resolved, machine-readable output of the motion planner.

    Exported as versioned JSON (see schemas/motion_plan.schema.json).  All
    positions are normalized scene coordinates; velocities are scene
    units/second; times are seconds.
    """

    schema_version: str = SCHEMA_VERSION
    name: str = "untitled"
    fps: Annotated[int, Field(ge=1, le=240)] = 30
    duration_seconds: Annotated[float, Field(gt=0.0)]
    units: dict[str, str] = Field(
        default_factory=lambda: {
            "position": "normalized_scene_units [0,1]",
            "velocity": "normalized_scene_units_per_second",
            "time": "seconds",
            "rotation": "degrees_clockwise_screen",
        }
    )
    characters: dict[str, CharacterState] = Field(default_factory=dict)
    props: dict[str, PropState] = Field(default_factory=dict)
    motion_segments: tuple[MotionSegment, ...] = ()
    interactions: tuple[Interaction, ...] = ()
    keyframes: tuple[Keyframe, ...] = ()
    validation_report: ValidationReport = ValidationReport(status="pass")
    timeline: tuple[Keyframe, ...] = Field(default_factory=tuple)

    @field_validator("duration_seconds")
    @classmethod
    def _positive(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError("duration_seconds must be positive")
        return v
