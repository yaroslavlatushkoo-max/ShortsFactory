"""Locomotion & smooth-transition benchmark for imported clips (isolated).

Mission 13: prove that a real imported character (Kenney Blocky Characters
2.0, CC0) can walk through a scene with seamless idle<->walk transitions and
actual displacement — using the *real* walk action, a parent trajectory
empty for locomotion, and baked segment keyframes (deterministic, no NLA).

This is an isolated prototype; it does not touch the production voxel engine.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .clips_map import find_clip
from .importer import inspect_model


class LocomotionError(RuntimeError):
    pass


@dataclass
class Phase:
    action: str          # canonical action: idle | walk
    clip: str            # actual imported clip name
    start: float         # seconds
    duration: float      # seconds
    displacement: float = 0.0  # world units for TRAJ during this phase

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "clip": self.clip,
            "start": round(self.start, 3),
            "duration": round(self.duration, 3),
            "displacement": round(self.displacement, 4),
        }


@dataclass
class LocomotionResult:
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


DEFAULT_PHASES = (
    ("idle", 0.80, 0.0),       # stand at start
    ("walk", 3.20, 1.4),       # walk + travel (3.2s = 6 full cycles of 0.53s)
    ("idle", 1.00, 0.0),       # stop & idle at destination
)


def compute_phases(clip_names: list[str], phase_spec=DEFAULT_PHASES,
                   fps: int = 30, clip_cycles: dict[str, int] | None = None) -> list[Phase]:
    """Build deterministic phase timeline from canonical specs.

    Durations in the spec are *maximums*; walk is clamped to whole clip cycles.
    ``clip_cycles`` maps clip name -> frames per cycle (from inspection); walk
    defaults to 16 frames when unknown.
    """
    clip_cycles = clip_cycles or {}
    idle_clip = find_clip(clip_names, "idle")
    walk_clip = find_clip(clip_names, "walk")
    if idle_clip is None or walk_clip is None:
        raise LocomotionError("Не найдены клипы idle/walk в модели.")
    phases: list[Phase] = []
    t = 0.0
    for action, dur, disp in phase_spec:
        clip = idle_clip if action == "idle" else walk_clip
        if action == "walk":
            cycle_frames = max(1, int(clip_cycles.get(walk_clip, 16)))
            cycles = max(1, int(round(dur * fps / cycle_frames)))
            dur = cycles * cycle_frames / fps
        phases.append(Phase(action=action, clip=clip, start=t,
                            duration=dur, displacement=disp))
        t += dur
    return phases


def _quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def build_locomotion_script(
    model_path: str | Path,
    out_dir: str | Path,
    *,
    phases: list[Phase] | None = None,
    width: int = 540,
    height: int = 960,
    fps: int = 30,
    start_x: float = -0.7,
    end_x: float = 0.7,
    camera_distance: float = 6.8,
) -> Path:
    """Write and return the self-contained Blender benchmark script."""
    model_path = Path(model_path)
    if not model_path.exists():
        raise LocomotionError(f"Модель не найдена: {model_path}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / "locomotion_bench.py"
    diag = out_dir / "diagnostics.json"

    if phases is None:
        insp = inspect_model(model_path)
        clip_cycles = {c.name: max(1, int(c.frame_end - c.frame_start))
                       for c in insp.clips}
        phases = compute_phases([c.name for c in insp.clips],
                                clip_cycles=clip_cycles)

    total_dur = max((p.start + p.duration for p in phases), default=5.0)
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
    L.append("START_X = %r" % float(start_x))
    L.append("END_X = %r" % float(end_x))
    L.append("CAM_DIST = %r" % float(camera_distance))
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
    L.append("# camera (static portrait 9:16)")
    L.append("cam = bpy.data.cameras.new('Cam')")
    L.append("cam_obj = bpy.data.objects.new('Camera', cam)")
    L.append("bpy.context.collection.objects.link(cam_obj)")
    L.append("scene.camera = cam_obj")
    L.append("cam_obj.location = (0.0, -CAM_DIST, 1.9)")
    L.append("cam_obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)")
    L.append("cam.sensor_fit = 'VERTICAL'")
    L.append("cam.sensor_height = 18.0")
    L.append("cam.lens = 35.0")
    L.append("")
    L.append("# lights")
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
    L.append("# trajectory parent: moves the whole character (locomotion)")
    L.append("traj = bpy.data.objects.new('TRAJ', None)")
    L.append("bpy.context.collection.objects.link(traj)")
    L.append("root = bpy.data.objects.get('root')")
    L.append("if root is None:")
    L.append("    raise RuntimeError('Импортированный корень root не найден.')")
    L.append("root.parent = traj")
    L.append("root.matrix_parent_inverse = traj.matrix_world.inverted()")
    L.append("traj.location = (START_X, 0.0, 0.0)")
    L.append("")
    L.append("# --- baking helpers ---")
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
    L.append("# --- bake each phase into timeline keyframes ---")
    L.append("PHASES = %r" % ([p.to_dict() for p in phases],))
    L.append("for ph in PHASES:")
    L.append("    clip = ph['clip']")
    L.append("    a = bind(clip)")
    L.append("    fr = a.frame_range")
    L.append("    fs = int(round(ph['start'] * FPS)) + 1")
    L.append("    fe = int(round((ph['start'] + ph['duration']) * FPS)) + 1")
    L.append("    n = fe - fs")
    L.append("    cycle_len = max(1, int(fr[1]) - int(fr[0]))")
    L.append("    objs = [o for o in bpy.data.objects if o.type in ('MESH', 'EMPTY')]")
    L.append("    for i in range(n + 1):")
    L.append("        f_clip = int(fr[0]) + (i % cycle_len)")
    L.append("        f_tl = fs + i")
    L.append("        bpy.context.scene.frame_set(f_clip)")
    L.append("        dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("        for o in objs:")
    L.append("            apply_pose(o, pose(o), f_tl)")
    L.append("    for o in bpy.data.objects:")
    L.append("        if o.animation_data is not None:")
    L.append("            o.animation_data.action = None")
    L.append("")
    L.append("# --- trajectory keyframes (smoothstep ease over walk phases) ---")
    L.append("def smoothstep(a, b, x):")
    L.append("    x = max(0.0, min(1.0, (x - a) / (b - a)))")
    L.append("    return x * x * (3.0 - 2.0 * x)")
    L.append("")
    L.append("walk_phases = [ph for ph in PHASES if ph['action'] == 'walk']")
    L.append("if walk_phases:")
    L.append("    ws = int(round(walk_phases[0]['start'] * FPS)) + 1")
    L.append("    we = int(round((walk_phases[0]['start'] + walk_phases[0]['duration']) * FPS)) + 1")
    L.append("    for f in range(ws, we + 1):")
    L.append("        u = smoothstep(ws, we, f)")
    L.append("        traj.location.x = START_X + (END_X - START_X) * u")
    L.append("        traj.keyframe_insert(data_path='location', frame=f)")
    L.append("")
    L.append("# --- diagnostics snapshot (post-bake) ---")
    L.append("diag = {'phases': PHASES, 'samples': {}, 'boundaries': {}}")
    L.append("for f in (1, int(0.8*FPS)+1, int(0.8*FPS)+2, int(4.0*FPS)+1, int(4.0*FPS)+2, total_frames):")
    L.append("    bpy.context.scene.frame_set(f)")
    L.append("    dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("    diag['samples'][f] = {")
    L.append("        'traj': [round(v, 4) for v in traj.matrix_world.translation],")
    L.append("        'root': [round(v, 4) for v in root.matrix_world.translation],")
    L.append("        'head': [round(v, 4) for v in bpy.data.objects.get('head').matrix_world.translation],")
    L.append("        'leg_left': [round(v, 4) for v in bpy.data.objects.get('leg-left').matrix_world.translation],")
    L.append("    }")
    L.append("for lbl, f1, f2 in (('idle_walk', int(0.8*FPS)+1, int(0.8*FPS)+2),")
    L.append("                      ('walk_idle', int(4.0*FPS)+1, int(4.0*FPS)+2)):")
    L.append("    def mat(f):")
    L.append("        bpy.context.scene.frame_set(f)")
    L.append("        dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("        return {o.name: list(o.matrix_world.to_quaternion()) for o in bpy.data.objects if o.type == 'MESH'}")
    L.append("    q1, q2 = mat(f1), mat(f2)")
    L.append("    dmax = 0.0")
    L.append("    for k in q1:")
    L.append("        if k in q2:")
    L.append("            dot = abs(sum(x*y for x, y in zip(q1[k], q2[k])))")
    L.append("            dmax = max(dmax, 2.0 * math.acos(min(1.0, dot)))")
    L.append("    diag['boundaries'][lbl] = {'frames': [f1, f2], 'max_quat_delta': round(dmax, 4)}")
    L.append("# walk loop boundary (clip wraps every cycle_len frames)")
    L.append("if walk_phases:")
    L.append("    ws = int(round(walk_phases[0]['start'] * FPS)) + 1")
    L.append("    cyc = int(round(walk_phases[0].get('cycle_frames', 16)))")
    L.append("    fA = ws + cyc - 1")
    L.append("    fB = ws + cyc")
    L.append("    q1 = mat(fA); q2 = mat(fB)")
    L.append("    dmax = 0.0")
    L.append("    for k in q1:")
    L.append("        if k in q2:")
    L.append("            dot = abs(sum(x*y for x, y in zip(q1[k], q2[k])))")
    L.append("            dmax = max(dmax, 2.0 * math.acos(min(1.0, dot)))")
    L.append("    diag['boundaries']['loop_walk'] = {'frames': [fA, fB], 'max_quat_delta': round(dmax, 4)}")
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


def render_locomotion(script: Path, *, blender_exe: str | None = None,
                      timeout: int = 3600) -> LocomotionResult:
    """Run the benchmark script in background Blender and collect results."""
    from ..blender_adapter import blender_executable

    exe = blender_exe or blender_executable()
    if not exe:
        return LocomotionResult(status="FAILED", error="Blender не найден.")
    out_dir = script.parent
    try:
        proc = subprocess.run([exe, "--background", "--python", str(script)],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return LocomotionResult(status="FAILED", script=str(script),
                                render_dir=str(out_dir), error="timeout")
    video = out_dir / "final.mp4"
    if proc.returncode != 0 or not video.exists():
        return LocomotionResult(status="FAILED", script=str(script),
                                render_dir=str(out_dir),
                                error=(proc.stderr or "")[-900:])
    diag: dict[str, Any] = {}
    diag_path = out_dir / "diagnostics.json"
    if diag_path.exists():
        try:
            diag = json.loads(diag_path.read_text(encoding="utf-8"))
        except Exception:
            diag = {}
    return LocomotionResult(status="OK", video=video.as_posix(),
                            script=str(script), render_dir=str(out_dir),
                            diagnostics=diag)
