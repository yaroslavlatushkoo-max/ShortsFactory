# blend3d/blender_lib/core.py — proven helpers from «Маленький садовник», generalized.
# Runs INSIDE Blender's python (bpy). No shortsfactory imports here.
import bpy, bmesh, math
from mathutils import Vector, Matrix, Euler

FPS = 30

def P(x, y, z): return Vector((x, y, z))
def clamp(u, a=0.0, b=1.0): return max(a, min(b, u))
def ss(u):
    u = clamp(u); return u * u * (3 - 2 * u)
def lerp(a, b, u): return a + (b - a) * u
def eob(u, s=1.9):
    u = clamp(u) - 1.0; return 1.0 + u * u * ((s + 1.0) * u + s)
def u_in(f, f0, f1): return clamp((f - f0) / max(1e-6, (f1 - f0)))
def R(seed):
    x = math.sin(seed * 12.9898 + 78.233) * 43758.5453
    return x - math.floor(x)
def DEG(a): return math.radians(a)

LOG = []
def log(msg):
    LOG.append(str(msg)); print("[B3D]", msg)

def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return bpy.context.scene

def link(o, col=None):
    (col or bpy.context.scene.collection).objects.link(o); return o

def new_empty(name, loc, parent=None):
    e = bpy.data.objects.new(name, None)
    e.empty_display_size = 0.05
    e.location = loc
    link(e)
    if parent: e.parent = parent
    return e

def shade_smooth(o):
    try: o.data.shade_smooth()
    except Exception:
        for p in o.data.polygons: p.use_smooth = True

def shade_flat(o):
    try: o.data.shade_flat()
    except Exception:
        for p in o.data.polygons: p.use_smooth = False

def hx(h):
    h = h.lstrip('#'); return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))

MATS = {}
def mat(name, base, rough=0.5, metal=0.0, emit=None, es=0.0, alpha=1.0):
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes.get('Principled BSDF')
    def setin(names, v):
        for n in names:
            if n in b.inputs: b.inputs[n].default_value = v; return b.inputs[n]
        raise KeyError(f"{name}: {names}")
    setin(['Base Color'], (*base, 1.0))
    setin(['Roughness'], rough); setin(['Metallic'], metal)
    setin(['Emission Color'], (*(emit or base), 1.0))
    setin(['Emission Strength'], es)
    if alpha < 1.0:
        setin(['Alpha'], alpha)
        for attr, val in (('surface_render_method', 'BLENDED'), ('blend_method', 'BLEND')):
            if hasattr(m, attr):
                try: setattr(m, attr, val)
                except Exception: pass
    MATS[name] = (m, b)
    return m

def mk(name, socket, keys):
    inp = MATS[name][1].inputs[socket]
    for f, v in keys:
        inp.default_value = v
        inp.keyframe_insert('default_value', frame=f)

def kfV(o, path, f, v):
    setattr(o, path, v)
    o.keyframe_insert(data_path=path, frame=f)

def parent(child, par):
    child.parent = par
    child.matrix_parent_inverse = Matrix()

def sphere(name, loc, r, material, scale=(1, 1, 1), rot=(0, 0, 0), seg=24):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=loc, segments=seg, ring_count=max(8, seg // 2))
    o = bpy.context.active_object; o.name = name
    o.scale = scale; o.rotation_euler = rot
    shade_smooth(o); o.data.materials.append(material)
    return o

def cyl(name, loc, r, depth, material, rot=(0, 0, 0), seg=24):
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=depth, location=loc, vertices=seg)
    o = bpy.context.active_object; o.name = name
    o.rotation_euler = rot
    shade_smooth(o); o.data.materials.append(material)
    return o

def cone(name, loc, r, depth, material, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_cone_add(radius1=r, radius2=0.0, depth=depth, location=loc, vertices=24)
    o = bpy.context.active_object; o.name = name
    o.rotation_euler = rot
    shade_smooth(o); o.data.materials.append(material)
    return o

def box(name, loc, size, material, rot=(0, 0, 0), bevel=0.0):
    """Axis-aligned cube centered at loc. size=(sx,sy,sz). bevel rounds edges slightly."""
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    o = bpy.context.active_object; o.name = name
    o.scale = size
    o.rotation_euler = rot
    shade_flat(o)
    if bevel > 0.0:
        mod = o.modifiers.new('Bevel', 'BEVEL')
        mod.width = bevel; mod.segments = 2
    o.data.materials.append(material)
    return o

def cube_block(name, ijk, material, size=1.0, bevel=0.01):
    """Minecraft-style block: unit cube centered on integer grid cell (i,j,k)."""
    return box(name, P(ijk[0], ijk[1], ijk[2]), (size, size, size), material, bevel=bevel)

def cube_round(name, loc, size, material, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    o = bpy.context.active_object; o.name = name
    o.rotation_euler = rot
    o.data.materials.append(material)
    return o

def parent_cam(o, cam, loc):
    o.parent = cam
    o.matrix_parent_inverse = Matrix()
    o.location = loc
    return o

def rz(v, ang):
    c, s = math.cos(ang), math.sin(ang)
    return P(v.x * c - v.y * s, v.x * s + v.y * c, v.z)

def heading_to(fr, to):
    d = P(to.x - fr.x, to.y - fr.y, 0)
    if d.length < 1e-6: return None
    return math.atan2(d.x, -d.y)
