"""Unit tests for the gesture library (stage C2)."""
from __future__ import annotations


from shortsfactory.generative.animation_pipeline.gestures import (
    DEFAULT_GESTURE_DURATION,
    GESTURE_LIBRARY,
    gestures_for_emotion,
    listener_gesture,
)


def test_library_has_actions_and_durations():
    actions = [a for a, _d, _e, _dur in GESTURE_LIBRARY]
    assert "nod" in actions
    assert "listen_nod" in actions
    assert all(DEFAULT_GESTURE_DURATION[a] > 0 for a in actions)


def test_gestures_for_emotion_known():
    opts = gestures_for_emotion("happy")
    assert opts
    assert "wave" in opts


def test_gestures_for_emotion_unknown_falls_back_to_listen():
    assert gestures_for_emotion("nonexistent") == ("listen",)


def test_listener_gesture_prefers_requested_when_appropriate():
    action, dur = listener_gesture("happy", prefer="wave")
    assert action == "wave"
    assert dur == DEFAULT_GESTURE_DURATION["wave"]


def test_listener_gesture_unknown_emotion_returns_listen_nod():
    action, _ = listener_gesture("nothing")
    assert action in ("listen", "listen_nod")


def test_listener_gesture_prefer_not_allowed_ignored():
    action, _ = listener_gesture("sad", prefer="wave")
    assert action != "wave"
