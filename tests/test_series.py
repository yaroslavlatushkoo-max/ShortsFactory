"""Ч2.5: серии blocky-фильмов — деление сториборда и CLI-агрегация.

Рендер не запускается: ``produce_film`` подменяется, проверяется только
склейка серий и сводный отчёт.
"""

import copy
import json
from pathlib import Path

import shortsfactory.generative.blocky_production as blocky_production
from shortsfactory.cli import _produce_series, build_parser


def _storyboard(n: int = 5) -> dict:
    return {
        "title": "Потерянный алмаз",
        "lang": "ru",
        "source": "fallback",
        "idea": "квест за алмазом",
        "scenes": [
            {
                "title": f"Сцена {i}",
                "duration": 6.0,
                "actors": [{"character": "steve"}],
            }
            for i in range(1, n + 1)
        ],
    }


def test_split_five_scenes_into_two():
    storyboard = _storyboard(5)
    parts = blocky_production.split_storyboard_episodes(storyboard, 2)
    assert [len(part["scenes"]) for part in parts] == [3, 2]
    assert parts[0]["title"] == "Потерянный алмаз — серия 1"
    assert parts[1]["title"] == "Потерянный алмаз — серия 2"
    for part in parts:
        assert part["lang"] == "ru"
        assert part["source"] == "fallback"
        assert part["idea"] == "квест за алмазом"
    assert parts[0]["scenes"] == storyboard["scenes"][:3]
    assert parts[1]["scenes"] == storyboard["scenes"][3:]


def test_split_keeps_scenes_untouched():
    storyboard = _storyboard(4)
    before = copy.deepcopy(storyboard["scenes"])
    parts = blocky_production.split_storyboard_episodes(storyboard, 2)
    # исходный сториборд не изменён, сцены разъехались по сериям как есть
    assert storyboard["scenes"] == before
    flat = [scene for part in parts for scene in part["scenes"]]
    assert flat == before


def test_split_single_episode_and_empty():
    parts = blocky_production.split_storyboard_episodes(_storyboard(3), 1)
    assert len(parts) == 1
    assert len(parts[0]["scenes"]) == 3
    assert blocky_production.split_storyboard_episodes({"title": "x"}, 2) == []


def _fake_produce_film(statuses):
    """Заглушка produce_film: статус по номеру серии, видео — фейковый путь."""

    def fake(storyboard, out_dir, **kwargs):
        index = int(str(Path(out_dir).name).split("_")[1])
        return {
            "status": statuses[index - 1],
            "video": str(Path(out_dir) / "film.mp4"),
        }

    return fake


def test_produce_series_aggregates_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(
        blocky_production, "produce_film", _fake_produce_film(["OK", "OK"])
    )
    report = _produce_series(
        _storyboard(5), tmp_path, episodes=2, voice=False, resume=False
    )
    assert report["status"] == "OK"
    assert report["title"] == "Потерянный алмаз"
    assert [episode["status"] for episode in report["episodes"]] == ["OK", "OK"]
    saved = json.loads(
        (tmp_path / "series_report.json").read_text(encoding="utf-8")
    )
    assert saved["status"] == "OK"
    assert len(saved["episodes"]) == 2


def test_produce_series_partial_when_one_episode_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        blocky_production, "produce_film", _fake_produce_film(["OK", "FAILED"])
    )
    report = _produce_series(_storyboard(4), tmp_path, episodes=2)
    assert report["status"] == "PARTIAL"
    saved = json.loads(
        (tmp_path / "series_report.json").read_text(encoding="utf-8")
    )
    assert saved["status"] == "PARTIAL"


def test_blocky_create_parser_accepts_episodes():
    args = build_parser().parse_args(
        ["blocky-create", "--idea", "квест за алмазом", "--episodes", "2"]
    )
    assert args.command == "blocky-create"
    assert args.episodes == 2


def test_blocky_create_parser_episodes_default_one():
    args = build_parser().parse_args(["blocky-create", "--idea", "квест"])
    assert args.episodes == 1
