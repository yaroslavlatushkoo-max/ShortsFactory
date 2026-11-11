# blend3d/blender_lib/blocks.py — Minecraft-style block palette and builders.
from core import *

PAL = {
    'grass_top': ('4FA341', 0.85), 'dirt': ('7A5230', 0.9), 'stone': ('8A8A8A', 0.85),
    'stone_dark': ('6E6E6E', 0.9), 'cobble': ('7D7D7D', 0.9), 'coal_ore': ('8A8A8A', 0.85),
    'iron_ore': ('9A9AA0', 0.8), 'diamond_ore': ('8A9A9A', 0.8), 'log': ('5A4426', 0.85),
    'log_top': ('8A6B3E', 0.85), 'leaves': ('3E7A34', 0.9), 'wood': ('6B4A2E', 0.8),
    'lava': ('FF7A1E', 0.6), 'diamond': ('4FE8DC', 0.25), 'coal': ('2A2A2A', 0.6),
    'iron': ('D8C8B8', 0.35), 'torch_wood': ('8A6B3E', 0.8), 'flame': ('FFC24A', 0.4),
    'crack': ('2E2E2E', 0.95), 'pick_handle': ('8A6B3E', 0.7), 'pick_head': ('9AA4AC', 0.35),
    'bedrock': ('565656', 0.95),
}

def build_palette():
    for key, (hexc, rough) in PAL.items():
        es = 3.0 if key in ('lava', 'flame') else (1.6 if key == 'diamond' else 0.0)
        mat('mc_' + key, hx(hexc), rough=rough, es=es)
    mat('mc_diamond', hx('4FE8DC'), rough=0.25, metal=0.1, es=0.9)
    mat('mc_lava', hx('FF7A1E'), rough=0.6, emit=hx('FF9A30'), es=3.2)
    mat('mc_flame', hx('FFC24A'), rough=0.4, emit=hx('FFC24A'), es=4.0)

def speck_block(name, ijk, base_mat, speck_mat, n=6, s=0.16, seed=0):
    """Stone block with mineral specks embedded in faces. Names without mc_ prefix."""
    b = cube_block(name, ijk, MATS['mc_' + base_mat][0])
    i, j, k = ijk
    for q in range(n):
        sx = (R(seed + q * 3 + 1) - 0.5) * 0.62
        sy = (R(seed + q * 3 + 2) - 0.5) * 0.62
        sz = (R(seed + q * 3 + 3) - 0.5) * 0.62
        box(f'{name}_s{q}', P(i + sx, j + sy, k + sz), (s, s, s), MATS['mc_' + speck_mat][0],
            rot=(R(seed + q) * 0.6, R(seed + q + 9) * 0.6, R(seed + q + 5) * 0.6))
    return b

def coal_block(name, ijk, seed=0):
    return speck_block(name, ijk, 'stone', 'coal', n=7, s=0.2, seed=seed)

def iron_block(name, ijk, seed=0):
    return speck_block(name, ijk, 'stone', 'iron', n=6, s=0.18, seed=seed)

def diamond_block(name, ijk, seed=0):
    return speck_block(name, ijk, 'stone', 'diamond', n=7, s=0.19, seed=seed)

def grass_block(name, ijk):
    i, j, k = ijk
    box(name + '_d', P(i, j, k - 0.06), (1, 1, 0.88), MATS['mc_dirt'][0], bevel=0.015)
    return box(name + '_t', P(i, j, k + 0.42), (1, 1, 0.18), MATS['mc_grass_top'][0], bevel=0.015)

def tree(name, x, y, h=4):
    """Cube tree: log column + leaf blob."""
    for k in range(h):
        m = MATS['mc_log_top'][0] if k == h - 1 else MATS['mc_log'][0]
        box(f'{name}_log{k}', P(x, y, k + 0.5), (0.6, 0.6, 1.0), m)
    for dx, dy, dz, s in ((0, 0, h + 0.3, 2.6), (0.9, 0.3, h - 0.2, 1.7), (-0.8, -0.4, h - 0.1, 1.8),
                          (0.2, -0.9, h + 0.1, 1.5), (-0.3, 0.8, h + 0.2, 1.6)):
        box(f'{name}_l{dx}{dy}{dz}', P(x + dx, y + dy, dz), (s, s, s), MATS['mc_leaves'][0], bevel=0.05)

def torch(name, loc, wall=None, scale=1.0):
    """Torch: small stick + glowing tip; wall=(nx,ny) normal direction if mounted."""
    x, y, z = loc
    stick = box(name + '_st', P(x, y, z), (0.09 * scale, 0.09 * scale, 0.5 * scale), MATS['mc_torch_wood'][0])
    tip = box(name + '_tip', P(x, y, z + 0.3 * scale), (0.13 * scale, 0.13 * scale, 0.14 * scale), MATS['mc_flame'][0])
    ld = bpy.data.lights.new(name + '_l', 'POINT')
    ld.energy = 26.0 * scale; ld.color = hx('FFB45A'); ld.shadow_soft_size = 0.12
    lo = bpy.data.objects.new(name + '_l', ld)
    lo.location = P(x, y, z + 0.42 * scale)
    link(lo)
    return stick, tip, lo

def lava_pool(name, i0, j0, w, d, z):
    """Emissive lava plane; returns surface object for emission animation."""
    surf = box(name, P(i0 + w / 2 - 0.5, j0 + d / 2 - 0.5, z), (w, d, 0.35), MATS['mc_lava'][0])
    ld = bpy.data.lights.new(name + '_l', 'POINT')
    ld.energy = 14.0; ld.color = hx('FF8A30'); ld.shadow_soft_size = 0.7
    lo = bpy.data.objects.new(name + '_l', ld)
    lo.location = P(i0 + w / 2 - 0.5, j0 + d / 2 - 0.5, z + 0.5)
    link(lo)
    return surf, lo

def star_sky(n=140, radius=44.0, seed0=500):
    for i in range(n):
        a = R(i + seed0) * math.tau
        zz = 0.15 + R(i + seed0 + 100) * 0.85
        horiz = math.sqrt(max(0.0, 1 - zz * zz))
        pos = P(math.cos(a) * horiz, math.sin(a) * horiz, zz) * radius
        mi = i % 6
        if f'mc_star_{mi}' not in MATS:
            mat(f'mc_star_{mi}', hx('E8F0FF'), rough=0.3, emit=hx('D8E8FF'), es=1.4)
        sphere(f'star{i}', pos, 0.014 + R(i + seed0 + 200) * 0.02, MATS[f'mc_star_{mi}'][0], seg=8)
