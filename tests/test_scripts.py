import re

import pytest

from shortsfactory.models import Plan, Source
from shortsfactory.scripts import ScriptService, ScriptValidationError, _clip_caption, validate_payload


SOURCE = Source(
    id="S1",
    title="Test",
    url="https://example.test",
    publisher="Test",
    language="en",
    accessed_at="2026-10-22",
)


def _text(words: int = 20) -> str:
    return " ".join(["word"] * words)


def test_sentence_payload_builds_exactly_five_scenes():
    payload = {
        "title": "Test",
        "sentences": [_text() for _ in range(5)],
        "visual_queries": ["ancient rome ruins"] * 5,
        "on_screen": ["Rome"] * 5,
    }
    normalized = ScriptService._sentence_payload(payload, [SOURCE])
    assert len(normalized["scenes"]) == 5
    assert normalized["scenes"][0]["text"].count(" ") + 1 == 20
    assert normalized["scenes"][0]["source_ids"] == ["S1"]
    assert normalized["scenes"][0]["visual_query"] == "ancient rome ruins"


def test_sentence_payload_attributes_sources_by_content():
    bread = Source(
        id="S1",
        title="Bread",
        url="https://example.test/bread",
        publisher="Test",
        language="ru",
        accessed_at="2026-10-22",
        extract="В V веке до н. э. эммер заменили пшеницей, и римляне начали есть хлеб.",
    )
    wars = Source(
        id="S2",
        title="Wars",
        url="https://example.test/wars",
        publisher="Test",
        language="ru",
        accessed_at="2026-10-22",
        extract="В 235–284 годах нашей эры произошло 26 гражданских войн.",
    )
    payload = {
        "title": "Test",
        "sentences": [
            "В V веке до н. э. эммер заменили пшеницей, и римляне начали есть хлеб.",
            "В 235–284 годах нашей эры произошло 26 гражданских войн.",
            "Римляне выпекали хлеб дома и покупали его в городских лавках.",
            "Хлеб быстро стал популярным среди бедных жителей Рима.",
            "Общественные пекарни снабжали хлебом городские кварталы.",
        ],
        "visual_queries": ["roman bread", "roman wars", "roman bakery", "roman bread", "roman bakery"],
        "on_screen": ["Хлеб", "Войны", "Пекарни", "Хлеб", "Пекарни"],
    }
    normalized = ScriptService._sentence_payload(payload, [bread, wars])
    assert normalized["scenes"][0]["source_ids"] == ["S1"]
    assert normalized["scenes"][1]["source_ids"] == ["S2"]


def test_sentence_payload_accepts_seven_scenes():
    payload = {
        "title": "Test",
        "sentences": [_text() for _ in range(7)],
        "visual_queries": ["ancient rome ruins"] * 7,
        "on_screen": ["Rome"] * 7,
    }
    normalized = ScriptService._sentence_payload(payload, [SOURCE])
    assert len(normalized["scenes"]) == 7


def test_sentence_payload_accepts_scene_objects():
    payload = {
        "title": "Test",
        "hook": "Hook",
        "scenes": [
            {"text": _text(18), "visual_query": "ancient rome", "on_screen": "Рим"}
            for _ in range(5)
        ],
    }
    normalized = ScriptService._sentence_payload(payload, [SOURCE])
    assert len(normalized["scenes"]) == 5
    assert normalized["scenes"][0]["visual_query"] == "ancient rome"
    assert normalized["scenes"][0]["on_screen"] == "Рим"


def test_validate_rejects_missing_visual_metadata(config):
    plan = Plan.from_dict({"topic": "Тема", "topic_en": "Topic", "duration": 40.0})
    scenes = [
        {"text": _text(18), "visual_query": "ancient rome", "on_screen": "Рим", "source_ids": ["S1"]}
        for _ in range(5)
    ]
    scenes[0] = {"text": _text(18), "source_ids": ["S1"]}
    with pytest.raises(ScriptValidationError) as exc:
        validate_payload(
            {"title": "Test", "hook": "Hook", "scenes": scenes},
            sources=[SOURCE],
            lang="ru",
            plan=plan,
            config=config,
        )
    message = "; ".join(exc.value.errors)
    assert "visual_query" in message
    assert "on_screen" in message


def test_sentence_payload_rejects_wrong_count():
    for count in (3, 8):
        payload = {"sentences": [_text() for _ in range(count)]}
        with pytest.raises(ScriptValidationError):
            ScriptService._sentence_payload(payload, [SOURCE])


def test_clip_caption_cuts_at_word_boundary():
    caption = _clip_caption("Древний Рим — цивилизация достигла пика могущества")
    assert caption.endswith("…")
    assert "цивилизаци…" not in caption
    assert len(caption) <= 34


def test_validate_builds_readable_caption_for_foreign_on_screen(config):
    source = Source(
        id="S1",
        title="Test",
        url="https://example.test",
        publisher="Test",
        language="ru",
        accessed_at="2026-10-22",
        extract="В V веке до н. э. эммер заменили пшеницей.",
    )
    plan = Plan.from_dict({"topic": "Тема", "topic_en": "Topic", "duration": 40.0})
    texts = [
        "В V веке до н. э. эммер заменили культурной пшеницей и начали печь хлеб для всех горожан города",
        "Хлеб быстро стал популярным среди бедных жителей огромного древнего города и его многочисленных окрестностей",
        "Общественные пекарни появились в Риме и снабжали свежим хлебом городские кварталы и рынки",
        "Гражданские войны ослабили республику и изменили жизнь римского общества на долгие годы вперёд",
        "Римские пекари делали разные сорта хлеба для богатых и бедных горожан каждый день без выходных и праздников",
    ]
    payload = {
        "title": "Русский заголовок о теме",
        "hook": "Короткий тизер для зрителя.",
        "scenes": [
            {
                "text": text,
                "visual_query": "ancient rome",
                "on_screen": "509 BC" if index == 0 else "Хлеб",
                "source_ids": ["S1"],
            }
            for index, text in enumerate(texts)
        ],
        "cta": "",
    }
    script = validate_payload(payload, sources=[source], lang="ru", plan=plan, config=config)
    assert script.scenes[0].on_screen == "В V веке до н. э."
    assert script.scenes[1].on_screen == "Хлеб"


def test_validate_rejects_roman_numeral_not_in_sources(config):
    source = Source(
        id="S1",
        title="Test",
        url="https://example.test",
        publisher="Test",
        language="ru",
        accessed_at="2026-10-22",
        extract="В V веке до н. э. эммер заменили пшеницей.",
    )
    plan = Plan.from_dict({"topic": "Тема", "topic_en": "Topic", "duration": 40.0})
    texts = [
        "Римляне выращивали пшеницу и делали из неё хлеб для своих городов",
        "В IV веке до н. э. эммер заменили культурной пшеницей и начали печь хлеб",
        "Хлеб быстро стал популярным среди бедных жителей огромного древнего города",
        "Общественные пекарни появились в Риме и снабжали хлебом городские кварталы",
        "Гражданские войны ослабили республику и изменили жизнь римского общества",
    ]
    payload = {
        "title": "Русский заголовок о теме",
        "hook": "Короткий тизер для зрителя.",
        "scenes": [
            {
                "text": text,
                "visual_query": "ancient rome",
                "on_screen": "Рим",
                "source_ids": ["S1"],
            }
            for text in texts
        ],
        "cta": "",
    }
    with pytest.raises(ScriptValidationError) as error:
        validate_payload(payload, sources=[source], lang="ru", plan=plan, config=config)
    assert any("4" in item and "источниках" in item for item in error.value.errors)


def test_validate_rejects_english_body_for_russian_script(config):
    plan = Plan.from_dict({"topic": "Тема", "topic_en": "Topic", "duration": 40.0})
    payload = {
        "title": "Русский заголовок о теме",
        "hook": "Короткий тизер для зрителя.",
        "scenes": [
            {
                "text": " ".join(["word"] * 15),
                "visual_query": "ancient rome",
                "on_screen": "Rome",
                "source_ids": ["S1"],
            }
            for _ in range(7)
        ],
        "cta": "",
    }
    with pytest.raises(ScriptValidationError) as error:
        validate_payload(payload, sources=[SOURCE], lang="ru", plan=plan, config=config)
    assert any("кириллиц" in item for item in error.value.errors)


def test_validate_replaces_foreign_on_screen(config):
    plan = Plan.from_dict({"topic": "Тема", "topic_en": "Topic", "duration": 40.0})
    texts = [
        "Хлеб и гражданские войны определили будущее великого Древнего Рима на века",
        "Римская империя расширялась благодаря дисциплине легионов и умелой организации",
        "Пшеница и эммер кормили римские города, провинции и многочисленные армии",
        "Гражданские войны ослабили республику и ускорили её окончательное падение",
        "Юлий Цезарь изменил устройство государства, сосредоточив власть в своих руках",
        "Общественные пекарни обеспечивали дешёвым хлебом бедные слои населения",
        "Падение западной империи завершило античную эпоху и изменило историю Европы навсегда и бесповоротно",
    ]
    payload = {
        "title": "Русский заголовок о теме",
        "hook": "Короткий тизер для зрителя.",
        "scenes": [
            {
                "text": text,
                "visual_query": "ancient rome",
                "on_screen": "476年",
                "source_ids": ["S1"],
            }
            for text in texts
        ],
        "cta": "",
    }
    script = validate_payload(payload, sources=[SOURCE], lang="ru", plan=plan, config=config)
    assert all(re.search(r"[\u0400-\u04FF]", scene.on_screen) for scene in script.scenes)
