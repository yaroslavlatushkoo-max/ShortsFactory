"""Blocky (Minecraft-style) character tests — pure Python, no Blender."""

import copy
import json
import math
import wave
from pathlib import Path

import pytest

from shortsfactory.generative.animation_pipeline import blocky_hero as bh
from shortsfactory.generative.animation_pipeline import gestures
from shortsfactory.generative.animation_pipeline import skinned_hero as sh
from shortsfactory.generative.animation_pipeline.skinned_hero import (
    BONES,
    CHARACTERS,
    PoseParams,
    timeline_from_actions,
)


def test_blocky_characters_registered():
    for key in ("steve", "alex"):
        assert key in CHARACTERS
        assert CHARACTERS[key].style == "blocky"
    assert CHARACTERS["steve"].top != CHARACTERS["alex"].top
    assert CHARACTERS["alex"].hair_style == "blocky_long"


def test_blocky_mesh_is_rigid_boxes():
    mb = bh.build_blocky_character_mesh(CHARACTERS["steve"])
    assert mb.verts and mb.faces
    # every vert is bound to one bone (rigid blocky binding)
    for wmap in mb.weights:
        assert len(wmap) == 1
        assert next(iter(wmap.values())) == pytest.approx(1.0)
    # faces: rigid box quads only (mouth is boxes too, no loft caps)
    assert all(len(f) == 4 for f in mb.faces)
    names = {n for w in mb.weights for n in w}
    assert names <= {b.name for b in BONES}
    # head box: the topmost vert is at the hair cap (or the skin-top skull
    # when a skin texture paints the crown instead of a geometry cap)
    expected_top = 1.8 if bh.STEVE.skin_tex else 1.81
    assert max(v[2] for v in mb.verts) == pytest.approx(expected_top, abs=1e-6)
    assert min(v[2] for v in mb.verts) == pytest.approx(0.02, abs=1e-6)


def test_blocky_vs_smooth_mesh_differ():
    smooth = sh.build_character_mesh(sh.MILO)
    blocky = sh.build_character_mesh(bh.STEVE)
    # boxes are far cheaper than the smooth body
    assert len(blocky.verts) * 3 < len(smooth.verts)
    assert len(blocky.faces) * 3 < len(smooth.faces)
    # the blocky face has its own brow material for emotions, plus the mouth
    # adds lip and tongue materials, so the count is at most two above smooth
    assert len(blocky.materials) <= len(smooth.materials) + 2


def test_blocky_face_geometry_is_rigged_to_the_face_bones():
    mb = bh.build_blocky_character_mesh(CHARACTERS["steve"])
    by_bone: dict[str, list[tuple[float, float, float]]] = {}
    for vert, wmap in zip(mb.verts, mb.weights):
        by_bone.setdefault(next(iter(wmap)), []).append(vert)
    # brows stay rigid boxes; the mouth is a thread riding the jaw/lip bones
    for bone in ("brow_l", "brow_r"):
        assert len(by_bone.get(bone, [])) == 8, bone
    if mb.skin_tex:
        # with a skin the mouth lives in the texture, not as mesh geometry
        for bone in ("jaw", "lip_l", "lip_r"):
            assert not by_bone.get(bone), bone
    else:
        for bone in ("jaw", "lip_l", "lip_r"):
            assert by_bone.get(bone), bone
    assert "brow" in {name for name, _ in mb.materials}
    # brows sit above the eyes and are proud of the hair fringe (y = -0.26)
    brow = by_bone["brow_l"]
    assert min(v[2] for v in brow) == pytest.approx(1.700, abs=1e-6)
    assert min(v[1] for v in brow) < -0.26


def test_blocky_mouth_is_distinct_boxes():
    mb = bh.build_blocky_character_mesh(CHARACTERS["steve"])
    by_bone: dict[str, list[tuple[float, float, float]]] = {}
    for vert, wmap in zip(mb.verts, mb.weights):
        by_bone.setdefault(next(iter(wmap)), []).append(vert)
    # each mouth part is one rigid box (8 verts): lower lip on the jaw,
    # corner blocks on the lip bones
    if mb.skin_tex:
        # mouth geometry is replaced by the three skin variants
        for bone in ("jaw", "lip_l", "lip_r"):
            assert not by_bone.get(bone), bone
        return
    jaw = by_bone["jaw"]
    lip_l = by_bone["lip_l"]
    lip_r = by_bone["lip_r"]
    assert len(jaw) == 8 and len(lip_l) == 8 and len(lip_r) == 8
    # the lower lip (jaw) sits below the upper lip line (z < 1.39)
    assert max(v[2] for v in jaw) <= 1.388
    # corners flank the mouth on each side (lip_l right, lip_r left)
    assert min(v[0] for v in lip_l) > 0.09
    assert max(v[0] for v in lip_r) < -0.09
    mat_names = {n for n, _ in mb.materials}
    assert "lips" in mat_names and "mouth" in mat_names


def test_blocky_upper_arm_uvs_are_rotated_180():
    # The upper-arm skin region is sampled upside down; the fix rotates its
    # UVs 180° (flip U and V).  Verified against the actual mesh UVs of the
    # left upper arm: front face top edge maps to u=0.625 for x=0.25
    # (0.5625 without the U-flip) and bottom face flips 0.25 -> 0.6875.
    mb = bh.build_blocky_character_mesh(CHARACTERS["steve"])
    bone_of = [next(iter(w)) for w in mb.weights]
    uvs = mb._loop_uvs
    kinds = ("bottom", "top", "front", "back", "left", "right")
    by_kind: dict[str, dict[float, tuple[float, float]]] = {}
    li = 0
    for fi, face in enumerate(mb.faces):
        kind = kinds[fi % 6]
        for vi in face:
            if bone_of[vi] == "upper_arm_L":
                x, y, z = mb.verts[vi]
                if abs(z - 1.24) < 1e-6 and abs(y + 0.125) < 1e-6:
                    by_kind.setdefault(kind, {})[round(x, 4)] = (
                        tuple(round(c, 6) for c in uvs[li])
                    )
            li += 1
    front = by_kind["front"]
    assert front[0.25] == pytest.approx((0.625, 0.1875), abs=1e-6)
    assert front[0.5] == pytest.approx((0.5625, 0.1875), abs=1e-6)
    bottom = by_kind["bottom"]
    assert bottom[0.25] == pytest.approx((0.6875, 0.25), abs=1e-6)
    assert bottom[0.5] == pytest.approx((0.625, 0.25), abs=1e-6)


def test_skin_face_swap_maps_left_right():
    # the skin sheet labels sides from the character's point of view; the
    # blocky mesh stores faces as -X=left / +X=right, so they swap
    assert bh._SKIN_FACE_SWAP["left"] == "right"
    assert bh._SKIN_FACE_SWAP["right"] == "left"
    for key in ("top", "bottom", "front", "back"):
        assert bh._SKIN_FACE_SWAP.get(key, key) == key


def test_apply_hop_lifts_and_returns():
    fps = 30
    base = PoseParams().resolved()
    poses = [base] * fps  # one second of standing
    hopped = bh.apply_hop(poses, fps, start=0.2, duration=0.4, height=0.2, back=0.3)
    assert hopped[5].resolved().root[2] == pytest.approx(base.root[2], abs=1e-9)
    peak = max(hopped, key=lambda p: p.root[2])
    assert peak.root[2] > base.root[2] + 0.15
    # feet travel with the body (no planted ankles mid-air)
    assert peak.ankle_l[2] > base.ankle_l[2] + 0.15
    # body moved backwards (+Y) and settled there
    assert hopped[-1].root[1] > base.root[1] + 0.2
    assert hopped[-1].root[2] == pytest.approx(base.root[2], abs=1e-9)


def test_scene_payload_structure(tmp_path: Path):
    scene = bh.first_film_scene()
    path, payload = bh.build_blocky_scene_payload(
        tmp_path, scene["actors"], props=scene["props"],
        camera_cuts=scene["camera_cuts"], environment=scene["environment"],
        scenario="test_scene", fps=30,
    )
    assert path.exists()
    assert payload["style"] == "blocky"
    assert len(payload["actors"]) == 2
    assert len(payload["bones"]) == 2 * len(BONES)
    assert len(payload["frames"]) == 300  # 10 s at 30 fps
    assert payload["props"][0]["name"] == "GlowCube"
    assert len(payload["camera_cuts"]) == 3
    # every frame carries both actors' hips
    assert "steve:hips" in payload["frames"][0]
    assert "alex:hips" in payload["frames"][-1]
    # weights use namespaced bones only
    known = {b["name"] for b in payload["bones"]}
    for wmap in payload["weights"]:
        assert set(wmap) <= known


def test_first_film_actions_are_supported():
    scene = bh.first_film_scene()
    for actor in scene["actors"]:
        timeline = timeline_from_actions(
            actor["actions"], duration=actor["duration"], fps=30, yaw=0.0
        )
        assert timeline.duration == pytest.approx(10.0, abs=0.05)


def test_cube_sits_in_front_of_steve(tmp_path: Path):
    scene = bh.first_film_scene()
    cube = scene["props"][0]
    _, payload = bh.build_blocky_scene_payload(
        tmp_path, scene["actors"], scenario="cube_check", fps=30,
    )
    steve = next(a for a in payload["actors"] if a["character"] == "steve")
    fx, fy, _ = steve["final_root"]
    cx, cy, cz = cube["center"]
    # the cube rests on a pedestal in front of Steve (-Y), within hand reach
    assert cy < fy
    assert 0.2 < fy - cy < 0.8
    assert cz == pytest.approx(0.74, abs=0.05)  # cube top ≈ touching hand height
    assert abs(cx - fx) < 0.4


def test_sfx_wav_duration_and_content(tmp_path: Path):
    data = bh.synth_sfx([(0.5, "sparkle"), (2.0, "boing")], 4.0)
    p = tmp_path / "sfx.wav"
    p.write_bytes(data)
    with wave.open(str(p), "rb") as w:
        assert w.getframerate() == 48000
        assert abs(w.getnframes() / w.getframerate() - 4.0) < 0.01
        import struct
        frames = w.readframes(w.getnframes())
        samples = struct.unpack(f"<{w.getnframes()}h", frames)
    assert max(abs(s) for s in samples) > 5000  # audible content


def test_blocky_open_mouth_has_dark_cavity():
    mb = bh.build_blocky_character_mesh(bh.STEVE)
    names = [n for n, _ in mb.materials]
    if mb.skin_tex:
        # the mouth lives in the skin variants (closed/round/open), so there
        # must be no jaw-bound mouth geometry on the model
        assert "jaw" not in {n for w in mb.weights for n in w}
        return
    assert "mouth" in names
    mouth_idx = names.index("mouth")
    cavity_ys = []
    for face, mi in zip(mb.faces, mb.face_mats):
        if mi == mouth_idx:
            cavity_ys.extend(mb.verts[v][1] for v in face)
    # black cavity exists and is recessed behind the lip slabs (deeper +Y,
    # i.e. y >= -0.26, inside the head cube)
    assert cavity_ys
    assert min(cavity_ys) >= -0.26


# --------------------------------------------------------------------------
# mouth-skin payload fixes: WAV segments fallback, on-disk gap-fill, UV guard
# --------------------------------------------------------------------------


def _write_speech_wav(path, *, sr=22050, silence=0.25, tone=0.5, tail=0.25):
    """Synthetic 16-bit mono WAV: silence, then a tone burst, then silence."""
    import numpy as np
    n = int((silence + tone + tail) * sr)
    t = np.arange(n) / sr
    x = np.zeros(n, dtype=np.float32)
    s0 = int(silence * sr)
    s1 = int((silence + tone) * sr)
    x[s0:s1] = 0.45 * np.sin(2 * np.pi * 440.0 * t[s0:s1])
    pcm = (x * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _talk_actor(text: str, *, start: float = 0.0, dur: float = 1.0) -> dict:
    return {
        "character": "steve",
        "origin": (0.0, 0.0, 0.0),
        "actions": [{"action": "idle", "start": 0.0, "duration": 2.0}],
        "voice_lines": [{
            "start": start, "duration": dur, "text": text,
        }],
        "mouth_skin": {
            "text": text, "lang": "ru", "start": start, "duration": dur,
        },
    }


def test_mouth_skin_falls_back_to_voice_wav_segments(tmp_path):
    """(A) без segments рот открывается только внутри озвучки из WAV."""
    wav = tmp_path / "line_001.wav"
    _write_speech_wav(wav)
    actor = _talk_actor("Привет, Алекс! Держи, проверь!")
    actor["voice_lines"][0]["wav"] = str(wav)
    path, payload = bh.build_blocky_scene_payload(
        tmp_path, [actor], scenario="skin_wav_fallback", fps=30
    )
    st = payload["mouth_states"]["steve"]
    qc = payload["mouth_segments"]["steve"]
    assert qc["source"] == "voice_lines.wav"
    spans = [tuple(s) for s in qc["segments"]]
    assert len(spans) == 1
    a, b = spans[0]
    assert 0.20 <= a <= 0.30, spans
    assert 0.70 <= b <= 0.80, spans
    f0 = int(round(a * 30))
    f1 = int(round(b * 30))
    opened = [f for f, v in st.items() if not v.endswith("_closed.png")]
    assert opened
    # открытые кадры только внутри окна озвучки (с запасом на min_hold_frames)
    assert all(f0 - 2 <= f <= f1 + 6 for f in opened), opened
    assert all(st[f] == "steve_mouth_closed.png" for f in range(0, max(0, f0 - 2)))
    assert all(st[f] == "steve_mouth_closed.png" for f in range(f1 + 7, 60))
    # и это же попадает на диск (QC/diagnostics могут проверить)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["mouth_segments"]["steve"]["source"] == "voice_lines.wav"


def test_mouth_skin_falls_back_to_voice_line_segments(tmp_path):
    """Сегменты, измеренные из реального дубля, тоже используются без WAV."""
    actor = _talk_actor("Привет, Алекс! Держи, проверь!")
    actor["voice_lines"][0]["segments"] = [[0.3, 1.0], [1.3, 1.7]]
    _, payload = bh.build_blocky_scene_payload(
        tmp_path, [actor], scenario="skin_line_segments", fps=30
    )
    qc = payload["mouth_segments"]["steve"]
    assert qc["source"] == "voice_lines.segments"
    assert qc["segments"] == [[0.3, 1.0], [1.3, 1.7]]
    st = payload["mouth_states"]["steve"]
    # пауза между репликами (1.0..1.3 с) остаётся закрытой (с запасом на
    # min_hold_frames, который может дотянуть бегущий рот на пару кадров)
    assert all(st[f].endswith("_closed.png") for f in range(36, 39))
    assert any(not st[f].endswith("_closed.png") for f in range(10, 30))


def test_mouth_states_gap_fill_is_written_to_disk(tmp_path):
    """(B) payload на диске содержит closed-дефолты для всех кадров."""
    actor = _talk_actor("Мама мыла раму. Урок окончен.", start=0.3, dur=1.4)
    path, _payload = bh.build_blocky_scene_payload(
        tmp_path, [actor], scenario="gap_fill_disk", fps=30
    )
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    frames = len(on_disk["frames"])
    st = on_disk["mouth_states"]["steve"]
    assert len(st) == frames
    assert st["0"] == "steve_mouth_closed.png"
    assert all(st[str(i)] for i in range(frames))


def test_uv_mismatch_raises_instead_of_silent_fallback(tmp_path, monkeypatch):
    """(C) расхождение UV -> ValueError, а не молчаливое снятие текстур."""

    class _BrokenUV:
        def __init__(self, spec):
            data = bh.build_blocky_character_mesh(spec).to_payload()
            data["uvs"] = data["uvs"][:-7]
            self._data = data

        def to_payload(self):
            return self._data

    monkeypatch.setattr(bh, "build_character_mesh", lambda spec: _BrokenUV(spec))
    actor = {
        "character": "steve",
        "origin": (0.0, 0.0, 0.0),
        "actions": [{"action": "idle", "start": 0.0, "duration": 1.0}],
    }
    with pytest.raises(ValueError, match="UV-координат"):
        bh.build_blocky_scene_payload(tmp_path, [actor], scenario="uv_guard", fps=30)


# --------------------------------------------------------------------------
# световые лимиты: POINT-свет и эмиссия не должны выжигать кадр в белый
# --------------------------------------------------------------------------


def _light_scene_props() -> list[dict]:
    return [
        {
            "name": "GlowCube", "center": (0.0, 0.7, 0.34),
            "size": (0.34, 0.34, 0.34), "emissive": True,
            "emission_rgb": (0.55, 0.95, 1.0),
            "emission_track": [[1, 0.6], [90, 9.0], [150, 5.0]],
        },
        {
            "name": "CubeLight", "type": "light", "center": (0.0, 0.7, 0.95),
            "energy_track": [[1, 2.0], [90, 80.0], [150, 40.0]],
        },
    ]


def _payload_actor() -> dict:
    return {
        "character": "steve", "origin": (0.0, 0.0, 0.0),
        "actions": [{"action": "idle", "start": 0.0, "duration": 1.0}],
    }


def test_payload_clamps_energy_and_emission_tracks(tmp_path: Path):
    """Треки 80 Вт / эмиссия 9.0 выходят из сборщика в пределах 15 / 2.5."""
    _, payload = bh.build_blocky_scene_payload(
        tmp_path, [_payload_actor()], props=_light_scene_props(),
        scenario="light_clamp", fps=30,
    )
    by_name = {p["name"]: p for p in payload["props"]}
    energy = [v for _, v in by_name["CubeLight"]["energy_track"]]
    emission = [v for _, v in by_name["GlowCube"]["emission_track"]]
    assert max(energy) <= bh.MAX_POINT_ENERGY, energy
    assert max(emission) <= bh.MAX_EMISSION_STRENGTH, emission
    # тайминги не тронуты, зажаты только величины
    assert [f for f, _ in by_name["CubeLight"]["energy_track"]] == [1, 90, 150]
    assert [f for f, _ in by_name["GlowCube"]["emission_track"]] == [1, 90, 150]


def test_first_film_payload_within_light_limits(tmp_path: Path):
    """Золотая сцена «Необычный куб» укладывается в лимиты после правок."""
    scene = bh.first_film_scene()
    _, payload = bh.build_blocky_scene_payload(
        tmp_path, scene["actors"], props=scene["props"],
        scenario="first_film_limits", fps=30,
    )
    for prop in payload["props"]:
        if prop.get("type") == "light":
            energy = [v for _, v in (prop.get("energy_track") or [])]
            assert max(energy) <= bh.MAX_POINT_ENERGY, (prop["name"], energy)
        else:
            emission = [v for _, v in (prop.get("emission_track") or [])]
            assert not emission or max(emission) <= bh.MAX_EMISSION_STRENGTH, (
                prop["name"], emission
            )
    # авторские значения тоже зажаты (не только clamp на выходе сборщика)
    for prop in scene["props"]:
        if prop.get("type") == "light":
            assert max(v for _, v in prop["energy_track"]) <= bh.MAX_POINT_ENERGY
        elif prop.get("emission_track"):
            assert max(v for _, v in prop["emission_track"]) <= bh.MAX_EMISSION_STRENGTH


# --------------------------------------------------------------------------
# B5: авто-реакции слушателя в payload-пути (build_blocky_scene_payload)
# --------------------------------------------------------------------------


_GESTURE_VERBS = {verb for verb, _d, _e, _dur in gestures.GESTURE_LIBRARY}


def _duo() -> tuple[dict, dict]:
    """Steve speaks 1.0-3.0 s; Alex just stands there (idle 0-5 s)."""
    steve = {
        "character": "steve", "origin": (-0.5, 0.0, 0.0),
        "actions": [{"action": "idle", "start": 0.0, "duration": 5.0}],
        "voice_lines": [{"start": 1.0, "duration": 2.0, "text": "Привет!"}],
    }
    alex = {
        "character": "alex", "origin": (0.5, 0.0, 0.0),
        "actions": [{"action": "idle", "start": 0.0, "duration": 5.0}],
    }
    return steve, alex


def test_listener_reaction_inserted_into_silent_actor():
    steve, alex = _duo()
    assert bh._apply_listener_reactions([steve, alex]) == 1
    reactions = [a for a in alex["actions"] if a["action"] != "idle"]
    assert len(reactions) == 1
    r = reactions[0]
    assert r["action"] in _GESTURE_VERBS
    assert r["start"] == pytest.approx(1.0)
    assert r["duration"] == pytest.approx(2.0)
    # speaker (x=-0.5) is to the left of the listener (x=+0.5)
    assert r["params"]["direction"] == -1.0
    # the speaker is untouched
    assert steve["actions"] == [{"action": "idle", "start": 0.0, "duration": 5.0}]


def test_listener_reaction_direction_flips_with_sides():
    steve, alex = _duo()
    steve["origin"], alex["origin"] = (0.5, 0.0, 0.0), (-0.5, 0.0, 0.0)
    bh._apply_listener_reactions([steve, alex])
    r = next(a for a in alex["actions"] if a["action"] != "idle")
    assert r["params"]["direction"] == 1.0


def test_listener_reaction_skips_authored_beats():
    steve, alex = _duo()
    alex["actions"] = [{"action": "walk", "start": 0.0, "duration": 5.0}]
    assert bh._apply_listener_reactions([steve, alex]) == 0
    assert alex["actions"] == [{"action": "walk", "start": 0.0, "duration": 5.0}]


def test_listener_reaction_carves_idle_without_overlaps():
    steve, alex = _duo()
    assert bh._apply_listener_reactions([steve, alex]) == 1
    actions = sorted(alex["actions"], key=lambda a: float(a["start"]))
    # no two actions overlap, no twitch-sized fragments
    for prev, nxt in zip(actions, actions[1:]):
        assert prev["start"] + prev["duration"] <= nxt["start"] + 1e-6
    assert all(a["duration"] >= bh.LISTENER_MIN_IDLE_PART for a in actions)
    # the idle is carved exactly around the speech window 1.0-3.0
    idles = [(a["start"], a["duration"]) for a in actions if a["action"] == "idle"]
    assert idles == [(0.0, 1.0), (3.0, 2.0)]
    r = next(a for a in actions if a["action"] != "idle")
    assert 1.0 <= r["start"] and r["start"] + r["duration"] <= 3.0


def test_listener_reactions_are_deterministic():
    steve, alex = _duo()
    run1 = copy.deepcopy([steve, alex])
    run2 = copy.deepcopy([steve, alex])
    assert bh._apply_listener_reactions(run1) == bh._apply_listener_reactions(run2) == 1
    assert run1 == run2


def test_listener_reaction_uses_line_emotion():
    steve, alex = _duo()
    steve["voice_lines"][0]["emotion"] = "surprised"
    assert bh._apply_listener_reactions([steve, alex]) == 1
    r = next(a for a in alex["actions"] if a["action"] != "idle")
    assert r["action"] in gestures.gestures_for_emotion("surprised")


def test_listener_reaction_caps_long_window_and_merges_overlaps():
    steve, alex = _duo()
    steve["voice_lines"] = [
        {"start": 1.0, "duration": 6.0, "text": "длинная реплика"},
        {"start": 2.0, "duration": 2.0, "text": "перекрытие"},
    ]
    assert bh._apply_listener_reactions([steve, alex]) == 1
    r = next(a for a in alex["actions"] if a["action"] != "idle")
    assert r["start"] == pytest.approx(1.0)
    assert r["duration"] == pytest.approx(bh.LISTENER_MAX_REACTION)


def test_listener_reaction_skips_monologue():
    solo, _alex = _duo()
    assert bh._apply_listener_reactions([solo]) == 0
    assert solo["actions"] == [{"action": "idle", "start": 0.0, "duration": 5.0}]


def test_payload_carves_split_idle_for_listener_reaction(tmp_path):
    """Through build_blocky_scene_payload: split idles are carved and synced
    into the payload actors (F4 gestures need one idle covering the window)."""
    steve, alex = _duo()
    alex["actions"] = [
        {"action": "idle", "start": 0.0, "duration": 1.5},
        {"action": "idle", "start": 1.5, "duration": 3.5},
    ]
    _, payload = bh.build_blocky_scene_payload(
        tmp_path, [steve, alex], scenario="listener_react", fps=30
    )
    info = next(a for a in payload["actors"] if a["character"] == "alex")
    actions = sorted(info["actions"], key=lambda a: float(a["start"]))
    for prev, nxt in zip(actions, actions[1:]):
        assert prev["start"] + prev["duration"] <= nxt["start"] + 1e-6
    reactions = [a for a in actions if a["action"] not in bh.LISTENER_IDLE_VERBS]
    assert len(reactions) == 1
    assert reactions[0]["start"] == pytest.approx(1.0)
    assert reactions[0]["duration"] == pytest.approx(2.0)
    assert reactions[0]["action"] in _GESTURE_VERBS


def test_listener_reaction_baked_into_frames(tmp_path):
    """Реакция слушателя обязана попасть в САМИ кадры, а не только в метаданные."""
    def build(with_voice, scenario):
        steve = {"character": "steve", "origin": (-0.45, 0.1, 0.0), "duration": 4.0,
                 "actions": [{"action": "idle", "start": 0.0, "duration": 4.0}]}
        alex = {"character": "alex", "origin": (0.45, 0.1, 0.0), "duration": 4.0,
                "actions": [{"action": "idle", "start": 0.0, "duration": 4.0}]}
        if with_voice:
            steve["voice_lines"] = [{
                "start": 1.0, "duration": 2.0, "text": "Привет, Алекс!",
                "emotion": "happy",
            }]
        _, payload = bh.build_blocky_scene_payload(
            tmp_path, [steve, alex], scenario=scenario, fps=30)
        return payload

    p_voice = build(True, "lr_voice")
    p_silent = build(False, "lr_silent")
    # голова Алекса в окне речи (кадры 30-90) отличается от бейзлайна без речи
    diffs = [
        math.dist(p_voice["frames"][fi]["alex:head"][:3],
                  p_silent["frames"][fi]["alex:head"][:3])
        for fi in range(30, 90)
    ]
    assert max(diffs) > 1e-4, "реакция слушателя не попала в выпеченные кадры"
    # вне окна (кадры 0-29) позиции совпадают — реакции только в окне речи
    # (допуск 0.5 мм: блендинг сегментов таймлайна даёт ~0.1 мм по краям)
    assert max(math.dist(p_voice["frames"][fi]["alex:head"][:3],
                         p_silent["frames"][fi]["alex:head"][:3])
               for fi in range(0, 30)) < 5e-4
