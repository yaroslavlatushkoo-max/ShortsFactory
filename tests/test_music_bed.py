"""Ч2.4: процедурная музыкальная подложка под blocky-сценами.

Проверяется только миксовая обвязка (``_scene_mix_inputs``) и сам WAV:
без Blender и без TTS — синтез музыки полностью локальный
(движок ``blend3d.host.sound``, вызывается через ``_write_procedural_music``).
"""

import wave
from pathlib import Path

import numpy as np
import pytest

import shortsfactory.generative.blocky_scene as blocky_scene
from shortsfactory.generative.blocky_scene import (
    DEFAULT_MUSIC_GAIN,
    BlockyScene,
)
from shortsfactory.generative.blocky_voice import DEFAULT_SFX_GAIN


class _FlagConfig:
    """Минимальный двойник Config: отвечает только про ``audio.music``."""

    def __init__(self, value, *, raise_on_get: bool = False) -> None:
        self._value = value
        self._raise = raise_on_get

    def get(self, section: str, key: str, default=None):
        if self._raise:
            raise RuntimeError("конфиг недоступен")
        if section == "audio" and key == "music":
            return self._value
        return default


@pytest.fixture
def scene() -> BlockyScene:
    return BlockyScene(title="Музыкальная сцена", duration=3.0)


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    data = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32767.0
    return data.reshape(-1, 2), rate


def test_music_gain_is_quarter_of_sfx_and_stays_under_it():
    """Подложка обязана быть тише SFX (и тем более голоса)."""
    assert DEFAULT_MUSIC_GAIN < DEFAULT_SFX_GAIN
    assert DEFAULT_MUSIC_GAIN == pytest.approx(DEFAULT_SFX_GAIN * 0.25, abs=0.01)


def test_music_input_and_wav_when_flag_on(scene, tmp_path, monkeypatch):
    monkeypatch.setattr(blocky_scene, "load_config", lambda: _FlagConfig(True))
    inputs = scene._scene_mix_inputs([], audio_dir=tmp_path / "audio")
    music = [item for item in inputs if item.label == "music"]
    assert len(music) == 1
    assert music[0].gain == pytest.approx(DEFAULT_MUSIC_GAIN)
    path = tmp_path / "audio" / "music.wav"
    assert path.exists()
    data, rate = _read_wav(path)
    assert abs(len(data) / rate - 3.0) < 0.05


def test_no_music_by_default_or_flag_off(scene, tmp_path, monkeypatch):
    for value in (None, False):  # None = ключа нет в конфиге
        monkeypatch.setattr(
            blocky_scene, "load_config", lambda value=value: _FlagConfig(value)
        )
        inputs = scene._scene_mix_inputs([], audio_dir=tmp_path / "audio")
        assert [item.label for item in inputs] == []
        assert not (tmp_path / "audio" / "music.wav").exists()


def test_broken_config_disables_music(scene, tmp_path, monkeypatch):
    monkeypatch.setattr(
        blocky_scene, "load_config", lambda: _FlagConfig(True, raise_on_get=True)
    )
    inputs = scene._scene_mix_inputs([], audio_dir=tmp_path / "audio")
    assert [item.label for item in inputs] == []


def test_music_wav_loudness_and_fades(scene, tmp_path, monkeypatch):
    """≈ -28 dBFS средней громкости; края тише середины более чем на 20 дБ."""
    monkeypatch.setattr(blocky_scene, "load_config", lambda: _FlagConfig(True))
    scene._scene_mix_inputs([], audio_dir=tmp_path / "audio")
    data, rate = _read_wav(tmp_path / "audio" / "music.wav")

    mean_dbfs = 20 * np.log10(max(1e-12, np.abs(data).mean()))
    assert -31.0 <= mean_dbfs <= -25.0

    segment = lambda a, b: np.abs(data[int(a * rate):int(b * rate)]).mean()
    middle = segment(1.25, 1.75)
    head = segment(0.0, 0.5)
    tail = segment(2.5, 3.0)
    assert 20 * np.log10(max(1e-12, head) / middle) <= -20.0
    assert 20 * np.log10(max(1e-12, tail) / middle) <= -20.0
