"""rig_2d.py — hierarchical 2D skeleton rig with forward kinematics.

A simplified 2D skeletal model: joints form a tree, each joint stores its
local position (relative to the parent, normalized character units) and
local rotation.  World positions are computed by walking down the chain.

This is a *kinematic diagnostic* rig, not a physical human body simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import Joint, Pose, RotationLimits, Vec2Field
from .vectors import (
    EPS,
    Vec2,
    add,
    rotate_deg,
    scale,
    sub,
    wrap_angle_deg,
)


@dataclass
class Rig2D:
    """A 2D skeleton: named joints with parent hierarchy."""

    root_id: str
    joints: dict[str, Joint]
    order: list[str] = field(default_factory=list)  # topological order

    # ------------------------------------------------------------------
    @classmethod
    def from_joints(cls, joints: list[Joint]) -> "Rig2D":
        if not joints:
            raise ValueError("rig needs at least one joint")
        by_id = {j.joint_id: j for j in joints}
        if len(by_id) != len(joints):
            raise ValueError("duplicate joint ids in rig")
        roots = [j for j in joints if j.parent_id is None]
        if len(roots) != 1:
            raise ValueError(
                f"rig must have exactly one root joint, got {len(roots)}"
            )
        root = roots[0]
        # Topological order (parents before children) + cycle detection.
        order: list[str] = []
        visited: set[str] = set()

        def visit(jid: str, chain: tuple[str, ...]) -> None:
            if jid in chain:
                raise ValueError(f"cycle in rig hierarchy at {jid!r}")
            if jid in visited:
                return
            joint = by_id[jid]
            if joint.parent_id is not None:
                if joint.parent_id not in by_id:
                    raise ValueError(
                        f"joint {jid!r}: unknown parent {joint.parent_id!r}"
                    )
                visit(joint.parent_id, chain + (jid,))
            visited.add(jid)
            order.append(jid)

        for j in joints:
            visit(j.joint_id, ())
        return cls(root_id=root.joint_id, joints=by_id, order=order)

    # ------------------------------------------------------------------
    def world_positions(self, rotations: Optional[dict[str, float]] = None) -> dict[str, Vec2]:
        """Forward kinematics: world position of every joint.

        ``rotations`` optionally overrides each joint's local rotation
        (degrees).  A joint's world transform = parent world transform
        ∘ (local position rotated into parent frame, plus local rotation).
        """
        rotations = rotations or {}
        world: dict[str, Vec2] = {}
        angles: dict[str, float] = {}
        for jid in self.order:
            j = self.joints[jid]
            local_rot = wrap_angle_deg(rotations.get(jid, j.rotation))
            if j.parent_id is None:
                world[jid] = j.local_position.as_tuple()
                angles[jid] = local_rot
            else:
                parent = self.joints[j.parent_id]
                parent_angle = angles[j.parent_id]
                # Local offset expressed in the parent's rotated frame.
                offset = rotate_deg(j.local_position.as_tuple(), parent_angle)
                world[jid] = add(world[j.parent_id], offset)
                angles[jid] = wrap_angle_deg(parent_angle + local_rot)
        return world

    def validate_limits(self, rotations: Optional[dict[str, float]] = None) -> list[str]:
        """Return joints whose (raw, unwrapped) rotation is outside limits."""
        rotations = rotations or {}
        bad: list[str] = []
        for jid, j in self.joints.items():
            angle = rotations.get(jid, j.rotation)
            if not j.limits.contains(angle):
                bad.append(jid)
        return bad

    def clamp_to_limits(self, rotations: dict[str, float]) -> dict[str, float]:
        """Clamp proposed rotations into each joint's limits (raw degrees)."""
        out: dict[str, float] = {}
        for jid, angle in rotations.items():
            if jid not in self.joints:
                raise ValueError(f"unknown joint {jid!r}")
            lim = self.joints[jid].limits
            out[jid] = min(lim.max_deg, max(lim.min_deg, angle))
        return out

    def mirror(self) -> "Rig2D":
        """Mirror the rig across the vertical axis (left <-> right).

        Local positions flip x, rotations negate.
        """
        mirrored: list[Joint] = []
        for j in self.joints.values():
            mirrored.append(
                Joint(
                    joint_id=j.joint_id,
                    parent_id=j.parent_id,
                    local_position=Vec2Field(
                        x=-j.local_position.x, y=j.local_position.y
                    ),
                    rotation=-j.rotation,
                    limits=RotationLimits(
                        min_deg=-j.limits.max_deg, max_deg=-j.limits.min_deg
                    ),
                    length=j.length,
                )
            )
        return Rig2D.from_joints(mirrored)


# ---------------------------------------------------------------------------
# Demonstration humanoid rig (diagnostic, not artistic)
# ---------------------------------------------------------------------------

def demo_humanoid_rig() -> Rig2D:
    """A diagnostic humanoid: head, torso, shoulders, elbows, hands,
    hips, knees, feet.  Positions in normalized character units (the
    character occupies roughly the unit square; y grows downward)."""
    J = Joint

    def RL(lo: float, hi: float) -> RotationLimits:
        return RotationLimits(min_deg=lo, max_deg=hi)

    joints = [
        J(joint_id="hip_center", parent_id=None,
          local_position=Vec2Field(x=0.5, y=0.52), limits=RL(-30, 30), length=0.30),
        J(joint_id="torso", parent_id="hip_center",
          local_position=Vec2Field(x=0.0, y=-0.18), limits=RL(-25, 25), length=0.18),
        J(joint_id="neck", parent_id="torso",
          local_position=Vec2Field(x=0.0, y=-0.18), limits=RL(-45, 45), length=0.06),
        J(joint_id="head", parent_id="neck",
          local_position=Vec2Field(x=0.0, y=-0.06), limits=RL(-30, 30), length=0.10),
        # Right side of screen.
        J(joint_id="shoulder_r", parent_id="torso",
          local_position=Vec2Field(x=0.09, y=-0.13), limits=RL(-170, 60), length=0.14),
        J(joint_id="elbow_r", parent_id="shoulder_r",
          local_position=Vec2Field(x=0.10, y=0.02), limits=RL(-150, 150), length=0.13),
        J(joint_id="hand_r", parent_id="elbow_r",
          local_position=Vec2Field(x=0.10, y=0.02), limits=RL(-90, 90), length=0.05),
        J(joint_id="shoulder_l", parent_id="torso",
          local_position=Vec2Field(x=-0.09, y=-0.13), limits=RL(-60, 170), length=0.14),
        J(joint_id="elbow_l", parent_id="shoulder_l",
          local_position=Vec2Field(x=-0.10, y=0.02), limits=RL(-150, 150), length=0.13),
        J(joint_id="hand_l", parent_id="elbow_l",
          local_position=Vec2Field(x=-0.10, y=0.02), limits=RL(-90, 90), length=0.05),
        J(joint_id="hip_r", parent_id="hip_center",
          local_position=Vec2Field(x=0.06, y=0.04), limits=RL(-100, 100), length=0.15),
        J(joint_id="knee_r", parent_id="hip_r",
          local_position=Vec2Field(x=0.01, y=0.15), limits=RL(0, 140), length=0.14),
        J(joint_id="foot_r", parent_id="knee_r",
          local_position=Vec2Field(x=0.0, y=0.14), limits=RL(-45, 45), length=None),
        J(joint_id="hip_l", parent_id="hip_center",
          local_position=Vec2Field(x=-0.06, y=0.04), limits=RL(-100, 100), length=0.15),
        J(joint_id="knee_l", parent_id="hip_l",
          local_position=Vec2Field(x=-0.01, y=0.15), limits=RL(-140, 0), length=0.14),
        J(joint_id="foot_l", parent_id="knee_l",
          local_position=Vec2Field(x=0.0, y=0.14), limits=RL(-45, 45), length=None),
    ]
    return Rig2D.from_joints(joints)


#: Named rig anchors usable as prop attachment points.
ATTACHMENT_POINTS: tuple[str, ...] = ("hand_r", "hand_l", "head", "torso")


def hand_world_position(
    rig: Rig2D,
    rotations: dict[str, float],
    root: Vec2,
    hand: str = "hand_r",
    character_scale: float = 1.0,
) -> Vec2:
    """World (scene) position of a hand anchor given root placement.

    ``root`` is in normalized scene coordinates; rig-local coordinates are
    scaled by ``character_scale`` before offsetting.  Approximate arm
    reach = length of the shoulder->elbow->hand chain times scale.
    """
    world = rig.world_positions(rotations)
    if hand not in world:
        raise ValueError(f"unknown attachment point {hand!r}")
    local = world[hand]
    # Rig local coords are centered around ~0.5; recenter then offset root.
    centered = sub(local, (0.5, 0.5))
    return add(root, scale(centered, character_scale))


def arm_reach(rig: Rig2D, side: str = "r") -> float:
    """Approximate maximum arm reach in normalized character units."""
    total = 0.0
    for jid in (f"shoulder_{side}", f"elbow_{side}"):
        j = rig.joints.get(jid)
        if j is not None and j.length is not None:
            total += j.length
    hand = rig.joints.get(f"hand_{side}")
    if hand is not None and hand.length is not None:
        total += hand.length
    if total < EPS:
        raise ValueError(f"rig has no measurable arm chain for side {side!r}")
    return total
