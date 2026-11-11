"""Авто-кадрирование (plan_framing) и разграничение fit/lens — без Blender."""

import pytest

from shortsfactory.generative.animation_pipeline import blocky_hero as bh
from shortsfactory.generative.animation_pipeline import skinned_hero as sh


def _two_actors() -> list[dict]:
    return [
        {
            "character": "steve", "origin": (0.0, 0.90, 0.0),
            "actions": [{"action": "idle", "start": 0.0, "duration": 2.0}],
        },
        {
            "character": "alex", "origin": (0.35, 0.90, 0.0),
            "actions": [{"action": "idle", "start": 0.0, "duration": 2.0}],
        },
    ]


def _chest_and_light() -> list[dict]:
    # сундук уходит левым краем на x = -1.16 (как в «замёрзшей деревне»,
    # где он обрезался в 100% кадров), светильник — дальше всех
    return [
        {
            "name": "Chest", "center": (-0.85, 0.72, 0.30),
            "size": (0.62, 0.50, 0.48), "rgb": (0.45, 0.30, 0.16),
        },
        {
            "name": "CubeLight", "type": "light",
            "center": (-3.00, 0.70, 0.95), "rgb": (0.6, 0.95, 1.0),
            "energy_track": [[1, 80.0]],
        },
    ]


def _visible_box(cut: dict) -> tuple[float, float, float, float]:
    """Видимая область кадра (x0, x1, z0, z1) по геометрии шаблона."""
    fit = float(cut["fit"])
    cx, _, cz = (float(v) for v in cut["center"])
    height = float(cut["height"])
    half_w = height * (1080.0 / 1920.0) / fit / 2.0
    half_h = height / fit / 2.0
    return cx - half_w, cx + half_w, cz - half_h, cz + half_h


def test_plan_framing_wide_keeps_chest_in_frame():
    """Широкий план обязан вместить сундук целиком (левый край x = -1.16)."""
    cuts = bh.plan_framing(
        _two_actors(), _chest_and_light(), fps=30, duration=2.0, mode="wide"
    )
    assert len(cuts) == 1
    cut = cuts[0]
    assert cut["frame"] == 1
    assert set(cut) >= {"frame", "center", "height", "lens", "fit"}
    x0, x1, z0, z1 = _visible_box(cut)
    assert x0 <= -1.16, f"левый край сундука обрезан: {cut}"
    assert x1 >= 0.35, f"правый актёр обрезан: {cut}"
    assert z0 <= 0.0 and z1 >= 1.8, f"полный рост не влез: {cut}"


def test_plan_framing_wide_ignores_light_props():
    """Пропсы-светильники (type=light) не участвуют в bbox — они невидимы."""
    cuts = bh.plan_framing(
        _two_actors(), _chest_and_light(), fps=30, duration=2.0, mode="wide"
    )
    x0, x1, _, _ = _visible_box(cuts[0])
    assert x0 > -3.0, "кадр раздался под невидимый источник света"


def test_plan_framing_close_covers_both_heads():
    """Парный кадр: обе головы (z ≈ 1.8) и оба актёра внутри с запасом."""
    cuts = bh.plan_framing(
        _two_actors(), [], fps=30, duration=2.0, mode="close"
    )
    cut = cuts[0]
    x0, x1, z0, z1 = _visible_box(cut)
    for x in (0.0, 0.35):
        assert x0 <= x <= x1, f"актёр на x={x} выпал из парного кадра: {cut}"
    assert z1 >= 1.8 + bh.FRAMING_MARGIN - 1e-6, f"голова обрезана: {cut}"


def test_plan_framing_includes_walk_targets():
    """Ходка с params.target расширяет bbox до точки назначения."""
    actors = [{
        "character": "steve", "origin": (0.0, 0.0, 0.0),
        "actions": [
            {"action": "walk", "start": 0.0, "duration": 3.0,
             "params": {"target": [2.5, 0.0]}},
        ],
    }]
    (cut,) = bh.plan_framing(actors, [], fps=30, duration=3.0)
    x0, x1, _, _ = _visible_box(cut)
    assert x1 >= 2.5, "точка назначения ходьбы обрезана"
    # ходьба, начинающаяся за пределами окна длительности, не влияет на кадр
    late = dict(actors[0], actions=[
        {"action": "walk", "start": 5.0, "duration": 3.0,
         "params": {"target": [2.5, 0.0]}},
    ])
    (cut_short,) = bh.plan_framing([late], [], fps=30, duration=1.0)
    assert x1 > 1.0 and abs(float(cut_short["center"][0])) < 1.0


def test_plan_framing_is_deterministic():
    a1 = bh.plan_framing(_two_actors(), _chest_and_light(), fps=30, duration=2.0)
    a2 = bh.plan_framing(_two_actors(), _chest_and_light(), fps=30, duration=2.0)
    assert a1 == a2


def test_plan_framing_rejects_unknown_mode():
    with pytest.raises(ValueError, match="режим"):
        bh.plan_framing(_two_actors(), [], fps=30, duration=2.0, mode="dutch")


def test_scene_script_writes_fit_and_lens(tmp_path):
    """fit/lens из аргументов попадают в сгенерированный скрипт Blender."""
    scene = bh.first_film_scene()
    script = bh.build_blocky_scene_script(
        tmp_path, scene["actors"], scenario="fit_lens", fps=30,
        fit=0.9, lens=50.0,
    )
    text = script.read_text(encoding="utf-8")
    assert f"FIT = {0.9!r}" in text
    assert f"LENS = {50.0!r}" in text
    # дефолты не изменились: старые вызовы дают прежний кадр
    script_default = bh.build_blocky_scene_script(
        tmp_path, scene["actors"], scenario="fit_default", fps=30,
    )
    text_default = script_default.read_text(encoding="utf-8")
    assert f"FIT = {bh.FRAMING_FIT!r}" in text_default
    assert f"LENS = {bh.FRAMING_LENS!r}" in text_default


def test_template_has_safety_clamps():
    """Шаблон Blender ограничивает POINT-свет и эмиссию на своей стороне."""
    assert "MAX_POINT_ENERGY = 15.0" in sh._BLENDER_TEMPLATE
    assert "MAX_EMISSION = 2.5" in sh._BLENDER_TEMPLATE
    assert "ldata.energy = min(float(val), MAX_POINT_ENERGY)" in sh._BLENDER_TEMPLATE
    assert "estr.default_value = min(float(val), MAX_EMISSION)" in sh._BLENDER_TEMPLATE
