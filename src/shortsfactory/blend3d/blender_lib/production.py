# blend3d/blender_lib/production.py — lighting, fx, camera, titles, render config, driver.
import sys
import traceback
from core import *

def add_light(name, type_, energy, color, loc, size=None, angle=None):
    ld = bpy.data.lights.new(name, type_)
    ld.energy = energy; ld.color = color
    if type_ == 'POINT': ld.shadow_soft_size = size or 0.1
    if type_ == 'AREA': ld.size = size or 1.0
    if type_ == 'SUN': ld.angle = angle or DEG(6)
    o = bpy.data.objects.new(name, ld); o.location = loc; link(o)
    return o

def aim(o, target):
    d = (P(*target) - o.location).normalized()
    o.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()

def two_shot_pos(a, b, side=(1, 0), lens=16, z=1.45, tz=0.95, pad=1.0, min_d=2.8):
    """Auto-framed profile two-shot for portrait 9:16 (v-FOV = 2*atan(10.125/lens)).
    Returns (pos, target): camera on `side` of the a-b midpoint, far enough that
    both characters fit with headroom. side=(1,0) shoots from the east, etc."""
    import math as _m
    mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
    span = _m.hypot(b[0] - a[0], b[1] - a[1]) + 1.4          # characters are ~0.6 m wide
    d_h = (span * pad / 2.0) * lens / 5.695                   # horizontal fit
    d_v = 1.45 * lens / 10.125                                # vertical fit (~2.9 m need)
    d = max(d_h, d_v, min_d)
    n = _m.hypot(side[0], side[1]) or 1.0
    return P(mx + side[0] / n * d, my + side[1] / n * d, z), P(mx, my, tz)

def ots_pos(subj, facing, side=1, d=2.6, lens=17, z=1.6, tz=0.9, ahead=1.3):
    """Over-the-shoulder single: camera behind `subj` offset `side` (+1 right),
    looking ahead along `facing` so the action target stays in frame."""
    import math as _m
    fx, fy = facing
    n = _m.hypot(fx, fy) or 1.0
    fx, fy = fx / n, fy / n
    sx, sy = -fy * side, fx * side                            # right-hand perpendicular
    pos = P(subj[0] - fx * d + sx * 0.55, subj[1] - fy * d + sy * 0.55, z)
    tgt = P(subj[0] + fx * ahead, subj[1] + fy * ahead, tz)
    return pos, tgt

def sky_gradient(top_hex, mid_hex, bot_hex, strength=0.55):
    world = bpy.data.worlds.new('World'); bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree; nt.nodes.clear()
    tc = nt.nodes.new('ShaderNodeTexCoord')
    sep = nt.nodes.new('ShaderNodeSeparateXYZ')
    mr = nt.nodes.new('ShaderNodeMapRange')
    mr.inputs['From Min'].default_value = -0.12; mr.inputs['From Max'].default_value = 0.55
    cr = nt.nodes.new('ShaderNodeValToRGB')
    cr.color_ramp.elements[0].color = (*hx(bot_hex), 1)
    e_mid = cr.color_ramp.elements.new(0.55); e_mid.color = (*hx(mid_hex), 1)
    cr.color_ramp.elements[-1].color = (*hx(top_hex), 1)
    bg = nt.nodes.new('ShaderNodeBackground'); bg.inputs['Strength'].default_value = strength
    wo = nt.nodes.new('ShaderNodeOutputWorld')
    nt.links.new(tc.outputs['Generated'], sep.inputs['Vector'])
    nt.links.new(sep.outputs['Z'], mr.inputs['Value'])
    nt.links.new(mr.outputs['Result'], cr.inputs['Fac'])
    nt.links.new(cr.outputs['Color'], bg.inputs['Color'])
    nt.links.new(bg.outputs['Background'], wo.inputs['Surface'])

class CamRig:
    """Camera with keyframed pos/target/lens; smoothstep between keys; cuts via adjacent keys."""
    def __init__(self, keys):
        self.keys = keys  # (frame, pos, target, lens)
        data = bpy.data.cameras.new('cam'); data.lens = keys[0][3]
        # NOTE: Blender 5.2 portrait AUTO-fit yields v-FOV ~ 2*atan(10.125/lens);
        # verified empirically on renders — keep AUTO and author lenses around it.
        self.obj = bpy.data.objects.new('cam', data)
        link(self.obj)
        self.data = data
        bpy.context.scene.camera = self.obj

    def state(self, f):
        ks = self.keys
        if f <= ks[0][0]: i = 0
        elif f >= ks[-1][0]: i = len(ks) - 2
        else:
            i = 0
            for j in range(len(ks) - 1):
                if ks[j][0] <= f < ks[j + 1][0]: i = j; break
        f0, p0, t0, l0 = ks[i]; f1, p1, t1, l1 = ks[i + 1]
        u = ss(u_in(f, f0, f1))
        return p0.lerp(p1, u), t0.lerp(t1, u), lerp(l0, l1, u)

    def bake(self, f_end, shake=1.0):
        for f in range(1, f_end + 1):
            cp, ct, cl = self.state(f)
            nz1 = 0.008 * math.sin(0.10 * f + 1.3) + 0.006 * math.sin(0.23 * f)
            nz2 = 0.008 * math.cos(0.09 * f) + 0.006 * math.sin(0.21 * f + 0.7)
            ct2 = ct + P(nz1 * shake, nz2 * shake, 0.005 * shake * math.sin(0.15 * f))
            kfV(self.obj, 'location', f, cp)
            dirv = ct2 - cp
            if dirv.length < 1e-5: dirv = P(0, -1, 0)
            kfV(self.obj, 'rotation_euler', f, dirv.to_track_quat('-Z', 'Y').to_euler())
            self.data.lens = cl
            self.data.keyframe_insert('lens', frame=f)

def burst(name, loc, n, mat_name, speed=1.6, size=0.05, up=1.0, seed0=0):
    """Expanding particle burst; returns list of (obj, velocity). Base scale preserved via custom prop."""
    out = []
    for i in range(n):
        a = R(seed0 + i) * math.tau
        v = P(math.cos(a) * (0.4 + R(seed0 + i + 7) * 0.6) * speed,
              math.sin(a) * (0.4 + R(seed0 + i + 17) * 0.6) * speed,
              up * (0.6 + R(seed0 + i + 27)))
        o = box(f'{name}{i}', loc, (size, size, size), MATS[mat_name][0],
                rot=(R(seed0 + i) * 3, R(seed0 + i + 3) * 3, 0))
        o['_base_scale'] = size
        o.scale = (0.01, 0.01, 0.01)
        out.append((o, v))
    return out

def bake_burst(parts, f, f0, dur, gravity=2.2):
    for o, v in parts:
        bsz = o.get('_base_scale', 0.05)
        if f0 <= f <= f0 + dur:
            u = u_in(f, f0, f0 + dur)
            base = parts_pos(o)
            t = u * dur / FPS
            p = base + v * t - P(0, 0, 0.5 * gravity * t * t)
            kfV(o, 'location', f, p)
            s = max(0.02, bsz * (1.0 - u * 0.8))
            kfV(o, 'scale', f, P(s, s, s))
        else:
            kfV(o, 'scale', f, P(0.01, 0.01, 0.01))

def parts_pos(o):
    v = o.get('_base_loc')
    return Vector(v) if v is not None else o.location.copy()

def set_base(parts):
    for o, v in parts:
        o['_base_loc'] = o.location.copy()

def fade_plane_and_titles(cam, title_lines, end_label, F_END,
                          t_in=(25, 70, 135, 168), t_out=(1385, 1440, 1462, 1500)):
    mat('ov_title', hx('FFF3D6'), rough=0.4, emit=hx('FFF3D6'), es=1.3, alpha=0.0)
    mat('ov_end', hx('FFF3D6'), rough=0.4, emit=hx('FFF3D6'), es=1.3, alpha=0.0)
    mat('ov_fade', hx('000000'), rough=0.5, es=0.0, alpha=0.0)
    mk('ov_title', 'Alpha', [(1, 0.0), (t_in[0], 0.0), (t_in[1], 1.0), (t_in[2], 1.0), (t_in[3], 0.0)])
    mk('ov_end', 'Alpha', [(1, 0.0), (t_out[0], 0.0), (t_out[1], 1.0), (F_END, 1.0)])
    mk('ov_fade', 'Alpha', [(1, 1.0), (16, 0.0), (t_out[0], 0.0), (t_out[1], 1.0), (F_END, 1.0)])
    font = bpy.data.fonts.load(r'C:\Windows\Fonts\arialbd.ttf')
    def add_text(name, body, size, mkey):
        cu = bpy.data.curves.new(name, 'FONT')
        cu.body = body; cu.size = size; cu.font = font
        cu.align_x = 'CENTER'; cu.align_y = 'CENTER'
        o = bpy.data.objects.new(name, cu); link(o)
        o.data.materials.append(MATS[mkey][0])
        return o
    objs = []
    n = len(title_lines)
    for i, (line, size) in enumerate(title_lines):
        o = add_text(f't{i}', line, size, 'ov_title')
        parent_cam(o, cam, (0, 0.088 - 0.030 * (n - 1 - i * 1.0), -0.32))
        objs.append(o)
    end_o = add_text('end_t', end_label, 0.030, 'ov_end')
    parent_cam(end_o, cam, (0, 0.02, -0.31))
    star = box('end_star', (0, 0, 0), (0.016, 0.005, 0.016), MATS['ov_end'][0])
    parent_cam(star, cam, (0, 0.048, -0.31))
    fade = cube_round('fade_plane', (0, 0, 0), 0.85, MATS['ov_fade'][0], rot=(DEG(90), 0, 0))
    parent_cam(fade, cam, (0, 0.0, -0.36))
    return objs

def render_cfg(F_END, out_base, engine='BLENDER_EEVEE', samples=40, exposure=0.45):
    sc = bpy.context.scene
    sc.render.engine = engine
    sc.eevee.taa_render_samples = samples
    try: sc.eevee.light_threshold = 0.02
    except Exception: pass
    sc.render.resolution_x = 1080; sc.render.resolution_y = 1920
    sc.render.resolution_percentage = 100
    sc.render.fps = FPS; sc.render.fps_base = 1.0
    sc.frame_start = 1; sc.frame_end = F_END
    sc.view_settings.view_transform = 'Standard'
    try: sc.view_settings.exposure = exposure
    except Exception: pass
    sc.render.image_settings.file_format = 'JPEG'
    sc.render.image_settings.quality = 93

def driver(mode, F_END, out_dir, out_prefix, preview_frames, ranges=None):
    sc = bpy.context.scene
    if mode == 'preview':
        sc.render.filepath = out_dir + '\\' + out_prefix + '####'
        for f in preview_frames:
            sc.frame_start = f; sc.frame_end = f
            bpy.ops.render.render(animation=True)
            log(f'preview {f} done')
    elif mode == 'render':
        sc.render.filepath = out_dir + '\\' + out_prefix + '####'
        ranges = ranges or [(1, F_END)]
        for f0, f1 in ranges:
            sc.frame_start = f0; sc.frame_end = f1
            bpy.ops.render.render(animation=True)
            log(f'render {f0}..{f1} done')

def run_driver(F_END, out_dir, out_prefix, preview_frames):
    mode = 'build'
    ranges = None
    if '--' in sys.argv:
        rest = sys.argv[sys.argv.index('--') + 1:]
        if rest and rest[0] in ('preview', 'render'): mode = rest[0]
        if len(rest) >= 3 and rest[0] == 'render':
            try:
                ranges = [(int(rest[1]), int(rest[2]))]
            except ValueError:
                ranges = None
    try:
        driver(mode, F_END, out_dir, out_prefix, preview_frames, ranges)
    except Exception:
        log('driver FAILED: ' + traceback.format_exc())
    return mode
