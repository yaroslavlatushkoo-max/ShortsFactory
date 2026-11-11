"""test_interpolation.py — state and pose interpolation."""

from __future__ import annotations

import pytest

from animation_lab.interpolation import (
    interpolate_character_state,
    interpolate_joint_angles,
    interpolate_pose,
    interpolate_position,
    interpolate_rotation,
    pose_transition_times,
    rotation_delta,
)
from animation_lab.models import CharacterState, Pose, Vec2Field


def _state(pos: tuple[float, float], rot: float = 0.0) -> CharacterState:
    return CharacterState(character_id="c", position=Vec2Field(x=pos[0], y=pos[1]),
                          rotation=rot)


def test_position_interpolation():
    assert interpolate_position((0, 0), (1, 1), 0.5) == (0.5, 0.5)
    assert interpolate_position((0, 0), (1, 1), 0.0) == (0.0, 0.0)
    assert interpolate_position((0, 0), (1, 1), 1.0) == (1.0, 1.0)


def test_rotation_shortest_arc():
    assert interpolate_rotation(170.0, -170.0, 0.5) == pytest.approx(180.0)
    assert interpolate_rotation(-10.0, 10.0, 0.5) == pytest.approx(0.0)


def test_rotation_delta():
    assert rotation_delta(10.0, 350.0) == pytest.approx(-20.0)


def test_character_state_blend_fields():
    a = _state((0, 0), rot=0.0)
    b = CharacterState(
        character_id="c",
        position=Vec2Field(x=1.0, y=1.0),
        rotation=90.0,
        emotion="surprised",
    )
    mid = interpolate_character_state(a, b, 0.5)
    assert mid.position.x == pytest.approx(0.5)
    assert mid.rotation == pytest.approx(45.0)
    # discrete fields flip at t>=0.5
    assert mid.emotion == "surprised"
    early = interpolate_character_state(a, b, 0.25)
    assert early.emotion == "neutral"


def test_joint_angle_interpolation_missing_keys():
    out = interpolate_joint_angles({"a": 10.0}, {"b": 20.0}, 0.5)
    assert out["a"] == pytest.approx(5.0)
    assert out["b"] == pytest.approx(10.0)


def test_pose_interpolation_requires_same_character():
    pa = Pose(pose_id="p1", character_id="a")
    pb = Pose(pose_id="p2", character_id="b")
    with pytest.raises(ValueError):
        interpolate_pose(pa, pb, 0.5)


def test_pose_transition_times():
    ts = pose_transition_times(0.0, 0.0, duration=2.0, fps=10)
    assert len(ts) == 21
    assert ts[0] == 0.0 and ts[-1] == 2.0
    with pytest.raises(ValueError):
        pose_transition_times(0, 0, duration=0.0, fps=10)
