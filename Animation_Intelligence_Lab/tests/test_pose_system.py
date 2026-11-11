"""test_pose_system.py — pose library, limits, mirroring, transitions."""

from __future__ import annotations

import pytest

from animation_lab.models import Pose, RotationLimits
from animation_lab.pose_system import demo_pose_library
from animation_lab.rig_2d import (
    arm_reach,
    demo_humanoid_rig,
    hand_world_position,
)


def test_library_loads_all_demo_poses():
    lib = demo_pose_library()
    for name in ("idle", "step", "reach_out", "hold_up", "startled", "sit",
                 "wave"):
        assert name in lib.poses


def test_demo_poses_respect_joint_limits():
    lib = demo_pose_library()
    violations: list[str] = []
    for pose in lib.poses.values():
        violations += [f"{pose.pose_id}:{j}" for j in lib.validate_pose(pose)]
    assert violations == []


def test_pose_transition_midpoints():
    lib = demo_pose_library()
    mid = lib.transition("idle", "reach_out", 0.5)
    ang_r_idle = lib.get("idle").joint_transforms["shoulder_r"]
    ang_r_reach = lib.get("reach_out").joint_transforms["shoulder_r"]
    assert mid.joint_transforms["shoulder_r"] == pytest.approx(
        (ang_r_idle + ang_r_reach) / 2
    )


def test_mirror_pose_swaps_sides():
    lib = demo_pose_library()
    mirrored = lib.mirror_pose(lib.get("reach_out"), "reach_out_m")
    assert mirrored.joint_transforms["shoulder_l"] == pytest.approx(
        -lib.get("reach_out").joint_transforms["shoulder_r"]
    )
    assert "mirrored" in mirrored.tags


def test_unknown_pose_lookup_raises():
    lib = demo_pose_library()
    with pytest.raises(KeyError):
        lib.get("does_not_exist")


def test_validate_pose_reports_violations():
    lib = demo_pose_library()
    bad = Pose(
        pose_id="bad", character_id=lib.character_id,
        joint_transforms={"elbow_r": 179.0},  # limits are 0..150
    )
    assert "elbow_r" in lib.validate_pose(bad)


def test_rig_world_positions_hierarchy():
    rig = demo_humanoid_rig()
    world = rig.world_positions()
    # head must be above (smaller y) the hip center
    assert world["head"][1] < world["hip_center"][1]
    # hand_r x greater than shoulder_r x (arm points outward)
    assert world["shoulder_r"][0] < world["hand_r"][0]


def test_arm_reach_positive_and_limited():
    rig = demo_humanoid_rig()
    reach = arm_reach(rig, "r")
    assert 0.1 < reach < 1.0


def test_hand_world_position_anchored_to_root():
    rig = demo_humanoid_rig()
    p = hand_world_position(rig, {}, root=(0.5, 0.5))
    q = hand_world_position(rig, {}, root=(0.6, 0.5))
    assert q[0] - p[0] == pytest.approx(0.1)


def test_rig_rejects_cycles_and_multi_roots():
    from animation_lab.models import Joint, Vec2Field

    j = lambda i, p: Joint(joint_id=i, parent_id=p,
                           local_position=Vec2Field(x=0, y=0))
    with pytest.raises(ValueError):
        from animation_lab.rig_2d import Rig2D

        Rig2D.from_joints([j("a", "b"), j("b", "a")])
    with pytest.raises(ValueError):
        from animation_lab.rig_2d import Rig2D

        Rig2D.from_joints([j("a", None), j("b", None)])


def test_limits_validation():
    with pytest.raises(ValueError):
        RotationLimits(min_deg=90, max_deg=0)
