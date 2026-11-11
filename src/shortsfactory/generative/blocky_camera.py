"""Director of photography for blocky scenes.

The module turns a timed dialogue into a shot list: an establishing shot, then
close-ups on whoever is speaking, reaction shots on whoever is listening, and a
final wide.  It is pure Python (no Blender import), so the grammar of the cuts
can be tested without rendering a single frame.

A shot is a plain dict in the same shape :class:`~shortsfactory.generative.
blocky_scene.BlockyScene` already accepts for ``camera_cuts``::

    {"frame": 25, "center": [x, 0.0, z], "height": 1.45, "name": "close_up",
     "move": {"to_frame": 60, "height": 1.35}}

``move`` is optional: it makes the camera dolly/push inside the shot instead of
holding one framing.  ``name`` is documentation only and is ignored by the
renderer.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

#: Framing presets.  ``height`` is the vertical size the camera fits, ``dz`` the
#: height of the frame centre, ``yaw`` an optional orbit for a 3/4 view.
SHOT_PRESETS: dict[str, dict[str, float]] = {
    "establishing": {"height": 3.40, "dz": 0.90, "yaw": 0.0},
    "wide": {"height": 3.05, "dz": 0.90, "yaw": 0.0},
    "two_shot": {"height": 2.35, "dz": 1.15, "yaw": 0.0},
    "medium": {"height": 1.90, "dz": 1.32, "yaw": 0.0},
    "close_up": {"height": 1.42, "dz": 1.45, "yaw": 0.0},
    "reaction": {"height": 1.30, "dz": 1.45, "yaw": 0.0},
    "over_shoulder": {"height": 1.62, "dz": 1.34, "yaw": 0.36},
    "insert": {"height": 0.80, "dz": 0.55, "yaw": 0.0},
}

#: A shot shorter than this is a flash frame, not a cut.
MIN_SHOT_SECONDS = 0.45


def _actor_x(actors: Sequence[dict[str, Any]], character: str) -> float | None:
    for actor in actors:
        if str(actor.get("character") or "") != character:
            continue
        origin = actor.get("origin") or [0.0, 0.0, 0.0]
        try:
            return float(origin[0])
        except (TypeError, ValueError, IndexError):
            return 0.0
    return None


def _stage_center(actors: Sequence[dict[str, Any]]) -> float:
    xs = []
    for actor in actors:
        origin = actor.get("origin") or [0.0, 0.0, 0.0]
        try:
            xs.append(float(origin[0]))
        except (TypeError, ValueError, IndexError):
            continue
    return sum(xs) / len(xs) if xs else 0.0


def _shot(
    frame: int,
    name: str,
    x: float,
    *,
    move_to: float | None = None,
    to_height: float | None = None,
    yaw: float | None = None,
) -> dict[str, Any]:
    preset = SHOT_PRESETS[name]
    cut: dict[str, Any] = {
        "frame": int(frame),
        "center": [round(float(x), 3), 0.0, round(float(preset["dz"]), 3)],
        "height": round(float(preset["height"]), 3),
        "name": name,
    }
    shot_yaw = float(preset.get("yaw", 0.0)) if yaw is None else float(yaw)
    if shot_yaw:
        cut["yaw"] = round(shot_yaw, 3)
    if move_to is not None:
        cut["move"] = {
            "to_frame": int(move_to),
            "height": round(float(to_height or preset["height"]), 3),
        }
    return cut


def _clamp_moves(
    shots: list[dict[str, Any]], duration: float, fps: int
) -> list[dict[str, Any]]:
    """Keep every ``move.to_frame`` inside its own shot (the cut is exclusive)."""
    last_frame = int(round(duration * fps))
    for index, shot in enumerate(shots):
        move = shot.get("move")
        if not move:
            continue
        end = shots[index + 1]["frame"] - 1 if index + 1 < len(shots) else last_frame
        move["to_frame"] = min(max(int(move["to_frame"]), shot["frame"] + 1), end)
    return shots


def plan_shot_list(
    dialogue: Sequence[dict[str, Any]],
    actors: Sequence[dict[str, Any]],
    duration: float,
    *,
    fps: int = 30,
    reaction_shots: bool = True,
    push_in: bool = True,
    opening: float = 1.1,
    ending: float = 1.2,
) -> list[dict[str, Any]]:
    """Build a shot list from the dialogue, in scene time.

    Grammar: a cut lands a fraction of a second *before* each line so the mouth
    is already on screen when the voice starts; a speaker who keeps talking gets
    a new angle instead of a repeat; a listener gets a reaction shot in the
    middle of a long line; the film closes on a wide so the last beat lands.
    """
    fps = max(1, int(fps))
    min_shot = max(1, int(round(MIN_SHOT_SECONDS * fps)))
    lines = [
        dict(line)
        for line in dialogue
        if float(line.get("duration") or 0.0) > 0
    ]
    lines.sort(key=lambda line: float(line.get("start") or 0.0))
    stage = _stage_center(actors)
    shots: list[dict[str, Any]] = []

    if not lines:
        # nothing is said: one slow push-in on the stage is still a shot list
        end = max(2, int(round(duration * fps)))
        shots.append(
            _shot(1, "wide", stage, move_to=min(end, 1 + min_shot * 2),
                  to_height=SHOT_PRESETS["two_shot"]["height"])
            if push_in
            else _shot(1, "wide", stage)
        )
        return _clamp_moves(shots, duration, fps)

    first_frame = max(1, int(round(float(lines[0].get("start") or 0.0) * fps)))
    open_end = max(1, min(first_frame - 1, int(round(opening * fps))))
    shots.append(
        _shot(1, "establishing", stage, move_to=open_end,
              to_height=SHOT_PRESETS["wide"]["height"])
        if push_in and open_end > 1
        else _shot(1, "establishing", stage)
    )

    previous_speaker = ""
    for index, line in enumerate(lines):
        speaker = str(line.get("character") or "")
        start = float(line.get("start") or 0.0)
        length = float(line.get("duration") or 0.0)
        line_end = start + length
        cut = int(round(start * fps)) + 1 - max(2, int(round(0.12 * fps)))
        cut = max(cut, shots[-1]["frame"] + min_shot)
        speaker_x = _actor_x(actors, speaker)
        if speaker_x is None:
            speaker_x = stage
        emotion = str(line.get("emotion") or "").strip()

        if speaker and speaker == previous_speaker:
            name = "two_shot" if index % 2 else "medium"
        elif index and index % 4 == 3:
            name = "over_shoulder"
        elif emotion:
            name = "close_up"
        else:
            name = "medium"
        move_to = None
        to_height = None
        if push_in and name in {"close_up", "medium", "reaction"}:
            tail = int(round(max(0.4, min(1.2, length * 0.55)) * fps))
            move_to = cut + tail
            to_height = max(1.05, SHOT_PRESETS[name]["height"] - 0.12)
        shots.append(
            _shot(cut, name, speaker_x, move_to=move_to, to_height=to_height)
        )

        # a silent listener is part of the scene: cut to their face mid-line
        if reaction_shots and length >= 1.5:
            listener = next(
                (
                    actor
                    for actor in actors
                    if str(actor.get("character") or "") != speaker
                ),
                None,
            )
            if listener is not None:
                listener_x = _actor_x(actors, str(listener.get("character") or ""))
                reaction = cut + int(round(length * 0.55 * fps))
                reaction = max(reaction, shots[-1]["frame"] + min_shot)
                if reaction < int(round(line_end * fps)) - min_shot:
                    shots.append(
                        _shot(
                            reaction,
                            "reaction",
                            listener_x if listener_x is not None else stage,
                            move_to=reaction + int(round(0.8 * fps)),
                            to_height=SHOT_PRESETS["reaction"]["height"] - 0.10,
                        )
                    )
        previous_speaker = speaker

    last_line_end = max(
        float(line.get("start") or 0.0) + float(line.get("duration") or 0.0)
        for line in lines
    )
    close = max(
        int(round((min(duration, last_line_end + 0.2) - ending) * fps)) + 1,
        shots[-1]["frame"] + min_shot,
    )
    if close <= int(round(duration * fps)):
        shots.append(_shot(close, "wide", stage))
    return _clamp_moves(shots, duration, fps)


def validate_shot_list(
    shots: Sequence[dict[str, Any]], duration: float, fps: int = 30
) -> list[str]:
    """Return human-readable problems with a shot list (empty means valid)."""
    issues: list[str] = []
    fps = max(1, int(fps))
    last_frame = int(round(duration * fps))
    min_frames = min(6, max(1, int(round(MIN_SHOT_SECONDS * fps * 0.6))))
    frames = [int(shot.get("frame") or 0) for shot in shots]
    if not shots:
        return ["Список планов пуст."]
    if frames[0] != 1:
        issues.append(f"Первый план начинается с кадра {frames[0]}, а не с 1.")
    for index, frame in enumerate(frames):
        if frame < 1 or frame > last_frame:
            issues.append(f"План #{index + 1}: кадр {frame} вне 1..{last_frame}.")
        if index and frame - frames[index - 1] < min_frames:
            issues.append(
                f"План #{index + 1}: короче {min_frames} кадров "
                f"({frame - frames[index - 1]})."
            )
    for index, shot in enumerate(shots):
        center = shot.get("center") or []
        if len(center) != 3:
            issues.append(f"План #{index + 1}: center должен содержать 3 координаты.")
        if float(shot.get("height") or 0.0) <= 0:
            issues.append(f"План #{index + 1}: некорректная высота кадра.")
        move = shot.get("move")
        if not move:
            continue
        to_frame = int(move.get("to_frame") or 0)
        end = frames[index + 1] - 1 if index + 1 < len(frames) else last_frame
        if to_frame <= frames[index]:
            issues.append(f"План #{index + 1}: move.to_frame не позже начала плана.")
        elif to_frame > end:
            issues.append(
                f"План #{index + 1}: move.to_frame {to_frame} выходит за план "
                f"(конец {end})."
            )
        if float(move.get("height") or 0.0) <= 0:
            issues.append(f"План #{index + 1}: move.height должна быть > 0.")
    return issues


def shot_summary(shots: Sequence[dict[str, Any]], fps: int = 30) -> list[dict[str, Any]]:
    """Compact per-shot rows for reports and tests."""
    fps = max(1, int(fps))
    rows = []
    for index, shot in enumerate(shots):
        frame = int(shot.get("frame") or 0)
        nxt = int(shots[index + 1].get("frame") or 0) if index + 1 < len(shots) else 0
        rows.append(
            {
                "name": str(shot.get("name") or ""),
                "frame": frame,
                "seconds": round((frame - 1) / fps, 3),
                "length_s": round((nxt - frame) / fps, 3) if nxt else None,
                "height": round(float(shot.get("height") or 0.0), 3),
                "move": bool(shot.get("move")),
            }
        )
    return rows


# --------------------------------------------------------------------------
# F3: авто-режиссура по содержимому сцены (говорящие, предметы, bbox)
# --------------------------------------------------------------------------

#: Типичная верхняя точка пропса (м) для кадрирования.
PROP_TOP: dict[str, float] = {
    "door": 2.20,
    "tree": 2.50,
    "rock": 0.70,
    "grass": 0.30,
    "stump": 0.80,
    "glowing_cube": 0.55,
    "key": 0.25,
    "chest": 0.90,
    "umbrella": 1.60,
    "spring_box": 0.55,
}
#: Верх головы персонажа (м).
CHAR_TOP = 1.81
#: Запас от края кадра (м).
FRAME_MARGIN = 0.35
#: Минимальная длина плана в кадрах (≈0.47 с @30fps).
MIN_SHOT_FRAMES = 14


def _prop_x(props: Sequence[dict[str, Any]], name: str) -> float | None:
    for prop in props:
        if str(prop.get("name") or "") != name:
            continue
        # расширенные пропсы (из _expand_prop) хранят центр в "center",
        # сырые (авторские) — в "x"
        center = prop.get("center")
        if isinstance(center, (list, tuple)) and len(center) >= 1:
            try:
                return float(center[0])
            except (TypeError, ValueError):
                return 0.0
        try:
            return float(prop["x"])
        except (TypeError, ValueError, IndexError):
            return 0.0
    return None


def _prop_top(name: str) -> float:
    return PROP_TOP.get(str(name).lower(), 0.6)


#: Запас широкого плана под интерактивные предметы (м): сундук у края сцены
#: больше не обрезается кадром.
WIDE_PROP_MARGIN = 0.4
#: Пресеты предметов, которые широкий план всегда держит в кадре, даже если
#: ни одно действие на них не ссылается.
WIDE_PROP_PRESETS = {"chest", "glowing_cube", "key"}
#: Дефолтный fit широкого плана (совпадает с build_blocky_scene_script).
WIDE_FIT = 0.82


def _interactive_prop_refs(
    actors: Sequence[dict[str, Any]],
    props: Sequence[dict[str, Any]],
) -> list[str]:
    """Имена «интерактивных» предметов широкого плана.

    Это предметы, на которые ссылаются действия актёров (``params.prop``),
    плюс пресеты chest / glowing_cube / key.  Порядок детерминированный,
    без повторов.
    """
    refs: list[str] = []
    for actor in actors:
        for act in actor.get("actions") or []:
            ref = str((act.get("params") or {}).get("prop") or "")
            if ref and ref not in refs:
                refs.append(ref)
    for prop in props:
        names = {
            str(prop.get(key) or "").strip().lower()
            for key in ("preset", "prop", "name")
        }
        if not (names & WIDE_PROP_PRESETS):
            continue
        ref = str(prop.get("prop") or prop.get("name") or "")
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def _prop_x_by_ref(props: Sequence[dict[str, Any]], ref: str) -> float | None:
    """X-координата предмета по логическому имени (``prop`` или ``name``)."""
    for prop in props:
        if str(prop.get("prop") or prop.get("name") or "") != ref:
            continue
        center = prop.get("center")
        if isinstance(center, (list, tuple)) and len(center) >= 1:
            try:
                return float(center[0])
            except (TypeError, ValueError):
                return 0.0
        try:
            return float(prop["x"])
        except (TypeError, ValueError, IndexError):
            return 0.0
    return None


def _wide_cut(
    actors: Sequence[dict[str, Any]],
    props: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Широкий план: актёры плюс интерактивные предметы (запас 0.4 м).

    Без интерактивных предметов план остаётся прежним (центр сцены, высота
    3.40).  С предметами горизонтальный охват расширяется так, чтобы bbox
    «актёры + предметы» (с запасом) поместился в кадр 9:16 при fit 0.82.
    """
    refs = _interactive_prop_refs(actors, props)
    if not refs:
        return {
            "frame": 1,
            "center": [round(_stage_center(actors), 3), 0.0, 0.90],
            "height": 3.40,
            "name": "wide",
        }
    xs = [
        x
        for x in (
            _actor_x(actors, str(a.get("character") or "")) for a in actors
        )
        if x is not None
    ]
    for ref in refs:
        px = _prop_x_by_ref(props, ref)
        if px is not None:
            xs.append(px)
    if not xs:
        xs = [_stage_center(actors)]
    min_x = min(xs) - WIDE_PROP_MARGIN
    max_x = max(xs) + WIDE_PROP_MARGIN
    # видимая ширина кадра = height * (1080/1920) / fit
    height = max(3.40, (max_x - min_x) * WIDE_FIT / (1080.0 / 1920.0))
    return {
        "frame": 1,
        "center": [round((min_x + max_x) * 0.5, 3), 0.0, 0.90],
        "height": round(float(height), 3),
        "name": "wide",
    }


def _scene_events(
    dialogue: Sequence[dict[str, Any]],
    actors: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Все содержательные события сцены: реплики и действия с предметами."""
    events: list[dict[str, Any]] = []
    for line in dialogue:
        character = str(line.get("character") or "").lower()
        try:
            start = float(line.get("start") or 0.0)
            end = start + float(line.get("duration") or 1.0)
        except (TypeError, ValueError):
            continue
        events.append({"start": start, "end": end, "kind": "talk", "actor": character, "prop": None})
    for actor in actors:
        character = str(actor.get("character") or "").lower()
        for act in actor.get("actions") or []:
            ref = str((act.get("params") or {}).get("prop") or "")
            if not ref:
                continue
            try:
                start = float(act.get("start") or 0.0)
                end = start + float(act.get("duration") or 1.0)
            except (TypeError, ValueError):
                continue
            events.append({"start": start, "end": end, "kind": "action", "actor": character, "prop": ref})
    events.sort(key=lambda event: event["start"])
    return events


def _framed_shot(
    frame: int,
    xs: Sequence[float],
    top: float,
    name: str,
) -> dict[str, Any]:
    """Собирает план так, чтобы объекты не обрезались (голова, пол, стороны)."""
    min_x, max_x = min(xs), max(xs)
    center_x = (min_x + max_x) * 0.5
    span_x = max_x - min_x
    h_min = float(SHOT_PRESETS[name]["height"])
    h_vert = top + 2.0 * FRAME_MARGIN
    h_horiz = span_x / (1080.0 / 1920.0) + 2.0 * FRAME_MARGIN
    height = max(h_min, h_vert, h_horiz)
    center_z = top * 0.5 + FRAME_MARGIN
    return {
        "frame": int(frame),
        "center": [round(center_x, 3), 0.0, round(center_z, 3)],
        "height": round(height, 3),
        "name": name,
    }


def plan_scene_shots(
    dialogue: Sequence[dict[str, Any]],
    actors: Sequence[dict[str, Any]],
    props: Sequence[dict[str, Any]],
    *,
    duration: float,
    fps: int = 30,
) -> list[dict[str, Any]]:
    """Авто-режиссура: план по содержимому сцены (F3).

    В отличие от ``plan_shot_list`` (чистая грамматика реплик), здесь учитываются
    координаты актёров и предметов: говорящий, собеседник, активный предмет и
    их взаимное расположение. Каждый план гарантированно покрывает bbox
    участников (верх головы 1.81 м и предмет не обрезаются).
    """
    fps = max(1, int(fps))
    empty = [_wide_cut(actors, props)]
    events = _scene_events(dialogue, actors)
    if not events:
        return empty
    shots: list[dict[str, Any]] = [_wide_cut(actors, props)]
    last_frame = int(round(duration * fps))
    for event in events:
        frame = max(1, int(round(event["start"] * fps)) + 1)
        if frame <= shots[-1]["frame"] + MIN_SHOT_FRAMES - 1:
            continue
        if frame > last_frame:
            break
        # участники кадра
        xs: list[float] = []
        top = 0.0
        actor_x = _actor_x(actors, event["actor"]) if event["actor"] else None
        if event["kind"] == "talk":
            if actor_x is not None:
                xs.append(actor_x)
            top = CHAR_TOP
            name = "medium"
            # второй собеседник — если его реплика пересекается
            for other in events:
                if (
                    other is not event
                    and other["kind"] == "talk"
                    and other["actor"] != event["actor"]
                    and other["start"] < event["end"]
                    and other["end"] > event["start"]
                ):
                    other_x = _actor_x(actors, other["actor"])
                    if other_x is not None:
                        xs.append(other_x)
                    name = "two_shot"
        else:  # action с предметом
            px = _prop_x(props, event["prop"])
            dist = (
                math.hypot(px - actor_x, 0.0)
                if px is not None and actor_x is not None
                else 0.0
            )
            if px is not None and (actor_x is None or dist > 1.5):
                # фокус на предмете
                xs.append(px)
                top = _prop_top(event["prop"])
                name = "insert"
            else:
                if actor_x is not None:
                    xs.append(actor_x)
                if px is not None:
                    xs.append(px)
                top = max(CHAR_TOP, _prop_top(event["prop"]))
                name = "medium"
        if not xs:
            xs = [_stage_center(actors)]
        shots.append(_framed_shot(frame, xs, top, name))
    if len(shots) > 1 and shots[-1]["name"] in ("wide", "establishing"):
        shots[-1]["frame"] = last_frame
    return shots
