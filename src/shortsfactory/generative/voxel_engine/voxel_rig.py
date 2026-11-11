"""Voxel engine — rig structure (quality v1).

Defines the hierarchy of empties that acts as the hero's skeleton.  Each limb
has an *empty* placed at its joint; the mesh is a child of that empty and is
offset downward so rotating the empty swings the limb around the joint.

Proportions (quality reset): the root sits at hip height above the ground so the
legs reach down to the floor (feet at z=0).  Head/torso/limb lengths are tuned
for a clear silhouette and readable poses on a small phone screen.
"""

from __future__ import annotations

#: Height of the hip (root) above the ground plane.
HIP_Z = 0.82

#: Stage-normalised world mapping (see camera/plan conventions).
STAGE_W = 8.0  # lateral width in world units
STAGE_D = 6.0  # depth span in world units

#: The named hierarchy (empty name, parent, local offset from parent).
RIG_PARTS: tuple[str, ...] = (
    "root",
    "torso",
    "head",
    "arm_l",
    "arm_r",
    "leg_l",
    "leg_r",
)

#: (part, parent, local offset (x,y,z)) — empties (offsets relative to parent).
RIG_HIERARCHY: tuple[tuple[str, str, tuple[float, float, float]], ...] = (
    ("root", None, (0.0, 0.0, 0.0)),
    ("torso", "root", (0.0, 0.0, 0.45)),
    ("head", "torso", (0.0, 0.0, 0.65)),
    ("arm_l", "torso", (-0.38, 0.0, 0.42)),
    ("arm_r", "torso", (0.38, 0.0, 0.42)),
    ("leg_l", "root", (-0.17, 0.0, 0.0)),
    ("leg_r", "root", (0.17, 0.0, 0.0)),
)

#: Mesh box dims for each part (w, d, l) — children of the joint empty.
RIG_PART_BOXES: dict[str, tuple[float, float, float]] = {
    "torso": (0.70, 0.42, 0.60),
    "head": (0.36, 0.36, 0.36),
    "arm_l": (0.20, 0.20, 0.60),
    "arm_r": (0.20, 0.20, 0.60),
    "leg_l": (0.24, 0.24, 0.82),
    "leg_r": (0.24, 0.24, 0.82),
}

#: Chest target height for the camera (local to root).
CHEST_HEIGHT = 0.80
