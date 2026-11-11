# blend3d/blender_lib/motion.py — motion primitives for blocky characters.
# All functions are pure schedule helpers; the film bake loop consumes them per frame.
from core import *

class Path:
    """Waypoint path with walk/run phases. segs: list of dict(f0,f1,a,b,speed)"""
    def __init__(self, segs):
        self.segs = segs

    def pos_heading(self, f):
        for s in self.segs:
            if s['f0'] <= f <= s['f1']:
                u = ss(u_in(f, s['f0'], s['f1']))
                a, b = P(s['a'][0], s['a'][1], 0), P(s['b'][0], s['b'][1], 0)
                dist = (b - a).length
                phase = (dist * u) / max(0.05, s.get('stride', 0.55)) * math.pi
                return a.lerp(b, u), heading_to(a, b), phase, u
        for s in reversed(self.segs):
            if s['f1'] < f:
                return P(s['b'][0], s['b'][1], 0), None, 0.0, 0.0
        return P(self.segs[0]['a'][0], self.segs[0]['a'][1], 0), None, 0.0, 0.0

def heading_schedule(keys, f):
    """keys: [(frame, angle)] with shortest-arc smoothstep interp."""
    if f <= keys[0][0]: return keys[0][1]
    for j in range(len(keys) - 1):
        if keys[j][0] <= f < keys[j + 1][0]:
            u = ss(u_in(f, keys[j][0], keys[j + 1][0]))
            a0, a1 = keys[j][1], keys[j + 1][1]
            d = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
            return a0 + d * u
    return keys[-1][1]

def walk_pose(phase, amp_leg=0.55, amp_arm=0.38, moving=True):
    """Returns (legL_rotX, legR_rotX, armL_rotX, armR_rotX, bob, sway)."""
    if not moving:
        return 0, 0, 0, 0, 0.0, 0.0
    s = math.sin(phase)
    c = math.sin(phase + math.pi)
    return (s * amp_leg, c * amp_leg, c * amp_arm, s * amp_arm,
            abs(s) * 0.035, 0.03 * s)

class Swing:
    """Pickaxe/tool swings with contact frames. hits: [frame,...]"""
    def __init__(self, hits, wind=8, strike=5, recover=10, base=(-0.15, 0.1), windup=-2.35, strike_end=-0.55):
        self.hits = hits
        self.wind = wind; self.strike = strike; self.recover = recover
        self.base = base; self.windup = windup; self.strike_end = strike_end

    def pose(self, f):
        for h in self.hits:
            f0, f1, f2 = h - self.wind, h - self.strike, h + self.recover
            if f0 <= f <= f2:
                if f <= f1:
                    u = ss(u_in(f, f0, f1))
                    rx = lerp(self.base[0], self.windup, u)
                else:
                    u = ss(u_in(f, f1, f2))
                    rx = lerp(self.strike_end, self.base[0], u)
                    if f <= h:
                        u2 = u_in(f, f1, h)
                        rx = lerp(self.windup, self.strike_end, u2 * u2)
                return rx, (f in range(h - 1, h + 2))
        return self.base[0], False

def fall_z(f, f0, f1, z0, z1, accel=1.0):
    u = u_in(f, f0, f1)
    return lerp(z0, z1, (u ** accel))

class FaceTrack:
    """emotes: [(f0, f1, emote)] with 8-frame transitions."""
    def __init__(self, emotes):
        self.emotes = emotes

    def at(self, f):
        cur, k = 'neutral', 0.0
        for f0, f1, em in self.emotes:
            if f0 - 8 <= f <= f1:
                cur = em
                if f < f0 + 8: k = min(1.0, u_in(f, f0 - 8, f0))
                elif f > f1 - 8: k = min(1.0, u_in(f1, f, f1 + 8))
                else: k = 1.0
        return cur, k

def head_look(head_p, target_local_dir, max_yaw=1.1, max_pitch=0.5):
    """Compute head pivot euler to look along dir (world). dir already rotated by body heading."""
    d = target_local_dir.normalized()
    yaw = math.atan2(-d.x, -d.y)
    pitch = math.asin(clamp(d.z, -1, 1))
    return Euler((clamp(-pitch, -max_pitch, max_pitch), 0, clamp(yaw, -max_yaw, max_yaw)), 'XYZ')

def hand_world(rig, side, heading, root_loc, arm_rx=0.0):
    """World position of hand attach point (mirrors rig hierarchy for prop baking).
    rig arm pivot sits at (±ax, 0, 1.36) in body space; hand attach at arm-local (0,0,-0.72)."""
    aw = 0.19 if rig['cfg']['slim'] else 0.25
    ax = (0.25 + aw / 2 + 0.01) * (1 if side == 'L' else -1)
    hy = 0.72 * math.sin(arm_rx)
    hz = 1.36 - 0.72 * math.cos(arm_rx)
    out = rz(P(ax, hy, hz), heading)
    return P(root_loc.x + out.x, root_loc.y + out.y, root_loc.z + out.z)
