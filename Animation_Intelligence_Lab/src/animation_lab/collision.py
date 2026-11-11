"""collision.py — lightweight 2D collision and reachability checks.

Circle and AABB colliders in normalized scene space.  The report *distinguishes*:

* hard geometric intersection (``error``-level facts),
* proximity warnings (near miss),
* heuristic suspicion of a visual defect (e.g. overlapping silhouettes of
  two characters for a long time).

This is NOT a full body-physics simulation; it is a kinematic diagnostic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from .models import CharacterState, PropState
from .vectors import EPS, Vec2, clamp01


# ---------------------------------------------------------------------------
# Colliders
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Circle:
    center: Vec2
    radius: float

    def __post_init__(self) -> None:
        if self.radius < 0:
            raise ValueError("radius must be >= 0")


@dataclass(frozen=True)
class AABB:
    """Axis-aligned bounding box: center + half-extents (normalized units)."""

    center: Vec2
    half: Vec2

    @property
    def min(self) -> Vec2:
        return (self.center[0] - self.half[0], self.center[1] - self.half[1])

    @property
    def max(self) -> Vec2:
        return (self.center[0] + self.half[0], self.center[1] + self.half[1])


# ---------------------------------------------------------------------------
# Primitive intersections
# ---------------------------------------------------------------------------

def circle_circle(a: Circle, b: Circle) -> bool:
    dx = b.center[0] - a.center[0]
    dy = b.center[1] - a.center[1]
    r = a.radius + b.radius
    return dx * dx + dy * dy < r * r - EPS


def circle_aabb(c: Circle, box: AABB) -> bool:
    cx, cy = c.center
    nx = clamp01((cx - box.min[0]) / max(2 * box.half[0], EPS)) * 2 * box.half[0] + box.min[0]
    ny = clamp01((cy - box.min[1]) / max(2 * box.half[1], EPS)) * 2 * box.half[1] + box.min[1]
    dx, dy = cx - nx, cy - ny
    return dx * dx + dy * dy < c.radius * c.radius - EPS


def aabb_aabb(a: AABB, b: AABB) -> bool:
    return (
        a.min[0] < b.max[0] - EPS and a.max[0] > b.min[0] + EPS
        and a.min[1] < b.max[1] - EPS and a.max[1] > b.min[1] + EPS
    )


# ---------------------------------------------------------------------------
# Character-level checks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OverlapReport:
    kind: str          # "collision" | "proximity" | "heuristic"
    severity: str      # "error" | "warning" | "info"
    message: str
    a_id: str
    b_id: Optional[str] = None
    distance: Optional[float] = None


def default_character_radius(state: CharacterState) -> float:
    """Radius of a character's body collider in scene units (scaled)."""
    return 0.045 * state.scale


def character_overlap(
    a: CharacterState, b: CharacterState, min_gap: float = 0.02
) -> Optional[OverlapReport]:
    """Check two characters at one instant.

    * bodies intersect                        -> ``collision`` (error)
    * gap below ``min_gap``                   -> ``proximity`` (warning)
    * deep overlap (centers < 60% of radii)   -> heuristic suspicion
    """
    ra = default_character_radius(a)
    rb = default_character_radius(b)
    d = math.hypot(
        b.position.x - a.position.x, b.position.y - a.position.y
    )
    if d < ra + rb - EPS:
        deep = d < 0.6 * (ra + rb)
        return OverlapReport(
            kind="collision",
            severity="error",
            message=(
                f"characters {a.character_id!r} and {b.character_id!r} "
                f"intersect (distance {d:.4f} < {ra + rb:.4f})"
            ),
            a_id=a.character_id,
            b_id=b.character_id,
            distance=d,
        ) if not deep else OverlapReport(
            kind="heuristic",
            severity="error",
            message=(
                f"characters {a.character_id!r} and {b.character_id!r} overlap "
                f"deeply (visual defect suspected)"
            ),
            a_id=a.character_id,
            b_id=b.character_id,
            distance=d,
        )
    if d < ra + rb + min_gap:
        return OverlapReport(
            kind="proximity",
            severity="warning",
            message=(
                f"characters {a.character_id!r} and {b.character_id!r} are "
                f"close ({d:.4f}), min gap {min_gap:.4f}"
            ),
            a_id=a.character_id,
            b_id=b.character_id,
            distance=d,
        )
    return None


def prop_overlap_character(
    prop: PropState, ch: CharacterState
) -> Optional[OverlapReport]:
    """A free prop sitting inside a character's body collider (visual defect)."""
    if prop.owner_id is not None:
        return None
    r = default_character_radius(ch)
    d = math.hypot(prop.position.x - ch.position.x, prop.position.y - ch.position.y)
    if d < r:
        return OverlapReport(
            kind="heuristic",
            severity="info",
            message=(
                f"free prop {prop.prop_id!r} rests inside body collider of "
                f"{ch.character_id!r} (possible visual defect)"
            ),
            a_id=prop.prop_id,
            b_id=ch.character_id,
            distance=d,
        )
    return None


def out_of_bounds(position: Vec2, margin: float = 0.0) -> bool:
    """True when a position leaves the [0,1]^2 scene (optionally with margin)."""
    m = margin
    return (
        position[0] < -m or position[0] > 1.0 + m
        or position[1] < -m or position[1] > 1.0 + m
    )


def min_character_distance(
    states: Sequence[CharacterState],
) -> Optional[float]:
    """Smallest pairwise distance among characters (None if < 2 characters)."""
    best: Optional[float] = None
    for i in range(len(states)):
        for j in range(i + 1, len(states)):
            d = math.hypot(
                states[j].position.x - states[i].position.x,
                states[j].position.y - states[i].position.y,
            )
            if best is None or d < best:
                best = d
    return best


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------

def within_arm_reach(
    actor: CharacterState,
    target: Vec2,
    reach: float = 0.18,
) -> bool:
    """Approximate check whether ``target`` is within the actor's arm reach.

    ``reach`` is in normalized scene units and should already include the
    character's scale factor; see :func:`animation_lab.rig_2d.arm_reach`.
    """
    d = math.hypot(target[0] - actor.position.x, target[1] - actor.position.y)
    return d <= reach * actor.scale + EPS


def reachable_target(
    start: Vec2,
    goal: Vec2,
    max_speed: float,
    duration: float,
) -> bool:
    """Whether ``goal`` is reachable from ``start`` in ``duration`` seconds."""
    if max_speed <= 0:
        raise ValueError("max_speed must be positive")
    d = math.hypot(goal[0] - start[0], goal[1] - start[1])
    return d <= max_speed * duration + EPS
