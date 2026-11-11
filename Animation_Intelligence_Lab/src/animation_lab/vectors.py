"""vectors.py — 2D vector math, coordinate conversions and easing utilities.

Coordinate conventions (see docs/KINEMATICS.md):

* The scene uses a **normalized 2D space**: both ``x`` and ``y`` lie in
  ``[0, 1]``.  ``(0, 0)`` is the *top-left* corner, ``x`` grows to the
  *right*, ``y`` grows *down* (screen convention, not mathematical).
* Time is expressed in **seconds**.  Pixel coordinates are derived only at
  visualization/export time, never stored in plans.

Angles are expressed in **degrees**; ``0`` points to the right (+x) and
positive rotation is *clockwise* on screen (because y points down).
"""

from __future__ import annotations

import math
from typing import Final, Literal, Sequence

import numpy as np

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Vec2 = tuple[float, float]

#: Tolerance used when comparing positions / rotations.
EPS: Final[float] = 1e-9


# ---------------------------------------------------------------------------
# Basic 2D vector helpers (pure python tuples, no numpy needed for size-2)
# ---------------------------------------------------------------------------

def vec2(x: float, y: float) -> Vec2:
    """Create a clamped-in-value 2-tuple. Validates finiteness."""
    x, y = float(x), float(y)
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError(f"Non-finite coordinates: ({x!r}, {y!r})")
    return (x, y)


def add(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] + b[0], a[1] + b[1])


def sub(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] - b[0], a[1] - b[1])


def scale(a: Vec2, k: float) -> Vec2:
    return (a[0] * k, a[1] * k)


def dot(a: Vec2, b: Vec2) -> float:
    return a[0] * b[0] + a[1] * b[1]


def length(a: Vec2) -> float:
    return math.hypot(a[0], a[1])


def distance(a: Vec2, b: Vec2) -> float:
    return length(sub(a, b))


def normalize(a: Vec2) -> Vec2:
    n = length(a)
    if n < EPS:
        return (0.0, 0.0)
    return (a[0] / n, a[1] / n)


def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation between scalars, clamped to [a, b] domain via t."""
    return a + (b - a) * t


def clamp01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def in_unit_square(p: Vec2) -> bool:
    """True when the point lies inside the normalized scene [0,1]x[0,1]."""
    return 0.0 <= p[0] <= 1.0 and 0.0 <= p[1] <= 1.0


def angle_degrees(a: Vec2, b: Vec2) -> float:
    """Clockwise-on-screen angle in degrees of vector ``b - a``.

    With the screen convention (y down), ``math.atan2`` on raw deltas returns
    the clockwise-positive angle already, since positive y is downward.
    """
    d = sub(b, a)
    return math.degrees(math.atan2(d[1], d[0]))


def rotate_deg(p: Vec2, degrees: float) -> Vec2:
    """Rotate point ``p`` around the origin by ``degrees`` (clockwise on screen)."""
    rad = math.radians(degrees)
    cos_t, sin_t = math.cos(rad), math.sin(rad)
    return (p[0] * cos_t - p[1] * sin_t, p[0] * sin_t + p[1] * cos_t)


def wrap_angle_deg(angle: float) -> float:
    """Wrap an angle into (-180, 180]."""
    wrapped = (angle + 180.0) % 360.0 - 180.0
    if wrapped <= -180.0:
        wrapped += 360.0
    return wrapped


def angle_lerp(a: float, b: float, t: float) -> float:
    """Interpolate rotation along the shortest arc (degrees)."""
    return a + wrap_angle_deg(b - a) * t


# ---------------------------------------------------------------------------
# Coordinate conversion: normalized scene space <-> pixels
# ---------------------------------------------------------------------------

def normalized_to_pixels(
    p: Vec2,
    width: int = 1920,
    height: int = 1080,
) -> Vec2:
    """Convert a normalized scene point to pixel coordinates.

    ``width``/``height`` are the target raster resolution in *pixels*.
    Y is not flipped: both spaces grow downward.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive integers")
    return (p[0] * width, p[1] * height)


def pixels_to_normalized(
    p: Vec2,
    width: int = 1920,
    height: int = 1080,
) -> Vec2:
    """Convert pixel coordinates back to normalized scene coordinates."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive integers")
    return (p[0] / width, p[1] / height)


# ---------------------------------------------------------------------------
# Easing
# ---------------------------------------------------------------------------

EasingName = Literal["linear", "ease_in", "ease_out", "ease_in_out"]


def ease_linear(t: float) -> float:
    return clamp01(t)


def ease_in(t: float) -> float:
    """Quadratic acceleration from rest."""
    t = clamp01(t)
    return t * t


def ease_out(t: float) -> float:
    """Quadratic deceleration to rest."""
    t = clamp01(t)
    return 1.0 - (1.0 - t) * (1.0 - t)


def ease_in_out(t: float) -> float:
    """Symmetric smooth acceleration/deceleration (cosine)."""
    t = clamp01(t)
    return 0.5 - 0.5 * math.cos(math.pi * t)


EASING_FUNCTIONS: dict[str, callable] = {
    "linear": ease_linear,
    "ease_in": ease_in,
    "ease_out": ease_out,
    "ease_in_out": ease_in_out,
}


def apply_easing(t: float, easing: str = "linear") -> float:
    """Apply a named easing function to a progress value ``t`` in [0, 1]."""
    try:
        fn = EASING_FUNCTIONS[easing]
    except KeyError:
        raise ValueError(
            f"Unknown easing {easing!r}. Available: {sorted(EASING_FUNCTIONS)}"
        ) from None
    return fn(t)


# ---------------------------------------------------------------------------
# Numerical derivative helpers (for velocity / acceleration estimation)
# ---------------------------------------------------------------------------

def finite_difference_velocities(
    times: Sequence[float],
    positions: Sequence[Vec2],
) -> list[Vec2]:
    """Central-difference velocity estimates for sampled trajectory points."""
    if len(times) != len(positions) or len(times) < 2:
        raise ValueError("times and positions must have the same length >= 2")
    vel: list[Vec2] = []
    for i in range(len(times)):
        if i == 0:
            dt = times[1] - times[0]
            v = scale(sub(positions[1], positions[0]), 1.0 / dt)
        elif i == len(times) - 1:
            dt = times[-1] - times[-2]
            v = scale(sub(positions[-1], positions[-2]), 1.0 / dt)
        else:
            dt = times[i + 1] - times[i - 1]
            v = scale(sub(positions[i + 1], positions[i - 1]), 1.0 / dt)
        vel.append(v)
    return vel


def polyline_samples(
    start: Vec2,
    end: Vec2,
    steps: int,
    easing: str = "linear",
) -> list[Vec2]:
    """Sample ``steps + 1`` points on an eased straight segment start->end."""
    if steps < 1:
        raise ValueError("steps must be >= 1")
    pts: list[Vec2] = []
    for i in range(steps + 1):
        t = apply_easing(i / steps, easing)
        pts.append((lerp(start[0], end[0], t), lerp(start[1], end[1], t)))
    return pts


def points_array(points: Sequence[Vec2]) -> np.ndarray:
    """Convert a sequence of points to an ``(N, 2)`` float ndarray."""
    arr = np.asarray(points, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError(f"expected shape (N, 2), got {arr.shape}")
    return arr
