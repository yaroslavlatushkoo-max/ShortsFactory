# blend3d/blender_lib/textures.py — Minecraft-style image textures + block factory.
# Own 16x16 pixel-art PNGs (no game assets) live in blend3d/presets/textures/.
import os
import bpy

TEX_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'presets', 'textures'))
FACES = ('top', 'bottom', 'north', 'south', 'east', 'west')

_loaded = {}

def tex_image(png_name):
    if png_name in _loaded:
        return _loaded[png_name]
    path = os.path.join(TEX_DIR, png_name + '.png')
    img = bpy.data.images.get(png_name)
    if img is None:
        img = bpy.data.images.load(path, check_existing=True)
        img.name = png_name
    img.colorspace_settings.name = 'sRGB'
    _loaded[png_name] = img
    return img

def tex_mat(tex_name, rough=0.95, emit=0.0, uv_scale=(1.0, 1.0)):
    """Image-texture material 'tx_<tex_name>'; cached by name."""
    mname = 'tx_' + tex_name
    existing = bpy.data.materials.get(mname)
    if existing is not None:
        return existing
    m = bpy.data.materials.new(mname)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = tex_image(tex_name)
    tex.interpolation = 'Closest'      # crisp Minecraft pixels
    try: tex.extension = 'REPEAT'
    except Exception: pass
    mp = nt.nodes.new('ShaderNodeMapping')
    mp.inputs['Scale'].default_value = (uv_scale[0], uv_scale[1], 1.0)
    coord = nt.nodes.new('ShaderNodeTexCoord')
    nt.links.new(coord.outputs['UV'], mp.inputs['Vector'])
    nt.links.new(mp.outputs['Vector'], tex.inputs['Vector'])
    nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    bsdf.inputs['Roughness'].default_value = rough
    if emit > 0.0:
        nt.links.new(tex.outputs['Color'], bsdf.inputs['Emission Color'])
        bsdf.inputs['Emission Strength'].default_value = emit
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    return m

def face_of_normal(n):
    if n.z > 0.5: return 'top'
    if n.z < -0.5: return 'bottom'
    if n.y > 0.5: return 'north'
    if n.y < -0.5: return 'south'
    if n.x > 0.5: return 'east'
    return 'west'

def box_uv(obj, uv_scale=(1.0, 1.0), per_face_fit=False):
    """Deterministic per-face UV for an axis-aligned box.
    per_face_fit=False: 0..uv_scale over the unit cube (full blocks).
    per_face_fit=True: each face spans the FULL texture 0..1 (limbs/torso —
    narrow boxes would otherwise sample only the middle of the skin)."""
    mesh = obj.data
    uvl = mesh.uv_layers.get('boxuv') or mesh.uv_layers.new(name='boxuv')
    if per_face_fit:
        faces_lo = {}
        for poly in mesh.polygons:
            n = poly.normal
            ax = max(range(3), key=lambda i: abs(n[i]))
            ua, va = (0, 1) if ax == 2 else (1, 2) if ax == 0 else (0, 2)
            cos = [mesh.vertices[mesh.loops[li].vertex_index].co for li in poly.loop_indices]
            lo = (min(c[ua] for c in cos), min(c[va] for c in cos))
            hi = (max(c[ua] for c in cos), max(c[va] for c in cos))
            faces_lo[poly.index] = (ua, va, lo, hi)
        for poly in mesh.polygons:
            ua, va, lo, hi = faces_lo[poly.index]
            du = (hi[0] - lo[0]) or 1.0
            dv = (hi[1] - lo[1]) or 1.0
            for k, li in enumerate(poly.loop_indices):
                co = mesh.vertices[mesh.loops[li].vertex_index].co
                u = (co[ua] - lo[0]) / du
                v = (co[va] - lo[1]) / dv
                uvl.data[poly.loop_start + k].uv = (u, v)
        return
    for poly in mesh.polygons:
        n = poly.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        if ax == 2:
            ua, va = 0, 1
        elif ax == 0:
            ua, va = 1, 2
        else:
            ua, va = 0, 2
        for k, li in enumerate(poly.loop_indices):
            co = mesh.vertices[mesh.loops[li].vertex_index].co
            u = (co[ua] + 0.5) * uv_scale[0]
            v = (co[va] + 0.5) * uv_scale[1]
            uvl.data[poly.loop_start + k].uv = (u, v)

BLOCK_FACES = {
    'grass': {'top': 'grass_top', 'bottom': 'dirt',
              'north': 'grass_side', 'south': 'grass_side',
              'east': 'grass_side', 'west': 'grass_side'},
}
for _b in ('dirt', 'stone', 'cobblestone', 'coal_ore', 'diamond_ore', 'iron_ore',
           'leaves', 'planks', 'lava', 'bedrock', 'obsidian', 'diamond_block'):
    BLOCK_FACES[_b] = {f: _b for f in FACES}
BLOCK_FACES['log'] = dict(top='log_top', bottom='log_top',
                          north='log_side', south='log_side',
                          east='log_side', west='log_side')

EMIT_TEX = {'lava': 3.2}

def tex_block(name, cell, block_id, size=1.0, uv_scale=(1.0, 1.0)):
    """Textured grid block at integer cell. Materials per face + box UV.
    Registers custom props '_cell' and 'block_id' for geometry audit."""
    faces = BLOCK_FACES[block_id]
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(cell[0], cell[1], cell[2]))
    o = bpy.context.active_object
    o.name = name
    o.scale = (size, size, size)
    mats = {}
    for face, tex_name in faces.items():
        mats[face] = tex_mat(tex_name,
                             rough=0.6 if tex_name == 'lava' else 0.95,
                             emit=EMIT_TEX.get(tex_name, 0.0),
                             uv_scale=uv_scale)
    mesh = o.data
    for face in FACES:
        mesh.materials.append(mats[face])
    mesh.uv_layers.new(name='boxuv')
    for poly in mesh.polygons:
        face = face_of_normal(poly.normal)
        mm = mats[face]
        for mi, material in enumerate(mesh.materials):
            if material == mm:
                poly.material_index = mi
                break
    box_uv(o, uv_scale)
    o['_cell'] = list(cell)
    o['block_id'] = block_id
    return o

def mc_block(name, cell, block_id, size=1.0):
    """Alias kept for film scripts."""
    return tex_block(name, cell, block_id, size=size)
