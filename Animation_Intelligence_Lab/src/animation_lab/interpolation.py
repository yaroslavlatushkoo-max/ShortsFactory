"""interpolation.py — generic interpolation between states and poses.

Includes scalar/position interpolation, rotation-aware shortest-arc blending
and :func:`interpolate_character_state`, which produces continuous
mid-segment :class:`~animation_lab.models.CharacterState` snapshots.
"""

from __future__ import annotations

from typing import Optional

from .models import CharacterState, Pose, Vec2Field
from .vectors import (
    Vec2,
    angle_lerp,
    clamp01,
    lerp,
    wrap_angle_deg,
)


def interpolate_position(a: Vec2, b: Vec2, t: float) -> Vec2:
    t = clamp01(t)
    return (lerp(a[0], b[0], t), lerp(a[1], b[1], t))


def interpolate_rotation(a: float, b: float, t: float) -> float:
    """Shortest-arc rotation interpolation in degrees."""
    return angle_lerp(a, b, clamp01(t))


def rotation_delta(a: float, b: float) -> float:
    """Signed shortest-arc difference ``b - a`` in degrees."""
    return wrap_angle_deg(b - a)


def interpolate_character_state(
    start: CharacterState,
    end: CharacterState,
    t: float,
) -> CharacterState:
    """Blend two character states at ``t`` in [0, 1].

    Position, rotation and velocity are interpolated; discrete fields
    (emotion, pose_id, visibility) follow the *end* state when ``t >= 0.5``,
    otherwise the start state — producing a deterministic flip-over point.
    """
    t = clamp01(t)
    pos = interpolate_position(start.position.as_tuple(), end.position.as_tuple(), t)
    rot = interpolate_rotation(start.rotation, end.rotation, t)
    vel = interpolate_position(
        start.root_velocity.as_tuple(), end.root_velocity.as_tuple(), t
    )
    gaze: Optional[Vec2Field] = None
    if start.gaze_target is not None and end.gaze_target is not None:
        gz = interpolate_position(
            start.gaze_target.as_tuple(), end.gaze_target.as_tuple(), t
        )
        gaze = Vec2Field(x=gz[0], y=gz[1])
    else:
        gaze = (
            end.gaze_target if end.gaze_target is not None
            else start.gaze_target
        )
    discrete = end if t >= 0.5 else start
    return CharacterState(
        character_id=start.character_id,
        position=Vec2Field(x=pos[0], y=pos[1]),
        rotation=rot,
        root_velocity=Vec2Field(x=vel[0], y=vel[1]),
        scale=lerp(start.scale, end.scale, t),
        emotion=discrete.emotion,
        gaze_target=gaze,
        current_action=discrete.current_action or start.current_action,
        pose_id=discrete.pose_id or start.pose_id,
        visibility=discrete.visibility,
    )


def interpolate_joint_angles(
    a: dict[str, float], b: dict[str, float], t: float
) -> dict[str, float]:
    """Interpolate per-joint rotations (shortest arc), missing keys -> 0."""
    t = clamp01(t)
    keys = set(a) | set(b)
    out: dict[str, float] = {}
    for k in keys:
        va, vb = a.get(k, 0.0), b.get(k, 0.0)
        out[k] = angle_lerp(va, vb, t)
    return out


def interpolate_pose(a: Pose, b: Pose, t: float) -> Pose:
    """Blend two key poses of the *same* character (root + joint angles)."""
    if a.character_id != b.character_id:
        raise ValueError(
            f"cannot interpolate poses of different characters "
            f"({a.character_id!r} vs {b.character_id!r})"
        )
    t = clamp01(t)
    root = interpolate_position(a.root_position.as_tuple(), b.root_position.as_tuple(), t)
    return Pose(
        pose_id=f"{a.pose_id}~{b.pose_id}@{t:.3f}",
        character_id=a.character_id,
        joint_transforms=interpolate_joint_angles(
            a.joint_transforms, b.joint_transforms, t
        ),
        root_position=root,
        root_rotation=interpolate_rotation(a.root_rotation, b.root_rotation, t),
        tags=tuple(sorted(set(a.tags) | set(b.tags))),
    )


def pose_transition_times(
    a: Pose, b: Pose, duration: float, fps: int
) -> list[float]:
    """Evenly spaced sample times for a pose transition of ``duration`` seconds."""
    if duration <= 0:
        raise ValueError("duration must be positive")
    if fps < 1:
        raise ValueError("fps must be >= 1")
    n = max(1, int(round(duration * fps)))
    return [round(duration * i / n, 9) for i in range(n + 1)]
