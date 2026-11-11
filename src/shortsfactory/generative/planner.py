"""Story intelligence: free text -> structured ScenePlan list (Mission 3 §3A).

Decoupled from frame creation. Uses the local Ollama when available,
otherwise a deterministic paragraph splitter so the pipeline still runs
offline and testably.
"""

from __future__ import annotations

import re
from typing import Any

from ..llm import LLMError, OllamaClient
from .models import ScenePlan

_SYSTEM = (
    "Ты — сторипланерический ИИ для вертикального видео 9:16. "
    "Разбей историю на короткие сцены. Верни ТОЛЬКО JSON-объект вида: "
    '{"scenes":[{"location":"...","characters":["..."],"action_summary":"...",'
    '"dialogue":"...","emotion":"...","visual_goal":"...","duration":5.0}]}. '
    "Каждая сцена: 1 локация, 1–2 персонажа, краткое действие, эмоция, "
    "визуальная цель. Длительность 4–10 секунд."
)


def _empty_text(text: str) -> bool:
    return not (text or "").strip()


def _split_deterministic(text: str) -> list[ScenePlan]:
    """Very simple, offline fallback: one scene per paragraph."""
    scenes: list[ScenePlan] = []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]
    for idx, para in enumerate(paragraphs):
        if len(para) > 400:
            para = para[:400]
        scenes.append(
            ScenePlan(
                idx=idx,
                location="general",
                characters=[],
                action_summary=para,
                dialogue="",
                emotion="neutral",
                visual_goal="show the described moment clearly",
                duration=5.0,
            )
        )
    return scenes


def _clean_scenes(obj: Any) -> list[ScenePlan]:
    scenes: list[ScenePlan] = []
    raw = obj.get("scenes") if isinstance(obj, dict) else None
    if not isinstance(raw, list):
        return scenes
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        try:
            duration = float(item.get("duration", 5.0))
        except (TypeError, ValueError):
            duration = 5.0
        duration = min(max(duration, 3.0), 10.0)
        scenes.append(
            ScenePlan(
                idx=idx,
                location=str(item.get("location", "general")),
                characters=[str(c) for c in item.get("characters", []) if isinstance(c, str)],
                action_summary=str(item.get("action_summary", "")),
                dialogue=str(item.get("dialogue", "")),
                emotion=str(item.get("emotion", "neutral")),
                visual_goal=str(item.get("visual_goal", "")),
                duration=duration,
            )
        )
    return scenes


def plan_story(text: str, llm: OllamaClient | None = None) -> list[ScenePlan]:
    """Turn a story text into a list of ScenePlan. Never raises: falls back to
    the deterministic splitter if the LLM is unavailable or returns garbage."""
    if _empty_text(text):
        raise ValueError("Сценарий пуст.")
    scenes: list[ScenePlan] = []
    if llm is not None:
        try:
            prompt = f"История:\n{text}\n\nВерни JSON со списком сцен."
            obj = llm.generate_json(prompt, system=_SYSTEM, temperature=0.4)
            scenes = _clean_scenes(obj)
        except (LLMError, ValueError, TypeError):
            scenes = []
    if not scenes:
        scenes = _split_deterministic(text)
    return scenes
