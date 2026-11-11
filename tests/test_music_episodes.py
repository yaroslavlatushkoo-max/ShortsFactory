"""Unit tests for procedural music (F6) and episode splitting (F6/D3)."""
from __future__ import annotations

import wave

from shortsfactory.generative.blocky_production import _split_storyboard_episodes
from shortsfactory.generative.blocky_scene import _write_procedural_music


def test_procedural_music_writes_valid_wav(tmp_path):
    out = tmp_path / "music.wav"
    _write_procedural_music(out, duration=3.0)
    assert out.exists() and out.stat().st_size > 1000
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 48000
        assert w.getnchannels() == 2
        assert w.getsampwidth() == 2
        assert w.getnframes() > 0


def test_procedural_music_has_fade_out_no_click(tmp_path):
    import numpy as np
    import wave as _wave

    out = tmp_path / "music.wav"
    _write_procedural_music(out, duration=2.0)
    with _wave.open(str(out), "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    tail = data[-2048:].astype(np.float32)
    assert float(np.max(np.abs(tail))) < 1200  # затухание к тишине


def test_split_episodes_preserves_order_and_titles():
    sb = {
        "title": "Мир",
        "logline": "x",
        "scenes": [
            {"index": i, "title": f"s{i}"} for i in range(1, 8)
        ],
    }
    eps = _split_storyboard_episodes(sb, 3)
    assert len(eps) == 3
    flat = [sc["index"] for ep in eps for sc in ep["scenes"]]
    assert flat == [1, 2, 3, 4, 5, 6, 7]
    assert eps[0]["title"].endswith("серия 1")
    assert eps[0]["scenes"][0]["index"] == 1


def test_split_episodes_empty_and_single():
    assert _split_storyboard_episodes({"scenes": []}, 3) == []
    sb = {"title": "T", "scenes": [{"index": 1}]}
    eps = _split_storyboard_episodes(sb, 5)
    assert len(eps) == 1
    assert eps[0]["scenes"][0]["index"] == 1
