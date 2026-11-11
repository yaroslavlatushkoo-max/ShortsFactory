"""Mission 16 — skinned cartoon rig with continuous mesh and analytic leg IK.

Why this module exists
----------------------
Mission 16 root-cause probe (`output/_quality_investigation/mission16/probe_rootcause.py`)
showed that Armature skinning in Blender 5.2.3 **does** deform vertices
(max vertex displacement 0.88 world units for a 0.9 rad bone rotation), and that
the earlier "0.0 pixel MAE / skinning unsupported" conclusion came from a
*blank render* (no world / no lights -> empty image -> MAE identically 0).
Verified numbers: blank render pixel std 0.001, valid render std 0.066.

Therefore this module builds the real thing instead of segmented cube bodies:

* one continuous low-poly mesh per character (torso, neck, head, arms, legs,
  shoes, hair, face) with explicit, deterministic per-vertex weights —
  no auto-weights, no imported clips;
* a real Armature (19 bones) with the mesh bound through an ARMATURE modifier;
* analytic two-bone leg IK with world-space ankle targets, so a planted foot
  keeps its world position (foot lock) while the pelvis travels;
* a locomotion state machine (idle / start / walk / turn / stop / turn in
  place / reach / gesture / react / talk / interact / return) sampled into
  per-frame bone transforms;
* a Blender render script + numeric diagnostics that check the *realised*
  pose against the intended one, plus foot drift and blank frames.

All animation math is pure Python, so it is unit-testable without Blender.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence

Vec3 = tuple[float, float, float]
Mat3 = tuple[Vec3, Vec3, Vec3]  # three columns (world images of local X, Y, Z)

UP: Vec3 = (0.0, 0.0, 1.0)
FWD: Vec3 = (0.0, -1.0, 0.0)
RIGHT: Vec3 = (1.0, 0.0, 0.0)

# --------------------------------------------------------------------------
# vector helpers
# --------------------------------------------------------------------------


def vadd(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vsub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vmul(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def vdot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def vlen(a: Vec3) -> float:
    return math.sqrt(vdot(a, a))


def vnorm(a: Vec3) -> Vec3:
    n = vlen(a)
    if n < 1e-12:
        return (0.0, 0.0, 1.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def vlerp(a: Vec3, b: Vec3, w: float) -> Vec3:
    return (a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w, a[2] + (b[2] - a[2]) * w)


def rot_x(v: Vec3, a: float) -> Vec3:
    c, s = math.cos(a), math.sin(a)
    return (v[0], v[1] * c - v[2] * s, v[1] * s + v[2] * c)


def rot_y(v: Vec3, a: float) -> Vec3:
    c, s = math.cos(a), math.sin(a)
    return (v[0] * c + v[2] * s, v[1], -v[0] * s + v[2] * c)


def rot_z(v: Vec3, a: float) -> Vec3:
    c, s = math.cos(a), math.sin(a)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c, v[2])


def rot_axis(v: Vec3, axis: Vec3, a: float) -> Vec3:
    """Rodrigues rotation of ``v`` around unit ``axis`` by ``a`` radians."""
    k = vnorm(axis)
    c, s = math.cos(a), math.sin(a)
    kv = vcross(k, v)
    kd = vdot(k, v)
    return (
        v[0] * c + kv[0] * s + k[0] * kd * (1.0 - c),
        v[1] * c + kv[1] * s + k[1] * kd * (1.0 - c),
        v[2] * c + kv[2] * s + k[2] * kd * (1.0 - c),
    )


def smoothstep(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3.0 - 2.0 * u)


def lerp(a: float, b: float, w: float) -> float:
    return a + (b - a) * w


# --------------------------------------------------------------------------
# skeleton
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BoneSpec:
    name: str
    head: Vec3
    tail: Vec3
    parent: str | None
    ref: str = "fwd"  # reference axis used to build the bone basis


def _mirror(head: Vec3, tail: Vec3) -> tuple[Vec3, Vec3]:
    return ((-head[0], head[1], head[2]), (-tail[0], tail[1], tail[2]))


_L_ARM = (
    BoneSpec("shoulder_L", (0.06, 0.0, 1.20), (0.195, 0.0, 1.175), "chest"),
    BoneSpec("upper_arm_L", (0.195, 0.0, 1.175), (0.245, 0.0, 0.925), "shoulder_L"),
    BoneSpec("forearm_L", (0.245, 0.0, 0.925), (0.262, 0.0, 0.715), "upper_arm_L"),
    BoneSpec("hand_L", (0.262, 0.0, 0.715), (0.268, 0.0, 0.600), "forearm_L"),
)
_L_LEG = (
    BoneSpec("thigh_L", (0.11, 0.0, 0.62), (0.115, -0.02, 0.36), "hips"),
    BoneSpec("shin_L", (0.115, -0.02, 0.36), (0.12, 0.0, 0.09), "thigh_L"),
    BoneSpec("foot_L", (0.12, 0.0, 0.09), (0.12, -0.17, 0.02), "shin_L", ref="up"),
)


def _mirror_bones(prefix_from: str, prefix_to: str, bones: Sequence[BoneSpec]) -> tuple[BoneSpec, ...]:
    out: list[BoneSpec] = []
    for b in bones:
        h, t = _mirror(b.head, b.tail)
        out.append(
            BoneSpec(
                b.name.replace(prefix_from, prefix_to),
                h,
                t,
                (b.parent.replace(prefix_from, prefix_to) if b.parent else None),
                b.ref,
            )
        )
    return tuple(out)


BONES: tuple[BoneSpec, ...] = (
    BoneSpec("hips", (0.0, 0.0, 0.62), (0.0, 0.0, 0.78), None),
    BoneSpec("spine", (0.0, 0.0, 0.78), (0.0, 0.0, 1.02), "hips"),
    BoneSpec("chest", (0.0, 0.0, 1.02), (0.0, 0.0, 1.28), "spine"),
    BoneSpec("neck", (0.0, 0.0, 1.28), (0.0, 0.0, 1.40), "chest"),
    BoneSpec("head", (0.0, 0.0, 1.40), (0.0, 0.0, 1.72), "neck"),
    # --- face: jaw + mouth corners, so visemes can actually reshape the mouth
    BoneSpec("jaw", (0.0, 0.012, 1.512), (0.0, -0.030, 1.432), "head"),
    BoneSpec("lip_l", (0.052, -0.170, 1.470), (0.096, -0.170, 1.470), "head"),
    BoneSpec("lip_r", (-0.052, -0.170, 1.470), (-0.096, -0.170, 1.470), "head"),
    # --- brows: acting channels (lift / knit) for the emotion system -------
    BoneSpec("brow_l", (0.028, -0.176, 1.606), (0.086, -0.168, 1.612), "head"),
    BoneSpec("brow_r", (-0.028, -0.176, 1.606), (-0.086, -0.168, 1.612), "head"),
    *_L_ARM,
    *_mirror_bones("_L", "_R", _L_ARM),
    *_L_LEG,
    *_mirror_bones("_L", "_R", _L_LEG),
)

BONE_BY_NAME: dict[str, BoneSpec] = {b.name: b for b in BONES}
BONE_ORDER: tuple[str, ...] = tuple(b.name for b in BONES)
BONE_LEN: dict[str, float] = {b.name: vlen(vsub(b.tail, b.head)) for b in BONES}
REST_DIR: dict[str, Vec3] = {b.name: vnorm(vsub(b.tail, b.head)) for b in BONES}

#: Hip height used by the animation system. Slightly below the rest-pose hip
#: (0.62) so the knees keep ~20 mm of slack instead of a locked, stiff leg.
HIP_Z: float = 0.585
#: Ankle height when the foot is flat on the floor.
GROUND_Z: float = 0.09


def basis_from(direction: Vec3, ref: Vec3) -> Mat3:
    """Orthonormal basis stored as three *columns* (world images of local X/Y/Z).

    Column 1 is the bone direction, so ``basis_from(d, ref) @ (0,1,0) == d``.
    """
    y = vnorm(direction)
    r = ref
    if abs(vdot(y, r)) > 0.999:
        r = UP if abs(vdot(y, UP)) < 0.999 else RIGHT
    x = vnorm(vcross(r, y))
    z = vcross(x, y)
    return (x, y, z)


def mat_mul(a: Mat3, b: Mat3) -> Mat3:
    """Matrix product for column-major 3x3 tuples."""
    return (
        (
            a[0][0] * b[0][0] + a[1][0] * b[0][1] + a[2][0] * b[0][2],
            a[0][1] * b[0][0] + a[1][1] * b[0][1] + a[2][1] * b[0][2],
            a[0][2] * b[0][0] + a[1][2] * b[0][1] + a[2][2] * b[0][2],
        ),
        (
            a[0][0] * b[1][0] + a[1][0] * b[1][1] + a[2][0] * b[1][2],
            a[0][1] * b[1][0] + a[1][1] * b[1][1] + a[2][1] * b[1][2],
            a[0][2] * b[1][0] + a[1][2] * b[1][1] + a[2][2] * b[1][2],
        ),
        (
            a[0][0] * b[2][0] + a[1][0] * b[2][1] + a[2][0] * b[2][2],
            a[0][1] * b[2][0] + a[1][1] * b[2][1] + a[2][1] * b[2][2],
            a[0][2] * b[2][0] + a[1][2] * b[2][1] + a[2][2] * b[2][2],
        ),
    )


def mat_T(a: Mat3) -> Mat3:
    return (
        (a[0][0], a[1][0], a[2][0]),
        (a[0][1], a[1][1], a[2][1]),
        (a[0][2], a[1][2], a[2][2]),
    )


def mat_vec(a: Mat3, v: Vec3) -> Vec3:
    return (
        a[0][0] * v[0] + a[1][0] * v[1] + a[2][0] * v[2],
        a[0][1] * v[0] + a[1][1] * v[1] + a[2][1] * v[2],
        a[0][2] * v[0] + a[1][2] * v[1] + a[2][2] * v[2],
    )


def mat_rows(a: Mat3) -> tuple[float, ...]:
    """Flatten to row-major 9 values (Blender ``Matrix`` row order)."""
    return (a[0][0], a[1][0], a[2][0], a[0][1], a[1][1], a[2][1], a[0][2], a[1][2], a[2][2])


_REST_BASIS: dict[str, Mat3] = {
    b.name: basis_from(REST_DIR[b.name], UP if b.ref == "up" else FWD) for b in BONES
}


# --------------------------------------------------------------------------
# character appearance (persistent identity, scene-independent)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CharacterSpec:
    key: str
    name: str
    top: tuple[float, float, float]  # hoodie / dress colour
    bottom: tuple[float, float, float]
    skin: tuple[float, float, float]
    hair: tuple[float, float, float]
    shoes: tuple[float, float, float]
    hair_style: str  # "spikes" | "bun"
    skirt: bool = False
    glasses: bool = False
    freckles: bool = False
    #: "smooth" — continuous tube/sphere body (default); "blocky" — Minecraft-like
    #: rigid boxes on the same 24-bone skeleton (see blocky_hero).
    style: str = "smooth"
    #: Optional 64x64 skin texture (PNG) in the classic Minecraft layout. When
    #: set, the blocky character samples it for the skin/shirt/pants parts via
    #: per-face UVs carried in the payload (see MeshBuilder.set_skin).
    skin_tex: str | None = None


MILO = CharacterSpec(
    key="milo",
    name="Milo",
    top=(0.09, 0.55, 0.50),        # teal hoodie
    bottom=(0.13, 0.22, 0.34),     # dark shorts
    skin=(0.90, 0.68, 0.52),
    hair=(0.13, 0.12, 0.15),
    shoes=(0.98, 0.82, 0.18),      # yellow sneakers
    hair_style="spikes",
)

NINA = CharacterSpec(
    key="nina",
    name="Nina",
    top=(0.95, 0.45, 0.16),        # orange dress
    bottom=(0.42, 0.24, 0.60),     # purple tights
    skin=(0.94, 0.72, 0.56),
    hair=(0.45, 0.20, 0.13),
    shoes=(0.28, 0.16, 0.42),
    hair_style="bun",
    skirt=True,
    glasses=True,
    freckles=True,
)

CHARACTERS: dict[str, CharacterSpec] = {"milo": MILO, "nina": NINA}


# --------------------------------------------------------------------------
# mesh construction (pure Python: verts / faces / weights / materials)
# --------------------------------------------------------------------------


@dataclass
class MeshBuilder:
    verts: list[Vec3] = field(default_factory=list)
    faces: list[tuple[int, ...]] = field(default_factory=list)
    weights: list[dict[str, float]] = field(default_factory=list)
    face_mats: list[int] = field(default_factory=list)
    materials: list[tuple[str, tuple[float, float, float]]] = field(default_factory=list)
    #: optional skin texture path (64x64 PNG) applied to marked materials
    skin_tex: str | None = None
    _skin_mat_names: list[str] = field(default_factory=list)
    #: per-loop UV (u, v) or None; only filled when a skin is present
    _loop_uvs: list[tuple[float, float] | None] = field(default_factory=list)

    def material(self, name: str, rgb: tuple[float, float, float]) -> int:
        for i, (n, _) in enumerate(self.materials):
            if n == name:
                return i
        self.materials.append((name, rgb))
        return len(self.materials) - 1

    def set_skin(self, tex: str | None) -> None:
        """Enable per-face UV painting and skin texturing for the mesh."""
        self.skin_tex = tex

    def mark_skin_mat(self, index: int) -> None:
        """Mark a material index as sampled from the skin texture."""
        if index < len(self.materials) and index not in self._skin_mat_names:
            self._skin_mat_names.append(index)

    def paint_face_uvs(self, face_index: int, uvs: Sequence[tuple[float, float]]) -> None:
        """Attach UVs (in corner order) to a face already appended to the mesh."""
        base = sum(len(f) for f in self.faces[:face_index])
        if len(self._loop_uvs) < base + len(uvs):
            self._loop_uvs.extend([None] * (base + len(uvs) - len(self._loop_uvs)))
        for i, (u, v) in enumerate(uvs):
            self._loop_uvs[base + i] = (u, v)

    def _add_vert(self, p: Vec3, w: dict[str, float]) -> int:
        self.verts.append(p)
        total = sum(w.values())
        self.weights.append({k: v / total for k, v in w.items() if v > 1e-6})
        return len(self.verts) - 1

    def add_loft(
        self,
        rings: Sequence[Sequence[Vec3]],
        ring_weights: Sequence[dict[str, float]],
        mat: int,
        *,
        cap_start: bool = True,
        cap_end: bool = True,
    ) -> None:
        """Bridge consecutive vertex rings with quads."""
        idx: list[list[int]] = []
        for ring, w in zip(rings, ring_weights):
            idx.append([self._add_vert(p, w) for p in ring])
        n = len(rings[0])
        for a, b in zip(idx, idx[1:]):
            for k in range(n):
                k2 = (k + 1) % n
                self.faces.append((a[k], a[k2], b[k2], b[k]))
                self.face_mats.append(mat)
        if cap_start:
            self.faces.append(tuple(reversed(idx[0])))
            self.face_mats.append(mat)
        if cap_end:
            self.faces.append(tuple(idx[-1]))
            self.face_mats.append(mat)

    def add_sphere(
        self,
        center: Vec3,
        radius: float,
        weights: dict[str, float],
        mat: int,
        *,
        rings: int = 8,
        sides: int = 10,
        scale: Vec3 = (1.0, 1.0, 1.0),
    ) -> None:
        lat = [(-0.5 + i / rings) * math.pi for i in range(rings + 1)]
        top = self._add_vert(vadd(center, (0.0, 0.0, radius * scale[2])), weights)
        rows: list[list[int]] = []
        for i in range(1, rings):
            phi = lat[i]
            row = []
            for k in range(sides):
                th = 2.0 * math.pi * k / sides
                p = (
                    center[0] + radius * scale[0] * math.cos(phi) * math.cos(th),
                    center[1] + radius * scale[1] * math.cos(phi) * math.sin(th),
                    center[2] + radius * scale[2] * math.sin(phi),
                )
                row.append(self._add_vert(p, weights))
            rows.append(row)
        bot = self._add_vert(vadd(center, (0.0, 0.0, -radius * scale[2])), weights)
        for k in range(sides):
            k2 = (k + 1) % sides
            self.faces.append((top, rows[0][k2], rows[0][k]))
            self.face_mats.append(mat)
            self.faces.append((bot, rows[-1][k], rows[-1][k2]))
            self.face_mats.append(mat)
        for a, b in zip(rows, rows[1:]):
            for k in range(sides):
                k2 = (k + 1) % sides
                self.faces.append((a[k], a[k2], b[k2], b[k]))
                self.face_mats.append(mat)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "verts": [[round(c, 5) for c in v] for v in self.verts],
            "faces": [list(f) for f in self.faces],
            "weights": [
                {k: round(v, 5) for k, v in w.items()} for w in self.weights
            ],
            "face_materials": self.face_mats,
            "materials": [{"name": n, "rgb": list(c)} for n, c in self.materials],
        }
        if self.skin_tex and self._skin_mat_names:
            for i, m in enumerate(payload["materials"]):
                if i in self._skin_mat_names:
                    m["tex"] = self.skin_tex
            total_loops = sum(len(f) for f in self.faces)
            if len(self._loop_uvs) < total_loops:
                self._loop_uvs.extend([None] * (total_loops - len(self._loop_uvs)))
            if any(uv is not None for uv in self._loop_uvs):
                payload["uvs"] = [
                    list(uv) if uv is not None else None for uv in self._loop_uvs
                ]
        return payload


def _ring(center: Vec3, tangent: Vec3, rx: float, ry: float, sides: int) -> list[Vec3]:
    t = vnorm(tangent)
    ref = UP if abs(vdot(t, UP)) < 0.85 else FWD
    e1 = vnorm(vcross(ref, t))
    e2 = vcross(t, e1)
    out = []
    for k in range(sides):
        a = 2.0 * math.pi * k / sides
        out.append(vadd(center, vadd(vmul(e1, math.cos(a) * rx), vmul(e2, math.sin(a) * ry))))
    return out


def _tube(
    mb: MeshBuilder,
    points: Sequence[Vec3],
    radii: Sequence[tuple[float, float]],
    weights: Sequence[dict[str, float]],
    mat: int,
    *,
    sides: int = 8,
    cap_start: bool = True,
    cap_end: bool = True,
) -> None:
    rings: list[list[Vec3]] = []
    for i, (p, r) in enumerate(zip(points, radii)):
        if i == 0:
            tang = vsub(points[1], points[0])
        elif i == len(points) - 1:
            tang = vsub(points[-1], points[-2])
        else:
            tang = vsub(points[i + 1], points[i - 1])
        rings.append(_ring(p, tang, r[0], r[1], sides))
    mb.add_loft(
        rings, weights, mat, cap_start=cap_start, cap_end=cap_end
    )


def _torus(
    mb: MeshBuilder,
    center: Vec3,
    major: float,
    minor: float,
    weights: dict[str, float],
    mat: int,
    *,
    segments: int = 12,
    sides: int = 6,
    normal: Vec3 = FWD,
) -> None:
    n = vnorm(normal)
    ref = UP if abs(vdot(n, UP)) < 0.9 else RIGHT
    e1 = vnorm(vcross(ref, n))
    e2 = vcross(n, e1)
    rings: list[list[Vec3]] = []
    for i in range(segments):
        a = 2.0 * math.pi * i / segments
        c = vadd(center, vadd(vmul(e1, math.cos(a) * major), vmul(e2, math.sin(a) * major)))
        radial = vnorm(vadd(vmul(e1, math.cos(a)), vmul(e2, math.sin(a))))
        ring = []
        for k in range(sides):
            b = 2.0 * math.pi * k / sides
            p = vadd(c, vadd(vmul(radial, math.cos(b) * minor), vmul(n, math.sin(b) * minor)))
            ring.append(p)
        rings.append(ring)
    # closed loop loft
    idx = [[mb._add_vert(p, weights) for p in ring] for ring in rings]
    for a, b in zip(idx, idx[1:] + idx[:1]):
        for k in range(sides):
            k2 = (k + 1) % sides
            mb.faces.append((a[k], a[k2], b[k2], b[k]))
            mb.face_mats.append(mat)


def build_character_mesh(spec: CharacterSpec) -> MeshBuilder:
    """Build a single continuous (multi-island) skinned body for ``spec``."""
    if spec.style == "blocky":
        from .blocky_hero import build_blocky_character_mesh

        return build_blocky_character_mesh(spec)
    mb = MeshBuilder()
    m_top = mb.material("top", spec.top)
    m_bot = mb.material("bottom", spec.bottom)
    m_skin = mb.material("skin", spec.skin)
    m_hair = mb.material("hair", spec.hair)
    m_shoe = mb.material("shoe", spec.shoes)
    m_white = mb.material("eyewhite", (0.97, 0.97, 0.96))
    m_dark = mb.material("dark", (0.06, 0.06, 0.08))
    m_glasses = mb.material("glasses", (0.10, 0.10, 0.12))

    SIDES = 10

    # ---- torso (hips -> neck base), elliptical rings -------------------
    torso_rows: list[tuple[float, float, float, dict[str, float]]] = [
        (0.60, 0.170, 0.115, {"hips": 1.0}),
        (0.70, 0.180, 0.120, {"hips": 0.75, "spine": 0.25}),
        (0.80, 0.175, 0.118, {"spine": 1.0}),
        (0.92, 0.180, 0.122, {"spine": 0.85, "chest": 0.15}),
        (1.04, 0.195, 0.130, {"chest": 1.0}),
        (1.16, 0.205, 0.135, {"chest": 1.0}),
        (1.26, 0.195, 0.128, {"chest": 0.8, "neck": 0.2}),
        (1.31, 0.130, 0.100, {"chest": 0.6, "neck": 0.4}),
    ]
    rings = [_ring((0.0, 0.0, z), UP, rx, ry, SIDES) for z, rx, ry, _ in torso_rows]
    mb.add_loft(rings, [w for *_, w in torso_rows], m_top, cap_start=True, cap_end=False)

    # ---- neck (mostly hidden by the head and the collar) ----------------
    _tube(
        mb,
        [(0.0, 0.0, 1.27), (0.0, 0.0, 1.32), (0.0, 0.0, 1.36)],
        [(0.064, 0.064), (0.060, 0.060), (0.058, 0.058)],
        [{"chest": 0.4, "neck": 0.6}, {"neck": 1.0}, {"neck": 0.5, "head": 0.5}],
        m_skin,
        sides=8,
        cap_start=False,
        cap_end=False,
    )

    # ---- head + face ---------------------------------------------------
    # Cartoon proportion: the head is deliberately oversized (0.37 wide) so the
    # character does not read as a realistic mannequin.  The face features are
    # placed on the head ellipsoid surface, slightly proud of it, so they are
    # never swallowed by the skin.
    head_c: Vec3 = (0.0, 0.0, 1.535)
    mb.add_sphere(head_c, 0.185, {"head": 1.0}, m_skin, rings=10, sides=14, scale=(1.02, 1.02, 1.05))
    # eyes: flattened discs sunk into the face, pupils slightly proud
    for sx in (-1.0, 1.0):
        mb.add_sphere(
            (0.064 * sx, -0.170, 1.556), 0.040, {"head": 1.0}, m_white,
            rings=6, sides=10, scale=(1.0, 0.33, 1.0),
        )
        mb.add_sphere(
            (0.064 * sx, -0.184, 1.554), 0.016, {"head": 1.0}, m_dark,
            rings=5, sides=8, scale=(1.0, 0.55, 1.0),
        )
        # brow
        _tube(
            mb,
            [(0.028 * sx, -0.176, 1.606), (0.086 * sx, -0.168, 1.612)],
            [(0.011, 0.009), (0.011, 0.009)],
            # each brow rides its own bone so emotions can lift / tilt them
            [{"brow_l": 1.0} if sx > 0 else {"brow_r": 1.0}] * 2,
            m_hair,
            sides=6,
        )
    # mouth: a thin thread drawn as the arc of a smile (a half-circle).  The
    # corners ride their own bones and the midpoint rides the jaw, so jaw_open
    # bends the thread downward and mouth_wide stretches it sideways: visemes
    # reshape the line instead of sliding a painted-on oval.
    m_lip = mb.material("lip", (0.52, 0.24, 0.26))
    # narrow dark cavity sitting below the thread: it only peeks out when the
    # jaw opens and the thread bends away from the skull
    mb.add_sphere(
        (0.0, -0.160, 1.466), 0.034, {"head": 1.0}, m_dark,
        rings=6, sides=10, scale=(1.0, 0.30, 0.22),
    )
    # the thread: five points along the arc, midpoint on the jaw so it drops
    # with the lower lip, ends driven by the lip-corner bones
    _tube(
        mb,
        [
            (-0.052, -0.178, 1.482),
            (-0.026, -0.190, 1.481),
            (0.0, -0.194, 1.480),
            (0.026, -0.190, 1.481),
            (0.052, -0.178, 1.482),
        ],
        [(0.0065, 0.005), (0.0065, 0.005), (0.0065, 0.005),
         (0.0065, 0.005), (0.0065, 0.005)],
        [
            {"lip_l": 0.7, "head": 0.3},
            {"jaw": 0.5, "head": 0.5},
            {"jaw": 1.0},
            {"jaw": 0.5, "head": 0.5},
            {"lip_r": 0.7, "head": 0.3},
        ],
        m_lip,
        sides=6,
    )
    for sx, corner in ((-1.0, "lip_r"), (1.0, "lip_l")):
        mb.add_sphere(
            (0.052 * sx, -0.178, 1.470), 0.011, {corner: 0.7, "head": 0.3},
            m_lip, rings=4, sides=6,
        )

    # ---- hair (cap + fringe + side masses so the crown reads as hair) ---
    mb.add_sphere(
        (0.0, 0.030, 1.550), 0.194, {"head": 1.0}, m_hair,
        rings=9, sides=14, scale=(1.02, 1.0, 1.0),
    )
    # fringe: sits proud of the skull and stops just above the brows (1.606),
    # otherwise it is swallowed by the head sphere and reads as a bald forehead
    mb.add_sphere(
        (0.0, -0.115, 1.672), 0.115, {"head": 1.0}, m_hair,
        rings=7, sides=12, scale=(1.50, 0.72, 0.52),
    )
    # temples / sideburns: pushed outside the skull (half-width 0.189)
    for sx in (-1.0, 1.0):
        mb.add_sphere(
            (0.175 * sx, 0.020, 1.575), 0.075, {"head": 1.0}, m_hair,
            rings=6, sides=10, scale=(0.45, 0.95, 0.85),
        )
        # ears, below the temple hair
        mb.add_sphere(
            (0.186 * sx, 0.015, 1.505), 0.040, {"head": 1.0}, m_skin,
            rings=5, sides=8, scale=(0.50, 1.0, 1.15),
        )
    if spec.hair_style == "spikes":
        for i, (dx, dy, h, tilt) in enumerate(
            ((-0.082, 0.02, 0.11, -0.45), (0.0, 0.03, 0.13, 0.0), (0.082, 0.02, 0.11, 0.45),
             (-0.038, -0.06, 0.10, -0.2), (0.044, -0.055, 0.09, 0.25))
        ):
            base = (dx, dy, 1.700)
            tip = (dx + math.sin(tilt) * h * 0.8, dy + 0.03, 1.700 + h)
            _tube(
                mb,
                [base, vlerp(base, tip, 0.55), tip],
                [(0.048, 0.043), (0.032, 0.030), (0.004, 0.004)],
                [{"head": 1.0}, {"head": 1.0}, {"head": 1.0}],
                m_hair,
                sides=6,
            )
    else:  # bun
        mb.add_sphere((0.0, 0.215, 1.640), 0.085, {"head": 1.0}, m_hair, rings=7, sides=10)
        for sx in (-1.0, 1.0):
            mb.add_sphere(
                (0.163 * sx, 0.01, 1.520), 0.052, {"head": 1.0}, m_hair,
                rings=6, sides=8, scale=(0.7, 0.9, 1.4),
            )
        # glasses
        for sx in (-1.0, 1.0):
            _torus(
                mb,
                (0.060 * sx, -0.182, 1.560),
                0.043,
                0.007,
                {"head": 1.0},
                m_glasses,
                segments=14,
                sides=6,
            )
        _tube(
            mb,
            [(-0.017, -0.182, 1.560), (0.017, -0.182, 1.560)],
            [(0.006, 0.006), (0.006, 0.006)],
            [{"head": 1.0}, {"head": 1.0}],
            m_glasses,
            sides=5,
        )
    if spec.freckles:
        for sx in (-1.0, 1.0):
            for i, (dy, dz) in enumerate(((-0.012, -0.004), (0.006, -0.012), (-0.004, -0.022))):
                mb.add_sphere(
                    (0.098 * sx + 0.004 * i * sx, -0.152 + dy, 1.505 + dz),
                    0.009,
                    {"head": 1.0},
                    m_glasses,
                    rings=4,
                    sides=6,
                )

    # ---- arms ----------------------------------------------------------
    for side, sx in ((("L"), 1.0), (("R"), -1.0)):
        sh = BONE_BY_NAME[f"shoulder_{side}"]
        ua = BONE_BY_NAME[f"upper_arm_{side}"]
        fa = BONE_BY_NAME[f"forearm_{side}"]
        hd = BONE_BY_NAME[f"hand_{side}"]
        pts = [
            vlerp(sh.head, sh.tail, 0.55),
            vlerp(ua.head, ua.tail, 0.45),
            ua.tail,
            vlerp(fa.head, fa.tail, 0.5),
            fa.tail,
        ]
        radii = [(0.070, 0.068), (0.062, 0.060), (0.055, 0.053), (0.048, 0.047), (0.043, 0.042)]
        wts = [
            {f"upper_arm_{side}": 1.0},
            {f"upper_arm_{side}": 1.0},
            {f"upper_arm_{side}": 0.5, f"forearm_{side}": 0.5},
            {f"forearm_{side}": 1.0},
            {f"forearm_{side}": 0.5, f"hand_{side}": 0.5},
        ]
        _tube(mb, pts, radii, wts, m_top, sides=8, cap_start=True, cap_end=False)
        hand_c = vlerp(hd.head, hd.tail, 0.55)
        mb.add_sphere(
            hand_c, 0.055, {f"hand_{side}": 1.0}, m_skin, rings=6, sides=8, scale=(1.0, 1.0, 1.15)
        )

    # ---- legs ----------------------------------------------------------
    for side, sx in (("L", 1.0), ("R", -1.0)):
        th = BONE_BY_NAME[f"thigh_{side}"]
        sn = BONE_BY_NAME[f"shin_{side}"]
        pts = [
            th.head,
            vlerp(th.head, th.tail, 0.55),
            th.tail,
            vlerp(sn.head, sn.tail, 0.5),
            sn.tail,
        ]
        radii = [(0.082, 0.080), (0.075, 0.073), (0.064, 0.062), (0.056, 0.054), (0.049, 0.048)]
        wts = [
            {f"thigh_{side}": 1.0},
            {f"thigh_{side}": 1.0},
            {f"thigh_{side}": 0.5, f"shin_{side}": 0.5},
            {f"shin_{side}": 1.0},
            {f"shin_{side}": 0.7, f"foot_{side}": 0.3},
        ]
        _tube(mb, pts, radii, wts, m_bot, sides=8, cap_start=False, cap_end=False)
        # shoe
        ax = sn.tail
        shoe_pts = [
            (ax[0], ax[1] + 0.030, 0.062),
            (ax[0], ax[1] - 0.045, 0.055),
            (ax[0], ax[1] - 0.135, 0.050),
        ]
        _tube(
            mb,
            shoe_pts,
            [(0.052, 0.050), (0.056, 0.058), (0.044, 0.048)],
            [{f"foot_{side}": 1.0}] * 3,
            m_shoe,
            sides=8,
        )

    # ---- skirt (Nina) --------------------------------------------------
    if spec.skirt:
        rows = [
            (1.02, 0.190, 0.128, {"spine": 0.5, "hips": 0.5}),
            (0.94, 0.215, 0.150, {"hips": 1.0}),
            (0.84, 0.250, 0.180, {"hips": 1.0}),
            (0.76, 0.280, 0.205, {"hips": 1.0}),
        ]
        rings = [_ring((0.0, 0.0, z), UP, rx, ry, SIDES) for z, rx, ry, _ in rows]
        mb.add_loft(rings, [w for *_, w in rows], m_top, cap_start=False, cap_end=True)

    return mb


# --------------------------------------------------------------------------
# pose parameters
# --------------------------------------------------------------------------


@dataclass
class PoseParams:
    root: Vec3 = (0.0, 0.0, HIP_Z)
    yaw: float = 0.0
    hips_pitch: float = 0.0
    spine_pitch: float = 0.0
    spine_roll: float = 0.0
    chest_pitch: float = 0.0
    chest_roll: float = 0.0
    chest_yaw: float = 0.0
    neck_pitch: float = 0.0
    neck_yaw: float = 0.0
    head_pitch: float = 0.0
    head_yaw: float = 0.0
    head_roll: float = 0.0
    arm_l_pitch: float = 0.04
    arm_l_abduct: float = 0.02
    arm_r_pitch: float = 0.04
    arm_r_abduct: float = 0.02
    elbow_l: float = 0.22
    elbow_r: float = 0.22
    wrist_l: float = 0.0
    wrist_r: float = 0.0
    ankle_l: Vec3 | None = None
    ankle_r: Vec3 | None = None
    foot_l_pitch: float = 0.0
    foot_r_pitch: float = 0.0
    foot_yaw: float = 0.0
    # --- face / viseme channels (see VISEMES) ---------------------------
    jaw_open: float = 0.0
    mouth_wide: float = 0.0
    mouth_round: float = 0.0
    brow_lift: float = 0.0
    brow_tilt: float = 0.0

    def resolved(self) -> "PoseParams":
        """Return a copy with explicit welt-space ankle targets."""
        if self.ankle_l is not None and self.ankle_r is not None:
            return self
        out = replace(self)
        f = rot_z(FWD, self.yaw)
        r = rot_z(RIGHT, self.yaw)
        base = vadd((self.root[0], self.root[1], 0.0), (0.0, 0.0, GROUND_Z))
        if out.ankle_l is None:
            out.ankle_l = vadd(vadd(base, vmul(r, 0.11)), vmul(f, 0.0))
        if out.ankle_r is None:
            out.ankle_r = vadd(vadd(base, vmul(r, -0.11)), vmul(f, 0.0))
        return out


_POSE_FIELDS = tuple(f for f in PoseParams.__dataclass_fields__ if f not in ("ankle_l", "ankle_r"))


def blend_params(a: PoseParams, b: PoseParams, w: float) -> PoseParams:
    a = a.resolved()
    b = b.resolved()
    w = min(1.0, max(0.0, w))
    kw: dict[str, Any] = {}
    for f in _POSE_FIELDS:
        if f == "root":
            kw[f] = vlerp(a.root, b.root, w)
        else:
            kw[f] = lerp(getattr(a, f), getattr(b, f), w)
    kw["ankle_l"] = vlerp(a.ankle_l, b.ankle_l, w)  # type: ignore[arg-type]
    kw["ankle_r"] = vlerp(a.ankle_r, b.ankle_r, w)  # type: ignore[arg-type]
    return PoseParams(**kw)


@dataclass
class SolvedPose:
    """World-space bone transforms for one frame."""

    heads: dict[str, Vec3]
    rots: dict[str, Mat3]

    def dirs(self) -> dict[str, Vec3]:
        return {n: vnorm(vsub(vadd(self.heads[n], vmul(mat_vec(self.rots[n], REST_DIR[n]), BONE_LEN[n])), self.heads[n])) for n in BONE_ORDER}

    def tail(self, name: str) -> Vec3:
        return vadd(self.heads[name], vmul(mat_vec(self.rots[name], REST_DIR[name]), BONE_LEN[name]))


def solve_pose(params: PoseParams) -> SolvedPose:
    """Forward kinematics for the spine/arms + analytic two-bone IK for legs.

    The torso/arm chain is solved in the character's *body frame* (forward = -Y)
    and rotated into world space by ``yaw`` afterwards; the legs solve directly
    in world space against world-space ankle targets.
    """
    p = params.resolved()
    yaw = p.yaw
    heads: dict[str, Vec3] = {}
    rots: dict[str, Mat3] = {}

    def to_world(local: Vec3) -> Vec3:
        return vadd(p.root, rot_z(local, yaw))

    def set_bone(name: str, head: Vec3, direction: Vec3) -> None:
        spec = BONE_BY_NAME[name]
        ref = rot_z(UP if spec.ref == "up" else FWD, yaw)
        heads[name] = head
        rots[name] = mat_mul(basis_from(direction, ref), mat_T(_REST_BASIS[name]))

    body_fwd = rot_z(FWD, yaw)
    body_right = rot_z(RIGHT, yaw)

    # --- spine chain (body-local accumulation) -------------------------
    pitch = p.hips_pitch
    d_hips_l = rot_axis(rot_x(UP, pitch), FWD, p.spine_roll * 0.0)
    local = (0.0, 0.0, 0.0)
    set_bone("hips", p.root, rot_z(d_hips_l, yaw))
    local = vadd(local, vmul(d_hips_l, BONE_LEN["hips"]))

    pitch += p.spine_pitch
    d_spine_l = rot_axis(rot_x(UP, pitch), FWD, p.spine_roll)
    set_bone("spine", to_world(local), rot_z(d_spine_l, yaw))
    local = vadd(local, vmul(d_spine_l, BONE_LEN["spine"]))

    pitch += p.chest_pitch
    d_chest_l = rot_axis(rot_x(UP, pitch), FWD, p.chest_roll)
    set_bone("chest", to_world(local), rot_z(d_chest_l, yaw + p.chest_yaw))
    chest_top_l = vadd(local, vmul(d_chest_l, BONE_LEN["chest"]))
    chest_top = to_world(chest_top_l)

    d_neck_l = rot_axis(rot_x(UP, pitch + p.neck_pitch), FWD, p.chest_roll * 0.6)
    set_bone("neck", chest_top, rot_z(d_neck_l, yaw + p.chest_yaw + p.neck_yaw))
    neck_top = vadd(chest_top, vmul(rot_z(d_neck_l, yaw + p.chest_yaw + p.neck_yaw), BONE_LEN["neck"]))

    d_head_l = rot_axis(rot_x(UP, pitch + p.neck_pitch + p.head_pitch), FWD, p.head_roll)
    d_head = rot_z(d_head_l, yaw + p.chest_yaw + p.neck_yaw + p.head_yaw)
    set_bone("head", neck_top, d_head)

    # --- face: jaw + mouth corners ride on the head transform -----------
    head_rot = rots["head"]
    head_rest = BONE_BY_NAME["head"].head

    def face_point(local_rest: Vec3) -> Vec3:
        return vadd(neck_top, mat_vec(head_rot, vsub(local_rest, head_rest)))

    def set_face_bone(name: str, head_world: Vec3, direction: Vec3) -> None:
        spec = BONE_BY_NAME[name]
        ref = mat_vec(head_rot, UP if spec.ref == "up" else FWD)
        heads[name] = head_world
        rots[name] = mat_mul(basis_from(direction, ref), mat_T(_REST_BASIS[name]))

    jaw = BONE_BY_NAME["jaw"]
    # positive jaw_open must drop the chin (rotate the jaw tail down/forward),
    # which is +X rotation for a character facing -Y.
    jaw_dir = rot_axis(
        mat_vec(head_rot, REST_DIR["jaw"]), mat_vec(head_rot, RIGHT), p.jaw_open
    )
    set_face_bone("jaw", face_point(jaw.head), jaw_dir)

    for side, sign in (("l", 1.0), ("r", -1.0)):
        name = f"lip_{side}"
        spec = BONE_BY_NAME[name]
        # corners spread outwards (wide) and pull in + forward (rounded)
        offset = vadd(
            vmul(mat_vec(head_rot, RIGHT), sign * (p.mouth_wide - 0.6 * p.mouth_round) * 0.02),
            vmul(mat_vec(head_rot, FWD), p.mouth_round * 0.014),
        )
        lip_dir = rot_axis(
            mat_vec(head_rot, REST_DIR[name]),
            mat_vec(head_rot, UP),
            sign * p.mouth_round * 0.35,
        )
        set_face_bone(name, vadd(face_point(spec.head), offset), lip_dir)

    # brows: lift (surprise/worry) and tilt (angry = inner ends down).  Both
    # ends of the bone move as one rigid piece, which is enough at this scale.
    for name, sign in (("brow_l", 1.0), ("brow_r", -1.0)):
        spec = BONE_BY_NAME[name]
        brow_head = vadd(
            face_point(spec.head), vmul(mat_vec(head_rot, UP), p.brow_lift)
        )
        brow_dir = rot_axis(
            mat_vec(head_rot, REST_DIR[name]),
            mat_vec(head_rot, FWD),
            sign * p.brow_tilt,
        )
        set_face_bone(name, brow_head, brow_dir)

    # --- arms (body-local swing / abduction, body-relative bend axes) ---
    chest_yaw_world = yaw + p.chest_yaw
    for side, sign, apitch, aabduct, elbow, wrist in (
        ("L", 1.0, p.arm_l_pitch, p.arm_l_abduct, p.elbow_l, p.wrist_l),
        ("R", -1.0, p.arm_r_pitch, p.arm_r_abduct, p.elbow_r, p.wrist_r),
    ):
        sh = BONE_BY_NAME[f"shoulder_{side}"]
        sh_offset_local = vsub(sh.head, (0.0, 0.0, 1.28))
        sh_head = vadd(chest_top, rot_z(rot_x(sh_offset_local, pitch * 0.0), p.chest_yaw))
        d_sh_local = vsub(sh.tail, sh.head)
        d_sh = rot_z(rot_x(d_sh_local, p.chest_pitch), chest_yaw_world)
        set_bone(f"shoulder_{side}", sh_head, d_sh)
        sh_end = vadd(sh_head, vmul(vnorm(d_sh), BONE_LEN[f"shoulder_{side}"]))

        base = REST_DIR[f"upper_arm_{side}"]
        d_up_l = rot_axis(rot_axis(base, vmul(FWD, sign), aabduct), RIGHT, apitch)
        d_up = rot_z(d_up_l, chest_yaw_world)
        set_bone(f"upper_arm_{side}", sh_end, d_up)
        elbow_pos = vadd(sh_end, vmul(vnorm(d_up), BONE_LEN[f"upper_arm_{side}"]))

        bend_axis = rot_z(RIGHT, chest_yaw_world)
        d_fore = rot_axis(vnorm(d_up), bend_axis, -elbow)
        set_bone(f"forearm_{side}", elbow_pos, d_fore)
        wrist_pos = vadd(elbow_pos, vmul(vnorm(d_fore), BONE_LEN[f"forearm_{side}"]))

        d_hand = rot_axis(vnorm(d_fore), bend_axis, -wrist)
        set_bone(f"hand_{side}", wrist_pos, d_hand)

    # --- legs: analytic 2-bone IK in world space -----------------------
    forward = body_fwd
    for side, sign, ankle, fpitch in (
        ("L", 1.0, p.ankle_l, p.foot_l_pitch),
        ("R", -1.0, p.ankle_r, p.foot_r_pitch),
    ):
        assert ankle is not None
        hip = vadd(p.root, vmul(body_right, 0.11 * sign))
        l1 = BONE_LEN[f"thigh_{side}"]
        l2 = BONE_LEN[f"shin_{side}"]
        delta = vsub(ankle, hip)
        d = vlen(delta)
        d_clamped = min(max(d, abs(l1 - l2) + 1e-4), l1 + l2 - 1e-4)
        # Если нога не дотягивается до целевой стопы (короткое бедро/голень
        # при широком шаге), НЕ тащим стопу за бедром: это выглядит как
        # проскальзывание подошвы.  При умеренном превышении (обычный шаг)
        # чуть смещаем точку бедра назад вдоль направления на стопу, оставляя
        # стопу на месте.  При экстремально недостижимой цели возвращаемся к
        # клампингу (нога не должна «взрываться»/растягиваться).
        overshoot = d - d_clamped
        hip_eff = hip
        if overshoot > 1e-9:
            u_back = vnorm(delta)
            if overshoot <= 0.12:
                hip_eff = vsub(hip, vmul(u_back, overshoot))
                ankle_eff = ankle
            else:
                ankle_eff = vadd(hip, vmul(u_back, d_clamped))
            delta = vsub(ankle_eff, hip_eff)
            d = vlen(delta)
        else:
            ankle_eff = ankle
        u = vnorm(delta)
        pole = vsub(forward, vmul(u, vdot(forward, u)))
        if vlen(pole) < 1e-5:
            pole = forward
        pole = vnorm(pole)
        a = (l1 * l1 - l2 * l2 + d_clamped * d_clamped) / (2.0 * d_clamped)
        h = math.sqrt(max(l1 * l1 - a * a, 0.0))
        knee = vadd(vadd(hip_eff, vmul(u, a)), vmul(pole, h))
        set_bone(f"thigh_{side}", hip_eff, vsub(knee, hip_eff))
        set_bone(f"shin_{side}", knee, vsub(ankle_eff, knee))
        foot_dir = rot_axis(REST_DIR[f"foot_{side}"], RIGHT, fpitch)
        set_bone(f"foot_{side}", ankle_eff, rot_z(foot_dir, yaw + p.foot_yaw))

    return SolvedPose(heads=heads, rots=rots)


# --------------------------------------------------------------------------
# gait / locomotion states
# --------------------------------------------------------------------------


def path_constant(origin: Vec3, yaw: float, speed: float, yaw_rate: float = 0.0) -> Callable[[float], tuple[Vec3, float]]:
    """Analytic root path: constant speed, optional constant yaw rate (arc)."""

    def at(t: float) -> tuple[Vec3, float]:
        y = yaw + yaw_rate * t
        if abs(yaw_rate) < 1e-6:
            return (vadd(origin, vmul(rot_z(FWD, yaw), speed * t)), y)
        # integral of a rotating heading: (Rz(w t) - I) @ J @ FWD * v / w
        jfwd = rot_z(FWD, math.pi / 2.0)
        offset = vmul(vsub(rot_z(jfwd, yaw_rate * t), jfwd), speed / yaw_rate)
        return (vadd(origin, offset), y)

    return at


@dataclass
class Gait:
    """Deterministic gait: planted feet keep an exact world position."""

    path: Callable[[float], tuple[Vec3, float]]
    speed: float
    stride: float = 0.44
    duty: float = 0.62
    lateral: float = 0.11
    lift: float = 0.075
    ground_z: float = 0.09

    @property
    def cycle(self) -> float:
        return self.stride / max(self.speed, 1e-3)

    def _plant(self, n: int, offset: float, sign: float) -> Vec3:
        t = (n - offset) * self.cycle
        pos, yaw = self.path(t)
        f = rot_z(FWD, yaw)
        r = rot_z(RIGHT, yaw)
        lead = self.duty * self.stride * 0.5
        p = vadd(pos, vadd(vmul(f, lead), vmul(r, self.lateral * sign)))
        return (p[0], p[1], self.ground_z)

    def foot(self, t: float, sign: float, offset: float) -> tuple[Vec3, float, bool, float]:
        """Return (ankle world position, foot yaw, planted, swing weight)."""
        ph_raw = t / self.cycle + offset
        n = math.floor(ph_raw)
        phase = ph_raw - n
        plant_t = (n - offset) * self.cycle
        if phase < self.duty:
            return self._plant(n, offset, sign), self.path(plant_t)[1], True, 0.0
        w = (phase - self.duty) / (1.0 - self.duty)
        p0 = self._plant(n, offset, sign)
        p1 = self._plant(n + 1, offset, sign)
        p = vlerp(p0, p1, smoothstep(w))
        p = (p[0], p[1], p[2] + self.lift * math.sin(math.pi * w))
        return p, self.path(t)[1], False, w


def _walk_params(gait: Gait, t: float) -> PoseParams:
    root, yaw = gait.path(t)
    al, yl, pl, wl = gait.foot(t, +1.0, 0.0)
    ar, yr, pr, wr = gait.foot(t, -1.0, 0.5)
    ph = 2.0 * math.pi * (t / gait.cycle)
    bob = -0.012 * math.cos(2.0 * ph)
    return PoseParams(
        root=(root[0], root[1], HIP_Z - 0.01 + bob),
        yaw=yaw,
        hips_pitch=0.04,
        spine_pitch=0.05,
        chest_pitch=0.03,
        chest_yaw=-0.06 * math.sin(ph),
        neck_pitch=-0.04,
        head_pitch=-0.02,
        head_yaw=0.04 * math.sin(ph),
        arm_l_pitch=0.55 * math.sin(ph + math.pi) - 0.05,
        arm_r_pitch=0.55 * math.sin(ph) - 0.05,
        arm_l_abduct=0.10,
        arm_r_abduct=0.10,
        elbow_l=0.30 + 0.20 * (0.0 if pl else math.sin(math.pi * wl)),
        elbow_r=0.30 + 0.20 * (0.0 if pr else math.sin(math.pi * wr)),
        ankle_l=al,
        ankle_r=ar,
        foot_l_pitch=0.30 * (0.0 if pl else math.sin(math.pi * wl)),
        foot_r_pitch=0.30 * (0.0 if pr else math.sin(math.pi * wr)),
        foot_yaw=0.0,
    )


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------


@dataclass
class Segment:
    state: str
    duration: float
    fn: Callable[[float], PoseParams]

    def at(self, u: float) -> PoseParams:
        return self.fn(min(1.0, max(0.0, u))).resolved()


def idle_params(t: float, *, base: PoseParams | None = None) -> PoseParams:
    b = base or PoseParams()
    breath = math.sin(2.0 * math.pi * t / 3.2)
    return replace(
        b,
        root=(b.root[0], b.root[1], b.root[2] + 0.006 * breath),
        spine_pitch=b.spine_pitch + 0.02 * breath,
        chest_pitch=b.chest_pitch - 0.02 * breath,
        head_pitch=b.head_pitch + 0.02 * breath,
        head_yaw=b.head_yaw + 0.05 * math.sin(2.0 * math.pi * t / 6.1),
        arm_l_pitch=b.arm_l_pitch + 0.03 * breath,
        arm_r_pitch=b.arm_r_pitch + 0.03 * breath,
    )


def make_idle(duration: float, base: PoseParams) -> Segment:
    return Segment("idle", duration, lambda u: idle_params(u * duration, base=base))


def make_start_walk(duration: float, base: PoseParams, yaw: float, lead: float) -> tuple[Segment, Callable[[float], tuple[Vec3, float]]]:
    """Anticipation step: weight shift + first foot lift.

    The segment ends on the exact stance a fresh `Gait` has at phase 0
    (left foot `lead` ahead, right foot `lead` behind, root advanced by
    `lead`), so the walk can start without a pose jump.
    """
    f = rot_z(FWD, yaw)
    r = rot_z(RIGHT, yaw)
    start = base.root
    end = vadd(start, vmul(f, lead))
    end = (end[0], end[1], base.root[2])

    def fn(u: float) -> PoseParams:
        s = smoothstep(u)
        lean = 0.10 * s
        # lead foot (left) lifts, plants slightly ahead; trailing foot stays
        lift = 0.05 * math.sin(math.pi * min(1.0, u / 0.75))
        al = vadd(vadd(vadd((start[0], start[1], 0.0), vmul(r, 0.11)), vmul(f, 2.0 * lead * s)), (0.0, 0.0, GROUND_Z + lift))
        ar = vadd(vadd((start[0], start[1], 0.0), vmul(r, -0.11)), (0.0, 0.0, GROUND_Z))
        root = vlerp(start, end, s)
        return PoseParams(
            root=root,
            yaw=yaw,
            hips_pitch=lean * 0.5,
            spine_pitch=lean * 0.6,
            chest_pitch=lean * 0.4,
            head_pitch=-lean * 0.3,
            arm_l_pitch=-0.35 * s,
            arm_r_pitch=0.4 * s,
            arm_l_abduct=0.08,
            arm_r_abduct=0.08,
            elbow_l=0.25 + 0.15 * s,
            elbow_r=0.25 + 0.25 * s,
            ankle_l=al,
            ankle_r=ar,
            foot_l_pitch=0.25 * lift / 0.05 if lift > 1e-6 else 0.0,
        )

    def path(t: float) -> tuple[Vec3, float]:
        return (vadd(end, vmul(f, 0.0)), yaw)

    return Segment("start", duration, fn), path


def make_walk(duration: float, path: Callable[[float], tuple[Vec3, float]], speed: float, stride: float) -> Segment:
    gait = Gait(path=path, speed=speed, stride=stride)
    return Segment("walk", duration, lambda u: _walk_params(gait, u * duration))


def make_stop(duration: float, start_params: PoseParams, gait: Gait, t_start: float, fwd: Vec3) -> Segment:
    """Decelerate and replant into a neutral stance (drift is measured, not hidden)."""
    root0 = start_params.root
    decel = 0.55
    travel = gait.speed * duration * decel
    end_root = vadd(root0, vmul(fwd, travel))
    end_root = (end_root[0], end_root[1], HIP_Z - 0.01)
    yaw = start_params.yaw
    r = rot_z(RIGHT, yaw)
    neutral_l = vadd(vadd((end_root[0], end_root[1], 0.0), vmul(r, 0.11)), (0.0, 0.0, 0.09))
    neutral_r = vadd(vadd((end_root[0], end_root[1], 0.0), vmul(r, -0.11)), (0.0, 0.0, 0.09))
    a0l, a0r = start_params.ankle_l, start_params.ankle_r
    assert a0l is not None and a0r is not None

    def fn(u: float) -> PoseParams:
        s = smoothstep(u)
        root = vlerp(root0, end_root, s)
        # move the feet late in the segment, with a small lift so it reads as a step
        w = smoothstep(min(1.0, max(0.0, (u - 0.15) / 0.7)))
        lift = 0.045 * math.sin(math.pi * w)
        al = vlerp(a0l, neutral_l, w)
        ar = vlerp(a0r, neutral_r, w)
        al = (al[0], al[1], al[2] + lift)
        ar = (ar[0], ar[1], ar[2] + lift * 0.6)
        return PoseParams(
            root=root,
            yaw=yaw,
            hips_pitch=0.05 * (1.0 - s),
            spine_pitch=0.06 * (1.0 - s),
            chest_pitch=0.03 * (1.0 - s),
            head_pitch=-0.02 * (1.0 - s),
            arm_l_pitch=-0.25 * (1.0 - s),
            arm_r_pitch=0.25 * (1.0 - s),
            arm_l_abduct=0.09,
            arm_r_abduct=0.09,
            elbow_l=0.3,
            elbow_r=0.3,
            ankle_l=al,
            ankle_r=ar,
            foot_l_pitch=0.15 * math.sin(math.pi * w),
            foot_r_pitch=0.1 * math.sin(math.pi * w),
        )

    return Segment("stop", duration, fn)


def make_turn_in_place(duration: float, base: PoseParams, yaw_from: float, yaw_to: float) -> Segment:
    root = base.root

    def fn(u: float) -> PoseParams:
        s = smoothstep(u)
        yaw = lerp(yaw_from, yaw_to, s)
        r = rot_z(RIGHT, yaw)
        # two small pivot steps: lift one foot at a time
        step_l = math.sin(math.pi * min(1.0, max(0.0, (u - 0.05) / 0.45)))
        step_r = math.sin(math.pi * min(1.0, max(0.0, (u - 0.5) / 0.45)))
        al = vadd(vadd((root[0], root[1], 0.0), vmul(r, 0.11)), (0.0, 0.0, 0.09 + 0.05 * step_l))
        ar = vadd(vadd((root[0], root[1], 0.0), vmul(r, -0.11)), (0.0, 0.0, 0.09 + 0.05 * step_r))
        return PoseParams(
            root=root,
            yaw=yaw,
            hips_pitch=0.03,
            chest_yaw=0.10 * math.sin(math.pi * s),
            head_yaw=0.25 * math.sin(math.pi * s),
            arm_l_pitch=-0.12 * math.sin(math.pi * s),
            arm_r_pitch=0.12 * math.sin(math.pi * s),
            arm_l_abduct=0.12 + 0.05 * math.sin(math.pi * s),
            arm_r_abduct=0.12 + 0.05 * math.sin(math.pi * s),
            elbow_l=0.3,
            elbow_r=0.3,
            ankle_l=al,
            ankle_r=ar,
            foot_l_pitch=0.2 * step_l,
            foot_r_pitch=0.2 * step_r,
        )

    return Segment("turn_in_place", duration, fn)


def base_value(base: PoseParams, name: str, state: str) -> float:
    """Neutral value of an overlay key (keeps overlays additive on the idle base)."""
    defaults = {
        "chest_pitch": 0.0,
        "chest_yaw": 0.0,
        "spine_pitch": 0.0,
        "neck_pitch": 0.0,
        "head_pitch": 0.0,
        "head_yaw": 0.0,
        "head_roll": 0.0,
        "arm_l_pitch": 0.0,
        "arm_l_abduct": 0.02,
        "arm_r_pitch": 0.0,
        "arm_r_abduct": 0.02,
        "elbow_l": 0.22,
        "elbow_r": 0.22,
        "wrist_l": 0.0,
        "wrist_r": 0.0,
        "hips_pitch": 0.0,
        "root_dz": 0.0,
    }
    if name == "root_dz":
        return 0.0
    return getattr(base, name, defaults.get(name, 0.0))


def make_overlay(
    duration: float,
    state: str,
    base: PoseParams,
    keys: Sequence[tuple[float, dict[str, float]]],
) -> Segment:
    def fn(u: float) -> PoseParams:
        seg_keys = list(keys)
        prev = seg_keys[0]
        cur = seg_keys[-1]
        for i in range(1, len(seg_keys)):
            if u <= seg_keys[i][0]:
                prev, cur = seg_keys[i - 1], seg_keys[i]
                break
        span = max(1e-6, cur[0] - prev[0])
        w = smoothstep((u - prev[0]) / span)
        kw: dict[str, Any] = {}
        rest = replace(base, ankle_l=base.resolved().ankle_l, ankle_r=base.resolved().ankle_r)
        for name in set(prev[1]) | set(cur[1]):
            a = prev[1].get(name, base_value(base, name, state))
            b = cur[1].get(name, base_value(base, name, state))
            if name == "root_dz":
                kw["root"] = (rest.root[0], rest.root[1], rest.root[2] + lerp(a, b, w))
            else:
                kw[name] = lerp(a, b, w)
        return idle_params(u * duration, base=replace(rest, **kw))

    return Segment(state, duration, fn)


@dataclass
class Timeline:
    segments: list[Segment]
    blend: float = 0.18

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)

    def sample(self, t: float) -> PoseParams:
        t = max(0.0, min(self.duration - 1e-6, t))
        acc = 0.0
        idx = 0
        for i, s in enumerate(self.segments):
            if t < acc + s.duration or i == len(self.segments) - 1:
                idx = i
                break
            acc += s.duration
        seg = self.segments[idx]
        u = (t - acc) / max(1e-6, seg.duration)
        cur = seg.at(u)
        # Ease in from the *previous* segment's final pose.  The look-ahead
        # blend was removed: blending towards ``next.at(0)`` before the
        # boundary and towards ``prev.at(1)`` after it made the pose jump
        # back and forth across the cut instead of cross-fading it.
        if idx > 0 and self.blend > 0.0 and t - acc < self.blend:
            w = smoothstep((t - acc) / self.blend)
            cur = blend_params(self.segments[idx - 1].at(1.0), cur, w)
        return cur.resolved()

    def sample_frames(self, fps: int) -> list[PoseParams]:
        n = int(round(self.duration * fps))
        return [self.sample((i + 0.0) / fps) for i in range(n)]


def default_timeline(scenario: str = "states", spec: CharacterSpec = MILO) -> Timeline:
    """Build the diagnostic timeline that exercises every locomotion state."""
    base = PoseParams()
    if scenario == "states":
        yaw = 0.0
        b = replace(base, yaw=yaw)
        return Timeline(
            segments=[
                make_idle(1.4, b),
                make_overlay(
                    1.4,
                    "reach",
                    b,
                    [
                        (0.0, {}),
                        (0.45, {"arm_r_pitch": -1.35, "arm_r_abduct": 0.30, "elbow_r": 0.25,
                                "chest_pitch": 0.10, "chest_yaw": -0.22, "head_yaw": -0.20,
                                "head_pitch": 0.12, "spine_pitch": 0.06}),
                        (1.0, {"arm_r_pitch": -1.20, "arm_r_abduct": 0.28, "elbow_r": 0.40,
                               "chest_pitch": 0.08, "chest_yaw": -0.18, "head_yaw": -0.16,
                               "head_pitch": 0.10, "spine_pitch": 0.05}),
                    ],
                ),
                make_overlay(
                    1.2,
                    "gesture",
                    b,
                    [
                        (0.0, {}),
                        (0.5, {"arm_l_pitch": -1.05, "arm_l_abduct": 0.55, "elbow_l": 1.15,
                               "wrist_l": 0.35, "chest_yaw": 0.18, "head_yaw": 0.16,
                               "head_pitch": -0.05}),
                        (1.0, {"arm_l_pitch": -0.95, "arm_l_abduct": 0.50, "elbow_l": 0.95,
                               "wrist_l": -0.20, "chest_yaw": 0.14, "head_yaw": 0.12}),
                    ],
                ),
                make_overlay(
                    0.9,
                    "react",
                    b,
                    [
                        (0.0, {}),
                        (0.25, {"chest_pitch": -0.16, "spine_pitch": -0.10, "head_pitch": -0.18,
                                "arm_l_pitch": -0.55, "arm_r_pitch": -0.55,
                                "arm_l_abduct": 0.42, "arm_r_abduct": 0.42,
                                "elbow_l": 0.95, "elbow_r": 0.95, "root_dz": -0.02}),
                        (1.0, {"chest_pitch": -0.05, "spine_pitch": -0.03, "head_pitch": -0.06,
                               "arm_l_pitch": -0.25, "arm_r_pitch": -0.25,
                               "arm_l_abduct": 0.20, "arm_r_abduct": 0.20,
                               "elbow_l": 0.55, "elbow_r": 0.55, "root_dz": 0.0}),
                    ],
                ),
                make_overlay(
                    1.6,
                    "talk",
                    b,
                    [
                        (0.0, {}),
                        (0.2, {"head_pitch": 0.08, "head_yaw": -0.10, "arm_r_pitch": -0.45,
                               "arm_r_abduct": 0.28, "elbow_r": 0.85, "wrist_r": 0.3,
                               "chest_yaw": -0.06}),
                        (0.45, {"head_pitch": -0.04, "head_yaw": 0.08, "arm_r_pitch": -0.30,
                                "arm_r_abduct": 0.22, "elbow_r": 0.60, "wrist_r": -0.2,
                                "chest_yaw": 0.02}),
                        (0.7, {"head_pitch": 0.07, "head_yaw": -0.06, "arm_l_pitch": -0.42,
                               "arm_l_abduct": 0.25, "elbow_l": 0.80, "wrist_l": 0.25}),
                        (1.0, {"head_pitch": 0.0, "head_yaw": 0.0, "arm_l_pitch": -0.15,
                               "elbow_l": 0.35, "arm_r_pitch": -0.15, "elbow_r": 0.35}),
                    ],
                ),
                make_overlay(
                    1.5,
                    "interact",
                    b,
                    [
                        (0.0, {}),
                        (0.35, {"arm_r_pitch": -0.85, "arm_r_abduct": 0.05, "elbow_r": 0.55,
                                "chest_pitch": 0.16, "spine_pitch": 0.10, "head_pitch": 0.20,
                                "root_dz": -0.03}),
                        (0.7, {"arm_r_pitch": -0.60, "arm_r_abduct": 0.05, "elbow_r": 1.05,
                               "chest_pitch": 0.12, "spine_pitch": 0.07, "head_pitch": 0.16,
                               "root_dz": -0.02, "wrist_r": 0.4}),
                        (1.0, {"arm_r_pitch": -0.20, "arm_r_abduct": 0.10, "elbow_r": 0.40,
                               "chest_pitch": 0.03, "spine_pitch": 0.02, "head_pitch": 0.04,
                               "root_dz": 0.0, "wrist_r": 0.0}),
                    ],
                ),
                make_turn_in_place(1.3, replace(b, root=base.root), 0.0, math.pi * 0.5),
                make_overlay(
                    1.2,
                    "return_idle",
                    replace(b, yaw=math.pi * 0.5),
                    [(0.0, {}), (1.0, {"head_yaw": 0.0, "chest_yaw": 0.0})],
                ),
            ],
            blend=0.18,
        )
    # ---- walk scenario -------------------------------------------------
    yaw = math.pi / 2.0  # forward = +X (profile view: best for foot-lock checks)
    start_root: Vec3 = (-0.85, 0.0, HIP_Z - 0.01)
    b = replace(base, root=start_root, yaw=yaw)
    speed = 0.75
    stride = 0.58
    lead = 0.58 * stride * 0.5
    start_seg, start_path = make_start_walk(0.8, b, yaw, lead)
    walk_path = path_constant(vadd(start_root, vmul(rot_z(FWD, yaw), lead)), yaw, speed)
    # whole number of gait cycles: the segment ends on a clean double-support
    # stance, so the next gait can start at phase 0 without a pose jump
    walk_dur = 4.0 * (stride / speed)
    walk_seg = make_walk(walk_dur, walk_path, speed, stride)
    gait = Gait(path=walk_path, speed=speed, stride=stride, duty=0.58)
    walk_end = _walk_params(gait, walk_dur)
    fwd = rot_z(FWD, yaw)
    turn_path = path_constant(walk_end.root, yaw, 0.55, yaw_rate=-0.35)
    turn_seg = make_walk(1.0, turn_path, 0.55, 0.40)
    turn_gait = Gait(path=turn_path, speed=0.55, stride=0.40, duty=0.58)
    turn_end = _walk_params(turn_gait, 1.0)
    stop_seg = make_stop(0.9, turn_end, turn_gait, 1.0, fwd)
    stop_base = stop_seg.at(1.0)
    return Timeline(
        segments=[
            make_idle(0.9, b),
            start_seg,
            walk_seg,
            turn_seg,
            stop_seg,
            make_idle(0.8, stop_base),
            make_turn_in_place(1.3, stop_base, stop_base.yaw, stop_base.yaw + math.pi * 0.75),
            make_idle(0.7, replace(stop_base, yaw=stop_base.yaw + math.pi * 0.75, ankle_l=None, ankle_r=None)),
        ],
        blend=0.16,
    )


# --------------------------------------------------------------------------
# production timeline: plan actions -> rig segments
# --------------------------------------------------------------------------

#: Plan vocabulary (2D engine + planner) mapped onto rig states.  A verb that is
#: missing here is a hard error: silently playing idle instead would hide the
#: fact that the shot was never animated.
PLAN_ACTION_STATES: dict[str, str] = {
    "idle": "idle",
    "stand": "idle",
    "wait": "idle",
    "return_idle": "idle",
    "walk": "walk",
    "run": "run",
    "crouch": "crouch",
    "reach": "reach",
    "pickup": "interact",
    "interact": "interact",
    "use": "interact",
    "react": "react",
    "surprise": "react",
    "joy": "joy",
    "celebrate": "celebrate",
    "gesture": "gesture",
    "wave": "wave",
    "talk": "talk",
    "turn": "turn_in_place",
    "turn_in_place": "turn_in_place",
    "stop": "stop",
    # dialogue acting (A4): gaze, agreement, denial, posture and object handling
    "look_at": "look_at",
    "listen": "listen",
    "listen_nod": "listen_nod",
    "nod": "nod",
    "shake_head": "shake_head",
    "shrug": "shrug",
    "point": "point",
    "grab": "grab",
    "hold": "hold",
    "release": "release",
    # body-level accidents: these move the whole root, so the feet stay planted
    "scared": "scared",
    "stumble": "stumble",
    "fall": "fall",
    "recover": "recover",
    # emotion verbs: a body-neutral segment plus a layered EMOTIONS delta
    "neutral": "emotion",
    "happy": "emotion",
    "sad": "emotion",
    "angry": "emotion",
    "confused": "emotion",
    "worried": "emotion",
    "surprised": "emotion",
}

#: Overlay keyframes per rig state.  Values are ABSOLUTE pose values and are
#: kept in sync with the diagnostic timeline in :func:`default_timeline`.
PLAN_OVERLAY_KEYS: dict[str, list[tuple[float, dict[str, float]]]] = {
    "reach": [
        (0.0, {}),
        (0.45, {"arm_r_pitch": -1.35, "arm_r_abduct": 0.30, "elbow_r": 0.25,
                "chest_pitch": 0.10, "chest_yaw": -0.22, "head_yaw": -0.20,
                "head_pitch": 0.12, "spine_pitch": 0.06}),
        (1.0, {"arm_r_pitch": -1.20, "arm_r_abduct": 0.28, "elbow_r": 0.40,
               "chest_pitch": 0.08, "chest_yaw": -0.18, "head_yaw": -0.16,
               "head_pitch": 0.10, "spine_pitch": 0.05}),
    ],
    "crouch": [
        (0.0, {}),
        (0.45, {"root_dz": -0.22, "chest_pitch": 0.18, "spine_pitch": 0.12,
                "head_pitch": 0.16, "arm_r_pitch": -0.35,
                "arm_r_abduct": 0.18, "elbow_r": 0.75,
                "arm_l_pitch": -0.28, "arm_l_abduct": 0.16, "elbow_l": 0.65}),
        (1.0, {"root_dz": -0.18, "chest_pitch": 0.14, "spine_pitch": 0.09,
               "head_pitch": 0.12, "arm_r_pitch": -0.25,
               "arm_r_abduct": 0.16, "elbow_r": 0.68,
               "arm_l_pitch": -0.20, "arm_l_abduct": 0.14, "elbow_l": 0.58}),
    ],
    "gesture": [
        (0.0, {}),
        (0.5, {"arm_l_pitch": -1.05, "arm_l_abduct": 0.55, "elbow_l": 1.15,
               "wrist_l": 0.35, "chest_yaw": 0.18, "head_yaw": 0.16,
               "head_pitch": -0.05}),
        (1.0, {"arm_l_pitch": -0.95, "arm_l_abduct": 0.50, "elbow_l": 0.95,
               "wrist_l": -0.20, "chest_yaw": 0.14, "head_yaw": 0.12}),
    ],
    "wave": [
        (0.0, {}),
        (0.3, {"arm_r_pitch": -1.30, "arm_r_abduct": 0.42, "elbow_r": 0.85,
               "wrist_r": 0.40, "chest_yaw": 0.10, "head_yaw": 0.08,
               "head_pitch": -0.06}),
        (0.55, {"arm_r_pitch": -1.25, "arm_r_abduct": 0.40, "elbow_r": 0.85,
                "wrist_r": 0.70, "chest_yaw": 0.12, "head_yaw": 0.10}),
        (0.8, {"arm_r_pitch": -1.25, "arm_r_abduct": 0.40, "elbow_r": 0.85,
               "wrist_r": -0.70, "chest_yaw": 0.08, "head_yaw": 0.06}),
        (1.0, {"arm_r_pitch": -1.20, "arm_r_abduct": 0.38, "elbow_r": 0.80,
               "wrist_r": 0.40, "chest_yaw": 0.06, "head_yaw": 0.04}),
    ],
    "celebrate": [
        (0.0, {}),
        (0.25, {"arm_l_pitch": -1.40, "arm_r_pitch": -1.40,
                "arm_l_abduct": 0.28, "arm_r_abduct": 0.28,
                "elbow_l": 0.70, "elbow_r": 0.70,
                "wrist_l": 0.35, "wrist_r": 0.35,
                "root_dz": 0.09, "head_pitch": -0.20, "chest_pitch": -0.08}),
        (0.55, {"arm_l_pitch": -1.35, "arm_r_pitch": -1.35,
                "arm_l_abduct": 0.26, "arm_r_abduct": 0.26,
                "elbow_l": 0.65, "elbow_r": 0.65,
                "wrist_l": 0.30, "wrist_r": 0.30,
                "root_dz": 0.05, "head_pitch": -0.16, "chest_pitch": -0.06}),
        (1.0, {"arm_l_pitch": -1.30, "arm_r_pitch": -1.30,
               "arm_l_abduct": 0.24, "arm_r_abduct": 0.24,
               "elbow_l": 0.60, "elbow_r": 0.60,
               "wrist_l": 0.25, "wrist_r": 0.25,
               "root_dz": 0.02, "head_pitch": -0.12, "chest_pitch": -0.04}),
    ],
    "joy": [
        (0.0, {}),
        (0.4, {"arm_r_pitch": -1.25, "arm_r_abduct": 0.30, "elbow_r": 0.95,
               "wrist_r": 0.40, "head_pitch": -0.15, "chest_pitch": -0.05,
               "root_dz": 0.07}),
        (0.7, {"arm_r_pitch": -1.20, "arm_r_abduct": 0.28, "elbow_r": 0.90,
               "wrist_r": 0.30, "head_pitch": -0.12, "chest_pitch": -0.04,
               "root_dz": 0.03}),
        (1.0, {"arm_r_pitch": -1.10, "arm_r_abduct": 0.26, "elbow_r": 0.85,
               "wrist_r": 0.20, "head_pitch": -0.08, "chest_pitch": -0.02,
               "root_dz": 0.0}),
    ],
    "react": [
        (0.0, {}),
        (0.25, {"chest_pitch": -0.16, "spine_pitch": -0.10, "head_pitch": -0.18,
                "arm_l_pitch": -0.55, "arm_r_pitch": -0.55,
                "arm_l_abduct": 0.42, "arm_r_abduct": 0.42,
                "elbow_l": 0.95, "elbow_r": 0.95, "root_dz": -0.02}),
        (1.0, {"chest_pitch": -0.05, "spine_pitch": -0.03, "head_pitch": -0.06,
               "arm_l_pitch": -0.25, "arm_r_pitch": -0.25,
               "arm_l_abduct": 0.20, "arm_r_abduct": 0.20,
               "elbow_l": 0.55, "elbow_r": 0.55, "root_dz": 0.0}),
    ],
    "talk": [
        (0.0, {}),
        (0.2, {"head_pitch": 0.08, "head_yaw": -0.10, "arm_r_pitch": -0.45,
               "arm_r_abduct": 0.28, "elbow_r": 0.85, "wrist_r": 0.3,
               "chest_yaw": -0.06}),
        (0.45, {"head_pitch": -0.04, "head_yaw": 0.08, "arm_r_pitch": -0.30,
                "arm_r_abduct": 0.22, "elbow_r": 0.60, "wrist_r": -0.2,
                "chest_yaw": 0.02}),
        (0.7, {"head_pitch": 0.07, "head_yaw": -0.06, "arm_l_pitch": -0.42,
               "arm_l_abduct": 0.25, "elbow_l": 0.80, "wrist_l": 0.25}),
        (1.0, {"head_pitch": 0.0, "head_yaw": 0.0, "arm_l_pitch": -0.15,
               "elbow_l": 0.35, "arm_r_pitch": -0.15, "elbow_r": 0.35}),
    ],
    "interact": [
        (0.0, {}),
        (0.35, {"arm_r_pitch": -0.85, "arm_r_abduct": 0.05, "elbow_r": 0.55,
                "chest_pitch": 0.16, "spine_pitch": 0.10, "head_pitch": 0.20,
                "root_dz": -0.03}),
        (0.7, {"arm_r_pitch": -0.60, "arm_r_abduct": 0.05, "elbow_r": 1.05,
               "chest_pitch": 0.12, "spine_pitch": 0.07, "head_pitch": 0.16,
               "root_dz": -0.02, "wrist_r": 0.4}),
        (1.0, {"arm_r_pitch": -0.20, "arm_r_abduct": 0.10, "elbow_r": 0.40,
               "chest_pitch": 0.03, "spine_pitch": 0.02, "head_pitch": 0.04,
               "root_dz": 0.0, "wrist_r": 0.0}),
    ],
    "return_idle": [
        (0.0, {}),
        (1.0, {"head_yaw": 0.0, "chest_yaw": 0.0}),
    ],
    # ---- A4 dialogue acting -------------------------------------------------
    # Values are absolute pose channels; anything left out falls back to the
    # state's neutral value, and ``make_overlay`` always keeps the feet planted.
    "look_at": [
        (0.0, {}),
        (0.35, {"head_yaw": 0.42, "neck_pitch": 0.05, "head_pitch": -0.05,
                "chest_yaw": 0.10}),
        (1.0, {"head_yaw": 0.36, "neck_pitch": 0.04, "chest_yaw": 0.08}),
    ],
    "listen": [
        (0.0, {}),
        (0.4, {"head_pitch": -0.07, "head_yaw": 0.20, "head_roll": 0.05,
               "chest_pitch": -0.03, "chest_yaw": 0.06,
               "arm_l_abduct": 0.06, "arm_r_abduct": 0.06}),
        (1.0, {"head_pitch": -0.04, "head_yaw": 0.15, "head_roll": 0.03,
               "chest_pitch": -0.02, "chest_yaw": 0.04}),
    ],
    "listen_nod": [
        (0.0, {}),
        (0.22, {"head_yaw": 0.24, "neck_pitch": 0.05, "head_pitch": 0.17,
                "chest_yaw": 0.07, "head_roll": 0.04, "chest_pitch": -0.02}),
        (0.42, {"head_yaw": 0.26, "neck_pitch": 0.01, "head_pitch": -0.04,
                "chest_yaw": 0.08, "head_roll": 0.02}),
        (0.62, {"head_yaw": 0.24, "neck_pitch": 0.04, "head_pitch": 0.13,
                "chest_yaw": 0.07, "head_roll": 0.03}),
        (1.0, {"head_yaw": 0.21, "neck_pitch": 0.02, "head_pitch": -0.01,
               "chest_yaw": 0.06, "head_roll": 0.02}),
    ],
    "nod": [
        (0.0, {}),
        (0.18, {"head_pitch": 0.22, "neck_pitch": 0.07, "chest_pitch": 0.03}),
        (0.34, {"head_pitch": -0.06, "neck_pitch": -0.02, "chest_pitch": 0.0}),
        (0.55, {"head_pitch": 0.19, "neck_pitch": 0.06, "chest_pitch": 0.02}),
        (0.72, {"head_pitch": -0.03, "neck_pitch": 0.0}),
        (1.0, {"head_pitch": 0.0, "neck_pitch": 0.0, "chest_pitch": 0.0}),
    ],
    "shake_head": [
        (0.0, {}),
        (0.2, {"head_yaw": -0.30, "chest_yaw": -0.06}),
        (0.45, {"head_yaw": 0.30, "chest_yaw": 0.06}),
        (0.7, {"head_yaw": -0.22, "chest_yaw": -0.04}),
        (1.0, {"head_yaw": 0.0, "chest_yaw": 0.0}),
    ],
    "shrug": [
        (0.0, {}),
        (0.4, {"arm_l_abduct": 0.34, "arm_r_abduct": 0.34,
               "arm_l_pitch": -0.16, "arm_r_pitch": -0.16,
               "elbow_l": 0.85, "elbow_r": 0.85, "wrist_l": -0.25,
               "wrist_r": -0.25, "head_roll": 0.10, "chest_pitch": -0.03,
               "root_dz": 0.012}),
        (1.0, {"arm_l_abduct": 0.26, "arm_r_abduct": 0.26,
               "elbow_l": 0.70, "elbow_r": 0.70, "wrist_l": -0.15,
               "wrist_r": -0.15, "head_roll": 0.06, "root_dz": 0.0}),
    ],
    "point": [
        (0.0, {}),
        (0.35, {"arm_r_pitch": -1.15, "arm_r_abduct": 0.12, "elbow_r": 0.10,
                "chest_yaw": -0.15, "head_yaw": -0.16, "head_pitch": 0.05,
                "spine_pitch": 0.03}),
        (1.0, {"arm_r_pitch": -1.05, "arm_r_abduct": 0.10, "elbow_r": 0.18,
               "chest_yaw": -0.12, "head_yaw": -0.13}),
    ],
    "grab": [
        (0.0, {}),
        (0.4, {"arm_r_pitch": -1.25, "arm_r_abduct": 0.06, "elbow_r": 0.55,
               "wrist_r": 0.25, "chest_pitch": 0.09, "head_pitch": 0.15,
               "spine_pitch": 0.05}),
        (1.0, {"arm_r_pitch": -1.15, "arm_r_abduct": 0.06, "elbow_r": 0.78,
               "wrist_r": 0.35, "chest_pitch": 0.06, "head_pitch": 0.12}),
    ],
    "hold": [
        (0.0, {}),
        (0.4, {"arm_l_pitch": -0.72, "arm_l_abduct": 0.10, "elbow_l": 1.25,
               "wrist_l": 0.15, "arm_r_pitch": -0.68, "arm_r_abduct": 0.10,
               "elbow_r": 1.20, "wrist_r": 0.15, "chest_pitch": 0.02,
               "head_pitch": 0.10}),
        (1.0, {"arm_l_pitch": -0.68, "arm_l_abduct": 0.10, "elbow_l": 1.18,
               "arm_r_pitch": -0.64, "elbow_r": 1.14, "head_pitch": 0.08}),
    ],
    "release": [
        (0.0, {}),
        (0.35, {"arm_l_pitch": -0.55, "arm_r_pitch": -0.55,
                "arm_l_abduct": 0.22, "arm_r_abduct": 0.22,
                "elbow_l": 0.45, "elbow_r": 0.45, "chest_pitch": -0.04,
                "head_pitch": -0.04}),
        (1.0, {"arm_l_pitch": -0.15, "arm_r_pitch": -0.15,
               "elbow_l": 0.30, "elbow_r": 0.30}),
    ],
    "scared": [
        (0.0, {}),
        (0.25, {"chest_pitch": -0.24, "spine_pitch": -0.15,
                "head_pitch": -0.14, "arm_l_pitch": -1.30, "arm_r_pitch": -1.30,
                "arm_l_abduct": 0.48, "arm_r_abduct": 0.48,
                "elbow_l": 1.75, "elbow_r": 1.75,
                "wrist_l": 0.80, "wrist_r": 0.80, "root_dz": -0.08}),
        (1.0, {"chest_pitch": -0.18, "spine_pitch": -0.11,
               "head_pitch": -0.10, "arm_l_pitch": -1.20, "arm_r_pitch": -1.20,
               "arm_l_abduct": 0.42, "arm_r_abduct": 0.42,
               "elbow_l": 1.60, "elbow_r": 1.60,
               "wrist_l": 0.70, "wrist_r": 0.70, "root_dz": -0.06}),
    ],
    "stumble": [
        (0.0, {}),
        (0.2, {"chest_pitch": 0.24, "spine_pitch": 0.16, "head_pitch": 0.20,
               "arm_l_pitch": -0.80, "arm_r_pitch": -0.90,
               "arm_l_abduct": 0.50, "arm_r_abduct": 0.50, "root_dz": -0.05}),
        (0.5, {"chest_pitch": -0.10, "spine_pitch": -0.06, "head_pitch": -0.12,
               "arm_l_pitch": -0.50, "arm_r_pitch": -0.60, "root_dz": 0.02}),
        (1.0, {"chest_pitch": 0.03, "spine_pitch": 0.02, "head_pitch": 0.02,
               "arm_l_pitch": -0.10, "arm_r_pitch": -0.10, "root_dz": 0.0}),
    ],
    "fall": [
        (0.0, {}),
        (0.45, {"root_dz": -0.30, "chest_pitch": 0.22, "spine_pitch": 0.14,
                "head_pitch": 0.18, "arm_l_pitch": -0.55, "arm_r_pitch": -0.65,
                "arm_l_abduct": 0.35, "arm_r_abduct": 0.35,
                "elbow_l": 0.60, "elbow_r": 0.60}),
        (1.0, {"root_dz": -0.42, "chest_pitch": 0.28, "spine_pitch": 0.18,
               "head_pitch": 0.24, "arm_l_pitch": -0.35, "arm_r_pitch": -0.45,
               "elbow_l": 0.90, "elbow_r": 0.90}),
    ],
    "recover": [
        (0.0, {"root_dz": -0.30, "chest_pitch": 0.20, "head_pitch": 0.16}),
        (0.5, {"root_dz": -0.10, "chest_pitch": -0.06, "head_pitch": -0.06,
               "arm_l_pitch": -0.30, "arm_r_pitch": -0.30}),
        (1.0, {"root_dz": 0.0, "chest_pitch": 0.0, "head_pitch": 0.0,
               "arm_l_pitch": 0.0, "arm_r_pitch": 0.0}),
    ],
    # a pure emotion segment keeps the body neutral; the emotion deltas are
    # layered on top by ``_apply_emotion`` (see EMOTIONS).
    "emotion": [(0.0, {}), (1.0, {})],
}


#: Acting states.  Every entry is a *delta* added to the underlying pose, so an
#: emotion can sit on top of idle, a walk or a gesture without fighting the IK
#: (root, ankles and foot pitches are never touched here).
EMOTIONS: dict[str, dict[str, float]] = {
    "neutral": {},
    "happy": {
        "head_pitch": -0.10, "head_roll": 0.04, "chest_pitch": -0.06,
        "arm_l_abduct": 0.06, "arm_r_abduct": 0.06,
        "brow_lift": 0.004, "jaw_open": 0.08, "mouth_wide": 0.35,
    },
    "surprised": {
        "head_pitch": -0.22, "spine_pitch": -0.10, "chest_pitch": -0.14,
        "arm_l_pitch": -0.35, "arm_r_pitch": -0.35,
        "arm_l_abduct": 0.35, "arm_r_abduct": 0.35,
        "elbow_l": 0.45, "elbow_r": 0.45,
        "brow_lift": 0.012, "jaw_open": 0.28, "mouth_round": 0.50,
    },
    "sad": {
        "head_pitch": 0.16, "spine_pitch": 0.10, "chest_pitch": 0.12,
        "arm_l_pitch": 0.10, "arm_r_pitch": 0.10,
        "elbow_l": -0.10, "elbow_r": -0.10,
        "brow_tilt": -0.30, "brow_lift": -0.004, "mouth_wide": -0.40,
    },
    "angry": {
        "head_pitch": 0.06, "chest_pitch": -0.04,
        "arm_l_abduct": 0.12, "arm_r_abduct": 0.12,
        "elbow_l": 0.28, "elbow_r": 0.28,
        "brow_tilt": 0.55, "brow_lift": -0.006, "mouth_wide": -0.50,
    },
    "confused": {
        "head_roll": 0.22, "head_yaw": 0.12, "chest_yaw": 0.08,
        "brow_lift": 0.006, "brow_tilt": 0.25, "mouth_round": 0.25,
    },
    "worried": {
        "head_pitch": 0.04, "chest_pitch": 0.05,
        "brow_tilt": -0.45, "brow_lift": 0.004, "mouth_wide": -0.20,
    },
    # A4: fear reads as recoil (body back, shoulders up) plus wide eyes
    "scared": {
        "head_pitch": -0.12, "spine_pitch": -0.06, "chest_pitch": -0.10,
        "arm_l_abduct": 0.20, "arm_r_abduct": 0.20,
        "elbow_l": 0.32, "elbow_r": 0.32,
        "brow_lift": 0.014, "brow_tilt": -0.20, "jaw_open": 0.16,
        "mouth_round": 0.30,
    },
}


def _apply_emotion(
    segment: Segment, emotion: str, intensity: float = 1.0
) -> Segment:
    """Layer an emotion delta over an existing segment.

    The delta ramps in and out over a quarter of the segment, so consecutive
    states never snap; ``root`` and ankles are untouched, therefore walking, IK
    and foot contact keep working while the character acts.
    """
    delta = EMOTIONS.get(emotion)
    if delta is None:
        raise ValueError(
            f"Неизвестная эмоция «{emotion}». Доступно: {', '.join(sorted(EMOTIONS))}."
        )
    scale = min(1.0, max(0.0, float(intensity)))
    if not delta or scale <= 0.0:
        return segment
    inner = segment.fn

    def fn(u: float) -> PoseParams:
        pose = inner(u)
        w = smoothstep(min(1.0, max(0.0, min(u, 1.0 - u) / 0.25))) * scale
        if w <= 1e-6:
            return pose
        changes: dict[str, Any] = {}
        for name, value in delta.items():
            if name == "root_dz":
                root = pose.root
                changes["root"] = (root[0], root[1], root[2] + value * w)
            else:
                changes[name] = getattr(pose, name, 0.0) + value * w
        return replace(pose, **changes)

    return Segment(segment.state, segment.duration, fn)


def _scaled_overlay(
    state: str,
    base: PoseParams,
    intensity: float,
) -> list[tuple[float, dict[str, float]]]:
    """Overlay keyframes with every value pulled towards the neutral pose.

    ``intensity`` is the plan's expression strength (``params["intensity"]``):
    ``1.0`` reproduces the diagnostic pose, ``0.4`` gives a half gesture.  The
    result is still continuous because the neutral value is the same one the
    overlay interpolates from.
    """
    keys = PLAN_OVERLAY_KEYS.get(state)
    if keys is None:
        raise ValueError(f"Нет оверлея для состояния «{state}».")
    scale = min(1.0, max(0.0, float(intensity)))
    if scale >= 0.999:
        return keys
    scaled: list[tuple[float, dict[str, float]]] = []
    for u, values in keys:
        scaled.append(
            (
                u,
                {
                    name: base_value(base, name, state)
                    + (value - base_value(base, name, state)) * scale
                    for name, value in values.items()
                },
            )
        )
    return scaled


#: Lateral channels that point to one side, so a mirrored overlay simply flips
#: their sign.  Pairs are swapped so a mirrored ``point`` uses the other hand.
_MIRROR_FLIP: tuple[str, ...] = ("head_yaw", "chest_yaw", "head_roll", "hips_yaw")
_MIRROR_PAIRS: dict[str, str] = {}
for _side_a, _side_b in (
    ("arm_l_pitch", "arm_r_pitch"),
    ("arm_l_abduct", "arm_r_abduct"),
    ("elbow_l", "elbow_r"),
    ("wrist_l", "wrist_r"),
    ("shoulder_l", "shoulder_r"),
):
    _MIRROR_PAIRS[_side_a] = _side_b
    _MIRROR_PAIRS[_side_b] = _side_a


def _mirror_overlay(
    keys: Sequence[tuple[float, dict[str, float]]],
) -> list[tuple[float, dict[str, float]]]:
    """Flip an overlay to the other side of the body.

    Used by ``params["direction"]``: a listener standing to the right of the
    speaker turns the head the other way, so the same authored gesture works for
    both seats without a second keyframe table.
    """
    mirrored: list[tuple[float, dict[str, float]]] = []
    for u, values in keys:
        flipped: dict[str, float] = {}
        for name, value in values.items():
            if name in _MIRROR_PAIRS:
                flipped[_MIRROR_PAIRS[name]] = value
            elif name in _MIRROR_FLIP:
                flipped[name] = -value
            else:
                flipped[name] = value
        mirrored.append((u, flipped))
    return mirrored


def timeline_from_actions(
    actions: Sequence[dict[str, Any]],
    *,
    duration: float | None = None,
    fps: int = 30,
    speed: float = 0.75,
    stride: float = 0.58,
    start_dur: float = 0.5,
    stop_dur: float = 0.55,
    blend: float = 0.16,
    yaw: float = math.pi / 2.0,
) -> Timeline:
    """Turn a plan's action list into a rig timeline.

    Each item is a neutral dict ``{"action", "start", "duration", "params"}``
    so this module does not have to import ``animation_plan`` (the dispatcher
    adapts ``PlanAction``).  Gaps become idle, ``walk`` is expanded into
    start + walk + stop with a whole number of gait cycles, and the timeline is
    padded to ``duration`` exactly.

    Unsupported verbs raise :class:`ValueError` — never a silent idle.
    """
    if not actions:
        raise ValueError("План не содержит действий для скин-рига.")
    min_d = 1.0 / max(1, int(fps))
    items: list[tuple[float, float, str, dict[str, Any]]] = []
    for raw in actions:
        name = str(raw.get("action") or raw.get("state") or "").strip().lower()
        if not name:
            raise ValueError("Действие без названия.")
        state = PLAN_ACTION_STATES.get(name)
        if state is None:
            raise ValueError(
                f"Действие «{name}» не поддерживается скин-ригом. "
                f"Доступно: {', '.join(sorted(PLAN_ACTION_STATES))}."
            )
        start = max(0.0, float(raw.get("start") or 0.0))
        dur = max(min_d, float(raw.get("duration") or min_d))
        params = raw.get("params") or {}
        if state == "emotion":
            # remember which emotion the verb named; the segment itself is neutral
            params = {**params, "emotion": name}
        items.append((start, dur, state, params))
    items.sort(key=lambda item: item[0])

    total = max(float(duration or 0.0), max(start + dur for start, dur, _, _ in items))
    segments: list[Segment] = []
    pos: Vec3 = PoseParams().root
    # profile view (forward = +X) is the default so foot lock is easy to judge;
    # a multi-actor scene passes yaw=0 and gets its world yaw from placement.
    yaw = float(yaw)
    cursor = 0.0

    for start, dur, state, params in items:
        gap = start - cursor
        if gap > 1e-6:
            segments.append(make_idle(gap, PoseParams(root=pos, yaw=yaw)))
            cursor = start
        emitted_from = len(segments)
        base = PoseParams(root=pos, yaw=yaw)
        if state == "walk":
            tf = params.get("target")
            if (
                tf is not None
                and isinstance(tf, (list, tuple))
                and len(tf) >= 2
            ):
                dx = float(tf[0])
                dy = float(tf[1])
                dist = math.hypot(dx, dy)
                if dist < 1e-3:
                    segments.append(make_idle(dur, base))
                else:
                    # F2.5: walk_to — идём к локальной точке (dx, dy) от текущего root
                    t_yaw = math.atan2(dy, dx)
                    sd = min(start_dur, dur * 0.2)
                    ed = min(stop_dur, dur * 0.2)
                    # Короткая дистанция: уменьшаем длину шага, чтобы за время
                    # ходьбы прошёл минимум один полный цикл (обе ноги по шагу) —
                    # иначе одна нога делает растянутый шаг и это читается как
                    # «перестановка» ступней.
                    stride_eff = max(0.15, min(stride, dist))
                    lead = stride_eff * 0.5
                    speed_max = 1.6
                    speed_eff = max(0.15, min(dist / max(dur - sd - ed, min_d), speed_max))
                    walk_d = max(min_d, dist / max(speed_eff, 1e-3))
                    start_seg, _ = make_start_walk(sd, replace(base, yaw=t_yaw), t_yaw, lead)
                    segments.append(start_seg)
                    origin0 = vadd(pos, vmul(rot_z(FWD, t_yaw), lead))
                    target0 = (pos[0] + dx, pos[1] + dy, pos[2])

                    def target_path(t: float) -> tuple[Vec3, float]:
                        u = min(1.0, max(0.0, t / max(walk_d, 1e-3)))
                        return (vlerp(origin0, target0, u), t_yaw)

                    segments.append(make_walk(walk_d, target_path, speed_eff, stride_eff))
                    gait = Gait(path=target_path, speed=speed_eff, stride=stride_eff, duty=0.58)
                    walk_end = _walk_params(gait, walk_d)
                    segments.append(make_stop(ed, walk_end, gait, walk_d, rot_z(FWD, t_yaw)))
            else:
                sd = min(start_dur, dur * 0.2)
                ed = min(stop_dur, dur * 0.2)
                walk_d = max(min_d, dur - sd - ed)
                cycles = max(1, int(round(walk_d / (stride / speed))))
                speed_eff = cycles * stride / walk_d
                lead = stride * 0.5
                start_seg, _ = make_start_walk(sd, base, yaw, lead)
                segments.append(start_seg)
                walk_path = path_constant(
                    vadd(pos, vmul(rot_z(FWD, yaw), lead)), yaw, speed_eff
                )
                segments.append(make_walk(walk_d, walk_path, speed_eff, stride))
                gait = Gait(path=walk_path, speed=speed_eff, stride=stride, duty=0.58)
                walk_end = _walk_params(gait, walk_d)
                segments.append(make_stop(ed, walk_end, gait, walk_d, rot_z(FWD, yaw)))
        elif state == "run":
            # Бег — отдельная локомоция: быстрее, шире шаг, короче фаза опоры,
            # выше подъём стопы.  Использует ту же Gait-механику, но свои
            # параметры цикла.
            tf = params.get("target")
            stride_run = 0.62
            duty_run = 0.50
            lift_run = 0.10
            run_speed = 1.9
            if (
                tf is not None
                and isinstance(tf, (list, tuple))
                and len(tf) >= 2
            ):
                dx = float(tf[0])
                dy = float(tf[1])
                dist = math.hypot(dx, dy)
                if dist < 1e-3:
                    segments.append(make_idle(dur, base))
                else:
                    t_yaw = math.atan2(dy, dx)
                    sd = min(start_dur, dur * 0.2)
                    ed = min(stop_dur, dur * 0.2)
                    lead = stride_run * 0.5
                    speed_max = 2.8
                    speed_eff = max(0.25, min(dist / max(dur - sd - ed, min_d), speed_max))
                    walk_d = max(min_d, dist / max(speed_eff, 1e-3))
                    start_seg, _ = make_start_walk(sd, replace(base, yaw=t_yaw), t_yaw, lead)
                    segments.append(start_seg)
                    origin0 = vadd(pos, vmul(rot_z(FWD, t_yaw), lead))
                    target0 = (pos[0] + dx, pos[1] + dy, pos[2])

                    def run_path(t: float) -> tuple[Vec3, float]:
                        u = min(1.0, max(0.0, t / max(walk_d, 1e-3)))
                        return (vlerp(origin0, target0, u), t_yaw)

                    gait = Gait(path=run_path, speed=speed_eff, stride=stride_run,
                                duty=duty_run, lift=lift_run)
                    segments.append(Segment("run", walk_d, lambda u: _walk_params(gait, u * walk_d)))
                    walk_end = _walk_params(gait, walk_d)
                    segments.append(make_stop(ed, walk_end, gait, walk_d, rot_z(FWD, t_yaw)))
            else:
                sd = min(start_dur, dur * 0.2)
                ed = min(stop_dur, dur * 0.2)
                walk_d = max(min_d, dur - sd - ed)
                cycles = max(1, int(round(walk_d / (stride_run / run_speed))))
                speed_eff = cycles * stride_run / walk_d
                lead = stride_run * 0.5
                start_seg, _ = make_start_walk(sd, base, yaw, lead)
                segments.append(start_seg)
                walk_path = path_constant(
                    vadd(pos, vmul(rot_z(FWD, yaw), lead)), yaw, speed_eff
                )
                gait = Gait(path=walk_path, speed=speed_eff, stride=stride_run,
                            duty=duty_run, lift=lift_run)
                segments.append(Segment("run", walk_d, lambda u: _walk_params(gait, u * walk_d)))
                walk_end = _walk_params(gait, walk_d)
                segments.append(make_stop(ed, walk_end, gait, walk_d, rot_z(FWD, yaw)))
        elif state == "idle":
            segments.append(make_idle(dur, base))
        elif state == "turn_in_place":
            delta = float(params.get("angle", math.pi * 0.5))
            segments.append(make_turn_in_place(dur, base, yaw, yaw + delta))
        elif state == "stop":
            # a standalone stop still needs speed to decelerate from: glide for
            # half the segment, then brake, so the feet never jump.
            half = max(min_d, dur * 0.5)
            speed_eff = max(0.3, stride / half)
            walk_path = path_constant(pos, yaw, speed_eff)
            gait = Gait(path=walk_path, speed=speed_eff, stride=stride, duty=0.58)
            segments.append(make_walk(half, walk_path, speed_eff, stride))
            segments.append(
                make_stop(
                    max(min_d, dur - half),
                    _walk_params(gait, half),
                    gait,
                    half,
                    rot_z(FWD, yaw),
                )
            )
        else:
            intensity = params.get("intensity", 1.0)
            keys = _scaled_overlay(state, base, intensity)
            # ``direction`` < 0 mirrors the gesture to the other side (a
            # listener on the right turns left, etc.)
            if float(params.get("direction", 1.0)) < 0:
                keys = _mirror_overlay(keys)
            segments.append(make_overlay(dur, state, base, keys))
        # acting: ``params['emotion']`` works on any state (walk included), and
        # an emotion verb is itself an emotion layer on a neutral body.
        emotion = str(params.get("emotion") or (state if state == "emotion" else ""))
        if emotion:
            actor_intensity = float(params.get("emotion_intensity", 1.0))
            for i in range(emitted_from, len(segments)):
                segments[i] = _apply_emotion(segments[i], emotion, actor_intensity)
        end = segments[-1].at(1.0)
        pos = end.root
        yaw = end.yaw
        cursor = start + sum(segment.duration for segment in segments[emitted_from:])

    if cursor < total - 1e-6:
        segments.append(make_idle(total - cursor, PoseParams(root=pos, yaw=yaw)))
    return Timeline(segments=segments, blend=blend)


# --------------------------------------------------------------------------
# multi-actor scenes: Milo and Nina share one armature with prefixed bones
# --------------------------------------------------------------------------

SCENE_PREFIX = ":"


def _rot_rows(rows: Sequence[float], yaw: float) -> tuple[float, ...]:
    """Rotate a row-major 3x3 rotation by ``yaw`` around Z."""
    c, s = math.cos(yaw), math.sin(yaw)
    r0, r1, r2, r3, r4, r5, r6, r7, r8 = rows
    return (
        c * r0 - s * r3, c * r1 - s * r4, c * r2 - s * r5,
        s * r0 + c * r3, s * r1 + c * r4, s * r2 + c * r5,
        r6, r7, r8,
    )


def _place_point(p: Sequence[float], origin: Vec3, yaw: float) -> Vec3:
    c, s = math.cos(yaw), math.sin(yaw)
    x, y, z = p
    return (origin[0] + c * x - s * y, origin[1] + s * x + c * y, origin[2] + z)


def _place_frames(
    frames: Sequence[dict[str, Sequence[float]]], origin: Vec3, yaw: float
) -> list[dict[str, list[float]]]:
    """Bake an actor offset/yaw into the encoded bone matrices."""
    placed_frames = []
    for frame in frames:
        placed = {}
        for name, vals in frame.items():
            head = _place_point(vals[:3], origin, yaw)
            rot = _rot_rows(vals[3:12], yaw)
            placed[name] = [round(head[0], 5), round(head[1], 5), round(head[2], 5)] + [
                round(v, 6) for v in rot
            ]
        placed_frames.append(placed)
    return placed_frames


def build_scene_payload(
    out_dir: str | Path,
    actors: Sequence[dict[str, Any]],
    *,
    scenario: str = "scene",
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    fit_fraction: float = 0.82,
    camera_lens: float = 42.0,
    samples: int = 32,
) -> tuple[Path, dict[str, Any]]:
    """Merge several rigged actors into one payload.

    Blender gets a single mesh object and a single armature whose bones are
    namespaced per actor (``milo:hips``, ``nina:hips``).  That keeps the proven
    single-deformation setup untouched while giving every character its own
    independent pose curves; the offsets and yaws are baked into the matrices.
    """
    if len(actors) < 2:
        raise ValueError("Сцена требует минимум двух персонажей.")

    verts: list[list[float]] = []
    faces: list[list[int]] = []
    weights: list[dict[str, float]] = []
    face_mats: list[int] = []
    materials: list[dict[str, Any]] = []
    bones: list[dict[str, Any]] = []
    frames: list[dict[str, list[float]]] = []
    windows: list[dict[str, Any]] = []
    actor_info: list[dict[str, Any]] = []
    names: list[str] = []
    total_duration = 0.0

    # ---- stage layout ---------------------------------------------------
    # A walking actor travels ~1.6 m, so a fixed grid of origins lets Milo walk
    # straight through Nina.  Measure every actor's own horizontal span from
    # its pose track and pack the actors side by side with a gap, in plan order.
    prepared: list[dict[str, Any]] = []
    for actor in actors:
        key = str(actor["character"]).lower()
        if key not in CHARACTERS:
            raise ValueError(f"Неизвестный персонаж «{key}».")
        spec_actions = list(actor.get("actions") or [])
        timeline = timeline_from_actions(
            spec_actions, duration=actor.get("duration"), fps=fps, yaw=0.0
        )
        poses = apply_visemes(
            timeline.sample_frames(fps), fps=fps, lines=actor.get("voice_lines")
        )
        yaw = float(actor.get("yaw") or 0.0)
        local = _place_frames(encode_frames(poses), (0.0, 0.0, 0.0), yaw)
        # hips, not every bone: arms and head would inflate the span and leave a
        # metre of empty air between two characters who are supposed to talk.
        xs = [frame["hips"][0] for frame in local]
        requested = tuple(actor.get("origin") or (0.0, 0.0, 0.0))
        prepared.append(
            {
                "key": key,
                "spec": CHARACTERS[key],
                "actions": spec_actions,
                "timeline": timeline,
                "poses": poses,
                "yaw": yaw,
                "local_min": min(xs),
                "local_max": max(xs),
                "origin_z": float(requested[2]),
            }
        )
        total_duration = max(total_duration, timeline.duration)

    #: body half-width and the empty space kept between two characters
    STAGE_MARGIN, STAGE_GAP = 0.24, 0.32
    cursor = 0.0
    for item in prepared:
        item["origin_x"] = cursor + STAGE_MARGIN - item["local_min"]
        cursor = item["origin_x"] + item["local_max"] + STAGE_MARGIN + STAGE_GAP
    stage_width = cursor - STAGE_GAP
    for item in prepared:
        item["origin"] = (item["origin_x"] - stage_width * 0.5, 0.0, item["origin_z"])

    for item in prepared:
        key = item["key"]
        spec = item["spec"]
        spec_actions = item["actions"]
        timeline = item["timeline"]
        mb = build_character_mesh(spec)
        data = mb.to_payload()
        origin = item["origin"]
        yaw = item["yaw"]
        prefix = f"{key}{SCENE_PREFIX}"
        base = len(verts)
        verts.extend([list(_place_point(v, origin, yaw)) for v in data["verts"]])
        faces.extend([[idx + base for idx in face] for face in data["faces"]])
        weights.extend(
            [{(prefix + bone): w for bone, w in wmap.items()} for wmap in data["weights"]]
        )
        mat_base = len(materials)
        materials.extend(data["materials"])
        face_mats.extend([mi + mat_base for mi in data["face_materials"]])
        for bone in BONES:
            bones.append(
                {
                    "name": prefix + bone.name,
                    "head": list(_place_point(bone.head, origin, yaw)),
                    "tail": list(_place_point(bone.tail, origin, yaw)),
                    "parent": (prefix + bone.parent) if bone.parent else None,
                }
            )
        poses = item["poses"]
        placed = _place_frames(encode_frames(poses), origin, yaw)
        if frames and len(placed) != len(frames):
            raise ValueError("Все персонажи сцены должны иметь одинаковое число кадров.")
        if not frames:
            frames = [{} for _ in placed]
        for i, frame in enumerate(placed):
            frames[i].update({prefix + name: vals for name, vals in frame.items()})
        for window in stance_windows(poses, fps):
            windows.append(
                {
                    **window,
                    "side": window["side"],
                    "actor": key,
                    "pos": list(_place_point(window["pos"], origin, yaw)),
                }
            )
        actor_info.append(
            {
                "character": key,
                "name": spec.name,
                "origin": [round(c, 4) for c in origin],
                "yaw": round(yaw, 4),
                "vert_start": base,
                "vert_end": len(verts),
                "actions": list(spec_actions),
                "duration": round(timeline.duration, 3),
            }
        )
        names.append(spec.name)
        total_duration = max(total_duration, timeline.duration)

    payload = {
        "character": "scene",
        "character_name": " + ".join(names),
        "scenario": scenario,
        "fps": fps,
        "samples": int(samples),
        "bones": bones,
        "verts": verts,
        "faces": faces,
        "weights": weights,
        "face_materials": face_mats,
        "materials": materials,
        "frames": frames,
        "stance_windows": windows,
        "actors": actor_info,
        "timeline_duration": round(total_duration, 3),
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"payload_{scenario}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path, payload


def build_scene_script(
    out_dir: str | Path,
    actors: Sequence[dict[str, Any]],
    *,
    scenario: str = "scene",
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    fit_fraction: float = 0.82,
    camera_lens: float = 42.0,
    samples: int = 32,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload_path, payload = build_scene_payload(
        out_dir,
        actors,
        scenario=scenario,
        fps=fps,
        width=width,
        height=height,
        fit_fraction=fit_fraction,
        camera_lens=camera_lens,
        samples=samples,
    )
    diag = out_dir / f"{scenario}_diagnostics.json"
    script = out_dir / f"skinned_{scenario}.py"
    text = (
        _BLENDER_TEMPLATE.replace("__PAYLOAD__", str(payload_path.resolve()))
        .replace("__DIAG__", str(diag.resolve()))
        .replace("__OUT__", str(out_dir.resolve()))
        .replace("__WIDTH__", str(int(width)))
        .replace("__HEIGHT__", str(int(height)))
        .replace("__FPS__", str(int(fps)))
        .replace("__FIT__", repr(float(fit_fraction)))
        .replace("__LENS__", repr(float(camera_lens)))
        .replace("__NAME__", payload["character_name"])
    )
    script.write_text(text, encoding="utf-8")
    return script


# --------------------------------------------------------------------------
# lip-sync: deterministic text -> viseme track (no phoneme model available)
# --------------------------------------------------------------------------

#: Viseme channels are ``(jaw_open, mouth_wide, mouth_round)`` in radians/metres.
#: They are deliberately exaggerated: at 1080x1920 a subtle mouth is invisible.
VISEMES: dict[str, tuple[float, float, float]] = {
    "rest": (0.00, 0.00, 0.00),
    "A": (0.34, 0.10, 0.00),
    "E": (0.16, 0.52, 0.00),
    "I": (0.09, 0.30, 0.00),
    "O": (0.30, -0.10, 0.55),
    "U": (0.14, -0.22, 0.85),
    "M": (0.00, -0.04, 0.00),
    "F": (0.07, 0.06, 0.00),
}

#: Character -> viseme.  This is a *grapheme* map, not a phoneme analyser: it
#: reads written letters, so a silent letter still moves the mouth and a
#: diphthong gets one shape.  Explicitly documented as a limitation.
_VISEME_CHARS: dict[str, str] = {
    "а": "A", "я": "A", "е": "E", "э": "E", "и": "I", "ы": "I", "й": "I",
    "о": "O", "ё": "O", "у": "U", "ю": "U",
    "б": "M", "п": "M", "м": "M", "ф": "F", "в": "F",
    "a": "A", "e": "E", "i": "I", "y": "I", "o": "O", "u": "U", "w": "U",
    "b": "M", "p": "M", "m": "M", "f": "F", "v": "F",
    "д": "E", "т": "E", "з": "E", "с": "E", "ц": "E", "ч": "E", "ш": "E",
    "щ": "E", "ж": "E", "л": "E", "н": "E", "р": "E", "г": "E", "к": "E",
    "х": "E", "d": "E", "t": "E", "s": "E", "z": "E", "n": "E", "l": "E",
    "r": "E", "g": "E", "k": "E", "h": "E", "j": "E", "c": "E", "x": "E",
}
_VOWELS = set("аяеэиыоёуюaeiouyw")

#: Characters that briefly close the mouth between words.
_VISEME_PAUSES = set(" \t\n.,!?…:;—-\"'()")


def _viseme_units(text: str) -> list[tuple[str, float]]:
    """Graphemes of ``text`` as ``(viseme, weight)`` pairs."""
    chars: list[tuple[str, float]] = []
    for ch in text.lower():
        if ch in _VISEME_PAUSES:
            chars.append(("rest", 0.5))
        elif ch in _VISEME_CHARS:
            chars.append((_VISEME_CHARS[ch], 1.0 if ch in _VOWELS else 0.7))
    return chars


def _speech_spans(
    segments: Sequence[Sequence[float]] | None, duration: float
) -> list[tuple[float, float]]:
    """Clamp authored ``[[start, end], ...]`` spans to ``[0, duration]``."""
    spans: list[tuple[float, float]] = []
    for item in segments or []:
        try:
            begin = max(0.0, float(item[0]))
            end = min(float(duration), float(item[1]))
        except (TypeError, ValueError, IndexError):
            continue
        if end - begin > 1e-3:
            spans.append((begin, end))
    spans.sort()
    merged: list[tuple[float, float]] = []
    for begin, end in spans:
        if merged and begin <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((begin, end))
    return merged


def viseme_track(
    text: str,
    *,
    start: float = 0.0,
    duration: float = 1.0,
    fps: int = 30,
    segments: Sequence[Sequence[float]] | None = None,
) -> list[tuple[float, str]]:
    """Spread the text's letters over ``duration`` as ``(time, viseme)`` keys.

    Every letter gets time proportional to its weight (vowels longer than
    consonants, pauses shortest), ending with ``rest`` so the mouth closes.

    ``segments`` are the *spoken* spans measured from the real WAV, relative to
    the line start.  When given, letters are distributed only inside those spans
    and the mouth stays shut in the gaps, so an internal pause of the recording
    no longer looks like continuous speech.  Without ``segments`` the previous
    text-only behaviour is preserved.
    """
    if duration <= 0:
        raise ValueError("Длительность реплики должна быть положительной.")
    chars = _viseme_units(text)
    if not chars:
        return [(start, "rest"), (start + duration, "rest")]
    spans = _speech_spans(segments, duration) or [(0.0, float(duration))]
    total = sum(weight for _, weight in chars)
    span_total = sum(end - begin for begin, end in spans) or float(duration)
    keys: list[tuple[float, str]] = []
    taken = 0
    for index, (begin, end) in enumerate(spans):
        share = (end - begin) / span_total
        count = (
            len(chars) - taken
            if index == len(spans) - 1
            else int(round(len(chars) * share))
        )
        count = max(0, min(count, len(chars) - taken))
        chunk = chars[taken:taken + count]
        taken += count
        if not chunk:
            continue
        local = sum(weight for _, weight in chunk) or total
        cursor = start + begin
        for viseme, weight in chunk:
            if not keys or keys[-1][1] != viseme:
                keys.append((round(cursor, 4), viseme))
            cursor += (end - begin) * weight / local
        keys.append((round(start + end, 4), "rest"))
    keys.append((round(start + duration, 4), "rest"))
    merged: list[tuple[float, str]] = []
    for when, viseme in keys:
        if merged and abs(merged[-1][0] - when) < 1e-6:
            merged[-1] = (when, viseme)
        else:
            merged.append((when, viseme))
    return merged


def apply_visemes(
    frames: Sequence[PoseParams],
    *,
    fps: int = 30,
    lines: Sequence[dict[str, Any]] | None = None,
) -> list[PoseParams]:
    """Blend the lip-sync channels of ``frames`` from timed speech lines.

    ``lines`` items are ``{"start", "duration", "text"}`` and may also carry data
    measured from the real take: ``segments`` (spoken spans in seconds relative
    to the line) and ``envelope``/``envelope_hz`` (loudness curve).  Visemes come
    from the text, while timing and loudness come from the WAV: the jaw only
    opens where the recording really has speech and opens wider on louder
    syllables.  Frames outside every line are returned untouched (closed mouth),
    so a silent scene stays identical to the pre-lip-sync behaviour.
    """
    if not lines or not frames:
        return list(frames)
    prepared: list[dict[str, Any]] = []
    for line in lines:
        begin = float(line.get("start") or 0.0)
        duration = float(line.get("duration") or 0.0)
        if duration <= 0:
            continue
        prepared.append(
            {
                "track": viseme_track(
                    str(line.get("text") or ""),
                    start=begin,
                    duration=duration,
                    fps=fps,
                    segments=line.get("segments"),
                ),
                "start": begin,
                "envelope": line.get("envelope") or (),
                "hz": float(line.get("envelope_hz") or 0.0),
            }
        )
    if not prepared:
        return list(frames)
    out: list[PoseParams] = []
    for index, frame in enumerate(frames):
        t = index / max(1, fps)
        channels: tuple[float, float, float] | None = None
        for item in prepared:
            track = item["track"]
            if len(track) < 2 or not (track[0][0] <= t < track[-1][0]):
                continue
            prev, nxt = track[-2], track[-1]
            for k in range(len(track) - 1):
                if track[k][0] <= t < track[k + 1][0]:
                    prev, nxt = track[k], track[k + 1]
                    break
            span = max(1e-6, nxt[0] - prev[0])
            w = smoothstep((t - prev[0]) / span)
            a = VISEMES[prev[1]]
            b = VISEMES[nxt[1]]
            channels = (lerp(a[0], b[0], w), lerp(a[1], b[1], w), lerp(a[2], b[2], w))
            envelope = item["envelope"]
            hz = item["hz"]
            if envelope and hz > 0:
                slot = int((t - item["start"]) * hz)
                level = float(envelope[slot]) if 0 <= slot < len(envelope) else 0.0
                gain = 0.0 if level < 0.06 else 0.25 + 0.75 * min(1.0, level)
                channels = (channels[0] * gain, channels[1] * gain, channels[2] * gain)
            break
        if channels is None:
            out.append(frame)
        else:
            # additive: an emotion's smile/frown must survive lip-sync
            out.append(
                replace(
                    frame,
                    jaw_open=round(min(0.6, max(0.0, frame.jaw_open + channels[0])), 4),
                    mouth_wide=round(min(1.0, max(-1.0, frame.mouth_wide + channels[1])), 4),
                    mouth_round=round(min(1.0, max(0.0, frame.mouth_round + channels[2])), 4),
                )
            )
    return out


# --------------------------------------------------------------------------
# frame encoding (pure python -> Blender payload)
# --------------------------------------------------------------------------


def encode_frames(frames: Sequence[PoseParams]) -> list[dict[str, list[float]]]:
    out: list[dict[str, list[float]]] = []
    for p in frames:
        solved = solve_pose(p)
        item: dict[str, list[float]] = {}
        for name in BONE_ORDER:
            h = solved.heads[name]
            r = solved.rots[name]
            item[name] = [round(h[0], 5), round(h[1], 5), round(h[2], 5)] + [
                round(x, 6) for x in mat_rows(r)
            ]
        out.append(item)
    return out


def stance_windows(frames: Sequence[PoseParams], fps: int) -> list[dict[str, Any]]:
    """Frames where a foot is expected to be world-locked (for drift checks)."""
    windows: list[dict[str, Any]] = []
    state: dict[str, dict[str, Any]] = {}
    last: dict[str, tuple[float, float, float]] = {}
    tol = 1e-6
    for i, p in enumerate(frames):
        for side in ("l", "r"):
            a = getattr(p, f"ankle_{side}")
            assert a is not None
            prev = last.get(side)
            cur = state.get(side)
            held = prev is not None and vlen(vsub(a, prev)) <= tol
            if held and cur is not None:
                cur["end"] = i
            elif cur is not None:
                if cur["end"] - cur["start"] >= 4:
                    windows.append(cur)
                state.pop(side, None)
            if not held:
                state[side] = {"side": side, "start": i, "end": i, "pos": [round(c, 5) for c in a]}
            last[side] = a
    for side, cur in state.items():
        if cur["end"] - cur["start"] >= 4:
            windows.append(cur)
    windows.sort(key=lambda w: w["start"])
    return windows


# --------------------------------------------------------------------------
# Blender script generation
# --------------------------------------------------------------------------

_BLENDER_TEMPLATE = r'''
import bpy, json, math, os, subprocess, sys, time
from pathlib import Path
from mathutils import Matrix, Vector

PAYLOAD = Path(r"__PAYLOAD__")
DIAG = Path(r"__DIAG__")
OUT = Path(r"__OUT__")
WIDTH = __WIDTH__
HEIGHT = __HEIGHT__
FPS = __FPS__
FIT = __FIT__
LENS = __LENS__
# safety clamps: 80 W POINT lights / emission 9.0 blow glowing props out to
# pure white (up to 2.76% of frame pixels), so values are capped at build time
MAX_POINT_ENERGY = 15.0
MAX_EMISSION = 2.5
CHAR_NAME = r"__NAME__"

data = json.loads(PAYLOAD.read_text(encoding="utf-8"))
T_START = time.time()
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = 'BLENDER_EEVEE_NEXT' if hasattr(bpy.types, 'BLENDER_EEVEE_NEXT') else 'BLENDER_EEVEE'
SAMPLES = int(data.get('samples') or 64)
try:
    scene.eevee.taa_render_samples = SAMPLES
except Exception:
    SAMPLES = 0
scene.render.resolution_x = WIDTH
scene.render.resolution_y = HEIGHT
scene.render.image_settings.file_format = 'PNG'
scene.render.fps = FPS
scene.render.film_transparent = False
try:
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
except Exception:
    pass
FRAMES = OUT / 'frames'
FRAMES.mkdir(parents=True, exist_ok=True)
scene.render.filepath = str(FRAMES / 'frame_')

# ---------- world / lights ----------
scene.world = bpy.data.worlds.new('Sky')
scene.world.use_nodes = True
bg = scene.world.node_tree.nodes.get('Background')
if bg:
    bg.inputs[0].default_value = (0.34, 0.52, 0.78, 1.0)
    bg.inputs[1].default_value = 0.55
ENV = data.get('environment') or {}
if bg and ENV.get('sky_rgb'):
    bg.inputs[0].default_value = (*ENV['sky_rgb'], 1.0)
    bg.inputs[1].default_value = float(ENV.get('sky_strength', 0.55))

sun_d = bpy.data.lights.new('Key', 'SUN')
sun = bpy.data.objects.new('Key', sun_d)
scene.collection.objects.link(sun)
sun.rotation_euler = (0.95, 0.15, 0.55)
sun_d.energy = 1.9
sun_d.angle = 0.3
if ENV.get('sun_energy'):
    sun_d.energy = float(ENV['sun_energy'])

fill_d = bpy.data.lights.new('Fill', 'AREA')
fill = bpy.data.objects.new('Fill', fill_d)
scene.collection.objects.link(fill)
fill.location = (-2.0, -2.5, 2.0)
fill.rotation_euler = (1.1, 0.0, -0.7)
fill_d.energy = 90.0
fill_d.size = 3.0
try:
    sun_d.use_contact_shadow = True
    fill_d.use_contact_shadow = True
except Exception:
    pass

# ---------- stage ----------
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, -0.10))
ground = bpy.context.active_object
ground.name = 'Stage'
ground.scale = (12.0, 12.0, 0.20)
gm = bpy.data.materials.new('StageMat')
gm.use_nodes = True
bs = gm.node_tree.nodes.get('Principled BSDF')
if bs:
    bs.inputs[0].default_value = (0.62, 0.66, 0.55, 1.0)
    bs.inputs['Roughness'].default_value = 0.85
if bs and ENV.get('ground_rgb'):
    bs.inputs[0].default_value = (*ENV['ground_rgb'], 1.0)
ground.data.materials.append(gm)

# ---------- props (optional, blocky environments) ----------
# Every prop is an axis-aligned box; the glowing cube carries keyframed
# location / emission tracks, a 'light' prop carries an energy track.
for pr in (data.get('props') or []):
    pname = str(pr.get('name') or 'prop')
    if pr.get('type') == 'light':
        ldata = bpy.data.lights.new(pname, 'POINT')
        lobj = bpy.data.objects.new(pname, ldata)
        scene.collection.objects.link(lobj)
        lobj.location = tuple(pr['center'])
        ldata.color = tuple(pr.get('rgb', (1.0, 1.0, 1.0)))
        ldata.energy = min(float(pr.get('energy', 10.0)), MAX_POINT_ENERGY)
        for fr, val in (pr.get('energy_track') or []):
            ldata.energy = min(float(val), MAX_POINT_ENERGY)
            ldata.keyframe_insert('energy', frame=int(fr))
        try:
            ldata.use_contact_shadow = True
        except Exception:
            pass
        continue
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=tuple(pr['center']))
    pob = bpy.context.active_object
    pob.name = pname
    pob.scale = tuple(pr.get('size', (1.0, 1.0, 1.0)))
    if pr.get('origin_offset'):
        # shift the mesh relative to the object origin so the origin lands on
        # a hinge edge; rot_track then swings the box around that hinge
        off = Vector(pr['origin_offset'])
        pob.data.transform(Matrix.Translation(off))
    pmat = bpy.data.materials.new(pname + '_mat')
    pmat.use_nodes = True
    pnode = pmat.node_tree.nodes.get('Principled BSDF')
    if pnode:
        pnode.inputs[0].default_value = (*pr.get('rgb', (0.5, 0.5, 0.5)), 1.0)
        if 'Roughness' in pnode.inputs:
            pnode.inputs['Roughness'].default_value = float(pr.get('roughness', 0.8))
        if 'Metallic' in pnode.inputs and pr.get('metallic'):
            pnode.inputs['Metallic'].default_value = float(pr['metallic'])
        if pr.get('emissive') and 'Emission Color' in pnode.inputs:
            pnode.inputs['Emission Color'].default_value = (
                *pr.get('emission_rgb', pr.get('rgb', (1.0, 1.0, 1.0))), 1.0)
            estr = pnode.inputs['Emission Strength']
            estr.default_value = min(float(pr.get('emission', 0.0)), MAX_EMISSION)
            for fr, val in (pr.get('emission_track') or []):
                estr.default_value = min(float(val), MAX_EMISSION)
                estr.keyframe_insert('default_value', frame=int(fr))
    pob.data.materials.append(pmat)
    if pr.get('tex') and Path(str(pr['tex'])).exists():
        t_img = bpy.data.images.load(str(pr['tex']), check_existing=True)
        t_node = pmat.node_tree.nodes.new('ShaderNodeTexImage')
        t_node.image = t_img
        t_node.interpolation = 'Closest'
        pmat.node_tree.links.new(t_node.outputs['Color'], pnode.inputs['Base Color'])
        _me = pob.data
        _uvl = _me.uv_layers.new(name='prop_uv')
        _me.uv_layers.active = _uvl
        for _poly in _me.polygons:
            _nl = _poly.normal
            _dom = max(range(3), key=lambda i: abs(_nl[i]))
            _axes = [i for i in range(3) if i != _dom]
            _ua, _va = _axes[0], _axes[1]
            _loop_i = list(_poly.loop_indices)
            _cos = [_me.vertices[_me.loops[li].vertex_index].co for li in _loop_i]
            _ua_lo = min(c[_ua] for c in _cos)
            _ua_hi = max(c[_ua] for c in _cos)
            _va_lo = min(c[_va] for c in _cos)
            _va_hi = max(c[_va] for c in _cos)
            for _li, _c in zip(_loop_i, _cos):
                _u = (_c[_ua] - _ua_lo) / (_ua_hi - _ua_lo or 1.0)
                _v = (_c[_va] - _va_lo) / (_va_hi - _va_lo or 1.0)
                _uvl.data[_li].uv = ((_u, _v))
    for fr, xyz in (pr.get('pos_track') or []):
        pob.location = tuple(xyz)
        pob.keyframe_insert('location', frame=int(fr))
    for fr, rxyz in (pr.get('rot_track') or []):
        pob.rotation_euler = tuple(rxyz)
        pob.keyframe_insert('rotation_euler', frame=int(fr))

# ---------- mesh ----------
me = bpy.data.meshes.new('HeroMesh')
me.from_pydata([tuple(v) for v in data['verts']], [], [tuple(f) for f in data['faces']])
me.validate(verbose=False)
me.update()
obj = bpy.data.objects.new('HeroBody', me)
scene.collection.objects.link(obj)

_skin_nodes = []
_skin_owner = {}
_skin_refs = []

for m in data['materials']:
    mat = bpy.data.materials.new(m['name'])
    mat.use_nodes = True
    p = mat.node_tree.nodes.get('Principled BSDF')
    tex = m.get('tex')
    if tex and p:
        img = bpy.data.images.load(tex, check_existing=True)
        img.colorspace_settings.name = 'sRGB'
        tnode = mat.node_tree.nodes.new('ShaderNodeTexImage')
        tnode.interpolation = 'Closest'
        tnode.image = img
        _skin_nodes.append(tnode)
        _skin_refs.append((str(m['name']).split(':')[0], mat.name, tnode.name))
        _skin_owner[tnode] = str(m['name']).split(':')[0]
        mat.node_tree.links.new(tnode.outputs['Color'], p.inputs['Base Color'])
        if 'Roughness' in p.inputs:
            p.inputs['Roughness'].default_value = 0.85
    elif p:
        p.inputs[0].default_value = (*m['rgb'], 1.0)
        if 'Roughness' in p.inputs:
            p.inputs['Roughness'].default_value = 0.62
    me.materials.append(mat)
for i, mi in enumerate(data['face_materials']):
    if i < len(me.polygons):
        me.polygons[i].material_index = mi
if data.get('uvs') and len(data['uvs']) == len(me.loops):
    uv_layer = me.uv_layers.new(name='skinuv')
    for li, uv in enumerate(data['uvs']):
        if uv is not None:
            uv_layer.data[li].uv = (uv[0], uv[1])
    me.uv_layers.active = uv_layer

# ---------- armature ----------
arm_data = bpy.data.armatures.new('HeroArmature')
arm = bpy.data.objects.new('HeroArmature', arm_data)
scene.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode='EDIT')
for b in data['bones']:
    eb = arm_data.edit_bones.new(b['name'])
    eb.head = tuple(b['head'])
    eb.tail = tuple(b['tail'])
for b in data['bones']:
    if b['parent']:
        arm_data.edit_bones[b['name']].parent = arm_data.edit_bones[b['parent']]
        arm_data.edit_bones[b['name']].use_connect = False
bpy.ops.object.mode_set(mode='OBJECT')
for b in data['bones']:
    arm_data.bones[b['name']].use_deform = True

# ---------- vertex groups + modifier (single deformation) ----------
groups = {}
for b in data['bones']:
    groups[b['name']] = obj.vertex_groups.new(name=b['name'])
for vi, wmap in enumerate(data['weights']):
    for bname, w in wmap.items():
        groups[bname].add([vi], float(w), 'REPLACE')
obj.parent = arm
obj.parent_type = 'OBJECT'
mod = obj.modifiers.new('Armature', 'ARMATURE')
mod.object = arm
mod.use_vertex_groups = True

for pb in arm.pose.bones:
    pb.rotation_mode = 'QUATERNION'

bone_order = [b['name'] for b in data['bones']]
rest_heads = {b['name']: Vector(b['head']) for b in data['bones']}
rest_local = {n: arm_data.bones[n].matrix_local.copy() for n in bone_order}
parents = {b['name']: b['parent'] for b in data['bones']}

# ---------- camera (fit by both height and travelled width) ----------
minx = miny = 1e9
maxx = maxy = -1e9
minz = 1e9
maxz = -1e9
for f in data['frames']:
    for n, vals in f.items():
        h = Vector(vals[:3])
        minx = min(minx, h.x); maxx = max(maxx, h.x)
        miny = min(miny, h.y); maxy = max(maxy, h.y)
        minz = min(minz, h.z); maxz = max(maxz, h.z)
char_h = max(1e-6, (maxz - minz) + 0.20)
char_w = max(1e-6, (maxx - minx) + 0.55)
cam_cx = (minx + maxx) * 0.5
char_cy = (miny + maxy) * 0.5
char_cz = (minz + maxz) * 0.5 + 0.02
_cam = data.get('camera') or {}
if _cam.get('center') and _cam.get('height'):
    # explicit framing (e.g. a face close-up for lip-sync review)
    cam_cx, char_cy, char_cz = [float(c) for c in _cam['center']]
    char_h = max(1e-6, float(_cam['height']))
    char_w = char_h * (WIDTH / HEIGHT)
cam_d = bpy.data.cameras.new('Cam')
cam = bpy.data.objects.new('Camera', cam_d)
scene.collection.objects.link(cam)
scene.camera = cam
cam_d.sensor_fit = 'VERTICAL'
cam_d.sensor_height = 18.0
cam_d.lens = LENS
tanv = (18.0 * 0.5) / LENS
tanh = tanv * (WIDTH / HEIGHT)
dist = max((char_h / FIT) / (2.0 * tanv), (char_w / FIT) / (2.0 * tanh))
cam.location = (cam_cx, char_cy - dist, char_cz)
cam.rotation_euler = (math.radians(90.0), 0.0, 0.0)

# ---------- montage cuts (optional) ----------
# Every cut = {frame, center, height, lens, fit, yaw?}.  The camera is keyframed
# at the first and the last frame of each segment with the same value, so within
# a shot it does not drift and between shots it jumps (a hard cut).
# A cut may also carry ``move`` = {to_frame, center?, height?, lens?, fit?, yaw?}
# for a smooth push-in / dolly inside the shot; the shot then ends on the target
# framing and both values stay keyed, so the next cut is still hard.
def _cam_pose(cx, cy, cz, height, lens, fit, yaw, tilt=0.0):
    tanv_ = (18.0 * 0.5) / lens
    tanh_ = tanv_ * (WIDTH / HEIGHT)
    dist_ = max((height / fit) / (2.0 * tanv_),
                (height * (WIDTH / HEIGHT) / fit) / (2.0 * tanh_))
    tilt_rad = math.radians(float(tilt))
    loc_ = (cx + dist_ * math.sin(yaw), cy - dist_ * math.cos(yaw),
            cz + dist_ * math.tan(tilt_rad))
    return loc_, (math.radians(90.0 - tilt), 0.0, yaw)

_cuts = data.get('camera_cuts') or []
if _cuts:
    _segs = []
    for _c in _cuts:
        _segs.append((max(1, int(_c['frame'])), {
            'center': [float(v) for v in _c['center']],
            'height': max(0.2, float(_c.get('height', 2.0))),
            'lens': float(_c.get('lens', LENS)),
            'fit': float(_c.get('fit', FIT)),
            'yaw': float(_c.get('yaw', 0.0)),
            'tilt': float(_c.get('tilt', 0.0)),
            'move': _c.get('move'),
        }))
    for _i, (_f0, _c) in enumerate(_segs):
        _f1 = (_segs[_i + 1][0] - 1) if _i + 1 < len(_segs) else scene.frame_end
        _loc, _rot = _cam_pose(_c['center'][0], _c['center'][1], _c['center'][2],
                               _c['height'], _c['lens'], _c['fit'], _c['yaw'],
                               _c['tilt'])
        cam.location = _loc
        cam.rotation_euler = _rot
        cam_d.lens = _c['lens']
        cam.keyframe_insert('location', frame=_f0)
        cam.keyframe_insert('rotation_euler', frame=_f0)
        cam_d.keyframe_insert('lens', frame=_f0)
        _mv = _c.get('move') or {}
        if _mv:
            _mf = min(max(_f0 + 1, int(_mv.get('to_frame', _f1))), max(_f0 + 1, _f1))
            _mloc, _mrot = _cam_pose(
                *[float(v) for v in _mv.get('center', _c['center'])],
                max(0.2, float(_mv.get('height', _c['height']))),
                float(_mv.get('lens', _c['lens'])),
                float(_mv.get('fit', _c['fit'])),
                float(_mv.get('yaw', _c['yaw'])),
                float(_mv.get('tilt', _c.get('tilt', 0.0))),
            )
            cam.location = _mloc
            cam.rotation_euler = _mrot
            cam_d.lens = float(_mv.get('lens', _c['lens']))
            cam.keyframe_insert('location', frame=_mf)
            cam.keyframe_insert('rotation_euler', frame=_mf)
            cam_d.keyframe_insert('lens', frame=_mf)
            if _f1 > _mf:
                cam.keyframe_insert('location', frame=_f1)
                cam.keyframe_insert('rotation_euler', frame=_f1)
                cam_d.keyframe_insert('lens', frame=_f1)
        else:
            cam.keyframe_insert('location', frame=_f1)
            cam.keyframe_insert('rotation_euler', frame=_f1)
            cam_d.keyframe_insert('lens', frame=_f1)
    # плавная интерполяция камеры между резами (избегаем резких «дёрганий»)
    try:
        if cam.animation_data and cam.animation_data.action:
            for _fc in cam.animation_data.action.fcurves:
                for _kp in _fc.keyframe_points:
                    _kp.interpolation = 'BEZIER'
    except Exception:
        pass

def pose_matrix(name, head, rot):
    R = Matrix(((rot[0], rot[1], rot[2]),
                (rot[3], rot[4], rot[5]),
                (rot[6], rot[7], rot[8]))).to_4x4()
    M = Matrix.Translation(Vector(head)) @ R @ Matrix.Translation(-rest_heads[name]) @ rest_local[name]
    return M

# ---------- mouth skin states: preload three variants per character ----------
_mouth_images = {}
_mouth_states = data.get('mouth_states') or {}
if _mouth_states:
    _skins_candidates = []
    for _m in data['materials']:
        _t = _m.get('tex')
        if _t:
            _p_t = Path(_t)
            if _p_t.is_absolute():
                _skins_candidates.append(_p_t.parent)
            break
    import os as _os
    _skins_candidates += [
        Path(_os.getcwd()) / "src" / "shortsfactory" / "blend3d" / "presets"
        / "textures" / "skins",
        Path(_os.getcwd()) / "assets" / "skins",
    ]
    for _char in _mouth_states:
        for _dir in _skins_candidates:
            _closed = _dir / f"{_char}_mouth_closed.png"
            if _closed.exists():
                for _st in ("closed", "round", "open"):
                    _p = _dir / f"{_char}_mouth_{_st}.png"
                    if _p.exists():
                        _mouth_images[(_char, _st)] = bpy.data.images.load(
                            str(_p), check_existing=True)
                break

# ---------- mouth skin switching: per-frame handler (used by the renderer) ----------
_mouth_dbg = {}

def _mouth_lookup(table, fi):
    for k in (fi, str(fi)):
        v = table.get(k)
        if v:
            return v
    return None

def _apply_mouth(scn, *args):
    fi = scn.frame_current - 1
    for owner, mname, nname in _skin_refs:
        table = _mouth_states.get(owner)
        if table is None:
            continue
        raw = _mouth_lookup(table, fi)
        st = "closed"
        if raw:
            stem = str(raw).rsplit('/', 1)[-1]
            st = stem[:-4].rsplit('_', 1)[-1]
        img = _mouth_images.get((owner, st))
        mat = bpy.data.materials.get(mname)
        if img is None or mat is None:
            continue
        node = mat.node_tree.nodes.get(nname)
        if node is not None and node.image != img:
            node.image = img
        _mouth_dbg[(owner, st)] = _mouth_dbg.get((owner, st), 0) + 1

if _mouth_images and _skin_refs:
    bpy.app.handlers.frame_change_pre.append(_apply_mouth)
print('MOUTH_SETUP owners=', sorted({r[0] for r in _skin_refs}),
      'state_keys=', sorted(_mouth_states.keys()),
      'images=', sorted(_mouth_images.keys()))

# ---------- apply poses ----------
scene.frame_start = 1
scene.frame_end = max(1, len(data['frames']))
intended = []
for fi, frame_data in enumerate(data['frames']):
    f = fi + 1
    world = {}
    for name in bone_order:
        vals = frame_data[name]
        head = Vector(vals[:3])
        rot = vals[3:12]
        M = pose_matrix(name, head, rot)
        world[name] = M
        p = parents[name]
        if p:
            rel = rest_local[p].inverted() @ rest_local[name]
            basis = (world[p] @ rel).inverted() @ M
        else:
            basis = rest_local[name].inverted() @ M
        pb = arm.pose.bones[name]
        pb.matrix_basis = basis
        pb.keyframe_insert(data_path='location', frame=f)
        pb.keyframe_insert(data_path='rotation_quaternion', frame=f)
    intended.append(world)

# ---------- diagnostics: realised pose vs intended ----------
diag = {'bones': len(bone_order), 'frames': len(data['frames']), 'fps': FPS,
        'samples': SAMPLES, 'resolution': [WIDTH, HEIGHT],
        'mesh': {'verts': len(me.vertices), 'faces': len(me.polygons)},
        'pose_error': {}, 'mesh_motion': {}, 'foot_contact': {}, 'blank_frames': {}}
if data.get('actors'):
    diag['actors'] = data['actors']
sample = sorted(set([0, len(data['frames']) // 4, len(data['frames']) // 2,
                     3 * len(data['frames']) // 4, len(data['frames']) - 1]))
max_err = 0.0
for fi in sample:
    scene.frame_set(fi + 1)
    dg = bpy.context.evaluated_depsgraph_get(); dg.update()
    for name in bone_order:
        got = arm.pose.bones[name].matrix
        exp = intended[fi][name]
        e = max(abs(got[i][j] - exp[i][j]) for i in range(4) for j in range(4))
        max_err = max(max_err, e)
diag['pose_error'] = {'max_abs_matrix_diff': round(max_err, 6),
                      'passed': bool(max_err < 1e-4)}

def region_of(wmap):
    if not wmap:
        return 'none'
    best = max(wmap.items(), key=lambda kv: kv[1])[0].split(':')[-1]
    if best in ('head', 'neck'):
        return 'head'
    if best.startswith(('upper_arm', 'forearm', 'hand', 'shoulder')):
        return 'arms'
    if best.startswith(('thigh', 'shin', 'foot')):
        return 'legs'
    return 'torso'

def eval_verts(frame_no):
    scene.frame_set(frame_no)
    dg = bpy.context.evaluated_depsgraph_get(); dg.update()
    ev = obj.evaluated_get(dg)
    m = ev.to_mesh()
    co = [tuple(v.co) for v in m.vertices]
    ev.to_mesh_clear()
    return co

nfr = len(data['frames'])
probe_frames = sorted(set([1, max(2, int(0.2 * nfr)), int(0.4 * nfr), int(0.6 * nfr),
                           int(0.8 * nfr), nfr]))
probe_frames = [f for f in probe_frames if 1 <= f <= nfr]
coords = {f: eval_verts(f) for f in probe_frames}
regions = {}
for vi, wmap in enumerate(data['weights']):
    r = region_of(wmap)
    best = 0.0
    for a_i in range(len(probe_frames)):
        for b_i in range(a_i + 1, len(probe_frames)):
            d = math.dist(coords[probe_frames[a_i]][vi], coords[probe_frames[b_i]][vi])
            best = max(best, d)
    regions.setdefault(r, []).append(best)
diag['mesh_motion'] = {r: {'max_disp': round(max(v), 4), 'mean_disp': round(sum(v) / len(v), 4),
                           'verts': len(v)}
                       for r, v in regions.items()}
diag['mesh_motion_probe_frames'] = probe_frames

# silhouette separation: the closest pair of vertices that belong to *different*
# actors.  E13 needs a real number here; two actors walking through each other
# otherwise look fine in per-actor pose and foot checks.
def _actor_key(wmap):
    for bone in wmap:
        if ':' in bone:
            return bone.split(':', 1)[0]
    return ''

actor_keys = sorted({_actor_key(w) for w in data['weights']} - {''})
actor_gap = {'measured': False, 'min_gap': None, 'min_frame': None, 'fps': FPS}
if len(actor_keys) >= 2:
    actor_gap['measured'] = True
    actor_gap['min_gap'] = 1.0
    for f in probe_frames:
        groups = {}
        for vi, wmap in enumerate(data['weights']):
            key = _actor_key(wmap)
            if key:
                groups.setdefault(key, []).append(coords[f][vi])
        keys = sorted(groups)
        for ai in range(len(keys)):
            for bi in range(ai + 1, len(keys)):
                for pa in groups[keys[ai]]:
                    for pb in groups[keys[bi]]:
                        d = math.dist(pa, pb)
                        if d < actor_gap['min_gap']:
                            actor_gap['min_gap'] = d
                            actor_gap['min_frame'] = f
    actor_gap['min_gap'] = round(actor_gap['min_gap'], 4)
diag['actor_gap'] = actor_gap

# foot drift over declared stance windows
windows = data.get('stance_windows', [])

def _foot_idxs(side, actor):
    # multi-actor scenes namespace bone names (milo:foot_L), so the actor has to
    # be part of the lookup: otherwise both characters' feet are averaged and the
    # drift reads as metres of sliding that never happened.
    prefix = (actor + ':') if actor else ''
    return [vi for vi, wmap in enumerate(data['weights'])
            if any((prefix + 'foot_' + side.upper()) in k for k in wmap)]
drift = {'windows': 0, 'max_drift': 0.0, 'max_penetration': 0.0, 'max_hover': 0.0}
for w in windows:
    s = w['start'] + 1
    e = w['end'] + 1
    idxs = _foot_idxs(w['side'], w.get('actor'))
    if not idxs or e <= s:
        continue
    drift['windows'] += 1
    base = eval_verts(s)
    ref = [base[i] for i in idxs]
    for f in range(s, e + 1, 2):
        co = eval_verts(f)
        for k, i in enumerate(idxs):
            d = math.hypot(co[i][0] - ref[k][0], co[i][1] - ref[k][1])
            drift['max_drift'] = max(drift['max_drift'], d)
        # sole height: the lowest vertex of the foot, not its highest
        zmin = min(co[i][2] for i in idxs)
        drift['max_penetration'] = max(drift['max_penetration'], -zmin)
        drift['max_hover'] = max(drift['max_hover'], zmin)
drift['max_drift'] = round(drift['max_drift'], 5)
drift['max_penetration'] = round(drift['max_penetration'], 5)
drift['max_hover'] = round(drift['max_hover'], 5)
diag['foot_contact'] = drift

with open(DIAG, 'w', encoding='utf-8') as f:
    json.dump(diag, f, ensure_ascii=False, indent=2)

# ---------- render ----------
T_RENDER = time.time()
bpy.ops.render.render(animation=True)
RENDER_S = round(time.time() - T_RENDER, 2)
print('MOUTH_DEBUG', _mouth_dbg)
T_BLANK = time.time()

# ---------- blank frame guard ----------
import struct
blank = {'checked': 0, 'blank': 0, 'min_std': 1.0, 'blank_frames': []}
step = max(1, len(data['frames']) // 12)
for fi in range(0, len(data['frames']), step):
    p = FRAMES / ('frame_%04d.png' % (fi + 1))
    if not p.exists():
        continue
    img = bpy.data.images.load(str(p), check_existing=False)
    px = list(img.pixels)
    n = len(px) // 4
    vals = [px[i * 4] for i in range(0, n, 17)]
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = math.sqrt(var)
    blank['checked'] += 1
    blank['min_std'] = min(blank['min_std'], std)
    if std < 0.01:
        blank['blank'] += 1
        blank['blank_frames'].append(fi + 1)
    bpy.data.images.remove(img)
blank['min_std'] = round(blank['min_std'], 4)
diag['blank_frames'] = blank
FRAMES_N = max(1, len(data['frames']))
diag['timings'] = {
    'prep_s': round(T_RENDER - T_START, 2),
    'render_s': RENDER_S,
    'blank_check_s': round(time.time() - T_BLANK, 2),
    'frames': FRAMES_N,
    'sec_per_frame': round(RENDER_S / FRAMES_N, 4),
    'samples': SAMPLES,
    'resolution': [WIDTH, HEIGHT],
}
with open(DIAG, 'w', encoding='utf-8') as f:
    json.dump(diag, f, ensure_ascii=False, indent=2)

ffmpeg = os.environ.get('FFMPEG_BIN', 'ffmpeg')
video = OUT / 'final.mp4'
seq = str(FRAMES / 'frame_%04d.png')
T_ENC = time.time()
subprocess.run([ffmpeg, '-y', '-framerate', str(FPS), '-i', seq,
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18',
                '-r', str(FPS), str(video)], check=True)
diag['timings']['encode_s'] = round(time.time() - T_ENC, 2)
diag['timings']['total_s'] = round(time.time() - T_START, 2)
with open(DIAG, 'w', encoding='utf-8') as f:
    json.dump(diag, f, ensure_ascii=False, indent=2)
print('SKINNED_TIMINGS', json.dumps(diag['timings']))
print('SKINNED_OK', video)
'''


@dataclass
class SkinnedResult:
    status: str
    video: str = ""
    script: str = ""
    payload: str = ""
    render_dir: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def build_skinned_payload(
    out_dir: str | Path,
    *,
    scenario: str = "states",
    character: str = "milo",
    fps: int = 30,
    width: int = 720,
    height: int = 1280,
    fit_fraction: float = 0.82,
    camera_lens: float = 42.0,
    samples: int = 64,
    actions: Sequence[dict[str, Any]] | None = None,
    duration: float | None = None,
    voice_lines: Sequence[dict[str, Any]] | None = None,
    camera: dict[str, Any] | None = None,
    yaw: float = math.pi / 2.0,
) -> tuple[Path, dict[str, Any]]:
    """Write the rig payload (mesh + per-frame bone matrices) and return it.

    `voice_lines` (`{start, duration, text}`) drive the mouth channels:
    without them the lips stay closed, exactly like before lip-sync existed.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = CHARACTERS[character]
    mb = build_character_mesh(spec)
    if actions:
        # production path: the plan decides what the character does and when
        timeline = timeline_from_actions(actions, duration=duration, fps=fps, yaw=yaw)
    else:
        timeline = default_timeline(scenario, spec)
    frames = apply_visemes(timeline.sample_frames(fps), fps=fps, lines=voice_lines)
    payload = {
        "character": spec.key,
        "character_name": spec.name,
        "scenario": scenario,
        "fps": fps,
        "samples": int(samples),
        "timeline_duration": round(timeline.duration, 3),
        **({'camera': camera} if camera else {}),
        "actions": [
            {
                "action": str(a.get("action") or a.get("state") or ""),
                "start": round(float(a.get("start") or 0.0), 3),
                "duration": round(float(a.get("duration") or 0.0), 3),
            }
            for a in (actions or [])
        ],
        "bones": [
            {"name": b.name, "head": list(b.head), "tail": list(b.tail), "parent": b.parent}
            for b in BONES
        ],
        **mb.to_payload(),
        "frames": encode_frames(frames),
        "stance_windows": stance_windows(frames, fps),
    }
    path = out_dir / f"payload_{character}_{scenario}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, payload


def build_skinned_script(
    out_dir: str | Path,
    *,
    scenario: str = "states",
    character: str = "milo",
    fps: int = 30,
    width: int = 720,
    height: int = 1280,
    fit_fraction: float = 0.82,
    camera_lens: float = 42.0,
    samples: int = 64,
    actions: Sequence[dict[str, Any]] | None = None,
    duration: float | None = None,
    voice_lines: Sequence[dict[str, Any]] | None = None,
    camera: dict[str, Any] | None = None,
    yaw: float = math.pi / 2.0,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload_path, payload = build_skinned_payload(
        out_dir,
        scenario=scenario,
        character=character,
        fps=fps,
        width=width,
        height=height,
        fit_fraction=fit_fraction,
        camera_lens=camera_lens,
        samples=samples,
        actions=actions,
        duration=duration,
        voice_lines=voice_lines,
        camera=camera,
        yaw=yaw,
    )
    diag = out_dir / f"{scenario}_{character}_diagnostics.json"
    script = out_dir / f"skinned_{character}_{scenario}.py"
    text = (
        _BLENDER_TEMPLATE.replace("__PAYLOAD__", str(payload_path.resolve()))
        .replace("__DIAG__", str(diag.resolve()))
        .replace("__OUT__", str(out_dir.resolve()))
        .replace("__WIDTH__", str(int(width)))
        .replace("__HEIGHT__", str(int(height)))
        .replace("__FPS__", str(int(fps)))
        .replace("__FIT__", repr(float(fit_fraction)))
        .replace("__LENS__", repr(float(camera_lens)))
        .replace("__NAME__", payload["character_name"])
    )
    script.write_text(text, encoding="utf-8")
    return script


def render_skinned(script: Path, *, blender_exe: str | None = None, timeout: int = 3600) -> SkinnedResult:
    import subprocess

    from ..blender_adapter import blender_executable

    exe = blender_exe or blender_executable()
    if not exe:
        return SkinnedResult(status="FAILED", error="Blender не найден.")
    out_dir = Path(script).parent
    try:
        proc = subprocess.run(
            [exe, "--background", "--python", str(script)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return SkinnedResult(status="FAILED", script=str(script), render_dir=str(out_dir), error="timeout")
    video = out_dir / "final.mp4"
    diag: dict[str, Any] = {}
    candidates = sorted(out_dir.glob("*_diagnostics.json"), key=lambda p: p.stat().st_mtime)
    if candidates:
        try:
            diag = json.loads(candidates[-1].read_text(encoding="utf-8"))
        except Exception:
            diag = {}
    if proc.returncode != 0 or not video.exists():
        return SkinnedResult(
            status="FAILED",
            script=str(script),
            render_dir=str(out_dir),
            diagnostics=diag,
            error=(proc.stderr or "")[-1200:],
        )
    # пост-рендер QC лип-синка: payload + WAV фильма -> метрики в diagnostics
    # под ключом "lip_sync".  Никогда не ломает рендер: без payload/WAV будет
    # {"status": "skipped", "reason": ...}.
    diag_path = None
    try:
        diag_path = sorted(out_dir.glob("*_diagnostics.json"), key=lambda p: p.stat().st_mtime)[-1]
    except (IndexError, OSError):
        diag_path = None
    if diag_path is not None:
        try:
            from .lip_sync_qc import attach_lip_sync_qc

            attach_lip_sync_qc(out_dir, script, diag_path, diag)
        except Exception as exc:  # QC обязана не ломать рендер
            diag["lip_sync"] = {"status": "skipped", "reason": str(exc)[-300:]}
    return SkinnedResult(
        status="OK",
        video=video.as_posix(),
        script=str(script),
        render_dir=str(out_dir),
        diagnostics=diag,
    )
