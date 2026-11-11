"""Mouth Rig tests — shapes, planning determinism, WAV fallback, integration."""

import wave

import pytest

from shortsfactory.generative.animation_pipeline import mouth_rig as mr
from shortsfactory.generative.animation_pipeline import blocky_hero as bh


def _write_wav(path, *, sr=22050, silence=0.25, tone=0.5, tail=0.25):
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


def test_shapes_closed_zero_and_open_positive():
    closed = mr.MOUTH_SHAPES["closed"]
    assert closed == (0.0, 0.0, 0.0)
    assert mr.MOUTH_SHAPES["open"][0] > 0.0
    assert mr.MOUTH_SHAPES["wide_open"][0] > mr.MOUTH_SHAPES["open"][0]


def test_plan_with_text_opens_and_closes(tmp_path):
    track = mr.plan_mouth_track("Привет!", duration=1.0, fps=30)
    shapes = [s for _, s in track]
    assert shapes[0] == "closed"
    assert shapes[-1] == "closed"
    assert any(s != "closed" for s in shapes)


def test_plan_fallback_wav_uses_speech_window(tmp_path):
    wav = tmp_path / "line.wav"
    _write_wav(wav)
    track = mr.plan_mouth_track(wav_path=wav, duration=1.0, fps=30)
    shapes = [s for _, s in track]
    # silence frames at both ends stay closed
    assert shapes[0] == "closed" and shapes[-1] == "closed"
    assert any(s != "closed" for s in shapes[6:24])


def test_plan_is_deterministic(tmp_path):
    a = mr.plan_mouth_track("Алекс", duration=1.0, fps=30)
    b = mr.plan_mouth_track("Алекс", duration=1.0, fps=30)
    assert a == b


def test_integration_mouth_track_applies(tmp_path):
    scene = {
        "duration": 2.0,
        "actors": [
            {
                "character": "steve",
                "origin": (0.0, 0.2, 0.0),
                "actions": [{"action": "idle", "start": 0.0, "duration": 2.0}],
                "mouth_track": [(0, "closed"), (30, "open"), (59, "closed")],
            }
        ],
    }
    path, payload = bh.build_blocky_scene_payload(
        tmp_path, scene["actors"], scenario="mouth_smoke", fps=30
    )
    assert path.exists()
    assert len(payload["frames"]) == 60
