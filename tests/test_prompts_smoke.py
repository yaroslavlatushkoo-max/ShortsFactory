"""Smoke-тесты промптов (generative/prompts.py): чистая текстовая логика."""
from __future__ import annotations

import json

from shortsfactory.generative.animation_pipeline.blocky_hero import CHARACTERS
from shortsfactory.generative.animation_pipeline.skinned_hero import PLAN_ACTION_STATES
from shortsfactory.generative.prompts import (
    ACTIONS,
    EXTRA_ACTIONS,
    PROP_PRESETS,
    SCHEMA_HINT,
    SFX_PRESETS,
    SYSTEM_PROMPT,
    build_story_prompt,
)


def test_build_story_prompt_contains_idea_and_characters():
    idea = "Ёжик нашёл светящийся куб"
    prompt = build_story_prompt(idea, scenes=3, seconds=18.0, lang="ru")
    assert idea in prompt
    # строка персонажей перечисляет всех героев движка
    for name in sorted(CHARACTERS):
        assert name in prompt
    assert "Язык реплик: ru" in prompt


def test_build_story_prompt_lists_actions_including_engine_verbs():
    prompt = build_story_prompt("Идея", scenes=3)
    assert "Действия:" in prompt
    for action in ("walk", "grab", "hold", "release"):
        assert action in prompt
    # EXTRA_ACTIONS выведен из PLAN_ACTION_STATES и обязан попасть в промпт
    assert EXTRA_ACTIONS == tuple(sorted(set(PLAN_ACTION_STATES) - set(ACTIONS)))
    for action in EXTRA_ACTIONS:
        assert action in prompt


def test_build_story_prompt_has_handoff_example_and_presets():
    prompt = build_story_prompt("Идея", scenes=2)
    assert "handoff_example" in prompt
    for preset in PROP_PRESETS:
        assert preset in prompt
    for sfx in SFX_PRESETS:
        assert sfx in prompt
    assert "из 2 сцен" in prompt


def test_build_story_prompt_stays_small_for_local_models():
    prompt = build_story_prompt("Идея", scenes=3, seconds=18.0)
    assert len(prompt) < 8000  # защита от разрастания промпта для 7B-моделей


def test_schema_hint_json_round_trip():
    payload = json.dumps(SCHEMA_HINT, ensure_ascii=False)
    assert json.loads(payload) == SCHEMA_HINT
    # пример в промпте — это ровно SCHEMA_HINT
    assert json.dumps(SCHEMA_HINT, ensure_ascii=False, indent=2) in build_story_prompt("Идея")


def test_system_prompt_mentions_json_and_dramaturgy():
    assert "JSON" in SYSTEM_PROMPT
    assert "Драматургия" in SYSTEM_PROMPT
    assert SYSTEM_PROMPT.strip()  # системный промпт не пустой
