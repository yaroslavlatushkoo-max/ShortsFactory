"""Тесты межсценовой непрерывности (blocky_continuity).

Проверяют, что финальное положение персонажа (final_root из отчёта рендера)
переносится в следующую сцену той же локации, а не стартовый origin
(запрет «телепортации обратно»).
"""

import pytest

from shortsfactory.generative.blocky_continuity import (
    WorldState,
    prepare_scene_continuity,
    snapshot_final_state,
)


def _scene(origin, env=""):
    return {
        "duration": 4.0,
        "environment": env,
        "actors": [
            {"character": "steve", "origin": list(origin), "actions": []}
        ],
    }


def test_snapshot_with_actor_roots_stores_final_root():
    world = WorldState()
    scene = _scene([0.0, 0.1, 0.0])
    snapshot_final_state(
        scene, world, scene_index=1,
        actor_roots={"steve": [2.35, 0.1, 1.4]},
    )
    # высота корня (z) обнуляется — следующая сцена стартует на полу
    assert world.actors["steve"] == [2.35, 0.1, 0.0]
    assert world.actors["steve:env"] == ""


def test_snapshot_without_roots_falls_back_to_origin():
    world = WorldState()
    scene = _scene([0.0, 0.1, 0.0])
    snapshot_final_state(scene, world, scene_index=1)
    assert world.actors["steve"] == [0.0, 0.1, 0.0]


def test_same_env_scene_inherits_previous_final_position():
    world = WorldState()
    snapshot_final_state(
        _scene([0.0, 0.1, 0.0]), world, scene_index=1,
        actor_roots={"steve": [2.35, 0.1, 1.4]},
    )
    # авторский origin снова дефолтный — но подготовленная сцена должна
    # унаследовать реальное место остановки
    prepared, issues = prepare_scene_continuity(
        _scene([0.0, 0.1, 0.0]), world, scene_index=2
    )
    assert issues == []
    assert prepared["actors"][0]["origin"] == [2.35, 0.1, 0.0]


def test_different_env_keeps_authored_origin():
    world = WorldState()
    snapshot_final_state(
        _scene([0.0, 0.1, 0.0], env="forest"), world, scene_index=1,
        actor_roots={"steve": [2.35, 0.1, 1.4]},
    )
    prepared, _ = prepare_scene_continuity(
        _scene([0.0, 0.1, 0.0], env="cave"), world, scene_index=2
    )
    # смена локации — разрешённый переход, авторский origin остаётся
    assert prepared["actors"][0]["origin"] == [0.0, 0.1, 0.0]
