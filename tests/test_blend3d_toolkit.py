# tests/test_blend3d_toolkit.py — host-side tests for the blend3d production toolkit.
# Pure python (no Blender required). Blender-side code is smoke-tested separately.
import json
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from shortsfactory.blend3d.host import qc
from shortsfactory.blend3d.host.film_spec import (
    FilmSpec, plan_from_idea, validate, Beat, FPS,
)
from shortsfactory.blend3d.host.render_job import (
    build_blender_argv, assemble_argv, frames_done,
    MODES,
)
from shortsfactory.blend3d.host.paths import find_blender

PRESETS = Path(__file__).resolve().parents[1] / "src" / "shortsfactory" / "blend3d" / "presets"

# ---------- film_spec: deterministic idea → plan ----------

def test_plan_from_idea_golem():
    spec = plan_from_idea("Стив и Алекс находят заброшенный храм и случайно пробуждают древнего голема")
    ids = [b.beat_id for b in spec.beats]
    assert ids[0] == "mine"                      # world established first
    assert "treasure" in ids and "threat" in ids # temple + golem recognized
    assert ids[-1] == "ending"                   # always resolves
    assert spec.frames == 1500 and spec.fps == FPS

def test_plan_beats_partition_timeline():
    spec = plan_from_idea("лава в шахте, спасение напарника, рассвет")
    assert [b.beat_id for b in spec.beats][0] == "mine"
    assert [b.beat_id for b in spec.beats][-1] == "ending"
    for a, b in zip(spec.beats, spec.beats[1:]):
        assert b.f0 == a.f1 + 1                  # contiguous, no gaps/overlaps

def test_plan_fallback_when_no_keywords():
    spec = plan_from_idea("что-то нейтральное")
    assert len(spec.beats) >= 3

def test_spec_json_roundtrip():
    spec = plan_from_idea("алмаз в шахте, спасение", duration_s=40)
    spec2 = FilmSpec.from_json(spec.to_json())
    assert spec2.frames == spec.frames
    assert [b.beat_id for b in spec2.beats] == [b.beat_id for b in spec.beats]

def test_validate_rejects_bad_beats():
    spec = FilmSpec(beats=[Beat("a", "a", "m", "c", 10, 5)])
    with pytest.raises(ValueError):
        validate(spec)

def test_validate_rejects_overlap():
    spec = FilmSpec(beats=[Beat("a", "a", "m", "c", 1, 100), Beat("b", "b", "m", "c", 50, 200)])
    with pytest.raises(ValueError):
        validate(spec)

# ---------- render_job: argv builders ----------

def test_build_blender_argv(tmp_path):
    script = tmp_path / "driver.py"
    script.write_text("print('x')", encoding="utf-8")
    argv = build_blender_argv(r"D:\Blender\blender.exe", script, "preview")
    assert argv[0] == r"D:\Blender\blender.exe" and argv[1] == "-b"
    assert argv[-1] == "preview" and "--" in argv
    for mode in MODES:
        assert build_blender_argv(r"D:\Blender\blender.exe", script, mode)[-1] == mode

def test_build_argv_rejects_missing_script(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_blender_argv(r"D:\Blender\blender.exe", tmp_path / "nope.py", "build")

def test_build_argv_rejects_bad_mode(tmp_path):
    script = tmp_path / "driver.py"; script.write_text("x")
    with pytest.raises(ValueError):
        build_blender_argv(r"D:\Blender\blender.exe", script, "explode")

def test_build_argv_rejects_missing_blender(tmp_path):
    script = tmp_path / "driver.py"; script.write_text("x")
    with pytest.raises(FileNotFoundError):
        build_blender_argv(r"D:\no_such_blender\blender.exe", script, "build")

def test_assemble_argv(tmp_path):
    fd = tmp_path / "frames"; fd.mkdir()
    (fd / "f0001.jpg").write_bytes(b"x")
    wav = tmp_path / "mix.wav"; wav.write_bytes(b"x")
    argv = assemble_argv(fd, wav, tmp_path / "out.mp4", fps=30)
    assert "libx264" in argv and "aac" in argv
    with pytest.raises(FileNotFoundError):
        assemble_argv(tmp_path / "no_frames", wav, tmp_path / "o.mp4")

def test_frames_done_counts(tmp_path):
    fd = tmp_path / "frames"; fd.mkdir()
    for i in (1, 2, 3):
        (fd / f"f{i:04d}.jpg").write_bytes(b"x")
    assert frames_done(fd) == 3
    assert frames_done(tmp_path / "absent") == 0

def test_find_blender_available():
    p = find_blender()   # this machine has D:\Blender\blender.exe
    assert p.is_file()

# ---------- qc: frames + wav ----------

def _write_test_frames(d, n=40, size=(1080, 1920), dark_from=None, dark_to=None):
    d.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    rs = np.random.RandomState(3)
    for i in range(1, n + 1):
        v = 0 if (dark_from is not None and dark_from <= i <= dark_to) else 160
        img = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        img[:, :, 1] = v; img[:, :, 2] = v
        img += rs.randint(0, 14, size=img.shape, dtype=np.uint8)  # per-frame noise: not frozen
        Image.fromarray(img).save(d / f"f{i:04d}.jpg", quality=90)

def _write_test_wav(p, dur=2.0, sr=48000, freq=440.0):
    t = np.arange(int(dur * sr)) / sr
    x = (0.5 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    w = wave.open(str(p), "wb")
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes(np.stack([x, x], axis=1).tobytes())
    w.close()

def test_qc_frames_ok(tmp_path):
    d = tmp_path / "frames"
    _write_test_frames(d, n=40)
    res = qc.qc_frames(d, fps=30, expect_size=(1080, 1920), sample_step=1)
    assert res["frames"] == 40 and res["issues"] == []

def test_qc_frames_detects_black(tmp_path):
    d = tmp_path / "frames"
    _write_test_frames(d, n=200, dark_from=40, dark_to=60)   # mid-timeline darkness
    res = qc.qc_frames(d, fps=30, sample_step=2)
    assert any(i.startswith("CRITICAL: black") for i in res["issues"])

def test_qc_frames_detects_wrong_size(tmp_path):
    d = tmp_path / "frames"
    _write_test_frames(d, n=10, size=(500, 800))
    res = qc.qc_frames(d, expect_size=(1080, 1920), sample_step=1)
    assert any("wrong size" in i or "CRITICAL" in i for i in res["issues"])

def test_qc_wav_levels(tmp_path):
    p = tmp_path / "mix.wav"
    _write_test_wav(p, dur=2.0)
    res = qc.qc_wav(p, expect_duration=2.0)
    assert res["duration"] == 2.0 and res["sr"] == 48000
    assert res["peak_dbfs"] > -7 and res["peak_dbfs"] <= 0

def test_qc_wav_silence_flagged(tmp_path):
    p = tmp_path / "sil.wav"
    _write_test_wav(p, freq=0.0)
    res = qc.qc_wav(p)
    assert any("silent" in i for i in res["issues"])

def test_verdict_ready(tmp_path):
    d = tmp_path / "frames"; _write_test_frames(d, n=40)
    w = tmp_path / "mix.wav"; _write_test_wav(w, dur=2.0)
    v = qc.verdict(d, wav_path=w, fps=30, expect_size=(1080, 1920),
                   mp4_facts={"width": 1080, "height": 1920, "fps": "30/1", "audio_ok": True})
    assert v["status"] == "READY", v["issues"]

def test_verdict_failed_on_wrong_resolution(tmp_path):
    d = tmp_path / "frames"; _write_test_frames(d, n=40)
    v = qc.verdict(d, mp4_facts={"width": 720, "height": 1280, "fps": "30/1", "audio_ok": True})
    assert v["status"] == "FAILED"

# ---------- presets ----------

def test_character_presets_valid():
    for name in ("steve", "alex"):
        d = json.loads((PRESETS / "characters" / f"{name}.json").read_text(encoding="utf-8"))
        for k in ("colors", "hair_style", "emotes", "motion"):
            assert k in d, f"{name}: missing {k}"
        assert d["style"] == "minecraft_blocky"

# ---------- sound: procedural synthesis ----------

def test_note_freq():
    from shortsfactory.blend3d.host.sound import note_freq
    assert abs(note_freq('A4') - 440.0) < 0.01
    assert abs(note_freq('C5') - 523.25) < 0.5

def test_render_events_produces_stereo_mix():
    from shortsfactory.blend3d.host.sound import render_events
    events = [
        {'t': 0.0, 'kind': 'bell', 'freq': 'C5', 'dur': 1.0, 'gain': 0.2, 'pan': -0.3},
        {'t': 0.5, 'kind': 'bass', 'freq': 'C3', 'dur': 1.5, 'gain': 0.1},
        {'t': 0.0, 'kind': 'pad', 'freqs': ['C4', 'E4', 'G4'], 'dur': 2.0, 'gain': 0.05},
    ]
    mix = render_events(events, duration=2.5, peak=0.8)
    assert mix.shape == (120000, 2)
    peak = np.abs(mix).max()
    assert 0.1 < peak <= 0.81
    assert np.isfinite(mix).all()

def test_sound_roundtrip_with_qc(tmp_path):
    from shortsfactory.blend3d.host.sound import render_events, pcm16
    import wave
    mix = render_events([
        {'t': 0.0, 'kind': 'bell', 'freq': 'A4', 'dur': 1.0, 'gain': 0.3},
        {'t': 0.5, 'kind': 'bass', 'freq': 'A2', 'dur': 1.2, 'gain': 0.15},
    ], duration=2.0, peak=0.7)
    p = tmp_path / "test.wav"
    w = wave.open(str(p), "wb")
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000)
    w.writeframes(pcm16(mix)); w.close()
    res = qc.qc_wav(p, expect_duration=2.0)
    assert res["duration"] == 2.0 and res["peak_dbfs"] > -10
    assert not any(i.startswith("CRITICAL") for i in res["issues"])

def test_events_from_timings_minimal():
    from shortsfactory.blend3d.host.sound import events_from_timings
    timings = {
        'pickaxe_hits': [[1.0, -0.2, 1.0], [1.5, 0.2, 1.05]],
        'walks': [[0.0, 1.0, 0.0]],
        'diamond_pops': [2.0],
        'final_chime': 2.4,
    }
    events = events_from_timings(timings, chords=[(0.0, 'A3', 'min')])
    kinds = [e['kind'] for e in events]
    assert 'bell' in kinds and 'bass' in kinds and 'pad' in kinds
    assert len([e for e in events if e['kind'] == 'bell']) >= 2 + 4 + 3 + 1  # hits+pops+arp+chime
    for e in events:
        assert 0.0 <= e['t'] <= 3.0
        assert np.isfinite(e['t'])

def test_render_events_from_timings_makes_audio():
    from shortsfactory.blend3d.host.sound import events_from_timings, render_events
    timings = {
        'pickaxe_hits': [[0.5, 0.0, 1.0]],
        'walks': [[0.0, 0.5, 0.0]],
        'diamond_pops': [1.2],
        'final_chime': 1.8,
    }
    events = events_from_timings(timings, chords=[(0.2, 'C4', 'maj')])
    mix = render_events(events, duration=2.5, peak=0.8)
    assert mix.shape == (120000, 2)
    assert np.isfinite(mix).all() and np.abs(mix).max() > 0.05

def test_placeholder_skin_distinguishes_left_right_limbs():
    from shortsfactory.blend3d.host.skins import build_placeholder_skin, region_px
    skin = build_placeholder_skin("steve")

    def mean(part: str, face: str) -> float:
        x0, y0, x1, y1 = region_px(part, face)
        return float(skin[y0:y1, x0:x1].mean())

    # the left limbs carry an orientation seam, so front regions must differ
    assert abs(mean("arm_l", "front") - mean("arm_r", "front")) > 0.001
    assert abs(mean("leg_l", "front") - mean("leg_r", "front")) > 0.001


def test_render_and_lighting_presets_valid():
    rp = json.loads((PRESETS / "render" / "shorts_1080x1920.json").read_text(encoding="utf-8"))
    assert rp["resolution"] == [1080, 1920] and rp["fps"] == 30
    lp = json.loads((PRESETS / "lighting" / "cave_night.json").read_text(encoding="utf-8"))
    assert "cave_fill" in lp["lights"] and "torch" in lp["lights"]
    mp = json.loads((PRESETS / "motion" / "walk_cycle.json").read_text(encoding="utf-8"))
    assert mp["defaults"]["stride_m"] > 0

# ---------- skins (64x64 Minecraft-style sheets) ----------

def test_skin_regions_fit_64x64():
    from shortsfactory.blend3d.host.skins import SKIN64_REGIONS
    for key, (x0, y0, x1, y1) in SKIN64_REGIONS.items():
        assert x0 >= 0 and y0 >= 0 and x1 <= 64 and y1 <= 64, key
        assert x1 > x0 and y1 > y0, key

def test_placeholder_skin_is_64x64_rgba():
    from shortsfactory.blend3d.host.skins import build_placeholder_skin
    arr = build_placeholder_skin("steve")
    assert arr.shape == (64, 64, 4)
    assert int(arr[..., 3].min()) == 255

def test_validate_skin_accepts_generated(tmp_path):
    from shortsfactory.blend3d.host.skins import build_placeholder_skin, validate_skin
    p = tmp_path / "skin.png"
    Image.fromarray(build_placeholder_skin("alex")).save(p)
    info = validate_skin(p)
    assert info["width"] == 64 and info["height"] == 64 and not info["legacy"]

def test_validate_skin_rejects_wrong_size(tmp_path):
    from shortsfactory.blend3d.host.skins import validate_skin
    p = tmp_path / "bad.png"
    Image.new("RGBA", (32, 64)).save(p)
    with pytest.raises(ValueError):
        validate_skin(p)

def test_placeholder_skin_has_shirt_in_body_front():
    from shortsfactory.blend3d.host.skins import build_placeholder_skin, region_px
    arr = build_placeholder_skin("steve")
    x0, y0, x1, y1 = region_px("body", "front")
    chunk = arr[y0:y1, x0:x1].astype(float).reshape(-1, 4)
    r, g, _b = chunk[:, 0].mean(), chunk[:, 1].mean(), chunk[:, 2].mean()
    assert g > 0.40 and r < 0.35  # Steve's cyan shirt lives on body front
