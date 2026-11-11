# examples/smoke_scene.py — Blender smoke test for the blend3d library.
# Builds a tiny scene from library components (2 blocky characters, block floor,
# torch, lights, camera) and renders 6 preview frames at low resolution.
# Run: blender -b -P smoke_scene.py -- preview
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + r'\..\blender_lib')
from core import *
from blocks import build_palette, cube_block, torch, star_sky
from chars import STEVE, ALEX, build_blocky_char, apply_emote
import production as PR

sc = reset_scene()
build_palette()
PR.sky_gradient('1B2450', '131C46', '060A1E', strength=0.5)
star_sky(60)

for i in range(-2, 3):
    for j in range(-2, 3):
        cube_block(f'fl{i}_{j}', (i, j, -0.5), MATS['mc_stone_dark'][0])
torch('t1', (1.9, 1.9, 0.9), scale=1.0)
steve = build_blocky_char(STEVE, 'steve', P(-0.9, 0.4, 0.0), heading=DEG(35))
alex = build_blocky_char(ALEX, 'alex', P(0.9, -0.4, 0.0), heading=DEG(-145))
PR.add_light('fill', 'AREA', 30, hx('8FA0C8'), (0, 0, 2.4), size=4.0)
PR.add_light('warm', 'POINT', 12, hx('FFB45A'), (1.6, 1.4, 1.3), size=0.2)

F_END = 6
for f in range(1, F_END + 1):
    step = (f - 1) / (F_END - 1)
    kfV(steve['root'], 'location', f, P(-0.9 + 0.5 * step, 0.4, 0))
    kfV(steve['armR'], 'rotation_euler', f, Euler((math.sin(f * 1.2) * 0.4, 0, 0)))
    kfV(alex['root'], 'location', f, P(0.9 - 0.3 * step, -0.4, 0))
    apply_emote(steve, 'happy', 1.0)
    apply_emote(alex, 'neutral', 0.8)
cam_data = bpy.data.cameras.new('cam'); cam_data.lens = 28
cam = bpy.data.objects.new('cam', cam_data); link(cam)
sc.camera = cam
ctr = P(0.2, 0, 1.0)
cam.location = ctr + P(-2.2, -2.8, 1.1)
cam.rotation_euler = (ctr - cam.location).to_track_quat('-Z', 'Y').to_euler()

PR.render_cfg(F_END, out_base=r'D:\Видео_Work\minecraft\smoke',
              engine='BLENDER_EEVEE', samples=32)
sc.render.resolution_x = 540
sc.render.resolution_y = 960
sc.view_settings.exposure = 0.45
mode = 'preview'
if '--' in sys.argv:
    rest = sys.argv[sys.argv.index('--') + 1:]
    if rest and rest[0] in ('preview',): mode = rest[0]
if mode == 'preview':
    sc.render.filepath = r'D:\Видео_Work\minecraft\renders\smoke####'
    for f in range(1, F_END + 1):
        sc.frame_start = f; sc.frame_end = f
        bpy.ops.render.render(animation=True)
        log(f'smoke frame {f} done')
print('SMOKE_LOG_START')
for l in LOG: print('SMOKE:', l)
print('SMOKE_LOG_END')
