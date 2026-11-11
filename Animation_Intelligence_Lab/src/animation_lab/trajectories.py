"""trajectories.py — deterministic trajectory evaluation for motion segments.

Every evaluator is a *pure function of time*: given a segment and a time
``t`` within ``[start_time, end_time]`` it returns the same position and
velocity on every call (no randomness, no hidden state).  Velocities are in
normalized scene units per second.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from .models import MotionSegment
from .vectors import (
    EPS,
    Vec2,
    angle_degrees,
    apply_easing,
    clamp01,
    lerp,
    scale,
    sub,
)

# ---------------------------------------------------------------------------
# Pure curve evaluators
# ---------------------------------------------------------------------------


def linear_point(start: Vec2, end: Vec2, t: float, easing: str = "linear") -> Vec2:
    """Eased straight line start->end, ``t`` in [0, 1]."""
    e = apply_easing(t, easing)
    return (lerp(start[0], end[0], e), lerp(start[1], end[1], e))


def cubic_bezier_point(
    p0: Vec2, p1: Vec2, p2: Vec2, p3: Vec2, t: float
) -> Vec2:
    """Cubic Bézier at parameter ``t`` in [0, 1] (positional, not eased)."""
    t = clamp01(t)
    u = 1.0 - t
    b0 = u * u * u
    b1 = 3.0 * u * u * t
    b2 = 3.0 * u * t * t
    b3 = t * t * t
    return (
        b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0],
        b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1],
    )


def bezier_velocity(
    p0: Vec2, p1: Vec2, p2: Vec2, p3: Vec2, t: float
) -> Vec2:
    """Analytic first derivative of the cubic Bézier, w.r.t. parameter ``t``."""
    t = clamp01(t)
    u = 1.0 - t
    d0 = 3.0 * u * u
    d1 = 6.0 * u * t
    d2 = 3.0 * t * t
    return (
        d0 * (p1[0] - p0[0]) + d1 * (p2[0] - p1[0]) + d2 * (p3[0] - p2[0]),
        d0 * (p1[1] - p0[1]) + d1 * (p2[1] - p1[1]) + d2 * (p3[1] - p2[1]),
    )


def waypoints_point(points: Sequence[Vec2], t: float) -> Vec2:
    """Piecewise-linear pass through ``points`` at global ``t`` in [0, 1]."""
    if len(points) < 2:
        if len(points) == 1:
            return points[0]
        raise ValueError("waypoints needs at least 2 points")
    t = clamp01(t)
    n_legs = len(points) - 1
    pos = t * n_legs
    idx = min(int(pos), n_legs - 1)
    local = pos - idx
    a, b = points[idx], points[idx + 1]
    return (lerp(a[0], b[0], local), lerp(a[1], b[1], local))


# ---------------------------------------------------------------------------
# Segment evaluation
# ---------------------------------------------------------------------------

def _bezier_controls(seg: MotionSegment) -> tuple[Vec2, Vec2, Vec2, Vec2]:
    start = seg.start_state.position.as_tuple()
    end = seg.end_state.position.as_tuple()
    ctrls = [c.as_tuple() for c in seg.trajectory.control_points]
    if len(ctrls) == 1:
        return start, ctrls[0], ctrls[0], end
    return start, ctrls[0], ctrls[1], end


def segment_position(seg: MotionSegment, time: float) -> Vec2:
    """Position of the segment's moving point at absolute ``time`` (seconds)."""
    if time <= seg.start_time:
        return seg.start_state.position.as_tuple()
    if time >= seg.end_time:
        return seg.end_state.position.as_tuple()
    u = (time - seg.start_time) / seg.duration
    traj = seg.trajectory

    if traj.kind == "hold":
        return seg.start_state.position.as_tuple()
    if traj.kind == "linear":
        return linear_point(
            seg.start_state.position.as_tuple(),
            seg.end_state.position.as_tuple(),
            u,
            traj.easing,
        )
    if traj.kind == "bezier3":
        p0, p1, p2, p3 = _bezier_controls(seg)
        e = apply_easing(u, traj.easing)
        return cubic_bezier_point(p0, p1, p2, p3, e)
    if traj.kind == "waypoints":
        pts = [seg.start_state.position.as_tuple()] + [
            c.as_tuple() for c in traj.control_points
        ] + [seg.end_state.position.as_tuple()]
        e = apply_easing(u, traj.easing)
        return waypoints_point(pts, e)
    if traj.kind == "arc":
        # Arc: single control point defines a quadratic-like bend, evaluated
        # as a cubic Bézier with a mirrored second control for smoothness.
        start = seg.start_state.position.as_tuple()
        end = seg.end_state.position.as_tuple()
        ctrl = traj.control_points[0].as_tuple() if traj.control_points else (
            (start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0
        )
        e = apply_easing(u, traj.easing)
        return cubic_bezier_point(start, ctrl, ctrl, end, e)
    raise ValueError(f"unknown trajectory kind {traj.kind!r}")


def segment_velocity(seg: MotionSegment, time: float) -> Vec2:
    """Velocity (scene units/second) at absolute ``time``, by finite differences.

    Boundary times return the one-sided derivative so that velocity stays
    continuous across segment borders when the plans are consistent.
    """
    if time <= seg.start_time:
        h = min(1e-3, seg.duration / 4.0)
        a = seg.start_state.position.as_tuple()
        b = segment_position(seg, seg.start_time + h)
        return scale(sub(b, a), 1.0 / h) if h > 0 else (0.0, 0.0)
    if time >= seg.end_time:
        h = min(1e-3, seg.duration / 4.0)
        a = segment_position(seg, seg.end_time - h)
        b = seg.end_state.position.as_tuple()
        return scale(sub(b, a), 1.0 / h) if h > 0 else (0.0, 0.0)
    h = min(1e-3, seg.duration / 4.0)
    a = segment_position(seg, time - h)
    b = segment_position(seg, time + h)
    # Central difference: (f(t+h) - f(t-h)) / (2h).
    return scale(sub(b, a), 1.0 / (2.0 * h))


def rotation_at(seg: MotionSegment, time: float) -> float:
    """Rotation (degrees, clockwise) at absolute ``time``, shortest-arc lerp."""
    if time <= seg.start_time:
        return seg.start_state.rotation
    if time >= seg.end_time:
        return seg.end_state.rotation
    from .vectors import angle_lerp

    u = (time - seg.start_time) / seg.duration
    e = apply_easing(u, seg.trajectory.easing)
    return angle_lerp(seg.start_state.rotation, seg.end_state.rotation, e)


def enforce_max_speed(
    start: Vec2, end: Vec2, duration: float, max_speed: float
) -> float:
    """Return the minimum duration needed to travel start->end at ``max_speed``.

    Used by the planner to reject or rescale motion that would exceed the
    speed constraint of a segment.
    """
    dist = math.hypot(end[0] - start[0], end[1] - start[1])
    if max_speed <= 0:
        raise ValueError("max_speed must be positive")
    if dist < EPS:
        return 0.0
    return dist / max_speed


def segment_meets_speed_limit(seg: MotionSegment, samples: int = 64) -> bool:
    """Check sampled speed against the segment constraint (approximate)."""
    limit = seg.trajectory.max_speed or seg.constraints.max_speed
    for i in range(samples + 1):
        t = seg.start_time + seg.duration * i / samples
        v = segment_velocity(seg, t)
        if math.hypot(v[0], v[1]) > limit + 1e-6:
            return False
    return True


def sample_trajectory(
    seg: MotionSegment, fps: int
) -> tuple[list[float], list[Vec2]]:
    """Sample positions at a fixed ``fps`` across the segment (inclusive ends)."""
    if fps < 1:
        raise ValueError("fps must be >= 1")
    n = max(1, int(round(seg.duration * fps)))
    times: list[float] = []
    pts: list[Vec2] = []
    for i in range(n + 1):
        t = seg.start_time + seg.duration * i / n
        times.append(round(t, 9))
        pts.append(segment_position(seg, t))
    return times, pts


def turn_towards(current: Vec2, target: Vec2) -> float:
    """Compute the rotation (degrees) to face ``target`` from ``current``."""
    return angle_degrees(current, target)


def stop_at(
    start: Vec2, stop: Vec2, final: Vec2, t_stop: float
) -> tuple[Vec2, Vec2, float]:
    """Plan a path start->stop (arrive by ``t_stop``, come to rest) then stop->final.

    Returns the ``(stop, final, t_stop)`` triple clamped to valid ranges;
    the caller builds two segments so the character actually decelerates to
    zero at ``stop`` before continuing.  ``t_stop`` must be positive.
    """
    if t_stop <= 0:
        raise ValueError("t_stop must be positive")
    return (stop, final, t_stop)


def approximate_acceleration(
    times: Sequence[float], velocities: Sequence[Vec2]
) -> list[float]:
    """Magnitude of acceleration at each sample (scene units/second^2)."""
    if len(times) != len(velocities) or len(times) < 2:
        raise ValueError("times and velocities must have the same length >= 2")
    acc: list[float] = []
    for i in range(len(times)):
        if i == 0:
            dt = times[1] - times[0]
            dv = sub(velocities[1], velocities[0])
        elif i == len(times) - 1:
            dt = times[-1] - times[-2]
            dv = sub(velocities[-1], velocities[-2])
        else:
            dt = times[i + 1] - times[i - 1]
            dv = sub(velocities[i + 1], velocities[i - 1])
        acc.append(math.hypot(dv[0], dv[1]) / dt if dt > 0 else 0.0)
    return acc


def continuity_gap(
    prev_end: Vec2, next_start: Vec2, tolerance: float = 1e-6
) -> Optional[float]:
    """Distance between the end of one segment and the start of the next.

    Returns ``None`` when within tolerance; otherwise the gap magnitude —
    a non-None value indicates a position jump (trajectory discontinuity).
    """
    gap = math.hypot(next_start[0] - prev_end[0], next_start[1] - prev_end[1])
    if gap <= tolerance:
        return None
    return gap
