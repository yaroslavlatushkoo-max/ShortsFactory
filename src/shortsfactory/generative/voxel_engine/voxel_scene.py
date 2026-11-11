"""Voxel engine — cubic world builder.

Returns a Blender-side source fragment that builds a deterministic blocky
environment (ground, grass, trees, rocks, sky backdrop) using simple cube
meshes.  Light, low-poly, EEVEE-friendly for a 4 GB VRAM laptop GPU.
"""

from __future__ import annotations

import random

from .voxel_materials import DEFAULT_PALETTE


def build_scene_source(seed: int = 20261023) -> str:
    """Return a Python source chunk that constructs the scene in Blender."""
    # Deterministic procedural layout.
    rng = random.Random(seed)
    # Trees live behind the hero (negative Y) or at the side edges so they never
    # occlude the hero or the cube in the camera's front frame.
    trees = []
    for _ in range(6):
        tx = rng.choice([rng.uniform(-3.6, -2.6), rng.uniform(2.6, 3.6), rng.uniform(-1.2, 1.2)])
        ty = rng.uniform(-3.0, -0.4)
        h = rng.randint(3, 5)
        trees.append((round(tx, 2), round(ty, 2), h))
    rocks = []
    for _ in range(5):
        rx = rng.uniform(-3.8, 3.8)
        ry = rng.choice([rng.uniform(-3.2, -1.0), rng.uniform(4.2, 5.4)])
        rocks.append((round(rx, 2), round(ry, 2), rng.choice([0.4, 0.55])))

    lines = [
        "# ---------------- scene (cubic world) ----------------",
        "_S = 0.25  # base voxel unit",
        "# ground slab",
        "_g = add_cube('ground_slab', 12.0, (0.0, 0.0, -0.35), _mat('ground'))",
        "_g.scale = (12.0, 8.0, 0.7)",
        "for _x in (-3.2, 0.0, 3.2):",
        "    for _y in (-2.2, 0.6, 3.4):",
        "        add_cube('grass_patch_%d_%d' % (_x, _y), 1.2, (_x, _y, 0.05), _mat('grass'))",
        "# blocky trees",
    ]
    for i, (tx, ty, h) in enumerate(trees):
        lines.append(f"# tree {i}")
        lines.append(f"for _c in range({h}):")
        lines.append(f"    add_cube('trunk_{i}_{{_c}}', 0.5, ({tx}, {ty}, 0.25 + 0.5*_c), _mat('trunk'))")
        lines.append(f"add_cube('leaf_{i}_a', 1.5, ({tx}, {ty}, 0.5 + 0.5*{h}), _mat('leaf'))")
        lines.append(f"add_cube('leaf_{i}_b', 1.1, ({tx} + 0.5, {ty}, 0.7 + 0.5*{h}), _mat('leaf_dark'))")
        lines.append(f"add_cube('leaf_{i}_c', 1.1, ({tx} - 0.5, {ty}, 0.7 + 0.5*{h}), _mat('leaf_dark'))")
        lines.append(f"add_cube('leaf_{i}_d', 1.0, ({tx}, {ty} + 0.5, 0.9 + 0.5*{h}), _mat('leaf'))")
        lines.append(f"add_cube('leaf_{i}_e', 1.0, ({tx}, {ty} - 0.5, 0.9 + 0.5*{h}), _mat('leaf'))")
    lines.append("# rocks")
    for i, (rx, ry, rs) in enumerate(rocks):
        lines.append(
            f"add_cube('rock_{i}', {rs}, ({rx}, {ry}, {rs}/2), _mat('rock'))"
        )
    # soft self-illuminated glow spots near hero/stone (decorative)
    lines.append("_gs = add_cube('glow_spots', 0.3, (0.9, 1.7, 0.15), _emit('stone_glow', 2.0))")
    # Twilight backdrop: a large emissive plane far behind + world color
    lines += [
        "# bright sunny-day background",
        "_w = bpy.data.worlds.new('Day')",
        "_w.use_nodes = True",
        "_bg = _w.node_tree.nodes.get('Background')",
        "if _bg:",
        "    _bg.inputs[0].default_value = (*PALETTE['sky_top'], 1.0)",
        "    _bg.inputs[1].default_value = 1.0",
        "bpy.context.scene.world = _w",
        "# warm key sun (clear day) + strong fill",
        "_sun = bpy.data.lights.new('Sun', 'SUN')",
        "_so = bpy.data.objects.new('Sun', _sun)",
        "bpy.context.collection.objects.link(_so)",
        "_so.rotation_euler = (1.0, 0.15, 0.5)",
        "_so.data.energy = 4.0",
        "_fill = bpy.data.lights.new('Fill', 'AREA')",
        "_fo = bpy.data.objects.new('Fill', _fill)",
        "bpy.context.collection.objects.link(_fo)",
        "_fo.location = (3.0, -3.0, 4.0)",
        "_fo.data.energy = 250.0",
        "_fo.data.size = 3.0",
        "_rim = bpy.data.lights.new('Rim', 'AREA')",
        "_ro = bpy.data.objects.new('Rim', _rim)",
        "bpy.context.collection.objects.link(_ro)",
        "_ro.location = (-3.5, -2.0, 3.0)",
        "_ro.rotation_euler = (0.6, 0.0, 1.2)",
        "_ro.data.energy = 120.0",
        "_ro.data.size = 2.5",
    ]
    return "\n".join(lines)
