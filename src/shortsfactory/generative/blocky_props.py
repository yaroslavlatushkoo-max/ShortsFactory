"""Prop handling for blocky scenes: a prop can be held, dropped and thrown.

The renderer only knows a per-prop ``pos_track`` (``[[frame, [x, y, z]], ...]``),
so this module turns an authored *carry spec* into a dense track computed from
the real hand bone of the character.  Nothing here guesses the animation: the
hand positions come from the same frames the renderer uses, and a released prop
falls with gravity, hits the floor and bounces until it comes to rest.

Carry spec (inside a prop entry of the scene JSON)::

    "carry": {
        "character": "steve",       # who holds it
        "bone": "hand_R",           # default hand_R
        "start": 1.6,               # seconds, when the hand takes it
        "release": 7.4,             # seconds, optional: drop it here
        "grip": [0.0, 0.0, 0.12],   # offset from the hand bone head
        "ground": 0.0,              # floor height (z of the surface)
        "restitution": 0.35,        # bounce, optional
        "transfer": {               # optional: physical handoff
            "character": "alex", "bone": "hand_L",
            "start": 4.0, "duration": 0.3,
            "gap": 0.55             # hand-distance at which the pass happens
        }
    }

``collect_carry_tracks`` replaces ``pos_track`` on every prop that has a carry
spec; props without one keep their authored track.
"""

from __future__ import annotations

import math

from typing import Any, Sequence

#: Standard gravity in m/s²; the blocky world is 1 unit = 1 metre.
GRAVITY = 9.81
#: How much of the impact speed a dropped prop keeps.
RESTITUTION = 0.35
#: максимальная скорость «магического» подлёта предмета к кисти, м/с
MAX_APPROACH_SPEED = 1.2
#: минимальная дистанция между руками передающего и получателя, при которой
#: считается, что произошёл физический контакт и предмет может перейти
#: (сумма длин двух вытянутых навстречу рук персонажей; для стоящих рядом
#: актёров внутренние кисти удалены друг от друга примерно на это расстояние)
HANDOFF_GAP = 0.7
# F4: если кисти не встретились из-за поз, но корни (hips) персонажей стоят
# достаточно близко — передача всё равно возможна (предмет перелетает из руки
# в руку коротким движением).  Это сумма длин двух вытянутых рук + допуск.
REACH_SUM = 1.5
#: Horizontal speed kept after a bounce.
FRICTION = 0.55
#: Below this vertical speed the prop is considered at rest.
REST_SPEED = 0.35
#: Default time the prop takes to fly from its resting place into the hand.
APPROACH = 0.18

Vec3 = tuple[float, float, float]

VALID_BONES = {"hand_L", "hand_R"}


def _frame_of(seconds: float, fps: int) -> int:
    """1-based frame number of ``seconds`` (the renderer counts from 1)."""
    return int(round(float(seconds) * int(fps))) + 1


def _smooth(u: float) -> float:
    u = min(1.0, max(0.0, float(u)))
    return u * u * (3.0 - 2.0 * u)


def _lerp3(a: Vec3, b: Vec3, w: float) -> Vec3:
    return (
        a[0] + (b[0] - a[0]) * w,
        a[1] + (b[1] - a[1]) * w,
        a[2] + (b[2] - a[2]) * w,
    )


def hand_track(
    frames: Sequence[dict[str, Any]],
    character: str,
    bone: str = "hand_R",
) -> list[Vec3]:
    """World position of one hand bone in every frame of the scene.

    ``frames`` is the payload's per-frame bone dictionary, so the values are
    already placed in world space by ``_place_frames``.  A missing bone is a
    hard error: silently pinning the prop to the floor would look like a bug in
    the animation rather than in the scene file.
    """
    key = f"{str(character).lower()}:{bone}"
    out: list[Vec3] = []
    for index, frame in enumerate(frames):
        point = frame.get(key)
        if point is None:
            raise ValueError(
                f"Кадр {index + 1}: у персонажа «{character}» нет кости «{bone}»."
            )
        out.append((float(point[0]), float(point[1]), float(point[2])))
    return out


def held_at(hands, index: int, grip_v) -> Vec3:
    point = hands[min(index, len(hands) - 1)]
    return (point[0] + grip_v[0], point[1] + grip_v[1], point[2] + grip_v[2])


def carry_track(
    frames: Sequence[dict[str, Any]],
    rest: Sequence[float],
    spec: dict[str, Any],
    *,
    fps: int = 30,
    size: Sequence[float] = (0.2, 0.2, 0.2),
    offset: Sequence[float] = (0.0, 0.0, 0.0),
) -> list[list[Any]]:
    """Dense ``pos_track`` for a prop that is carried and possibly dropped.

    ``rest`` is the authored centre of the prop (where it lies before pickup),
    ``size`` is used for the floor contact, and ``offset`` shifts the prop
    relative to the hand (multi-box props share one carry spec).
    """
    if not frames:
        raise ValueError("Нет кадров для расчёта переноса предмета.")
    character = str(spec.get("character") or "").strip().lower()
    if not character:
        raise ValueError("Перенос предмета: не указан персонаж.")
    bone = str(spec.get("bone") or "hand_R")
    if bone not in VALID_BONES:
        raise ValueError(
            f"Перенос предмета: кость «{bone}» не поддерживается (нужна hand_L или hand_R)."
        )
    start = float(spec.get("start") or 0.0)
    approach = max(1e-3, float(spec.get("approach", APPROACH)))
    release = spec.get("release")
    if release is not None and float(release) < 0:
        # release < 0 — «нести до конца сцены» (семантика blocky_continuity)
        release = None
    release = None if release is None else float(release)
    if release is not None and release <= start:
        raise ValueError("Перенос предмета: release должен быть позже start.")
    grip = tuple(float(v) for v in (spec.get("grip") or (0.0, 0.0, 0.12)))
    shift = tuple(float(v) for v in offset)
    rest_v = (float(rest[0]), float(rest[1]), float(rest[2]))
    floor = float(spec.get("ground", 0.0)) + float(size[2]) * 0.5
    restitution = min(0.9, max(0.0, float(spec.get("restitution", RESTITUTION))))

    hands = hand_track(frames, character, bone)
    grip_v = (grip[0] + shift[0], grip[1] + shift[1], grip[2] + shift[2])

    # длительность подхода масштабируется реальной дистанцией rest -> кисть
    # в момент захвата: предмет с пола летит к руке дольше, чем с полки
    # (иначе подход 1.2 м за 0.2 с даёт 0.42 м/кадр и QC честно падает)
    grab_index = min(len(frames) - 1, max(0, int(round(start * fps))))
    rest_to_hand = math.dist(rest_v, held_at(hands, grab_index, grip_v))
    # подлёт ЗАКАНЧИВАЕТСЯ точно в момент захвата: окно [start - s, start]
    approach_s = min(start, max(approach, rest_to_hand / MAX_APPROACH_SPEED))

    transfer = spec.get("transfer") or {}
    second: list[Vec3] | None = None
    transfer_start = transfer_dur = 0.0
    transfer_gap = HANDOFF_GAP
    contact_frame: int | None = None
    if transfer:
        other = str(transfer.get("character") or "").strip().lower()
        if not other:
            raise ValueError("Перенос предмета: в transfer не указан персонаж.")
        other_bone = str(transfer.get("bone") or "hand_L")
        if other_bone not in VALID_BONES:
            raise ValueError(
                f"Перенос предмета: кость «{other_bone}» не поддерживается."
            )
        second = hand_track(frames, other, other_bone)
        if len(second) != len(hands):
            raise ValueError("Перенос предмета: у актёров разное число кадров.")
        transfer_start = float(transfer.get("start") or 0.0)
        transfer_dur = max(1e-3, float(transfer.get("duration", 0.3)))
        transfer_gap = max(0.15, float(transfer.get("gap", HANDOFF_GAP)))
        if transfer_start < start:
            raise ValueError("Перенос предмета: transfer стартует раньше захвата.")
        if release is not None and transfer_start >= release:
            raise ValueError("Перенос предмета: transfer должен быть до release.")

    def held(index: int) -> Vec3:
        point = hands[index]
        return (point[0] + grip_v[0], point[1] + grip_v[1], point[2] + grip_v[2])

    def held_second(index: int) -> Vec3:
        assert second is not None
        point = second[index]
        return (point[0] + grip_v[0], point[1] + grip_v[1], point[2] + grip_v[2])

    if second is not None:
        # F4: физическая передача — предмет остаётся у передающего, пока руки
        # не сблизятся на расстояние ``transfer_gap`` (контакт).  Только после
        # этого начинается короткий переход к руке получателя.  Если руки
        # так и не встретились — передача невозможна, честная ошибка.
        first_idx = min(len(frames) - 1, max(0, int(round(transfer_start * fps))))
        for idx in range(first_idx, len(frames)):
            if math.dist(held(idx), held_second(idx)) <= transfer_gap:
                contact_frame = idx
                break
        if contact_frame is None:
            # F4: если кисти не встретились из-за поз (например, рука поднята
            # или направлена в сторону), но персонажи стоят рядом — передача
            # всё равно возможна: предмет перелетает из руки в руку коротким
            # движением.  Проверяем достижимость по корням (hips).
            def _hip(index: int, who: str) -> Vec3:
                key = f"{str(who).lower()}:hips"
                point = frames[index].get(key)
                if point is None:
                    raise ValueError(
                        f"Кадр {index + 1}: у персонажа «{who}» нет кости hips."
                    )
                return (float(point[0]), float(point[1]), float(point[2]))

            best_i: int | None = None
            for idx in range(first_idx, len(frames)):
                d = math.dist(_hip(idx, character), _hip(idx, other))
                if d <= REACH_SUM:
                    best_i = idx
                    break
            if best_i is None:
                best_d = min(
                    math.dist(_hip(i, character), _hip(i, other))
                    for i in range(first_idx, len(frames))
                )
                raise ValueError(
                    f"Перенос предмета: руки «{character}» и «{other}» не сближаются "
                    f"(мин. дистанция {min(math.dist(held(i), held_second(i)) for i in range(first_idx, len(frames))):.2f} м "
                    f"> gap {transfer_gap:.2f} м; корни на {best_d:.2f} м) — передача невозможна."
                )
            contact_frame = best_i

    track: list[list[Any]] = []
    free: tuple[Vec3, Vec3, bool] | None = None
    dt = 1.0 / max(1, int(fps))
    for index in range(len(frames)):
        t = index / max(1, int(fps))
        approach_begin = start - approach_s
        if t < approach_begin:
            position = rest_v
        elif free is not None:
            position, velocity, resting = free
            if not resting:
                vx, vy, vz = velocity[0], velocity[1], velocity[2] - GRAVITY * dt
                x = position[0] + vx * dt
                y = position[1] + vy * dt
                z = position[2] + vz * dt
                if z <= floor:
                    z = floor
                    if abs(vz) > REST_SPEED:
                        vz = -vz * restitution
                        vx *= FRICTION
                        vy *= FRICTION
                    else:
                        vz = 0.0
                        vx = 0.0
                        vy = 0.0
                        resting = True
                position = (x, y, z)
                velocity = (vx, vy, vz)
                free = (position, velocity, resting)
            else:
                position = (position[0], position[1], floor)
        elif t < start:
            # предмет подлетает к кисти и приходит в неё ровно к моменту grab
            w = _smooth((t - approach_begin) / approach_s)
            position = _lerp3(rest_v, held(index), w)
        elif second is not None and index < contact_frame:
            position = held(index)
        elif second is not None and index < contact_frame + int(round(transfer_dur * fps)):
            w = _smooth((index - contact_frame) / (transfer_dur * fps))
            position = _lerp3(held(index), held_second(index), w)
        elif second is not None and (release is None or t < release):
            position = held_second(index)
        elif release is None or t < release:
            position = held(index)
        else:
            # release moment: the prop keeps the hand's speed, then gravity wins
            previous = track[-1][1] if track else rest_v
            velocity = (
                (previous[0] - track[-2][1][0]) / dt if len(track) >= 2 else 0.0,
                (previous[1] - track[-2][1][1]) / dt if len(track) >= 2 else 0.0,
                (previous[2] - track[-2][1][2]) / dt if len(track) >= 2 else 0.0,
            )
            free = (tuple(previous), velocity, False)  # type: ignore[arg-type]
            position = tuple(previous)  # type: ignore[assignment]
        track.append([index + 1, [round(position[0], 4), round(position[1], 4), round(position[2], 4)]])
    return track


def collect_carry_tracks(
    props: Sequence[dict[str, Any]],
    frames: Sequence[dict[str, Any]],
    *,
    fps: int = 30,
) -> list[dict[str, Any]]:
    """Return copies of ``props`` whose ``carry`` specs became ``pos_track``."""
    out: list[dict[str, Any]] = []
    for raw in props:
        prop = dict(raw)
        spec = prop.get("carry")
        if spec:
            spec = dict(spec)
            prop["pos_track"] = carry_track(
                frames,
                prop.get("center") or (0.0, 0.0, 0.0),
                spec,
                fps=fps,
                size=prop.get("size") or (0.2, 0.2, 0.2),
                offset=prop.get("carry_offset") or (0.0, 0.0, 0.0),
            )
            prop["carry"] = spec
        out.append(prop)
    return out


def validate_carry(
    props: Sequence[dict[str, Any]],
    actors: Sequence[dict[str, Any]],
    duration: float,
) -> list[str]:
    """Readable problems with the carry specs of a scene (empty list is good)."""
    problems: list[str] = []
    known = {
        str(actor.get("character") or "").strip().lower()
        for actor in actors
    }
    action_starts: dict[str, list[tuple[float, float, str]]] = {}
    for actor in actors:
        key = str(actor.get("character") or "").strip().lower()
        for act in actor.get("actions") or []:
            action_starts.setdefault(key, []).append(
                (
                    float(act.get("start") or 0.0),
                    float(act.get("duration") or 0.0),
                    str(act.get("action") or ""),
                )
            )
    for prop in props:
        spec = prop.get("carry")
        if not spec:
            continue
        name = prop.get("name") or "предмет"
        who = str(spec.get("character") or "").strip().lower()
        if who not in known:
            problems.append(f"«{name}»: держит несуществующий персонаж «{who}».")
            continue
        bone = str(spec.get("bone") or "hand_R")
        if bone not in VALID_BONES:
            problems.append(f"«{name}»: кость «{bone}» не поддерживается.")
        start = float(spec.get("start") or 0.0)
        if start < 0 or start > duration:
            problems.append(f"«{name}»: захват в {start:.2f} с вне сцены.")
        release = spec.get("release")
        if release is not None:
            release = float(release)
            # release < 0 — «нести до конца сцены» (тот же смысл использует
            # snapshot_final_state в blocky_continuity): это не ошибка.
            if release >= 0:
                if release <= start:
                    problems.append(f"«{name}»: отпускание {release:.2f} с не позже захвата.")
                if release > duration:
                    problems.append(f"«{name}»: отпускание {release:.2f} с вне сцены.")
        transfer = spec.get("transfer") or {}
        if transfer:
            other = str(transfer.get("character") or "").strip().lower()
            if other not in known:
                problems.append(f"«{name}»: передают несуществующему «{other}».")
            if str(transfer.get("bone") or "hand_L") not in VALID_BONES:
                problems.append(
                    f"«{name}»: кость передачи «{transfer.get('bone')}» не поддерживается."
                )
            if float(transfer.get("start") or 0.0) < start:
                problems.append(f"«{name}»: передача раньше захвата.")
        # holding something requires a hand action, otherwise the prop slides
        # after a hand that never moved
        verbs = {verb for _, _, verb in action_starts.get(who, [])}
        if not verbs & {"grab", "hold", "reach", "pickup", "interact", "use", "talk"}:
            problems.append(
                f"«{name}»: у «{who}» нет действия с руками (grab/hold/reach)."
            )
    return problems
