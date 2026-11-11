from shortsfactory.nlp import parse


def test_full_production_request(config):
    intent = parse(
        "Создай 3 интересных Shorts о космосе. Длительность 35–45 секунд, "
        "русский и английский языки, профессиональный динамичный монтаж. "
        "Сам выбери визуальный стиль. Подготовь всё для публикации.",
        config,
    )
    assert intent.action == "batch"
    assert intent.count == 3
    assert intent.topic == "космос"
    assert intent.duration == 40
    assert intent.duration_range == (35.0, 45.0)
    assert intent.languages == ["ru", "en"]
    assert intent.profile == "science"
    assert intent.pace == "fast"
    assert intent.needs_clarification is False


def test_status_intent(config):
    intent = parse("Покажи, какие задания завершены, а какие требуют внимания", config)
    assert intent.action == "status"


def test_remake_overrides(config):
    intent = parse("Переделай последний ролик: быстрее темп, другой голос, больше графики", config)
    assert intent.action == "remake"
    assert intent.remake_overrides["pace"] == "fast"
    assert intent.remake_overrides["voice"] == "alternate"
    assert intent.remake_overrides["visual_density"] == "high"


def test_unknown_topic_needs_clarification(config):
    intent = parse("Создай один Short про интересное научное открытие", config)
    assert intent.action == "create"
    assert intent.needs_clarification is True


def test_topic_stops_before_duration_after_comma(config):
    intent = parse("Создай 2 Shorts о космосе, 40 секунд, RU и EN", config)
    assert intent.topic == "космос"
    assert intent.duration == 40
    assert intent.count == 2


def test_topic_stops_before_duration_word_after_comma(config):
    intent = parse("Создай Short о Древнем Риме, длительность 40 секунд, RU и EN", config)
    assert intent.topic == "Древнем Риме"
    assert intent.duration == 40
    assert intent.languages == ["ru", "en"]
