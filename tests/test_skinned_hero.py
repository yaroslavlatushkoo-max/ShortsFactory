"""Mission 16 — skinned continuous-mesh rig tests.

Unit tests (pure Python, no Blender) cover the skeleton graph, the mesh
weights, the analytic leg IK and the locomotion timeline.  The integration
test renders a short clip in background Blender and asserts the *measured*
deformation, pose accuracy, foot lock and non-blank frames.
"""

from __future__ import annotations

import json
import math
import os

import pytest

from shortsfactory.generative.animation_pipeline import (
    MILO,
    NINA,
    BONES,
    BONE_BY_NAME,
    BONE_LEN,
    BONE_ORDER,
    Gait,
    PoseParams,
    SkinnedResult,
    EMOTIONS,
    PLAN_ACTION_STATES,
    PLAN_OVERLAY_KEYS,
    build_character_mesh,
    build_skinned_payload,
    build_skinned_script,
    default_timeline,
    encode_frames,
    path_constant,
    render_skinned,
    solve_pose,
    stance_windows,
    timeline_from_actions,
    viseme_track,
    apply_visemes,
    VISEMES,
    vlen,
    vsub,
)

BLENDER = r"D:\Blender\blender.exe"


def _solved(params: PoseParams | None = None):
    return solve_pose(params or PoseParams())


# ---------------------------------------------------------------- skeleton --


def test_bone_graph_is_connected_and_named():
    names = [b.name for b in BONES]
    assert len(names) == len(set(names)) == 24
    for bone in BONES:
        if bone.parent:
            assert bone.parent in names
    for required in ("hips", "spine", "chest", "neck", "head", "upper_arm_L",
                     "forearm_R", "hand_L", "thigh_L", "shin_R", "foot_L"):
        assert required in names


def test_bone_lengths_are_preserved_by_the_solver():
    solved = _solved()
    for name in BONE_ORDER:
        head = solved.heads[name]
        tail = solved.tail(name)
        assert vlen(vsub(tail, head)) == pytest.approx(BONE_LEN[name], abs=1e-6)


def test_connected_limb_bones_chain_exactly():
    solved = _solved()
    for child in ("forearm_L", "hand_L", "forearm_R", "hand_R",
                  "shin_L", "foot_L", "shin_R", "foot_R"):
        parent = BONE_BY_NAME[child].parent
        gap = vlen(vsub(solved.heads[child], solved.tail(parent)))
        assert gap < 1e-6, f"{child} is detached from {parent}"


# -------------------------------------------------------------------- mesh --


@pytest.mark.parametrize("spec", [MILO, NINA], ids=["milo", "nina"])
def test_mesh_is_well_formed_and_skinned(spec):
    mb = build_character_mesh(spec)
    assert len(mb.verts) > 600
    assert len(mb.faces) > 500
    assert len(mb.face_mats) == len(mb.faces)
    assert all(len(f) >= 3 for f in mb.faces)
    assert all(0 <= i < len(mb.verts) for f in mb.faces for i in f)
    bones = set(BONE_ORDER)
    for weights in mb.weights:
        assert abs(sum(weights.values()) - 1.0) < 1e-6
        assert set(weights) <= bones
    # every bone that should deform the body owns geometry
    used = {b for w in mb.weights for b in w}
    for required in ("hips", "spine", "chest", "head", "upper_arm_L",
                     "forearm_L", "hand_L", "thigh_L", "shin_L", "foot_L"):
        assert required in used, f"no vertices weighted to {required}"
    zs = [v[2] for v in mb.verts]
    assert 1.60 < max(zs) < 1.95
    assert -0.03 < min(zs) < 0.06


def test_characters_keep_their_identity_colours():
    assert MILO.top == (0.09, 0.55, 0.50)          # teal hoodie
    assert MILO.shoes == (0.98, 0.82, 0.18)        # yellow sneakers
    assert MILO.hair_style == "spikes"
    assert NINA.top == (0.95, 0.45, 0.16)          # orange dress
    assert NINA.bottom == (0.42, 0.24, 0.60)       # purple tights
    assert NINA.skirt and NINA.glasses and NINA.freckles
    assert NINA.hair_style == "bun"


def test_skirt_and_glasses_exist_only_for_nina():
    milo = build_character_mesh(MILO)
    nina = build_character_mesh(NINA)
    assert len(nina.verts) > len(milo.verts)


# ---------------------------------------------------------------------- IK --


def test_ik_reaches_world_ankle_targets():
    params = PoseParams(root=(0.3, -0.2, 0.585), yaw=0.7,
                        ankle_l=(0.25, -0.15, 0.09), ankle_r=(0.32, -0.25, 0.14))
    solved = solve_pose(params)
    assert vlen(vsub(solved.heads["foot_L"], params.ankle_l)) < 1e-6
    assert vlen(vsub(solved.heads["foot_R"], params.ankle_r)) < 1e-6


def test_ik_clamps_instead_of_exploding_when_target_is_unreachable():
    solved = solve_pose(PoseParams(ankle_l=(0.9, 0.0, 0.09)))
    hip = solved.heads["thigh_L"]
    d = vlen(vsub(solved.heads["shin_L"], hip))
    assert BONE_LEN["thigh_L"] - 1e-6 <= d <= BONE_LEN["thigh_L"] + 1e-6
    assert vlen(vsub(solved.heads["foot_L"], hip)) <= BONE_LEN["thigh_L"] + BONE_LEN["shin_L"] + 1e-6


def test_knees_bend_forward():
    solved = solve_pose(PoseParams(root=(0.0, 0.0, 0.53)))
    for side in ("L", "R"):
        knee = solved.heads[f"shin_{side}"]
        hip = solved.heads[f"thigh_{side}"]
        ankle = solved.heads[f"foot_{side}"]
        assert knee[1] < (hip[1] + ankle[1]) / 2.0  # -Y is character forward


# ------------------------------------------------------------------- gaits --


def test_planted_foot_keeps_exact_world_position():
    gait = Gait(path=path_constant((0.0, 0.0, 0.585), math.pi / 2, 0.75), speed=0.75, stride=0.58)
    prev = None
    for i in range(150):
        pos, _yaw, planted, _w = gait.foot(i / 30.0, +1.0, 0.0)
        if prev is not None and planted and prev[1]:
            assert vlen(vsub(pos, prev[0])) < 1e-9
        prev = (pos, planted)


def test_gait_translates_the_body_using_its_own_speed():
    path = path_constant((0.0, 0.0, 0.585), math.pi / 2, 0.75)
    moved = vlen(vsub(path(2.0)[0], path(0.0)[0]))
    assert moved == pytest.approx(1.5, abs=1e-6)


# ---------------------------------------------------------------- timeline --


@pytest.mark.parametrize("scenario", ["states", "walk"])
def test_timeline_has_all_states_and_stays_continuous(scenario):
    timeline = default_timeline(scenario)
    frames = timeline.sample_frames(30)
    assert len(frames) > 200
    states = [s.state for s in timeline.segments]
    if scenario == "walk":
        for required in ("idle", "start", "walk", "stop", "turn_in_place"):
            assert required in states
    else:
        for required in ("idle", "reach", "gesture", "react", "talk",
                         "interact", "turn_in_place"):
            assert required in states
    # no per-frame teleports
    for a, b in zip(frames, frames[1:]):
        assert vlen(vsub(a.root, b.root)) < 0.06
        for side in ("l", "r"):
            step = vlen(vsub(getattr(a, f"ankle_{side}"), getattr(b, f"ankle_{side}")))
            assert step < 0.14, side
    # and every limb really moves
    solved = [solve_pose(f) for f in frames[::4]]
    for bone in ("head", "hand_L", "foot_L"):
        pts = [s.tail(bone) for s in solved]
        extent = max(vlen(vsub(p, q)) for p in pts[::4] for q in pts[::4])
        assert extent > 0.05, bone


def test_state_boundaries_do_not_teleport_the_feet():
    timeline = default_timeline("walk")
    frames = timeline.sample_frames(30)
    bounds = set()
    acc = 0.0
    for seg in timeline.segments[:-1]:
        acc += seg.duration
        bounds.add(int(round(acc * 30)))
    for i, (a, b) in enumerate(zip(frames, frames[1:])):
        if i in bounds or i + 1 in bounds:
            for side in ("l", "r"):
                step = vlen(vsub(getattr(a, f"ankle_{side}"), getattr(b, f"ankle_{side}")))
                assert step < 0.06, (i, side, step)


def test_encoded_rotations_are_orthonormal():
    frames = default_timeline("states").sample_frames(30)[::25]
    encoded = encode_frames(frames)
    assert len(encoded) == len(frames) > 8
    for item in encoded:
        assert set(item) == set(BONE_ORDER)
        for name, vals in item.items():
            assert len(vals) == 12
            r = vals[3:]
            # row-major 3x3: rows must be unit length and mutually orthogonal
            rows = [r[0:3], r[3:6], r[6:9]]
            for row in rows:
                assert abs(vlen(tuple(row)) - 1.0) < 1e-5
            for i in range(3):
                for j in range(i + 1, 3):
                    dot = sum(rows[i][k] * rows[j][k] for k in range(3))
                    assert abs(dot) < 1e-5


def test_stance_windows_are_detected():
    frames = default_timeline("walk").sample_frames(30)
    windows = stance_windows(frames, 30)
    assert len(windows) >= 6
    assert all(w["end"] > w["start"] for w in windows)


# --------------------------------------------------------------- artefacts --


def test_payload_contains_a_skinned_rig(tmp_path):
    path, payload = build_skinned_payload(tmp_path, scenario="states", character="nina")
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["character"] == "nina"
    assert len(data["bones"]) == 24
    assert len(data["verts"]) == len(data["weights"])
    assert data["frames"] and len(data["frames"][0]) == 24
    assert data["materials"]


def test_script_generates_and_compiles(tmp_path):
    script = build_skinned_script(tmp_path, scenario="walk", width=180, height=320)
    src = script.read_text(encoding="utf-8")
    for marker in ("SKINNED_OK", "matrix_basis", "ARMATURE", "view_transform",
                   "max_drift", "min_std"):
        assert marker in src
    compile(src, str(script), "exec")


@pytest.mark.integration
def test_skinned_render_deforms_mesh_and_locks_feet(tmp_path):
    if not os.path.exists(BLENDER):
        pytest.skip("Blender not found")
    os.environ["BLENDER_EXECUTABLE"] = BLENDER
    script = build_skinned_script(tmp_path, scenario="walk", character="milo",
                                  width=180, height=320)
    res = render_skinned(script, timeout=1800)
    assert res.status == "OK", res.error
    assert (tmp_path / "final.mp4").exists()
    d = res.diagnostics or {}
    assert d.get("pose_error", {}).get("passed") is True
    motion = d.get("mesh_motion", {})
    for region in ("head", "arms", "legs", "torso"):
        assert motion.get(region, {}).get("max_disp", 0) > 0.10, region
    foot = d.get("foot_contact", {})
    assert foot.get("windows", 0) >= 4
    assert foot.get("max_drift", 1.0) < 0.03
    assert foot.get("max_penetration", 1.0) < 0.02
    blank = d.get("blank_frames", {})
    assert blank.get("checked", 0) > 0
    assert blank.get("blank", 1) == 0
    assert blank.get("min_std", 0.0) > 0.02


# --------------------------------------------------------------- dispatcher --


def _plan_with(character_id: str, action: str):
    from shortsfactory.generative.animation_plan import (
        AnimationPlan,
        PlanAction,
        PlanCameraKey,
        PlanCharacter,
    )

    return AnimationPlan(
        scene_id="skinned_test",
        duration=2.0,
        characters=[PlanCharacter(id=character_id)],
        actions=[PlanAction(character=character_id, action=action, start=0.0, duration=1.0)],
        camera=[PlanCameraKey(t=0.0)],
    )


def test_dispatcher_picks_character_and_scenario():
    from shortsfactory.generative.engine_dispatcher import _skinned_actions, _skinned_targets

    milo_plan = _plan_with("milo", "wave")
    assert _skinned_targets(milo_plan) == ("milo", "milo")
    assert [a["action"] for a in _skinned_actions(milo_plan, "milo")] == ["wave"]
    assert _skinned_targets(_plan_with("nina", "walk")) == ("nina", "nina")


def test_dispatcher_skinned_engine_reports_diagnostics(tmp_path, monkeypatch):
    from shortsfactory.generative import engine_dispatcher as disp

    calls = {}

    def fake_render(script, timeout=0):
        calls["script"] = str(script)
        return SkinnedResult(
            status="OK",
            video="v.mp4",
            script="s.py",
            render_dir="rd",
            diagnostics={
                "pose_error": {"passed": True, "max_abs_matrix_diff": 3e-06},
                "foot_contact": {"windows": 17, "max_drift": 0.01724, "max_penetration": 0.00285},
                "blank_frames": {"checked": 13, "blank": 0},
            },
        )

    monkeypatch.setattr(disp, "blender_available", lambda: True)
    monkeypatch.setattr(disp, "render_skinned", fake_render)

    res = disp.render_plan(_plan_with("milo", "walk"), None, tmp_path, engine="skinned")
    assert res.status == "OK"
    assert res.engine_used == "skinned"
    assert res.video == "v.mp4"
    assert res.blender_script == "s.py"
    assert any("milo" in note for note in res.notes)
    assert any("Пустых кадров 0" in note for note in res.notes)
    # the script really was generated on disk before rendering
    assert (tmp_path / "skinned").is_dir()


def test_dispatcher_skinned_requires_blender(tmp_path, monkeypatch):
    from shortsfactory.generative import engine_dispatcher as disp

    monkeypatch.setattr(disp, "blender_available", lambda: False)
    monkeypatch.setattr(
        disp,
        "render_skinned",
        lambda *a, **k: pytest.fail("must not render without Blender"),
    )
    res = disp.render_plan(_plan_with("milo", "walk"), None, tmp_path, engine="skinned")
    assert res.status == "PARTIAL"
    assert res.engine_used == "skinned"
    assert any("Blender" in note for note in res.notes)


# ------------------------------------------------------------- lip-sync --


def test_viseme_track_covers_line_and_returns_to_rest():
    track = viseme_track("Привет, мир!", start=1.0, duration=2.0)
    assert track[0][0] == 1.0
    assert track[-1] == (3.0, "rest")
    shapes = {name for _, name in track}
    assert len(shapes) >= 3, shapes
    assert all(name in VISEMES for name in shapes)
    # strictly increasing times
    times = [t for t, _ in track]
    assert times == sorted(times) and len(times) == len(set(times))


def test_viseme_track_rejects_empty_duration():
    with pytest.raises(ValueError):
        viseme_track("a", duration=0.0)


def test_viseme_track_keeps_the_mouth_shut_between_speech_segments():
    track = viseme_track(
        "мама мыла раму", start=1.0, duration=2.0, segments=[[0.2, 0.7], [1.3, 1.8]]
    )
    times = [t for t, _ in track]
    assert times == sorted(times) and len(times) == len(set(times))
    # a rest is placed at the end of each spoken span and at the line end
    assert (1.7, "rest") in track and (2.8, "rest") in track and (3.0, "rest") in track
    # nothing but silence happens between the two spans
    assert [v for t, v in track if 1.7 < t < 2.3] in ([], ["rest"])
    # the first viseme starts no earlier than the first spoken span
    assert track[0][0] >= 1.2 - 1e-9


def test_apply_visemes_uses_the_loudness_envelope():
    fps = 15
    frames = default_timeline("states").sample_frames(fps)
    line = {"start": 0.0, "duration": 2.0, "text": "мама мыла раму"}
    quiet = dict(line, envelope=[0.0] * 100, envelope_hz=50.0)
    loud = dict(line, envelope=[1.0] * 100, envelope_hz=50.0)
    silent_jaw = max(f.jaw_open for f in apply_visemes(frames[:30], fps=fps, lines=[quiet]))
    loud_jaw = max(f.jaw_open for f in apply_visemes(frames[:30], fps=fps, lines=[loud]))
    assert silent_jaw == 0.0, "a silent take still moved the jaw"
    assert loud_jaw > 0.2, loud_jaw


def test_apply_visemes_closes_the_jaw_in_an_internal_pause():
    fps = 15
    frames = default_timeline("states").sample_frames(fps)
    envelope = [1.0] * 20 + [0.0] * 30 + [1.0] * 60  # loud, silence, loud
    line = {
        "start": 0.0,
        "duration": 2.0,
        "text": "мама мыла раму",
        "envelope": envelope,
        "envelope_hz": 50.0,
    }
    out = apply_visemes(frames[:30], fps=fps, lines=[line])
    pause = [f.jaw_open for f in out[int(0.45 * fps):int(0.95 * fps)]]
    assert max(pause) == 0.0, pause


def test_apply_visemes_only_touches_spoken_frames():
    frames = default_timeline("states").sample_frames(15)
    silent = apply_visemes(frames, fps=15, lines=[])
    assert silent == frames
    spoken = apply_visemes(
        frames, fps=15, lines=[{"start": 1.0, "duration": 1.5, "text": "мама"}]
    )
    assert len(spoken) == len(frames)
    # before the line the mouth stays closed
    for frame in spoken[:14]:
        assert frame.jaw_open == 0.0 and frame.mouth_wide == 0.0
    window = spoken[15:37]
    assert max(f.jaw_open for f in window) > 0.2
    assert max(f.mouth_wide for f in window) > 0.05
    # "мама" is all bilabial + open vowels: the mouth still closes on "м"
    assert min(f.jaw_open for f in window) < 0.05
    # the rest of the body is untouched
    assert window[0].root == frames[15].root
    assert window[0].arm_l_pitch == frames[15].arm_l_pitch


def test_payload_with_voice_lines_moves_the_jaw(tmp_path):
    quiet = build_skinned_payload(
        tmp_path / "quiet", scenario="plan", character="milo", fps=15,
        width=180, height=320,
        actions=[{"action": "talk", "start": 0.0, "duration": 2.0}],
        duration=2.0,
    )[1]
    loud = build_skinned_payload(
        tmp_path / "loud", scenario="plan", character="milo", fps=15,
        width=180, height=320,
        actions=[{"action": "talk", "start": 0.0, "duration": 2.0}],
        duration=2.0, voice_lines=[{"start": 0.2, "duration": 1.4, "text": "мама мыла раму"}],
    )[1]
    jaw = "jaw"
    silent_frames = quiet["frames"]
    spoken_frames = loud["frames"]
    differing = [
        i
        for i, (a, b) in enumerate(zip(silent_frames, spoken_frames))
        if a[jaw] != b[jaw]
    ]
    assert differing, "voice lines must rotate the jaw bone"
    # the body animation is identical: only the mouth channels were overridden
    for i in differing:
        assert silent_frames[i]["head"] == spoken_frames[i]["head"]
    # a closed mouth and an open mouth must not share the jaw rotation
    assert len({tuple(spoken_frames[i][jaw][3:]) for i in differing}) >= 3


def test_dispatcher_estimates_voice_line_timing():
    from shortsfactory.generative.engine_dispatcher import _skinned_voice_lines

    plan = _plan_with("milo", "talk")
    plan.voice_lines = [type("L", (), {"character": "milo", "text": "мама мыла раму", "start": 0.5})()]
    lines = _skinned_voice_lines(plan, "milo", limit=plan.duration)
    assert len(lines) == 1
    assert lines[0]["start"] == 0.5
    assert 0.6 <= lines[0]["duration"] <= plan.duration - 0.5


# ---------------------------------------------------- plan-driven timeline --


def test_timeline_from_actions_matches_plan_timing():
    timeline = timeline_from_actions(
        [
            {"action": "idle", "start": 0.0, "duration": 1.0},
            {"action": "walk", "start": 1.0, "duration": 2.0},
            {"action": "wave", "start": 3.0, "duration": 1.5},
        ],
        duration=5.0,
    )
    assert abs(timeline.duration - 5.0) < 1e-6
    states = [segment.state for segment in timeline.segments]
    assert states[0] == "idle"
    assert "start" in states and "walk" in states and "stop" in states
    assert "wave" in states
    # gaps never fall through: consecutive segments start where the last ended
    total = sum(segment.duration for segment in timeline.segments)
    assert abs(total - timeline.duration) < 1e-9


def test_timeline_from_actions_pads_short_plans():
    timeline = timeline_from_actions(
        [{"action": "walk", "start": 0.0, "duration": 1.2}], duration=4.0
    )
    assert abs(timeline.duration - 4.0) < 1e-6
    assert timeline.segments[-1].state == "idle"


def test_timeline_from_actions_rejects_unknown_verbs():
    with pytest.raises(ValueError, match="не поддерживается"):
        timeline_from_actions([{"action": "backflip", "start": 0.0, "duration": 1.0}])
    with pytest.raises(ValueError):
        timeline_from_actions([])


def test_timeline_from_actions_keeps_walk_continuous():
    timeline = timeline_from_actions(
        [{"action": "walk", "start": 0.5, "duration": 3.0}], duration=4.0
    )
    frames = timeline.sample_frames(30)
    windows = stance_windows(frames, 30)
    assert len(windows) >= 4, "plan-driven walk must contain planted-foot windows"
    worst = 0.0
    for window in windows:
        key = "ankle_l" if window["side"] == "l" else "ankle_r"
        for frame in frames[window["start"] : window["end"] + 1]:
            target = getattr(frame, key)
            if target is None:
                continue  # IK-driven pose: foot is solved, not scripted
            drift = math.dist(target, window["pos"])
            worst = max(worst, drift)
    assert worst < 0.05, f"planted foot slid {worst:.4f} m"


def test_payload_records_plan_actions(tmp_path):
    actions = [
        {"action": "idle", "start": 0.0, "duration": 1.0},
        {"action": "walk", "start": 1.0, "duration": 1.5},
    ]
    _, payload = build_skinned_payload(
        tmp_path, scenario="plan", character="milo", fps=15,
        width=180, height=320, actions=actions, duration=2.5,
    )
    assert payload["timeline_duration"] > 0
    assert payload["actions"] == [
        {"action": "idle", "start": 0.0, "duration": 1.0},
        {"action": "walk", "start": 1.0, "duration": 1.5},
    ]
    assert len(payload["frames"]) == round(payload["timeline_duration"] * 15)


# --------------------------------------------------------------- emotions --


def test_emotions_change_pose_and_face():
    base = solve_pose(PoseParams())
    poses = {
        name: solve_pose(PoseParams(**{k: v for k, v in delta.items() if k != "root_dz"}))
        for name, delta in EMOTIONS.items()
    }
    assert poses["happy"].heads["head"][2] != poses["sad"].heads["head"][2]
    assert poses["angry"].rots["brow_l"] != poses["sad"].rots["brow_l"]
    assert poses["surprised"].rots["brow_l"] != base.rots["brow_l"]
    assert all(EMOTIONS[name] for name in EMOTIONS if name != "neutral")


def test_emotion_verb_reaches_the_timeline():
    timeline = timeline_from_actions(
        [
            {"action": "idle", "start": 0.0, "duration": 0.5},
            {"action": "angry", "start": 0.5, "duration": 1.0},
        ],
        duration=1.5,
        fps=10,
    )
    frames = timeline.sample_frames(10)
    assert frames[0].brow_tilt == 0.0
    assert max(frame.brow_tilt for frame in frames) > 0.4


def test_dispatcher_forwards_emotion_params():
    from shortsfactory.generative.animation_plan import PlanAction
    from shortsfactory.generative.engine_dispatcher import _skinned_actions

    plan = _plan_with("milo", "walk")
    plan.actions = [
        PlanAction(
            character="milo",
            action="walk",
            start=0.0,
            duration=1.5,
            params={"emotion": "happy", "emotion_intensity": 0.5},
        )
    ]
    actions = _skinned_actions(plan, "milo")
    assert actions[0]["params"]["emotion"] == "happy"
    assert actions[0]["params"]["emotion_intensity"] == 0.5


def test_unknown_emotion_is_an_error():
    with pytest.raises(ValueError, match="Неизвестная эмоция"):
        timeline_from_actions(
            [
                {
                    "action": "idle",
                    "start": 0.0,
                    "duration": 1.0,
                    "params": {"emotion": "smug"},
                }
            ],
            duration=1.0,
        )


# --------------------------------------------------------------- acting v2 --

A4_VERBS = (
    "look_at",
    "listen",
    "nod",
    "shake_head",
    "shrug",
    "point",
    "grab",
    "hold",
    "release",
    "scared",
    "stumble",
    "fall",
    "recover",
)

_POSE_CHANNELS = (
    "chest_pitch",
    "chest_yaw",
    "spine_pitch",
    "neck_pitch",
    "head_pitch",
    "head_yaw",
    "head_roll",
    "arm_l_pitch",
    "arm_l_abduct",
    "arm_r_pitch",
    "arm_r_abduct",
    "elbow_l",
    "elbow_r",
    "wrist_l",
    "wrist_r",
    "hips_pitch",
)


def _act_frames(verb: str, duration: float = 1.2, fps: int = 30):
    timeline = timeline_from_actions(
        [{"action": verb, "start": 0.0, "duration": duration}],
        duration=duration,
        fps=fps,
    )
    return timeline.sample_frames(fps)


def test_a4_verbs_are_wired_and_change_the_pose():
    idle = _act_frames("idle")
    ref = idle[len(idle) // 2]
    for verb in A4_VERBS:
        assert PLAN_ACTION_STATES[verb] == verb
        assert verb in PLAN_OVERLAY_KEYS
        frames = _act_frames(verb)
        assert len(frames) == round(1.2 * 30)
        mid = frames[len(frames) // 2]
        delta = max(
            abs(getattr(mid, name) - getattr(ref, name)) for name in _POSE_CHANNELS
        )
        assert delta > 0.02, (verb, delta)
        # an overlay never moves the feet: both ankles stay where they started
        first, last = frames[1].resolved(), frames[-2].resolved()
        assert first.ankle_l == pytest.approx(last.ankle_l, abs=1e-6)
        assert first.ankle_r == pytest.approx(last.ankle_r, abs=1e-6)


def test_nod_and_shake_head_really_oscillate():
    nods = [frame.head_pitch for frame in _act_frames("nod")]
    assert max(nods) - min(nods) > 0.1
    shakes = [frame.head_yaw for frame in _act_frames("shake_head")]
    assert max(shakes) - min(shakes) > 0.3


def test_fall_and_recover_move_the_root_but_not_the_feet():
    fall = _act_frames("fall")
    recover = _act_frames("recover")
    assert fall[-2].root[2] < fall[1].root[2] - 0.2
    assert recover[-2].root[2] > recover[1].root[2] + 0.15
    for frames in (fall, recover):
        first, last = frames[1].resolved(), frames[-2].resolved()
        assert first.ankle_l == pytest.approx(last.ankle_l, abs=1e-6)
        assert first.ankle_r == pytest.approx(last.ankle_r, abs=1e-6)
        assert first.root[0] == pytest.approx(last.root[0], abs=1e-6)
        assert first.root[1] == pytest.approx(last.root[1], abs=1e-6)


def test_scared_emotion_reaches_the_face():
    delta = EMOTIONS["scared"]
    assert delta["brow_lift"] > 0 and delta["jaw_open"] > 0
    frames = timeline_from_actions(
        [
            {
                "action": "idle",
                "start": 0.0,
                "duration": 1.0,
                "params": {"emotion": "scared", "emotion_intensity": 0.8},
            }
        ],
        duration=1.0,
        fps=10,
    ).sample_frames(10)
    assert max(frame.brow_lift for frame in frames) > 0
    assert max(frame.jaw_open for frame in frames) > 0


def test_emotion_does_not_break_walking():
    timeline = timeline_from_actions(
        [
            {
                "action": "walk",
                "start": 0.0,
                "duration": 2.0,
                "params": {"emotion": "happy", "emotion_intensity": 1.0},
            }
        ],
        duration=2.4,
        fps=30,
    )
    frames = timeline.sample_frames(30)
    windows = stance_windows(frames, 30)
    assert windows, "an emotional walk must still plant its feet"
    worst = 0.0
    for window in windows:
        key = "ankle_l" if window["side"] == "l" else "ankle_r"
        for frame in frames[window["start"] : window["end"] + 1]:
            target = getattr(frame, key)
            if target is None:
                continue
            worst = max(worst, math.dist(target, window["pos"]))
    assert worst < 0.05, f"emotion moved a planted foot by {worst:.4f} m"
    assert max(frame.mouth_wide for frame in frames) > 0.1


def test_emotion_ramps_instead_of_snapping():
    timeline = timeline_from_actions(
        [{"action": "sad", "start": 0.0, "duration": 1.0}], duration=1.0, fps=30
    )
    frames = timeline.sample_frames(30)
    values = [frame.head_pitch for frame in frames]
    steps = [abs(b - a) for a, b in zip(values, values[1:])]
    assert max(steps) < 0.05, f"emotion snapped by {max(steps):.3f} rad in one frame"
