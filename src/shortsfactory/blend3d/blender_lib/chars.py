# blend3d/blender_lib/chars.py — parameterized blocky humanoid rig (Minecraft-style).
# textured=True applies pixel-art skin textures (face/hair/body/limbs) from presets.
from core import *
from textures import tex_mat, box_uv

STEVE = dict(skin='C98F6B', shirt='00A8A8', pants='4A4AC4', shoes='4C4C4C',
             hair='5A3A22', hair_style='steve', pupil='523DA3', slim=False)
ALEX = dict(skin='E0A882', shirt='6FA84E', pants='9A8B6F', shoes='5E5244',
            hair='E08A2E', hair_style='alex', pupil='4E8C3A', slim=True)

FACES6 = ('top', 'bottom', 'north', 'south', 'east', 'west')
# character faces -Y at rotZ=0 → the face texture goes on the 'south' face

def _apply_box_faces(obj, tex_by_face, rough=0.75):
    mesh = obj.data
    for face in FACES6:
        mesh.materials.append(tex_mat(tex_by_face[face], rough=rough))
    mesh.uv_layers.new(name='boxuv')
    for poly in mesh.polygons:
        n = poly.normal
        face = ('top' if n.z > 0.5 else 'bottom' if n.z < -0.5 else
                'north' if n.y > 0.5 else 'south' if n.y < -0.5 else
                'east' if n.x > 0.5 else 'west')
        mm = tex_mat(tex_by_face[face], rough=rough)
        for mi, material in enumerate(mesh.materials):
            if material == mm:
                poly.material_index = mi
                break
    box_uv(obj, per_face_fit=True)

def _char_mats(cfg, tag):
    for key in ('skin', 'shirt', 'pants', 'shoes', 'hair'):
        mat(f'ch_{tag}_{key}', hx(cfg[key]), rough=0.7)
    mat(f'ch_{tag}_pupil', hx(cfg['pupil']), rough=0.35)
    mat(f'ch_{tag}_eye_w', hx('FFFFFF'), rough=0.3, es=1.4)
    mat(f'ch_{tag}_mouth', hx('3A2A28'), rough=0.4)

def build_blocky_char(cfg, name, loc, heading=0.0, textured=True):
    """Blocky humanoid, 1.98 m, faces -Y at heading=0. Returns rig dict.
    textured=True: pixel-art skins from presets/textures (face on -Y head face)."""
    tag = name
    _char_mats(cfg, tag)
    M = lambda k: MATS[f'ch_{tag}_{k}'][0]
    tx = cfg.get('textured', True)
    T = lambda part: f'{tag}_{part}'

    root = new_empty(name + '_root', loc)
    root.rotation_euler = (0, 0, heading)

    # legs: pivot at hip z 0.75
    legL = new_empty(name + '_legL', (0.125, 0, 0.75), parent=root)
    legR = new_empty(name + '_legR', (-0.125, 0, 0.75), parent=root)
    for side, piv in (('L', legL), ('R', legR)):
        leg = box(f'{name}_leg{side}', (0, 0, -0.375), (0.24, 0.24, 0.72), M('pants'), bevel=0.006)
        parent(leg, piv)
        if tx: _apply_box_faces(leg, {f: T('leg') for f in FACES6})
        shoe = box(f'{name}_shoe{side}', (0, 0.015, -0.66), (0.26, 0.27, 0.2), M('shoes'), bevel=0.01)
        parent(shoe, piv)
        if tx: _apply_box_faces(shoe, {f: T('shoe') for f in FACES6})

    # torso on body_p (pivot z 0.78)
    body_p = new_empty(name + '_bodyP', (0, 0, 0.78), parent=root)
    body = box(name + '_body', (0, 0, 0.345), (0.5, 0.25, 0.78), M('shirt'), bevel=0.008)
    parent(body, body_p)
    if tx: _apply_box_faces(body, {f: T('body') for f in FACES6})

    # arms
    aw = 0.19 if cfg['slim'] else 0.25
    ax = 0.25 + aw / 2 + 0.01
    armL = new_empty(name + '_armL', (ax, 0, 0.58), parent=body_p)
    armR = new_empty(name + '_armR', (-ax, 0, 0.58), parent=body_p)
    for side, piv in (('L', armL), ('R', armR)):
        arm = box(f'{name}_arm{side}', (0, 0, -0.34), (aw, 0.24, 0.72), M('skin'), bevel=0.006)
        parent(arm, piv)
        if tx: _apply_box_faces(arm, {f: T('arm') for f in FACES6})
        hand = box(f'{name}_hand{side}', (0, 0, -0.735), (aw, 0.24, 0.14), M('skin'), bevel=0.01)
        parent(hand, piv)
        if tx: _apply_box_faces(hand, {f: T('arm') for f in FACES6})

    # head: pivot at neck (world z 1.51)
    head_p = new_empty(name + '_headP', (0, 0, 0.73), parent=body_p)
    head = box(name + '_head', (0, 0, 0.25), (0.5, 0.5, 0.5), M('skin'), bevel=0.01)
    parent(head, head_p)

    eyeL = eyeR = pupL = pupR = None
    if tx:
        _apply_box_faces(head, {'south': T('face'), 'north': T('head_back'),
                                'top': T('head_top'), 'bottom': T('head_side'),
                                'east': T('head_side'), 'west': T('head_side')})
    else:
        eyeL = box(name + '_eyeL', (0.115, -0.26, 0.05), (0.13, 0.05, 0.10), M('eye_w'))
        eyeR = box(name + '_eyeR', (-0.115, -0.26, 0.05), (0.13, 0.05, 0.10), M('eye_w'))
        pupL = box(name + '_pupL', (0.115, -0.272, 0.05), (0.065, 0.05, 0.10), M('pupil'))
        pupR = box(name + '_pupR', (-0.115, -0.272, 0.05), (0.065, 0.05, 0.10), M('pupil'))
        for o in (eyeL, eyeR, pupL, pupR): parent(o, head)

    # brows (animation) + mouth thread — always geometric, sit above/below painted eyes
    browL = box(name + '_browL', (0.115, -0.262, 0.145), (0.15, 0.06, 0.045), M('hair'))
    browR = box(name + '_browR', (-0.115, -0.262, 0.145), (0.15, 0.06, 0.045), M('hair'))
    # mouth: a thin cartoon "thread" — overlapping spheres along the smile arc.
    # The mouth empty carries the rig handle so emotions still scale the whole
    # mouth, but the shape is a rounded line, not a square plate.
    mat(f'ch_{tag}_lip', hx('B24A4A'), rough=0.35)
    mouth = new_empty(name + '_mouth', (0, -0.26, -0.12), parent=head)
    for i, (dx, dy, dz) in enumerate((
            (-0.085, -0.010, 0.016),
            (-0.043, -0.002, 0.006),
            (0.000, 0.004, 0.000),
            (0.043, -0.002, 0.006),
            (0.085, -0.010, 0.016))):
        s = sphere(f'{name}_mouth_{i}', (dx, dy, dz), 0.018, M('lip'),
                   scale=(1.25, 0.85, 0.85), seg=10)
        parent(s, mouth)
    for o in (browL, browR): parent(o, head)
    if tx:
        _apply_box_faces(browL, {f: T('hair') for f in FACES6})
        _apply_box_faces(browR, {f: T('hair') for f in FACES6})

    # hair for flat mode
    if not tx:
        if cfg['hair_style'] == 'steve':
            parent(box(name + '_hair_top', (0, 0, 0.2), (0.54, 0.54, 0.16), M('hair'), bevel=0.01), head)
            parent(box(name + '_hair_fr', (0, -0.245, 0.16), (0.54, 0.1, 0.16), M('hair')), head)
            parent(box(name + '_hair_bk', (0, 0.24, 0.03), (0.54, 0.1, 0.4), M('hair')), head)
        else:
            parent(box(name + '_hair_top', (0, 0.02, 0.2), (0.56, 0.56, 0.18), M('hair'), bevel=0.01), head)
            parent(box(name + '_hair_bk', (0, 0.24, -0.07), (0.56, 0.14, 0.64), M('hair')), head)
            parent(box(name + '_tail', (0, 0.38, -0.23), (0.22, 0.26, 0.52), M('hair'), bevel=0.02), head)
            parent(box(name + '_strL', (0.25, -0.06, -0.03), (0.1, 0.1, 0.44), M('hair')), head)
            parent(box(name + '_strR', (-0.25, -0.06, -0.03), (0.1, 0.1, 0.44), M('hair')), head)

    handR_p = new_empty(name + '_handRP', (0, 0, -0.72), parent=armR)
    handL_p = new_empty(name + '_handLP', (0, 0, -0.72), parent=armL)

    base_scales = {}
    for k in ('eyeL', 'eyeR', 'pupL', 'pupR', 'browL', 'browR', 'mouth'):
        o = bpy.data.objects.get(name + '_' + k)
        if o is not None: base_scales[k] = Vector(o.scale)

    return dict(name=name, root=root, body=body, body_p=body_p, head_p=head_p, head=head,
                armL=armL, armR=armR, legL=legL, legR=legR,
                eyeL=eyeL, eyeR=eyeR, pupL=pupL, pupR=pupR, browL=browL, browR=browR,
                mouth=mouth, handR=handR_p, handL=handL_p, cfg=cfg, textured=tx,
                base_scales=base_scales)

EMOTES = {
    'neutral':  dict(brow_z=(0, 0), brow_up=0.0, eye=1.0, mouth=(1.0, 1.0)),
    'happy':    dict(brow_z=(-6, 6), brow_up=0.012, eye=1.05, mouth=(1.35, 1.5)),
    'soft':     dict(brow_z=(-3, 3), brow_up=0.006, eye=1.02, mouth=(1.2, 1.3)),
    'scared':   dict(brow_z=(13, -13), brow_up=0.01, eye=1.3, mouth=(0.55, 2.2)),
    'worried':  dict(brow_z=(9, -9), brow_up=0.004, eye=1.15, mouth=(0.8, 1.5)),
    'determ':   dict(brow_z=(-11, 11), brow_up=-0.012, eye=0.95, mouth=(1.0, 0.8)),
    'sheepish': dict(brow_z=(7, -1), brow_up=0.002, eye=0.98, mouth=(0.75, 1.1)),
}

def apply_emote(rig, emote, k=1.0):
    e = EMOTES.get(emote, EMOTES['neutral'])
    n = rig['name']
    for obj_key, ang in (('browL', e['brow_z'][0]), ('browR', e['brow_z'][1])):
        o = bpy.data.objects.get(n + '_' + obj_key)
        if o is None: continue
        o.rotation_euler = (0, 0, DEG(ang) * k)
        o.location.z = 0.145 + e['brow_up'] * k
    for obj_key in ('eyeL', 'eyeR', 'pupL', 'pupR'):
        o = bpy.data.objects.get(n + '_' + obj_key)
        if o is None: continue
        s = rig['base_scales'].get(obj_key)
        if s is None: continue
        o.scale = (s.x * lerp(1.0, e['eye'], k), s.y, s.z * lerp(1.0, e['eye'], k))
    m = bpy.data.objects.get(n + '_mouth')
    if m is not None:
        ms = rig['base_scales'].get('mouth')
        if ms is not None:
            m.scale = (ms.x * lerp(1.0, e['mouth'][0], k), ms.y, ms.z * lerp(1.0, e['mouth'][1], k))
