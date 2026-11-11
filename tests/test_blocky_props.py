"""D11: a prop follows a hand, is released and falls to the floor."""

from __future__ import annotations

import math

import pytest

from shortsfactory.generative.blocky_props import (
    carry_track,
    collect_carry_tracks,
    hand_track,
    validate_carry,
)


def _frames(count: int = 90, *, speed: float = 0.25) -> list[dict]:
    """Two actors walking their hands slowly; world-space bone dictionaries."""
    frames = []
    for index in range(count):
        t = index / 30.0
        frames.append(
            {
                "steve:hand_R": [speed * t, 0.9, 1.0 + 0.05 * t],
                "steve:hand_L": [-0.5, 0.9, 1.0],
                "alex:hand_L": [1.0 - speed * t, 0.9, 1.05],
            }
        )
    return frames


def _spec(**extra):
    spec = {"character": "steve", "bone": "hand_R", "start": 0.5, "release": 1.5}
    spec.update(extra)
    return spec


# ------------------------------------------------------------------ hand track


def test_hand_track_returns_world_positions():
    frames = _frames(10)
    track = hand_track(frames, "steve", "hand_R")
    assert len(track) == 10
    assert track[0] == (0.0, 0.9, 1.0)
    assert track[-1][0] == pytest.approx(0.25 * 9 / 30.0)


def test_hand_track_rejects_a_missing_bone():
    with pytest.raises(ValueError, match="нет кости"):
        hand_track(_frames(3), "alex", "hand_R")


# ----------------------------------------------------------------- carry track


def test_carry_track_rests_then_reaches_the_hand_without_a_jump():
    frames = _frames()
    rest = (0.0, 0.9, 1.0)  # lying next to the hand
    track = carry_track(frames, rest, _spec(approach=0.2), fps=30)
    assert [frame for frame, _ in track] == list(range(1, len(frames) + 1))
    # still at rest before the pickup, and the first approach frame is the rest
    assert track[8][1] == [0.0, 0.9, 1.0]  # ещё на земле (до начала подлёта)
    assert track[14][1] != [0.0, 0.9, 1.0]  # mid-approach: летит к кисти
    # the step into the hand never teleports: each frame moves less than 6 cm
    steps = [
        math.dist(track[i][1], track[i - 1][1]) for i in range(16, 22)
    ]
    assert max(steps) < 0.06, steps


def test_carry_track_holds_a_constant_offset_from_the_hand():
    frames = _frames()
    grip = (0.0, 0.0, 0.12)
    track = carry_track(frames, (0.0, 0.0, 0.1), _spec(approach=0.2), fps=30)
    hands = hand_track(frames, "steve", "hand_R")
    for index in range(21, 45):  # after the approach, before the release
        pos = track[index][1]
        assert pos[0] == pytest.approx(hands[index][0] + grip[0], abs=1e-4)
        assert pos[1] == pytest.approx(hands[index][1] + grip[1], abs=1e-4)
        assert pos[2] == pytest.approx(hands[index][2] + grip[2], abs=1e-4)


def test_released_prop_falls_to_the_floor_and_stops():
    frames = _frames()
    track = carry_track(
        frames,
        (0.0, 0.0, 0.1),
        _spec(approach=0.2, release=1.0, ground=0.0),
        fps=30,
        size=(0.2, 0.2, 0.2),
    )
    floor = 0.1
    zs = [entry[1][2] for entry in track]
    assert min(zs) >= floor - 1e-6
    # the prop does not keep flying: it comes to rest on the floor
    assert track[-1][1][2] == pytest.approx(floor, abs=1e-3)
    assert all(z <= zs[29] + 1e-6 for z in zs[30:]), "the prop bounced higher than it fell from"
    # gravity must actually be applied, not a straight line to the floor
    assert zs[-1] < zs[29]


def test_release_with_a_moving_hand_keeps_the_horizontal_speed():
    frames = _frames(count=120, speed=1.2)
    track = carry_track(
        frames,
        (0.0, 0.0, 0.1),
        _spec(approach=0.2, release=1.0, ground=0.0),
        fps=30,
    )
    at_release = track[30][1]
    later = track[45][1]
    assert later[0] > at_release[0], "the prop stopped dead in the air"
    assert later[2] < at_release[2], "the prop did not fall"


def test_transfer_hands_the_prop_to_the_second_actor():
    frames = _frames()
    spec = _spec(
        approach=0.2,
        release=None,
        transfer={"character": "alex", "bone": "hand_L", "start": 1.2, "duration": 0.4},
    )
    track = carry_track(frames, (0.0, 0.0, 0.1), spec, fps=30)
    second = hand_track(frames, "alex", "hand_L")
    for index in range(48, 60):
        pos = track[index][1]
        assert pos[0] == pytest.approx(second[index][0], abs=1e-4)
        assert pos[1] == pytest.approx(second[index][1], abs=1e-4)
        assert pos[2] == pytest.approx(second[index][2] + 0.12, abs=1e-4)


def test_transfer_waits_for_real_hand_contact():
    """F4: предмет держится у передающего, пока руки не встретятся."""
    frames = []
    for index in range(90):
        t = index / 30.0
        # алекс подходит справа: рука едет от x=3.0 к x=0.0
        frames.append(
            {
                "steve:hand_R": [0.0, 0.9, 1.0],
                "steve:hand_L": [-0.5, 0.9, 1.0],
                "alex:hand_L": [3.0 - 2.0 * min(t, 1.5), 0.9, 1.05],
            }
        )
    spec = _spec(
        approach=0.2,
        release=None,
        transfer={"character": "alex", "bone": "hand_L", "start": 1.0, "duration": 0.3},
    )
    track = carry_track(frames, (0.0, 0.0, 0.1), spec, fps=30)
    first = hand_track(frames, "steve", "hand_R")
    second = hand_track(frames, "alex", "hand_L")
    # первый кадр, где руки ближе порога контакта (0.7 м)
    contact_idx = next(
        i for i in range(len(frames))
        if math.dist(first[i], second[i]) <= 0.7
    )
    # до контакта предмет остаётся у Стива (не телепортируется к Алекс)
    for index in range(20, contact_idx):
        pos = track[index][1]
        assert pos[0] == pytest.approx(first[index][0], abs=1e-4)
        assert pos[1] == pytest.approx(first[index][1], abs=1e-4)
        assert pos[2] == pytest.approx(first[index][2] + 0.12, abs=1e-4)
    # после короткого перехода предмет в руке Алекс
    after = contact_idx + int(round(0.3 * 30)) + 2
    pos = track[after][1]
    assert pos[0] == pytest.approx(second[after][0], abs=1e-4)
    assert pos[2] == pytest.approx(second[after][2] + 0.12, abs=1e-4)


def test_transfer_fails_when_hands_never_meet():
    frames = []
    for index in range(90):
        t = index / 30.0
        frames.append(
            {
                "steve:hand_R": [0.0, 0.9, 1.0],
                "steve:hand_L": [-0.5, 0.9, 1.0],
                "steve:hips": [0.0, 0.95, 0.0],
                "alex:hand_L": [3.0, 0.9, 1.05],  # стоит далеко
                "alex:hips": [4.0, 0.95, 0.0],  # и корни далеко
            }
        )
    spec = _spec(
        approach=0.2,
        release=None,
        transfer={"character": "alex", "bone": "hand_L", "start": 1.0, "duration": 0.3},
    )
    with pytest.raises(ValueError, match="не сближаются"):
        carry_track(frames, (0.0, 0.0, 0.1), spec, fps=30)


def test_transfer_falls_back_to_hip_distance_when_hands_far():
    """F4: кисти не встретились (позы), но корни стоят рядом — передача
    всё равно возможна (предмет перелетает из руки в руку)."""
    frames = []
    for index in range(90):
        t = index / 30.0
        frames.append(
            {
                "steve:hand_R": [0.0, 0.9, 1.0],
                "steve:hand_L": [-0.5, 0.9, 1.0],
                "steve:hips": [0.0, 0.95, 0.0],
                "alex:hand_L": [3.0, 0.9, 1.05],  # руки далеко
                "alex:hips": [0.5, 0.95, 0.0],  # но корни близко
            }
        )
    spec = _spec(
        approach=0.2,
        release=None,
        transfer={"character": "alex", "bone": "hand_L", "start": 1.0, "duration": 0.3},
    )
    track = carry_track(frames, (0.0, 0.0, 0.1), spec, fps=30)
    second = hand_track(frames, "alex", "hand_L")
    # в конце предмет уже в руке Алекс (после перехода)
    pos = track[-1][1]
    assert pos[0] == pytest.approx(second[-1][0], abs=1e-4)
    assert pos[2] == pytest.approx(second[-1][2] + 0.12, abs=1e-4)


def test_carry_track_rejects_broken_specs():
    frames = _frames()
    with pytest.raises(ValueError, match="не указан персонаж"):
        carry_track(frames, (0, 0, 0), {"start": 0.0})
    with pytest.raises(ValueError, match="не поддерживается"):
        carry_track(frames, (0, 0, 0), _spec(bone="foot_L"))
    with pytest.raises(ValueError, match="позже start"):
        carry_track(frames, (0, 0, 0), _spec(start=1.0, release=0.5))
    with pytest.raises(ValueError, match="раньше захвата"):
        carry_track(
            frames,
            (0, 0, 0),
            _spec(transfer={"character": "alex", "start": 0.1}),
        )
    with pytest.raises(ValueError, match="Нет кадров"):
        carry_track([], (0, 0, 0), _spec())


# --------------------------------------------------------------- scene helpers


def test_collect_carry_tracks_only_touches_carry_props():
    frames = _frames(30)
    props = [
        {"name": "cube", "center": [0.0, 0.0, 0.1], "carry": _spec(release=0.9)},
        {"name": "rock", "center": [1.0, 1.0, 0.0]},
    ]
    out = collect_carry_tracks(props, frames, fps=30)
    assert "pos_track" in out[0] and len(out[0]["pos_track"]) == 30
    assert "pos_track" not in out[1]
    # the source list is not mutated
    assert "pos_track" not in props[0]


def _actor(character: str, verbs: list[str]) -> dict:
    actions = [
        {"action": verb, "start": float(i), "duration": 1.0}
        for i, verb in enumerate(verbs)
    ]
    return {"character": character, "actions": actions}


def test_validate_carry_reports_readable_problems():
    actors = [_actor("steve", ["grab"]), _actor("alex", ["idle"])]
    good = [{"name": "cube", "carry": _spec()}]
    assert validate_carry(good, actors, 3.0) == []

    bad = [
        {"name": "cube", "carry": _spec(character="nobody")},
        {"name": "stick", "carry": _spec(bone="foot_L")},
        {"name": "ball", "carry": _spec(start=2.0, release=1.0)},
        {
            "name": "key",
            "carry": _spec(character="alex"),
        },
    ]
    problems = validate_carry(bad, actors, 3.0)
    text = " ".join(problems)
    assert "несуществующий персонаж" in text
    assert "кость «foot_L» не поддерживается" in text
    assert "не позже захвата" in text
    assert "нет действия с руками" in text
