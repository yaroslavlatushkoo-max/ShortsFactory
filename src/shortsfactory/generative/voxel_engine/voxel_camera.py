"""Voxel engine — camera / direction (quality v1).

A deliberate, staged camera system:

* a **target** empty childed to the hero's chest → the camera always frames the
  hero's face/torso even while walking (translation tracked);
* the **camera** uses a Track-To constraint on that target and is positioned by
  **shot segments** (explicit offset + focal length, safe 9:16 vertical framing),
  which are keyframed across every frame so the hero and the object stay in
  frame and the composition reads cleanly on a phone screen.
"""

from __future__ import annotations

from .voxel_rig import CHEST_HEIGHT

#: Shot table → (start_frac, end_frac, offset_x, offset_y, offset_z, lens_mm).
#: Offset is added to the hero root location.  +Y is the "front" side the
#: hero faces, so a positive offset_y places the camera in front of the hero
#: (face visible).  +X is the hero's right.
#: Main shot: medium-wide, three-quarter front, far enough to show the hero
#: fully plus surrounding environment, with room in the direction of movement.
#: Final shot: closer close-up on the hero's face/torso for the reaction beat.
DEFAULT_SHOTS = (
    (0.0, 0.82, 1.6, 6.0, 1.1, 30.0),
    (0.82, 1.01, 0.7, 3.2, 1.3, 45.0),
)


def _fmt(v: float) -> str:
    return f"{v:.4f}"


def camera_source(camera_keys: list, fps: int, px: float, py: float, shots: tuple | None = None) -> str:
    """Source chunk creating a staged tracking camera for the plan."""
    shots = shots or DEFAULT_SHOTS
    rows = []
    for (s, e, ox, oy, oz, lens) in shots:
        rows.append(f"    ({_fmt(s)}, {_fmt(e)}, {_fmt(ox)}, {_fmt(oy)}, {_fmt(oz)}, {_fmt(lens)}),")
    shot_table = "[\n" + "\n".join(rows) + "\n]"

    return f'''# ---------------- camera (staged 9:16) ----------------
cam = bpy.data.cameras.new('Cam')
cam.sensor_fit = 'VERTICAL'
cam.sensor_height = 18.0
cam_obj = bpy.data.objects.new('Camera', cam)
bpy.context.collection.objects.link(cam_obj)
bpy.context.scene.camera = cam_obj

tgt = bpy.data.objects.new('CamTarget', None)
bpy.context.collection.objects.link(tgt)
tgt.parent = HERO['root']
tgt.location = (0.0, 0.0, {CHEST_HEIGHT})

con = cam_obj.constraints.new('TRACK_TO')
con.target = tgt
con.track_axis = 'TRACK_NEGATIVE_Z'
con.up_axis = 'UP_Y'

SHOTS = {shot_table}
DUR = DURATION

def _cam_setup():
    bpy.context.scene.frame_set(1)
    for f in range(bpy.context.scene.frame_start, bpy.context.scene.frame_end + 1):
        t = (f - 1) / FPS
        ox = oy = oz = lens = None
        for (s, e, a, b, c, l) in SHOTS:
            if s * DUR <= t < e * DUR:
                ox, oy, oz, lens = a, b, c, l
                break
        if ox is None:
            ox, oy, oz, lens = SHOTS[-1][2], SHOTS[-1][3], SHOTS[-1][4], SHOTS[-1][5]
        bpy.context.scene.frame_set(f)
        r = HERO['root'].location
        cam_obj.location = (r[0] + ox, r[1] + oy, r[2] + oz)
        cam_obj.keyframe_insert(data_path='location', frame=f)
        cam.lens = lens
        cam.keyframe_insert(data_path='lens', frame=f)

_cam_setup()
'''
