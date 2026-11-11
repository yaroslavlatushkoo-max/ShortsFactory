"""Библиотека жестов и реакций слушателя (этап C2).

Переиспользуемый набор: действие движка + «зачем» + в каких эмоциях
уместно.  Функция ``listener_gesture`` подбирает реакцию слушателя
на эмоцию говорящего (для диалогов).
"""
from __future__ import annotations

from typing import Mapping, Sequence

#: (действие, описание, уместные эмоции, длительность по умолчанию)
GESTURE_LIBRARY: tuple[tuple[str, str, tuple[str, ...], float], ...] = (
    ("nod", "слушатель кивает в знак согласия",
     ("neutral", "happy", "excited"), 1.0),
    ("shake_head", "слушатель качает головой (несогласие/удивление)",
     ("confused", "surprised"), 1.0),
    ("shrug", "пожимает плечами (не знаю)",
     ("confused", "neutral"), 1.2),
    ("point", "указывает на предмет/партнёра",
     ("neutral", "excited", "angry"), 1.2),
    ("wave", "машет рукой (приветствие/прощание)",
     ("happy", "excited", "neutral"), 1.2),
    ("react", "эмоционально реагирует на реплику",
     ("surprised", "scared", "sad", "angry"), 1.5),
    ("look_at", "смотрит на собеседника/предмет",
     ("neutral", "worried", "confused"), 1.2),
    ("listen_nod", "слушает и поддакивает",
     ("neutral", "happy"), 2.0),
    ("scared", "пугается (отшатывается)",
     ("scared",), 1.0),
    ("fall", "падает (комедийная эмоция)",
     ("surprised",), 1.0),
)

_GESTURES_BY_EMOTION: dict[str, tuple[str, ...]] = {}
for _action, _desc, _emotions, _dur in GESTURE_LIBRARY:
    for _em in _emotions:
        _list = _GESTURES_BY_EMOTION.setdefault(_em, [])
        if _action not in _list:
            _list.append(_action)
_GESTURES_BY_EMOTION = {k: tuple(v) for k, v in _GESTURES_BY_EMOTION.items()}

DEFAULT_GESTURE_DURATION: Mapping[str, float] = {
    a: d for a, _d, _e, d in GESTURE_LIBRARY
}


def gestures_for_emotion(emotion: str) -> Sequence[str]:
    """Действия, уместные при данной эмоции."""
    return _GESTURES_BY_EMOTION.get(emotion, ("listen",))


def listener_gesture(emotion: str, *,
                     prefer: str | None = None) -> tuple[str, float]:
    """Выбирает жест слушателя ответ.

    ``prefer`` — желаемое действие (если оно уместно); иначе первое
    подходящее, при незнакомой эмоции — ``listen_nod``.
    """
    opts = list(gestures_for_emotion(emotion))
    if prefer and prefer in opts:
        return prefer, DEFAULT_GESTURE_DURATION.get(prefer, 1.2)
    if opts and opts[0] != "listen":
        return opts[0], DEFAULT_GESTURE_DURATION.get(opts[0], 1.2)
    return "listen_nod", DEFAULT_GESTURE_DURATION.get("listen_nod", 2.0)


__all__ = [
    "GESTURE_LIBRARY",
    "DEFAULT_GESTURE_DURATION",
    "gestures_for_emotion",
    "listener_gesture",
]
