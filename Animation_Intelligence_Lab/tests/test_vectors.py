"""test_vectors.py — vectors, coordinate conversion, easing."""

from __future__ import annotations

import math

import pytest

from animation_lab.vectors import (
    angle_degrees,
    angle_lerp,
    apply_easing,
    distance,
    in_unit_square,
    normalized_to_pixels,
    pixels_to_normalized,
    polyline_samples,
    rotate_deg,
    scale,
    sub,
    vec2,
    wrap_angle_deg,
)


def test_vec2_rejects_non_finite():
    with pytest.raises(ValueError):
        vec2(float("nan"), 0.1)
    with pytest.raises(ValueError):
        vec2(0.1, float("inf"))


def test_distance_and_scale():
    a, b = (0.0, 0.0), (3.0, 4.0)
    assert math.isclose(distance(a, b), 5.0)
    assert scale(a, 2) == (0.0, 0.0)
    assert scale(b, 2) == (6.0, 8.0)


def test_sub():
    assert sub((5, 3), (2, 1)) == (3, 2)


def test_normalized_to_pixels_roundtrip():
    p = (0.25, 0.75)
    px = normalized_to_pixels(p, width=1920, height=1080)
    assert px == (480.0, 810.0)
    back = pixels_to_normalized(px, width=1920, height=1080)
    assert back[0] == pytest.approx(0.25)
    assert back[1] == pytest.approx(0.75)


def test_pixel_conversion_rejects_bad_resolution():
    with pytest.raises(ValueError):
        normalized_to_pixels((0.5, 0.5), width=0, height=100)


def test_angle_degrees_screen_convention():
    # y grows downward: angle 90 deg points DOWN on screen.
    assert angle_degrees((0, 0), (1, 0)) == pytest.approx(0.0)
    assert angle_degrees((0, 0), (0, 1)) == pytest.approx(90.0)
    assert angle_degrees((0, 0), (-1, 0)) == pytest.approx(180.0)


def test_rotate_deg_clockwise():
    # Rotating (1,0) by 90 deg gives (0,1) — downward on screen.
    assert rotate_deg((1, 0), 90.0) == pytest.approx((0, 1), abs=1e-9)


def test_wrap_angle():
    assert wrap_angle_deg(190) == pytest.approx(-170)
    assert wrap_angle_deg(-190) == pytest.approx(170)
    assert wrap_angle_deg(0) == pytest.approx(0)


def test_angle_lerp_shortest_arc():
    assert angle_lerp(170.0, -170.0, 0.5) == pytest.approx(180.0)
    assert angle_lerp(-10.0, 10.0, 0.5) == pytest.approx(0.0)


def test_easing_bounds_and_monotony():
    for name in ("linear", "ease_in", "ease_out", "ease_in_out"):
        assert apply_easing(0.0, name) == pytest.approx(0.0)
        assert apply_easing(1.0, name) == pytest.approx(1.0)
        prev = -1.0
        for i in range(11):
            v = apply_easing(i / 10, name)
            assert v >= prev - 1e-12
            prev = v


def test_easing_unknown_name():
    with pytest.raises(ValueError):
        apply_easing(0.5, "nope")


def test_in_unit_square():
    assert in_unit_square((0, 0))
    assert in_unit_square((1, 1))
    assert not in_unit_square((-0.01, 0.5))
    assert not in_unit_square((0.5, 1.01))


def test_polyline_samples_count_and_ends():
    pts = polyline_samples((0, 0), (1, 1), steps=10)
    assert len(pts) == 11
    assert pts[0] == (0, 0)
    assert pts[-1] == (1, 1)
