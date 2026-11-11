"""Voxel engine — object interactions (pickup / hold / glow).

Creates the plan's objects as real cube meshes and, for the attach event, moves
the object to the hero's hand and reparents it to the hand joint so it follows
subsequent motion.  The glowing cube's emission strength is keyframed to ramp
up during the hero's reaction.
"""

from __future__ import annotations

from .voxel_rig import STAGE_D, STAGE_W


def _sx(x: float) -> float:
    return (float(x) - 0.5) * STAGE_W


def _sy(y: float) -> float:
    return (float(y) - 0.5) * STAGE_D


def _S(v: float) -> str:
    return f"{v:.4f}"


def interaction_source(objects: list, events: list, fps: int, pickup_start: float | None = None) -> str:
    """Return source that builds objects and drives the pickup interaction."""
    # When no explicit pickup start is given, default to 0.5 s before attach.
    if pickup_start is None:
        att = next((float(e.t) for e in events if e.kind == "attach"), 1.0)
        pickup_start = max(0.0, att - 0.6)
    lines = [
        "# ---------------- interactions ----------------",
        "CUBES = {}",
        "GLOW_MAT = None",
    ]
    for i, obj in enumerate(objects):
        oid = obj.id
        # start position from object keyframes (stage) or default.
        kf = (obj.keyframes or [])
        if kf:
            sx0 = float(kf[0].get("x", 0.62))
            sy0 = float(kf[0].get("y", 0.80))
        else:
            sx0, sy0 = 0.62, 0.80
        scale = float(kf[0].get("scale", 0.55)) if kf else 0.55
        lines.append(f"c = add_cube('{oid}', 0.5, ({_S(_sx(sx0))}, {_S(_sy(sy0))}, 0.25), _emit('stone', 2.5))")
        lines.append(f"c.scale = ({_S(scale)}, {_S(scale)}, {_S(scale)})")
        lines.append(f"CUBES['{oid}'] = c")
        lines.append("GLOW_MAT = c.active_material")

    # attach event(s)
    for ev in events:
        if ev.kind != "attach":
            continue
        oid = ev.object
        t_att = float(ev.t)
        f_att = int(round(t_att * fps)) + 1
        f_pu = int(round(pickup_start * fps)) + 1
        # Smoothly move the object to the *actual* hand position during the
        # pickup action (runtime-computed from the arm joint) so it lands in the
        # hand without a visible jump, then reparent to the hand.
        lines.append("# animate object toward the hand (world) during pickup")
        lines.append("CUBES['%s'].keyframe_insert(data_path='location', frame=1)" % oid)
        lines.append("import mathutils")
        lines.append("HAND_OFFSET = mathutils.Vector((0.0, 0.35, -0.58))")
        lines.append("ORIGIN = CUBES['%s'].location.copy()" % oid)
        lines.append("for _i, _f in enumerate(range(max(1, %d), %d + 1)):" % (f_pu, f_att))
        lines.append("    bpy.context.scene.frame_set(_f)")
        lines.append("    hw = HERO['arm_r'].matrix_world @ HAND_OFFSET")
        lines.append("    u = _i / max(1, %d - %d)" % (f_att, f_pu))
        lines.append("    ease = u * u * (3.0 - 2.0 * u)")
        lines.append("    pos = ORIGIN.lerp(mathutils.Vector((hw.x, hw.y, max(0.1, hw.z))), ease)")
        lines.append("    CUBES['%s'].location = pos" % oid)
        lines.append("    CUBES['%s'].keyframe_insert(data_path='location', frame=_f)" % oid)
        lines.append(f"bpy.context.scene.frame_set({f_att})")
        lines.append(f"CUBES['{oid}'].parent = HERO['arm_r']")
        lines.append("CUBES['%s'].matrix_parent_inverse = HERO['arm_r'].matrix_world.inverted()" % oid)
        lines.append("CUBES['%s'].location = HAND_OFFSET" % oid)
        lines.append("CUBES['%s'].keyframe_insert(data_path='location', frame=%d)" % (oid, f_att))
        lines.append("CUBES['%s'].keyframe_insert(data_path='rotation_euler', frame=%d)" % (oid, f_att))
    # emission ramp at react time (use last camera/action near end)
    lines += [
        "# emission ramp (glow brightens on reaction)",
        "if GLOW_MAT is not None:",
        "    try:",
        "        es = GLOW_MAT.node_tree.nodes.get('Principled BSDF').inputs['Emission Strength']",
        "        es.default_value = 2.5",
        "        es.keyframe_insert('default_value', frame=1)",
        "        rf = int(round(DURATION * 0.62 * FPS)) + 1",
        "        es.default_value = 9.0",
        "        es.keyframe_insert('default_value', frame=rf)",
        "    except Exception:",
        "        pass",
    ]
    return "\n".join(lines)
