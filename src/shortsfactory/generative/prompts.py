"""Промпты для генерации сюжетов (этап A3).

Модуль отделён от ``blocky_story``: здесь живёт только текстовая логика
(сборка промпта и константы), чтобы ядро генерации оставалось читаемым,
а промпты можно было тестировать и править независимо.
"""
from __future__ import annotations

import json

from .animation_pipeline.skinned_hero import PLAN_ACTION_STATES
from .blocky_scene import (
    AMBIENCE_PRESETS,
    CHARACTERS,
    ENVIRONMENT_PRESETS,
)

#: Props a storyboard may ask for.  Kept in sync with ``blocky_scene``.
PROP_PRESETS = (
    "chest",
    "door",
    "glowing_cube",
    "grass",
    "key",
    "rock",
    "spring_box",
    "stump",
    "tree",
    "umbrella",
)
SFX_PRESETS = ("sparkle", "boing", "pop")
EMOTIONS = (
    "neutral",
    "happy",
    "surprised",
    "sad",
    "angry",
    "confused",
    "worried",
    "scared",
)
ACTIONS = (
    "idle",
    "walk",
    "talk",
    "look_at",
    "listen",
    "nod",
    "shake_head",
    "shrug",
    "point",
    "grab",
    "hold",
    "release",
    "react",
    "scared",
    "stumble",
    "fall",
    "recover",
    "wave",
)
#: остальные глаголы движка, которых нет в курируемом списке выше — промпт
#: обязан предлагать их наравне.
EXTRA_ACTIONS = tuple(sorted(set(PLAN_ACTION_STATES) - set(ACTIONS)))

#: Schema the model is asked to fill.  Spelled out in the prompt because small
#: local models follow a literal example far better than prose.
SCHEMA_HINT = {
    "title": "Название фильма",
    "scenes": [
        {
            "title": "Название сцены",
            "duration": 6.0,
            "environment": "forest",
            "ambience": "forest",
            "actors": [
                {
                    "character": "steve",
                    "origin": [-0.6, 0.95, 0.0],
                    "actions": [
                        {"action": "idle", "start": 0.0, "duration": 6.0}
                    ],
                }
            ],
            "dialogue": [
                {
                    "character": "steve",
                    "text": "Реплика.",
                    "start": 0.6,
                    "duration": 2.0,
                    "emotion": "happy",
                }
            ],
            "props": [
                {"preset": "tree", "x": -1.8, "y": 2.4},
                {"preset": "door", "x": 0.2, "y": 2.2, "state": "closed"}
            ],
            "sfx_events": [[0.6, "pop"]],
        }
    ],
    "handoff_example": {
        "title": "Передача куба",
        "duration": 6.0,
        "environment": "forest",
        "ambience": "forest",
        "actors": [
            {
                "character": "steve",
                "origin": [-0.6, 0.95, 0.0],
                "actions": [
                    {"action": "hold", "start": 0.0, "duration": 5.0,
                     "params": {"prop": "glowing_cube"}},
                    {"action": "release", "start": 5.0, "duration": 1.0,
                     "params": {"prop": "glowing_cube"}},
                ],
            },
            {
                "character": "alex",
                "origin": [0.6, 0.95, 0.0],
                "actions": [
                    {"action": "grab", "start": 3.0, "duration": 1.0,
                     "params": {"prop": "glowing_cube"}},
                ],
            },
        ],
        "props": [
            {"preset": "glowing_cube", "x": -0.6, "y": 0.9,
             "name": "glowing_cube"},
        ],
    },
}

SYSTEM_PROMPT = (
    "Ты режиссёр коротких блочных мультфильмов. Отвечай ТОЛЬКО валидным JSON "
    "без markdown. Используй только перечисленные персонажи, действия, "
    "эмоции, пресеты окружения и предметов. Реплики — короткие, живые, на "
    "заданном языке. Каждая сцена — 4–12 секунд, 1–3 реплики.\n"
    "Драматургия: первая сцена — крючок (сразу что-то странное, смешное или "
    "неожиданное), последняя — ясная концовка или панчлайн; завязку не "
    "растягивай.\n"
    "Эмоция каждой реплики (emotion) управляет мимикой героя — указывай её "
    "всегда. Реакции слушателя (кивки, взгляды на говорящего) ставятся "
    "автоматически: действие listen вручную не задавай, лучше займи второго "
    "героя делом или эмоцией."
)


def build_story_prompt(
    idea: str,
    *,
    scenes: int = 3,
    seconds: float | None = None,
    lang: str = "ru",
) -> str:
    """Prompt that asks for exactly ``scenes`` scenes of the requested length."""
    total = seconds if seconds else 6.0 * max(1, int(scenes))
    target = total / max(1, int(scenes))
    return (
        f"Идея мультфильма: {idea.strip()}\n"
        f"Сделай раскадровку из {int(scenes)} сцен, примерно по {target:.1f} с.\n"
        f"Язык реплик: {lang}.\n"
        f"Персонажи: {', '.join(sorted(CHARACTERS))}.\n"
        "Озвучка доступна ТОЛЬКО для steve (мужской) и alex (женский). "
        "Все реплики пиши только от имени этих двух; остальные персонажи — "
        "немые статисты.\n"
        f"Действия: {', '.join(ACTIONS + EXTRA_ACTIONS)}.\n"
        "Правила взаимодействий: действие grab/pickup/interact/use обязано "
        "указывать предмет через params: {\"prop\": \"Имя\"}; предмет с этим "
        "именем должен быть в props той же сцены. Дверь открывается так: "
        "актёр делает interact с params.prop = имя двери в сцене, где стоит "
        "дверь, и следующие сцены происходят уже по другую сторону. "
        "Ключ/сундук ищи через look_at и grab. Не перемещай персонажей "
        "резко между сценами без перехода.\n"
        "Передача предмета между персонажами: если по сюжету один персонаж "
        "передаёт предмет другому (даёт, протягивает, вручает), НЕ разбивай "
        "это на несколько сцен — сделай ОДНУ сцену, где оба персонажа стоят "
        "рядом: первый держит предмет (действие hold с params.prop = имя "
        "предмета), а второй в той же сцене берёт его из рук (действие grab с "
        "params.prop = то же имя). Предмет должен быть в props этой сцены. "
        "Смотри готовый пример handoff_example ниже. Программа сама выстроит "
        "рукопожатие и момент смены владельца.\n"
        "Постановка диалога: реплики идут по очереди (start возрастает) с "
        "паузой 0.3–0.6 с между ними; положение персонажей между сценами "
        "переносится автоматически, walk между сценами не пиши.\n"
        f"Эмоции: {', '.join(EMOTIONS)}.\n"
        f"Окружение: {', '.join(sorted(ENVIRONMENT_PRESETS))}; "
        f"амбиент: {', '.join(sorted(AMBIENCE_PRESETS))}. "
        "Сцены разнообразь: не держи весь фильм в одном окружении, если "
        "сюжет это позволяет.\n"
        f"Предметы: {', '.join(PROP_PRESETS)}.\n"
        f"SFX: {', '.join(SFX_PRESETS)}.\n"
        "Формат ответа (пример, значения замени своими):\n"
        + json.dumps(SCHEMA_HINT, ensure_ascii=False, indent=2)
    )
