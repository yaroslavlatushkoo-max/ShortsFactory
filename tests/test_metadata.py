from shortsfactory.metadata import MetadataService
from shortsfactory.models import Scene, Script, Source


def test_hook_is_not_repeated_in_description():
    hook = "Кольца есть у всех планет-гигантов."
    body = "Кольца есть у всех планет-гигантов. У Сатурна они самые заметные."
    summary = MetadataService._dedupe_hook(hook, body)
    assert summary == "У Сатурна они самые заметные."


def test_summary_uses_complete_sentences():
    text = (
        "Первое предложение достаточно длинное, чтобы проверить границу. "
        "Второе предложение добавляет подробности о кольцах Сатурна. "
        "Третье предложение не должно обрываться на середине."
    )
    summary = MetadataService._summarize(text, 140)
    assert summary.endswith("Сатурна.")
    assert "…" not in summary
    assert len(summary) <= 140


def test_summary_clips_single_overlong_sentence():
    summary = MetadataService._summarize("слово " * 100, 50)
    assert len(summary) <= 50
    assert summary.endswith("…")


def test_description_core_has_no_source_or_hashtag_blocks():
    script = Script(
        lang="ru",
        title="Тест",
        hook="Короткий хук о Риме.",
        scenes=[Scene(id="scene_01", text="Первая сцена о Риме.")],
    )
    source = Source(
        id="S1",
        title="Источник",
        url="https://example.com/source",
        publisher="Test",
        language="ru",
        accessed_at="2026-10-22",
    )
    core = MetadataService._description_core(script)
    description = MetadataService._description(script, [source], ["#история"], "ru")
    assert source.url not in core
    assert "#история" not in core
    assert description.count(source.url) == 1
    assert description.count("#история") == 1
