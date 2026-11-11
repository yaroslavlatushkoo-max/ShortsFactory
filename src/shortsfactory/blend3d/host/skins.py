"""Host-side 64x64 skin support for the blocky characters.

This module implements the *standard Minecraft skin layout* (64x64 grid) as a
pure-Python data model — region rectangles, validation and generation of our
own CC0 placeholder skins for Steve and Alex (pixel art drawn here, no Mojang
assets).  It does not require Blender.

The user can drop their own 64x64 skin PNGs into ``blend3d/presets/textures/
skins/`` (e.g. ``steve_mouth_closed.png`` / ``alex_mouth_closed.png``) and the same regions are
used to map them onto the model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

#: Standard 64x64 skin sheet.  Keys are ``<part>:<face>``; values are pixel
#: rectangles ``(x0, y0, x1, y1)`` with y growing downwards (top-left origin),
#: matching the image file convention.  The "left" limbs live in the second
#: row (y >= 48) exactly like in the classic Minecraft layout.
SKIN64_REGIONS: dict[str, tuple[int, int, int, int]] = {
    # head (8x8 cells)
    "head:top": (8, 0, 16, 8),
    "head:bottom": (16, 0, 24, 8),
    "head:right": (0, 8, 8, 16),
    "head:front": (8, 8, 16, 16),
    "head:left": (16, 8, 24, 16),
    "head:back": (24, 8, 32, 16),
    # body / torso (4 wide x 12 tall cells)
    "body:right": (16, 20, 20, 32),
    "body:front": (20, 20, 28, 32),
    "body:left": (28, 20, 32, 32),
    "body:back": (32, 20, 40, 32),
    "body:top": (20, 16, 28, 20),
    "body:bottom": (28, 16, 36, 20),
    # right arm (4x12, row 1)
    "arm_r:top": (44, 16, 48, 20),
    "arm_r:bottom": (48, 16, 52, 20),
    "arm_r:right": (40, 20, 44, 32),
    "arm_r:front": (44, 20, 48, 32),
    "arm_r:left": (48, 20, 52, 32),
    "arm_r:back": (52, 20, 56, 32),
    # right leg (4x12, row 1)
    "leg_r:top": (4, 16, 8, 20),
    "leg_r:bottom": (8, 16, 12, 20),
    "leg_r:right": (0, 20, 4, 32),
    "leg_r:front": (4, 20, 8, 32),
    "leg_r:left": (8, 20, 12, 32),
    "leg_r:back": (12, 20, 16, 32),
    # left leg (4x12, row 2)
    "leg_l:top": (20, 48, 24, 52),
    "leg_l:bottom": (24, 48, 28, 52),
    "leg_l:right": (16, 52, 20, 64),
    "leg_l:front": (20, 52, 24, 64),
    "leg_l:left": (24, 52, 28, 64),
    "leg_l:back": (28, 52, 32, 64),
    # left arm (4x12, row 2)
    "arm_l:top": (36, 48, 40, 52),
    "arm_l:bottom": (40, 48, 44, 52),
    "arm_l:right": (32, 52, 36, 64),
    "arm_l:front": (36, 52, 40, 64),
    "arm_l:left": (40, 52, 44, 64),
    "arm_l:back": (44, 52, 48, 64),
}

FACES = ("top", "bottom", "south", "north", "west", "east")


def region_px(part: str, face: str) -> tuple[int, int, int, int]:
    return SKIN64_REGIONS[f"{part}:{face}"]


def region_norm(part: str, face: str, size: int = 64) -> tuple[float, float, float, float]:
    """Region as normalized UV rect ``(u0, v0, u1, v1)`` (v up, Blender style)."""
    x0, y0, x1, y1 = region_px(part, face)
    u0 = x0 / size
    u1 = x1 / size
    v0 = 1.0 - y1 / size
    v1 = 1.0 - y0 / size
    return (u0, v0, u1, v1)


def validate_skin(path: str | Path) -> dict[str, Any]:
    """Check a skin PNG: must be 64x64 (or 64x32 legacy) and non-empty.

    Returns meta info; raises ValueError for invalid files.  64x32 legacy
    skins are accepted but reported so the caller can react.
    """
    from PIL import Image

    p = Path(path)
    if not p.exists():
        raise ValueError(f"Скин не найден: {p}")
    with Image.open(p) as im:
        w, h = im.size
        if (w, h) not in ((64, 64), (64, 32)):
            raise ValueError(
                f"Скин {p.name} имеет размер {w}x{h}, нужен 64x64 (или legacy 64x32)."
            )
        rgba = im.convert("RGBA")
        alpha = np.asarray(rgba)[:, :, 3]
        if not bool((alpha > 0).any()):
            raise ValueError(f"Скин {p.name} полностью прозрачен.")
        return {"path": str(p), "width": w, "height": h, "legacy": h == 32}


# ---------------------------------------------------------------------------
# CC0 placeholder skins (16x16-style pixel art drawn here, no Mojang assets)
# ---------------------------------------------------------------------------

PALETTE = {
    # Steve
    "steve": {
        "skin": (0.72, 0.52, 0.36),
        "skin_dark": (0.55, 0.39, 0.27),
        "shirt": (0.00, 0.66, 0.66),
        "shirt_dark": (0.00, 0.52, 0.52),
        "pants": (0.18, 0.24, 0.62),
        "pants_dark": (0.12, 0.16, 0.45),
        "hair": (0.26, 0.17, 0.10),
        "hair_dark": (0.18, 0.11, 0.06),
        "shoes": (0.35, 0.35, 0.38),
        "eye": (0.32, 0.44, 0.85),
        "mouth": (0.42, 0.25, 0.20),
    },
    # Alex
    "alex": {
        "skin": (0.85, 0.68, 0.55),
        "skin_dark": (0.70, 0.54, 0.42),
        "shirt": (0.35, 0.62, 0.28),
        "shirt_dark": (0.26, 0.47, 0.20),
        "pants": (0.45, 0.32, 0.20),
        "pants_dark": (0.35, 0.25, 0.15),
        "hair": (0.72, 0.40, 0.16),
        "hair_dark": (0.60, 0.32, 0.12),
        "shoes": (0.45, 0.45, 0.48),
        "eye": (0.22, 0.42, 0.28),
        "mouth": (0.48, 0.28, 0.22),
    },
}


def _px(rgb: tuple[float, float, float]) -> tuple[int, int, int, int]:
    return (round(rgb[0] * 255), round(rgb[1] * 255), round(rgb[2] * 255), 255)


def _skin_region(canvas: np.ndarray, part: str, face: str):
    x0, y0, x1, y1 = region_px(part, face)
    return canvas[y0:y1, x0:x1]


def build_placeholder_skin(style: str = "steve") -> np.ndarray:
    """Draw an original CC0 64x64 skin close in spirit to Steve/Alex."""
    pal = PALETTE[style]
    skin = _px(pal["skin"])
    skin_dark = _px(pal["skin_dark"])
    shirt = _px(pal["shirt"])
    shirt_dark = _px(pal["shirt_dark"])
    pants = _px(pal["pants"])
    pants_dark = _px(pal["pants_dark"])
    hair = _px(pal["hair"])
    hair_dark = _px(pal["hair_dark"])
    shoes = _px(pal["shoes"])
    eye = _px(pal["eye"])
    mouth = _px(pal["mouth"])
    transparent = (0, 0, 0, 0)

    canvas = np.zeros((64, 64, 4), dtype=np.uint8)
    canvas[:, :, 3] = 255

    # ---- head -----------------------------------------------------------
    head_skin = _skin_region(canvas, "head", "front")
    head_skin[:] = skin
    head_skin[2:5, 3] = eye                      # left eye column
    head_skin[2:5, 5] = eye                      # right eye column
    head_skin[5, 3:6] = mouth                    # mouth line
    head_skin[0, 1:3] = hair_dark                # fringe
    head_skin[0, 6:8] = hair_dark
    for face in ("top", "bottom", "right", "left", "back"):
        _skin_region(canvas, "head", face)[:] = hair if face != "bottom" else skin_dark
    # ---- body -----------------------------------------------------------
    for face, base in (("front", shirt), ("back", shirt_dark),
                       ("right", shirt_dark), ("left", shirt_dark)):
        _skin_region(canvas, "body", face)[:] = base
    _skin_region(canvas, "body", "top")[:] = shirt_dark
    _skin_region(canvas, "body", "bottom")[:] = pants_dark
    # ---- arms: skin hands at bottom rows of the 4x12 region -------------
    for arm in ("arm_r", "arm_l"):
        for face in ("front", "back", "right", "left"):
            reg = _skin_region(canvas, arm, face)
            reg[:] = shirt
            reg[9:12, :] = skin                 # hand
            reg[0:2, :] = shirt_dark            # shoulder shade
        if arm == "arm_l":
            # orientation seam: the left sleeve keeps a darker edge on every
            # side, so the generated skins visibly distinguish left from right
            for face in ("front", "back", "right", "left"):
                _skin_region(canvas, arm, face)[:, 0] = shirt_dark
        _skin_region(canvas, arm, "top")[:] = shirt_dark
        _skin_region(canvas, arm, "bottom")[:] = skin_dark
    # ---- legs: pants + shoes at the feet --------------------------------
    for leg in ("leg_r", "leg_l"):
        for face in ("front", "back", "right", "left"):
            reg = _skin_region(canvas, leg, face)
            reg[:] = pants
            reg[9:12, :] = shoes                # shoe
            reg[0:2, :] = pants_dark
        if leg == "leg_l":
            # orientation seam on the left trouser leg (mirrors the sleeve)
            for face in ("front", "back", "right", "left"):
                _skin_region(canvas, leg, face)[:, 0] = pants_dark
        _skin_region(canvas, leg, "top")[:] = pants_dark
        _skin_region(canvas, leg, "bottom")[:] = pants_dark

    # Alex gets a slightly different head (hair on top / sides)
    if style == "alex":
        top = _skin_region(canvas, "head", "top")
        top[:] = hair
        top[0:6, 1:7] = hair_dark
        _skin_region(canvas, "head", "back")[:] = hair
        _skin_region(canvas, "head", "right")[:] = hair
        _skin_region(canvas, "head", "left")[:] = hair

    del transparent
    return canvas


def save_default_skins(out_dir: str | Path) -> list[Path]:
    """Write the two placeholder skins into ``out_dir``; returns paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for style in ("steve", "alex"):
        p = out / f"{style}_64.png"
        from PIL import Image

        Image.fromarray(build_placeholder_skin(style), "RGBA").save(p)
        written.append(p)
    return written


if __name__ == "__main__":
    here = Path(__file__).resolve().parents[1] / "presets" / "textures" / "skins"
    for p in save_default_skins(here):
        print(p)
