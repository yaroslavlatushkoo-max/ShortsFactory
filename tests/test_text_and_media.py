from PIL import ImageFont

from shortsfactory.editing import RenderService
from shortsfactory.media import MediaService, license_allowed
from shortsfactory.models import Scene, Script
from shortsfactory.scripts import word_budget
from shortsfactory.subtitles import (
    SUBTITLE_MAX_LINES,
    SUBTITLE_MAX_WIDTH,
    _chunk_sentence,
    build_subtitles,
    wrap_lines,
)
from shortsfactory.timeline import SceneTiming, Timeline
from shortsfactory.util import extract_numbers, roman_numbers, sentence_split
from shortsfactory.visuals import VisualService


def test_roman_numbers_support_centuries():
    assert "5" in roman_numbers("Хлеб появился во V веке до н. э.")
    assert "2" in roman_numbers("Public bakeries appeared in the II век.")
    assert "1" in roman_numbers("Начало относят к I веку")
    # a lone capital letter without a century marker is not a number
    assert roman_numbers("Rome and I said") == set()


def test_extract_numbers_normalizes_thousands_separators():
    numbers = extract_numbers("2.540 км, 1,086 бар, 10.600 м, 11.034 м, 1,071.8 кг")
    assert numbers == {"2540", "1086", "10600", "11034", "1071.8"}


def test_extract_numbers_keeps_decimal_points_and_percentages():
    numbers = extract_numbers("0.0037%, 4.96%, 10.6 км и 3.3 градуса")
    assert numbers == {"0.0037%", "4.96%", "10.6", "3.3"}


def test_media_query_adds_topic_to_short_generic_query():
    assert MediaService._query_for_scene("Religion", "Ancient Rome") == "Religion Ancient Rome"
    assert MediaService._query_for_scene("Roman Ideology", "Ancient Rome") == "Roman Ideology Ancient Rome"


def test_media_query_keeps_specific_topic_query():
    assert MediaService._query_for_scene("Rome map", "Ancient Rome") == "Rome map"
    assert MediaService._query_for_scene("", "Ancient Rome") == "Ancient Rome"


def test_sentence_split_keeps_abbreviations_inside_sentence():
    text = "Пик пришёлся на II веке н. э. Затем наступил кризис. Это важно."
    assert sentence_split(text) == [
        "Пик пришёлся на II веке н. э.",
        "Затем наступил кризис.",
        "Это важно.",
    ]


def test_motion_rate_depends_on_pace(config):
    service = RenderService(config)
    scene = Scene(id="scene_01", text="Тест")
    slow, _ = service._motion(scene, 8.0, 0, "historical", "slow")
    medium, _ = service._motion(scene, 8.0, 0, "historical", "medium")
    fast, _ = service._motion(scene, 8.0, 0, "historical", "fast")
    assert slow != medium != fast
    assert slow != fast


def test_license_filter_accepts_reusable_material():
    assert license_allowed("Public domain")
    assert license_allowed("CC0")
    assert license_allowed("CC BY 4.0")


def test_license_filter_rejects_restricted_material():
    assert not license_allowed("CC BY-SA 4.0")
    assert not license_allowed("CC BY-NC 4.0")
    assert not license_allowed("CC BY-ND 4.0")
    assert not license_allowed("")


def test_word_budget_english_is_larger_than_russian(config):
    ru_low, ru_high = word_budget(config, "ru", 40)
    en_low, en_high = word_budget(config, "en", 40)
    assert 0 < ru_low < ru_high
    assert en_low > ru_low
    assert en_high > ru_high


def test_word_budget_keeps_duration_margin(config):
    ru_low, ru_high = word_budget(config, "ru", 40)
    # 72 words rendered 35.14 s, just below the 35.2 s acceptance floor.
    assert ru_low == 75
    assert ru_low < ru_high


def test_fit_lines_marks_truncated_long_caption(config):
    visuals = VisualService(config)
    text = "Очень длинная подпись сцены " * 30
    lines, font, size = visuals._fit_lines(text, 500, 2, 80, 40)
    assert lines[-1].endswith("…")
    assert all(font.getlength(line) <= 500 for line in lines)


def test_wrap_lines_keeps_two_line_limit(config):
    font = ImageFont.truetype(str(config.fonts_dir / "Montserrat-ExtraBold.ttf"), 64)
    text = "Кольца Сатурна состоят изо льда и пыли"
    lines = wrap_lines(text, font, SUBTITLE_MAX_WIDTH, SUBTITLE_MAX_LINES)
    assert lines is not None
    assert 1 <= len(lines) <= SUBTITLE_MAX_LINES
    assert all(font.getlength(line) <= SUBTITLE_MAX_WIDTH for line in lines)


def test_chunk_sentence_does_not_break_abbreviation(config):
    font = ImageFont.truetype(str(config.fonts_dir / "Montserrat-ExtraBold.ttf"), 64)
    text = (
        "Древний Рим — цивилизация, достигшая пика могущества во II веке н. э., "
        "контролировавшая огромные территории от Англии до Судана."
    )
    chunks = _chunk_sentence(text, 8.5, font, SUBTITLE_MAX_WIDTH)
    assert not any(chunk.endswith("н.") for chunk, _ in chunks)
    assert not any(chunk.lstrip().startswith("э.") for chunk, _ in chunks)
    assert any("н.\u00a0э." in chunk for chunk, _ in chunks)


def test_ass_number_highlight_uses_real_override_tags(tmp_path, config):
    text = "Плотное кольцо находится в 50000 километрах."
    script = Script(lang="ru", title="Тест", hook=text, scenes=[Scene(id="scene_01", text=text)])
    manifest = {
        "scenes": [
            {
                "scene_id": "scene_01",
                "duration": 4.0,
                "sentences": [{"text": text, "start": 0.0, "duration": 3.2}],
            }
        ]
    }
    timeline = Timeline(
        lang="ru",
        scenes=[SceneTiming("scene_01", 3.2, 4.0, 0.0, 0.15, 0.6)],
        total_video=4.0,
        total_audio=4.0,
    )
    result = build_subtitles(script, manifest, timeline, tmp_path, config)
    ass = (tmp_path / "subtitles" / "ru.ass").read_text(encoding="utf-8")
    assert "{\\c&H" in ass
    assert "({\\c" not in ass
    assert len(result["cues"]) >= 1
    assert (tmp_path / "subtitles" / "ru.srt").exists()
