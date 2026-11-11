"""pose_system.py — key pose library, pose transitions and validation.

Builds on :mod:`animation_lab.rig_2d`.  Provides a small library of named
poses for the demo humanoid, shortest-arc interpolation between poses,
mirroring, and validation of joint angles against limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .interpolation import interpolate_joint_angles
from .models import Pose, Vec2Field
from .rig_2d import Rig2D, demo_humanoid_rig
from .vectors import wrap_angle_deg


@dataclass
class PoseLibrary:
    """Named poses for one character, plus a shared rig."""

    rig: Rig2D = field(default_factory=demo_humanoid_rig)
    poses: dict[str, Pose] = field(default_factory=dict)
    character_id: str = "character"

    def register(self, pose: Pose) -> None:
        if pose.character_id != self.character_id:
            raise ValueError(
                f"pose {pose.pose_id!r} is for {pose.character_id!r}, "
                f"library is for {self.character_id!r}"
            )
        self.poses[pose.pose_id] = pose

    def get(self, pose_id: str) -> Pose:
        try:
            return self.poses[pose_id]
        except KeyError:
            raise KeyError(
                f"unknown pose {pose_id!r}; available: {sorted(self.poses)}"
            ) from None

    # ------------------------------------------------------------------
    def validate_pose(self, pose: Pose) -> list[str]:
        """Return joints whose angles violate their limits in ``pose``."""
        return self.rig.validate_limits(pose.joint_transforms)

    def mirror_pose(self, pose: Pose, new_id: str) -> Pose:
        """Mirror a pose across the vertical axis (r <-> l naming swapped)."""
        swap = {
            "shoulder_r": "shoulder_l", "elbow_r": "elbow_l", "hand_r": "hand_l",
            "shoulder_l": "shoulder_r", "elbow_l": "elbow_r", "hand_l": "hand_r",
            "hip_r": "hip_l", "knee_r": "knee_l", "foot_r": "foot_l",
            "hip_l": "hip_r", "knee_l": "knee_r", "foot_l": "foot_r",
        }
        mirrored: dict[str, float] = {}
        for jid, angle in pose.joint_transforms.items():
            mirrored[swap.get(jid, jid)] = wrap_angle_deg(-angle)
        return Pose(
            pose_id=new_id,
            character_id=pose.character_id,
            joint_transforms=mirrored,
            root_position=pose.root_position,
            root_rotation=-pose.root_rotation,
            tags=pose.tags + ("mirrored",),
        )

    def transition(
        self, from_id: str, to_id: str, t: float
    ) -> Pose:
        """Interpolated pose at progress ``t`` in [0, 1] between two poses."""
        a, b = self.get(from_id), self.get(to_id)
        angles = interpolate_joint_angles(a.joint_transforms, b.joint_transforms, t)
        from .interpolation import interpolate_position, interpolate_rotation

        root = interpolate_position(
            a.root_position.as_tuple(), b.root_position.as_tuple(), t
        )
        safe_id = f"{from_id}_{to_id}_{int(t * 1000):03d}"
        return Pose(
            pose_id=safe_id,
            character_id=self.character_id,
            joint_transforms=angles,
            root_position=Vec2Field(x=root[0], y=root[1]),
            root_rotation=interpolate_rotation(a.root_rotation, b.root_rotation, t),
        )


# ---------------------------------------------------------------------------
# Demo pose set for the humanoid rig
# ---------------------------------------------------------------------------

def demo_pose_library(character_id: str = "character_a") -> PoseLibrary:
    """Named poses: idle, step, reach_out, hold_up, startled, sit, wave."""
    lib = PoseLibrary(rig=demo_humanoid_rig(), character_id=character_id)

    def P(pose_id: str, angles: dict[str, float], tags: tuple[str, ...] = ()) -> Pose:
        return Pose(
            pose_id=pose_id,
            character_id=character_id,
            joint_transforms=angles,
            root_position=Vec2Field(x=0.0, y=0.0),
            tags=tags,
        )

    lib.register(P("idle", {
        "shoulder_r": 5.0, "elbow_r": 8.0, "hand_r": 0.0,
        "shoulder_l": -5.0, "elbow_l": -8.0, "hand_l": 0.0,
        "knee_r": 4.0, "knee_l": -4.0,
    }, tags=("base",)))

    lib.register(P("step", {
        "shoulder_r": -10.0, "elbow_r": 15.0,
        "shoulder_l": 10.0, "elbow_l": -15.0,
        "hip_r": -20.0, "knee_r": 25.0, "hip_l": 15.0, "knee_l": -10.0,
    }, tags=("walk",)))

    lib.register(P("reach_out", {
        "shoulder_r": -75.0, "elbow_r": 10.0, "hand_r": 5.0,
        "shoulder_l": 15.0, "elbow_l": -20.0,
    }, tags=("reach",)))

    lib.register(P("hold_up", {
        "shoulder_r": -120.0, "elbow_r": -35.0, "hand_r": 10.0,
        "shoulder_l": 15.0, "elbow_l": -20.0,
    }, tags=("hold",)))

    lib.register(P("startled", {
        "torso": -8.0,
        "shoulder_r": -140.0, "elbow_r": -60.0,
        "shoulder_l": 140.0, "elbow_l": 60.0,
        "knee_r": 18.0, "knee_l": -18.0,
    }, tags=("react",)))

    lib.register(P("sit", {
        "torso": 5.0,
        "hip_r": -85.0, "knee_r": 85.0,
        "hip_l": -85.0, "knee_l": -85.0,
        "shoulder_r": 10.0, "elbow_r": 25.0,
        "shoulder_l": -10.0, "elbow_l": -25.0,
    }, tags=("sitting",)))

    lib.register(P("wave", {
        "shoulder_r": -150.0, "elbow_r": -40.0, "hand_r": 20.0,
        "shoulder_l": -5.0, "elbow_l": -8.0,
    }, tags=("gesture",)))

    return lib
