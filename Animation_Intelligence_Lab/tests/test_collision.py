"""test_collision.py — colliders, bounds, proximity, reachability."""

from __future__ import annotations

import pytest

from animation_lab.collision import (
    AABB,
    Circle,
    aabb_aabb,
    character_overlap,
    circle_aabb,
    circle_circle,
    default_character_radius,
    min_character_distance,
    out_of_bounds,
    prop_overlap_character,
    reachable_target,
    within_arm_reach,
)
from animation_lab.models import CharacterState, PropState, Vec2Field


def _char(cid: str, x: float, y: float) -> CharacterState:
    return CharacterState(character_id=cid, position=Vec2Field(x=x, y=y))


def test_circle_circle():
    assert circle_circle(Circle((0, 0), 0.1), Circle((0.15, 0), 0.1))
    assert not circle_circle(Circle((0, 0), 0.1), Circle((0.3, 0), 0.1))


def test_aabb_aabb():
    a = AABB((0.5, 0.5), (0.1, 0.1))
    b = AABB((0.55, 0.5), (0.1, 0.1))
    c = AABB((0.9, 0.9), (0.05, 0.05))
    assert aabb_aabb(a, b)
    assert not aabb_aabb(a, c)


def test_circle_aabb():
    box = AABB((0.5, 0.5), (0.1, 0.1))
    assert circle_aabb(Circle((0.55, 0.5), 0.05), box)
    assert not circle_aabb(Circle((0.9, 0.9), 0.05), box)


def test_character_overlap_far_apart_is_none():
    assert character_overlap(_char("a", 0.1, 0.1), _char("b", 0.9, 0.9)) is None


def test_character_overlap_close_is_warning():
    # 0.10 apart: outside the body colliders (2*0.045=0.09) but inside the
    # proximity threshold (0.09 + min_gap 0.05 = 0.14).
    a, b = _char("a", 0.5, 0.5), _char("b", 0.6, 0.5)
    rep = character_overlap(a, b, min_gap=0.05)
    assert rep is not None and rep.kind == "proximity"
    assert rep.severity == "warning"


def test_character_overlap_intersects_is_error():
    a, b = _char("a", 0.5, 0.5), _char("b", 0.505, 0.5)
    rep = character_overlap(a, b)
    assert rep is not None
    assert rep.severity == "error"
    assert rep.kind in ("collision", "heuristic")


def test_min_character_distance():
    d = min_character_distance([_char("a", 0.0, 0.0), _char("b", 0.3, 0.4),
                                _char("c", 1.0, 1.0)])
    assert d == pytest.approx(0.5)
    assert min_character_distance([_char("a", 0, 0)]) is None


def test_out_of_bounds():
    assert out_of_bounds((-0.1, 0.5))
    assert out_of_bounds((0.5, 1.2))
    assert not out_of_bounds((0.5, 0.5))
    # with margin 0.1: positions up to 1.1 are tolerated
    assert not out_of_bounds((1.05, 0.5), margin=0.1)
    assert out_of_bounds((1.2, 0.5), margin=0.1)
    assert out_of_bounds((0.5, -0.05), margin=0.1) is False


def test_within_arm_reach():
    a = _char("a", 0.5, 0.5)
    assert within_arm_reach(a, (0.6, 0.5), reach=0.15)
    assert not within_arm_reach(a, (0.9, 0.5), reach=0.15)


def test_reachable_target():
    assert reachable_target((0, 0), (0.3, 0), max_speed=0.5, duration=1.0)
    assert not reachable_target((0, 0), (0.6, 0), max_speed=0.5, duration=1.0)
    with pytest.raises(ValueError):
        reachable_target((0, 0), (1, 0), max_speed=0.0, duration=1.0)


def test_free_prop_inside_body_flagged():
    ch = _char("a", 0.5, 0.5)
    prop = PropState(prop_id="p", position=Vec2Field(x=0.51, y=0.51))
    rep = prop_overlap_character(prop, ch)
    assert rep is not None and rep.kind == "heuristic"
    owned = prop.model_copy(update={"owner_id": "a", "state": "attached"})
    assert prop_overlap_character(owned, ch) is None


def test_default_radius_scales():
    a = _char("a", 0.5, 0.5)
    big = a.model_copy(update={"scale": 2.0})
    assert default_character_radius(big) > default_character_radius(a)
