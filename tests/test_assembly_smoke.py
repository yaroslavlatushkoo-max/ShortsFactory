"""Smoke-тесты сборки видео (generative/assembly.py) на реальном ffmpeg.

Клипы генерируются lavfi-источниками (маленькие цветные mp4 по 0.4 с),
звук — синтетические WAV. Никакого Blender, только subprocess ffmpeg.
"""
from __future__ import annotations

import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from shortsfactory.generative.assembly import concat_clips, mix_audio, probe
from shortsfactory.util import CommandError, run

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe не найдены в PATH",
)


def _make_color_clip(path: Path, color: str) -> None:
    run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:size=160x240:rate=30:duration=0.4",
            "-t", "0.4", str(path),
        ]
    )


def _make_wav(path: Path, seconds: float = 0.3, freq: float = 440.0, rate: int = 22050) -> None:
    t = np.linspace(0.0, seconds, int(rate * seconds), endpoint=False)
    samples = (np.sin(2 * np.pi * freq * t) * 0.5 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())


def test_concat_clips_joins_two_clips(tmp_path: Path):
    clip1 = tmp_path / "clip1.mp4"
    clip2 = tmp_path / "clip2.mp4"
    _make_color_clip(clip1, "red")
    _make_color_clip(clip2, "blue")

    out = concat_clips([str(clip1), str(clip2)], tmp_path / "build")

    assert out.exists()
    assert out.stat().st_size > 0
    info = probe(out)
    duration = float(info["format"]["duration"])
    assert duration == pytest.approx(0.8, abs=0.25)
    # нормализация приводит оба клипа к канвасу 1080x1920
    video = next(s for s in info["streams"] if s.get("codec_type") == "video")
    assert video["width"] == 1080 and video["height"] == 1920


def test_concat_clips_without_clips_raises(tmp_path: Path):
    with pytest.raises(CommandError, match="Нет клипов"):
        concat_clips([], tmp_path / "build")


def test_mix_audio_places_two_segments(tmp_path: Path):
    clip = tmp_path / "clip.mp4"
    _make_color_clip(clip, "green")
    wav1 = tmp_path / "seg1.wav"
    wav2 = tmp_path / "seg2.wav"
    _make_wav(wav1, freq=440.0)
    _make_wav(wav2, freq=880.0)

    out = mix_audio(clip, [(0.0, str(wav1)), (0.1, str(wav2))], tmp_path / "mix")

    assert out.exists()
    assert out.stat().st_size > 0
    info = probe(out)
    codecs = {s.get("codec_type"): s.get("codec_name") for s in info["streams"]}
    assert codecs.get("video") == "h264"
    assert codecs.get("audio") == "aac"
