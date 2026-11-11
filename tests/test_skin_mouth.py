"""Tests for skin_mouth: three mouth texture variants per character.

Pure Python, no Blender.  Synthetic 64x64 PNGs are used so the tests do not
depend on the user's uploaded skins.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from shortsfactory.generative.animation_pipeline.skin_mouth import (
    MouthSkinBank,
    classify_mouth,
    plan_mouth_texture,
)


def _solid(rgb, size=64):
    img = Image.new("RGBA", (size, size), rgb)
    return img


def _png_bytes(rgb):
    buf = io.BytesIO()
    _solid(rgb).save(buf, format="PNG")
    return buf.getvalue()


def _skin_dir(tmp_path: Path) -> Path:
    d = tmp_path / "skins"
    d.mkdir(parents=True, exist_ok=True)
    # closed=dark, round=mid, open=bright; base == closed per spec
    for name, rgb in (
        ("steve_mouth_closed.png", (10, 10, 10, 255)),
        ("steve_mouth_round.png", (120, 60, 60, 255)),
        ("steve_mouth_open.png", (230, 230, 230, 255)),
        ("steve_64.png", (10, 10, 10, 255)),
        ("alex_mouth_closed.png", (20, 20, 20, 255)),
        ("alex_mouth_round.png", (140, 70, 70, 255)),
        ("alex_mouth_open.png", (240, 240, 240, 255)),
        ("alex_64.png", (20, 20, 20, 255)),
    ):
        (d / name).write_bytes(_png_bytes(rgb))
    return d


def test_bank_finds_all_five_variants(tmp_path):
    bank = MouthSkinBank("steve", skins_dir=_skin_dir(tmp_path))
    assert bank.closed_name == "steve_mouth_closed.png"
    assert bank.base_name == "steve_mouth_closed.png"
    for key, fname in (
        ("closed", "steve_mouth_closed.png"),
        ("round", "steve_mouth_round.png"),
        ("open", "steve_mouth_open.png"),
    ):
        img = bank.variant(key)
        assert img is not None and img.shape[:2] == (64, 64)


def test_bank_composite_dimensions(tmp_path):
    bank = MouthSkinBank("alex", skins_dir=_skin_dir(tmp_path))
    png = bank.composite_png("open")
    img = Image.open(io.BytesIO(png))
    assert img.size == (64, 64)


def test_classify_ru_closed_round_open():
    # closed
    assert classify_mouth("ru", "м", "", "а") == "closed"
    assert classify_mouth("ru", "б", "а", "о") == "closed"
    assert classify_mouth("ru", "п", "", "") == "closed"
    # round
    assert classify_mouth("ru", "о", "", "") == "round"
    assert classify_mouth("ru", "ё", "", "") == "round"
    assert classify_mouth("ru", "у", "", "") == "round"
    assert classify_mouth("ru", "ю", "", "") == "round"
    # open
    assert classify_mouth("ru", "а", "", "") == "open"
    assert classify_mouth("ru", "э", "", "") == "open"
    assert classify_mouth("ru", "е", "", "") == "open"
    assert classify_mouth("ru", "и", "", "") == "open"
    assert classify_mouth("ru", "ы", "", "") == "open"
    assert classify_mouth("ru", "я", "", "") == "open"
    # consonant between vowels follows the neighbour
    assert classify_mouth("ru", "т", "а", "о") == "open"


def test_classify_en_closed_round_open():
    assert classify_mouth("en", "m", "", "") == "closed"
    assert classify_mouth("en", "b", "", "") == "closed"
    assert classify_mouth("en", "p", "", "") == "closed"
    assert classify_mouth("en", "o", "", "") == "round"
    assert classify_mouth("en", "u", "", "") == "round"
    assert classify_mouth("en", "w", "", "") == "round"
    assert classify_mouth("en", "a", "", "") == "open"
    assert classify_mouth("en", "e", "", "") == "open"
    assert classify_mouth("en", "i", "", "") == "open"
    assert classify_mouth("en", "y", "", "") == "open"


def test_plan_min_hold_prolongs_short_runs():
    states = plan_mouth_texture(
        "м о а", lang="ru", duration=3.0, fps=30, min_hold_frames=8
    )
    runs: list[tuple[str, int]] = []
    prev = None
    cnt = 0
    for s in states:
        if s == prev:
            cnt += 1
        else:
            if prev is not None:
                runs.append((prev, cnt))
            prev = s
            cnt = 1
    runs.append((prev, cnt))
    assert any(shape == "round" and length >= 8 for shape, length in runs)
    assert any(shape == "open" and length >= 8 for shape, length in runs)


def test_plan_uses_closed_at_boundaries_and_in_pauses():
    track = plan_mouth_texture("мама", lang="ru", duration=2.0, fps=30)
    assert len(track) == 60
    # starts and ends closed
    assert track[0] == "closed"
    assert track[-1] == "closed"
    # open vowels appear somewhere in the middle
    assert "open" in track


def test_bank_caching_loads_png_once(tmp_path):
    d = _skin_dir(tmp_path)
    bank = MouthSkinBank("steve", skins_dir=d)
    original = bank._load
    counter = {"n": 0}

    def counting(path):
        counter["n"] += 1
        return original(path)

    bank._load = counting  # type: ignore[method-assign]
    bank.variant("round")
    n_warm = counter["n"]
    bank.variant("round")
    bank.variant("open")
    assert counter["n"] == n_warm
