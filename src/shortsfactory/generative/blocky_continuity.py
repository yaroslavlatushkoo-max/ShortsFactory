"""F1: межсценовая непрерывность состояния мира (RC1, B-01/B-12/B-13/B-21).

Модуль отвечает за единое состояние мира, которое переносится между сценами:

* положение персонажей (запрет телепортации без сюжетного перехода);
* состояние дверей (закрыта/открыта) и других интерактивных объектов;
* предмет в руках персонажа и его «след» между сценами;
* запрет дублирования предметов в одной сцене;
* валидация конфликтующих событий (например, дверь уже открыта, но её
  снова «открывают» анимацией).

Правила применяются к уже нормализованным сценам ``storyboard["scenes"]``:
подготовленная сцена записывается на место исходного файла сцены, после чего
её рендерит обычный конвейер.  Состояние накапливается в :class:`WorldState`
по ходу ``produce_film``.

Ключевое следствие для B-01: дверь по умолчанию **закрыта и статична** —
поворот появляется только если сцена явно содержит событие (``open_frame``)
или состояние ``state="open"``.  Повторное применение дефолтных keyframes к
объекту с сохранённым состоянием запрещено.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Sequence

__all__ = [
    "DOOR_PRESET",
    "FEATURE_VERSION",
    "WorldState",
    "hash_scene",
    "prepare_scene_continuity",
    "snapshot_final_state",
    "validate_scene_continuity",
]

FEATURE_VERSION = "f1-1"

#: Пресет двери (в нормализованной сцене пресеты пишутся строчными).
DOOR_PRESET = "door"

#: Максимальное смещение авторизованного origin, которое ещё не считается
#: телепортацией внутри одной локации.
TELEPORT_EPS = 0.001


@dataclass
class WorldState:
    """Единое состояние мира, сохраняемое между сценами."""

    actors: dict[str, list[float]] = field(default_factory=dict)
    #: имя пропса -> состояние
    props: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: имя пропса -> персонаж, который держит его в конце последней сцены
    carried: dict[str, str] = field(default_factory=dict)
    #: последняя сцена, где появлялся пропс
    last_seen: dict[str, int] = field(default_factory=dict)


def _name(entry: dict[str, Any], index: int) -> str:
    name = str(entry.get("name") or "").strip()
    if name:
        return name
    return f"{entry.get('preset') or 'prop'}#{index}"


def _env_of(scene: dict[str, Any]) -> str:
    return str(scene.get("environment") or "").strip().lower()


def _door_open_frame(entry: dict[str, Any]) -> int | None:
    if str(entry.get("preset") or "").strip().lower() != DOOR_PRESET:
        return None
    raw = entry.get("open_frame")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


def _is_open(entry: dict[str, Any]) -> bool:
    return str(entry.get("state") or "").strip().lower() == "open"


def hash_scene(scene: dict[str, Any]) -> str:
    """Стабильный входной хеш сцены для resume (входные данные не изменились)."""
    canonical = json.dumps(scene, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload = f"{FEATURE_VERSION}|{canonical}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return digest[:16]


def validate_scene_continuity(scene: dict[str, Any]) -> list[str]:
    """Локальные проверки одной сцены (до применения состояния мира)."""
    problems: list[str] = []
    seen: dict[str, int] = {}
    for index, entry in enumerate(scene.get("props") or [], 1):
        name = _name(entry, index)
        if name in seen:
            problems.append(f"Пропс «{name}» продублирован в сцене (запись #{seen[name]} и #{index}).")
        seen[name] = index
        if _is_open(entry):
            open_frame = _door_open_frame(entry)
            if open_frame is not None:
                problems.append(
                    f"Дверь «{name}» уже открыта (state=open), но ей задано "
                    f"событие открытия open_frame={open_frame} — конфликт событий."
                )
    return problems


def prepare_scene_continuity(
    scene: dict[str, Any],
    world: WorldState,
    *,
    scene_index: int = 1,
) -> tuple[dict[str, Any], list[str]]:
    """Применить сохранённое состояние мира к сцене.

    Возвращает ``(новый_сцена, проблемы)``.  Сцена изменяется только там, где
    состояние предыдущих сцен прямо конфликтует с дефолтами; авторские данные,
    заданные явно, не перетираются.
    """
    new_scene = json.loads(json.dumps(scene, ensure_ascii=False))
    issues = validate_scene_continuity(new_scene)

    env = _env_of(new_scene)
    # --- персонажи: исчезновение/появление без сюжетного повода -------------
    prev_cast = {
        str(k) for k in world.actors
        if not str(k).endswith(":env")
    }
    current_cast = {
        str(a.get("character") or "")
        for a in new_scene.get("actors") or []
        if str(a.get("character") or "")
    }
    for who in sorted(prev_cast - current_cast):
        prev_env = world.actors.get(who + ":env")
        if prev_env == env:
            # персонаж остаётся в той же локации — возвращаем его как статиста
            # (стоит рядом, молчит), чтобы не было внезапного исчезновения
            prev_origin = world.actors.get(who)
            if prev_origin is not None:
                new_scene.setdefault("actors", []).append(
                    {
                        "character": who,
                        "origin": list(prev_origin),
                        "actions": [
                            {
                                "action": "idle",
                                "start": 0.0,
                                "duration": float(
                                    new_scene.get("duration") or 8.0
                                ),
                            }
                        ],
                    }
                )
    # --- персонажи: запрет телепортации без сюжетного перехода --------------
    for actor in new_scene.get("actors") or []:
        who = str(actor.get("character") or "")
        if not who:
            continue
        prev = world.actors.get(who)
        if prev is None:
            continue
        origin = actor.get("origin") or [0.0, 0.0, 0.0]
        try:
            dx = abs(float(origin[0]) - prev[0])
            dz = abs(float(origin[2] or 0.0) - prev[2])
        except (TypeError, ValueError, IndexError):
            continue
        if dx <= TELEPORT_EPS and dz <= TELEPORT_EPS:
            # не двигался — нечего править
            continue
        prev_env = world.actors.get(who + ":env")
        if prev_env != env:
            # намеренная смена локации/времени — разрешённый переход
            continue
        # Авто-исправление: возвращаем предыдущее положение, чтобы персонаж не
        # телепортировался в пределах одной локации.  Это не проблема итогового
        # видео (оно непрерывно), поэтому в issues не пишем.
        actor["origin"] = [prev[0], prev[1], prev[2]]

    # --- персонажи: минимальный зазор в одной локации --------------------
    # LLM часто ставит обоих героев в одну точку (origin [0, ...]); без этого
    # силуэты пересекаются и QC-гейт silhouettes честно падает.  Расставляем
    # актёров с минимальным зазором, не трогая порядок и замысел.
    scene_actors = new_scene.get("actors") or []
    if env and len(scene_actors) > 1:
        xs: list[float] = []
        for a in scene_actors:
            origin = a.get("origin") or [0.0, 0.0, 0.0]
            try:
                xs.append(float(origin[0]))
            except (TypeError, ValueError, IndexError):
                xs.append(0.0)
        mean_x = sum(xs) / len(xs)
        for i, a in enumerate(scene_actors):
            sign = -1.0 if i % 2 == 0 else 1.0
            target = mean_x + sign * (0.60 + 0.65 * (i // 2))
            origin = a.get("origin")
            if origin is None:
                origin = [0.0, 0.0, 0.0]
                a["origin"] = origin
            try:
                origin[0] = round(float(target), 3)
            except (TypeError, ValueError, IndexError):
                pass

    # --- пропсы ------------------------------------------------------------
    for index, entry in enumerate(new_scene.get("props") or [], 1):
        name = _name(entry, index)
        prev_state = world.props.get(name)
        if prev_state is None:
            continue

        preset = str(entry.get("preset") or "").strip().lower()
        # дверь: сохранить открытое/закрытое состояние, запретить повторные
        # дефолтные keyframes (B-01/B-13/B-21)
        if preset == DOOR_PRESET:
            prev_open = bool(prev_state.get("open"))
            authored_open = _is_open(entry)
            open_frame = _door_open_frame(entry)
            if prev_open:
                if not authored_open and open_frame is None:
                    entry["state"] = "open"
                elif authored_open and open_frame is not None:
                    issues.append(
                        f"Дверь «{name}» уже открыта с прошлой сцены, но сцена "
                        f"{scene_index} снова открывает её — конфликт событий."
                    )
            else:
                # закрыта: если явного события нет — остаётся статично закрытой
                if open_frame is None and not authored_open:
                    entry.pop("state", None)
                    entry.pop("open_frame", None)

        # персонаж, держащий предмет: сохранить владельца, если он есть в сцене
        prev_carrier = world.carried.get(name)
        if prev_carrier is not None:
            cast = {str(a.get("character") or "") for a in new_scene.get("actors") or []}
            carry = entry.get("carry")
            if prev_carrier in cast and not carry:
                entry["carry"] = {
                    "character": prev_carrier,
                    "bone": "hand_R",
                    "start": 0.0,
                    "release": -1.0,
                    "approach": 0.2,
                    "grip": [0.0, 0.0, 0.16],
                    "ground": 0.0,
                }
                # персонаж продолжает нести предмет — он обязан его держать;
                # иначе validate_carry жалуется на отсутствие действия с руками
                for actor in new_scene.get("actors") or []:
                    if str(actor.get("character") or "") != prev_carrier:
                        continue
                    verbs = {
                        str(a.get("action") or "")
                        for a in actor.get("actions") or []
                    }
                    if not verbs & {
                        "grab", "hold", "reach", "pickup",
                        "interact", "use", "talk",
                    }:
                        dur = float(new_scene.get("duration") or 6.0)
                        actor.setdefault("actions", []).append(
                            {
                                "action": "hold",
                                "start": 0.0,
                                "duration": dur,
                                "params": {"prop": name},
                            }
                        )
                        actor["actions"].sort(
                            key=lambda a: float(a.get("start") or 0.0)
                        )
            elif prev_carrier not in cast and not carry:
                issues.append(
                    f"Предмет «{name}» держал «{prev_carrier}», но его нет в сцене "
                    f"{scene_index} — предмет некому передать."
                )

    return new_scene, issues


def snapshot_final_state(
    scene: dict[str, Any],
    world: WorldState,
    *,
    scene_index: int = 1,
    actor_roots: dict[str, Sequence[float]] | None = None,
) -> None:
    """Записать финальное состояние мира после сцены ``scene``.

    ``actor_roots`` — карта «персонаж → фактический финальный корень
    (мировые координаты)» из отчёта рендера.  Если передан, он используется
    вместо начального ``origin``: так следующая сцена наследует реальное место
    остановки персонажа, а не стартовую точку (запрет «телепортации обратно»).
    """
    env = _env_of(scene)
    for actor in scene.get("actors") or []:
        who = str(actor.get("character") or "")
        if not who:
            continue
        origin = actor.get("origin") or [0.0, 0.0, 0.0]
        if actor_roots and who in actor_roots:
            root = actor_roots[who]
            # final_root содержит высоту корня (z ≈ 0.6) — для следующей сцены
            # персонаж должен стартовать на уровне пола (z = 0), иначе стопы
            # окажутся в воздухе и QC «foot» провалится (hover >> порог).
            world.actors[who] = [float(root[0]), float(root[1]), 0.0]
        else:
            world.actors[who] = [float(v) for v in origin[:3]]
        world.actors[who + ":env"] = env

    for index, entry in enumerate(scene.get("props") or [], 1):
        name = _name(entry, index)
        preset = str(entry.get("preset") or "").strip().lower()
        state: dict[str, Any] = {"preset": preset}
        if preset == DOOR_PRESET:
            open_frame = _door_open_frame(entry)
            duration = float(scene.get("duration") or 0.0)
            fps = int(scene.get("fps") or 30)
            frames = max(1, int(round(duration * fps)))
            state["open"] = bool(
                _is_open(entry) or (open_frame is not None and open_frame <= frames)
            )
        world.props[name] = state
        world.last_seen[name] = scene_index

        carry = entry.get("carry")
        if isinstance(carry, dict):
            who = str(carry.get("character") or "")
            release = float(carry.get("release") or -1.0)
            duration = float(scene.get("duration") or 0.0)
            if who and (release < 0 or release >= duration):
                world.carried[name] = who
            elif who:
                world.carried.pop(name, None)
        else:
            world.carried.pop(name, None)
