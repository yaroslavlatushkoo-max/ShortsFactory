"""Voxel engine — VoxelHero character builder (quality v1).

Returns a Blender-side source fragment that constructs an original cubic
humanoid (head, torso, two arms, two legs, eyes/brows/mouth) from box meshes
parented to a hierarchy of joint empties (see ``voxel_rig``).  The rig sits at
hip height so the feet rest on the ground; the face is simple but expressive.
"""

from __future__ import annotations

from .voxel_rig import HIP_Z, RIG_PART_BOXES

#: (name, parent_kind, local offset).  arm -> torso, leg -> root.
_LIMB_JOINTS = (
    ("arm_l", "torso", (-0.40, 0.0, 0.44)),
    ("arm_r", "torso", (0.40, 0.0, 0.44)),
    ("leg_l", "root", (-0.18, 0.0, 0.0)),
    ("leg_r", "root", (0.18, 0.0, 0.0)),
)


def build_hero_source(char_name: str = "VoxelHero", px: float = 0.0, py: float = 3.0) -> str:
    """Source chunk that builds the hero and stores handles in ``HERO``."""
    lines = [
        "# ---------------- VoxelHero (quality v1) ----------------",
        "HERO = {}",
        f"HERO['root'] = bpy.data.objects.new('{char_name}_root', None)",
        "bpy.context.collection.objects.link(HERO['root'])",
        f"HERO['root'].location = ({px}, {py}, {HIP_Z})",
        "torso = bpy.data.objects.new('torso', None)",
        "bpy.context.collection.objects.link(torso)",
        "torso.parent = HERO['root']",
        "torso.location = (0.0, 0.0, 0.45)",
        "HERO['torso'] = torso",
        "HERO['torso_mesh'] = add_box(torso, 'torso_mesh', 0.70, 0.42, 0.60, _mat('hero_shirt'), (0,0,0))",
        "head = bpy.data.objects.new('head', None)",
        "bpy.context.collection.objects.link(head)",
        "head.parent = torso",
        "head.location = (0.0, 0.0, 0.66)",
        "HERO['head'] = head",
        "HERO['head_mesh'] = add_cube('head_mesh', 0.36, (0,0,0), _mat('hero_skin'), head)",
        "# face (front = +Y): eyes, brows, mouth — larger for readability",
        "add_cube('eye_l', 0.075, (-0.09, 0.19, 0.04), _mat('hero_eyes'), head)",
        "add_cube('eye_r', 0.075, (0.09, 0.19, 0.04), _mat('hero_eyes'), head)",
        "add_cube('brow_l', 0.085, (-0.09, 0.20, 0.11), _mat('hero_hair'), head)",
        "add_cube('brow_r', 0.085, (0.09, 0.20, 0.11), _mat('hero_hair'), head)",
        "# mouth: a thin thread — small segments along the smile arc (lip colour)",
        "add_cube('mouth_l', 0.035, (-0.055, 0.205, -0.030), _mat('hero_mouth'), head)",
        "add_cube('mouth_sl', 0.035, (-0.030, 0.210, -0.050), _mat('hero_mouth'), head)",
        "add_cube('mouth_c', 0.035, (0.000, 0.212, -0.065), _mat('hero_mouth'), head)",
        "add_cube('mouth_sr', 0.035, (0.030, 0.210, -0.050), _mat('hero_mouth'), head)",
        "add_cube('mouth_r', 0.035, (0.055, 0.205, -0.030), _mat('hero_mouth'), head)",
    ]
    for pname, parent_kind, offset in _LIMB_JOINTS:
        w, d, ln = RIG_PART_BOXES[pname]
        off = f"({offset[0]}, {offset[1]}, {offset[2]})"
        lines.append(f"j = bpy.data.objects.new('{pname}', None)")
        lines.append("bpy.context.collection.objects.link(j)")
        lines.append(f"j.parent = HERO['{parent_kind}']")
        lines.append(f"j.location = {off}")
        lines.append(f"HERO['{pname}'] = j")
        key = "hero_shirt" if pname.startswith("arm") else "hero_pants"
        lines.append(
            f"add_box(j, '{pname}_mesh', {w}, {d}, {ln}, _mat('{key}'), (0,0,0))"
        )
    lines += [
        "# feet on the ground (shoe at the bottom of the leg)",
        "add_cube('shoe_l', 0.26, (-0.18, 0.06, -0.82), _mat('hero_shoes'), HERO['leg_l'])",
        "add_cube('shoe_r', 0.26, (0.18, 0.06, -0.82), _mat('hero_shoes'), HERO['leg_r'])",
        "add_box(HERO['torso'], 'belt', 0.74, 0.44, 0.12, _mat('hero_trim'), (0,0,-0.35))",
    ]
    return "\n".join(lines)
