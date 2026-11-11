"""test_trajectories.py — deterministic trajectory evaluation, continuity,
velocity/acceleration."""

from __future__ import annotations

import math

import pytest

from animation_lab.models import (
    CharacterState,
    MotionSegment,
    TrajectorySpec,
    Vec2Field,
)
from animation_lab.trajectories import (
    approximate_acceleration,
    continuity_gap,
    cubic_bezier_point,
    enforce_max_speed,
    sample_trajectory,
    segment_meets_speed_limit,
    segment_position,
    segment_velocity,
    turn_towards,
    waypoints_point,
)


def _state(cid: str, pos: tuple[float, float], rot: float = 0.0) -> CharacterState:
    return CharacterState(
        character_id=cid, position=Vec2Field(x=pos[0], y=pos[1]), rotation=rot
    )


def _seg(
    t0: float, t1: float, p0: tuple[float, float], p1: tuple[float, float],
    traj: TrajectorySpec | None = None, cid: str = "c",
) -> MotionSegment:
    return MotionSegment(
        id="s1", character_id=cid, action_type="walk", start_time=t0,
        end_time=t1, start_state=_state(cid, p0), end_state=_state(cid, p1),
        trajectory=traj or TrajectorySpec(),
    )


def test_segment_position_deterministic():
    seg = _seg(0, 2, (0.1, 0.2), (0.6, 0.8))
    a = segment_position(seg, 1.0)
    b = segment_position(seg, 1.0)
    assert a == b
    assert a[0] == pytest.approx(0.35)
    assert a[1] == pytest.approx(0.5)


def test_segment_endpoints_exact():
    seg = _seg(1, 3, (0.2, 0.3), (0.7, 0.9))
    assert segment_position(seg, 1.0) == (0.2, 0.3)
    assert segment_position(seg, 3.0) == (0.7, 0.9)
    assert segment_position(seg, 0.0) == (0.2, 0.3)
    assert segment_position(seg, 5.0) == (0.7, 0.9)


def test_bezier_stays_within_hull_and_hits_ends():
    p0, p1, p2, p3 = (0, 0), (0.2, 1.0), (0.8, 1.0), (1.0, 0.0)
    assert cubic_bezier_point(p0, p1, p2, p3, 0.0) == p0
    assert cubic_bezier_point(p0, p1, p2, p3, 1.0) == p3
    mid = cubic_bezier_point(p0, p1, p2, p3, 0.5)
    assert 0 <= mid[0] <= 1 and 0 <= mid[1] <= 1


def test_waypoints_passes_through_points():
    pts = [(0, 0), (0.5, 0.5), (1, 0)]
    assert waypoints_point(pts, 0.0) == pts[0]
    assert waypoints_point(pts, 0.5) == pts[1]
    assert waypoints_point(pts, 1.0) == pts[2]


def test_velocity_zero_at_hold():
    seg = _seg(0, 1, (0.4, 0.4), (0.4, 0.4),
               traj=TrajectorySpec(kind="hold"))
    v = segment_velocity(seg, 0.5)
    assert math.hypot(v[0], v[1]) == pytest.approx(0.0, abs=1e-6)


def test_velocity_direction_linear():
    seg = _seg(0, 2, (0.0, 0.0), (0.2, 0.0))
    v = segment_velocity(seg, 1.0)
    assert v[0] > 0 and v[1] == pytest.approx(0.0, abs=1e-6)


def test_speed_limit_respected_with_duration():
    # 0.4 units in 1 s -> 0.4 u/s < 0.5 limit: OK
    seg = _seg(0, 1, (0.0, 0.0), (0.4, 0.0))
    assert segment_meets_speed_limit(seg)
    # same distance in 0.5 s -> 0.8 u/s > 0.5 limit: violation
    fast = _seg(0, 0.5, (0.0, 0.0), (0.4, 0.0))
    assert not segment_meets_speed_limit(fast)


def test_enforce_max_speed():
    assert enforce_max_speed((0, 0), (0.5, 0), 1.0, 0.5) == pytest.approx(1.0)
    assert enforce_max_speed((0, 0), (0.5, 0), 1.0, 1.0) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        enforce_max_speed((0, 0), (1, 0), 1.0, 0.0)


def test_sample_trajectory_includes_ends():
    seg = _seg(0, 2, (0.0, 0.0), (0.4, 0.4))
    times, pts = sample_trajectory(seg, fps=10)
    assert len(times) == len(pts)
    assert pts[0] == (0.0, 0.0)
    assert pts[-1] == (0.4, 0.4)
    assert times == sorted(times)


def test_continuity_gap():
    assert continuity_gap((0.1, 0.1), (0.1, 0.1)) is None
    gap = continuity_gap((0.1, 0.1), (0.5, 0.1))
    assert gap is not None and gap == pytest.approx(0.4)


def test_acceleration_estimation():
    times = [0.0, 1.0, 2.0, 3.0]
    vels = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
    acc = approximate_acceleration(times, vels)
    assert all(a == pytest.approx(1.0) for a in acc)
    with pytest.raises(ValueError):
        approximate_acceleration([0.0], [(0, 0)])


def test_turn_towards_down_is_90():
    assert turn_towards((0.5, 0.5), (0.5, 0.9)) == pytest.approx(90.0)


def test_negative_duration_rejected():
    with pytest.raises(ValueError):
        _seg(2, 1, (0, 0), (1, 1))
