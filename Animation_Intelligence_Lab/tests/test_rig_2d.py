"""test_rig_2d.py — forward kinematics, limits, mirroring, anchors."""

from __future__ import annotations

import pytest

from animation_lab.models import Joint, Vec2Field
from animation_lab.rig_2d import (
    ATTACHMENT_POINTS,
    Rig2D,
    arm_reach,
    demo_humanoid_rig,
    hand_world_position,
)


def _joint(jid: str, parent: str | None, x: float, y: float, rot: float = 0.0):
    return Joint(joint_id=jid, parent_id=parent,
                 local_position=Vec2Field(x=x, y=y), rotation=rot)


def test_chain_forward_kinematics():
    rig = Rig2D.from_joints([
        _joint("root", None, 0.5, 0.5),
        _joint("child", "root", 0.1, 0.0),
        _joint("grand", "child", 0.1, 0.0),
    ])
    world = rig.world_positions()
    assert world["root"] == (0.5, 0.5)
    assert world["child"] == (0.6, 0.5)
    assert world["grand"] == (0.7, 0.5)


def test_rotation_propagates_to_children():
    rig = Rig2D.from_joints([
        _joint("root", None, 0.5, 0.5),
        _joint("child", "root", 0.1, 0.0),
        _joint("grand", "child", 0.1, 0.0),
    ])
    world = rig.world_positions(rotations={"root": 90.0})
    # child rotated 90 deg (screen: down) -> (0.5, 0.6)
    assert world["child"][1] == pytest.approx(0.6)
    assert world["grand"][1] == pytest.approx(0.7)


def test_joint_limits_enforced():
    rig = Rig2D.from_joints([
        _joint("root", None, 0.5, 0.5),
        _joint("arm", "root", 0.1, 0.0),
    ])
    assert rig.validate_limits({"arm": 500.0}) == ["arm"]
    clamped = rig.clamp_to_limits({"arm": 500.0})
    assert clamped["arm"] == rig.joints["arm"].limits.max_deg
    with pytest.raises(ValueError):
        rig.clamp_to_limits({"unknown": 1.0})


def test_rig_requires_single_root():
    from animation_lab.rig_2d import Rig2D

    # Two joints without parents -> two roots -> rejected.
    with pytest.raises(ValueError):
        Rig2D.from_joints([
            _joint("a", None, 0.1, 0.1), _joint("b", None, 0.2, 0.2)
        ])
    with pytest.raises(ValueError):
        Rig2D.from_joints([])


def test_mirroring_flips_x_and_rotation():
    rig = Rig2D.from_joints([
        _joint("root", None, 0.5, 0.5, rot=10.0),
        _joint("arm", "root", 0.1, 0.2, rot=25.0),
    ])
    mirrored = rig.mirror()
    assert mirrored.joints["arm"].local_position.x == pytest.approx(-0.1)
    assert mirrored.joints["arm"].rotation == pytest.approx(-25.0)


def test_demo_rig_structure():
    rig = demo_humanoid_rig()
    for jid in ("head", "torso", "shoulder_r", "elbow_r", "hand_r",
                "hip_l", "knee_l", "foot_l"):
        assert jid in rig.joints
    assert "hand_r" in ATTACHMENT_POINTS


def test_arm_reach_returns_reasonable_value():
    rig = demo_humanoid_rig()
    assert 0.0 < arm_reach(rig, "r") < 0.5


def test_hand_world_position_tracks_root():
    rig = demo_humanoid_rig()
    base = hand_world_position(rig, {}, root=(0.5, 0.5))
    shifted = hand_world_position(rig, {}, root=(0.7, 0.4))
    assert shifted[0] - base[0] == pytest.approx(0.2)
    assert shifted[1] - base[1] == pytest.approx(-0.1)
