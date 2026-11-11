"""Единый контракт пайплайна (этап A2).

Story → Storyboard → ScenePlan → RenderJob → Film.

Модуль лёгкий: без импортов LLM/Blender, чтобы его можно было
использовать из любого инструмента (GUI, CLI, тесты), не тянув
тяжёлые зависимости.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

# --------------------------------------------------------------------------
# Story / Storyboard
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StoryIdea:
    """Входная идея для генерации сюжета."""

    idea: str
    scenes: int = 3
    lang: str = "ru"
    seconds: float | None = None

    def __post_init__(self) -> None:
        if not str(self.idea or "").strip():
            raise ValueError("idea не может быть пустой")
        if int(self.scenes) < 1:
            raise ValueError("scenes должно быть >= 1")


@dataclass(frozen=True)
class StoryScene:
    """Одна сцена сториборда (нормализованная)."""

    index: int
    title: str
    location: str = ""
    summary: str = ""
    detail: str = ""
    actor: str = "steve"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], index: int) -> "StoryScene":
        return cls(
            index=index,
            title=str(raw.get("title", "") or ""),
            location=str(raw.get("location", "") or ""),
            summary=str(raw.get("summary", "") or ""),
            detail=str(raw.get("detail", "") or ""),
            actor=str(raw.get("actor", "steve") or "steve"),
        )


@dataclass(frozen=True)
class Storyboard:
    """Готовый сториборд с метаданными."""

    title: str
    logline: str = ""
    source: str = "unknown"  # 'ollama' | 'fallback' | ...
    idea: str = ""
    scenes: tuple[StoryScene, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Storyboard":
        raw_scenes = raw.get("scenes") or []
        scenes = tuple(
            StoryScene.from_dict(s, i) for i, s in enumerate(raw_scenes, 1)
        )
        return cls(
            title=str(raw.get("title", "") or ""),
            logline=str(raw.get("logline", "") or ""),
            source=str(raw.get("source", "unknown") or "unknown"),
            idea=str(raw.get("idea", "") or ""),
            scenes=scenes,
        )

    @property
    def scene_count(self) -> int:
        return len(self.scenes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "logline": self.logline,
            "source": self.source,
            "idea": self.idea,
            "scenes": [
                {
                    "index": s.index,
                    "title": s.title,
                    "location": s.location,
                    "summary": s.summary,
                    "detail": s.detail,
                    "actor": s.actor,
                }
                for s in self.scenes
            ],
        }


# --------------------------------------------------------------------------
# ScenePlan / RenderJob / Film
# --------------------------------------------------------------------------


@dataclass
class ScenePlan:
    """План сцены: кто, что говорит, сколько длится."""

    index: int
    actor: str
    text: str
    duration_s: float
    title: str = ""
    closeup: bool = False

    @classmethod
    def from_story_scene(cls, scene: StoryScene, duration_s: float,
                         closeup: bool = False) -> "ScenePlan":
        return cls(
            index=scene.index,
            actor=scene.actor,
            text=scene.detail or scene.summary or scene.title,
            duration_s=float(duration_s),
            title=scene.title,
            closeup=closeup,
        )


@dataclass
class RenderJob:
    """Задание на рендер одной сцены."""

    scene: ScenePlan
    engine: str = "blocky"
    fps: int = 12
    width: int = 480
    height: int = 720
    samples: int = 8

    def frame_count(self) -> int:
        return max(0, int(round(self.scene.duration_s * self.fps)))


@dataclass
class Film:
    """Финальный артефакт."""

    jobs: list[RenderJob] = field(default_factory=list)
    target_s: float = 600.0
    out_path: Path | None = None
    audio_path: Path | None = None

    def total_target_frames(self) -> int:
        return max(0, int(round(self.target_s * 30)))


# --------------------------------------------------------------------------
# Утилиты
# --------------------------------------------------------------------------


def build_scene_plans(storyboard: Storyboard, *,
                      target_s: float = 600.0,
                      min_scene_s: float = 8.0,
                      closeup_every: int = 0) -> list[ScenePlan]:
    """Распределяет длительность по сценам (пропорционально).

    Каждая сцена получает минимум min_scene_s; оставшееся время
    делится пропорционально весу (1 + длина текста).
    """
    scenes = list(storyboard.scenes)
    if not scenes:
        return []
    text_lens = [max(1, len(s.detail or s.summary or s.title)) for s in scenes]
    total_weight = sum(text_lens)
    plans: list[ScenePlan] = []
    used = 0.0
    for i, sc in enumerate(scenes):
        share = (target_s - min_scene_s * len(scenes)) * text_lens[i] / total_weight
        dur = max(min_scene_s, share + min_scene_s)
        closeup = closeup_every > 0 and (i % closeup_every == 0)
        plans.append(ScenePlan.from_story_scene(sc, dur, closeup=closeup))
        used += dur
    if plans and used > target_s:
        # scale down uniformly
        k = target_s / used
        plans = [
            ScenePlan(p.index, p.actor, p.text, p.duration_s * k,
                      p.title, p.closeup)
            for p in plans
        ]
    return plans


@dataclass(frozen=True)
class Episode:
    """Один эпизод сериала: набор сцен + тайминг (этап D3)."""

    index: int
    title: str
    scenes: tuple[ScenePlan, ...]
    target_s: float = 600.0

    def total_scene_seconds(self) -> float:
        return sum(p.duration_s for p in self.scenes)


def make_episodes(storyboard: Storyboard, *,
                  episodes: int = 3,
                  target_s: float = 600.0,
                  min_scene_s: float = 8.0,
                  closeup_every: int = 0) -> list[Episode]:
    """Разбивает сториборд на ``episodes`` эпизодов.

    Сцены делятся последовательно (порядок сюжета сохраняется);
    каждый эпизод получает одинаковый бюджет ``target_s`` и строит
    свои ScenePlan через build_scene_plans.
    """
    scenes = list(storyboard.scenes)
    if not scenes:
        return []
    n = max(1, int(episodes))
    chunk = max(1, (len(scenes) + n - 1) // n)
    out: list[Episode] = []
    for idx in range(n):
        part = scenes[idx * chunk:(idx + 1) * chunk]
        if not part:
            continue
        partial = Storyboard(
            title=f"{storyboard.title} — серия {idx + 1}",
            logline=storyboard.logline,
            source=storyboard.source,
            idea=storyboard.idea,
            scenes=tuple(part),
        )
        plans = build_scene_plans(
            partial, target_s=target_s, min_scene_s=min_scene_s,
            closeup_every=closeup_every)
        out.append(Episode(idx + 1, partial.title, tuple(plans), target_s))
    return out


def estimate_render_seconds(job: RenderJob, *,
                            base_per_frame: float = 0.06) -> float:
    """Грубая оценка времени рендера одного задания.

    base_per_frame — секунд на кадр при 480×720, 12 fps, 8 samples
    (эмпирически ~0.06 на RTX 3050 Ti).
    """
    frames = job.frame_count()
    scale = (job.width / 480) * (job.height / 720) * (job.samples / 8)
    return frames * base_per_frame * scale


def pick_render_settings(budget_s: float, *, frames: int,
                         base_per_frame: float = 0.06) -> dict[str, int]:
    """Подбирает (width, height, samples, fps) под бюджет времени."""
    if frames <= 0 or budget_s <= 0:
        return {"width": 480, "height": 720, "samples": 8, "fps": 12}
    scale = budget_s / (frames * base_per_frame)
    if scale >= 16:
        return {"width": 1080, "height": 1920, "samples": 32, "fps": 30}
    if scale >= 4:
        return {"width": 720, "height": 1280, "samples": 16, "fps": 24}
    if scale >= 1.5:
        return {"width": 480, "height": 720, "samples": 12, "fps": 12}
    return {"width": 480, "height": 720, "samples": 8, "fps": 12}
