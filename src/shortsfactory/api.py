"""Стабильный API-слой ShortsFactory (этап E1).

Python-функции поверх ядра (не CLI): генерация сюжета, сохранение,
рендер отдельной сцены.  Импорт лёгкий (без torch/Blender).
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .generative.blocky_story import (
    StoryError,
    fallback_storyboard,
    generate_storyboard,
    write_storyboard,
)
from .generative.contracts import ScenePlan, RenderJob


def generate_story(idea: str, *, scenes: int = 3, lang: str = "ru",
                   stream: bool = False,
                   on_token: Callable[[str], None] | None = None,
                   attempts: int = 2) -> dict[str, Any]:
    """Идея → сториборд (dict).  Ollama (со стримингом) → fallback."""
    return generate_storyboard(
        idea, scenes=scenes, lang=lang,
        stream=stream, on_token=on_token, attempts=attempts,
    )


def save_story(story: dict[str, Any], out_dir: str | Path) -> Path:
    """Сохраняет сториборд как story.json (и возвращает путь)."""
    return write_storyboard(story, Path(out_dir) / "story.json")


def render_scene_clip(plan: ScenePlan, out_dir: str | Path, *,
                      engine: str = "blocky",
                      fps: int = 12, width: int = 480, height: int = 720,
                      samples: int = 8, timeout: int = 1800,
                      use_cache: bool = True) -> Path:
    """Рендерит одну сцену в короткий клип (MP4).

    ``use_cache=True``: готовый клип переиспользуется только при совпадении
    плана сцены и параметров рендера с сохранёнными метаданными кэша.
    """
    if engine not in ("blocky",):
        raise ValueError(f"Движок {engine!r} пока не поддерживается в API")
    from .generative.animation_pipeline import blocky_hero as bh

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"scene_{plan.index:02d}_final.mp4"
    cache_path = out.with_suffix(".cache.json")
    cache_key = hashlib.sha256(json.dumps(
        {"version": 1, "scene": asdict(plan), "engine": engine,
         "fps": fps, "width": width, "height": height, "samples": samples},
        sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    if use_cache and out.is_file() and out.stat().st_size > 0:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
        if isinstance(cached, dict) and cached.get("input_hash") == cache_key:
            return out
    # A failed refresh must not leave metadata validating an older clip.
    cache_path.unlink(missing_ok=True)
    actors = [{
        "character": plan.actor,
        "origin": (0.0, 0.0, 0.0),
        "actions": [
            {"action": "idle", "start": 0.0, "duration": 0.5},
            {"action": "talk", "start": 0.5, "duration": max(0.5, plan.duration_s - 1.0)},
            {"action": "idle", "start": plan.duration_s - 0.5, "duration": 0.5},
        ],
        "voice_lines": [{"start": 0.5, "duration": max(0.5, plan.duration_s - 1.0),
                         "text": plan.text}],
        "mouth_skin": {"text": plan.text, "lang": "ru",
                       "start": 0.5, "duration": max(0.5, plan.duration_s - 1.0)},
    }]
    camera_cuts = [{"frame": 1, "center": (0.0, 0.0, 0.95),
                    "height": 1.15 if plan.closeup else 2.6,
                    "lens": 50.0 if plan.closeup else 42.0,
                    "fit": 0.85}]
    script = bh.build_blocky_scene_script(
        out_dir, actors, camera_cuts=camera_cuts,
        scenario=f"scene_{plan.index:02d}", fps=fps,
        width=width, height=height, samples=samples,
    )
    # 2.4: пре-рендер геометрический QC по payload — стоп до Blender при провале
    from .generative.animation_pipeline.blocky_qc_gate import enforce_pre_render_gate
    enforce_pre_render_gate(out_dir, f"scene_{plan.index:02d}", label=f"сцена {plan.index}")
    result = bh.render_skinned(script, timeout=timeout)
    if result.status != "OK":
        raise RuntimeError(f"Рендер сцены {plan.index} провалился: {result.error}")
    video = Path(result.video) if result.video else None
    if video is None or not video.is_file() or video.stat().st_size == 0:
        raise RuntimeError(f"Рендер сцены {plan.index} не создал видео")
    out.write_bytes(video.read_bytes())
    cache_path.write_text(json.dumps({"input_hash": cache_key}), encoding="utf-8")
    return out


def qc_pre_render(description: dict | Any, *,
                  fps: int | None = None,
                  reach: float = 0.18,
                  min_gap: float = 0.05) -> dict:
    """Пре-рендер QC через AnimationLab (коллизии/досягаемость/FSM).

    ``description`` — словарь-описание сцены (см.
    ``shortsfactory.generative.animlab_qc.build_scene_description``) или
    готовый объект ``SceneDescription``.  Возвращает отчёт QC.
    """
    from .generative.animlab_qc import run_pre_render_qc

    return run_pre_render_qc(description, fps=fps, reach=reach,
                             min_gap=min_gap)


def render_job(job: RenderJob, out_dir: str | Path, *,
               timeout: int = 1800, use_cache: bool = True) -> Path:
    """Диспетчер движков (этап A4): один интерфейс на любое задание.

    ``job`` — ``RenderJob`` из ``contracts``; ``engine`` выбирает
    реализацию.  Сейчас полноценно поддерживается ``blocky``; остальные
    движки объявлены честно (not implemented), а не молча игнорируются.
    """
    out_dir = Path(out_dir)
    if job.engine == "blocky":
        return render_scene_clip(
            job.scene, out_dir,
            fps=job.fps, width=job.width, height=job.height,
            samples=job.samples, timeout=timeout, use_cache=use_cache,
        )
    if job.engine in ("voxel", "procedural", "skinned"):
        raise NotImplementedError(
            f"Движок {job.engine!r} заявлен в интерфейсе, но ещё не "
            "подключён к api.render_job (только blocky).")
    raise ValueError(f"Неизвестный движок: {job.engine!r}")


__all__ = [
    "StoryError",
    "fallback_storyboard",
    "generate_story",
    "save_story",
    "render_scene_clip",
    "render_job",
    "qc_pre_render",
    "ScenePlan",
    "RenderJob",
]
