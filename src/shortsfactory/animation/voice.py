"""Voice track for Animation Engine v1: real TTS dialogue and mouth sync.

The engine does not invent phonemes. Each dialogue line is synthesized once with
the project's existing Piper voices, placed on the scene timeline and turned into
a short normalised loudness envelope. Mouth openness is read from that envelope,
so the lips follow the actual audio instead of a decorative random loop.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..logging_setup import get_logger
from .scene import AnimationScene

#: Sampling rate of the rendered audio bed.
AUDIO_RATE = 48000

#: Resolution of the mouth envelope, in frames per second.
MOUTH_HZ = 50

#: Window used to measure loudness, in seconds.
RMS_WINDOW = 0.02


def _read_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError(f"Ожидался 16-битный WAV: {path}")
    data = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def _resample(samples: np.ndarray, rate: int, target: int) -> np.ndarray:
    if rate == target or samples.size == 0:
        return samples
    count = int(round(samples.size * target / rate))
    source_x = np.linspace(0.0, 1.0, samples.size, endpoint=False)
    target_x = np.linspace(0.0, 1.0, count, endpoint=False)
    return np.interp(target_x, source_x, samples)


def _envelope(samples: np.ndarray, rate: int) -> np.ndarray:
    """Normalised loudness curve sampled at :data:`MOUTH_HZ`."""
    window = max(1, int(round(rate * RMS_WINDOW)))
    count = max(1, samples.size // window)
    trimmed = samples[: count * window].reshape(count, window)
    rms = np.sqrt(np.mean(trimmed ** 2, axis=1))
    peak = float(rms.max()) if rms.size else 0.0
    if peak <= 1e-6:
        return np.zeros(int(round(samples.size / rate * MOUTH_HZ)) + 1, dtype=np.float64)
    level = np.sqrt(np.clip(rms / peak, 0.0, 1.0))
    if level.size >= 3:
        level = np.convolve(level, np.array([0.25, 0.5, 0.25]), mode="same")
    return level


@dataclass
class VoiceLineTrack:
    character: str
    text: str
    start: float
    duration: float
    source: str
    envelope: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(1))

    def mouth(self, t: float) -> tuple[float, bool]:
        """Return (mouth openness 0..1, speaking flag) for a line at time ``t``."""
        local = t - self.start
        if local < 0.0 or local > self.duration:
            return 0.0, False
        position = local * MOUTH_HZ
        index = int(position)
        if index >= self.envelope.size - 1:
            level = float(self.envelope[-1]) if self.envelope.size else 0.0
        else:
            fraction = position - index
            level = float(self.envelope[index] * (1.0 - fraction) + self.envelope[index + 1] * fraction)
        return min(1.0, 0.08 + 0.92 * level), True

    def to_dict(self) -> dict[str, Any]:
        return {
            "character": self.character,
            "text": self.text,
            "start": round(self.start, 3),
            "duration": round(self.duration, 3),
            "source": self.source,
            "envelope_peak": round(float(self.envelope.max()), 4) if self.envelope.size else 0.0,
        }


@dataclass
class VoiceTrack:
    """Voice-only audio plus per-line mouth envelopes."""

    path: Path
    samples: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    lines: list[VoiceLineTrack] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def mouth_at(self, character: str, t: float) -> tuple[float, bool]:
        mouth = 0.0
        speaking = False
        for line in self.lines:
            if line.character != character:
                continue
            value, active = line.mouth(t)
            if active:
                speaking = True
                mouth = max(mouth, value)
        return mouth, speaking

    def to_dict(self) -> dict[str, Any]:
        total = sum(line.duration for line in self.lines)
        return {
            "path": self.path.as_posix(),
            "lines": [line.to_dict() for line in self.lines],
            "speech_seconds": round(total, 3),
            "warnings": list(self.warnings),
        }


def build_voice_track(scene: AnimationScene, config: Any, out_dir: Path) -> VoiceTrack | None:
    """Synthesize every dialogue line, place it on the timeline, write ``voice.wav``."""
    if not scene.voice_lines:
        return None
    from ..tts import TTSService

    logger = get_logger()
    service = TTSService(config)
    samples = np.zeros(int(round(scene.duration * AUDIO_RATE)), dtype=np.float64)
    lines: list[VoiceLineTrack] = []
    warnings: list[str] = []

    for index, item in enumerate(scene.voice_lines, 1):
        source, duration = service.synthesize_line(item.text, scene.voice_lang, voice_override=item.voice)
        audio, rate = _read_mono(source)
        audio = _resample(audio, rate, AUDIO_RATE)
        start = int(round(item.start * AUDIO_RATE))
        available = max(0, samples.size - start)
        if audio.size > available:
            warnings.append(
                f"Реплика {index} ({item.character}) длиннее сцены: {duration:.2f} с, "
                f"обрезана до {available / AUDIO_RATE:.2f} с."
            )
            audio = audio[:available]
        samples[start:start + audio.size] += audio
        lines.append(
            VoiceLineTrack(
                character=item.character,
                text=item.text,
                start=item.start,
                duration=audio.size / AUDIO_RATE,
                source=source.as_posix(),
                envelope=_resample_envelope(_envelope(audio, AUDIO_RATE), audio.size / AUDIO_RATE),
            )
        )
        logger.info(
            "Реплика %s (%s): %.2f с, старт %.2f с", index, item.character, audio.size / AUDIO_RATE, item.start
        )

    fade = min(int(0.01 * AUDIO_RATE), samples.size)
    if fade > 0:
        samples[:fade] *= np.linspace(0.0, 1.0, fade)
        samples[-fade:] *= np.linspace(1.0, 0.0, fade)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "voice.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(AUDIO_RATE)
        handle.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return VoiceTrack(path=path, samples=samples, lines=lines, warnings=warnings)


def _resample_envelope(envelope: np.ndarray, duration: float) -> np.ndarray:
    """Stretch an envelope to cover exactly ``duration`` seconds at MOUTH_HZ."""
    count = max(2, int(round(duration * MOUTH_HZ)) + 1)
    if envelope.size == count:
        return envelope
    if envelope.size < 2:
        return np.zeros(count, dtype=np.float64)
    source_x = np.linspace(0.0, 1.0, envelope.size)
    target_x = np.linspace(0.0, 1.0, count)
    return np.interp(target_x, source_x, envelope)
