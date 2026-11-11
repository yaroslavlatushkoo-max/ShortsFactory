"""Procedural 2D cut-out rig with versioned character looks.

A character is a hierarchy of body parts ("cut-out" pieces). Each part is a
rigid segment attached to a joint of its parent; parts can be rotated
independently and are drawn with vector shapes (capsules, ellipses, polygons)
through Pillow.

Geometry is expressed in fractions of the character height so the same rig
scales cleanly to any resolution. Local coordinates use a mathematical frame
with the origin at the character's hip joint and ``+y`` pointing up; the
renderer mirrors them into screen space.

The *look* (hair, face, outfit, palette) is a separate asset, see
:mod:`shortsfactory.animation.looks`. This module only knows how to draw the
styles declared there.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageOps

# --------------------------------------------------------------------- geometry

#: Height of the hip joint above the feet, in fractions of the character height.
HIP_HEIGHT = 0.47

#: Radius of the head, in fractions of the character height.
HEAD_RADIUS = 0.108

#: Neck bone length (shoulders to the head joint).
NECK_LENGTH = 0.05

#: A part is a segment attached to a joint of its parent.
#:   parent  — id of the parent part (``None`` for the root)
#:   dir     — +1 when the segment points up, -1 when it points down
#:   length  — segment length in fractions of the character height
#:   at      — joint offset inside the parent's local frame (fractions of height)
#:   rest    — fixed rotation added to the part's own joint angle
PART_TREE: dict[str, dict[str, Any]] = {
    "hips": {"parent": None, "dir": 1, "length": 0.0, "at": (0.0, 0.0), "rest": 0.0},
    "torso": {"parent": "hips", "dir": 1, "length": 0.28, "at": (0.0, 0.0), "rest": 0.0},
    "head": {"parent": "torso", "dir": 1, "length": NECK_LENGTH, "at": (0.0, 0.28), "rest": 0.0},
    # Arms rest slightly forward/back so the silhouette never disappears into the torso.
    "arm_l": {"parent": "torso", "dir": -1, "length": 0.155, "at": (-0.115, 0.26), "rest": -0.10},
    "forearm_l": {"parent": "arm_l", "dir": -1, "length": 0.145, "at": (0.0, -0.155), "rest": 0.0},
    "hand_l": {"parent": "forearm_l", "dir": -1, "length": 0.045, "at": (0.0, -0.145), "rest": 0.0},
    "arm_r": {"parent": "torso", "dir": -1, "length": 0.155, "at": (0.115, 0.26), "rest": 0.10},
    "forearm_r": {"parent": "arm_r", "dir": -1, "length": 0.145, "at": (0.0, -0.155), "rest": 0.0},
    "hand_r": {"parent": "forearm_r", "dir": -1, "length": 0.045, "at": (0.0, -0.145), "rest": 0.0},
    "leg_l": {"parent": "hips", "dir": -1, "length": 0.25, "at": (-0.052, 0.0), "rest": 0.0},
    "shin_l": {"parent": "leg_l", "dir": -1, "length": 0.22, "at": (0.0, -0.25), "rest": 0.0},
    "foot_l": {"parent": "shin_l", "dir": -1, "length": 0.075, "at": (0.0, -0.22), "rest": 1.15},
    "leg_r": {"parent": "hips", "dir": -1, "length": 0.25, "at": (0.052, 0.0), "rest": 0.0},
    "shin_r": {"parent": "leg_r", "dir": -1, "length": 0.22, "at": (0.0, -0.25), "rest": 0.0},
    "foot_r": {"parent": "shin_r", "dir": -1, "length": 0.075, "at": (0.0, -0.22), "rest": 1.15},
}

#: Back-to-front draw order. The far-side limbs are painted first so the torso
#: and the near-side limbs overdraw them, giving a simple depth illusion.
DRAW_ORDER: tuple[str, ...] = (
    "leg_l", "shin_l", "foot_l", "arm_l", "forearm_l", "hand_l",
    "hips", "torso", "head",
    "leg_r", "shin_r", "foot_r", "arm_r", "forearm_r", "hand_r",
)

RIGS: tuple[str, ...] = ("humanoid_v1",)

#: Attachment points an object can be parented to. Hands are the usual targets,
#: but a prop may also be pinned to a forearm, the chest or the head.
RIG_POINTS: dict[str, str] = {
    "hand_l": "кисть левой руки",
    "hand_r": "кисть правой руки",
    "forearm_l": "предплечье левой руки",
    "forearm_r": "предплечье правой руки",
    "head": "голова",
    "chest": "грудь",
}

#: Default palette of an original cartoon character; scenes may override colours.
DEFAULT_PALETTE: dict[str, str] = {
    "skin": "#f4c9a3",
    "skin_shadow": "#dba97f",
    "shirt": "#2fb6c4",
    "shirt_dark": "#218b96",
    "pants": "#33405c",
    "shoe": "#1f2430",
    "hair": "#5a3b26",
    "outline": "#1b2230",
    "eye_white": "#ffffff",
    "pupil": "#22303f",
    "mouth": "#8a3a3a",
    "accent": "#f2d06b",
}


@dataclass
class PartTransform:
    """Screen-space transform of a single part after forward kinematics."""

    name: str
    joint: tuple[float, float]
    tip: tuple[float, float]
    angle: float


def _rot(angle: float, point: tuple[float, float]) -> tuple[float, float]:
    x, y = point
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    return (x * cos_a - y * sin_a, x * sin_a + y * cos_a)


def describe_rig() -> dict[str, Any]:
    """Machine-readable description of the rig, exported with each render."""
    parts = [
        {
            "id": name,
            "parent": spec["parent"],
            "direction": "up" if spec["dir"] > 0 else "down",
            "length": spec["length"],
            "joint_offset": list(spec["at"]),
            "rest_angle_deg": round(math.degrees(spec["rest"]), 2),
        }
        for name, spec in PART_TREE.items()
    ]
    return {
        "rig": "humanoid_v1",
        "units": "fractions of character height",
        "hip_height": HIP_HEIGHT,
        "head_radius": HEAD_RADIUS,
        "parts": parts,
        "draw_order": list(DRAW_ORDER),
        "attachment_points": dict(RIG_POINTS),
        "independently_animatable": [
            "hips (position + lean)",
            "torso",
            "head",
            "arm_l / forearm_l / hand_l",
            "arm_r / forearm_r / hand_r",
            "leg_l / shin_l / foot_l",
            "leg_r / shin_r / foot_r",
            "eyes (blink)",
            "mouth (open/close)",
        ],
    }


def resolve(
    angles: dict[str, float],
    facing: int,
    height_px: float,
    hip_px: tuple[float, float],
) -> dict[str, PartTransform]:
    """Forward kinematics: joint angles (radians) -> screen-space part transforms.

    ``hip_px`` is the screen position of the hip joint.
    """
    local_joint: dict[str, tuple[float, float]] = {}
    local_angle: dict[str, float] = {}

    for name, spec in PART_TREE.items():
        parent = spec["parent"]
        if parent is None:
            joint = (0.0, 0.0)
            parent_angle = 0.0
        else:
            parent_angle = local_angle[parent]
            offset = _rot(parent_angle, (spec["at"][0] * height_px, spec["at"][1] * height_px))
            parent_joint = local_joint[parent]
            joint = (parent_joint[0] + offset[0], parent_joint[1] + offset[1])
        # The rig is authored for the right side; the left arm mirrors the
        # joint angle so a positive value raises both arms outwards instead
        # of folding the far arm across the chest.
        sign = -1.0 if name in ("arm_l", "forearm_l", "hand_l") else 1.0
        angle = parent_angle + sign * angles.get(name, 0.0) + spec["rest"]
        local_joint[name] = joint
        local_angle[name] = angle

    result: dict[str, PartTransform] = {}
    for name, spec in PART_TREE.items():
        joint = local_joint[name]
        angle = local_angle[name]
        tip_world = (
            joint[0] + _rot(angle, (0.0, spec["dir"] * spec["length"] * height_px))[0],
            joint[1] + _rot(angle, (0.0, spec["dir"] * spec["length"] * height_px))[1],
        )
        result[name] = PartTransform(
            name=name,
            joint=(hip_px[0] + facing * joint[0], hip_px[1] - joint[1]),
            tip=(hip_px[0] + facing * tip_world[0], hip_px[1] - tip_world[1]),
            angle=angle * facing,
        )
    return result


def head_center(
    head: PartTransform, height_px: float, head_scale: float = 1.0
) -> tuple[float, float]:
    """Screen position of the head circle centre (shared by drawing and props)."""
    radius = HEAD_RADIUS * head_scale * height_px
    offset = radius + NECK_LENGTH * height_px * 0.35
    ox, oy = _rot(head.angle, (0.0, offset))
    return (head.joint[0] + ox, head.joint[1] - oy)


def attachment_transform(
    parts: dict[str, PartTransform],
    point: str,
    height_px: float,
    head_scale: float = 1.0,
) -> tuple[tuple[float, float], float]:
    """World position and screen angle of a named attachment point.

    The returned angle follows the parent part, so a prop rotates with the
    hand instead of staying axis-aligned.
    """
    if point not in RIG_POINTS:
        raise KeyError(point)
    if point.startswith("hand_"):
        part = parts[point]
        return part.tip, part.angle
    if point.startswith("forearm_"):
        side = point.split("_", 1)[1]
        arm = parts[f"arm_{side}"]
        forearm = parts[point]
        position = (
            arm.tip[0] + (forearm.tip[0] - arm.tip[0]) * 0.55,
            arm.tip[1] + (forearm.tip[1] - arm.tip[1]) * 0.55,
        )
        return position, forearm.angle
    if point == "head":
        head = parts["head"]
        return head_center(head, height_px, head_scale), head.angle
    torso = parts["torso"]
    chest = (
        torso.joint[0] + (torso.tip[0] - torso.joint[0]) * 0.72,
        torso.joint[1] + (torso.tip[1] - torso.joint[1]) * 0.72,
    )
    return chest, torso.angle


# ---------------------------------------------------------------------- drawing

Face = dict[str, float]


def _s(value: Any, fallback: str) -> str:
    return str(value) if value else fallback


def _line_width(height_px: float, ratio: float = 0.0042, minimum: int = 3) -> int:
    return max(minimum, round(height_px * ratio))


def _segment(
    draw: Any,
    start: tuple[float, float],
    end: tuple[float, float],
    width: float,
    fill: str,
    outline: str,
    outline_width: float,
) -> None:
    outer = max(1, round(width + outline_width * 2))
    inner = max(1, round(width))
    draw.line((start, end), fill=outline, width=outer)
    r_out = max(1, round(outer / 2))
    r_in = max(1, round(inner / 2))
    for point in (start, end):
        draw.ellipse((point[0] - r_out, point[1] - r_out, point[0] + r_out, point[1] + r_out), fill=outline)
    draw.line((start, end), fill=fill, width=inner)
    for point in (start, end):
        draw.ellipse((point[0] - r_in, point[1] - r_in, point[0] + r_in, point[1] + r_in), fill=fill)


def _ellipse(draw: Any, cx: float, cy: float, rx: float, ry: float, **kwargs: Any) -> None:
    draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), **kwargs)


def _draw_back_hair(
    draw: Any, hx: float, hy: float, radius: float, look: dict[str, Any], outline: str, width: int
) -> None:
    hair = look.get("hair") or {}
    style = str(hair.get("style", "flat"))
    color = _s(hair.get("color"), look["palette"]["hair"])
    if style == "bun":
        _ellipse(draw, hx, hy - radius * 0.15, radius * 1.06, radius * 1.02, fill=color, outline=outline, width=width)
        _ellipse(draw, hx - radius * 0.55, hy - radius * 0.95, radius * 0.42, radius * 0.42,
                 fill=color, outline=outline, width=width)
        _ellipse(draw, hx + radius * 0.1, hy - radius * 1.05, radius * 0.30, radius * 0.30, fill=color)
    elif style == "bob":
        _ellipse(draw, hx, hy + radius * 0.10, radius * 1.12, radius * 1.15, fill=color, outline=outline, width=width)
        draw.rectangle((hx - radius * 1.1, hy - radius * 1.1, hx + radius * 1.1, hy),
                       fill=color)
    elif style == "curly":
        for angle_deg in range(0, 360, 45):
            angle = math.radians(angle_deg)
            _ellipse(draw, hx + math.cos(angle) * radius * 0.95, hy - radius * 0.55 + math.sin(angle) * radius * 0.55,
                     radius * 0.42, radius * 0.42, fill=color, outline=outline, width=max(2, width - 1))
    elif style == "spiky":
        _ellipse(draw, hx, hy - radius * 0.1, radius * 1.04, radius * 1.0, fill=color, outline=outline, width=width)
    elif style == "flat":
        draw.pieslice(
            (hx - radius * 1.04, hy - radius * 1.16, hx + radius * 1.04, hy + radius * 0.42),
            start=180, end=360, fill=color, outline=outline, width=width,
        )


def _draw_front_hair(
    draw: Any, hx: float, hy: float, radius: float, look: dict[str, Any], outline: str, width: int
) -> None:
    hair = look.get("hair") or {}
    style = str(hair.get("style", "flat"))
    color = _s(hair.get("color"), look["palette"]["hair"])
    if style == "spiky":
        points = [
            (hx - radius * 1.02, hy - radius * 0.10),
            (hx - radius * 0.95, hy - radius * 0.82),
            (hx - radius * 0.60, hy - radius * 0.45),
            (hx - radius * 0.42, hy - radius * 1.16),
            (hx - radius * 0.10, hy - radius * 0.62),
            (hx + radius * 0.18, hy - radius * 1.22),
            (hx + radius * 0.42, hy - radius * 0.58),
            (hx + radius * 0.70, hy - radius * 1.02),
            (hx + radius * 0.84, hy - radius * 0.40),
            (hx + radius * 1.02, hy - radius * 0.08),
        ]
        draw.polygon(points, fill=color, outline=outline)
        draw.line(points + [points[0]], fill=outline, width=width)
        # fringe over the forehead
        draw.pieslice(
            (hx - radius * 0.98, hy - radius * 0.95, hx + radius * 0.98, hy - radius * 0.05),
            start=180, end=360, fill=color,
        )
    elif style == "bob":
        draw.pieslice(
            (hx - radius * 1.02, hy - radius * 1.05, hx + radius * 1.02, hy + radius * 0.25),
            start=178, end=362, fill=color,
        )
        draw.rectangle((hx - radius * 1.02, hy - radius * 0.35, hx - radius * 0.78, hy + radius * 0.55), fill=color)
        draw.rectangle((hx + radius * 0.78, hy - radius * 0.35, hx + radius * 1.02, hy + radius * 0.55), fill=color)
    elif style == "bun":
        draw.pieslice(
            (hx - radius * 0.95, hy - radius * 0.98, hx + radius * 0.95, hy - radius * 0.10),
            start=180, end=360, fill=color,
        )
    elif style == "curly":
        for dx, dy, rr in ((-0.55, -0.62, 0.34), (0.0, -0.78, 0.36), (0.55, -0.60, 0.32)):
            _ellipse(draw, hx + radius * dx, hy + radius * dy, radius * rr, radius * rr, fill=color)


def _draw_eyes(
    draw: Any,
    hx: float,
    hy: float,
    radius: float,
    look: dict[str, Any],
    face: dict[str, float],
    outline: str,
    width: int,
) -> None:
    palette = look["palette"]
    style = str((look.get("face") or {}).get("eye", "round"))
    expression = str(face.get("expression", "neutral"))
    openness = max(0.04, min(1.0, float(face.get("blink", 1.0))))
    eye_dx = radius * 0.40
    eye_dy = radius * 0.10
    wide = 1.16 if style == "wide" or expression == "surprise" else 1.0
    w = radius * (0.26 if style == "almond" else 0.25) * wide
    h = radius * 0.31 * wide * openness
    if expression == "joy":
        # happy closed eyes: upward arcs
        for sign in (-1, 1):
            ex = hx + sign * eye_dx
            draw.arc((ex - w, hy - eye_dy - h * 1.2, ex + w, hy - eye_dy + h * 0.9),
                     start=200, end=340, fill=outline, width=max(3, round(radius * 0.09)))
        return
    for sign in (-1, 1):
        ex = hx + sign * eye_dx
        ey = hy - eye_dy
        _ellipse(draw, ex, ey, w, max(1.0, h), fill=palette["eye_white"], outline=outline, width=width)
        if openness > 0.32:
            pr = w * (0.62 if style != "almond" else 0.52)
            ph = max(1.5, h * (0.72 if style != "almond" else 0.55))
            gaze = float(face.get("gaze", 0.0)) * w * 0.25
            _ellipse(draw, ex + gaze, ey + h * 0.06, pr, ph, fill=palette["pupil"])
            _ellipse(draw, ex + gaze - pr * 0.35, ey - ph * 0.28, pr * 0.26, ph * 0.26, fill="#ffffff")
        if style == "almond":
            draw.arc((ex - w, ey - h * 1.15, ex + w, ey + h * 0.55),
                     start=195, end=345, fill=outline, width=max(2, round(radius * 0.05)))


def _draw_brows(
    draw: Any,
    hx: float,
    hy: float,
    radius: float,
    look: dict[str, Any],
    face: dict[str, float],
    outline: str,
    width: int,
) -> None:
    expression = str(face.get("expression", "neutral"))
    brow_style = str((look.get("face") or {}).get("brow", "thick"))
    thickness = max(3, round(radius * (0.075 if brow_style == "thick" else 0.055)))
    lift = {"surprise": 0.30, "joy": 0.20, "confused": 0.10}.get(expression, 0.0)
    tilt = {"annoyed": -0.26, "confused": 0.14}.get(expression, 0.0)
    eye_dx = radius * 0.40
    for sign in (-1, 1):
        ex = hx + sign * eye_dx
        ey = hy - radius * 0.52 - lift * radius
        inner_drop = tilt * sign * radius
        draw.line(
            ((ex - radius * 0.24, ey + inner_drop), (ex + radius * 0.24, ey - inner_drop * 0.4)),
            fill=outline, width=thickness,
        )


def _draw_mouth(
    draw: Any,
    hx: float,
    hy: float,
    radius: float,
    look: dict[str, Any],
    face: dict[str, float],
    outline: str,
    width: int,
) -> None:
    palette = look["palette"]
    expression = str(face.get("expression", "neutral"))
    openness = max(0.0, min(1.0, float(face.get("mouth", 0.0))))
    mouth_style = str((look.get("face") or {}).get("mouth", "normal"))
    wide = 1.15 if mouth_style == "wide" else 0.9
    mx = hx
    my = hy + radius * 0.46
    mw = radius * 0.34 * wide
    if expression == "surprise" or (openness > 0.55 and expression != "joy"):
        h = radius * (0.10 + 0.30 * openness)
        _ellipse(draw, mx, my, mw * 0.82, h, fill=palette["mouth"], outline=outline, width=width)
    elif expression == "annoyed":
        draw.arc((mx - mw, my - radius * 0.10, mx + mw, my + radius * 0.40),
                 start=200, end=340, fill=outline, width=max(3, round(radius * 0.08)))
    elif expression == "confused":
        draw.line((mx - mw * 0.8, my + radius * 0.04, mx + mw * 0.4, my - radius * 0.02),
                  fill=outline, width=max(3, round(radius * 0.08)))
    elif expression == "joy" or openness > 0.16:
        h = radius * (0.06 + 0.26 * openness)
        _ellipse(draw, mx, my + radius * 0.02, mw, h, fill=palette["mouth"], outline=outline, width=width)
        if openness > 0.5:
            draw.chord((mx - mw * 0.8, my - h * 0.7, mx + mw * 0.8, my + h * 1.4),
                       start=10, end=170, fill="#ffffff")
    else:
        draw.arc((mx - mw * 0.9, my - radius * 0.16, mx + mw * 0.9, my + radius * 0.18),
                 start=20, end=160, fill=outline, width=max(3, round(radius * 0.075)))


def _draw_head(
    draw: Any,
    head: PartTransform,
    height_px: float,
    look: dict[str, Any],
    face: dict[str, float],
) -> None:
    palette = look["palette"]
    outline = palette["outline"]
    proportions = look.get("proportions") or {}
    head_scale = float(proportions.get("head", 1.0))
    radius = HEAD_RADIUS * head_scale * height_px
    hx, hy = head_center(head, height_px, head_scale)
    width = _line_width(height_px)

    # ears
    for sign in (-1, 1):
        _ellipse(draw, hx + sign * radius * 0.98, hy + radius * 0.10, radius * 0.20, radius * 0.26,
                 fill=palette["skin"], outline=outline, width=max(2, width - 1))

    _ellipse(draw, hx, hy, radius, radius, fill=palette["skin"], outline=outline, width=width)
    # soft cheek shadow
    draw.arc((hx - radius * 0.86, hy - radius * 0.9, hx + radius * 0.86, hy + radius * 0.95),
             start=20, end=160, fill=palette.get("skin_shadow", palette["skin"]), width=max(2, width - 1))

    _draw_back_hair(draw, hx, hy, radius, look, outline, width)
    # re-draw the face over the back hair so the silhouette reads as a head
    _ellipse(draw, hx, hy, radius * 0.97, radius * 0.97, fill=palette["skin"])
    _draw_front_hair(draw, hx, hy, radius, look, outline, width)

    _draw_brows(draw, hx, hy, radius, look, face, outline, width)
    _draw_eyes(draw, hx, hy, radius, look, face, outline, width)

    # nose
    nose = str((look.get("face") or {}).get("nose", "button"))
    if nose == "button":
        _ellipse(draw, hx, hy + radius * 0.22, radius * 0.07, radius * 0.05,
                 fill=palette.get("skin_shadow", palette["skin"]))

    # freckles
    if bool((look.get("face") or {}).get("freckles")):
        for sign in (-1, 1):
            for offset in (0.0, 0.10, 0.20):
                _ellipse(draw, hx + sign * radius * (0.38 + offset), hy + radius * 0.24,
                         max(1.0, radius * 0.025), max(1.0, radius * 0.025),
                         fill=palette.get("skin_shadow", palette["skin"]))
            if str(face.get("expression")) == "joy":
                _ellipse(draw, hx + sign * radius * 0.55, hy + radius * 0.36,
                         radius * 0.13, radius * 0.075, fill="#eba9a0")

    _draw_mouth(draw, hx, hy, radius, look, face, outline, width)

    if "glasses" in (look.get("accessories") or []):
        eye_dx = radius * 0.40
        ey = hy - radius * 0.10
        lens = radius * 0.30
        for sign in (-1, 1):
            ex = hx + sign * eye_dx
            _ellipse(draw, ex, ey, lens, lens * 0.92, outline=outline, width=max(3, width))
        draw.line((hx - eye_dx + lens, ey, hx + eye_dx - lens, ey), fill=outline, width=max(3, width))


def draw_character(
    draw: Any,
    parts: dict[str, PartTransform],
    look: dict[str, Any],
    height_px: float,
    face: dict[str, float],
) -> None:
    """Draw a rigged character; ``look`` carries palette and style assets.

    ``face`` supports ``blink`` (0..1), ``mouth`` (0..1), ``expression``
    (``neutral``/``surprise``/``joy``/``confused``/``annoyed``) and ``gaze``.
    """
    palette = look["palette"]
    outfit = look.get("outfit") or {}
    proportions = look.get("proportions") or {}
    build = float(proportions.get("build", 1.0))
    long_sleeve = str(outfit.get("sleeve", "short")) == "long"
    style = str(outfit.get("style", "tee"))
    outline = palette["outline"]
    width = _line_width(height_px)
    limb_w = height_px * 0.062 * build
    arm_w = height_px * 0.052 * build
    torso_w = height_px * 0.155 * build

    def seg(name: str, seg_width: float, fill: str) -> None:
        part = parts[name]
        _segment(draw, part.joint, part.tip, seg_width, fill, outline, width)

    # far side
    seg("leg_l", limb_w, palette["pants"])
    seg("shin_l", limb_w * 0.94, palette["pants"])
    seg("foot_l", limb_w * 1.02, palette["shoe"])
    seg("arm_l", arm_w, palette["shirt"] if long_sleeve else palette["shirt"])
    seg("forearm_l", arm_w * 0.92, palette["shirt_dark"] if long_sleeve else palette["skin"])

    # pelvis / skirt
    hips = parts["hips"]
    if style == "dress":
        skirt_w = torso_w * 1.05
        draw.polygon(
            [
                (hips.joint[0] - torso_w * 0.42, hips.joint[1] - height_px * 0.02),
                (hips.joint[0] + torso_w * 0.42, hips.joint[1] - height_px * 0.02),
                (hips.joint[0] + skirt_w * 0.62, hips.joint[1] + height_px * 0.20),
                (hips.joint[0] - skirt_w * 0.62, hips.joint[1] + height_px * 0.20),
            ],
            fill=palette["shirt"], outline=outline, width=width,
        )
        draw.line(
            ((hips.joint[0] - skirt_w * 0.60, hips.joint[1] + height_px * 0.19),
             (hips.joint[0] + skirt_w * 0.60, hips.joint[1] + height_px * 0.19)),
            fill=palette["shirt_dark"], width=max(2, width),
        )
    else:
        _segment(draw, hips.joint, (hips.joint[0], hips.joint[1] + height_px * 0.02),
                 torso_w * 1.02, palette["pants"], outline, width)

    # torso
    todo = parts["torso"]
    _segment(draw, todo.joint, todo.tip, torso_w, palette["shirt"], outline, width)
    if style == "hoodie":
        draw.line(
            ((todo.joint[0] - torso_w * 0.32, todo.joint[1] - height_px * 0.10),
             (todo.joint[0] + torso_w * 0.32, todo.joint[1] - height_px * 0.10)),
            fill=palette["shirt_dark"], width=max(3, width),
        )
        # hood roll around the neck
        _ellipse(draw, todo.tip[0], todo.tip[1] + height_px * 0.012, torso_w * 0.52,
                 torso_w * 0.20, fill=palette["shirt_dark"], outline=outline, width=width)
    elif style == "overalls":
        draw.line(
            ((todo.joint[0] - torso_w * 0.30, todo.tip[1] + height_px * 0.02),
             (todo.joint[0] - torso_w * 0.30, todo.joint[1] - height_px * 0.02)),
            fill=palette["pants"], width=max(4, round(torso_w * 0.16)),
        )
        draw.line(
            ((todo.joint[0] + torso_w * 0.30, todo.tip[1] + height_px * 0.02),
             (todo.joint[0] + torso_w * 0.30, todo.joint[1] - height_px * 0.02)),
            fill=palette["pants"], width=max(4, round(torso_w * 0.16)),
        )
    elif style == "tee" and bool(outfit.get("collar")):
        draw.arc(
            (todo.tip[0] - torso_w * 0.34, todo.tip[1] - height_px * 0.03,
             todo.tip[0] + torso_w * 0.34, todo.tip[1] + height_px * 0.03),
            start=0, end=180, fill=outline, width=max(2, width),
        )

    # neck
    _segment(draw, todo.tip, parts["head"].joint, height_px * 0.042, palette["skin"], outline, width)
    _draw_head(draw, parts["head"], height_px, look, face)

    # near side
    seg("leg_r", limb_w, palette["pants"])
    seg("shin_r", limb_w * 0.94, palette["pants"])
    seg("foot_r", limb_w * 1.02, palette["shoe"])
    seg("arm_r", arm_w, palette["shirt"])
    seg("forearm_r", arm_w * 0.92, palette["shirt_dark"] if long_sleeve else palette["skin"])

    for hand in ("hand_l", "hand_r"):
        part = parts[hand]
        r = height_px * 0.034 * build
        draw.ellipse((part.tip[0] - r - width, part.tip[1] - r - width,
                      part.tip[0] + r + width, part.tip[1] + r + width), fill=outline)
        draw.ellipse((part.tip[0] - r, part.tip[1] - r,
                      part.tip[0] + r, part.tip[1] + r), fill=palette["skin"])


# ------------------------------------------------------- v3 sprite composition

#: Local composition canvas, in fractions of the character height.  The hip is
#: centred horizontally and sits low enough that the shoes never touch the edge.
V3_CANVAS_WIDTH = 1.30
V3_CANVAS_HEIGHT = 1.30
V3_HIP_X = 0.5
V3_HIP_Y = 0.57

#: Vertical clearance kept between the sole and the stage floor.
GROUND_CLEARANCE = 0.055


def _paste_sprite(
    canvas: Image.Image,
    sprite: Image.Image,
    at: tuple[float, float],
    angle: float,
    anchor: tuple[float, float],
    *,
    scale_y: float = 1.0,
) -> None:
    """Rotate a sprite around its anchor and alpha-composite it at ``at``.

    All v3 sprites are authored in their rest direction; ``angle`` is the
    screen-space bone angle.  Pillow rotates counter-clockwise, which matches
    the rig's positive angle convention in screen coordinates.
    """
    w, h = sprite.size
    if abs(scale_y - 1.0) > 0.02:
        new_h = max(2, int(round(h * scale_y)))
        sprite = sprite.resize((w, new_h), Image.LANCZOS)
        ax = anchor[0] * w
        ay = anchor[1] * new_h
        h = new_h
    else:
        ax = anchor[0] * w
        ay = anchor[1] * h
    theta = math.degrees(angle)
    rotated = sprite.rotate(theta, resample=Image.BICUBIC, expand=True)
    nw, nh = rotated.size
    dx, dy = ax - w / 2.0, ay - h / 2.0
    cos_t, sin_t = math.cos(math.radians(theta)), math.sin(math.radians(theta))
    ndx = dx * cos_t + dy * sin_t
    ndy = -dx * sin_t + dy * cos_t
    nx, ny = nw / 2.0 + ndx, nh / 2.0 + ndy
    canvas.alpha_composite(rotated, (int(round(at[0] - nx)), int(round(at[1] - ny))))


def _face_point(
    center: tuple[float, float],
    offset: tuple[float, float],
    angle: float,
    height_px: float,
) -> tuple[float, float]:
    """Rotate an art offset (``+y`` down) around the head centre."""
    dx, dy = offset[0] * height_px, offset[1] * height_px
    ox = dx * math.cos(angle) + dy * math.sin(angle)
    oy = -dx * math.sin(angle) + dy * math.cos(angle)
    return (center[0] + ox, center[1] + oy)


def compose_character(
    art: Any,
    look: dict[str, Any],
    angles: dict[str, float],
    height_px: float,
    face: dict[str, float],
    facing: int = 1,
) -> tuple[Image.Image, tuple[float, float]]:
    """Compose a v3 character from external SVG sprites.

    Returns the RGBA canvas and the local position of the hip joint, so the
    renderer can paste the character at an arbitrary screen position.
    """
    parts = resolve(angles, 1, height_px, (0.0, 0.0))
    width = max(2, int(round(V3_CANVAS_WIDTH * height_px)))
    height = max(2, int(round(V3_CANVAS_HEIGHT * height_px)))
    hip = (width * V3_HIP_X, height * V3_HIP_Y)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    proportions = look.get("proportions") or {}
    head_scale = float(proportions.get("head", 1.0))
    outfit = str((look.get("outfit") or {}).get("style", ""))
    if outfit not in art.outfits:
        outfit = None

    def point(name: str) -> tuple[float, float]:
        part = parts[name]
        return (hip[0] + part.joint[0], hip[1] + part.joint[1])

    def paint_part(part_name: str, key: str, scale: float = 1.0, shoe: bool = False,
                   flip: bool = False) -> None:
        part = parts[part_name]
        spec = art.part(key, outfit)
        sprite = art.sprite(key, height_px * scale, outfit=outfit)
        anchor = (float(spec["anchor"][0]), float(spec["anchor"][1]))
        if flip:
            sprite = ImageOps.mirror(sprite)
            anchor = (1.0 - anchor[0], anchor[1])
        if shoe:
            rest = float(spec.get("rest", 1.15))
            angle = -(part.angle - rest)
        else:
            angle = part.angle
        _paste_sprite(canvas, sprite, point(part_name), angle, anchor)

    def paint_leg(side: str) -> None:
        # Thigh first, then the shin overlaps its lower edge: both sprites use
        # the same colour, so the knee reads as one continuous leg.
        paint_part(f"leg_{side}", "thigh")
        paint_part(f"shin_{side}", "shin")
        paint_part(f"foot_{side}", "shoe", shoe=True, flip=(side == "l"))

    def paint_upper_arm(side: str) -> None:
        paint_part(f"arm_{side}", "upper_arm")

    def paint_lower_arm(side: str) -> None:
        paint_part(f"forearm_{side}", "forearm")
        elbow = parts[f"forearm_{side}"].joint
        ex, ey = hip[0] + elbow[0], hip[1] + elbow[1]
        radius = height_px * 0.028
        color_key = str(art.graphics.get("sleeve_color", "shirt"))
        ImageDraw.Draw(canvas).ellipse(
            (ex - radius, ey - radius, ex + radius, ey + radius),
            fill=art.palette.get(color_key, art.palette["shirt"]),
        )
        paint_part(f"hand_{side}", "hand")

    def paint_head() -> None:
        head = parts["head"]
        paint_part("head", "head", scale=head_scale)
        center = head_center(head, height_px, head_scale)
        center = (hip[0] + center[0], hip[1] + center[1])
        expression = str(face.get("expression", "neutral"))
        spec = art.expression(expression)
        openness = max(0.0, min(1.0, float(face.get("blink", 1.0))))
        mouth_open = max(0.0, min(1.0, float(face.get("mouth", 0.0))))
        gaze = max(-1.0, min(1.0, float(face.get("gaze", 0.0))))

        def paint_face(key: str, group: str | None, scale_y: float = 1.0,
                       anchor_y: float = 0.5) -> None:
            item = art.face_part(key)
            sprite = art.sprite(key, height_px * head_scale, group=group, face=True)
            at = _face_point(center, (float(item.get("offset", [0, 0])[0]),
                                      float(item.get("offset", [0, 0])[1])),
                             head.angle, height_px * head_scale)
            _paste_sprite(canvas, sprite, at, head.angle, (0.5, anchor_y), scale_y=scale_y)

        eye_group = str(spec.get("eyes") or "open")
        if eye_group not in ("happy", "closed") and openness < 0.25:
            eye_group = "closed"
        eye_scale = 1.0 if eye_group in ("happy", "closed") else max(0.2, openness)
        offsets = {}
        if eye_group not in ("happy", "closed") and abs(gaze) > 0.02:
            offsets = {"iris_l": (gaze * 6.5, 0.0), "iris_r": (gaze * 6.5, 0.0)}
        eyes = art.face_part("eyes")
        sprite = art.sprite("eyes", height_px * head_scale, group=eye_group,
                            offsets=offsets, face=True)
        at = _face_point(center, (float(eyes["offset"][0]), float(eyes["offset"][1])),
                         head.angle, height_px * head_scale)
        _paste_sprite(canvas, sprite, at, head.angle, (0.5, 0.5), scale_y=eye_scale)

        paint_face("brows", str(spec.get("brows") or "neutral"))
        if "nose" in art.face:
            paint_face("nose", None)
        mouth_group = str(spec.get("mouth") or "smile")
        mouth_scale = 1.0
        if mouth_open > 0.08 and expression not in ("joy", "laugh", "surprise"):
            mouth_group = "open"
            mouth_scale = 0.7 + 0.5 * mouth_open
        elif expression in ("joy", "laugh", "surprise"):
            mouth_scale = 1.0 + 0.12 * mouth_open
        paint_face("mouth", mouth_group, scale_y=mouth_scale, anchor_y=0.35)
        if spec.get("blush") and "extras" in art.face:
            paint_face("extras", "blush")
        if "glasses" in (look.get("accessories") or []) and "glasses" in art.face:
            paint_face("glasses", None)

        paint_part("head", "hair_front", scale=head_scale)

    paint_leg("l")
    paint_leg("r")
    paint_upper_arm("l")
    paint_lower_arm("l")
    paint_upper_arm("r")
    paint_part("head", "hair_back", scale=head_scale)
    paint_part("hips", "pelvis")
    if art.has_part("skirt", outfit):
        paint_part("hips", "skirt")
    paint_part("torso", "torso")
    paint_head()
    paint_lower_arm("r")

    if facing < 0:
        canvas = ImageOps.mirror(canvas)
    return canvas, hip

