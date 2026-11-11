"""Voxel engine — materials & palette.

Pure-Python helpers that produce the Blender-side material code and define the
default palette.  No Blender import here (testable without bpy).
"""

from __future__ import annotations

from typing import Any


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    """'#rrggbb' -> (r,g,b) in 0..1."""
    s = value.lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    r = int(s[0:2], 16) / 255.0
    g = int(s[2:4], 16) / 255.0
    b = int(s[4:6], 16) / 255.0
    return (r, g, b)


#: Default palette for the sunny-day voxel set.
DEFAULT_PALETTE: dict[str, str] = {
    "sky_top": "#5bb7f5",
    "sky_bottom": "#cfe9ff",
    "ground": "#7cb562",
    "ground_detail": "#5c9448",
    "grass": "#6fbf6f",
    "grass_dark": "#4f9a55",
    "trunk": "#8a5a34",
    "trunk_dark": "#6b4226",
    "leaf": "#3f9a4a",
    "leaf_dark": "#2e7a3a",
    "rock": "#9a9a9c",
    "rock_dark": "#7a7a7c",
    "stone": "#aef2c4",
    "stone_glow": "#d8f8df",
    "hero_skin": "#f2c9a8",
    "hero_hair": "#4a352a",
    "hero_shirt": "#37a79a",
    "hero_pants": "#2c7a72",
    "hero_eyes": "#3b6b9a",
    "hero_mouth": "#8a3b3b",
    "hero_shoes": "#2c2f38",
    "hero_trim": "#2a6b63",
}


def material_source(code: str = "materials") -> str:
    """Blender-side helper source for creating materials by palette key."""
    return f'''
# ---------------- materials ----------------
def _mat(key, strength=0.0):
    color = PALETTE[key]
    m = bpy.data.materials.new("M_" + key)
    m.use_nodes = True
    bs = m.node_tree.nodes.get("Principled BSDF")
    if bs:
        for inp in bs.inputs:
            n = inp.name
            if n == "Base Color":
                inp.default_value = (*color, 1.0)
            elif n == "Emission Color" and strength > 0.0:
                inp.default_value = (*color, 1.0)
            elif n == "Emission Strength" and strength > 0.0:
                inp.default_value = strength
    return m
def _emit(key, strength):
    m = _mat(key, strength)
    return m
'''
