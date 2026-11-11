from __future__ import annotations

import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

from .config import Config
from .logging_setup import get_logger
from .util import atomic_write_json, now_iso


class MusicError(RuntimeError):
    pass


SAMPLE_RATE = 48000


def _note(root: float, semitones: int) -> float:
    return root * (2 ** (semitones / 12))


class MusicService:
    """Small procedural ambient bed. No external audio or licensing dependencies."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = get_logger()

    def generate(self, profile: dict[str, Any], duration: float, workspace: Path, lang: str) -> dict[str, Any] | None:
        if not bool(self.config.get("audio", "music", False)):
            return None
        duration = max(4.0, float(duration))
        path = workspace / "audio" / "music.wav"
        meta_path = workspace / "audio" / "music.json"
        if path.exists() and meta_path.exists():
            try:
                import json

                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if abs(float(meta.get("duration", 0)) - duration) < 0.2:
                    return meta
            except (OSError, ValueError):
                pass

        mood = str(profile.get("music", "calm"))
        if mood in {"epic", "cinematic"}:
            roots = [110.0, 87.31, 130.81, 98.0]
            brightness = 0.55
        elif mood in {"driving", "energetic"}:
            roots = [130.81, 110.0, 146.83, 98.0]
            brightness = 0.75
        elif mood in {"reflective", "mysterious"}:
            roots = [98.0, 82.41, 110.0, 73.42]
            brightness = 0.35
        else:
            roots = [110.0, 130.81, 98.0, 146.83]
            brightness = 0.45

        total_samples = int(duration * SAMPLE_RATE)
        t = np.arange(total_samples, dtype=np.float64) / SAMPLE_RATE
        bar = duration / 4.0
        mix = np.zeros(total_samples, dtype=np.float64)
        chord_index = np.zeros(total_samples, dtype=np.int32)
        for index in range(4):
            chord_index[(t >= index * bar) & (t < (index + 1) * bar)] = index
        for index, root in enumerate(roots):
            active = chord_index == index
            if not np.any(active):
                continue
            local_t = t[active] - index * bar
            local = np.zeros_like(local_t)
            for semitone, level in ((0, 0.30), (7, 0.18), (12, 0.12), (3, 0.10) if index % 2 else (4, 0.10)):
                frequency = _note(root, semitone)
                # Slow detune creates a soft, wide pad without sounding like a test tone.
                local += level * np.sin(2 * np.pi * frequency * local_t)
                local += level * 0.45 * np.sin(2 * np.pi * frequency * 1.003 * local_t + 0.4)
            attack = np.minimum(1.0, local_t / 1.2)
            release = np.minimum(1.0, np.maximum(0.0, (bar - local_t) / 1.0))
            mix[active] += local * attack * release

        # Sparse high arpeggio, one soft note per half-bar.
        step = max(1, int(bar / 2 * SAMPLE_RATE))
        notes = [12, 19, 24, 19, 12, 24, 19, 12]
        for index, semitone in enumerate(notes):
            start = index * step
            if start >= total_samples:
                break
            length = min(int(1.8 * SAMPLE_RATE), total_samples - start)
            local_t = np.arange(length) / SAMPLE_RATE
            root = roots[index % len(roots)]
            envelope = np.exp(-local_t * 2.4) * np.minimum(1.0, local_t / 0.02)
            tone = np.sin(2 * np.pi * _note(root, semitone + 12) * local_t)
            mix[start : start + length] += tone * envelope * 0.055 * brightness

        # Gentle low pulse for pace, no percussion samples.
        beat = max(1, int(bar / 4 * SAMPLE_RATE))
        for start in range(0, total_samples, beat):
            length = min(int(0.35 * SAMPLE_RATE), total_samples - start)
            local_t = np.arange(length) / SAMPLE_RATE
            envelope = np.exp(-local_t * 18) * 0.05
            mix[start : start + length] += np.sin(2 * np.pi * 55 * local_t) * envelope

        # Very light stereo width; mono-compatible.
        left = mix.copy()
        right = np.roll(mix, int(0.011 * SAMPLE_RATE)) * 0.92
        fade_in = np.minimum(1.0, t / 1.2)
        fade_out = np.minimum(1.0, np.maximum(0.0, (duration - t) / 1.5))
        envelope = fade_in * fade_out
        left *= envelope
        right *= envelope
        peak = max(float(np.max(np.abs(left))), float(np.max(np.abs(right))), 1e-6)
        scale = 0.32 / peak
        stereo = np.stack([left * scale, right * scale], axis=1)
        pcm = np.clip(stereo * 32767, -32768, 32767).astype("<i2")

        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".part")
        try:
            with wave.open(str(temp), "wb") as handle:
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(SAMPLE_RATE)
                handle.writeframes(pcm.tobytes())
            # On Windows the destination may be briefly locked (e.g. an FFmpeg pass still
            # reading it); retry instead of failing, and never leave a stray .part behind.
            for attempt in range(5):
                try:
                    temp.replace(path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.5)
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass
        meta = {
            "path": path.as_posix(),
            "duration": round(duration, 3),
            "sample_rate": SAMPLE_RATE,
            "channels": 2,
            "mood": mood,
            "license": "Generated procedurally by ShortsFactory; no third-party audio.",
            "generated_at": now_iso(),
        }
        atomic_write_json(meta_path, meta)
        self.logger.info("Музыка: %.1f с, настроение %s", duration, mood)
        return meta
