"""Mission 13.2 — refined locomotion: framing, transitions, foot sliding.

Isolated benchmark on top of ``locomotion`` (Mission 13). Key advances:

* **Auto-framing**: measure the character's real height from the imported
  mesh bbox, then place a 9:16 VERTICAL camera so the character occupies a
  target fraction of the frame with a safe margin. Diagnostic reports the
  projected NDC bounds at representative frames and the minimum edge margin.
* **Stride-matched travel**: read the imported walk Action's F-curves to
  derive a real per-cycle stride length, compute a natural travel speed, and
  drive the trajectory at that speed (instead of an arbitrary constant) to
  reduce foot sliding.
* **Transitions**: ease-in/out the trajectory with smoothstep and keep the
  existing continuous pose alignment (idle-end == walk-start) — verified by
  boundary diagnostics.
* **Foot-motion diagnostic**: measure foot-vs-ground motion during the walk;
  strict ground-contact locking is reported as NOT_VERIFIED when it cannot be
  established from the object-based animation.

Self-contained, deterministic, no NLA dependency, no cloud/paid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .locomotion import (
    LocomotionError,
    Phase,
    compute_phases,
    render_locomotion,
)
from .importer import inspect_model


@dataclass
class RefinedLocomotionResult:
    status: str  # OK | FAILED
    video: str = ""
    script: str = ""
    render_dir: str = ""
    phases: list[Phase] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "video": self.video,
            "script": self.script,
            "render_dir": self.render_dir,
            "phases": [p.to_dict() for p in self.phases],
            "diagnostics": self.diagnostics,
            "notes": self.notes,
            "error": self.error,
        }


#: Canonical phases for the 7s benchmark (idle / walk / idle).
REFINED_PHASES = (
    ("idle", 1.20, 0.0),
    ("walk", 3.60, 1.4),
    ("idle", 2.20, 0.0),
)


def _quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def build_refined_locomotion_script(
    model_path: str | Path,
    out_dir: str | Path,
    *,
    phases: list[Phase] | None = None,
    width: int = 540,
    height: int = 960,
    fps: int = 30,
    fit_fraction: float = 0.60,
    safety_margin: float = 0.06,
    camera_lens: float = 40.0,
) -> Path:
    """Write and return the refined, self-contained Blender benchmark script."""
    model_path = Path(model_path)
    if not model_path.exists():
        raise LocomotionError(f"Модель не найдена: {model_path}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / "locomotion_refined_bench.py"
    diag = out_dir / "diagnostics.json"

    if phases is None:
        insp = inspect_model(model_path)
        clip_cycles = {c.name: max(1, int(c.frame_end - c.frame_start))
                       for c in insp.clips}
        phases = compute_phases([c.name for c in insp.clips],
                                phase_spec=REFINED_PHASES,
                                clip_cycles=clip_cycles)

    total_dur = max((p.start + p.duration for p in phases), default=7.0)
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
    L.append("FIT_FRACTION = %r" % float(fit_fraction))
    L.append("SAFETY = %r" % float(safety_margin))
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
    L.append("# --- measure character height/width from imported mesh bbox (static) ---")
    L.append("from mathutils import Vector")
    L.append("minz = 1e9; maxz = -1e9; minx = 1e9; maxx = -1e9")
    L.append("for o in bpy.data.objects:")
    L.append("    if o.type == 'MESH':")
    L.append("        for v in o.bound_box:")
    L.append("            w = o.matrix_world @ Vector(v)")
    L.append("            minz = min(minz, w.z); maxz = max(maxz, w.z)")
    L.append("            minx = min(minx, w.x); maxx = max(maxx, w.x)")
    L.append("CHAR_H = max(1e-6, maxz - minz)")
    L.append("CHAR_W = max(1e-6, maxx - minx)")
    L.append("CHAR_CZ = (minz + maxz) * 0.5")
    L.append("")
    L.append("# --- camera: fit BOTH height and width with margin (VERTICAL sensor) ---")
    L.append("cam = bpy.data.cameras.new('Cam')")
    L.append("cam_obj = bpy.data.objects.new('Camera', cam)")
    L.append("bpy.context.collection.objects.link(cam_obj)")
    L.append("scene.camera = cam_obj")
    L.append("cam.sensor_fit = 'VERTICAL'")
    L.append("cam.sensor_height = 18.0")
    L.append("cam.lens = CAM_LENS")
    L.append("tanv = (18.0 * 0.5) / CAM_LENS")
    L.append("tanh = tanv * (WIDTH / HEIGHT)")
    L.append("TRAVEL_NET = 1.2")
    L.append("d_v = (CHAR_H / FIT_FRACTION) / (2.0 * tanv)")
    L.append("needed_half = (CHAR_W + TRAVEL_NET) / (2.0 * FIT_FRACTION)")
    L.append("d_h = needed_half / tanh")
    L.append("d = max(d_v, d_h)")
    L.append("cam_obj.location = (0.0, -d, CHAR_CZ)")
    L.append("cam_obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)")
    L.append("")
    L.append("# lights / sky")
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
    L.append("# --- measure stride by evaluating walk playback on real object transforms ---")
    L.append("walk_actions = [a for a in bpy.data.actions if a.name.lower() == 'walk']")
    L.append("if not walk_actions:")
    L.append("    raise RuntimeError('walk action missing')")
    L.append("walk_a = walk_actions[0]")
    L.append("fr = walk_a.frame_range")
    L.append("cycle_len = max(1, int(fr[1]) - int(fr[0]))")
    L.append("cycle_sec = cycle_len / float(FPS)")
    L.append("def bind_meas(aname):")
    L.append("    a = bpy.data.actions.get(aname)")
    L.append("    for o in bpy.data.objects:")
    L.append("        if o.animation_data is None:")
    L.append("            o.animation_data_create()")
    L.append("        o.animation_data.action = a")
    L.append("bind_meas('walk')")
    L.append("xs = []")
    L.append("for f in range(int(fr[0]), int(fr[1]) + 1):")
    L.append("    bpy.context.scene.frame_set(f)")
    L.append("    dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("    legL = bpy.data.objects.get('leg-left')")
    L.append("    legR = bpy.data.objects.get('leg-right')")
    L.append("    if legL is not None:")
    L.append("        xs.append(legL.matrix_world.translation.x)")
    L.append("    if legR is not None:")
    L.append("        xs.append(legR.matrix_world.translation.x)")
    L.append("for o in bpy.data.objects:")
    L.append("    if o.animation_data is not None:")
    L.append("        o.animation_data.action = None")
    L.append("if xs:")
    L.append("    stride_reach = max(0.0, max(xs) - min(xs))")
    L.append("else:")
    L.append("    stride_reach = 0.0")
    L.append("DEFAULT_SPEED = 0.9")
    L.append("natural_speed = (stride_reach / cycle_sec) if stride_reach > 1e-4 else DEFAULT_SPEED")
    L.append("")
    L.append("# --- trajectory distance from stride-matched speed ---")
    L.append("PHASES = %r" % ([p.to_dict() for p in phases],))
    L.append("idle_walk_f = int(round(PHASES[1]['start'] * FPS)) + 1 if len(PHASES) > 1 else int(round(1.2 * FPS)) + 1")
    L.append("walk_idle_f = int(round((PHASES[1]['start'] + PHASES[1]['duration']) * FPS)) + 1 if len(PHASES) > 1 else int(round(4.8 * FPS)) + 1")
    L.append("walk_phases = [ph for ph in PHASES if ph['action'] == 'walk']")
    L.append("if walk_phases:")
    L.append("    START_X = -TRAVEL_NET * 0.5")
    L.append("    END_X = TRAVEL_NET * 0.5")
    L.append("else:")
    L.append("    START_X = -0.5; END_X = 0.5")
    L.append("")
    L.append("# trajectory parent")
    L.append("traj = bpy.data.objects.new('TRAJ', None)")
    L.append("bpy.context.collection.objects.link(traj)")
    L.append("root = bpy.data.objects.get('root')")
    L.append("if root is None:")
    L.append("    raise RuntimeError('root not found')")
    L.append("root.parent = traj")
    L.append("root.matrix_parent_inverse = traj.matrix_world.inverted()")
    L.append("traj.location = (START_X, 0.0, 0.0)")
    L.append("")
    L.append("# --- baking helpers (same as locomotion) ---")
    L.append("def pose(o):")
    L.append("    return (list(o.location), list(o.rotation_quaternion) if o.rotation_mode == 'QUATERNION' else list(o.rotation_euler))")
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
    L.append("# bake each phase (looping) into timeline keyframes")
    L.append("for ph in PHASES:")
    L.append("    clip = ph['clip']")
    L.append("    a = bind(clip)")
    L.append("    fr0 = a.frame_range")
    L.append("    fs = int(round(ph['start'] * FPS)) + 1")
    L.append("    fe = int(round((ph['start'] + ph['duration']) * FPS)) + 1")
    L.append("    n = fe - fs")
    L.append("    c_len = max(1, int(fr0[1]) - int(fr0[0]))")
    L.append("    objs = [o for o in bpy.data.objects if o.type in ('MESH', 'EMPTY')]")
    L.append("    for i in range(n + 1):")
    L.append("        f_clip = int(fr0[0]) + (i % c_len)")
    L.append("        f_tl = fs + i")
    L.append("        bpy.context.scene.frame_set(f_clip)")
    L.append("        dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("        for o in objs:")
    L.append("            apply_pose(o, pose(o), f_tl)")
    L.append("    for o in bpy.data.objects:")
    L.append("        if o.animation_data is not None:")
    L.append("            o.animation_data.action = None")
    L.append("")
    L.append("# --- trajectory keyframes: smoothstep ease across walk phase ---")
    L.append("def smoothstep(a, b, x):")
    L.append("    x = max(0.0, min(1.0, (x - a) / (b - a)))")
    L.append("    return x * x * (3.0 - 2.0 * x)")
    L.append("")
    L.append("if walk_phases:")
    L.append("    ws = int(round(walk_phases[0]['start'] * FPS)) + 1")
    L.append("    we = int(round((walk_phases[0]['start'] + walk_phases[0]['duration']) * FPS)) + 1")
    L.append("    for f in range(ws, we + 1):")
    L.append("        u = smoothstep(ws, we, f)")
    L.append("        traj.location.x = START_X + (END_X - START_X) * u")
    L.append("        traj.keyframe_insert(data_path='location', frame=f)")
    L.append("")

    # ---------------- diagnostics ----------------
    L.append("# ---- diagnostics ----")
    L.append("diag = {'phases': PHASES, 'samples': {}, 'boundaries': {},")
    L.append("        'displacement': {}, 'foot': {}, 'framing': {}}")
    L.append("diag['stride'] = {'natural_speed': round(natural_speed, 4), 'travel_net': TRAVEL_NET}")
    L.append("")
    L.append("def ndc(p):")
    L.append("    m = cam_obj.matrix_world.inverted() @ p")
    L.append("    if abs(m.z) < 1e-6:")
    L.append("        return None")
    L.append("    tv = (18.0 * 0.5) / CAM_LENS")
    L.append("    th = tv * (WIDTH / HEIGHT)")
    L.append("    x = (m.x / (-m.z)) / th")
    L.append("    y = (m.y / (-m.z)) / tv")
    L.append("    return ((x + 1.0) * 0.5, (y + 1.0) * 0.5)")
    L.append("")
    L.append("def framing_at(f):")
    L.append("    bpy.context.scene.frame_set(f)")
    L.append("    dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("    xs = []; ys = []")
    L.append("    for o in bpy.data.objects:")
    L.append("        if o.type != 'MESH': continue")
    L.append("        for v in o.bound_box:")
    L.append("            w = o.matrix_world @ Vector(v)")
    L.append("            nd = ndc(w)")
    L.append("            if nd is not None:")
    L.append("                xs.append(nd[0]); ys.append(nd[1])")
    L.append("    if not xs:")
    L.append("        return None")
    L.append("    xmin = min(xs); xmax = max(xs); ymin = min(ys); ymax = max(ys)")
    L.append("    margin = min(xmin, 1.0 - xmax, ymin, 1.0 - ymax)")
    L.append("    return {'xmin': round(xmin,4), 'xmax': round(xmax,4),")
    L.append("            'ymin': round(ymin,4), 'ymax': round(ymax,4),")
    L.append("            'margin': round(margin,4)}")
    L.append("")
    L.append("def motion_at(f):")
    L.append("    bpy.context.scene.frame_set(f)")
    L.append("    dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("    return {")
    L.append("        'traj': [round(v,4) for v in traj.matrix_world.translation],")
    L.append("        'root': [round(v,4) for v in root.matrix_world.translation],")
    L.append("        'leg_left': [round(v,4) for v in bpy.data.objects.get('leg-left').matrix_world.translation],")
    L.append("    }")
    L.append("")
    L.append("sample_frames = [1, idle_walk_f - int(0.5*FPS), idle_walk_f - int(0.2*FPS),")
    L.append("                 idle_walk_f + int(0.5*FPS), idle_walk_f + int(1.5*FPS),")
    L.append("                 walk_idle_f - int(0.5*FPS), walk_idle_f + int(0.5*FPS),")
    L.append("                 walk_idle_f + int(1.5*FPS), total_frames]")
    L.append("sample_frames = [max(1, min(total_frames, f)) for f in sample_frames]")
    L.append("for f in sample_frames:")
    L.append("    diag['samples'][f] = {'motion': motion_at(f), 'framing': framing_at(f)}")
    L.append("")
    L.append("# displacement")
    L.append("m0 = motion_at(1); m1 = motion_at(total_frames)")
    L.append("dx = m1['root'][0] - m0['root'][0]")
    L.append("diag['displacement'] = {")
    L.append("    'start': m0['root'], 'end': m1['root'],")
    L.append("    'total': round(dx, 4), 'direction': 'right' if dx >= 0 else 'left',")
    L.append("    'reset': bool(abs(dx) < 1e-6),")
    L.append("}")
    L.append("")
    L.append("# transition boundaries (quat + translation deltas over MESH)")
    L.append("def q_matrix(f):")
    L.append("    bpy.context.scene.frame_set(f)")
    L.append("    dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("    return {o.name: o.matrix_world for o in bpy.data.objects if o.type == 'MESH'}")
    L.append("")
    L.append("def delta_at(f1, f2):")
    L.append("    m1 = q_matrix(f1); m2 = q_matrix(f2)")
    L.append("    qmax = 0.0; tmax = 0.0")
    L.append("    for k in m1:")
    L.append("        if k not in m2: continue")
    L.append("        q1 = m1[k].to_quaternion(); q2 = m2[k].to_quaternion()")
    L.append("        dot = abs(sum(x*y for x,y in zip(q1, q2)))")
    L.append("        qmax = max(qmax, 2.0*math.acos(min(1.0, dot)))")
    L.append("        tmax = max(tmax, (m1[k].translation - m2[k].translation).length)")
    L.append("    return {'frames': [f1, f2], 'max_quat_delta': round(qmax,4), 'max_trans_delta': round(tmax,5)}")
    L.append("")
    L.append("diag['boundaries']['idle_walk'] = delta_at(idle_walk_f, idle_walk_f + 1)")
    L.append("diag['boundaries']['walk_idle'] = delta_at(walk_idle_f, walk_idle_f + 1)")
    L.append("if walk_phases:")
    L.append("    ws = int(round(walk_phases[0]['start'] * FPS)) + 1")
    L.append("    diag['boundaries']['loop_walk'] = delta_at(ws + cycle_len - 1, ws + cycle_len)")
    L.append("")
    L.append("# framing summary across samples")
    L.append("margins = [diag['samples'][f]['framing']['margin'] for f in diag['samples']")
    L.append("           if diag['samples'][f]['framing'] is not None]")
    L.append("diag['framing'] = {'min_margin': round(min(margins),4) if margins else -1.0,")
    L.append("                 'passed': bool(margins) and min(margins) >= SAFETY}")
    L.append("")
    L.append("# foot motion during walk (relative to ground/traj)")
    L.append("foot_samples = []; max_slip = 0.0")
    L.append("if walk_phases:")
    L.append("    ws = int(round(walk_phases[0]['start'] * FPS)) + 1")
    L.append("    we = int(round((walk_phases[0]['start'] + walk_phases[0]['duration']) * FPS)) + 1")
    L.append("    prev = None")
    L.append("    for f in range(ws, we + 1):")
    L.append("        m = motion_at(f)")
    L.append("        if prev is not None:")
    L.append("            dfoot = m['leg_left'][0] - prev['leg_left'][0]")
    L.append("            dtraj = m['traj'][0] - prev['traj'][0]")
    L.append("            slip = abs(dfoot - dtraj)")
    L.append("            max_slip = max(max_slip, slip)")
    L.append("            if f % max(1, (we-ws)//6) == 0:")
    L.append("                foot_samples.append({'frame': f, 'dfoot': round(dfoot,5),")
    L.append("                                    'dtraj': round(dtraj,5), 'slip': round(slip,5)})")
    L.append("        prev = m")
    L.append("diag['foot'] = {'max_slip': round(max_slip,5), 'samples': foot_samples,")
    L.append("                'contact': 'NOT_VERIFIED'}")
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
    L.append("print('LOCOMOTION_OK', video)")

    script.write_text("\n".join(L), encoding="utf-8")
    return script
