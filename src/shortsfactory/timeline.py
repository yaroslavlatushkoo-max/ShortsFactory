from __future__ import annotations

import math
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .models import Script
from .util import atomic_write_json, now_iso


class TimelineError(RuntimeError):
    pass


@dataclass
class SceneTiming:
    scene_id: str
    speech_duration: float
    scene_duration: float
    video_start: float
    audio_start: float
    tail: float


@dataclass
class Timeline:
    lang: str
    scenes: list[SceneTiming] = field(default_factory=list)
    total_video: float = 0.0
    total_audio: float = 0.0
    transition: float = 0.4
    sample_rate: int = 22050
    target: float = 0.0
    fit_note: str = ""
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "lang": self.lang,
            "scenes": [asdict(scene) for scene in self.scenes],
            "total_video": round(self.total_video, 3),
            "total_audio": round(self.total_audio, 3),
            "transition": self.transition,
            "sample_rate": self.sample_rate,
            "target": self.target,
            "fit_note": self.fit_note,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Timeline":
        return cls(
            lang=str(data.get("lang", "")),
            scenes=[SceneTiming(**item) for item in data.get("scenes", [])],
            total_video=float(data.get("total_video", 0) or 0),
            total_audio=float(data.get("total_audio", 0) or 0),
            transition=float(data.get("transition", 0.4) or 0.4),
            sample_rate=int(data.get("sample_rate", 22050) or 22050),
            target=float(data.get("target", 0) or 0),
            fit_note=str(data.get("fit_note", "")),
            generated_at=str(data.get("generated_at", "")),
        )

    def timing(self, scene_id: str) -> SceneTiming | None:
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                return scene
        return None


def _duration(manifest_scene: dict[str, Any]) -> float:
    value = float(manifest_scene.get("duration", 0) or 0)
    if value <= 0:
        raise TimelineError(f"Сцена {manifest_scene.get('scene_id')}: нулевая длительность озвучки")
    return value


def _compute(
    speech: list[float], lead: float, tail: float, last_tail: float, xfade: float, hold: float
) -> tuple[list[float], list[float], float]:
    durations: list[float] = []
    starts: list[float] = []
    current = 0.0
    count = len(speech)
    for index, value in enumerate(speech):
        starts.append(current)
        scene_tail = last_tail if index == count - 1 else tail
        duration = lead + value + scene_tail
        durations.append(duration)
        current += duration - (xfade if index < count - 1 else 0.0)
    total = current + hold
    return durations, starts, total


def build(
    script: Script,
    manifest: dict[str, Any],
    config: Config,
    *,
    target: float | None = None,
) -> Timeline:
    scenes_manifest = {str(item.get("scene_id")): item for item in manifest.get("scenes", [])}
    speech: list[float] = []
    for scene in script.scenes:
        item = scenes_manifest.get(scene.id)
        if not item:
            raise TimelineError(f"Нет озвучки для сцены {scene.id}")
        speech.append(_duration(item))
    if not speech:
        raise TimelineError("Пустой сценарий")

    lead = float(config.get("video", "lead_in", 0.15))
    tail_base = float(config.get("video", "scene_tail", 0.6))
    last_tail_base = float(config.get("video", "last_scene_tail", 1.4))
    xfade = float(config.get("video", "transition", 0.4))
    target = float(target if target is not None else 0)
    note = ""

    tail = tail_base
    last_tail = last_tail_base
    hold = 0.0
    durations, starts, total = _compute(speech, lead, tail, last_tail, xfade, hold)

    if target > 0:
        diff = target - total
        # Adjust the breathing room before changing the voice speed.
        if abs(diff) > 0.35:
            count = len(speech)
            delta = diff / max(1, count)
            candidate = max(0.25, min(1.35, tail + delta))
            candidate_last = max(0.7, min(3.0, last_tail + delta))
            durations, starts, total = _compute(speech, lead, candidate, candidate_last, xfade, 0.0)
            if total < target - 0.25:
                hold = min(3.0, target - total)
                total += hold
                note = f"Добавлено удержание кадра {hold:.2f} с для целевой длительности."
            elif total > target + 0.35:
                note = f"Речь длиннее цели на {total - target:.2f} с; тайминг не ускорялся ниже допустимого."
            else:
                note = f"Паузы подогнаны под цель ({target:.1f} с)."
            tail, last_tail = candidate, candidate_last
        else:
            note = f"Длительность близка к цели ({total:.1f} с)."

    for scene, value, duration, start in zip(script.scenes, speech, durations, starts):
        scene.speech_duration = round(value, 3)
        scene.scene_duration = round(duration, 3)
        scene.video_start = round(start, 3)
        scene.audio_start = round(start + lead, 3)

    return Timeline(
        lang=script.lang,
        scenes=[
            SceneTiming(
                scene_id=scene.id,
                speech_duration=scene.speech_duration,
                scene_duration=scene.scene_duration,
                video_start=scene.video_start,
                audio_start=scene.audio_start,
                tail=round(last_tail if index == len(script.scenes) - 1 else tail, 3),
            )
            for index, scene in enumerate(script.scenes)
        ],
        total_video=round(total, 3),
        total_audio=round(total, 3),
        transition=xfade,
        target=round(target, 3),
        fit_note=note,
        generated_at=now_iso(),
    )


def _read_wav(path: Path) -> tuple[bytes, int]:
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
                raise TimelineError(f"Ожидался mono 16-bit WAV: {path}")
            return handle.readframes(handle.getnframes()), handle.getframerate()
    except wave.Error as exc:
        raise TimelineError(f"Не читается WAV {path}: {exc}") from exc


def write_narration(
    workspace: Path,
    lang: str,
    manifest: dict[str, Any],
    timeline: Timeline,
    sample_rate: int = 22050,
) -> Path:
    """Place every scene recording at its exact video start, keeping silence between scenes."""
    audio_dir = workspace / "audio" / lang
    audio_dir.mkdir(parents=True, exist_ok=True)
    total_samples = max(1, int(math.ceil(timeline.total_video * sample_rate)))
    buffer = bytearray(total_samples * 2)
    scenes_manifest = {str(item.get("scene_id")): item for item in manifest.get("scenes", [])}

    for timing in timeline.scenes:
        item = scenes_manifest.get(timing.scene_id)
        if not item:
            raise TimelineError(f"Нет файла озвучки для {timing.scene_id}")
        scene_path = audio_dir / str(item.get("file", ""))
        frames, rate = _read_wav(scene_path)
        if rate != sample_rate:
            raise TimelineError(
                f"Частота {rate} Гц не совпадает с ожидаемой {sample_rate} Гц ({scene_path})"
            )
        offset = int(round(timing.audio_start * sample_rate))
        frame_count = len(frames) // 2
        end = offset + frame_count
        if end > len(buffer) // 2:
            raise TimelineError(
                f"Озвучка {timing.scene_id} выходит за границу видео: {end / sample_rate:.2f} с"
            )
        buffer[offset * 2 : end * 2] = frames[: frame_count * 2]

    path = audio_dir / "narration.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(buffer))

    atomic_write_json(audio_dir / "timeline.json", timeline.to_dict())
    return path


def sentence_windows(manifest: dict[str, Any], timeline: Timeline) -> list[dict[str, Any]]:
    """Absolute sentence timings, used for subtitles."""
    windows: list[dict[str, Any]] = []
    for scene in timeline.scenes:
        item = next(
            (entry for entry in manifest.get("scenes", []) if entry.get("scene_id") == scene.scene_id),
            None,
        )
        if not item:
            continue
        for sentence in item.get("sentences", []):
            start = scene.audio_start + float(sentence.get("start", 0) or 0)
            duration = float(sentence.get("duration", 0) or 0)
            windows.append(
                {
                    "scene_id": scene.scene_id,
                    "text": str(sentence.get("text", "")),
                    "start": round(start, 3),
                    "end": round(start + duration, 3),
                    "duration": round(duration, 3),
                }
            )
    return windows
