"""Unit tests for the live-listener gestures (stage F4)."""
from __future__ import annotations

from shortsfactory.generative.animation_pipeline import blocky_hero as bh


def _actors():
    return [
        {
            "character": "steve",
            "origin": (0.0, 0.0, 0.0),
            "actions": [{"action": "idle", "start": 0.0, "duration": 6.0}],
            "voice_lines": [{"start": 1.0, "duration": 2.0, "text": "hi",
                             "emotion": "happy"}],
        },
        {
            "character": "alex",
            "origin": (0.8, 0.0, 0.0),
            "actions": [{"action": "idle", "start": 0.0, "duration": 6.0}],
        },
    ]


def test_listener_gets_gesture_in_speech_window():
    actors = _actors()
    n = bh._apply_listener_gestures(actors)
    assert n == 1
    actions = actors[1]["actions"]
    names = [a["action"] for a in actions]
    assert "nod" in names or "listen_nod" in names or "react" in names
    # no overlap, durations still sum to 6.0
    cursor = 0.0
    for a in actions:
        assert abs(float(a["start"]) - cursor) < 1e-6
        cursor += float(a["duration"])
    assert abs(cursor - 6.0) < 1e-6


def test_listener_turn_to_speaker_when_offset():
    actors = _actors()
    bh._apply_listener_gestures(actors, turn_to_speaker=True)
    names = [a["action"] for a in actors[1]["actions"]]
    assert "turn_in_place" in names


def test_listener_skip_when_listener_talks():
    actors = _actors()
    actors[1]["actions"] = [
        {"action": "idle", "start": 0.0, "duration": 1.0},
        {"action": "talk", "start": 1.0, "duration": 2.0},
        {"action": "idle", "start": 3.0, "duration": 3.0},
    ]
    n = bh._apply_listener_gestures(actors)
    assert n == 0
    assert [a["action"] for a in actors[1]["actions"]] == [
        "idle", "talk", "idle"]


def test_listener_deterministic():
    a1 = _actors()
    a2 = _actors()
    bh._apply_listener_gestures(a1)
    bh._apply_listener_gestures(a2)
    assert a1[1]["actions"] == a2[1]["actions"]


def test_payload_still_builds_with_listeners(tmp_path):
    actors = _actors()
    _, payload = bh.build_blocky_scene_payload(
        tmp_path, actors, scenario="f4_probe", fps=30)
    assert len(payload["frames"]) == 180
