"""Mission 15 — full-body rig control on a real skeletal model (isolated).

This module works with the CC-BY-4.0 ``CesiumMan`` glTF sample (Khronos
glTF-Sample-Assets) — a real Armature with 20 poseable bones: 3 spine
segments, 2 neck segments (head is a mesh parented to the last neck joint),
3 joints per arm (shoulder/upper/forearm) and 4 per leg
(hip/knee/ankle/foot).  No hard-coded bone names are required: groups are
*detected* from the actual bone names at runtime.

Two isolated benchmark scenarios are produced:

* ``joint``   — a stationary-camera demonstration of independent joint control
  (head/neck → spine → shoulders → elbows → pelvis → knees/feet → whole body).
* ``fullbody`` — a ~11 s scene: idle breathing + weight shift → notice &
  look → sequential body turn → expressive arm gesture → real walk clip loop
  with root travel → smooth stop → final pose looking at the camera.

Animation sources: procedural FK controllers for pose phases + the model's own
walk clip (``Anim_0``) for locomotion, blended with short in/out transitions.

The generated Blender script is self-contained and deterministic (no NLA,
no network, no cloud/paid).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .locomotion import LocomotionError, render_locomotion


@dataclass
class FullBodyResult:
    status: str  # OK | FAILED
    video: str = ""
    script: str = ""
    render_dir: str = ""
    scenario: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "video": self.video,
            "script": self.script,
            "render_dir": self.render_dir,
            "scenario": self.scenario,
            "diagnostics": self.diagnostics,
            "notes": self.notes,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Host-side pure helpers (unit-tested without Blender)
# ---------------------------------------------------------------------------

def detect_control_groups(bone_names: list[str]) -> dict[str, list[str]]:
    """Map actual bone names onto semantic control groups (pattern-based).

    Recognises the CesiumMan naming and generic conventions:
    spine/torso, neck, head, arm/leg left/right (ordered parent→child).
    Unknown bones are ignored (never fabricate control).
    """
    import re as _re

    groups: dict[str, list[str]] = {
        "spine": [], "neck": [], "head": [],
        "arm_l": [], "arm_r": [], "leg_l": [], "leg_r": [],
    }

    def _side(name: str) -> str | None:
        tokens = [t for t in _re.split(r"[_\W]+", name.lower()) if t]
        if "l" in tokens or "left" in tokens:
            return "L"
        if "r" in tokens or "right" in tokens:
            return "R"
        return None

    def _ordered(sublist):
        def score(nm):
            s = nm.lower()
            v = 0
            if "torso" in s or "spine" in s:
                v += 100
            if "neck" in s:
                v += 200
            if "arm" in s:
                v += 300
            if "leg" in s:
                v += 400
            digits = _re.findall(r"\d+", s)
            v += sum(int(x) for x in digits[-2:])
            return v
        return sorted(sublist, key=score)

    for n in bone_names:
        low = n.lower()
        if "torso" in low or "spine" in low or "chest" in low:
            groups["spine"].append(n)
        elif "neck" in low:
            groups["neck"].append(n)
        elif low.startswith("head") or low == "head":
            groups["head"].append(n)
        elif "arm" in low:
            side = _side(n)
            if side == "L":
                groups["arm_l"].append(n)
            elif side == "R":
                groups["arm_r"].append(n)
        elif "leg" in low:
            side = _side(n)
            if side == "L":
                groups["leg_l"].append(n)
            elif side == "R":
                groups["leg_r"].append(n)
    for k in groups:
        groups[k] = _ordered(groups[k])
    return groups


def _quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# ---------------------------------------------------------------------------
# Script builder
# ---------------------------------------------------------------------------

_PROBE_BONES = """# --- bone detection (pattern-based, no hardcoded names) ---
def parent_chain(name):
    chain = []
    for other in bpy.data.objects:
        pass
    return chain

def detect_groups():
    names = [pb.name for pb in arm.pose.bones]
    groups = {'spine': [], 'neck': [], 'head': [],
              'arm_l': [], 'arm_r': [], 'leg_l': [], 'leg_r': []}
    def side(nm):
        toks = [t for t in re.split(r'[_\\W]+', nm.lower()) if t]
        if 'l' in toks or 'left' in toks: return 'L'
        if 'r' in toks or 'right' in toks: return 'R'
        return None
    for n in names:
        low = n.lower()
        if 'torso' in low or 'spine' in low or 'chest' in low:
            groups['spine'].append(n)
        elif 'neck' in low:
            groups['neck'].append(n)
        elif low.startswith('head') or low == 'head':
            groups['head'].append(n)
        elif 'arm' in low:
            s = side(n)
            if s == 'L': groups['arm_l'].append(n)
            elif s == 'R': groups['arm_r'].append(n)
        elif 'leg' in low:
            s = side(n)
            if s == 'L': groups['leg_l'].append(n)
            elif s == 'R': groups['leg_r'].append(n)
    def bone_depth(nm, cache=None):
        if cache is None: cache = {}
        if nm in cache: return cache[nm]
        b = arm.data.bones.get(nm)
        if b is None or b.parent is None:
            cache[nm] = 0
        else:
            cache[nm] = bone_depth(b.parent.name, cache) + 1
        return cache[nm]
    for k in groups:
        groups[k] = sorted(groups[k], key=lambda n: bone_depth(n))
    return groups
"""

_SKEL_HELPERS = """# --- pose helpers ---
def set_bone_rot(pb, axis, ang, f):
    if pb.rotation_mode == 'QUATERNION':
        from mathutils import Quaternion, Euler
        e = Euler((0.0, 0.0, 0.0), 'XYZ')
        setattr(e, axis, ang)
        pb.rotation_quaternion = e.to_quaternion()
        pb.keyframe_insert(data_path='rotation_quaternion', frame=f)
    else:
        setattr(pb.rotation_euler, axis, ang)
        pb.keyframe_insert(data_path='rotation_euler', frame=f)

def bone_euler(pb):
    if pb.rotation_mode == 'QUATERNION':
        from mathutils import Euler
        return pb.rotation_quaternion.to_euler('XYZ')
    return pb.rotation_euler.copy()

def q_slerp(q1, q2, u):
    from mathutils import Quaternion
    q1 = Quaternion(q1); q2 = Quaternion(q2)
    return q1.slerp(q2, max(0.0, min(1.0, u)))

def neutral_pose(f):
    for pb in arm.pose.bones:
        if pb.rotation_mode == 'QUATERNION':
            pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            pb.keyframe_insert(data_path='rotation_quaternion', frame=f)
        else:
            pb.rotation_euler = (0.0, 0.0, 0.0)
            pb.keyframe_insert(data_path='rotation_euler', frame=f)

def ease(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3.0 - 2.0 * u)

def window(f, t0, t1, fade=0.12):
    if f < t0 or f > t1:
        return 0.0
    if t1 - t0 <= 1e-6:
        return 1.0
    u = (f - t0) / (t1 - t0)
    du = max(fade, 1e-3)
    amp = ease(min(1.0, u / du))
    amp *= ease(min(1.0, (1.0 - u) / du))
    return amp
"""

def _build_scenario_lines(scenario: str, fps: int) -> list[str]:
    """Return script lines implementing the requested scenario."""
    L: list[str] = []
    if scenario == "joint":
        # ---- Test A: joint demonstration (stationary camera, ~10s) ----
        L += [
            "TOTAL = 10.0",
            "scene.frame_end = int(round(TOTAL * FPS))",
            "neutral_pose(1)",
            "# 1) head/neck",
            "for f in range(int(1.0*FPS), int(2.0*FPS)+1):",
            "    u = ease((f - 1.0*FPS)/(1.0*FPS))",
            "    set_bone_rot(pb('neck', -1), 'z', 0.9*u, f)   # turn",
            "    set_bone_rot(pb('neck', -1), 'x', 0.25*u, f)   # tilt",
            "for f in range(int(2.0*FPS), int(3.0*FPS)+1):",
            "    u = ease((f - 2.0*FPS)/(1.0*FPS))",
            "    set_bone_rot(pb('neck', -1), 'y', 0.35*u, f)",
            "    set_bone_rot(pb('neck', -2), 'x', -0.3*u, f)",
            "# 2) spine sway",
            "for f in range(int(3.0*FPS), int(4.0*FPS)+1):",
            "    u = ease((f - 3.0*FPS)/(1.0*FPS))",
            "    set_bone_rot(pb('spine', -1), 'x', 0.12*u, f)",
            "    set_bone_rot(pb('spine', -2), 'x', 0.18*u, f)",
            "    set_bone_rot(pb('spine', -3), 'y', 0.1*u, f)",
            "# 3) shoulders raise/rotate",
            "for f in range(int(4.0*FPS), int(5.0*FPS)+1):",
            "    u = ease((f - 4.0*FPS)/(1.0*FPS))",
            "    set_bone_rot(pb('arm_l', 0), 'z', -0.5*u, f)",
            "    set_bone_rot(pb('arm_r', 0), 'z', 0.5*u, f)",
            "    set_bone_rot(pb('arm_l', 0), 'x', -0.4*u, f)",
            "    set_bone_rot(pb('arm_r', 0), 'x', 0.4*u, f)",
            "# 4) elbows + forearm",
            "for f in range(int(5.0*FPS), int(6.0*FPS)+1):",
            "    u = ease((f - 5.0*FPS)/(1.0*FPS))",
            "    set_bone_rot(pb('arm_l', 1), 'z', 1.1*u, f)",
            "    set_bone_rot(pb('arm_r', 1), 'z', -1.1*u, f)",
            "    set_bone_rot(pb('arm_l', 2), 'x', -0.7*u, f)",
            "    set_bone_rot(pb('arm_r', 2), 'x', 0.7*u, f)",
            "# 5) pelvis",
            "for f in range(int(6.0*FPS), int(7.0*FPS)+1):",
            "    u = ease((f - 6.0*FPS)/(1.0*FPS))",
            "    set_bone_rot(pb('spine', 0), 'x', -0.2*u, f)",
            "    set_bone_rot(pb('spine', 0), 'z', 0.3*u, f)",
            "# 6) knees/feet",
            "for f in range(int(7.0*FPS), int(8.0*FPS)+1):",
            "    u = ease((f - 7.0*FPS)/(1.0*FPS))",
            "    set_bone_rot(pb('leg_l', 1), 'z', 0.9*u, f)",
            "    set_bone_rot(pb('leg_r', 1), 'z', -0.9*u, f)",
            "    set_bone_rot(pb('leg_l', 2), 'x', -0.25*u, f)",
            "    set_bone_rot(pb('leg_r', 2), 'x', 0.25*u, f)",
            "    set_bone_rot(pb('leg_l', 3), 'x', 0.2*u, f)",
            "    set_bone_rot(pb('leg_r', 3), 'x', -0.2*u, f)",
            "# 7) coordinated whole-body wave",
            "for f in range(int(8.0*FPS), int(9.0*FPS)+1):",
            "    u = ease((f - 8.0*FPS)/(1.0*FPS))",
            "    s = math.sin(u * math.pi * 2.0) * 0.6",
            "    set_bone_rot(pb('spine', -1), 'z', 0.5*s, f)",
            "    set_bone_rot(pb('arm_l', 0), 'x', -0.6*s, f)",
            "    set_bone_rot(pb('arm_r', 0), 'x', 0.6*s, f)",
            "    set_bone_rot(pb('leg_l', 1), 'z', 0.5*s, f)",
            "    set_bone_rot(pb('leg_r', 1), 'z', -0.5*s, f)",
            "    set_bone_rot(pb('neck', -1), 'y', 0.2*s, f)",
            "# settle",
            "for f in range(int(9.0*FPS), int(10.0*FPS)+1):",
            "    u = 1.0 - ease((f - 9.0*FPS)/(1.0*FPS))",
            "    neutral_pose(f)",
        ]
        L.append("RENDER_SEQ = True")
        return L

    # -------------------- Test B: full-body scene --------------------
    L += [
        "TOTAL = 11.0",
        "scene.frame_end = int(round(TOTAL * FPS))",
        "neutral_pose(1)",
        "# 0-1.5s idle breathing + weight shift",
        "for f in range(1, int(1.5*FPS)+1):",
        "    u = (f - 1)/(1.5*FPS)",
        "    br = math.sin(u * math.pi * 2.0 * 0.5)",
        "    set_bone_rot(pb('spine', 0), 'x', -0.08*br, f)",
        "    set_bone_rot(pb('spine', 1), 'x', 0.06*br, f)",
        "    set_bone_rot(pb('neck', -1), 'y', 0.04*br, f)",
        "    set_bone_rot(pb('neck', -2), 'y', -0.03*br, f)",
        "    set_bone_rot(pb('arm_l', 0), 'x', 0.05*br, f)",
        "    set_bone_rot(pb('arm_r', 0), 'x', -0.05*br, f)",
        "# 1.5-2.5s notice object: head turn",
        "for f in range(int(1.5*FPS), int(2.5*FPS)+1):",
        "    u = ease((f - 1.5*FPS)/(1.0*FPS))",
        "    set_bone_rot(pb('neck', -1), 'z', 0.8*u, f)",
        "    set_bone_rot(pb('neck', -2), 'z', 0.4*u, f)",
        "# 2.5-3.75s sequential body turn (pelvis→spine→neck→head)",
        "for f in range(int(2.5*FPS), int(3.75*FPS)+1):",
        "    u = ease((f - 2.5*FPS)/(1.25*FPS))",
        "    set_bone_rot(pb('spine', 0), 'z', 0.35*u, f)",
        "    set_bone_rot(pb('spine', 1), 'z', 0.3*u, f)",
        "    set_bone_rot(pb('spine', 2), 'z', 0.25*u, f)",
        "    set_bone_rot(pb('neck', -1), 'z', 0.6*u, f)",
        "    set_bone_rot(pb('neck', -2), 'z', 0.3*u, f)",
        "# 3.75-5.0s expressive gesture + look at object",
        "for f in range(int(3.75*FPS), int(5.0*FPS)+1):",
        "    u = ease((f - 3.75*FPS)/(1.25*FPS))",
        "    set_bone_rot(pb('arm_r', 0), 'x', -1.2*u, f)",
        "    set_bone_rot(pb('arm_r', 1), 'z', 1.0*u, f)",
        "    set_bone_rot(pb('arm_r', 2), 'x', -0.5*u, f)",
        "    set_bone_rot(pb('arm_l', 0), 'x', -0.5*u, f)",
        "    set_bone_rot(pb('neck', -1), 'z', 0.9*u, f)",
        "    set_bone_rot(pb('neck', -2), 'y', -0.15*u, f)",
        "# 5.0-6.5s walk transition: blend to clip frame 0",
        "for f in range(int(5.0*FPS), int(6.5*FPS)+1):",
        "    u = ease((f - 5.0*FPS)/(1.5*FPS))",
        "    set_bone_rot(pb('spine', 0), 'z', 0.35*(1-u), f)",
        "    set_bone_rot(pb('neck', -1), 'z', 0.9*(1-u), f)",
        "    set_bone_rot(pb('arm_r', 0), 'x', -1.2*(1-u), f)",
        "    set_bone_rot(pb('arm_r', 1), 'z', 1.0*(1-u), f)",
        "# 6.5-9.0s real walk loop (Anim_0) with TRAJ travel",
        "# (baked separately below by bake_clip_loop)",
        "# 9.0-10.0s stop transition back to neutral",
        "for f in range(int(9.0*FPS), int(10.0*FPS)+1):",
        "    u = ease((f - 9.0*FPS)/(1.0*FPS))",
        "    neutral_pose(f)",
        "# 10.0-11.0s final pose looking at camera",
        "for f in range(int(10.0*FPS), int(11.0*FPS)+1):",
        "    u = ease((f - 10.0*FPS)/(1.0*FPS))",
        "    set_bone_rot(pb('neck', -1), 'z', -0.6*u, f)",
        "    set_bone_rot(pb('neck', -2), 'z', -0.3*u, f)",
        "    set_bone_rot(pb('spine', 1), 'x', -0.05*u, f)",
        "    set_bone_rot(pb('arm_l', 0), 'x', 0.15*u, f)",
        "    set_bone_rot(pb('arm_r', 0), 'x', -0.15*u, f)",
    ]
    return L


_CLIP_BAKE = """# --- real clip loop baking (Armature pose bones) ---
def read_clip_pose(act, frame):
    arm.animation_data_create()
    arm.animation_data.action = act
    bpy.context.scene.frame_set(frame)
    dg = bpy.context.evaluated_depsgraph_get(); dg.update()
    out = {}
    for pb in arm.pose.bones:
        if pb.rotation_mode == 'QUATERNION':
            q = pb.rotation_quaternion.copy()
        else:
            q = pb.rotation_euler.copy()
        out[pb.name] = q
    arm.animation_data.action = None
    return out

def bake_clip_loop(clip, fs, n, cycle_len, travel=False):
    act = bpy.data.actions.get(clip)
    if act is None:
        return False
    fr0 = act.frame_range
    c0 = int(fr0[0])
    frames = []
    for i in range(n):
        f_clip = c0 + (i % cycle_len)
        frames.append(read_clip_pose(act, f_clip))
    for i, qs in enumerate(frames):
        f = fs + i
        for name, q in qs.items():
            pb = arm.pose.bones.get(name)
            if pb is None:
                continue
            if pb.rotation_mode == 'QUATERNION':
                pb.rotation_quaternion = q
                pb.keyframe_insert(data_path='rotation_quaternion', frame=f)
            else:
                pb.rotation_euler = q
                pb.keyframe_insert(data_path='rotation_euler', frame=f)
    return True
"""

_EXTRA_BAKE = """# --- extra baking for test B (walk loop) ---
def bake_testb_extra():
    if scenario != 'fullbody':
        return
    # walk window 6.5-9.0s at 30fps = 75 frames
    ws = int(round(6.5 * FPS)) + 1
    n = int(round(2.5 * FPS))
    bake_clip_loop(WALK_CLIP, ws, n, WALK_CYCLE)
    # trajectory travel over same window
    we = ws + n
    for f in range(ws, we + 1):
        u = smoothstep(ws, we, f)
        traj.location.x = START_X + (END_X - START_X) * u
        traj.keyframe_insert(data_path='location', frame=f)
"""

def build_full_body_script(
    model_path: str | Path,
    out_dir: str | Path,
    *,
    scenario: str = "joint",
    width: int = 540,
    height: int = 960,
    fps: int = 30,
    fit_fraction: float = 0.68,
    camera_lens: float = 45.0,
    travel: float = 0.9,
    walk_clip: str = "Anim_0",
    walk_cycle_frames: int = 16,
) -> Path:
    """Write and return the self-contained Mission 15 Blender script."""
    model_path = Path(model_path)
    if not model_path.exists():
        raise LocomotionError(f"Модель не найдена: {model_path}")
    if scenario not in ("joint", "fullbody"):
        raise LocomotionError(f"Неизвестный сценарий: {scenario}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / f"full_body_{scenario}.py"
    diag = out_dir / "diagnostics.json"

    L: list[str] = []
    L.append("import bpy, json, math, subprocess, os, sys, re")
    L.append("from pathlib import Path")
    L.append("")
    L.append('MODEL = r"%s"' % _quote(str(model_path)))
    L.append('OUT = Path(r"%s")' % _quote(str(out_dir.resolve())))
    L.append('DIAG = r"%s"' % _quote(str(diag.resolve())))
    L.append("FPS = %d" % int(fps))
    L.append("WIDTH = %d" % int(width))
    L.append("HEIGHT = %d" % int(height))
    L.append("FIT = %r" % float(fit_fraction))
    L.append("CAM_LENS = %r" % float(camera_lens))
    L.append("TRAVEL = %r" % float(travel))
    L.append('WALK_CLIP = %r' % walk_clip)
    L.append("WALK_CYCLE = %d" % int(walk_cycle_frames))
    L.append("scenario = %r" % scenario)
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
    L.append("")
    L.append("arm = None")
    L.append("for o in bpy.data.objects:")
    L.append("    if o.type == 'ARMATURE':")
    L.append("        arm = o; break")
    L.append("if arm is None:")
    L.append("    raise RuntimeError('Не найдена Armature в модели.')")
    L.append("")
    L.append(_PROBE_BONES)
    L.append("GROUPS = detect_groups()")
    L.append("def pb(group, idx):")
    L.append("    g = GROUPS.get(group, [])")
    L.append("    if not g: raise RuntimeError('Нет костей группы ' + group)")
    L.append("    return arm.pose.bones[g[idx]]")
    L.append("")
    L.append(_SKEL_HELPERS)
    L.append("")
    L.append("neutral_pose(1)")
    L.append("")
    # camera fit
    L.append("from mathutils import Vector")
    L.append("minx=1e9; maxx=-1e9; minz=1e9; maxz=-1e9")
    L.append("for o in bpy.data.objects:")
    L.append("    if o.type != 'MESH': continue")
    L.append("    for v in o.bound_box:")
    L.append("        w = o.matrix_world @ Vector(v)")
    L.append("        minx=min(minx,w.x); maxx=max(maxx,w.x)")
    L.append("        minz=min(minz,w.z); maxz=max(maxz,w.z)")
    L.append("CHAR_W = max(1e-6, maxx-minx); CHAR_H = max(1e-6, maxz-minz)")
    L.append("CHAR_CZ = (minz+maxz)*0.5")
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
    L.append("dh = ((CHAR_W + TRAVEL)/(2.0*FIT))/tanh")
    L.append("d = max(dv, dh)")
    L.append("cam_obj.location = (0.0, -d, CHAR_CZ)")
    L.append("cam_obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)")
    L.append("sun = bpy.data.lights.new('Sun', 'SUN')")
    L.append("sun_obj = bpy.data.objects.new('Sun', sun)")
    L.append("bpy.context.collection.objects.link(sun_obj)")
    L.append("sun_obj.rotation_euler = (0.9, 0.2, 0.6)")
    L.append("sun_obj.data.energy = 4.0")
    L.append("scene.world = bpy.data.worlds.new('Sky')")
    L.append("w = scene.world; w.use_nodes = True")
    L.append("bg = w.node_tree.nodes.get('Background')")
    L.append("if bg:")
    L.append("    bg.inputs[0].default_value = (0.55, 0.75, 1.0, 1.0)")
    L.append("    bg.inputs[1].default_value = 1.0")
    L.append("")
    # trajectory
    L.append("traj = bpy.data.objects.new('TRAJ', None)")
    L.append("bpy.context.collection.objects.link(traj)")
    L.append("arm.parent = traj")
    L.append("arm.matrix_parent_inverse = traj.matrix_world.inverted()")
    L.append("START_X = -TRAVEL*0.5; END_X = TRAVEL*0.5")
    L.append("traj.location = (START_X, 0.0, 0.0)")
    L.append("")
    L.append("def smoothstep(a,b,x):")
    L.append("    x = max(0.0, min(1.0, (x-a)/(b-a)))")
    L.append("    return x*x*(3.0-2.0*x)")
    L.append("")
    L.append(_CLIP_BAKE)
    L.append(_EXTRA_BAKE)
    L.append("")
    L += _build_scenario_lines(scenario, fps)
    L.append("")
    L.append("if 'bake_testb_extra' in globals():")
    L.append("    bake_testb_extra()")
    L.append("")
    L.append("# ================= diagnostics =================")
    L.append("total_frames = int(round(TOTAL*FPS))")
    L.append("diag = {'scenario': %r, 'samples': {}, 'boundaries': {}, 'displacement': {}, 'framing': {}, 'limbs': {}}" % scenario)
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
    L.append("def rot_at(f):")
    L.append("    bpy.context.scene.frame_set(f)")
    L.append("    dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("    out = {}")
    L.append("    for pb in arm.pose.bones:")
    L.append("        q = pb.matrix.to_quaternion()")
    L.append("        out[pb.name] = [round(x,4) for x in (q.w, q.x, q.y, q.z)]")
    L.append("    return out")
    L.append("")
    L.append("sample_frames = [1, int(1.2*FPS)+1, int(2.0*FPS)+1, int(3.2*FPS)+1,")
    L.append("                 int(4.4*FPS)+1, int(5.5*FPS)+1, int(7.5*FPS)+1,")
    L.append("                 int(9.5*FPS)+1, int(10.6*FPS)+1]")
    L.append("sample_frames = [max(1, min(total_frames, f)) for f in sample_frames]")
    L.append("for f in sample_frames:")
    L.append("    diag['samples'][f] = {'framing': framing_at(f)}")
    L.append("")
    L.append("margins = [diag['samples'][f]['framing']['margin'] for f in diag['samples']")
    L.append("           if diag['samples'][f]['framing'] is not None]")
    L.append("diag['framing'] = {'min_margin': round(min(margins),3) if margins else -1.0,")
    L.append("                  'passed': bool(margins) and min(margins) >= 0.04}")
    L.append("")
    L.append("diag['displacement'] = {'travel_net': TRAVEL, 'start': -TRAVEL*0.5, 'end': TRAVEL*0.5}")
    L.append("")
    L.append("# per-bone max angular delta over each phase window")
    L.append("def max_delta(qs):")
    L.append("    best = 0.0")
    L.append("    for i in range(len(qs)-1):")
    L.append("        dot = abs(sum(a*b for a,b in zip(qs[i], qs[i+1])))")
    L.append("        best = max(best, 2.0*math.acos(min(1.0, dot)))")
    L.append("    return round(best, 3)")
    L.append("")
    L.append("windows = {'idle': (1, int(1.4*FPS)), 'neck': (int(1.5*FPS)+1, int(2.4*FPS)),")
    L.append("           'spine': (int(3.0*FPS)+1, int(3.9*FPS)),")
    L.append("           'arms': (int(4.0*FPS)+1, int(5.9*FPS)),")
    L.append("           'legs': (int(7.0*FPS)+1, int(7.9*FPS)),")
    L.append("           'walk': (int(7.0*FPS)+1, int(8.5*FPS))}")
    L.append("for wname, (a, b) in windows.items():")
    L.append("    qs = {nm: [] for nm in arm.pose.bones.keys()}")
    L.append("    for f in range(a, b+1):")
    L.append("        r = rot_at(f)")
    L.append("        for nm in qs: qs[nm].append(r[nm])")
    L.append("    diag['limbs'][wname] = {nm: max_delta(qs[nm]) for nm in qs}")
    L.append("")
    L.append("diag['boundaries']['idle_to_act'] = {'frames': [int(1.5*FPS), int(1.5*FPS)+1]}")
    L.append("diag['boundaries']['act_to_walk'] = {'frames': [int(6.5*FPS), int(6.5*FPS)+1]}")
    L.append("diag['boundaries']['walk_to_stop'] = {'frames': [int(9.0*FPS), int(9.0*FPS)+1]}")
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
    L.append("print('FULLBODY_OK', video)")

    script.write_text("\n".join(L), encoding="utf-8")
    return script


def render_full_body(script: Path, *, blender_exe: str | None = None,
                     timeout: int = 3600) -> FullBodyResult:
    """Run the Mission 15 benchmark script in background Blender."""
    from ..blender_adapter import blender_executable
    exe = blender_exe or blender_executable()
    if not exe:
        return FullBodyResult(status="FAILED", error="Blender не найден.")
    out_dir = script.parent
    scenario = "fullbody" if "fullbody" in script.name else "joint"
    try:
        proc = subprocess.run([exe, "--background", "--python", str(script)],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return FullBodyResult(status="FAILED", script=str(script), scenario=scenario,
                              render_dir=str(out_dir), error="timeout")
    video = out_dir / "final.mp4"
    if proc.returncode != 0 or not video.exists():
        return FullBodyResult(status="FAILED", script=str(script), scenario=scenario,
                              render_dir=str(out_dir), error=(proc.stderr or "")[-1200:])
    diag: dict[str, Any] = {}
    diag_path = out_dir / "diagnostics.json"
    if diag_path.exists():
        try:
            diag = json.loads(diag_path.read_text(encoding="utf-8"))
        except Exception:
            diag = {}
    return FullBodyResult(status="OK", video=video.as_posix(), script=str(script),
                          render_dir=str(out_dir), scenario=scenario, diagnostics=diag)
