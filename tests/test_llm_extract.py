"""Закалка extract_json: частые ответы маленьких локальных моделей."""
import pytest

from shortsfactory.llm import LLMError, extract_json


def test_plain_object_passes():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_markdown_fence_is_stripped():
    text = "Вот ответ:\n```json\n{\"title\": \"Куб\"}\n```\n"
    assert extract_json(text) == {"title": "Куб"}


def test_text_around_json_is_cut():
    text = 'Ваше величество, вот раскадровка: {"scenes": []} — готово!'
    assert extract_json(text) == {"scenes": []}


def test_top_level_scene_array_is_wrapped():
    scene = {"title": "Находка", "duration": 6.0, "environment": "cave"}
    data = extract_json('[' + _dumps(scene) + ']')
    assert data["scenes"][0] == scene


def _dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def test_trailing_comma_is_repaired():
    text = '{"title": "Куб", "scenes": [{"a": 1},], "lang": "ru",}'
    data = extract_json(text)
    assert data["lang"] == "ru"
    assert data["scenes"] == [{"a": 1}]


def test_nested_trailing_comma_in_array():
    text = '{"scenes": [{"title": "Сцена", "props": [1, 2,],},]}'
    data = extract_json(text)
    assert data["scenes"][0]["props"] == [1, 2]


def test_empty_answer_raises():
    with pytest.raises(LLMError):
        extract_json("")


def test_no_json_raises():
    with pytest.raises(LLMError):
        extract_json("модель ушла в рассуждения и забыла про JSON")


def test_broken_json_still_raises():
    with pytest.raises(LLMError):
        extract_json('{"scenes": [ошибка]')
