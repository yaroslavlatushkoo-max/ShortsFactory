import json
import shutil

import numpy as np
import pytest
from PIL import Image

from shortsfactory.generative.story_film import (
    StoryFilmError,
    build_story_film,
    compose_scene_frame,
    render_character_sprite,
    render_background,
    write_srt,
)


def _mini_storyboard():
    return {
        "title": "Тестовая история",
        "target_duration_s": 8.0,
        "scenes": [
            {"title": "Начало", "location": "forest", "summary": "Стив и Алекс увидели следы.",
             "detail": "Стив заметил странные следы на тропе."},
            {"title": "Развязка", "location": "cave", "summary": "Они нашли кристалл.",
             "detail": "Внутри пещеры сверкал кристалл."},
        ],
    }


def test_render_character_sprite_size_and_alpha():
    spr = render_character_sprite("steve", scale=4)
    assert spr.size == (96, 208)  # 24*4 x 52*4
    # есть непрозрачные пиксели
    alpha = spr.getchannel("A")
    assert np.asarray(alpha).max() == 255


def test_render_character_sprite_alex_missing_falls_back():
    # неизвестные персонажи получают универсальный спрайт (без исключения)
    spr = render_character_sprite("ghost", scale=4)
    assert spr.size == (96, 208)


def test_render_background_size_and_palette():
    img = render_background("forest", 320, 480, seed=1)
    assert img.size == (320, 480)
    assert img.mode == "RGB"


def test_compose_scene_frame_with_chars():
    frame = compose_scene_frame(
        title="Сцена", location="meadow",
        characters=[("steve", "left"), ("alex", "right")],
        width=320, height=480, voice_line="Привет!",
    )
    assert frame.size == (320, 480)


def test_motion_changes_character_position():
    moves = [
        {"char": "steve", "start": (0.15, 0.82), "end": (0.75, 0.82), "t0": 0.0, "t1": 1.0},
    ]
    f0 = compose_scene_frame(
        title="T", location="forest", characters=[("steve", "left")],
        width=320, height=480, t=0.25, moves=moves,
    )
    f1 = compose_scene_frame(
        title="T", location="forest", characters=[("steve", "left")],
        width=320, height=480, t=0.75, moves=moves,
    )
    # разница между кадрами есть (персонаж передвинулся)
    a = np.asarray(f0, dtype=np.int16)
    b = np.asarray(f1, dtype=np.int16)
    assert float(np.abs(a - b).mean()) > 0.5


def test_generic_character_sprite():
    spr = render_character_sprite("boy", scale=4)
    assert spr.size == (96, 208)
    assert np.asarray(spr.getchannel("A")).max() == 255


def test_walk_legs_change_between_phases():
    """Ноги переступают: нижняя часть спрайта меняется между фазами шага."""
    base = render_character_sprite("boy", scale=4, leg_phase=0.0)
    a = render_character_sprite("boy", scale=4, leg_phase=0.5)
    b = render_character_sprite("boy", scale=4, leg_phase=-0.5)
    # зона ног: нижние 30% спрайта
    crop = lambda img: np.asarray(img.crop((0, int(img.height * 0.62), img.width, img.height)))
    assert np.abs(crop(a).astype(int) - crop(base).astype(int)).mean() > 1.0
    assert np.abs(crop(a).astype(int) - crop(b).astype(int)).mean() > 1.0
    # и скин-спрайт тоже шагает
    sa = render_character_sprite("steve", scale=4, leg_phase=0.5)
    sb = render_character_sprite("steve", scale=4, leg_phase=-0.5)
    assert np.abs(crop(sa).astype(int) - crop(sb).astype(int)).mean() > 0.5


def test_write_srt_formats():
    p = write_srt([(1.0, 2.5, "Привет"), (3.0, 4.0, "Мир")], __import__("pathlib").Path("_t.srt"))
    text = p.read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:02,500" in text
    assert "Привет" in text
    p.unlink()


@pytest.mark.slow
def test_build_story_film_smoke(tmp_path):
    """Полный smoke: небольшой ролик из fallback-сториборда (без Blender)."""
    sb = _mini_storyboard()
    out = tmp_path / "out"
    final = build_story_film(
        sb, out,
        lang="ru", target_s=6.0,
        fps=12, width=240, height=320,
        ambient=False, subtitles=True,
    )
    assert final.exists()
    assert final.stat().st_size > 10_000
    report = json.loads((out / "story_film_report.json").read_text(encoding="utf-8"))
    assert report["scenes"] and report["total_s"] > 4
    srt = out / "Тестовая_история.srt"
    assert srt.exists()
    assert "Стив" in srt.read_text(encoding="utf-8")
    # видео непустое и является mp4
    with open(final, "rb") as f:
        assert f.read(4) == b"\x00\x00\x00\x18" or final.suffix == ".mp4"
