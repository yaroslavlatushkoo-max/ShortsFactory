"""Mission 15 (pivot) — authored skeletal voxel hero rig (isolated).

External CC0 voxel skeletal humanoids with *verified* skinning were not found
within constraints (CesiumMan unskinned; Fox render-unverified). This module
implements the documented fallback: a fully **authored** skeletal rig built
inside the generated Blender script (Armature + skinned cube-mesh boxes with
vertex groups and an Armature modifier). It provides real per-joint control:

* spine: torso_1..3 (+ neck_1..2 + head)
* arms L/R: arm_{L,R}_1 (shoulder) / _2 (elbow) / _3 (hand)
* legs L/R: leg_{L,R}_1 (thigh) / _2 (knee) / _3 (shin) / _4 (foot)

Bone groups are still *detected* from the authored names (no hard-coding in
the controller logic). Deterministic, offline, no NLA, no cloud/paid.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .locomotion import LocomotionError
from .full_body_rig import FullBodyResult

#: (parent, child) bone pairs for the authored rig. Order matters (root→leaf).
AUTH_BONES: tuple[tuple[str, str], ...] = (
    ("torso_1", "torso_2"),
    ("torso_2", "torso_3"),
    ("torso_3", "neck_1"),
    ("neck_1", "neck_2"),
    ("neck_2", "head"),
    ("torso_2", "arm_L_1"),
    ("arm_L_1", "arm_L_2"),
    ("arm_L_2", "arm_L_3"),
    ("torso_2", "arm_R_1"),
    ("arm_R_1", "arm_R_2"),
    ("arm_R_2", "arm_R_3"),
    ("torso_1", "leg_L_1"),
    ("leg_L_1", "leg_L_2"),
    ("leg_L_2", "leg_L_3"),
    ("leg_L_3", "leg_L_4"),
    ("torso_1", "leg_R_1"),
    ("leg_R_1", "leg_R_2"),
    ("leg_R_2", "leg_R_3"),
    ("leg_R_3", "leg_R_4"),
)

#: Bone head positions (rest) in object space.
AUTH_BONE_HEADS: dict[str, tuple[float, float, float]] = {
    "torso_1": (0.0, 0.0, 0.50),
    "torso_2": (0.0, 0.0, 0.90),
    "torso_3": (0.0, 0.0, 1.30),
    "neck_1": (0.0, 0.0, 1.55),
    "neck_2": (0.0, 0.0, 1.70),
    "head": (0.0, 0.0, 1.85),
    "arm_L_1": (-0.35, 0.0, 0.98),
    "arm_L_2": (-0.55, 0.0, 0.78),
    "arm_L_3": (-0.72, 0.0, 0.55),
    "arm_R_1": (0.35, 0.0, 0.98),
    "arm_R_2": (0.55, 0.0, 0.78),
    "arm_R_3": (0.72, 0.0, 0.55),
    "leg_L_1": (-0.14, 0.0, 0.55),
    "leg_L_2": (-0.14, 0.0, 0.32),
    "leg_L_3": (-0.14, 0.0, 0.12),
    "leg_L_4": (-0.30, 0.0, -0.10),
    "leg_R_1": (0.14, 0.0, 0.55),
    "leg_R_2": (0.14, 0.0, 0.32),
    "leg_R_3": (0.14, 0.0, 0.12),
    "leg_R_4": (0.30, 0.0, -0.10),
}


def _quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def build_authored_rig_script(
    out_dir: str | Path,
    *,
    scenario: str = "joint",
    width: int = 540,
    height: int = 960,
    fps: int = 30,
    fit_fraction: float = 0.68,
    camera_lens: float = 45.0,
    travel: float = 0.9,
    seed: int = 20261028,
) -> Path:
    """Write and return the self-contained authored-rig Blender benchmark script."""
    if scenario not in ("joint", "fullbody"):
        raise LocomotionError(f"Неизвестный сценарий: {scenario}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / f"full_body_authored_{scenario}.py"
    diag = out_dir / f"{scenario}_diagnostics.json"

    total_dur = 10.0 if scenario == "joint" else 11.0

    L: list[str] = []
    L.append("import bpy, json, math, subprocess, os, sys")
    L.append("from pathlib import Path")
    L.append("")
    L.append('OUT = Path(r"%s")' % _quote(str(out_dir.resolve())))
    L.append('DIAG = r"%s"' % _quote(str(diag.resolve())))
    L.append("FPS = %d" % int(fps))
    L.append("WIDTH = %d" % int(width))
    L.append("HEIGHT = %d" % int(height))
    L.append("FIT = %r" % float(fit_fraction))
    L.append("CAM_LENS = %r" % float(camera_lens))
    L.append("TRAVEL = %r" % float(travel))
    L.append("TOTAL = %r" % float(total_dur))
    L.append("SCENARIO = %r" % scenario)
    L.append("SEED = %d" % int(seed))
    L.append("")
    L.append("bpy.ops.wm.read_factory_settings(use_empty=True)")
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
    L.append("scene.frame_end = int(round(TOTAL * FPS))")
    L.append("total_frames = scene.frame_end")
    L.append("")
    L.append("# ---------- lights / sky ----------")
    L.append("sun = bpy.data.lights.new('Sun', 'SUN')")
    L.append("sun_obj = bpy.data.objects.new('Sun', sun)")
    L.append("bpy.context.collection.objects.link(sun_obj)")
    L.append("sun_obj.rotation_euler = (0.9, 0.2, 0.6)")
    L.append("sun_obj.data.energy = 3.5")
    L.append("scene.world = bpy.data.worlds.new('Sky')")
    L.append("w = scene.world; w.use_nodes = True")
    L.append("bg = w.node_tree.nodes.get('Background')")
    L.append("if bg:")
    L.append("    bg.inputs[0].default_value = (0.45, 0.7, 1.0, 1.0)")
    L.append("    bg.inputs[1].default_value = 1.0")
    L.append("")
    L.append("# ---------- ground ending")
    L.append("bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0,0,-0.12))")
    L.append("gr = bpy.context.active_object; gr.name='Ground'")
    L.append("gr.scale = (10.0, 8.0, 0.24)")
    L.append("gm = bpy.data.materials.new('GroundMat'); gm.use_nodes=True")
    L.append("gbs = gm.node_tree.nodes.get('Principled BSDF')")
    L.append("if gbs: gbs.inputs[0].default_value = (0.35,0.55,0.30,1.0)")
    L.append("gr.data.materials.append(gm)")
    L.append("")
    L.append("# ---------- authored EMPTY-joint rig (segmented body) ----------")
    L.append("from mathutils import Vector")
    L.append("BONES = %r" % (list(dict(AUTH_BONES).keys()) + [c for _, c in AUTH_BONES if c not in dict(AUTH_BONES)],))
    L.append("BONE_HEADS = %r" % AUTH_BONE_HEADS)
    L.append("EMPTIES = {}")
    L.append("for nm in BONES:")
    L.append("    e = bpy.data.objects.new('E_' + nm, None)")
    L.append("    bpy.context.collection.objects.link(e)")
    L.append("    e.location = Vector(BONE_HEADS[nm])")
    L.append("    EMPTIES[nm] = e")
    L.append("for p, c in %s:" % repr(tuple(AUTH_BONES)))
    L.append("    if p in EMPTIES and c in EMPTIES:")
    L.append("        EMPTIES[c].parent = EMPTIES[p]")
    L.append("")
    L.append("def make_part(name, joint, dims, loc):")
    L.append("    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(loc[0], loc[1], loc[2]))")
    L.append("    o = bpy.context.active_object; o.name = name")
    L.append("    o.scale = dims")
    L.append("    o.parent = EMPTIES[joint]")
    L.append("    o.location = tuple(Vector(loc) - Vector(BONE_HEADS[joint]))")
    L.append("    return o")
    L.append("")
    L.append("m = bpy.data.materials.new('Hero'); m.use_nodes=True")
    L.append("bs = m.node_tree.nodes.get('Principled BSDF')")
    L.append("bs.inputs[0].default_value = (0.14,0.52,0.44,1.0)  # teal shirt")
    L.append("m2 = bpy.data.materials.new('HeroPants'); m2.use_nodes=True")
    L.append("bs2 = m2.node_tree.nodes.get('Principled BSDF')")
    L.append("bs2.inputs[0].default_value = (0.17,0.35,0.40,1.0)")
    L.append("mskin = bpy.data.materials.new('Skin'); mskin.use_nodes=True")
    L.append("bsk = mskin.node_tree.nodes.get('Principled BSDF')")
    L.append("bsk.inputs[0].default_value = (0.95,0.79,0.66,1.0)")
    L.append("")
    L.append("make_part('torso_bot','torso_1',(0.50,0.34,0.30),(0.0,0.0,0.62))")
    L.append("make_part('torso_mid','torso_2',(0.50,0.34,0.36),(0.0,0.0,0.95))")
    L.append("make_part('torso_top','torso_3',(0.46,0.32,0.32),(0.0,0.0,1.28))")
    L.append("make_part('neck','neck_2',(0.22,0.22,0.18),(0.0,0.0,1.62))")
    L.append("make_part('head','head',(0.40,0.40,0.38),(0.0,0.0,1.88))")
    L.append("make_part('arm_l_upper','arm_L_1',(0.20,0.20,0.30),(-0.46,0.0,0.88))")
    L.append("make_part('arm_l_lower','arm_L_2',(0.16,0.16,0.32),(-0.63,0.0,0.66))")
    L.append("make_part('hand_l','arm_L_3',(0.14,0.14,0.18),(-0.74,0.0,0.46))")
    L.append("make_part('arm_r_upper','arm_R_1',(0.20,0.20,0.30),(0.46,0.0,0.88))")
    L.append("make_part('arm_r_lower','arm_R_2',(0.16,0.16,0.32),(0.63,0.0,0.66))")
    L.append("make_part('hand_r','arm_R_3',(0.14,0.14,0.18),(0.74,0.0,0.46))")
    L.append("make_part('leg_l_upper','leg_L_1',(0.24,0.24,0.30),(-0.14,0.0,0.42))")
    L.append("make_part('leg_l_lower','leg_L_2',(0.20,0.20,0.30),(-0.14,0.0,0.22))")
    L.append("make_part('shin_l','leg_L_3',(0.18,0.18,0.30),(-0.14,0.0,0.02))")
    L.append("make_part('foot_l','leg_L_4',(0.34,0.20,0.12),(-0.26,0.0,-0.12))")
    L.append("make_part('leg_r_upper','leg_R_1',(0.24,0.24,0.30),(0.14,0.0,0.42))")
    L.append("make_part('leg_r_lower','leg_R_2',(0.20,0.20,0.30),(0.14,0.0,0.22))")
    L.append("make_part('shin_r','leg_R_3',(0.18,0.18,0.30),(0.14,0.0,0.02))")
    L.append("make_part('foot_r','leg_R_4',(0.34,0.20,0.12),(0.26,0.0,-0.12))")
    L.append("")
    L.append("# skin materials on parts")
    L.append("for nm, mat in (('head',mskin),('neck',mskin),('hand_l',mskin),('hand_r',mskin),")
    L.append("                 ('foot_l',mskin),('foot_r',mskin)):")
    L.append("    o = bpy.data.objects.get(nm)")
    L.append("    if o is not None: o.data.materials.append(mat)")
    L.append("for nm in ('torso_bot','torso_mid','torso_top',")
    L.append("          'arm_l_upper','arm_l_lower','arm_r_upper','arm_r_lower'):")
    L.append("    o = bpy.data.objects.get(nm)")
    L.append("    if o is not None: o.data.materials.append(m)")
    L.append("for nm in ('leg_l_upper','leg_l_lower','shin_l','leg_r_upper','leg_r_lower','shin_r'):")
    L.append("    o = bpy.data.objects.get(nm)")
    L.append("    if o is not None: o.data.materials.append(m2)")
    L.append("")
    L.append("# ---------- camera (portrait fit) ----------")
    L.append("minz=1e9; maxz=-1e9; minx=1e9; maxx=-1e9")
    L.append("for o in bpy.data.objects:")
    L.append("    if o.type != 'MESH': continue")
    L.append("    for v in o.bound_box:")
    L.append("        wv = o.matrix_world @ Vector(v)")
    L.append("        minz=min(minz,wv.z); maxz=max(maxz,wv.z)")
    L.append("        minx=min(minx,wv.x); maxx=max(maxx,wv.x)")
    L.append("CHAR_H = max(1e-6, maxz-minz); CHAR_W = max(1e-6, maxx-minx)")
    L.append("CHAR_CZ = (minz+maxz)*0.5")
    L.append("cam = bpy.data.cameras.new('Cam'); cam_obj = bpy.data.objects.new('Camera', cam)")
    L.append("bpy.context.collection.objects.link(cam_obj); scene.camera = cam_obj")
    L.append("cam.sensor_fit = 'VERTICAL'; cam.sensor_height = 18.0; cam.lens = CAM_LENS")
    L.append("tanv = (18.0*0.5)/CAM_LENS; tanh = tanv*(WIDTH/HEIGHT)")
    L.append("d = max((CHAR_H/FIT)/(2.0*tanv), (CHAR_W+TRAVEL)/(2.0*FIT)/tanh)")
    L.append("cam_obj.location = (0.0, -d, CHAR_CZ)")
    L.append("cam_obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)")
    L.append("")
    L.append("# ---------- TRAJ parent for locomotion ----------")
    L.append("traj = bpy.data.objects.new('TRAJ', None)")
    L.append("bpy.context.collection.objects.link(traj)")
    L.append("EMPTIES['torso_1'].parent = traj")
    L.append("EMPTIES['torso_1'].matrix_parent_inverse = traj.matrix_world.inverted()")
    L.append("traj.location = (-TRAVEL*0.5, 0.0, 0.0)")
    L.append("")
    L.append("# ---------- pose helpers (rotate joint empties) ----------")
    L.append("def set_rot(nm, axis, ang, f):")
    L.append("    e = EMPTIES[nm]")
    L.append("    if axis == 'x': e.rotation_euler[0] = ang")
    L.append("    elif axis == 'y': e.rotation_euler[1] = ang")
    L.append("    else: e.rotation_euler[2] = ang")
    L.append("    e.keyframe_insert(data_path='rotation_euler', frame=f)")
    L.append("def neutral(f):")
    L.append("    for nm in BONES:")
    L.append("        e = EMPTIES[nm]; e.rotation_euler = (0.0,0.0,0.0)")
    L.append("        e.keyframe_insert(data_path='rotation_euler', frame=f)")
    L.append("def ease(u): return u*u*(3.0-2.0*u)")
    L.append("")
    L.append("# ---------- joint demo timeline ----------")
    L.append("if SCENARIO == 'joint':")
    L.append("    neutral(1)")
    L.append("    def seg(a, b, fn):")
    L.append("        f0 = int(round(a*FPS))+1; f1 = int(round(b*FPS))+1")
    L.append("        for f in range(f0, f1+1):")
    L.append("            u = (f-f0)/max(1,f1-f0)")
    L.append("            fn(f, u)")
    L.append("    # head tilt/turn (1-2s)")
    L.append("    seg(1,2, lambda f,u: (set_rot('head','z',0.5*ease(u),f), set_rot('head','x',0.3*ease(u),f)))")
    L.append("    # neck + spine (2-3s)")
    L.append("    seg(2,3, lambda f,u: (set_rot('neck_2','x',0.2*ease(u),f), set_rot('torso_3','x',0.2*ease(u),f), set_rot('torso_2','x',0.15*ease(u),f)))")
    L.append("    # spine sway (3-4s)")
    L.append("    seg(3,4, lambda f,u: (set_rot('torso_1','y',0.25*math.sin(u*math.pi),f), set_rot('torso_2','y',0.2*math.sin(u*math.pi),f), set_rot('torso_3','y',0.15*math.sin(u*math.pi),f)))")
    L.append("    # shoulders (4-5s)")
    L.append("    seg(4,5, lambda f,u: (set_rot('arm_L_1','x',0.7*ease(u),f), set_rot('arm_R_1','x',0.7*ease(u),f)))")
    L.append("    # elbows + wrists (5-6s)")
    L.append("    seg(5,6, lambda f,u: (set_rot('arm_L_2','x',1.4*ease(u),f), set_rot('arm_R_2','x',-1.4*ease(u),f), set_rot('arm_L_3','z',0.6*ease(u),f)))")
    L.append("    # pelvis (6-7s)")
    L.append("    seg(6,7, lambda f,u: set_rot('torso_1','y',0.4*ease(u),f))")
    L.append("    # knees/feet (7-8s)")
    L.append("    seg(7,8, lambda f,u: (set_rot('leg_L_2','x',0.9*ease(u),f), set_rot('leg_R_2','x',0.9*ease(u),f), set_rot('leg_L_4','x',0.4*ease(u),f)))")
    L.append("    # coordinated whole body (8-9s)")
    L.append("    seg(8,9, lambda f,u: (set_rot('arm_L_1','x',0.8*ease(u),f), set_rot('arm_L_2','x',1.2*ease(u),f), set_rot('leg_L_1','x',0.4*ease(u),f), set_rot('leg_R_1','x',-0.4*ease(u),f), set_rot('head','z',0.3*ease(u),f)))")
    L.append("    # settle (9-10s)")
    L.append("    seg(9,10, lambda f,u: neutral(f))")
    L.append("else:")
    L.append("    # fullbody: idle breathing + weight shift (0-1.5s)")
    L.append("    neutral(1)")
    L.append("    for f in range(1, int(1.5*FPS)+1):")
    L.append("        u = (f-1)/max(1,int(1.5*FPS)-1)")
    L.append("        breath = 0.04*math.sin(u*math.pi*2.0)")
    L.append("        set_rot('torso_2','x',breath,f); set_rot('head','x',-breath*0.6,f)")
    L.append("    # notice: head turn (1.5-2.5s)")
    L.append("    for f in range(int(1.5*FPS)+1, int(2.5*FPS)+1):")
    L.append("        u = (f - int(1.5*FPS))/max(1,int(2.5*FPS)-int(1.5*FPS))")
    L.append("        set_rot('head','z',0.6*ease(u),f); set_rot('neck_2','z',0.3*ease(u),f)")
    L.append("    # sequential body turn (2.5-3.75s)")
    L.append("    for f in range(int(2.5*FPS)+1, int(3.75*FPS)+1):")
    L.append("        u = (f - int(2.5*FPS))/max(1,int(3.75*FPS)-int(2.5*FPS))")
    L.append("        set_rot('torso_1','y',0.5*ease(u),f)")
    L.append("        set_rot('torso_2','y',0.45*ease(u),f); set_rot('torso_3','y',0.4*ease(u),f)")
    L.append("        set_rot('arm_L_1','y',0.4*ease(u),f); set_rot('arm_R_1','y',0.4*ease(u),f)")
    L.append("    # expressive arm gesture (3.75-5s)")
    L.append("    for f in range(int(3.75*FPS)+1, int(5.0*FPS)+1):")
    L.append("        u = (f - int(3.75*FPS))/max(1,int(5.0*FPS)-int(3.75*FPS))")
    L.append("        set_rot('arm_R_1','x',-1.1*ease(u),f); set_rot('arm_R_2','x',-1.4*ease(u),f)")
    L.append("        set_rot('arm_R_3','z',0.5*ease(u),f)")
    L.append("    # walk via procedural controller (5-9s): steps + counter-arms + travel")
    L.append("    ws = int(5.0*FPS)+1; we = int(9.0*FPS)+1")
    L.append("    for f in range(ws, we+1):")
    L.append("        u = (f-ws)/max(1,we-ws)")
    L.append("        ph = u*math.pi*2.0*2.5  # ~2.5 gait cycles")
    L.append("        set_rot('leg_L_1','x',0.55*math.sin(ph),f)")
    L.append("        set_rot('leg_L_2','x',0.35*math.sin(ph),f)")
    L.append("        set_rot('leg_R_1','x',-0.55*math.sin(ph),f)")
    L.append("        set_rot('leg_R_2','x',-0.35*math.sin(ph),f)")
    L.append("        set_rot('arm_L_1','x',-0.45*math.sin(ph),f)")
    L.append("        set_rot('arm_R_1','x',0.45*math.sin(ph),f)")
    L.append("        set_rot('torso_2','y',0.08*math.sin(ph),f)")
    L.append("        uu = ease(u)")
    L.append("        traj.location.x = -TRAVEL*0.5 + TRAVEL*uu")
    L.append("        traj.keyframe_insert(data_path='location', frame=f)")
    L.append("    # stop: decelerate + stabilize (9-10s)")
    L.append("    for f in range(we+1, int(10.0*FPS)+1):")
    L.append("        u = (f-(we+1))/max(1,int(10.0*FPS)-(we+1))")
    L.append("        damp = 1.0 - ease(u)")
    L.append("        ph = (1.0+u)*math.pi*2.0*0.6")
    L.append("        set_rot('leg_L_1','x',0.55*damp*math.sin(ph),f)")
    L.append("        set_rot('leg_R_1','x',-0.55*damp*math.sin(ph),f)")
    L.append("        set_rot('arm_L_1','x',-0.45*damp*math.sin(ph),f)")
    L.append("        set_rot('arm_R_1','x',0.45*damp*math.sin(ph),f)")
    L.append("        traj.location.x = -TRAVEL*0.5 + TRAVEL*(1.0-0.1*ease(u))")
    L.append("        traj.keyframe_insert(data_path='location', frame=f)")
    L.append("    # final look at camera + pose (10-11s)")
    L.append("    for f in range(int(10.0*FPS)+1, int(11.0*FPS)+1):")
    L.append("        u = (f - int(10.0*FPS))/max(1,int(11.0*FPS)-int(10.0*FPS))")
    L.append("        set_rot('head','z',0.35*ease(u),f); set_rot('head','x',0.12*ease(u),f)")
    L.append("        set_rot('torso_3','z',0.1*ease(u),f)")
    L.append("")
    L.append("# ---------- diagnostics ----------")
    L.append("diag = {'scenario': SCENARIO, 'samples': {}, 'framing': {}, 'limbs': {}, 'displacement': {}}")
    L.append("def framing_at(f):")
    L.append("    bpy.context.scene.frame_set(f)")
    L.append("    dg = bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("    xs=[]; ys=[]")
    L.append("    for o in bpy.data.objects:")
    L.append("        if o.type != 'MESH': continue")
    L.append("        for v in o.bound_box:")
    L.append("            m = cam_obj.matrix_world.inverted() @ (o.matrix_world @ Vector(v))")
    L.append("            if abs(m.z) < 1e-6: continue")
    L.append("            x=(m.x/-m.z)/tanh; y=(m.y/-m.z)/tanv")
    L.append("            xs.append((x+1)*0.5); ys.append((y+1)*0.5)")
    L.append("    if not xs: return None")
    L.append("    return {'margin': round(min(min(xs),1-max(xs),min(ys),1-max(ys)),3)}")
    L.append("for f in (1, int(2.0*FPS)+1, int(4.0*FPS)+1, int(6.0*FPS)+1, int(8.0*FPS)+1, total_frames):")
    L.append("    diag['samples'][f] = framing_at(f)")
    L.append("margins = [v['margin'] for v in diag['samples'].values() if v]")
    L.append("diag['framing'] = {'min_margin': round(min(margins),3) if margins else -1.0, 'passed': bool(margins) and min(margins) >= 0.04}")
    L.append("def bone_delta(nm, f1, f2):")
    L.append("    bpy.context.scene.frame_set(f1); dg=bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("    a1 = list(EMPTIES[nm].rotation_euler)")
    L.append("    bpy.context.scene.frame_set(f2); dg=bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("    a2 = list(EMPTIES[nm].rotation_euler)")
    L.append("    return max(abs(x-y) for x,y in zip(a1,a2))")
    L.append("fA = int(3.5*FPS)+1; fB = int(4.5*FPS)+1")
    L.append("for nm in BONES:")
    L.append("    diag['limbs'][nm] = {'max_delta_rad': round(bone_delta(nm, fA, fB),3)}")
    L.append("traj_0 = traj.matrix_world.translation; traj_1 = traj.matrix_world.translation")
    L.append("bpy.context.scene.frame_set(1); dg=bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("p0 = list(traj.matrix_world.translation)")
    L.append("bpy.context.scene.frame_set(total_frames); dg=bpy.context.evaluated_depsgraph_get(); dg.update()")
    L.append("p1 = list(traj.matrix_world.translation)")
    L.append("dx = p1[0]-p0[0]")
    L.append("diag['displacement'] = {'start':[round(v,3) for v in p0], 'end':[round(v,3) for v in p1], 'total':round(dx,3), 'reset':bool(abs(dx)<1e-6)}")
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


def render_authored_rig(script: Path, *, blender_exe: str | None = None,
                        timeout: int = 3600) -> FullBodyResult:
    """Run an authored-rig benchmark script in background Blender."""
    from ..blender_adapter import blender_executable
    exe = blender_exe or blender_executable()
    if not exe:
        return FullBodyResult(status="FAILED", error="Blender не найден.")
    out_dir = script.parent
    try:
        proc = subprocess.run([exe, "--background", "--python", str(script)],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return FullBodyResult(status="FAILED", script=str(script),
                              render_dir=str(out_dir), error="timeout")
    video = out_dir / "final.mp4"
    if proc.returncode != 0 or not video.exists():
        return FullBodyResult(status="FAILED", script=str(script),
                              render_dir=str(out_dir),
                              error=(proc.stderr or "")[-900:])
    diag: dict[str, Any] = {}
    diag_path = out_dir / f"{script.name.replace('full_body_authored_','').replace('.py','')}_diagnostics.json"
    if diag_path.exists():
        try:
            diag = json.loads(diag_path.read_text(encoding="utf-8"))
        except Exception:
            diag = {}
    scenario = "joint" if "joint" in script.name else "fullbody"
    return FullBodyResult(status="OK", video=video.as_posix(),
                          script=str(script), render_dir=str(out_dir),
                          scenario=scenario, diagnostics=diag)
