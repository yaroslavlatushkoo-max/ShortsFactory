"""Blocky (Minecraft-like) character style and the first blocky short film.

Why this module exists
----------------------
The skinned engine proved that a rigged, IK-driven character can look good, but
a blocky cartoon does not need smooth deformation at all.  Minecraft-style
characters are rigid boxes pivoting at joints.  This module adds:

* two original blocky characters, **Steve** and **Alex** (own geometry and
  colours — no Mojang assets, flat colours instead of extracted textures);
* ``build_blocky_character_mesh`` — rigid box parts bound 1.0 to single bones
  of the *same* 24-bone skeleton used by :mod:`.skinned_hero`, so the whole
  state machine (idle / walk / reach / emotions / IK foot lock) keeps working;
* a small scene assembler that merges actors, static/animated props (trees,
  rocks, a glowing cube) and montage camera cuts into one payload;
* procedural sound effects (numpy) for the prototype — no external audio.

Everything here is additive: payloads without ``style="blocky"`` / ``props`` /
``camera_cuts`` render exactly as before.
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

from ..blocky_props import collect_carry_tracks
from .skin_mouth import plan_mouth_texture
from .gestures import listener_gesture

from . import mouth_rig

from ...blend3d.host.skins import region_norm

from .skinned_hero import (
    BONES,
    CHARACTERS,
    CharacterSpec,
    MeshBuilder,
    PoseParams,
    SCENE_PREFIX,
    _BLENDER_TEMPLATE,
    _POSE_FIELDS,
    _place_frames,
    _place_point,
    apply_visemes,
    build_character_mesh,
    encode_frames,
    render_skinned,
    solve_pose,
    stance_windows,
    timeline_from_actions,

)

# --------------------------------------------------------------------------
# blocky characters (original designs in the blocky idiom, CC0-clean)
# --------------------------------------------------------------------------

#: Directory with the generated 64x64 Minecraft-style skin sheets (own CC0
#: pixel art, not Mojang assets).  Drop a real skin PNG named {name}_64.png
#: here (64x64 classic layout) to replace the placeholder art.
_SKINS_DIR = Path(__file__).resolve().parents[2] / "blend3d" / "presets" / "textures" / "skins"


def _skin_path(name: str) -> str | None:
    """Absolute path to ``{name}_mouth_closed.png`` if present, else None.

    The base skin is the closed-mouth variant, the only base texture the
    user provides (the legacy ``*_64.png`` files are never used).
    """
    p = _SKINS_DIR / f"{name}_mouth_closed.png"
    return str(p) if p.exists() else None


STEVE = CharacterSpec(
    key="steve",
    name="Steve",
    top=(0.00, 0.66, 0.66),        # cyan shirt
    bottom=(0.18, 0.24, 0.62),     # blue trousers
    skin=(0.72, 0.52, 0.36),
    hair=(0.26, 0.17, 0.10),       # dark brown
    shoes=(0.35, 0.35, 0.38),      # grey
    hair_style="blocky_short",
    style="blocky",
    skin_tex=_skin_path("steve"),
)

ALEX = CharacterSpec(
    key="alex",
    name="Alex",
    top=(0.35, 0.62, 0.28),        # green shirt
    bottom=(0.45, 0.32, 0.20),     # brown trousers
    skin=(0.85, 0.68, 0.55),
    hair=(0.72, 0.40, 0.16),       # orange, long at the back
    shoes=(0.45, 0.45, 0.48),
    hair_style="blocky_long",
    style="blocky",
    skin_tex=_skin_path("alex"),
)

CHARACTERS.setdefault("steve", STEVE)
CHARACTERS.setdefault("alex", ALEX)


# --------------------------------------------------------------------------
# blocky mesh: rigid boxes, one bone per part, no smooth deformation
# --------------------------------------------------------------------------


#: face-kind -> (u axis, v axis) used to UV-map a box face (0=x, 1=y, 2=z).
_FACE_UV_AXES: dict[str, tuple[int, int]] = {
    "bottom": (0, 1), "top": (0, 1),
    "front": (0, 2), "back": (0, 2),
    "left": (1, 2), "right": (1, 2),
}

#: The blocky characters face -Y, so in this coordinate system +X is the
#: character's LEFT side and -X is their RIGHT (right = forward x up).  The
#: Minecraft skin sheet labels "right"/"left" from the character's own point
#: of view, so side faces must swap before sampling the skin regions.
_SKIN_FACE_SWAP: dict[str, str] = {"left": "right", "right": "left"}


def _part_for_bone(bone: str) -> str | None:
    """Map a skeleton bone name to the Minecraft-style skin region part."""
    if bone == "head":
        return "head"
    if bone in ("hips", "chest"):
        return "body"
    if bone.startswith("upper_arm_") or bone.startswith("forearm_"):
        return "arm_r" if bone.endswith("R") else "arm_l"
    if bone.startswith("thigh_") or bone.startswith("shin_"):
        return "leg_r" if bone.endswith("R") else "leg_l"
    return None


def _box(
    mb: MeshBuilder,
    x0: float, x1: float,
    y0: float, y1: float,
    z0: float, z1: float,
    weights: dict[str, float],
    mat: int,
    *,
    skin_part: str | None = None,
    uv_vrange: tuple[float, float] = (0.0, 1.0),
    uv_flip_face: dict[str, tuple[bool, bool]] | None = None,
) -> None:
    """Add an axis-aligned box with outward-facing quads.

    When ``skin_part`` names a 64x64 skin region, every face gets UVs sampled
    from that region, so a single skin texture paints head/body/limbs without
    extra materials.  Skips UV painting when the builder has no skin texture.
    """
    verts = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    a, b, c, d, e, f, g, h = (mb._add_vert(p, weights) for p in verts)
    faces = (
        (a, d, c, b),   # bottom  (-Z)
        (e, f, g, h),   # top     (+Z)
        (a, b, f, e),   # front   (-Y, the character's face side)
        (c, d, h, g),   # back    (+Y)
        (a, e, h, d),   # left    (-X)
        (b, c, g, f),   # right   (+X)
    )
    for face in faces:
        mb.faces.append(face)
        mb.face_mats.append(mat)
    if not (skin_part and mb.skin_tex):
        return
    coords = {"x0": x0, "x1": x1, "y0": y0, "y1": y1, "z0": z0, "z1": z1}
    axes_names = ("x", "y", "z")
    base = len(mb.faces) - len(faces)
    for i, (face, kind) in enumerate(zip(faces, ("bottom", "top", "front", "back", "left", "right"))):
        u_axis, v_axis = _FACE_UV_AXES[kind]
        skin_face = _SKIN_FACE_SWAP.get(kind, kind)
        u0, v0, u1, v1 = region_norm(skin_part, skin_face)
        u_lo = coords[axes_names[u_axis] + "0"]
        u_hi = coords[axes_names[u_axis] + "1"]
        v_lo = coords[axes_names[v_axis] + "0"]
        v_hi = coords[axes_names[v_axis] + "1"]
        uvs = []
        for vi in face:
            p = mb.verts[vi]
            u = (p[u_axis] - u_lo) / (u_hi - u_lo or 1.0)
            v = (p[v_axis] - v_lo) / (v_hi - v_lo or 1.0)
            if uv_flip_face:
                flip_u, flip_v = uv_flip_face.get(kind, (False, False))
                if flip_u:
                    u = 1.0 - u
                if flip_v:
                    v = 1.0 - v
            v = uv_vrange[0] + v * (uv_vrange[1] - uv_vrange[0])
            uvs.append((u0 + u * (u1 - u0), v0 + v * (v1 - v0)))
        mb.paint_face_uvs(base + i, uvs)


def build_blocky_character_mesh(spec: CharacterSpec) -> MeshBuilder:
    """Blocky body on the standard skeleton: every part is a rigid box.

    Parts overlap slightly at the joints so rotations read as blocky hinges
    instead of gaps.  Each box is bound 1.0 to a single bone, which turns the
    armature deformation into plain rigid segment transforms — exactly the
    Minecraft look, with the IK foot lock of the skinned engine intact.
    """
    mb = MeshBuilder()
    m_shirt = mb.material("top", spec.top)
    m_pants = mb.material("bottom", spec.bottom)
    m_skin = mb.material("skin", spec.skin)
    m_hair = mb.material("hair", spec.hair)
    m_shoe = mb.material("shoe", spec.shoes)
    m_eyewhite = mb.material("eyewhite", (0.97, 0.97, 0.96))
    pupil_rgb = (0.20, 0.30, 0.60) if spec.key == "steve" else (0.20, 0.45, 0.22)
    m_pupil = mb.material("pupil", pupil_rgb)
    m_mouth = None
    if not mb.skin_tex:
        m_mouth = mb.material("mouth", (0.10, 0.07, 0.07))
    # brows sit on the hair fringe, so they must be darker than the hair to read
    m_brow = mb.material(
        "brow", tuple(max(0.0, c * 0.55) for c in spec.hair)
    )

    if spec.skin_tex:
        # a 64x64 skin sheet paints skin/shirt/pants faces via per-face UVs
        mb.set_skin(spec.skin_tex)
        mb.mark_skin_mat(m_skin)
        mb.mark_skin_mat(m_shirt)
        mb.mark_skin_mat(m_pants)

    W = 1.0

    # ---- head: 0.5 cube riding the head bone -----------------------------
    _box(mb, -0.25, 0.25, -0.25, 0.25, 1.30, 1.80, {"head": W}, m_skin,
         skin_part=_part_for_bone("head"),
         uv_flip_face={"top": (True, False), "bottom": (True, False)})
    # face (front = -Y): with a skin the eyes are painted on the texture;
    # solid 3D eye/pupil boxes are only used for flat-colour mode
    if not mb.skin_tex:
        _box(mb, 0.05, 0.17, -0.270, -0.250, 1.56, 1.72, {"head": W}, m_eyewhite)
        _box(mb, -0.17, -0.05, -0.270, -0.250, 1.56, 1.72, {"head": W}, m_eyewhite)
        _box(mb, 0.08, 0.14, -0.280, -0.262, 1.60, 1.68, {"head": W}, m_pupil)
        _box(mb, -0.14, -0.08, -0.280, -0.262, 1.60, 1.68, {"head": W}, m_pupil)
    # mouth: with a skin the mouth is fully painted on the texture variants
    # (closed/round/open); solid 3D mouth geometry is only used for
    # flat-colour mode.
    if not mb.skin_tex:
        m_lips = mb.material("lips", (0.13, 0.11, 0.11))
        m_tongue = mb.material("tongue", (0.45, 0.12, 0.14))
        # upper lip + tooth hint (static on the head)
        _box(mb, -0.10, 0.10, -0.265, -0.245, 1.410, 1.438, {"head": W}, m_lips)
        _box(mb, -0.075, 0.075, -0.260, -0.248, 1.400, 1.420, {"head": W}, m_eyewhite)
        # black oral cavity (recessed, never pokes through)
        _box(mb, -0.095, 0.095, -0.260, -0.205, 1.390, 1.445, {"head": W}, m_mouth)
        # dark-red tongue sits inside the cavity; it shows when the mouth opens
        _box(mb, -0.05, 0.05, -0.250, -0.215, 1.405, 1.435, {"head": W}, m_tongue)
        # lower lip rides the jaw; jaw_open drops it away from the upper lip
        _box(mb, -0.10, 0.10, -0.290, -0.265, 1.355, 1.388, {"jaw": W}, m_lips)
        # lip corners on their own bones (mouth_wide stretches the mouth sideways)
        _box(mb, 0.10, 0.125, -0.280, -0.255, 1.375, 1.425, {"lip_l": W}, m_lips)
        _box(mb, -0.125, -0.10, -0.280, -0.255, 1.375, 1.425, {"lip_r": W}, m_lips)
    _box(mb, 0.045, 0.155, -0.267, -0.259, 1.700, 1.720, {"brow_l": W}, m_brow)
    _box(mb, -0.155, -0.045, -0.267, -0.259, 1.700, 1.720, {"brow_r": W}, m_brow)
    # hair cap + back panel (Alex keeps hers long); with a skin the head is
    # painted by the sheet, so the flat hair boxes are skipped entirely
    if not mb.skin_tex:
        _box(mb, -0.26, 0.26, -0.26, 0.26, 1.62, 1.81, {"head": W}, m_hair)
        if spec.hair_style == "blocky_long":
            _box(mb, -0.26, 0.26, 0.16, 0.265, 1.36, 1.64, {"head": W}, m_hair)
            _box(mb, -0.265, -0.20, -0.20, 0.20, 1.48, 1.62, {"head": W}, m_hair)
            _box(mb, 0.20, 0.265, -0.20, 0.20, 1.48, 1.62, {"head": W}, m_hair)
        else:
            _box(mb, -0.26, 0.26, 0.16, 0.265, 1.44, 1.66, {"head": W}, m_hair)
            _box(mb, -0.265, -0.20, -0.22, 0.20, 1.52, 1.64, {"head": W}, m_hair)
            _box(mb, 0.20, 0.265, -0.22, 0.20, 1.52, 1.64, {"head": W}, m_hair)

    # ---- torso: two boxes (hips / chest) so the waist bends blockily ------
    _box(mb, -0.25, 0.25, -0.13, 0.13, 0.60, 0.96, {"hips": W}, m_shirt,
         skin_part="body", uv_vrange=(0.0, 0.5))
    _box(mb, -0.25, 0.25, -0.13, 0.13, 0.94, 1.30, {"chest": W}, m_shirt,
         skin_part="body", uv_vrange=(0.5, 1.0))

    # ---- arms: skin-coloured Minecraft-style, hinge at the elbow ----------
    for sign in (1.0, -1.0):
        side = "L" if sign > 0 else "R"
        x0, x1 = sorted((sign * 0.25, sign * 0.50))
        arm_part = _part_for_bone("upper_arm_" + side)
        # upper-arm skin region is sampled upside down: rotate its UVs 180°
        # (flip U and V) so the sleeve art reads the right way up.
        _box(mb, x0, x1, -0.125, 0.125, 1.24, 0.95, {"upper_arm_" + side: W}, m_skin,
             skin_part=arm_part, uv_vrange=(0.45, 1.0),
             uv_flip_face={k: (True, True) for k in
                           ("bottom", "top", "front", "back", "left", "right")})
        _box(mb, x0, x1, -0.125, 0.125, 0.97, 0.60, {"forearm_" + side: W}, m_skin,
             skin_part=arm_part, uv_vrange=(0.0, 0.45))

    # ---- legs: trousers, hinge at the knee, rigid shoe on the foot bone ---
    for sign in (1.0, -1.0):
        side = "L" if sign > 0 else "R"
        x0, x1 = (0.0, 0.25) if sign > 0 else (-0.25, 0.0)
        leg_part = _part_for_bone("thigh_" + side)
        _box(mb, x0, x1, -0.125, 0.125, 0.62, 0.36, {"thigh_" + side: W}, m_pants,
             skin_part=leg_part, uv_vrange=(0.0, 0.5))
        _box(mb, x0, x1, -0.125, 0.125, 0.38, 0.12, {"shin_" + side: W}, m_pants,
             skin_part=leg_part, uv_vrange=(0.5, 1.0))
        _box(mb, x0, x1, -0.27, 0.09, 0.02, 0.14, {"foot_" + side: W}, m_shoe)

    return mb


# --------------------------------------------------------------------------
# comic recoil hop: rigid world-space offset applied to sampled poses
# --------------------------------------------------------------------------


def ease_poses(
    poses: Sequence[PoseParams],
    fps: int = 30,
    *,
    kernel: int = 5,
    idle_motion: bool = True,
) -> list[PoseParams]:
    """Smooth a pose sequence with a symmetric Gaussian kernel per channel.

    Keeps the frame count identical (no re-sampling), so audio/lip-sync
    timing is preserved while mechanical bone pops are softened into cartoon
    motion.  A deterministic, non-random idle micro-motion is layered on the
    torso/head so characters breathe between actions.
    """
    n = len(poses)
    if n < 3:
        return list(poses)
    if kernel % 2 == 0:
        kernel += 1
    half = kernel // 2
    sigma = max(1.0, half / 2.0)
    weights = [
        math.exp(-((i - half) ** 2) / (2.0 * sigma * sigma))
        for i in range(kernel)
    ]
    total = sum(weights)
    weights = [w / total for w in weights]

    import copy
    out = [copy.deepcopy(p) for p in poses]
    for field in _POSE_FIELDS:
        if not hasattr(out[0], field):
            continue
        values = [getattr(p, field) for p in poses]
        if not all(isinstance(v, (int, float)) for v in values):
            continue
        for i in range(n):
            lo = max(0, i - half)
            hi = min(n - 1, i + half)
            ws = weights[half - (i - lo): half + (hi - i) + 1]
            wsum = sum(ws)
            acc = sum(v * w for v, w in zip(values[lo:hi + 1], ws))
            setattr(out[i], field, acc / wsum)

    if idle_motion:
        import math as _m
        for i, p in enumerate(out):
            t = i / max(1, fps)
            p.chest_pitch += 0.008 * _m.sin(2.0 * _m.pi * t / 1.7)
            p.head_pitch += 0.004 * _m.sin(2.0 * _m.pi * t / 2.3)
            p.arm_l_pitch += 0.006 * _m.sin(2.0 * _m.pi * t / 2.9)
            p.arm_r_pitch += 0.006 * _m.cos(2.0 * _m.pi * t / 2.9)
    return out


def apply_hop(
    poses: Sequence[PoseParams],
    fps: int,
    *,
    start: float,
    duration: float = 0.45,
    height: float = 0.16,
    back: float = 0.22,
) -> list[PoseParams]:
    """Leap backwards off the ground between ``start`` and ``start + duration``.

    The whole body (root + both ankle targets) follows the same parabola, so
    the IK keeps the blocky legs consistent and the recoil reads as one jump,
    not a pose glitch.
    """
    out: list[PoseParams] = [p.resolved() for p in poses]
    f0 = max(0, int(round(start * fps)))
    f1 = min(len(out), int(round((start + duration) * fps)))
    n = max(1, f1 - f0)
    for i in range(f0, f1):
        u = (i - f0 + 1) / n
        w = math.sin(math.pi * min(1.0, u))          # up … and down
        rush = min(1.0, u / 0.4) * (1.0 - 0.25 * u)  # backwards dash settles
        p = replace(out[i])
        dz = w * height
        dy = back * rush
        p.root = (p.root[0], p.root[1] + dy, p.root[2] + dz)
        for side in ("l", "r"):
            a = getattr(p, f"ankle_{side}")
            setattr(p, f"ankle_{side}", (a[0], a[1] + dy, a[2] + dz))
        # comic recoil: chest snaps back/up, arms fly half-way up
        p.chest_pitch = p.chest_pitch - 0.14 * w
        p.arm_l_pitch = p.arm_l_pitch - 0.45 * w
        p.arm_r_pitch = p.arm_r_pitch - 0.45 * w
        out[i] = p
    # the recoil is permanent: every later frame keeps the landed offset, so
    # the body never snaps back to its pre-hop spot
    if 0 < f0 and f0 <= f1:
        settled = back * (1.0 - 0.25)  # rush value at u == 1
        for i in range(f1, len(out)):
            p = replace(out[i])
            p.root = (p.root[0], p.root[1] + settled, p.root[2])
            for side in ("l", "r"):
                a = getattr(p, f"ankle_{side}")
                setattr(p, f"ankle_{side}", (a[0], a[1] + settled, a[2]))
            out[i] = p
    return out


# --------------------------------------------------------------------------
# scene assembly: blocky actors + props + camera cuts in one payload
# --------------------------------------------------------------------------


def _resolve_prop_sync(
    props: Sequence[dict[str, Any]],
    action_starts: dict[str, dict[str, float]],
    fps: int,
) -> list[dict[str, Any]]:
    """Shift prop animation tracks so they fire relative to an actor's action.

    A prop with ``sync: {actor, action, offset}`` has its track frames
    interpreted relative to the moment that actor's action begins (track frame
    1 == action start + offset).  This is what keeps a door opening exactly
    when the character touches it instead of at a hand-tuned absolute frame.
    """
    out: list[dict[str, Any]] = []
    for raw in props:
        prop = dict(raw)
        sync = prop.get("sync")
        if sync:
            actor_key = str(sync.get("actor") or "")
            verb = str(sync.get("action") or "").strip().lower()
            offset = float(sync.get("offset") or 0.0)
            starts = action_starts.get(actor_key) or {}
            if verb not in starts:
                raise ValueError(
                    f"Пропс «{prop.get('name')}»: sync ссылается на действие "
                    f"«{verb}» актёра «{actor_key}», а такого действия нет."
                )
            sync_frame = int(round((starts[verb] + offset) * fps))
            for track in ("pos_track", "rot_track", "emission_track", "energy_track"):
                entries = prop.get(track)
                if entries:
                    prop[track] = [[int(f) + sync_frame - 1, v] for f, v in entries]
        out.append(prop)
    return out


def _mouth_spans_from_voice(
    mskin: dict[str, Any],
    voice_lines: Sequence[dict[str, Any]] | None,
) -> tuple[list[tuple[float, float]] | None, str | None]:
    """Resolve absolute speech spans for a ``mouth_skin`` line from voice data.

    Search order: ``segments`` measured per voice line (stored relative to the
    line start), the line's own WAV re-analysed with the Mouth Rig envelope
    (the same algorithm as ``mouth_rig._speech_segments_from_wav``), then
    ``mouth_skin["wav"]``.  A voice line matches when its window overlaps the
    mouth_skin window; exact text matches win over plain overlaps.  Returns
    ``(spans, source)`` or ``(None, None)`` when nothing is resolvable — the
    caller then keeps the grapheme spread clamped to the mouth_skin duration.
    """
    try:
        ms0 = float(mskin.get("start") or 0.0)
        ms1 = ms0 + float(mskin.get("duration") or 0.0)
    except (TypeError, ValueError):
        return None, None
    if ms1 <= ms0:
        return None, None
    text = str(mskin.get("text") or "").strip().lower()
    candidates: list[dict[str, Any]] = []
    for line in voice_lines or ():
        try:
            ls = float(line.get("start") or 0.0)
            le = ls + float(line.get("duration") or 0.0)
        except (TypeError, ValueError):
            continue
        if min(le, ms1) - max(ls, ms0) <= 0:
            continue
        candidates.append(line)
    if text and candidates:
        exact = [
            ln for ln in candidates
            if str(ln.get("text") or "").strip().lower() == text
        ]
        candidates = exact or candidates

    spans: list[tuple[float, float]] = []
    source: str | None = None
    for line in candidates:
        ls = float(line.get("start") or 0.0)
        segs = line.get("segments")
        if segs:
            source = source or "voice_lines.segments"
            spans.extend(
                (ls + float(a), ls + float(b))
                for a, b in segs
                if float(b) > float(a)
            )
            continue
        wav = line.get("wav")
        if wav:
            try:
                segs = mouth_rig._speech_segments_from_wav(wav)
            except Exception:
                continue
            if segs:
                source = source or "voice_lines.wav"
                spans.extend((ls + a, ls + b) for a, b in segs)
    if not spans:
        wav = mskin.get("wav")
        if wav:
            try:
                segs = mouth_rig._speech_segments_from_wav(wav)
            except Exception:
                segs = []
            if segs:
                source = "mouth_skin.wav"
                spans.extend((ms0 + a, ms0 + b) for a, b in segs)
    if not spans:
        return None, None
    return sorted(spans), source


#: Пределы безопасности света и эмиссии: выше этих значений засветка
#: выжигает светящиеся пропсы в чистый белый (до 2.76% пикселей кадра).
MAX_POINT_ENERGY = 15.0
MAX_EMISSION_STRENGTH = 2.5


def _clamp_light_tracks(
    props: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Ограничивает энергия POINT-света (≤``MAX_POINT_ENERGY``) и силу эмиссии
    материалов (≤``MAX_EMISSION_STRENGTH``) в треках пропсов.

    Авторские сценарии могут задать 60–80 Вт или эмиссию 8–9 — на рендере это
    превратится в сплошное белое пятно.  Тайминги треков не меняются, только
    величины.
    """
    out: list[dict[str, Any]] = []
    for raw in props:
        prop = dict(raw)
        if str(prop.get("type") or "") == "light":
            if prop.get("energy_track"):
                prop["energy_track"] = [
                    [int(fr), min(float(val), MAX_POINT_ENERGY)]
                    for fr, val in prop["energy_track"]
                ]
            if prop.get("energy") is not None:
                prop["energy"] = min(float(prop["energy"]), MAX_POINT_ENERGY)
        else:
            if prop.get("emission_track"):
                prop["emission_track"] = [
                    [int(fr), min(float(val), MAX_EMISSION_STRENGTH)]
                    for fr, val in prop["emission_track"]
                ]
            if prop.get("emission") is not None:
                prop["emission"] = min(float(prop["emission"]), MAX_EMISSION_STRENGTH)
        out.append(prop)
    return out


def _apply_listener_gestures(
    actors: Sequence[dict[str, Any]],
    *,
    turn_to_speaker: bool = True,
) -> int:
    """Insert listener reactions into idle windows of silent actors (F4).

    For every actor that is speaking (has ``voice_lines``), every other actor
    whose plan is idle during the speech window gets a listener gesture
    (``listener_gesture`` — nod / react / ... picked from the speaker's
    emotion) inserted in place of that idle segment.  When ``turn_to_speaker``,
    a ``turn_in_place`` is prepended when the listener stands next to the
    speaker (offset mostly along X).  Returns how many gestures were inserted.
    """
    import math

    # collect speech windows per actor: (start, end, emotion)
    speech: dict[str, list[tuple[float, float, str]]] = {}
    for actor in actors:
        key = str(actor.get("character") or "").lower()
        for line in actor.get("voice_lines") or ():
            try:
                s = float(line.get("start") or 0.0)
                e = s + float(line.get("duration") or 0.0)
            except (TypeError, ValueError):
                continue
            if e <= s:
                continue
            emo = str(line.get("emotion") or actor.get("emotion") or "neutral")
            speech.setdefault(key, []).append((s, e, emo))
    if not speech:
        return 0

    origins = {str(a.get("character") or "").lower(): a.get("origin") or (0.0, 0.0, 0.0) for a in actors}
    inserted = 0
    for actor in actors:
        key = str(actor.get("character") or "").lower()
        actions = list(actor.get("actions") or [])
        changed = False
        for speaker, windows in speech.items():
            if speaker == key:
                continue
            for s, e, emo in windows:
                if e - s < 0.5:
                    continue
                # find an idle that fully covers [s, e)
                idx = None
                for i, act in enumerate(actions):
                    if str(act.get("action") or "") != "idle":
                        continue
                    try:
                        a0 = float(act.get("start") or 0.0)
                        a1 = a0 + float(act.get("duration") or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if a0 <= s + 1e-6 and a1 >= e - 1e-6:
                        idx = i
                        break
                if idx is None:
                    continue
                idle = actions[idx]
                a0 = float(idle.get("start") or 0.0)
                a1 = a0 + float(idle.get("duration") or 0.0)
                g_name, g_dur = listener_gesture(emo)
                g_dur = min(g_dur, max(0.4, e - s))
                if e - s - g_dur < 0.05:
                    g_dur = max(0.4, e - s)

                seg: list[dict[str, Any]] = []
                if s - a0 > 0.05:
                    seg.append({"action": "idle", "start": round(a0, 3),
                               "duration": round(s - a0, 3)})
                if turn_to_speaker:
                    sx = float(origins.get(speaker, (0.0, 0.0, 0.0))[0])
                    lx = float(origins.get(key, (0.0, 0.0, 0.0))[0])
                    dx = sx - lx
                    if abs(dx) > 0.15 and e - s - g_dur >= 0.8:
                        angle = math.pi / 2.0 if dx > 0 else -math.pi / 2.0
                        seg.append({"action": "turn_in_place",
                                   "start": round(s, 3), "duration": 0.8,
                                   "params": {"angle": round(angle, 4)}})
                        seg.append({"action": g_name,
                                   "start": round(s + 0.8, 3),
                                   "duration": round(g_dur, 3)})
                        gesture_end = s + 0.8 + g_dur
                    else:
                        seg.append({"action": g_name, "start": round(s, 3),
                                   "duration": round(g_dur, 3)})
                        gesture_end = s + g_dur
                else:
                    seg.append({"action": g_name, "start": round(s, 3),
                               "duration": round(g_dur, 3)})
                    gesture_end = s + g_dur
                after = a1 - gesture_end
                if after > 0.05:
                    seg.append({"action": "idle",
                               "start": round(gesture_end, 3),
                               "duration": round(after, 3)})
                actions[idx:idx + 1] = seg
                changed = True
                inserted += 1
        if changed:
            actor["actions"] = actions
    return inserted


# --------------------------------------------------------------------------
# B5: авто-реакции слушателя для payload-пути
# --------------------------------------------------------------------------

#: Глаголы, которые означают «просто стоит»: реакция слушателя может их
#: разрезать.  Авторские (не-idle) действия в окне речи — неприкосновенны.
LISTENER_IDLE_VERBS = frozenset({"idle", "stand", "wait", "return_idle"})
#: Обрывки idle короче этого выбрасываются, а не дёргаются на кадре.
LISTENER_MIN_IDLE_PART = 0.15
#: Одна реакция длится не дольше этого, даже если реплика длиннее.
LISTENER_MAX_REACTION = 4.0


def _listener_actor_x(actor: dict[str, Any]) -> float:
    """World X of an actor (0.0 when the origin is missing or malformed)."""
    origin = actor.get("origin") or (0.0, 0.0, 0.0)
    try:
        return float(origin[0] or 0.0)
    except (TypeError, ValueError, IndexError):
        return 0.0


def _merge_speech_windows(
    spans: Sequence[tuple[float, float, str, str]],
) -> list[tuple[float, float, str, str]]:
    """Union of overlapping line windows — a listener never reacts twice at once.

    ``spans`` items are ``(start, end, emotion, speaker)``.  Overlapping (and
    touching) windows collapse into one; the earliest line keeps its emotion
    and speaker so the result stays deterministic.
    """
    merged: list[list[Any]] = []
    for start, end, emotion, speaker in sorted(spans, key=lambda t: (t[0], t[1])):
        if merged and start <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end, emotion, speaker])
    return [(s, e, emo, spk) for s, e, emo, spk in merged]


def _carve_listener_idle(
    actions: list[dict[str, Any]],
    begin: float,
    end: float,
    *,
    min_part: float = LISTENER_MIN_IDLE_PART,
) -> bool:
    """Free the ``[begin, end]`` window by trimming idle actions.

    Mirror of ``blocky_scene._carve_idle``: idle is split into "before" and
    "after" pieces, parts shorter than ``min_part`` are dropped instead of
    producing a twitch.  Returns False when an *authored* (non-idle) action
    occupies the window — the caller then leaves the listener alone.
    """
    for act in actions:
        a0 = float(act.get("start") or 0.0)
        a1 = a0 + float(act.get("duration") or 0.0)
        if a1 <= begin + 1e-6 or a0 >= end - 1e-6:
            continue
        if str(act.get("action") or "").strip().lower() not in LISTENER_IDLE_VERBS:
            return False
    kept: list[dict[str, Any]] = []
    for act in actions:
        a0 = float(act.get("start") or 0.0)
        a1 = a0 + float(act.get("duration") or 0.0)
        if a1 <= begin + 1e-6 or a0 >= end - 1e-6:
            kept.append(act)
            continue
        if a0 < begin - 1e-6 and begin - a0 >= min_part:
            kept.append({**act, "start": round(a0, 3), "duration": round(begin - a0, 3)})
        if a1 > end + 1e-6 and a1 - end >= min_part:
            kept.append({**act, "start": round(end, 3), "duration": round(a1 - end, 3)})
    actions[:] = kept
    return True


def _apply_listener_reactions(actors: Sequence[dict[str, Any]]) -> int:
    """Insert listener reactions into the idle windows of silent actors.

    The scene path (``BlockyScene.render`` with ``auto_act``) already does this
    via ``blocky_scene._apply_listener_reactions``; films built straight through
    :func:`build_blocky_scene_payload` bypassed it and the listener stood frozen.
    For every actor, the listening windows are the union of the *other* actors'
    ``voice_lines`` spans.  A window is skipped when an authored (non-idle)
    action occupies it; idle-like actions are carved (see
    :func:`_carve_listener_idle`).  The gesture comes from
    :func:`gestures.listener_gesture` keyed by the spoken line's emotion, and
    ``params["direction"]`` points the listener at the speaker.  Deterministic:
    no randomness anywhere, same input gives the same actions.  Returns how
    many reactions were inserted; ``actor["duration"]`` is never touched.
    """
    speech: dict[str, list[tuple[float, float, str]]] = {}
    for actor in actors:
        key = str(actor.get("character") or "")
        for line in actor.get("voice_lines") or ():
            try:
                start = float(line.get("start") or 0.0)
                duration = float(line.get("duration") or 0.0)
            except (TypeError, ValueError):
                continue
            if duration <= 0:
                continue
            emotion = str(line.get("emotion") or "neutral")
            speech.setdefault(key, []).append((start, start + duration, emotion))
    if not speech:
        return 0
    xs = {
        str(actor.get("character") or ""): _listener_actor_x(actor)
        for actor in actors
    }
    added = 0
    for actor in actors:
        who = str(actor.get("character") or "")
        # only the lines of OTHER actors turn this one into a listener
        spans = [
            (start, end, emotion, speaker)
            for speaker, lines in speech.items() if speaker != who
            for (start, end, emotion) in lines
        ]
        if not spans:  # monologue: nobody else speaks, nothing to react to
            continue
        listener_x = _listener_actor_x(actor)
        actions = actor.setdefault("actions", [])
        for begin, end, emotion, speaker in _merge_speech_windows(spans):
            if not _carve_listener_idle(actions, begin, end):
                continue  # authored beat is sacred: only idle may be carved
            verb, _gesture_dur = listener_gesture(emotion)
            direction = 1.0 if xs.get(speaker, 0.0) >= listener_x else -1.0
            actions.append(
                {
                    "action": verb,
                    "start": round(begin, 3),
                    "duration": round(min(LISTENER_MAX_REACTION, end - begin), 3),
                    "params": {"direction": direction},
                }
            )
            actions.sort(key=lambda act: float(act.get("start") or 0.0))
            added += 1
    return added


def build_blocky_scene_payload(
    out_dir: str | Path,
    actors: Sequence[dict[str, Any]],
    *,
    props: Sequence[dict[str, Any]] | None = None,
    camera_cuts: Sequence[dict[str, Any]] | None = None,
    environment: dict[str, Any] | None = None,
    scenario: str = "blocky_scene",
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    samples: int = 32,
) -> tuple[Path, dict[str, Any]]:
    """Merge blocky actors (and optional props / camera cuts) into a payload.

    ``actors`` items: ``{character, origin, actions, duration, yaw?, hops?,
    voice_lines?}``.  Returns the payload path (also used by the Blender
    template) and the payload dict.
    """
    if not actors:
        raise ValueError("Сцена требует хотя бы одного персонажа.")

    # F4: живой слушатель — вставляем жесты-реакции в idle-окна молчащих
    # актёров рядом с речью других.  Оба прохода ДО выпекания кадров:
    # _apply_listener_gestures закрывает окна с одним цельным idle,
    # _apply_listener_reactions — остальные (раскроенные idle), иначе реакция
    # попала бы только в метаданные и слушатель не анимировался бы в рендере.
    if len(actors) >= 2 and any(actor.get("voice_lines") for actor in actors):
        _apply_listener_gestures(actors)
        _apply_listener_reactions(actors)

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
    finals: dict[str, tuple[float, float, float]] = {}
    action_starts: dict[str, dict[str, float]] = {}
    total_duration = 0.0
    uvs_all: list[tuple[float, float] | None] = []
    uvs_ok = True
    uv_errors: list[str] = []
    flat_actors: list[str] = []

    for actor in actors:
        key = str(actor["character"]).lower()
        if key not in CHARACTERS:
            raise ValueError(f"Неизвестный персонаж «{key}».")
        spec = CHARACTERS[key]
        actions = list(actor.get("actions") or [])
        timeline = timeline_from_actions(actions, duration=actor.get("duration"), fps=fps, yaw=0.0)
        poses = apply_visemes(timeline.sample_frames(fps), fps=fps, lines=actor.get("voice_lines"))
        # Blocky faces sit far from the camera, so amplify the mouth channels
        # (open / wide / round) to keep distinct visemes readable.  This is
        # blocky-only: the smooth engine already exaggerates enough.
        poses = [
            replace(
                p,
                jaw_open=min(0.6, p.jaw_open * 1.6),
                mouth_wide=max(-1.0, min(1.0, p.mouth_wide * 1.5)),
                mouth_round=min(1.0, p.mouth_round * 1.4),
            )
            for p in poses
        ]
        for hop in actor.get("hops") or []:
            poses = apply_hop(poses, fps, **hop)
        # cartoon smoothing: soften mechanical bone pops, keep frame count and
        # lip-sync timing intact; deterministic idle micro-motion is optional
        poses = ease_poses(poses, fps, idle_motion=actor.get("idle_motion", True))
        if actor.get("mouth_smoothing", True):
            poses = mouth_rig.smooth_mouth_channels(poses, kernel=3)
        if actor.get("emotion"):
            poses = mouth_rig.emotion_overlay(
                poses, actor["emotion"], fps=fps,
                strength=actor.get("emotion_strength", 1.0),
            )
        # optional explicit Mouth Rig track: overrides viseme channels with the
        # chosen shape per frame (silence stays closed, deterministic)
        mouth_track = actor.get("mouth_track")
        if mouth_track:
            track_by_frame = dict(mouth_track)
            rebuilt = []
            for idx, pose in enumerate(poses):
                shape = track_by_frame.get(idx)
                if shape and shape in mouth_rig.MOUTH_SHAPES:
                    j, w, r = mouth_rig.MOUTH_SHAPES[shape]
                    pose = replace(pose, jaw_open=float(j), mouth_wide=float(w), mouth_round=float(r))
                rebuilt.append(pose)
            poses = rebuilt
        yaw = float(actor.get("yaw") or 0.0)
        origin = tuple(actor.get("origin") or (0.0, 0.0, 0.0))

        mb = build_character_mesh(spec)
        data = mb.to_payload()
        prefix = f"{key}{SCENE_PREFIX}"
        base = len(verts)
        verts.extend([list(_place_point(v, origin, yaw)) for v in data["verts"]])
        faces.extend([[idx + base for idx in face] for face in data["faces"]])
        weights.extend(
            [{(prefix + bone): w for bone, w in wmap.items()} for wmap in data["weights"]]
        )
        mat_base = len(materials)
        for material_entry in data["materials"]:
            material_entry["name"] = f"{key}:{material_entry['name']}"
        materials.extend(data["materials"])
        face_mats.extend([mi + mat_base for mi in data["face_materials"]])
        # merge per-actor skin UV loops; a mismatch would silently shift the
        # texture of every following actor, so it must fail loudly instead
        d_uvs = data.get("uvs")
        loops = sum(len(f) for f in data["faces"])
        if d_uvs is None or len(d_uvs) != loops:
            if any("tex" in m for m in data["materials"]):
                uv_errors.append(
                    f"«{key}»: UV-координат "
                    f"{'нет' if d_uvs is None else len(d_uvs)}, "
                    f"а вершин граней {loops}"
                )
            else:
                flat_actors.append(key)
            uvs_ok = False
        else:
            uvs_all.extend(d_uvs)
        for bone in BONES:
            bones.append(
                {
                    "name": prefix + bone.name,
                    "head": list(_place_point(bone.head, origin, yaw)),
                    "tail": list(_place_point(bone.tail, origin, yaw)),
                    "parent": (prefix + bone.parent) if bone.parent else None,
                }
            )
        placed = _place_frames(encode_frames(poses), origin, yaw)
        if frames and len(placed) != len(frames):
            raise ValueError(
                f"Все персонажи сцены должны иметь одинаковое число кадров: "
                f"{key}={len(placed)} кадров, у ранее добавленных актёров {len(frames)}"
            )
        if not frames:
            frames = [{} for _ in placed]
        for i, frame in enumerate(placed):
            frames[i].update({prefix + name: vals for name, vals in frame.items()})
        for window in stance_windows(poses, fps):
            windows.append(
                {**window, "actor": key, "pos": list(_place_point(window["pos"], origin, yaw))}
            )
        last_root = poses[-1].resolved().root
        finals[key] = (origin[0] + last_root[0], origin[1] + last_root[1], origin[2] + last_root[2])
        starts = action_starts.setdefault(key, {})
        for act in actions:
            verb = str(act.get("action") or "").strip().lower()
            start = float(act.get("start") or 0.0)
            if verb and (verb not in starts or start < starts[verb]):
                starts[verb] = start
        actor_info.append(
            {
                "character": key,
                "name": spec.name,
                "origin": [round(c, 4) for c in origin],
                "yaw": round(yaw, 4),
                "vert_start": base,
                "vert_end": len(verts),
                "actions": actions,
                "duration": round(timeline.duration, 3),
                "final_root": [round(c, 4) for c in finals[key]],
            }
        )
        names.append(spec.name)
        total_duration = max(total_duration, timeline.duration)

    # текстуры кожи без корректной UV-разметки не рендерим молча: падаем с
    # понятной ошибкой, пока проблема не дошла до Blender
    if uv_errors or (not uvs_ok and uvs_all):
        details = "; ".join(uv_errors) or (
            "актёры без текстуры кожи (" + ", ".join(flat_actors) + ") "
            "смешаны с актёрами с UV-разметкой"
        )
        raise ValueError(
            "Не совпадает число UV-координат с числом вершин граней: "
            f"{details}. Рендер прерван, чтобы не потерять текстуры кожи молча."
        )

    has_uvs = bool(uvs_all) and any(uv is not None for uv in uvs_all)

    payload: dict[str, Any] = {
        "character": "scene",
        "character_name": " + ".join(names),
        "scenario": scenario,
        "style": "blocky",
        "fps": fps,
        "samples": int(samples),
        "bones": bones,
        "verts": verts,
        "faces": faces,
        "weights": weights,
        "face_materials": face_mats,
        "materials": materials,
        **({"uvs": [list(uv) if uv else None for uv in uvs_all]} if has_uvs else {}),
        "frames": frames,
        "stance_windows": windows,
        "actors": actor_info,
        "timeline_duration": round(total_duration, 3),
    }
    if props:
        payload["props"] = _clamp_light_tracks(
            _resolve_prop_sync(
                collect_carry_tracks(props, frames, fps=fps), action_starts, fps
            )
        )
    if camera_cuts:
        payload["camera_cuts"] = [dict(c) for c in camera_cuts]
    if environment:
        payload["environment"] = dict(environment)
    mouth_states: dict[str, dict[int, str]] = {}
    mouth_segments: dict[str, dict[str, Any]] = {}
    for _actor in actors:
        mskin = _actor.get("mouth_skin") or {}
        text = str(mskin.get("text") or "").strip()
        if not text:
            continue
        lang = str(mskin.get("lang") or "ru")
        segments_raw = mskin.get("segments")
        seg_source = "mouth_skin.segments" if segments_raw else None
        if not segments_raw:
            # нет явных сегментов — пробуем восстановить их по голосу актёра,
            # чтобы рот не двигался после конца озвучки
            resolved, seg_source = _mouth_spans_from_voice(
                mskin, _actor.get("voice_lines")
            )
            if resolved:
                segments_raw = resolved
        abs_spans: list[tuple[float, float]] | None = None
        if segments_raw:
            spans = [(max(0.0, float(a)), float(b))
                     for a, b in segments_raw if float(b) > float(a)]
            start = min((a for a, _b in spans), default=0.0)
            dur = max((b for _a, b in spans), default=0.0)
            abs_spans = list(spans)
            # планировщик работает в относительном времени от первого старта
            spans = [(a - start, b - start) for a, b in spans]
        else:
            spans = None
            start = float(mskin.get("start") or 0.0)
            dur = float(mskin.get("duration") or 0.0)
        key = str(_actor["character"]).lower()
        if dur > 0:
            shapes = plan_mouth_texture(
                text, lang, duration=dur, fps=fps, segments=spans)
            f0 = int(round(start * fps))
            total = len(frames)
            mouth_states[key] = {
                f0 + i: f"{key}_mouth_{s}.png"
                for i, s in enumerate(shapes) if f0 + i < total
            }
            if abs_spans:
                mouth_segments[key] = {
                    "source": seg_source or "unknown",
                    "segments": [
                        [round(a, 4), round(b, 4)] for a, b in abs_spans
                    ],
                }
    if mouth_states:
        payload["mouth_states"] = mouth_states
    if mouth_segments:
        payload["mouth_segments"] = mouth_segments

    # Close any gaps so every scene frame maps to a state (default 'closed'
    # outside the speech windows).  Это нужно сделать ДО записи файла, чтобы
    # payload на диске совпадал с тем, что реально использует рендер.
    if "mouth_states" in payload and frames:
        for char_key, per_character in payload["mouth_states"].items():
            default_state = f"{char_key}_mouth_closed.png"
            for frame_idx in range(len(frames)):
                per_character.setdefault(frame_idx, default_state)

    # B5: авто-реакции слушателя для payload-пути.  Вызывается после обработки
    # (реакции слушателя вставлены ДО выпекания кадров — см. вызов рядом с
    # _apply_listener_gestures; здесь они уже внутри sampled frames)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"payload_{scenario}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    return path, payload


def build_blocky_scene_script(
    out_dir: str | Path,
    actors: Sequence[dict[str, Any]],
    *,
    props: Sequence[dict[str, Any]] | None = None,
    camera_cuts: Sequence[dict[str, Any]] | None = None,
    environment: dict[str, Any] | None = None,
    scenario: str = "blocky_scene",
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    samples: int = 32,
    fit: float = 0.82,
    lens: float = 42.0,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload_path, payload = build_blocky_scene_payload(
        out_dir,
        actors,
        props=props,
        camera_cuts=camera_cuts,
        environment=environment,
        scenario=scenario,
        fps=fps,
        width=width,
        height=height,
        samples=samples,
    )
    diag = out_dir / f"{scenario}_diagnostics.json"
    script = out_dir / f"blocky_{scenario}.py"
    text = (
        _BLENDER_TEMPLATE.replace("__PAYLOAD__", str(payload_path.resolve()))
        .replace("__DIAG__", str(diag.resolve()))
        .replace("__OUT__", str(out_dir.resolve()))
        .replace("__WIDTH__", str(int(width)))
        .replace("__HEIGHT__", str(int(height)))
        .replace("__FPS__", str(int(fps)))
        .replace("__FIT__", repr(float(fit)))
        .replace("__LENS__", repr(float(lens)))
        .replace("__NAME__", payload["character_name"])
    )
    script.write_text(text, encoding="utf-8")
    return script


# --------------------------------------------------------------------------
# авто-кадрирование: bbox актёров + пропсов -> готовые camera_cuts
# --------------------------------------------------------------------------


#: Запас от краёв кадра (м): ничего не обрезается вплотную к границе.
FRAMING_MARGIN = 0.4
#: Верх полного роста персонажа (м): вертикальный охват кадра.
FRAMING_BODY_TOP = 1.8
#: fit/lens, совместимые с дефолтами build_blocky_scene_script.
FRAMING_FIT = 0.82
FRAMING_LENS = 42.0
#: Аспект кадра (ширина/высота): сцены по умолчанию вертикальные 9:16.
FRAMING_ASPECT = 1080.0 / 1920.0


def _framing_spans(
    actors: Sequence[dict[str, Any]],
    props: Sequence[dict[str, Any]],
    *,
    window_end: float,
) -> list[tuple[float, float]]:
    """Точки (x, y), обязанные попасть в кадр.

    Актёры дают свой origin и (для walk/run с ``params.target``) origin +
    target — так ходка за пределы origin не обрезается.  Пропсы дают весь
    свой горизонтальный размер по ``center``/``size``; пропсы-светильники
    (``type == "light"``) пропускаются — это невидимые точки без геометрии.
    """
    points: list[tuple[float, float]] = []
    for actor in actors:
        origin = actor.get("origin") or (0.0, 0.0, 0.0)
        px, py = float(origin[0]), float(origin[1])
        points.append((px, py))
        for act in actor.get("actions") or []:
            verb = str(act.get("action") or "").strip().lower()
            if verb not in ("walk", "run"):
                continue
            if float(act.get("start") or 0.0) > window_end:
                continue
            target = (act.get("params") or {}).get("target")
            if isinstance(target, (list, tuple)) and len(target) >= 2:
                points.append((px + float(target[0]), py + float(target[1])))
    for prop in props:
        if str(prop.get("type") or "") == "light":
            continue
        center = prop.get("center")
        if not (isinstance(center, (list, tuple)) and len(center) >= 2):
            continue
        size = prop.get("size") or (0.0, 0.0, 0.0)
        sx = float(size[0]) / 2.0 if len(size) > 0 else 0.0
        sy = float(size[1]) / 2.0 if len(size) > 1 else 0.0
        cx, cy = float(center[0]), float(center[1])
        points.extend([(cx - sx, cy - sy), (cx + sx, cy + sy)])
    return points


def plan_framing(
    actors: Sequence[dict[str, Any]],
    props: Sequence[dict[str, Any]],
    *,
    fps: int = 30,
    duration: float,
    mode: str = "wide",
) -> list[dict[str, Any]]:
    """Детерминированный авто-кадр: bbox сцены -> готовые ``camera_cuts``.

    Возвращает список словарей в формате монтажных склеек шаблона Blender
    (``{frame, center, height, lens, fit}``), который можно передать прямо в
    ``build_blocky_scene_script(camera_cuts=...)``.

    - ``mode="wide"``: полный охват — bbox по всем актёрам (origin + цели
      ходьбы) и всем геометрическим пропсам (пропсы-светильники
      ``type == "light"`` пропускаются), с запасом :data:`FRAMING_MARGIN`
      0.4 м с боков; вертикаль — от пола (z=0) до полного роста ~1.8 м.
    - ``mode="close"``: парный кадр первых двух актёров (origin + верх
      головы), тот же запас 0.4 м — второй герой больше не выпадает из
      близкого плана.

    Использование::

        cuts = plan_framing(scene["actors"], scene["props"],
                            fps=30, duration=30.0, mode="wide")
        build_blocky_scene_script(out, scene["actors"],
                                  props=scene["props"], camera_cuts=cuts)

    ``height`` считается из геометрии шаблона: видимая область кадра равна
    ``height / fit`` по вертикали и ``height * aspect / fit`` по горизонтали,
    поэтому bbox гарантированно помещается при данных ``fit``/``lens``.
    """
    if mode not in ("wide", "close"):
        raise ValueError(
            f"Неизвестный режим кадрирования «{mode}»: ожидается wide или close."
        )
    window_end = float(duration) + 1.0 / max(1, int(fps))

    points: list[tuple[float, float]] = []
    if mode == "wide":
        points = _framing_spans(actors, props, window_end=window_end)
    else:
        for actor in list(actors)[:2]:
            origin = actor.get("origin") or (0.0, 0.0, 0.0)
            points.append((float(origin[0]), float(origin[1])))
    if not points:
        points = [(0.0, 0.0)]
    margin = FRAMING_MARGIN
    x_min = min(p[0] for p in points) - margin
    x_max = max(p[0] for p in points) + margin
    y_min = min(p[1] for p in points)
    y_max = max(p[1] for p in points)
    # вертикаль: полный рост от пола, с запасом сверху (низ в z=0 не расширяем)
    z_min, z_max = 0.0, FRAMING_BODY_TOP + margin

    fit, lens = FRAMING_FIT, FRAMING_LENS
    # видимая область: height/fit по вертикали, height*aspect/fit по горизонтали
    height = max(
        (z_max - z_min) * fit,
        (x_max - x_min) * fit / FRAMING_ASPECT,
    )
    return [{
        "frame": 1,
        "center": [
            round((x_min + x_max) * 0.5, 3),
            round((y_min + y_max) * 0.5, 3),
            round((z_min + z_max) * 0.5, 3),
        ],
        "height": round(float(height), 3),
        "lens": lens,
        "fit": fit,
    }]


# --------------------------------------------------------------------------
# procedural sound effects (own synthesis — no external audio, licence-clean)
# --------------------------------------------------------------------------


def synth_sfx(events: Sequence[tuple[float, str]], duration: float, sample_rate: int = 48000) -> bytes:
    """Render the SFX track as a mono 16-bit WAV of exactly ``duration`` seconds."""
    import io
    import wave

    import numpy as np

    total = np.zeros(int(round(duration * sample_rate)), dtype=np.float64)

    def sparkle(d: float) -> "np.ndarray":
        t = np.linspace(0.0, d, int(d * sample_rate), endpoint=False)
        f = 1500.0 + 1100.0 * t / d
        s = np.sin(2 * np.pi * f * t) * np.exp(-3.5 * t)
        s += 0.45 * np.sin(2 * np.pi * f * 1.5 * t) * np.exp(-5.0 * t)
        s += 0.25 * np.sin(2 * np.pi * f * 2.02 * t + 1.1) * np.exp(-6.0 * t)
        return s * 0.55

    def boing(d: float) -> "np.ndarray":
        t = np.linspace(0.0, d, int(d * sample_rate), endpoint=False)
        f = 210.0 * np.exp(-2.6 * t) + 65.0
        phase = 2 * np.pi * np.cumsum(f) / sample_rate
        return np.sin(phase) * np.exp(-2.8 * t) * 0.8

    def pop(d: float) -> "np.ndarray":
        t = np.linspace(0.0, d, int(d * sample_rate), endpoint=False)
        return np.sin(2 * np.pi * 260.0 * t) * np.exp(-28.0 * t) * 0.7

    lengths = {"sparkle": 1.2, "boing": 0.55, "pop": 0.12}
    makers = {"sparkle": sparkle, "boing": boing, "pop": pop}
    for start, kind in events:
        seg = makers[kind](lengths[kind])
        i0 = int(round(start * sample_rate))
        i1 = min(len(total), i0 + len(seg))
        if i0 < len(total):
            total[i0:i1] += seg[: i1 - i0]

    peak = float(np.max(np.abs(total))) or 1.0
    total = total / peak * 0.7
    pcm = (total * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def mux_audio(video: Path, wav_bytes: bytes, out: Path) -> Path:
    """Mux the SFX track under the rendered video (video stream is copied)."""
    wav_path = video.parent / "sfx.wav"
    wav_path.write_bytes(wav_bytes)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-i", str(video), "-i", str(wav_path),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-ar", "48000", "-ac", "2", "-shortest", str(out)],
        check=True, shell=False,
    )
    return out


# --------------------------------------------------------------------------
# the first blocky short: «Необычный куб»
# --------------------------------------------------------------------------


def first_film_scene() -> dict[str, Any]:
    """Story, props and camera cuts for the 10-second prototype scene."""
    duration = 10.0
    steve_origin = (-0.35, 0.90, 0.0)
    alex_origin = (0.35, 0.90, 0.0)

    steve_actions = [
        {"action": "idle", "start": 0.0, "duration": 0.4},
        {"action": "walk", "start": 0.4, "duration": 2.0},
        {"action": "idle", "start": 2.4, "duration": 1.2},
        {"action": "walk", "start": 3.6, "duration": 1.0},
        {"action": "idle", "start": 4.6, "duration": 0.4},
        # interact (not reach): the arm actually comes down to cube height
        {"action": "interact", "start": 5.0, "duration": 0.7},
        {"action": "surprised", "start": 5.8, "duration": 1.2},
        {"action": "idle", "start": 7.0, "duration": 1.4},
        {"action": "happy", "start": 8.4, "duration": 1.0},
        {"action": "idle", "start": 9.4, "duration": 0.6},
    ]
    alex_actions = [
        {"action": "idle", "start": 0.0, "duration": 0.4},
        {"action": "walk", "start": 0.4, "duration": 2.0},
        {"action": "idle", "start": 2.4, "duration": 2.0},
        {"action": "confused", "start": 4.4, "duration": 1.2},
        {"action": "surprised", "start": 5.8, "duration": 1.2},
        {"action": "idle", "start": 7.0, "duration": 1.3},
        {"action": "happy", "start": 8.3, "duration": 1.0},
        {"action": "idle", "start": 9.3, "duration": 0.7},
    ]
    hops = [{"start": 5.72, "duration": 0.45, "height": 0.17, "back": 0.26}]

    # Probe the timeline once to learn where Steve and his hand end up: the
    # cube must sit exactly under the touching hand, and every camera shot
    # must frame real, known positions (vertical 9:16 makes the horizontal
    # span the binding constraint, so the pair stays close together).
    probe = timeline_from_actions(steve_actions, duration=duration, fps=30, yaw=0.0)
    probe_poses = probe.sample_frames(30)
    end_root = probe_poses[-1].resolved().root
    steve_end = (steve_origin[0] + end_root[0], steve_origin[1] + end_root[1])
    touch_pose = probe_poses[int(5.4 * 30) - 1].resolved()
    touch_hand = solve_pose(touch_pose).heads["hand_R"]
    # the cube rests on a blocky stone pedestal right under the touching hand
    cube = (steve_end[0] + touch_hand[0], steve_origin[1] + touch_hand[1] - 0.07, 0.74)
    pedestal = (cube[0], cube[1] + 0.04, 0.285)

    flash_frame = 171   # t = 5.7 s, right after the touch
    props: list[dict[str, Any]] = [
        # the glowing cube (hero prop) on its pedestal
        {
            "name": "GlowCube", "center": list(cube), "size": (0.34, 0.34, 0.34),
            "rgb": (0.30, 0.34, 0.38), "roughness": 0.4, "emissive": True,
            "emission_rgb": (0.55, 0.95, 1.00),
            "emission_track": [
                [1, 0.6], [160, 0.8], [168, 1.2], [flash_frame - 1, 2.0],
                [flash_frame + 1, 2.5], [flash_frame + 4, 2.5],
                [flash_frame + 12, 2.5], [240, 1.8], [300, 1.8],
            ],
        },
        {"name": "CubePedestal", "center": list(pedestal), "size": (0.60, 0.60, 0.57),
         "rgb": (0.46, 0.46, 0.48), "roughness": 0.9},
        {"name": "CubeLight", "type": "light", "center": [cube[0], cube[1], cube[2] + 0.1],
         "rgb": (0.6, 0.95, 1.0),
         "energy_track": [[1, 2.0], [165, 3.0], [flash_frame, 15.0], [flash_frame + 5, 15.0],
                          [flash_frame + 15, 14.0], [300, 10.0]]},
        # blocky forest — everything placed INSIDE the vertical camera frustum
        # (front camera: half-width ≈ 0.12 × distance, so props hug the action)
        {"name": "Trunk1", "center": (-1.10, 2.30, 0.55), "size": (0.24, 0.24, 1.10), "rgb": (0.38, 0.26, 0.15)},
        {"name": "Leaves1a", "center": (-1.10, 2.30, 1.45), "size": (1.10, 1.10, 0.80), "rgb": (0.20, 0.46, 0.18)},
        {"name": "Leaves1b", "center": (-1.10, 2.30, 2.02), "size": (0.75, 0.75, 0.50), "rgb": (0.24, 0.52, 0.20)},
        {"name": "Trunk2", "center": (1.20, 2.70, 0.55), "size": (0.24, 0.24, 1.10), "rgb": (0.38, 0.26, 0.15)},
        {"name": "Leaves2a", "center": (1.20, 2.70, 1.45), "size": (1.10, 1.10, 0.80), "rgb": (0.20, 0.46, 0.18)},
        {"name": "Leaves2b", "center": (1.20, 2.70, 2.02), "size": (0.75, 0.75, 0.50), "rgb": (0.24, 0.52, 0.20)},
        {"name": "Trunk3", "center": (0.95, 3.30, 0.55), "size": (0.24, 0.24, 1.10), "rgb": (0.38, 0.26, 0.15)},
        {"name": "Leaves3a", "center": (0.95, 3.30, 1.45), "size": (1.10, 1.10, 0.80), "rgb": (0.20, 0.46, 0.18)},
        {"name": "Leaves3b", "center": (0.95, 3.30, 2.02), "size": (0.75, 0.75, 0.50), "rgb": (0.24, 0.52, 0.20)},
        {"name": "Rock1", "center": (0.95, -1.20, 0.14), "size": (0.42, 0.36, 0.28), "rgb": (0.48, 0.48, 0.50)},
        {"name": "Rock2", "center": (-1.05, 0.20, 0.10), "size": (0.30, 0.26, 0.20), "rgb": (0.52, 0.52, 0.54)},
        {"name": "Grass1", "center": (0.75, 0.30, 0.06), "size": (0.12, 0.12, 0.12), "rgb": (0.30, 0.60, 0.22)},
        {"name": "Grass2", "center": (-0.85, -0.90, 0.06), "size": (0.12, 0.12, 0.12), "rgb": (0.30, 0.60, 0.22)},
        {"name": "Grass3", "center": (0.65, -2.00, 0.06), "size": (0.12, 0.12, 0.12), "rgb": (0.28, 0.56, 0.20)},
        {"name": "Grass4", "center": (-0.75, 1.40, 0.06), "size": (0.12, 0.12, 0.12), "rgb": (0.28, 0.56, 0.20)},
    ]
    # the cube bounces after the flash: three decaying hops
    base = list(cube)
    props[0]["pos_track"] = [
        [1, base], [194, base], [198, [base[0], base[1], base[2] + 0.34]],
        [202, base], [207, [base[0], base[1], base[2] + 0.15]],
        [211, base], [214, [base[0], base[1], base[2] + 0.06]],
        [217, base],
    ]

    camera_cuts = [
        # 1) wide: both walk in and stop (0–3.3 s) — vertical 9:16 makes the
        #    horizontal span the constraint, so the shot stays generous
        {"frame": 1, "center": [0.0, -0.35, 0.95], "height": 3.40, "lens": 42.0, "fit": 0.85},
        # 2) medium: Steve at the pedestal, full body + cube at his feet (3.3–6.5 s)
        {"frame": 100, "center": [steve_end[0] - 0.15, steve_end[1] - 0.20, 0.90],
         "height": 2.05, "lens": 42.0, "fit": 0.85},
        # 3) medium-wide: the flash, both recoils, the bouncing cube (6.5–10 s)
        {"frame": 195, "center": [0.0, steve_end[1] + 0.55, 0.90], "height": 2.90,
         "lens": 42.0, "fit": 0.85},
    ]
    environment = {
        "sky_rgb": (0.45, 0.66, 0.90), "sky_strength": 0.65,
        "ground_rgb": (0.30, 0.55, 0.24), "sun_energy": 2.1,
    }
    return {
        "duration": duration,
        "actors": [
            {
                "character": "steve", "origin": steve_origin,
                "actions": steve_actions, "duration": duration, "hops": hops,
            },
            {
                "character": "alex", "origin": alex_origin,
                "actions": alex_actions, "duration": duration, "hops": hops,
            },
        ],
        "props": props,
        "camera_cuts": camera_cuts,
        "environment": environment,
        "sfx_events": [(5.66, "sparkle"), (6.48, "boing"), (7.08, "pop")],
    }


def render_first_film(out_dir: str | Path, *, timeout: int = 3600) -> dict[str, Any]:
    """End-to-end: payload → Blender render → SFX mux → report. Returns paths+QC."""
    scene = first_film_scene()
    out = Path(out_dir)
    script = build_blocky_scene_script(
        out / "render",
        scene["actors"],
        props=scene["props"],
        camera_cuts=scene["camera_cuts"],
        environment=scene["environment"],
        scenario="unusual_cube",
        fps=30,
        samples=32,
    )
    result = render_skinned(script, timeout=timeout)
    report: dict[str, Any] = {"status": result.status, "error": result.error}
    if result.status == "OK":
        video = Path(result.video)
        final = out / "final_audio.mp4"
        wav = synth_sfx(scene["sfx_events"], scene["duration"])
        mux_audio(video, wav, final)
        report["video"] = str(video)
        report["final_audio"] = str(final)
        report["diagnostics"] = result.diagnostics
        sheet = out / "contact_sheet.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(final),
             "-vf", "fps=1.2,scale=300:-1,tile=4x3", "-frames:v", "1", str(sheet)],
            check=True, shell=False,
        )
        report["contact_sheet"] = str(sheet)
    (out / "film_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "output/blocky_first_film")
    print(json.dumps(render_first_film(target), ensure_ascii=False, indent=2))
