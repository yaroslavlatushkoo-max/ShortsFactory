"""Voxel engine — procedural animation (quality v1).

Produces a Blender-side source chunk that, given the plan's action timeline,
keyframes the hero's joints (limbs swing, root translation/rotation, torso &
head tilt, jump arc, fall/recover).  All motion is deterministic and the root
height is anchored at ``HIP_Z`` so the feet stay on the ground.
"""

from __future__ import annotations

from .voxel_rig import HIP_Z


def _S(v: float) -> str:
    return f"{v:.4f}"


_ANIM_HELPERS = r'''
# ---------------- animation helpers ----------------
BASE_Z = %HIPZ%
S = {'x': START_X, 'y': START_Y, 'z': 0.0, 'turn': 0.0}
def _root_loc():
    return (S['x'], S['y'], BASE_Z + S['z'])
def _base(f, keep=()):
    for part in ('torso','head','arm_l','arm_r','leg_l','leg_r'):
        if part in keep:
            continue
        o = HERO[part]
        o.rotation_euler = (0.0, 0.0, 0.0)
        o.keyframe_insert(data_path='rotation_euler', frame=f)
    HERO['root'].location = _root_loc()
    HERO['root'].keyframe_insert(data_path='location', frame=f)
    HERO['root'].rotation_euler = (0.0, 0.0, S['turn'])
    HERO['root'].keyframe_insert(data_path='rotation_euler', frame=f)
def _pose(f, rot=None, loc=None):
    if rot:
        for part, euler in rot.items():
            HERO[part].rotation_euler = euler
            HERO[part].keyframe_insert(data_path='rotation_euler', frame=f)
    if loc is not None:
        HERO['root'].location = loc
        HERO['root'].keyframe_insert(data_path='location', frame=f)
def _k(obj, path, value, f):
    setattr(obj, path, value)
    obj.keyframe_insert(data_path=path, frame=f)
'''

def _set_action_source() -> str:
    return r'''
def set_action(action_name, start, duration, params):
    f0 = int(round(start * FPS)) + 1
    f1 = int(round((start + duration) * FPS)) + 1
    if f1 <= f0:
        f1 = f0 + 1
    _base(f0)
    a = action_name
    if a == 'walk' or a == 'run':
        tx = (float(params.get('to_x', 0.62)) - 0.5) * STAGE_W
        cycles = float(params.get('cycles', 3.0))
        amp = 0.35 if a == 'walk' else 0.7
        lean = 0.10 if a == 'walk' else 0.16
        steps = max(2, f1 - f0)
        for i in range(steps):
            f = f0 + i
            u = i / (steps - 1)
            x = S['x'] + (tx - S['x']) * u
            swing = math.sin(u * 6.2832 * cycles)
            _pose(f, rot={
                'arm_l': (amp * swing, 0.0, 0.0),
                'arm_r': (-amp * swing, 0.0, 0.0),
                'leg_l': (0.55 * swing, 0.0, 0.0),
                'leg_r': (-0.55 * swing, 0.0, 0.0),
                'torso': (lean, 0.0, -0.08 * math.cos(u * 6.2832 * cycles)),
            }, loc=(x, S['y'], BASE_Z + S['z']))
        S['x'] = tx
    elif a == 'stop':
        # Decelerate to a standstill, returning limbs to neutral.
        steps = max(3, f1 - f0)
        for i in range(steps):
            f = f0 + i
            u = i / (steps - 1)
            ease = 1.0 - (1.0 - u) * (1.0 - u)  # ease-in deceleration
            tail = (1.0 - u)
            _pose(f, rot={
                'arm_l': (0.3 * tail, 0.0, 0.0),
                'arm_r': (-0.3 * tail, 0.0, 0.0),
                'leg_l': (0.4 * tail, 0.0, 0.0),
                'leg_r': (-0.4 * tail, 0.0, 0.0),
                'torso': (0.06 * tail, 0.0, 0.0),
            }, loc=(S['x'], S['y'], BASE_Z + S['z'] + 0.03 * tail))
    elif a == 'jump':
        peak = float(params.get('peak', 0.7))
        steps = max(2, f1 - f0)
        for i in range(steps):
            f = f0 + i
            u = i / (steps - 1)
            z = BASE_Z + S['z'] + 4.0 * peak * u * (1.0 - u)
            tuck = 0.5 if 0.35 < u < 0.65 else 0.0
            _pose(f, rot={
                'leg_l': (tuck, 0.0, 0.0),
                'leg_r': (-tuck, 0.0, 0.0),
                'arm_l': (0.5, 0.0, 0.0),
                'arm_r': (-0.5, 0.0, 0.0),
            }, loc=(S['x'], S['y'], z))
        S['z'] = 0.0
    elif a == 'turn':
        sign = 1.0 if float(params.get('to_facing', 1)) >= 0 else -1.0
        target = S['turn'] + sign * 0.5
        steps = max(3, f1 - f0)
        for i in range(steps):
            f = f0 + i
            u = i / (steps - 1)
            ang = S['turn'] + (target - S['turn']) * u
            _pose(f, rot={'head': (0.0, 0.0, -sign * 0.35 * u)})
            _k(HERO['root'], 'rotation_euler', (0.0, 0.0, ang), f)
        S['turn'] = target
    elif a == 'bend':
        low = float(params.get('intensity', 1.0)) * 0.32
        _pose(f1, rot={'torso': (0.55 * low, 0.0, 0.0)},
                  loc=(S['x'], S['y'], BASE_Z + S['z'] - low))
        S['z'] = S['z'] - low
    elif a == 'reach':
        amp = float(params.get('intensity', 1.0)) * 1.2
        _pose(f1, rot={'arm_r': (-amp, 0.0, 0.0),
                       'arm_l': (0.25, 0.0, 0.0)})
    elif a == 'pickup':
        amp = float(params.get('intensity', 1.0))
        _base(f0, keep=('torso', 'head'))
        _pose(f0, rot={'arm_r': (-0.4, 0.0, 0.0)},
                  loc=(S['x'], S['y'], BASE_Z + S['z'] - 0.22 * amp))
        _pose(f1, rot={'arm_r': (-1.4 * amp, 0.0, 0.0),
                       'torso': (0.42 * amp, 0.0, 0.0)},
                  loc=(S['x'], S['y'], BASE_Z + S['z'] - 0.36 * amp))
        S['z'] = S['z'] - 0.36 * amp
    elif a == 'hold':
        _base(f0, keep=('torso', 'head', 'arm_l'))
        _pose(f0, rot={'arm_r': (-0.65, 0.0, 0.0)})
        _pose(f1, rot={'arm_r': (-0.5, 0.0, 0.0)})
    elif a == 'react':
        amp = float(params.get('intensity', 1.0))
        f_mid = max(f0 + 1, int((f0 + f1) * 0.45))
        # anticipation: slight crouch / brace
        _pose(f0, rot={'torso': (0.18, 0.0, 0.0), 'arm_r': (-0.3, 0.0, 0.0)},
                  loc=(S['x'], S['y'], BASE_Z + S['z'] - 0.06))
        # surprise: recoil + arms up + head back
        _pose(f_mid, rot={'arm_l': (math.pi, 0.0, 0.0),
                          'arm_r': (math.pi, 0.0, 0.0),
                          'head': (-0.5 * amp, 0.0, 0.0),
                          'torso': (-0.28 * amp, 0.0, 0.0)},
                  loc=(S['x'], S['y'], BASE_Z + S['z'] + 0.14 * amp))
        # settle into a surprised hold
        _pose(f1, rot={'arm_l': (2.6, 0.0, 0.0),
                       'arm_r': (2.6, 0.0, 0.0),
                       'head': (-0.3 * amp, 0.0, 0.0),
                       'torso': (-0.15 * amp, 0.0, 0.0)},
                  loc=(S['x'], S['y'], BASE_Z + S['z'] + 0.08 * amp))
    elif a == 'fall':
        _pose(f1, rot={'torso': (0.7, 0.0, 0.0)},
                  loc=(S['x'], S['y'], 0.15))
        _k(HERO['root'], 'rotation_euler', (0.0, 0.0, 1.0), f1)
    elif a == 'recover':
        _pose(f1, rot={'torso': (0.0, 0.0, 0.0)},
                  loc=(S['x'], S['y'], BASE_Z + S['z']))
        _k(HERO['root'], 'rotation_euler', (0.0, 0.0, S['turn']), f1)
    else:  # idle
        steps = max(2, min(7, f1 - f0))
        for i in range(steps):
            f = f0 + i
            u = i / (steps - 1)
            sway = math.sin(u * 6.2832 * 2.0)
            HERO['root'].location = (S['x'], S['y'], BASE_Z + S['z'] + 0.015 * sway)
            HERO['root'].keyframe_insert(data_path='location', frame=f)
            HERO['arm_l'].rotation_euler = (0.07 * sway, 0.0, 0.0)
            HERO['arm_l'].keyframe_insert(data_path='rotation_euler', frame=f)
            HERO['arm_r'].rotation_euler = (-0.07 * sway, 0.0, 0.0)
            HERO['arm_r'].keyframe_insert(data_path='rotation_euler', frame=f)
'''

def action_sequence_source(actions: list, fps: int, start_world: tuple[float, float]) -> str:
    """Return source that sets ``START_X/START_Y`` and applies the timeline."""
    sx, sy = start_world
    helpers = _ANIM_HELPERS.replace("%HIPZ%", _S(HIP_Z))
    parts = [
        "# ---------------- animation timeline ----------------",
        f"START_X = {_S(sx)}",
        f"START_Y = {_S(sy)}",
        helpers,
        _set_action_source(),
        "set_action('idle', 0.0, 0.001, {})  # establish baseline frame 1",
    ]
    for a in actions:
        name = a.action
        start = float(a.start)
        duration = float(a.duration)
        params = dict(a.params or {})
        parts.append(f"set_action({name!r}, {_S(start)}, {_S(duration)}, {params!r})")
    return "\n".join(parts)
