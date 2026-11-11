"""Тесты чистых хелперов desktop_app (живут в gui_worker.py).

Без Tk и тяжёлых импортов: сканирование библиотеки фильмов, точечное
обновление флага ``[audio] music`` в config.toml, список моделей Ollama.
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gui_worker  # noqa: E402


# ------------------------------------------------------- film library scan
def _make_film(root: Path, name: str, *, report: dict | None = None,
               video: str | None = None, contact_sheet: bool = False) -> Path:
    d = root / name
    d.mkdir(parents=True)
    if report is not None:
        (d / "film_report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8")
    if video is not None:
        (d / video).write_bytes(b"\x00\x00\x00\x18ftypmp42")
    if contact_sheet:
        (d / "contact_sheet.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")
    return d


def test_scan_empty_or_missing_dir(tmp_path: Path):
    assert gui_worker.scan_film_library(tmp_path / "output") == []
    out = tmp_path / "output"
    out.mkdir()
    assert gui_worker.scan_film_library(out) == []


def test_scan_finds_report_and_video(tmp_path: Path):
    out = tmp_path / "output"
    out.mkdir()
    film_dir = _make_film(out, "blocky_first_film", report={
        "title": "Первый фильм", "status": "OK", "duration": 37.2,
        "video": "film.mp4",
    }, video="film.mp4", contact_sheet=True)
    films = gui_worker.scan_film_library(out)
    assert len(films) == 1
    entry = films[0]
    assert entry["title"] == "Первый фильм"
    assert entry["status"] == "OK"
    assert entry["duration"] == 37.2
    assert entry["video"] == film_dir / "film.mp4"
    assert entry["contact_sheet"] == film_dir / "contact_sheet.jpg"
    assert entry["report"] == film_dir / "film_report.json"


def test_scan_finds_dir_without_report(tmp_path: Path):
    out = tmp_path / "output"
    out.mkdir()
    _make_film(out, "legacy_film", video="final_audio.mp4")
    films = gui_worker.scan_film_library(out)
    assert len(films) == 1
    assert films[0]["status"] == "OK"
    assert films[0]["report"] is None
    assert films[0]["title"] == "legacy_film"


def test_scan_skips_dirs_without_markers_and_underscore(tmp_path: Path):
    out = tmp_path / "output"
    out.mkdir()
    _make_film(out, "previews")  # ни отчёта, ни видео
    _make_film(out, "_audit", video="final_audio.mp4")  # служебная папка
    films = gui_worker.scan_film_library(out)
    assert films == []


def test_scan_covers_nested_level(tmp_path: Path):
    out = tmp_path / "output"
    out.mkdir()
    _make_film(out / "blocky_films", "nested_film", report={
        "title": "Вложенный", "status": "PARTIAL", "duration": 12.0},
        video="film.mp4")
    films = gui_worker.scan_film_library(out)
    assert [f["title"] for f in films] == ["Вложенный"]
    assert films[0]["status"] == "PARTIAL"
    # третий уровень не сканируется
    deep = out / "a" / "b"
    deep.mkdir(parents=True)
    _make_film(deep, "too_deep", video="final_audio.mp4")
    assert [f["title"] for f in gui_worker.scan_film_library(out)] == \
        ["Вложенный"]


def test_scan_sorted_newest_first(tmp_path: Path):
    import os
    out = tmp_path / "output"
    out.mkdir()
    old = _make_film(out, "old", video="final_audio.mp4")
    new = _make_film(out, "new", video="final_audio.mp4")
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))
    films = gui_worker.scan_film_library(out)
    assert [f["path"].name for f in films] == ["new", "old"]


# -------------------------------------------------------- config music flag
CONFIG_SAMPLE = """\
[app]
name = "ShortsFactory Pro"
version = "1.0.0"

[video]
fps = 30

[audio]
music_enabled = true
# Ч2.4: процедурная музыкальная подложка в blocky-сценах.
music = false
target_lufs = -14.0

[quality]
manual_visual_review = true
"""


@pytest.fixture()
def config_copy(tmp_path: Path) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(CONFIG_SAMPLE, encoding="utf-8")
    return p


def test_read_music_flag(config_copy: Path):
    assert gui_worker.read_config_music_flag(config_copy) is False
    assert gui_worker.read_config_music_flag(
        config_copy.with_name("nope.toml")) is False


def test_set_music_flag_round_trip(config_copy: Path):
    assert gui_worker.set_config_music_flag(config_copy, True) is True
    assert gui_worker.read_config_music_flag(config_copy) is True
    assert gui_worker.set_config_music_flag(config_copy, False) is True
    assert gui_worker.read_config_music_flag(config_copy) is False


def test_set_music_flag_preserves_rest_of_file(config_copy: Path):
    original_lines = CONFIG_SAMPLE.splitlines()
    music_idx = original_lines.index("music = false")
    gui_worker.set_config_music_flag(config_copy, True)
    after = config_copy.read_text(encoding="utf-8").splitlines()
    assert len(after) == len(original_lines)  # только замена значения
    assert after[music_idx] == "music = true"
    # комментарий и соседние ключи не тронуты
    assert after[music_idx - 1] == "# Ч2.4: процедурная музыкальная подложка в blocky-сценах."
    assert "music_enabled = true" in "\n".join(after)
    # файл остаётся валидным TOML
    data = tomllib.loads(config_copy.read_text(encoding="utf-8"))
    assert data["audio"]["music"] is True
    assert data["audio"]["target_lufs"] == -14.0


def test_set_music_flag_inserts_missing_key(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text("[app]\nname = \"x\"\n\n[audio]\nmusic_enabled = true\n",
                 encoding="utf-8")
    assert gui_worker.set_config_music_flag(p, True) is True
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["audio"]["music"] is True
    assert data["audio"]["music_enabled"] is True  # соседний ключ на месте


def test_set_music_flag_creates_section(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text("[app]\nname = \"x\"\n", encoding="utf-8")
    assert gui_worker.set_config_music_flag(p, False) is True
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["audio"]["music"] is False


def test_set_music_flag_unwritable_returns_false(tmp_path: Path):
    missing = tmp_path / "missing_dir" / "config.toml"
    assert gui_worker.set_config_music_flag(missing, True) is False


# ---------------------------------------------------------------- models
def test_model_choices_dedup_and_default(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[llm]\nmodel = "qwen2.5:7b"\nfallback_models = ["gemma3:4b", '
        '"gemma3:4b"]\n', encoding="utf-8")
    assert gui_worker.ollama_model_choices(p) == ["qwen2.5:7b", "gemma3:4b"]


def test_model_choices_adds_default_when_absent(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text('[llm]\nmodel = "gemma3:4b"\n', encoding="utf-8")
    choices = gui_worker.ollama_model_choices(p)
    assert choices == ["gemma3:4b", gui_worker.DEFAULT_MODEL]


def test_model_choices_missing_config(tmp_path: Path):
    assert gui_worker.ollama_model_choices(tmp_path / "nope.toml") == \
        [gui_worker.DEFAULT_MODEL]
