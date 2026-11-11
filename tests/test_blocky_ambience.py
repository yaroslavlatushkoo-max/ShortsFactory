"""BUGFIX: ambience="none" — допустимое состояние, фон пропускается, микс жив."""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from shortsfactory.generative.blocky_scene import BlockyScene
from shortsfactory.generative.blocky_voice import MixInput, mix_report, mix_scene_audio

AUDIO_RATE = 48000


@pytest.fixture(autouse=True)
def _isolated_audio_config(monkeypatch):
    """Тесты амбиенса не должны зависеть от config/config.toml."""
    import shortsfactory.generative.blocky_scene as bs

    class _StubConfig:
        def get(self, section, key, default=None):
            return default  # audio.music -> False, music_volume -> дефолт

    monkeypatch.setattr(bs, "load_config", lambda *a, **k: _StubConfig())


def _scene(*, ambience: str = "", duration: float = 2.0) -> BlockyScene:
    return BlockyScene.from_dict(
        {"title": "t", "duration": duration, "ambience": ambience, "actors": []}
    )


def _write_tone(path: Path, seconds: float, freq: float = 440.0) -> Path:
    frames = int(round(seconds * AUDIO_RATE))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(AUDIO_RATE)
        for i in range(frames):
            sample = int(12000 * (1.0 + 0.5 * (i % 3)))
            out.writeframes(struct.pack("<h", sample))
    return path


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def _take_input(path: Path, *, start: float = 0.0, label: str = "steve") -> MixInput:
    return MixInput(path, start=start, gain=0.8, label=label)


# ------------------------------------------------------------------ выбор источников


def test_ambience_none_without_dialogue_produces_no_inputs(tmp_path):
    inputs = _scene(ambience="none")._scene_mix_inputs([], audio_dir=tmp_path)
    assert inputs == []


def test_ambience_none_with_dialogue_keeps_takes(tmp_path):
    take = _take_input(_write_tone(tmp_path / "a.wav", 0.4))
    inputs = _scene(ambience="none")._scene_mix_inputs([take], audio_dir=tmp_path)
    assert [item.label for item in inputs] == ["steve"]


def test_ambience_none_with_dialogue_and_sfx_keeps_both(tmp_path):
    take = _take_input(_write_tone(tmp_path / "a.wav", 0.4))
    scene = _scene(ambience="none")
    scene.sfx_events = [[0.5, "pop"]]
    inputs = scene._scene_mix_inputs([take], audio_dir=tmp_path)
    assert [item.label for item in inputs] == ["steve", "sfx"]
    assert (tmp_path / "sfx.wav").exists()
    assert not (tmp_path / "ambience.wav").exists()


def test_regular_ambience_preset_still_adds_bed(tmp_path):
    take = _take_input(_write_tone(tmp_path / "a.wav", 0.4))
    inputs = _scene(ambience="forest")._scene_mix_inputs([take], audio_dir=tmp_path)
    assert [item.label for item in inputs] == ["steve", "ambience"]
    assert (tmp_path / "ambience.wav").exists()


def test_missing_ambience_defaults_to_silence(tmp_path):
    scene = _scene()
    assert scene.ambience == ""
    assert scene._scene_mix_inputs([], audio_dir=tmp_path) == []


def test_ambience_none_is_accepted_by_validation():
    scene = BlockyScene.from_dict(
        {
            "title": "t",
            "duration": 2.0,
            "ambience": "none",
            "actors": [
                {
                    "character": "steve",
                    "duration": 2.0,
                    "actions": [{"action": "idle", "start": 0.0, "duration": 2.0}],
                }
            ],
        }
    )
    assert scene.ambience == "none"
    scene.validate()


# ------------------------------------------------------------------ микс: длительность и синхрон


def test_none_ambience_mix_keeps_duration_and_is_audible(tmp_path):
    """Без фона диалоги+SFX по-прежнему микшируются в полную длительность сцены."""
    take = _take_input(_write_tone(tmp_path / "a.wav", 0.4), start=0.5)
    scene = _scene(ambience="none")
    scene.sfx_events = [[1.0, "pop"]]
    inputs = scene._scene_mix_inputs([take], audio_dir=tmp_path)
    assert len(inputs) == 2
    out = mix_scene_audio(tmp_path / "mix.wav", inputs, duration=2.0)
    assert abs(_wav_duration(out) - 2.0) < 0.02
    report = mix_report(out)
    assert report["duration_s"] > 1.9
    assert report["peak"] > 0.05
    assert report["clipped"] is False


def test_none_ambience_mix_delays_dialogue_to_its_start(tmp_path):
    """Реплика со start=1.0 должна появиться в миксе не раньше своей секунды."""
    take = _take_input(_write_tone(tmp_path / "a.wav", 0.4), start=1.0)
    scene = _scene(ambience="none")
    inputs = scene._scene_mix_inputs([take], audio_dir=tmp_path)
    out = mix_scene_audio(tmp_path / "mix.wav", inputs, duration=2.0)
    with wave.open(str(out), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    silence_before = max(
        abs(int.from_bytes(frames[i:i + 2], "little", signed=True))
        for i in range(0, int(AUDIO_RATE * 2 * 0.7), 2)
    )
    # 0.7 с тишины до старта реплики (adelay 1000 мс)
    assert silence_before < 200, "голос слышен раньше запрошенной секунды"
