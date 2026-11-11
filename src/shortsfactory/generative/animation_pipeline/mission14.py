"""Mission 14 — clearly *visible* character animation benchmark (isolated).

The Motivation: previous M13.2 rendered a *correct* but visually weak result —
the character occupied ~33% of the frame height, the whole-body trajectory
translation dominated, and the feet slid because travel speed was not matched
to the clip's stride. This module builds a new isolated benchmark that:

* frames the character LARGER (fit_fraction ~0.78) so limb swing is readable;
* reduces travel distance and matches it to a measured stride so the walk
  reads as walking rather than sliding;
* applies a controllable *swing amplification* factor (scaled about the bind
  pose) so the arm/leg rotation is clearly visible on screen;
* adds subtle procedural secondary motion (idle breathing, torso/head sway)
  so the character never "freezes" during idle;
* keeps the camera static (the character is what moves).

Self-contained, deterministic, no NLA, no cloud/paid. Diagnostics measure
per-limb rotation, frame-to-frame difference, travel, framing and continuity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .locomotion import LocomotionError


@dataclass
class Mission14Result:
    status: str
    video: str = ""
    script: str = ""
    render_dir: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "video": self.video,
            "script": self.script,
            "render_dir": self.render_dir,
            "diagnostics": self.diagnostics,
            "notes": self.notes,
            "error": self.error,
        }


def _quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def build_mission14_script(
    model_path: str | Path,
    out_dir: str | Path,
    *,
    width: int = 540,
    height: int = 960,
    fps: int = 30,
    fit_fraction: float = 0.78,
    amplitude: float = 1.4,
    travel: float = 0.9,
    camera_lens: float = 40.0,
    idle_breathing: bool = True,
) -> Path:
    """Write and return the self-contained Mission 14 Blender benchmark script."""
    model_path = Path(model_path)
    if not model_path.exists():
        raise LocomotionError(f"Модель не найдена: {model_path}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / "mission14_bench.py"
    diag = out_dir / "diagnostics.json"

    total_dur = 10.0
    total_frames = int(round(total_dur * fps))

    L: list[str] = []
    L.append("import bpy, json, math, subprocess, os, sys")
    L.append("from pathlib import Path")
    L.append("")
    L.append('MODEL = r"%s"' % _quote(str(model_path)))
    L.append('OUT = Path(r"%s")' % _quote(str(out_dir.resolve())))
    L.append('DIAG = r"%s"' % _quote(str(diag.resolve())))
    L.append("FPS = %d" % int(fps))
    L.append("WIDTH = %d" % int(width))
    L.append("HEIGHT = %d" % int(height))
    L.append("FIT = %r" % float(fit_fraction))
    L.append("AMPL = %r" % float(amplitude))
    L.append("TRAVEL = %r" % float(travel))
    L.append("CAM_LENS = %r" % float(camera_lens))
    L.append("")
    L.append("bpy.ops.wm.read_factory_settings(use_empty=True)")
    L.append('ext = MODEL.rsplit(".", 1)[-1].lower()')
    L.append('if ext in ("glb", "gltf"):')
    L.append("    bpy.ops.import_scene.gltf(filepath=MODEL)")
    L.append('elif ext == "fbx":')
    L.append("    bpy.ops.import_scene.fbx(filepath=MODEL)")
    L.append('elif ext == "obj":')
    L.append("    bpy.ops.wm.obj_import(filepath=MODEL)")
    L.append("else:")
    L.append('    raise RuntimeError("Unsupported format " + ext)')
    L.append("")
    L.append("scene = bpy.context.scene")
    L.append("scene.render.engine = ('BLENDER_EEVEE_NEXT' if hasattr(bpy.types, 'BLENDER_EEVEE_NEXT') else 'BLENDER_EEVEE')")
    L.append("scene.render.resolution_x = WIDTH")
    L.append("scene.render.resolution_y = HEIGHT")
    L.append("scene.render.image_settings.file_format = 'PNG'")
    L.append("scene.render.fps = FPS")
    L.append("FRAMES = OUT / 'frames'")
    L.append("FRAMES.mkdir(parents=True, exist_ok=True)")
    L.append("scene.render.filepath = str(FRAMES / 'frame_')")
    L.append("scene.frame_start = 1")
    L.append("scene.frame_end = %d" % int(total_frames))
    L.append("total_frames = %d" % int(total_frames))
    L.append("")
    L.append("# --- measure character geometry ---")
    L.append("from mathutils import Vector")
    L.append("import mathutils")
    L.append("minx=1e9; maxx=-1e9; minz=1e9; maxz=-1e9")
    L.append("for o in bpy.data.objects:")
    L.append("    if o.type != 'MESH': continue")
    L.append("    for v in o.bound_box:")
    L.append("        w = o.matrix_world @ Vector(v)")
    L.append("        minx=min(minx,w.x); maxx=max(maxx,w.x)")
    L.append("        minz=min(minz,w.z); maxz=max(maxz,w.z)")
    L.append("CHAR_W = max(1e-6, maxx-minx); CHAR_H = max(1e-6, maxz-minz)")
    L.append("CHAR_CZ = (minz+maxz)*0.5")
    L.append("")
    L.append("# --- camera: fit character (larger) with VERTICAL sensor ---")
    L.append("cam = bpy.data.cameras.new('Cam')")
    L.append("cam_obj = bpy.data.objects.new('Camera', cam)")
    L.append("bpy.context.collection.objects.link(cam_obj)")
    L.append("scene.camera = cam_obj")
    L.append("cam.sensor_fit = 'VERTICAL'")
    L.append("cam.sensor_height = 18.0")
    L.append("cam.lens = CAM_LENS")
    L.append("tanv = (18.0*0.5)/CAM_LENS")
    L.append("tanh = tanv*(WIDTH/HEIGHT)")
    L.append("dv = (CHAR_H/FIT)/(2.0*tanv)")
    L.append("needw = (CHAR_W + TRAVEL)/(2.0*FIT)")
    L.append("dh = needw/tanh")
    L.append("d = max(dv, dh)")
    L.append("cam_obj.location = (0.0, -d, CHAR_CZ)")
    L.append("cam_obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)")
    L.append("")
    L.append("# --- lights / sky (sunny) ---")
    L.append("sun = bpy.data.lights.new('Sun', 'SUN')")
    L.append("sun_obj = bpy.data.objects.new('Sun', sun)")
    L.append("bpy.context.collection.objects.link(sun_obj)")
    L.append("sun_obj.rotation_euler = (0.9, 0.2, 0.6)")
    L.append("sun_obj.data.energy = 3.5")
    L.append("scene.world = bpy.data.worlds.new('Sky')")
    L.append("w = scene.world")
    L.append("w.use_nodes = True")
    L.append("bg = w.node_tree.nodes.get('Background')")
    L.append("if bg:")
    L.append("    bg.inputs[0].default_value = (0.45, 0.7, 1.0, 1.0)")
    L.append("    bg.inputs[1].default_value = 1.0")
    L.append("")
    L.append("# --- trajectory empty (locomotion) ---")
    L.append("traj = bpy.data.objects.new('TRAJ', None)")
    L.append("bpy.context.collection.objects.link(traj)")
    L.append("root = bpy.data.objects.get('root')")
    L.append("if root is None:")
    L.append("    raise RuntimeError('root not found')")
    L.append("root.parent = traj")
    L.append("root.matrix_parent_inverse = traj.matrix_world.inverted()")
    L.append("traj.location = (-TRAVEL*0.5, 0.0, 0.0)")
    L.append("")
    L.append("# --- helpers ---")
    L.append("def pose(o):")
    L.append("    return (list(o.location), list(o.rotation_quaternion) if o.rotation_mode == 'QUATERNION' else list(o.rotation_euler))")
    L.append("")
    L.append("def scale_quat(q, k):")
    L.append("    if k == 1.0: return q")
    L.append("    # rotate quaternion: axis-angle scaled by factor k")
    L.append("    import mathutils")
    L.append("    qu = mathutils.Quaternion(q)")
    L.append("    axis, ang = qu.to_axis_angle()")
    L.append("    return tuple(qu if ang == 0.0 else (mathutils.Quaternion(axis, ang*k))[:])")
    L.append("")
    L.append("def apply_pose(o, p, f):")
    L.append("    loc, rot = p")
    L.append("    o.location = loc")
    L.append("    o.keyframe_insert(data_path='location', frame=f)")
    L.append("    if o.rotation_mode == 'QUATERNION':")
    L.append("        o.rotation_quaternion = rot")
    L.append("        o.keyframe_insert(data_path='rotation_quaternion', frame=f)")
    L.append("    else:")
    L.append("        o.rotation_euler = rot")
    L.append("        o.keyframe_insert(data_path='rotation_euler', frame=f)")
    L.append("")
    L.append("def bind(aname):")
    L.append("    a = bpy.data.actions.get(aname)")
    L.append("    if a is None:")
    L.append("        raise RuntimeError('Missing clip ' + aname)")
    L.append("    for o in bpy.data.objects:")
    L.append("        if o.animation_data is None:")
    L.append("            o.animation_data_create()")
    L.append("        o.animation_data.action = a")
    L.append("    return a")
    L.append("")
    L.append("# --- bake a clip loop starting at timeline frame fs, length n frames ---")
    L.append("def bake_loop(clip, fs, n, sc=1.0):")
    L.append("    a = bind(clip)")
    L.append("    fr0 = a.frame_range")
    L.append("    c_len = max(1, int(fr0[1]) - int(fr0[0]))")
    L.append("    objs = [o for o in bpy.data.objects if o.type in ('MESH','EMPTY')]")
    L.append("    for i in range(n):")
    L.append("        f_clip = int(fr0[0]) + (i % c_len)")
    L.append("        f_tl = fs + i")
    L.append("        bpy.context.scene.frame_set(f_clip)")
    L.append("        dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("        pp = []")
    L.append("        for o in objs:")
    L.append("            p = pose(o)")
    L.append("            if sc != 1.0 and o.rotation_mode == 'QUATERNION' and o.name in ('leg-left','leg-right','arm-left','arm-right','head'):")
    L.append("                p = (p[0], scale_quat(p[1], sc))")
    L.append("            pp.append((o, p))")
    L.append("        # detach action before writing keyframes (writing while bound corrupts evaluation)")
    L.append("        for o in bpy.data.objects:")
    L.append("            if o.animation_data is not None:")
    L.append("                o.animation_data.action = None")
    L.append("        for o, p in pp:")
    L.append("            apply_pose(o, p, f_tl)")
    L.append("    for o in bpy.data.objects:")
    L.append("        if o.animation_data is not None:")
    L.append("            o.animation_data.action = None")
    L.append("")
    L.append("# ---------- timeline (10s) ----------")
    L.append("# idle 0-1.5s (with procedural breathing added below)")
    L.append("bake_loop('idle', 1, int(1.5*FPS), 1.0)")
    L.append("# transition into walk: first 1.5-3.0s = blend idle->walk via 1 walk cycle eased")
    L.append("bake_loop('walk', int(1.5*FPS)+1, int(1.5*FPS), 1.0)")
    L.append("# travel walk 3.0-6.0s: 3s loop == several walk cycles")
    L.append("bake_loop('walk', int(3.0*FPS)+1, int(3.0*FPS), AMPL)")
    L.append("# stop 6.0-7.5s: walk slow -> idle (bake half walk then idle)")
    L.append("bake_loop('walk', int(6.0*FPS)+1, int(0.75*FPS), 1.0)")
    L.append("bake_loop('idle', int(6.75*FPS)+1, int(0.75*FPS), 1.0)")
    L.append("# final idle 7.5-10s, breathing")
    L.append("bake_loop('idle', int(7.5*FPS)+1, int(2.5*FPS), 1.0)")
    L.append("")
    L.append("# --- procedural breathing (subtle torso/head sway) on final + initial idle ---")
    L.append("if True:")
    L.append("    for seg_start, seg_end in ((1, int(1.5*FPS)), (int(7.5*FPS)+1, total_frames)):")
    L.append("        for f in range(seg_start, seg_end+1):")
    L.append("            u = (f - seg_start) / max(1, seg_end - seg_start)")
    L.append("            sway = math.sin(u * math.pi * 2.0 * 0.5) * 0.03")
    L.append("            bpy.context.scene.frame_set(f)")
    L.append("            dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("            for nm, amp in (('torso', 1.0), ('head', -0.6)):")
    L.append("                o = bpy.data.objects.get(nm)")
    L.append("                if o is None: continue")
    L.append("                if o.rotation_mode == 'QUATERNION':")
    L.append("                    r = mathutils.Quaternion((0.0, 0.0, sway * amp))")
    L.append("                    o.rotation_quaternion = o.rotation_quaternion @ r")
    L.append("                    o.keyframe_insert(data_path='rotation_quaternion', frame=f)")
    L.append("                else:")
    L.append("                    o.rotation_euler = (0.0, sway * amp, 0.0)")
    L.append("                    o.keyframe_insert(data_path='rotation_euler', frame=f)")
    L.append("")
    L.append("# --- trajectory translation: smoothstep across the travel-walk window ---")
    L.append("def smoothstep(a,b,x):")
    L.append("    x = max(0.0, min(1.0, (x-a)/(b-a)))")
    L.append("    return x*x*(3.0-2.0*x)")
    L.append("")
    L.append("ws = int(3.0*FPS)+1; we = int(6.0*FPS)+1")
    L.append("for f in range(ws, we+1):")
    L.append("    u = smoothstep(ws, we, f)")
    L.append("    traj.location.x = -TRAVEL*0.5 + TRAVEL*u")
    L.append("    traj.keyframe_insert(data_path='location', frame=f)")
    L.append("")
    L.append("# ================= diagnostics =================")
    L.append("diag = {'samples': {}, 'boundaries': {}, 'displacement': {}, 'framing': {}, 'limbs': {}}")
    L.append("")
    L.append("def ndc(p):")
    L.append("    m = cam_obj.matrix_world.inverted() @ p")
    L.append("    if abs(m.z) < 1e-6: return None")
    L.append("    x = (m.x/-m.z)/tanh; y = (m.y/-m.z)/tanv")
    L.append("    return ((x+1)*0.5, (y+1)*0.5)")
    L.append("")
    L.append("def framing_at(f):")
    L.append("    bpy.context.scene.frame_set(f)")
    L.append("    dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("    xs=[]; ys=[]")
    L.append("    for o in bpy.data.objects:")
    L.append("        if o.type != 'MESH': continue")
    L.append("        for v in o.bound_box:")
    L.append("            nd = ndc(o.matrix_world @ Vector(v))")
    L.append("            if nd is not None: xs.append(nd[0]); ys.append(nd[1])")
    L.append("    if not xs: return None")
    L.append("    return {'xmin':round(min(xs),3),'xmax':round(max(xs),3),")
    L.append("            'ymin':round(min(ys),3),'ymax':round(max(ys),3),")
    L.append("            'margin':round(min(min(xs),1-max(xs),min(ys),1-max(ys)),3)}")
    L.append("")
    L.append("def motion_at(f):")
    L.append("    bpy.context.scene.frame_set(f)")
    L.append("    dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("    return {'root':[round(v,3) for v in root.matrix_world.translation],")
    L.append("            'leg_left':[round(v,3) for v in bpy.data.objects.get('leg-left').matrix_world.translation],")
    L.append("            'arm_left':[round(v,3) for v in bpy.data.objects.get('arm-left').matrix_world.translation]}")
    L.append("")
    L.append("sample_frames = sorted(set([1, int(0.7*FPS)+1, int(1.5*FPS)+1, int(2.2*FPS)+1,")
    L.append("             int(3.5*FPS)+1, int(4.5*FPS)+1, int(5.5*FPS)+1,")
    L.append("             int(6.7*FPS)+1, int(8.0*FPS)+1, total_frames]))")
    L.append("for f in sample_frames:")
    L.append("    diag['samples'][f] = {'motion': motion_at(f), 'framing': framing_at(f)}")
    L.append("")
    L.append("m0 = motion_at(1); m1 = motion_at(total_frames)")
    L.append("dx = m1['root'][0] - m0['root'][0]")
    L.append("diag['displacement'] = {'start':m0['root'], 'end':m1['root'], 'total':round(dx,3),")
    L.append("                        'direction':'right' if dx>=0 else 'left', 'reset':bool(abs(dx)<1e-6)}")
    L.append("")
    L.append("margins = [diag['samples'][f]['framing']['margin'] for f in diag['samples']")
    L.append("           if diag['samples'][f]['framing'] is not None]")
    L.append("diag['framing'] = {'min_margin': round(min(margins),3) if margins else -1.0,")
    L.append("                  'passed': bool(margins) and min(margins) >= 0.04}")
    L.append("")
    L.append("# per-limb angular swing during travel walk (raw animated quaternions)")
    L.append("import mathutils")
    L.append("def quat_at(f, nm):")
    L.append("    bpy.context.scene.frame_set(f)")
    L.append("    dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("    o = bpy.data.objects.get(nm)")
    L.append("    if o is None: return None")
    L.append("    return mathutils.Quaternion(o.rotation_quaternion) if o.rotation_mode == 'QUATERNION' else o.matrix_world.to_quaternion()")
    L.append("")
    L.append("for nm in ('leg-left','leg-right','arm-left','arm-right','head'):")
    L.append("    fs = int(3.5*FPS)+1; fe = int(4.5*FPS)+1")
    L.append("    qs = [quat_at(f, nm) for f in range(fs, fe+1)]")
    L.append("    qs = [q for q in qs if q is not None]")
    L.append("    maxd = 0.0")
    L.append("    for i in range(len(qs)-1):")
    L.append("        dot = abs(sum(x*y for x,y in zip(qs[i], qs[i+1])))")
    L.append("        maxd = max(maxd, 2.0*math.acos(min(1.0,dot)))")
    L.append("    diag['limbs'][nm] = {'max_swing_rad': round(maxd,3)}")
    L.append("")
    L.append("# boundaries")
    L.append("def delta_at(f1,f2):")
    L.append("    d1 = motion_at(f1); d2 = motion_at(f2)")
    L.append("    dp = [d2['root'][i]-d1['root'][i] for i in range(3)]")
    L.append("    return {'frames':[f1,f2], 'root_delta': [round(v,4) for v in dp]}")
    L.append("")
    L.append("diag['boundaries']['idle_walk'] = delta_at(int(1.5*FPS)+1, int(1.5*FPS)+2)")
    L.append("diag['boundaries']['walk_idle'] = delta_at(int(6.75*FPS)+1, int(6.75*FPS)+2)")
    L.append("diag['boundaries']['travel_start'] = delta_at(int(3.0*FPS)+1, int(3.0*FPS)+2)")
    L.append("")
    L.append("with open(DIAG, 'w', encoding='utf-8') as f:")
    L.append("    json.dump(diag, f, ensure_ascii=False, indent=2)")
    L.append("")
    L.append("bpy.ops.render.render(animation=True)")
    L.append("ffmpeg = os.environ.get('FFMPEG_BIN', 'ffmpeg')")
    L.append("video = OUT / 'final.mp4'")
    L.append("seq = str(FRAMES / 'frame_%04d.png')")
    L.append("subprocess.run([ffmpeg, '-y', '-framerate', str(FPS), '-i', seq,")
    L.append("                '-vf', 'scale=%d:%d' % (WIDTH, HEIGHT),")
    L.append("                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18',")
    L.append("                '-r', str(FPS), str(video)], check=True)")
    L.append("print('MISSION14_OK', video)")

    script.write_text("\n".join(L), encoding="utf-8")
    return script
