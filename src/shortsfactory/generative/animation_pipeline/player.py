"""Play imported animation clips on an imported model in Blender (isolated).

Mission 11 prototype: imports a GLB (etc.), plays a timeline of clips by
**baking** the imported Actions onto the scene timeline (deterministic,
time-offset, per-object).  Renders a vertical MP4 with EEVEE and writes a
diagnostic JSON that proves which clips actually moved which objects.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .clips_map import find_clip
from .importer import inspect_model


class AnimationPipelineError(RuntimeError):
    pass


@dataclass
class ScheduleItem:
    action: str
    clip: str
    start_seconds: float
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "clip": self.clip,
            "start_seconds": round(self.start_seconds, 3),
            "duration_seconds": round(self.duration_seconds, 3),
        }


@dataclass
class PlayResult:
    status: str  # OK | FAILED
    video: str = ""
    script: str = ""
    render_dir: str = ""
    schedule: list[ScheduleItem] = field(default_factory=list)
    motion_deltas: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "video": self.video,
            "script": self.script,
            "render_dir": self.render_dir,
            "schedule": [s.to_dict() for s in self.schedule],
            "motion_deltas": self.motion_deltas,
            "notes": self.notes,
            "error": self.error,
        }


def _quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def build_play_script(model_path: str | Path, schedule: list[ScheduleItem],
                      out_dir: str | Path, *, width: int = 540,
                      height: int = 960, fps: int = 30) -> Path:
    model_path = Path(model_path)
    if not model_path.exists():
        raise AnimationPipelineError(f"Модель не найдена: {model_path}")
    if not schedule:
        raise AnimationPipelineError("Пустое расписание клипов.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / "play_clips.py"
    diag = out_dir / "play_diag.json"

    clips = sorted({s.clip for s in schedule})
    segs = [(s.action, s.clip, s.start_seconds, s.duration_seconds) for s in schedule]

    L: list[str] = []
    L.append('import bpy, math, json, subprocess, os, sys')
    L.append('from pathlib import Path')
    L.append('')
    L.append('MODEL = r"%s"' % _quote(str(model_path)))
    L.append('OUT = Path(r"%s")' % _quote(str(out_dir.resolve())))
    L.append('FRAMES = OUT / "frames"')
    L.append('FRAMES.mkdir(parents=True, exist_ok=True)')
    L.append('FPS = %d' % int(fps))
    L.append('WIDTH = %d' % int(width))
    L.append('HEIGHT = %d' % int(height))
    L.append('DIAG = r"%s"' % _quote(str(diag.resolve())))
    L.append('')
    L.append('bpy.ops.wm.read_factory_settings(use_empty=True)')
    L.append('ext = MODEL.rsplit(".", 1)[-1].lower()')
    L.append('if ext in ("glb", "gltf"):')
    L.append('    bpy.ops.import_scene.gltf(filepath=MODEL)')
    L.append('elif ext == "fbx":')
    L.append('    bpy.ops.import_scene.fbx(filepath=MODEL)')
    L.append('elif ext == "obj":')
    L.append('    bpy.ops.wm.obj_import(filepath=MODEL)')
    L.append('else:')
    L.append('    raise RuntimeError("Unsupported format " + ext)')
    L.append('')
    L.append('scene = bpy.context.scene')
    L.append("scene.render.engine = ('BLENDER_EEVEE_NEXT' if hasattr(bpy.types, 'BLENDER_EEVEE_NEXT') else 'BLENDER_EEVEE')")
    L.append('scene.render.resolution_x = WIDTH')
    L.append('scene.render.resolution_y = HEIGHT')
    L.append("scene.render.image_settings.file_format = 'PNG'")
    L.append('scene.render.fps = FPS')
    L.append('scene.render.filepath = str(FRAMES / "frame_")')
    L.append('scene.frame_start = 1')
    L.append('')
    L.append('def pose(o):')
    L.append('    loc = o.location.copy()')
    L.append("    eul = list(o.rotation_euler) if o.rotation_mode == 'XYZ' else None")
    L.append("    quat = list(o.rotation_quaternion) if o.rotation_mode == 'QUATERNION' else None")
    L.append('    return loc, eul, quat')
    L.append('')
    L.append('def apply_pose(o, p, f):')
    L.append('    loc, eul, quat = p')
    L.append('    o.location = loc')
    L.append('    o.keyframe_insert(data_path="location", frame=f)')
    L.append('    if eul is not None:')
    L.append('        o.rotation_euler = eul')
    L.append('        o.keyframe_insert(data_path="rotation_euler", frame=f)')
    L.append('    if quat is not None:')
    L.append('        o.rotation_quaternion = quat')
    L.append('        o.keyframe_insert(data_path="rotation_quaternion", frame=f)')
    L.append('')
    L.append('def clip_moves(aname, obj):')
    L.append('    a = bpy.data.actions.get(aname)')
    L.append('    if a is None or obj is None:')
    L.append('        return False')
    L.append('    for o in bpy.data.objects:')
    L.append('        if o.animation_data is None:')
    L.append('            o.animation_data_create()')
    L.append('        o.animation_data.action = None')
    L.append('    if obj.animation_data is None:')
    L.append('        obj.animation_data_create()')
    L.append('    obj.animation_data.action = a')
    L.append('    fr = a.frame_range')
    L.append('    samples = []')
    L.append('    for f in (int(fr[0]), int(fr[0]) + 1, int(fr[0]) + max(1, (int(fr[1]) - int(fr[0])) // 2)):')
    L.append('        bpy.context.scene.frame_set(f)')
    L.append('        dg = bpy.context.evaluated_depsgraph_get(); dg.update()')
    L.append('        samples.append(pose(obj))')
    L.append('    d = 0.0')
    L.append('    for i in range(len(samples)):')
    L.append('        for j in range(i + 1, len(samples)):')
    L.append('            d = max(d, (samples[j][0] - samples[i][0]).length)')
    L.append('            for ci in range(1, 3):')
    L.append('                if samples[i][ci] is not None and samples[j][ci] is not None:')
    L.append('                    d = max(d, max(abs(samples[j][ci][k] - samples[i][ci][k]) for k in range(len(samples[i][ci]))))')
    L.append('    return d > 1e-4')
    L.append('')
    L.append('# precompute which objects each clip animates')
    L.append('CLIPS = %r' % (clips,))
    L.append('MOVES = {}')
    L.append('for aname in CLIPS:')
    L.append('    MOVES[aname] = []')
    L.append('    for o in bpy.data.objects:')
    L.append('        if clip_moves(aname, o):')
    L.append('            MOVES[aname].append(o.name)')
    L.append('')
    L.append('def bake(aname, fs, dur_sec):')
    L.append('    a = bpy.data.actions.get(aname)')
    L.append('    fr = a.frame_range')
    L.append('    n = int(round(dur_sec * FPS))')
    L.append('    objs = [o for o in bpy.data.objects if o.name in MOVES[aname]]')
    L.append('    for o in bpy.data.objects:')
    L.append('        if o.animation_data is not None:')
    L.append('            o.animation_data.action = None')
    L.append('    # bind only the moving objects for sampling')
    L.append('    for o in objs:')
    L.append('        if o.animation_data is None:')
    L.append('            o.animation_data_create()')
    L.append('        o.animation_data.action = a')
    L.append('    samples = {}')
    L.append('    for k in range(n + 1):')
    L.append('        bpy.context.scene.frame_set(int(fr[0]) + k)')
    L.append('        dg = bpy.context.evaluated_depsgraph_get(); dg.update()')
    L.append('        for o in objs:')
    L.append('            samples.setdefault(o.name, []).append((fs + k, pose(o)))')
    L.append('    for o in bpy.data.objects:')
    L.append('        if o.animation_data is not None:')
    L.append('            o.animation_data.action = None')
    L.append('    for name, pts in samples.items():')
    L.append('        o = bpy.data.objects.get(name)')
    L.append('        if o is None:')
    L.append('            continue')
    L.append('        for f, p in pts:')
    L.append('            apply_pose(o, p, f)')
    L.append('    return [o for o in objs]')
    L.append('')
    L.append('schedule = %r' % (segs,))
    L.append('total_dur = max((s[2] + s[3] for s in schedule), default=2.0)')
    L.append('scene.frame_end = max(1, int(round(total_dur * FPS)))')
    L.append('motion = []')
    L.append('for seg in schedule:')
    L.append('    fs = int(round(seg[2] * FPS)) + 1')
    L.append('    moved = bake(seg[1], fs, seg[3])')
    L.append('    motion.append({"clip": seg[1], "action": seg[0],')
    L.append('                   "animated_objects": [o.name for o in moved]})')
    L.append('')
    L.append('cam = bpy.data.cameras.new("Cam")')
    L.append('cam_obj = bpy.data.objects.new("Camera", cam)')
    L.append('bpy.context.collection.objects.link(cam_obj)')
    L.append('bpy.context.scene.camera = cam_obj')
    L.append('cam_obj.location = (0.0, -6.8, 1.9)')
    L.append('cam_obj.rotation_euler = (math.radians(88.0), 0.0, 0.0)')
    L.append('cam.sensor_fit = "VERTICAL"')
    L.append('cam.sensor_height = 18.0')
    L.append('cam.lens = 35.0')
    L.append('')
    L.append('sun = bpy.data.lights.new("Sun", "SUN")')
    L.append('sun_obj = bpy.data.objects.new("Sun", sun)')
    L.append('bpy.context.collection.objects.link(sun_obj)')
    L.append('sun_obj.rotation_euler = (0.9, 0.2, 0.6)')
    L.append('sun_obj.data.energy = 3.5')
    L.append('bpy.context.scene.world = bpy.data.worlds.new("Twilight")')
    L.append('w = bpy.context.scene.world')
    L.append('w.use_nodes = True')
    L.append('bg = w.node_tree.nodes.get("Background")')
    L.append('if bg:')
    L.append('    bg.inputs[0].default_value = (0.45, 0.7, 1.0, 1.0)')
    L.append('    bg.inputs[1].default_value = 1.0')
    L.append('')
    L.append('bpy.ops.render.render(animation=True)')
    L.append('ffmpeg = os.environ.get("FFMPEG_BIN", "ffmpeg")')
    L.append('video = OUT / "final.mp4"')
    L.append('seq = str(FRAMES / "frame_%04d.png")')
    L.append('subprocess.run([ffmpeg, "-y", "-framerate", str(FPS), "-i", seq,')
    L.append('                "-vf", "scale=%d:%d" % (WIDTH, HEIGHT),')
    L.append('                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",')
    L.append('                "-r", str(FPS), str(video)], check=True)')
    L.append('with open(DIAG, "w", encoding="utf-8") as f:')
    L.append('    json.dump({"schedule": schedule, "motion_deltas": motion}, f,')
    L.append('              ensure_ascii=False, indent=2)')
    L.append('print("PLAY_OK", video)')

    script.write_text("\n".join(L), encoding="utf-8")
    return script


def build_schedule(model_path: str | Path, actions: list[str],
                   clip_durations: dict[str, float] | None = None,
                   segment_length: float = 1.2, start: float = 0.0) -> list[ScheduleItem]:
    """Map canonical actions to available clips and lay them out sequentially."""
    insp = inspect_model(model_path)
    names = [c.name for c in insp.clips]
    schedule: list[ScheduleItem] = []
    t = start
    for action in actions:
        clip = find_clip(names, action)
        if clip is None:
            continue
        dur = clip_durations.get(action, segment_length) if clip_durations else segment_length
        meta = next((c for c in insp.clips if c.name == clip), None)
        if meta is not None:
            dur = min(dur, max(0.4, meta.duration_seconds))
        schedule.append(ScheduleItem(action=action, clip=clip,
                                     start_seconds=t, duration_seconds=dur))
        t += dur
    return schedule


def render_play_script(script: Path, *, blender_exe: str | None = None,
                       timeout: int = 1800) -> PlayResult:
    from ..blender_adapter import blender_executable

    exe = blender_exe or blender_executable()
    if not exe:
        return PlayResult(status="FAILED", error="Blender не найден.")
    out_dir = script.parent
    try:
        proc = subprocess.run([exe, "--background", "--python", str(script)],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return PlayResult(status="FAILED", script=str(script),
                          render_dir=str(out_dir), error="timeout")
    video = out_dir / "final.mp4"
    if proc.returncode != 0 or not video.exists():
        return PlayResult(status="FAILED", script=str(script),
                          render_dir=str(out_dir),
                          error=(proc.stderr or "")[-800:])
    diag = out_dir / "play_diag.json"
    motion = []
    if diag.exists():
        try:
            data = json.loads(diag.read_text(encoding="utf-8"))
            motion = data.get("motion_deltas", [])
        except Exception:
            motion = []
    notes = []
    if motion:
        moved_total = sum(len(m.get("animated_objects", [])) for m in motion)
        if moved_total == 0:
            notes.append("ВНИМАНИЕ: клипы не сдвинули ни один объект (проверьте импорт).")
        else:
            notes.append(f"Клипы сдвинули объекты: {moved_total} записей.")
    return PlayResult(status="OK", video=video.as_posix(), script=str(script),
                      render_dir=str(out_dir), motion_deltas=motion, notes=notes)
