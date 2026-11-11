"""Text → storyboard: turn a one-line idea into validated ``BlockyScene`` JSON.

The mission is deliberately narrow: an LLM (Ollama, local and free) proposes a
short multi-scene story; this module owns the **contract**.  Every field is
coerced and clamped, unknown characters/props are dropped with a reason, and a
scene that still fails :meth:`BlockyScene.validate` is rejected.  When Ollama is
not installed or returns nonsense, :func:`fallback_storyboard` builds a
deterministic story with the same schema, so the pipeline never depends on the
model being available.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Sequence

from ..llm import LLMError, OllamaClient, extract_json
from ..config import Config, load_config
from .animation_pipeline.skinned_hero import PLAN_ACTION_STATES
from .blocky_scene import (
    AMBIENCE_PRESETS,
    CHARACTERS,
    ENVIRONMENT_PRESETS,
    BlockyScene,
)
from .prompts import SCHEMA_HINT, SYSTEM_PROMPT, build_story_prompt  # noqa: F401

#: Props a storyboard may ask for.  Kept in sync with ``blocky_scene``.
PROP_PRESETS = (
    "chest",
    "door",
    "glowing_cube",
    "grass",
    "key",
    "rock",
    "spring_box",
    "stump",
    "tree",
    "umbrella",
)
SFX_PRESETS = ("sparkle", "boing", "pop")
EMOTIONS = (
    "neutral",
    "happy",
    "surprised",
    "sad",
    "angry",
    "confused",
    "worried",
    "scared",
)
ACTIONS = (
    "idle",
    "walk",
    "talk",
    "look_at",
    "listen",
    "nod",
    "shake_head",
    "shrug",
    "point",
    "grab",
    "hold",
    "release",
    "react",
    "scared",
    "stumble",
    "fall",
    "recover",
    "wave",
)
#: остальные глаголы движка, которых нет в курируемом списке выше — промпт
#: обязан предлагать их наравне (B-06: словарь истории != словарь движка)
EXTRA_ACTIONS = tuple(sorted(set(PLAN_ACTION_STATES) - set(ACTIONS)))

#: B-18: схема параметров каждого действия (whitelist ключей + типы значений).
#: Типы: "vec2" — [dx, dy]; "prop" — имя существующего пропса сцены;
#: "emotion" — одна из EMOTIONS; "num01" — число 0..1; "num" — любое число.
#: Ключей, которых нет в схеме, быть не должно — они отбрасываются с причиной.
ACTION_PARAM_SCHEMAS: dict[str, dict[str, str]] = {
    "idle": {},
    "stand": {},
    "wait": {},
    "return_idle": {},
    "walk": {"target": "vec2"},
    "run": {"target": "vec2"},
    "talk": {"emotion": "emotion", "emotion_intensity": "num01",
             "prop": "prop", "direction": "num"},
    "look_at": {"prop": "prop", "direction": "num"},
    "listen": {"direction": "num"},
    "listen_nod": {"direction": "num"},
    "nod": {},
    "shake_head": {},
    "shrug": {},
    "point": {"prop": "prop", "direction": "num"},
    "grab": {"prop": "prop"},
    "pickup": {"prop": "prop"},
    "hold": {"prop": "prop"},
    "release": {"prop": "prop"},
    "interact": {"prop": "prop"},
    "use": {"prop": "prop"},
    "react": {"emotion": "emotion", "emotion_intensity": "num01"},
    "scared": {"emotion_intensity": "num01"},
    "stumble": {},
    "fall": {},
    "recover": {},
    "wave": {},
    "turn_in_place": {"angle": "num"},
    "reach": {"prop": "prop"},
    "crouch": {},
}

#: SCHEMA_HINT / SYSTEM_PROMPT / build_story_prompt перенесены в ``prompts.py``
#: (этап A3); здесь остаются только константы для валидации/парсинга.


class StoryError(RuntimeError):
    """The idea or the model answer cannot become a valid storyboard."""





# ------------------------------------------------------------------- coercing


def _number(value: Any, default: float, *, low: float | None = None,
            high: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


def _env(value: Any) -> str:
    name = str(value or "").strip().lower()
    return name if name in ENVIRONMENT_PRESETS else "forest"


def _origin(raw: Any) -> list[float]:
    values = raw if isinstance(raw, (list, tuple)) else []
    x = _number(values[0] if len(values) > 0 else 0.0, 0.0, low=-2.4, high=2.4)
    y = _number(values[1] if len(values) > 1 else 0.95, 0.95, low=0.4, high=3.0)
    return [round(x, 3), round(y, 3), 0.0]


def _actions(
    raw: Any,
    duration: float,
    *,
    rejects: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Keep only known verbs, fit them into the scene, never return an empty list.

    B-18: неизвестные действия не удаляются молча — причина попадает в
    ``rejects`` (человекочитаемый reject log), если список передан.
    """
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("action") or "").strip().lower()
        # частые синонимы LLM -> глаголы движка (ремонт без отбрасывания)
        name = {"open": "interact", "take": "grab", "pick": "grab",
                "pickup": "grab", "point_at": "point"}.get(name, name)
        if name not in ACTIONS:
            if rejects is not None:
                rejects.append(f"действие «{name}» не поддерживается — проигнорировано.")
            continue
        start = _number(item.get("start"), 0.0, low=0.0)
        length = _number(item.get("duration"), 1.0, low=1.0 / 30.0)
        if start >= duration - 1e-6:
            continue
        length = min(length, duration - start)
        entry: dict[str, Any] = {
            "action": name,
            "start": round(start, 3),
            "duration": round(length, 3),
        }
        params = item.get("params")
        if isinstance(params, dict) and params:
            entry["params"] = dict(params)
        out.append(entry)
    out.sort(key=lambda act: act["start"])
    if not out:
        out = [{"action": "idle", "start": 0.0, "duration": round(duration, 3)}]
    return out


def _repair_action_params(
    action_name: str,
    params: Any,
    *,
    known_props: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """B-18: whitelist ключей params + проверка типов и существования предмета.

    Возвращает (почищенный dict params, список человекочитаемых проблем).
    Неизвестные ключи удаляются с причиной (а не молча); значения приводятся
    к ожидаемому типу; ссылки на несуществующие предметы снимаются.
    """
    rejects: list[str] = []
    if not isinstance(params, dict):
        return {}, [f"действие «{action_name}»: params не объект — сброшены."]
    schema = ACTION_PARAM_SCHEMAS.get(action_name)
    allowed = set(schema) if schema is not None else set()
    out: dict[str, Any] = {}
    for key, value in list(params.items()):
        if key not in allowed:
            rejects.append(
                f"действие «{action_name}»: неизвестный параметр «{key}» удалён."
            )
            continue
        kind = (schema or {}).get(key, "")
        if kind == "vec2":
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                try:
                    out[key] = [float(value[0]), float(value[1])]
                    continue
                except (TypeError, ValueError):
                    pass
            rejects.append(
                f"действие «{action_name}»: параметр «{key}» должен быть [dx, dy] — удалён."
            )
        elif kind == "prop":
            name = str(value or "").strip()
            if name in known_props:
                out[key] = name
            else:
                rejects.append(
                    f"действие «{action_name}»: предмет «{name}» не существует — "
                    "ссылка удалена."
                )
        elif kind == "emotion":
            name = str(value or "").strip().lower()
            if name in EMOTIONS:
                out[key] = name
            else:
                rejects.append(
                    f"действие «{action_name}»: эмоция «{name}» не поддерживается — удалена."
                )
        elif kind == "num01":
            try:
                out[key] = round(min(1.0, max(0.0, float(value))), 3)
            except (TypeError, ValueError):
                rejects.append(
                    f"действие «{action_name}»: параметр «{key}» не число — удалён."
                )
        elif kind == "num":
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                rejects.append(
                    f"действие «{action_name}»: параметр «{key}» не число — удалён."
                )
        else:
            out[key] = value
    return out, rejects


def _validate_scene_action_params(scene: dict[str, Any]) -> list[str]:
    """B-18: строгая валидация параметров всех действий одной сцены.

    Вызывается ПОСЛЕ ``stage_interactions`` (пропсы уже материализованы и
    авто-привязаны).  Параметры чинятся на месте; возвращается reject log.
    """
    rejects: list[str] = []
    known_props = {
        str(p.get("name") or "") for p in scene.get("props") or []
    }
    for actor in scene.get("actors") or []:
        who = str(actor.get("character") or "")
        for act in actor.get("actions") or []:
            name = str(act.get("action") or "").strip().lower()
            if "params" not in act:
                continue
            repaired, problems = _repair_action_params(
                name, act.get("params"), known_props=known_props
            )
            for problem in problems:
                rejects.append(f"{who}: {problem}")
            if repaired:
                act["params"] = repaired
            else:
                act.pop("params", None)
    return rejects


def _dialogue(raw: Any, actors: list[dict[str, Any]], duration: float,
              lang: str) -> list[dict[str, Any]]:
    known = {str(actor.get("character")) for actor in actors}
    lines: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        who = str(item.get("character") or "").strip().lower()
        if not text or who not in known:
            continue
        start = _number(item.get("start"), 0.5, low=0.0, high=max(0.0, duration - 0.5))
        length = _number(item.get("duration"), max(0.9, len(text) / 12.0), low=0.6)
        entry: dict[str, Any] = {
            "character": who,
            "text": text,
            "start": round(start, 3),
            "duration": round(length, 3),
        }
        emotion = str(item.get("emotion") or "").strip().lower()
        if emotion in EMOTIONS:
            entry["emotion"] = emotion
            entry["emotion_intensity"] = round(
                _number(item.get("emotion_intensity"), 0.8, low=0.0, high=1.0), 3
            )
        lines.append(entry)
    lines.sort(key=lambda line: line["start"])
    return lines


def _props(raw: Any, duration: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    frames = max(1, int(round(float(duration) * 30.0))) if duration else 1
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        preset = str(item.get("preset") or "").strip().lower()
        if preset not in PROP_PRESETS:
            continue
        entry: dict[str, Any] = {
            "preset": preset,
            "x": round(_number(item.get("x"), 0.0, low=-4.0, high=4.0), 3),
            "y": round(_number(item.get("y"), 1.6, low=0.4, high=4.0), 3),
        }
        name = str(item.get("name") or "").strip()
        if name:
            entry["name"] = name[:40]
        if "z" in item:
            entry["z"] = round(_number(item.get("z"), 0.0, low=0.0, high=3.0), 3)
        if "size" in item:
            entry["size"] = round(_number(item.get("size"), 0.3, low=0.05, high=1.5), 3)
        if preset == "door":
            state = str(item.get("state") or "closed").strip().lower()
            if state in ("closed", "open"):
                entry["state"] = state
            open_frame = _number(item.get("open_frame"), None, low=1.0)
            if open_frame is not None:
                entry["open_frame"] = min(int(round(open_frame)), frames)
            end_frame = _number(item.get("end_frame"), None, low=1.0)
            if end_frame is not None:
                entry["end_frame"] = min(int(round(end_frame)), frames)
        carry = item.get("carry")
        if isinstance(carry, dict) and carry.get("character") and carry.get("bone"):
            grip = carry.get("grip") if isinstance(carry.get("grip"), list) else []
            release = _number(carry.get("release"), -1.0)
            entry["carry"] = {
                "character": str(carry["character"]).strip().lower(),
                "bone": str(carry["bone"]).strip(),
                "start": round(_number(carry.get("start"), 0.0, low=0.0), 3),
                "release": round(release, 3),
                "approach": round(_number(carry.get("approach"), 0.2, low=0.0), 3),
                "grip": [round(_number(v, 0.0), 3) for v in grip[:3]],
                "ground": round(_number(carry.get("ground"), 0.0), 3),
            }
        out.append(entry)
    return out


def _camera_cuts(raw: Any) -> list[dict[str, Any]]:
    """Preserve authored camera plans; the model normally leaves this empty."""
    cuts: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        center = item.get("center")
        if not isinstance(center, (list, tuple)) or len(center) < 3:
            continue
        cut: dict[str, Any] = {
            "frame": int(_number(item.get("frame"), 1.0, low=1.0)),
            "center": [round(_number(v, 0.0), 3) for v in center[:3]],
            "height": round(_number(item.get("height"), 2.0, low=0.2, high=12.0), 3),
        }
        move = item.get("move")
        if isinstance(move, dict) and move.get("to_frame"):
            target = move.get("center") if isinstance(move.get("center"), (list, tuple)) else center
            cut["move"] = {
                "to_frame": int(_number(move.get("to_frame"), 1.0, low=1.0)),
                "center": [round(_number(v, 0.0), 3) for v in target[:3]],
                "height": round(_number(move.get("height"), cut["height"], low=0.2, high=12.0), 3),
            }
        cuts.append(cut)
    cuts.sort(key=lambda cut: cut["frame"])
    return cuts



def _sfx(raw: Any, duration: float) -> list[list[Any]]:
    out: list[list[Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        name = str(item[1]).strip().lower()
        if name not in SFX_PRESETS:
            continue
        when = _number(item[0], 0.0, low=0.0, high=max(0.0, duration - 0.05))
        out.append([round(when, 3), name])
    out.sort(key=lambda pair: pair[0])
    return out


#: пресеты, с которыми осмысленно взаимодействовать руками/взглядом
INTERACTIVE_PRESETS = {"glowing_cube", "key", "chest", "door"}
GRABBABLE_PRESETS = {"glowing_cube", "key", "chest"}
HAND_TARGET_VERBS = {"grab", "pickup", "interact", "use", "look_at", "point"}
CARRY_VERBS = {"grab", "pickup"}
#: дистанция, ближе которой предмет «в зоне досягаемости» руки
REACH_DISTANCE = 0.9


def _add_approach_walk(
    actions: list[dict[str, Any]],
    act: dict[str, Any],
    tx: float,
    ty: float,
    dist: float,
    scene_duration: float,
) -> float | bool:
    """Вставляет перед ``act`` действие walk с локальной целью (tx, ty).

    F2.5: персонаж подходит к предмету, а не предмет к персонажу.
    Возвращает новую рекомендуемую длительность сцены (float), если walk
    вставлен (возможно, со сдвигом последующих действий), либо False.
    """
    try:
        idx = actions.index(act)
    except ValueError:
        return False
    # порядок по времени (на всякий случай)
    ordering = sorted(range(len(actions)), key=lambda i: (float(actions[i].get("start") or 0.0), i))
    prev_end = 0.0
    for i in ordering[:idx]:
        prev_end = max(prev_end, float(actions[i].get("start") or 0.0) + float(actions[i].get("duration") or 0.0))
    start0 = float(act.get("start") or 0.0)
    needed = min(dist / 1.0 + 0.35, 6.0)
    gap = start0 - prev_end
    if gap >= needed:
        walk_start = round(start0 - needed, 3)
        actions.insert(idx, {"action": "walk", "start": walk_start,
                             "duration": round(needed, 3),
                             "params": {"target": [round(tx, 3), round(ty, 3)]}})
    else:
        shift = round(needed - gap + 0.05, 3)
        walk_start = round(prev_end + 0.05, 3)
        actions.insert(idx, {"action": "walk", "start": walk_start,
                             "duration": round(needed, 3),
                             "params": {"target": [round(tx, 3), round(ty, 3)]}})
        for i in range(idx + 1, len(actions)):
            actions[i]["start"] = round(float(actions[i]["start"]) + shift, 3)
    max_end = max(
        (float(a.get("start") or 0.0) + float(a.get("duration") or 0.0) for a in actions),
        default=0.0,
    )
    return max(max_end, scene_duration, 0.0)


def stage_interactions(scene: dict[str, Any]) -> list[str]:
    """Автопостановка взаимодействий одной сцены (F2, минимальная версия).

    Делает то, что автор делал бы руками в JSON:

    * безымянным пропсам даёт детерминированные имена;
    * привязывает действия рук (grab/pickup/interact/use/look_at/point) к
      интерактивному предмету сцены, если модель не задала ``params.prop``;
    * придвигает предмет в зону досягаемости актёра, иначе взаимодействие
      нечитаемо (дефект run_new: grab в 2.5 м от невидимого куба);
    * grab/pickup автоматически добавляет ``carry`` — предмет уходит в кисть;
    * interact/use/grab на двери ставит событие открытия (``open_frame``),
      если его ещё нет.

    Возвращает список человекочитаемых adjustments — они попадают в отчёт,
    чтобы постановка не была невидимой магией.
    """
    adjustments: list[str] = []
    actors = scene.get("actors") or []
    if not actors:
        return adjustments
    # props может быть пустым: LLM часто ссылается на предмет в params,
    # забыв добавить его в props — материализуем ниже (ремонт)
    props = scene.get("props") or []
    scene["props"] = props
    fps = max(1, int(scene.get("fps") or 30))

    for index, prop in enumerate(props, 1):
        if not str(prop.get("name") or "").strip():
            prop["name"] = f"{prop.get('preset') or 'prop'}#{index}"
    by_name = {str(p["name"]): p for p in props}
    interactive = [p for p in props if str(p.get("preset") or "") in INTERACTIVE_PRESETS]
    door = next((p for p in props if str(p.get("preset") or "") == "door"), None)
    door_has_event = bool(
        door and (door.get("open_frame") or str(door.get("state") or "") == "open")
    )

    for actor in actors:
        who = str(actor.get("character") or "")
        origin = actor.get("origin") or [0.0, 0.95, 0.0]
        try:
            ox, oy = float(origin[0]), float(origin[1])
        except (TypeError, ValueError, IndexError):
            continue
        for act in list(actor.get("actions") or []):
            verb = str(act.get("action") or "")
            if verb not in HAND_TARGET_VERBS:
                continue
            params = act.setdefault("params", {})
            ref = str(params.get("prop") or params.get("preset") or "").strip()
            if ref and ref not in by_name:
                if ref.lower() in PROP_PRESETS:
                    existing = [
                        p for p in props
                        if str(p.get("preset") or "").lower() == ref.lower()
                    ]
                    if existing:
                        # пресет уже есть в сцене — привязываемся к нему,
                        # а не плодим дубликат
                        new_name = str(existing[0]["name"])
                        params["prop"] = new_name
                        ref = new_name
                        adjustments.append(
                            f"{who}/{verb}: предмет «{ref}» привязан к "
                            "существующему пресету."
                        )
                    else:
                        # LLM ссылается на пресет («chest»), забыв добавить его в
                        # props — материализуем предмет в сцене (F2.5: герой
                        # подойдёт к нему сам, а не предмет к герою)
                        new_prop = {"preset": ref.lower(), "x": 0.0,
                                    "y": -1.5, "name": ref}
                        props.append(new_prop)
                        by_name[ref] = new_prop
                        interactive.append(new_prop)
                        adjustments.append(
                            f"{who}/{verb}: предмет «{ref}» отсутствовал в props — "
                            "материализован рядом с актёром."
                        )
                else:
                    adjustments.append(
                        f"{who}/{verb}: предмет «{ref}» отсутствует в сцене — ссылка снята."
                    )
                    params.pop("prop", None)
                    ref = ""
            if not ref:
                candidates = [
                    p for p in interactive
                    if p.get("preset") not in GRABBABLE_PRESETS or verb in CARRY_VERBS
                ]
                if verb in CARRY_VERBS:
                    grabbable = [
                        p for p in interactive
                        if p.get("preset") in GRABBABLE_PRESETS
                    ]
                    if grabbable:
                        candidates = grabbable
                if not candidates:
                    continue
                if len(candidates) == 1:
                    ref = str(candidates[0]["name"])
                else:
                    # несколько кандидатов — берём ближайший к актёру
                    try:
                        ref = str(min(
                            candidates,
                            key=lambda c: math.hypot(
                                float(c.get("x", 0.0)) - ox,
                                float(c.get("y", 0.0)) - oy,
                            ),
                        )["name"])
                    except (TypeError, ValueError):
                        ref = str(candidates[0]["name"])
                params["prop"] = ref
                adjustments.append(f"{who}/{verb}: авто-привязка к предмету «{ref}».")
            prop = by_name.get(ref)
            if prop is None:
                continue

            is_door = prop is door
            try:
                px, py = float(prop.get("x", 0.0)), float(prop.get("y", 0.0))
                dist = math.hypot(px - ox, py - oy)
            except (TypeError, ValueError):
                px = py = dist = 0.0
            if not is_door and dist > REACH_DISTANCE:
                scene_duration = float(scene.get("duration") or 8.0)
                actor_actions = actor.get("actions")
                new_dur = (
                    _add_approach_walk(
                        actor_actions, act, px - ox, py - oy, dist, scene_duration
                    )
                    if actor_actions is not None
                    else False
                )
                if new_dur is not False:
                    adjustments.append(
                        f"{who}/{verb}: {who} подходит к «{ref}» "
                        f"({dist:.2f} м → в зоне досягаемости)."
                    )
                    if float(new_dur) > scene_duration:
                        scene["duration"] = round(float(new_dur), 3)
                else:
                    adjustments.append(
                        f"{who}/{verb}: нет окна для подхода к «{ref}» — "
                        "предмет останется на месте."
                    )

            if verb in CARRY_VERBS and not prop.get("carry"):
                start = float(act.get("start") or 0.0)
                prop["carry"] = {
                    "character": who,
                    "bone": "hand_R",
                    "start": round(start, 3),
                    "release": -1.0,
                    "approach": 0.2,
                    "grip": [0.0, 0.0, 0.16],
                    "ground": 0.0,
                }
                adjustments.append(
                    f"Предмет «{ref}» передан в руку «{who}» (carry, с {start:.2f} с)."
                )

            if is_door and not door_has_event and verb in {"interact", "use", "grab"}:
                start = float(act.get("start") or 0.0)
                door["open_frame"] = int(round((start + 0.35) * fps))
                adjustments.append(
                    f"Дверь «{door['name']}» откроется на {start + 0.35:.2f} с "
                    f"(действие {who}/{verb})."
                )
                door_has_event = True
    _plan_handoffs(scene, adjustments)
    return adjustments


def _plan_handoffs(scene: dict[str, Any], adjustments: list[str]) -> list[str]:
    """F4: автоматическая постановка физической передачи предмета.

    Если один персонаж держит предмет (hold), а другой в той же сцене позже
    берёт его (grab/pickup) — выстраиваем рукопожатие: B подходит к A
    (если далеко), а предмет переходит из руки A в руку B только в момент,
    когда руки встретились (контакт, см. ``transfer.gap`` в ``carry_track``).
    Никакой мгновенной смены владельца.  Передающим считается тот, кто начал
    взаимодействовать с предметом раньше всех; carry при необходимости
    переносится на него.
    """
    actors = scene.get("actors") or []
    props = scene.get("props") or []
    by_character = {str(a.get("character") or ""): a for a in actors}
    origins: dict[str, tuple[float, float]] = {}
    for a in actors:
        o = a.get("origin") or [0.0, 0.95, 0.0]
        try:
            origins[str(a.get("character") or "")] = (float(o[0]), float(o[1]))
        except (TypeError, ValueError, IndexError):
            pass

    HAND_VERBS = {"grab", "pickup", "hold", "reach", "interact", "use"}

    for prop in props:
        spec = prop.get("carry")
        if not spec:
            continue
        name = str(prop.get("name") or "")
        # кто и когда взаимодействует с предметом руками
        interactions: list[tuple[str, str, float, dict[str, Any]]] = []
        for actor in actors:
            who = str(actor.get("character") or "")
            if not who:
                continue
            for act in list(actor.get("actions") or []):
                verb = str(act.get("action") or "")
                if verb not in HAND_VERBS:
                    continue
                params = act.get("params") or {}
                if str(params.get("prop") or "") != name:
                    continue
                interactions.append(
                    (who, verb, float(act.get("start") or 0.0), act)
                )
        if len({w for w, _, _, _ in interactions}) < 2:
            continue
        interactions.sort(key=lambda it: it[2])
        giver, _, start_give, _ = interactions[0]
        taker_entry = next(
            (it for it in interactions[1:]
             if it[1] in CARRY_VERBS and it[2] > start_give + 0.1),
            None,
        )
        if taker_entry is None:
            continue
        who, _, t_take, taker_act = taker_entry
        if spec.get("transfer"):
            continue
        # передающий держит предмет до момента передачи
        spec["character"] = giver
        spec["bone"] = "hand_R"
        spec["start"] = round(float(start_give), 3)
        spec["release"] = max(float(spec.get("release") or -1.0), t_take)
        if giver in origins and who in origins:
            gx, gy = origins[giver]
            wx, wy = origins[who]
            dist = math.hypot(wx - gx, wy - gy)
            actor_actions = by_character[who].get("actions") or []
            # F4: LLM часто штампует несколько одинаковых walk, уводящих
            # персонажа прочь (суммарный сдвиг огромный).  Убираем прежние
            # подходы и планируем один контролируемый.
            kept = []
            removed_walks = False
            for a in actor_actions:
                if (
                    str(a.get("action") or "") == "walk"
                    and float(a.get("start") or 0.0) <= t_take
                ):
                    removed_walks = True
                else:
                    kept.append(a)
            if removed_walks:
                actor_actions[:] = kept
            if dist > 1.8:
                # остановиться на дистанции вытянутой руки от передающего,
                # не проходя сквозь него (зазор тел — залог чистого силуэта)
                tdx = gx - wx
                tdy = gy - wy
                if abs(tdx) > 1e-3:
                    tdx -= 1.35 * (1.0 if tdx > 0 else -1.0)
                if abs(tdy) > 1e-3:
                    tdy -= 1.35 * (1.0 if tdy > 0 else -1.0)
                scene_duration = float(scene.get("duration") or 8.0)
                new_dur = _add_approach_walk(
                    actor_actions, taker_act, tdx, tdy,
                    dist, scene_duration,
                )
                if new_dur is not False and float(new_dur) > scene_duration:
                    scene["duration"] = round(float(new_dur), 3)
                adjustments.append(
                    f"{who}: подходит к «{giver}» для передачи «{name}» "
                    f"({dist:.2f} м)."
                )
                t_take = float(taker_act.get("start") or 0.0)
        spec["transfer"] = {
            "character": who,
            "bone": "hand_L",
            "start": round(max(start_give, t_take - 0.35), 3),
            "duration": 0.3,
            "gap": 0.7,
        }
        adjustments.append(
            f"Предмет «{name}» будет передан от «{giver}» к «{who}» "
            f"(рукопожатие, с {max(start_give, t_take - 0.35):.2f} с)."
        )
    return adjustments


def ensure_door_event(scenes: list[dict[str, Any]], idea: str) -> list[str]:
    """Идея обещает «открыть дверь» — в раскадровке обязано быть событие.

    Ищет сцену с дверью и ручным действием; ставит на нём open_frame.
    Если ручного действия нет — добавляет interact в последнюю сцену с дверью.
    """
    log: list[str] = []
    text = str(idea or "").lower()
    if "двер" not in text or not any(w in text for w in ("откр", "откры", "unlock")):
        return log
    for scene in scenes:
        for prop in scene.get("props") or []:
            if prop.get("preset") == "door" and (
                prop.get("open_frame") or str(prop.get("state") or "") == "open"
            ):
                return log  # событие уже спланировано
    best = None
    for index, scene in enumerate(scenes):
        door = next(
            (p for p in scene.get("props") or [] if p.get("preset") == "door"), None
        )
        if door is None:
            continue
        for actor in scene.get("actors") or []:
            for act in actor.get("actions") or []:
                if str(act.get("action") or "") in {"interact", "use", "grab", "hold"}:
                    best = (index, scene, act, door)
    if best is None:
        for scene in reversed(scenes):
            door = next(
                (p for p in scene.get("props") or [] if p.get("preset") == "door"), None
            )
            if door is None:
                continue
            duration = float(scene.get("duration") or 6.0)
            start = round(duration * 0.45, 3)
            actor = (scene.get("actors") or [{}])[0]
            actor.setdefault("actions", []).append(
                {"action": "interact", "start": start, "duration": 0.9,
                 "params": {"prop": str(door.get("name") or "Door")}}
            )
            fps = max(1, int(scene.get("fps") or 30))
            door["open_frame"] = int(round((start + 0.35) * fps))
            log.append(
                f"Сцена «{scene.get('title')}»: добавлен interact и открытие двери "
                "(идея обещает открытие, действий не было)."
            )
            return log
        return log
    index, scene, act, door = best
    fps = max(1, int(scene.get("fps") or 30))
    start = float(act.get("start") or 0.0)
    door["open_frame"] = int(round((start + 0.35) * fps))
    log.append(
        f"Дверь откроется в сцене {index + 1} на действии «{act.get('action')}» "
        "(идея обещает открытие двери)."
    )
    return log


def normalize_scene(raw: dict[str, Any], *, index: int = 1) -> dict[str, Any]:
    """Coerce one scene dict into the strict shape ``BlockyScene`` accepts."""
    if not isinstance(raw, dict):
        raise StoryError(f"Сцена #{index}: ожидался объект, получено {type(raw).__name__}.")
    duration = _number(raw.get("duration"), 6.0, low=2.0, high=30.0)
    actors: list[dict[str, Any]] = []
    rejects: list[str] = []
    for item in raw.get("actors") if isinstance(raw.get("actors"), list) else []:
        if not isinstance(item, dict):
            continue
        who = str(item.get("character") or "").strip().lower()
        if who not in CHARACTERS:
            continue
        actors.append(
            {
                "character": who,
                "origin": _origin(item.get("origin")),
                "actions": _actions(item.get("actions"), duration, rejects=rejects),
            }
        )
    if not actors:
        actors = [
            {
                "character": "steve",
                "origin": [-0.6, 0.95, 0.0],
                "actions": [{"action": "idle", "start": 0.0, "duration": round(duration, 3)}],
            }
        ]
    dialogue = _dialogue(raw.get("dialogue"), actors, duration, str(raw.get("lang") or "ru"))
    # реплика ссылается на главного героя, которого LLM забыл в actors ->
    # добавляем актёра с idle (ремонт), иначе теряется половина истории
    for line in dialogue:
        who = str(line.get("character") or "")
        if who in CHARACTERS and who not in {a["character"] for a in actors}:
            actors.append(
                {
                    "character": who,
                    "origin": [round(0.6 + 0.7 * len(actors), 3), 0.95, 0.0],
                    "actions": [
                        {"action": "idle", "start": 0.0, "duration": round(duration, 3)}
                    ],
                }
            )
    # a spoken line must fit before the outro; the renderer may extend the scene
    # later when the real take is longer than the authored slot
    for line in dialogue:
        duration = max(duration, line["start"] + line["duration"] + 0.4)
    environment = _env(raw.get("environment"))
    ambience = str(raw.get("ambience") or "").strip().lower()
    if ambience not in AMBIENCE_PRESETS:
        ambience = environment if environment in AMBIENCE_PRESETS else ""
    scene: dict[str, Any] = {
        "title": str(raw.get("title") or f"Сцена {index}").strip(),
        "duration": round(min(30.0, duration), 3),
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "samples": 32,
        "environment": environment,
        "ambience": ambience,
        "lang": "ru",
        "auto_act": bool(raw.get("auto_act", True)),
        "auto_camera": bool(raw.get("auto_camera", True)),
        "actors": actors,
        "dialogue": dialogue,
        "props": _props(raw.get("props"), duration),
        "sfx_events": _sfx(raw.get("sfx_events"), duration),
    }
    cuts = _camera_cuts(raw.get("camera_cuts"))
    if cuts:
        scene["camera_cuts"] = cuts
    adjustments = stage_interactions(scene)
    # B-18: строгая валидация параметров (whitelist + схемы) — после авто-постановки
    adjustments += _validate_scene_action_params(scene)
    # B-18: reject log отброшенных действий/параметров модели — в отчёт
    if rejects:
        adjustments.append("B-18 reject log: " + "; ".join(rejects))
    scene["adjustments"] = adjustments
    return scene


def parse_storyboard(
    data: Any,
    *,
    lang: str = "ru",
    idea: str = "",
) -> dict[str, Any]:
    """Validate and normalize a model answer into a renderable storyboard.

    Accepts either ``{"title", "scenes": [...]}`` or a single scene dict.
    Raises :class:`StoryError` with a readable reason when nothing usable is
    left (never returns a silently empty film).
    """
    if not isinstance(data, dict):
        raise StoryError("Ответ модели: ожидался JSON-объект.")
    raw_scenes = data.get("scenes")
    if raw_scenes is None:
        raw_scenes = [data]
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise StoryError("В раскадровке нет ни одной сцены.")
    lang = str(data.get("lang") or lang).strip().lower() or "ru"
    scenes = [normalize_scene(item, index=i) for i, item in enumerate(raw_scenes, 1)]
    for scene in scenes:
        scene["lang"] = lang
    title = str(data.get("title") or scenes[0]["title"] or "Без названия").strip()
    storyboard = {"title": title, "lang": lang, "scenes": scenes}
    storyboard["adjustments"] = ensure_door_event(scenes, idea)
    problems = check_storyboard(storyboard)
    if problems:
        raise StoryError("Раскадровка не проходит проверку: " + "; ".join(problems))
    # Из идеи (LLM-путь) фильм обязан быть говорливым: история без реплик —
    # дефект генерации, а не осознанное авторское решение (B-17 допускает
    # немые сцены, но не немой фильм целиком). Авторские раскадровки
    # (--storyboard, idea="") не ограничиваются.
    if str(idea or "").strip() and not any(
        scene.get("dialogue") for scene in scenes
    ):
        raise StoryError(
            "Модель не создала ни одной реплики — раскадровка немая."
        )
    return storyboard


def check_storyboard(storyboard: dict[str, Any]) -> list[str]:
    """Return every schema/engine problem instead of raising on the first one."""
    problems: list[str] = []
    scenes = storyboard.get("scenes") if isinstance(storyboard, dict) else None
    if not isinstance(scenes, list) or not scenes:
        return ["В раскадровке нет ни одной сцены."]
    for index, raw in enumerate(scenes, 1):
        try:
            scene = BlockyScene.from_dict(raw)
            scene.validate()
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"Сцена #{index}: {exc}")
            continue
        # B-17: немые сцены допустимы (комедия действия) — не считаем дефектом
    return problems


# -------------------------------------------------------------------- fallback


def fallback_storyboard(
    idea: str,
    *,
    scenes: int = 3,
    lang: str = "ru",
) -> dict[str, Any]:
    """Deterministic story that needs no model and always validates."""
    idea = str(idea or "").strip() or "друзья находят странный предмет"
    count = max(1, min(4, int(scenes)))
    setups = [
        (
            "Находка",
            "forest",
            "forest",
            "steve",
            "alex",
            [
                ("steve", "Смотри, что я нашёл под деревом.", "surprised", 0.8),
                ("alex", "Оно же светится. Не трогай.", "worried", 1.0),
            ],
            [("tree", -1.8, 2.3), ("glowing_cube", -0.1, 1.5)],
            "sparkle",
        ),
        (
            "Спор",
            "cave",
            "cave",
            "alex",
            "steve",
            [
                ("alex", "Я же говорил: оно живое.", "confused", 0.9),
                ("steve", "Тогда надо вернуть его обратно.", "sad", 0.7),
            ],
            [("rock", -1.5, 1.9), ("rock", 1.5, 2.1)],
            "boing",
        ),
        (
            "Дом",
            "meadow",
            "meadow",
            "steve",
            "alex",
            [
                ("steve", "Мы сделали это. Смотри, как тихо.", "happy", 0.8),
                ("alex", "Тише... Кажется, оно снова гудит.", "scared", 0.9),
            ],
            [("grass", -1.2, 0.9), ("stump", 0.9, 1.7)],
            "pop",
        ),
        (
            "Финал",
            "forest",
            "forest",
            "alex",
            "steve",
            [
                ("alex", "Больше никаких находок. Договорились?", "worried", 0.7),
                ("steve", "Договорились.", "happy", 0.6),
            ],
            [("tree", 1.7, 2.2), ("grass", -0.9, 0.8)],
            "sparkle",
        ),
    ]
    scenes_out: list[dict[str, Any]] = []
    # A-audit: шаблон был всегда одним и тем же — при недоступной Ollama все
    # фильмы выглядели одинаково.  Детерминированная вариация окружений и SFX
    # по хэшу идеи (никакого random: повторный запуск даёт тот же фильм).
    digest = hashlib.sha256(idea.encode("utf-8")).digest()
    env_pools: tuple[tuple[str, ...], ...] = (
        ("forest", "meadow"),
        ("cave",),
        ("meadow", "forest"),
        ("forest",),
    )
    sfx_pool = ("sparkle", "boing", "pop")
    varied: list[tuple[str, str, str, str, str, list, list, str]] = []
    for beat, setup in enumerate(setups[:count]):
        title, _env, _amb, first, second, lines, props, _sfx = setup
        pool = env_pools[beat % len(env_pools)]
        environment = pool[digest[beat] % len(pool)]
        sfx = sfx_pool[digest[beat + 4] % len(sfx_pool)]
        varied.append((title, environment, environment, first, second, lines, props, sfx))
    for title, environment, ambience, first, second, lines, props, sfx in varied:
        duration = round(max(5.0, 2.0 + 1.9 * len(lines) + 1.6), 2)
        start = 0.7
        dialogue = []
        for who, text, emotion, intensity in lines:
            dialogue.append(
                {
                    "character": who,
                    "text": text,
                    "start": round(start, 3),
                    "duration": round(max(1.2, len(text) / 11.0), 3),
                    "emotion": emotion,
                    "emotion_intensity": intensity,
                }
            )
            start += round(max(1.2, len(text) / 11.0) + 0.35, 3)
        duration = max(duration, round(start + 0.6, 3))
        scene = {
            "title": title,
            "duration": duration,
            "environment": environment,
            "ambience": ambience,
            "actors": [
                {
                    "character": first,
                    "origin": [-0.6, 0.95, 0.0],
                    "actions": [
                        {"action": "idle", "start": 0.0, "duration": duration}
                    ],
                },
                {
                    "character": second,
                    "origin": [0.6, 0.95, 0.0],
                    "actions": [
                        {"action": "idle", "start": 0.0, "duration": duration}
                    ],
                },
            ],
            "dialogue": dialogue,
            "props": [
                {"preset": preset, "x": x, "y": y} for preset, x, y in props
            ],
            "sfx_events": [[round(dialogue[0]["start"], 3), sfx]],
        }
        scenes_out.append(normalize_scene(scene, index=len(scenes_out) + 1))
    storyboard = {
        "title": idea[:80],
        "idea": idea,
        "lang": lang,
        "scenes": scenes_out,
    }
    problems = check_storyboard(storyboard)
    if problems:  # pragma: no cover - the template is authored to pass
        raise StoryError("Встроенный сценарий некорректен: " + "; ".join(problems))
    return storyboard


# ----------------------------------------------------------------- generation


def _story_num_predict(config: Any) -> int:
    """Token limit for the storyboard answer; default when config is silent."""
    get = getattr(config, "get", None)
    if callable(get):
        try:
            return max(1200, int(get("llm", "story_num_predict", 3000)))
        except Exception:  # pragma: no cover - defensive: odd config objects
            return 3000
    return 3000


def generate_storyboard(
    idea: str,
    *,
    config: Config | None = None,
    scenes: int = 3,
    seconds: float | None = None,
    lang: str = "ru",
    attempts: int = 2,
    model: str | None = None,
    on_token: Callable[[str], None] | None = None,
    stream: bool = False,
) -> dict[str, Any]:
    """Idea → storyboard.  Ollama first, deterministic fallback second.

    A failed attempt (network, empty answer, invalid JSON, engine rejection) is
    retried with the same prompt; after ``attempts`` the fallback is used.  The
    caller can tell them apart by ``storyboard["source"]``.

    When ``stream`` is true and Ollama is available, tokens are delivered to
    ``on_token`` (a callable receiving plain-text chunks) as they are
    generated — useful for live previews in a GUI.
    """
    idea = str(idea or "").strip()
    if not idea:
        raise StoryError("Идея пуста: нечего экранизировать.")
    cfg = config if config is not None else load_config()
    prompt = build_story_prompt(idea, scenes=scenes, seconds=seconds, lang=lang)
    try:
        client = OllamaClient(cfg)
    except Exception:  # pragma: no cover - construction never raises today
        client = None
    if client is not None:
        base_prompt = prompt
        failures: list[str] = []
        for attempt_index in range(max(1, int(attempts))):
            if failures:
                # повтор с обратной связью: маленькая локальная модель без
                # объяснения повторяет ту же ошибку (A-audit: retry был вслепую)
                prompt = (
                    f"{base_prompt}\n\nПредыдущая попытка отклонена проверкой: "
                    f"{failures[-1]}\nИсправь эту проблему и верни валидный JSON "
                    "строго по схеме из примера."
                )
            try:
                if stream:
                    parts: list[str] = []
                    for chunk in client.generate_stream(
                        prompt,
                        system=SYSTEM_PROMPT,
                        temperature=0.7,
                        model=model,
                        num_predict=_story_num_predict(cfg),
                    ):
                        if on_token is not None:
                            on_token(chunk)
                        parts.append(chunk)
                    raw = extract_json("".join(parts))
                else:
                    raw = client.generate_json(
                        prompt,
                        system=SYSTEM_PROMPT,
                        temperature=0.7,
                        model=model,
                        num_predict=_story_num_predict(cfg),
                    )
            except LLMError as exc:
                failures.append(str(exc))
                continue
            except Exception as exc:  # pragma: no cover - defensive: any network error
                failures.append(str(exc))
                continue
            try:
                storyboard = parse_storyboard(raw, lang=lang, idea=idea)
                storyboard["idea"] = idea
            except StoryError as exc:
                failures.append(str(exc))
                continue
            storyboard["source"] = "ollama"
            storyboard["idea"] = idea
            if attempt_index:
                storyboard["retries"] = attempt_index
            return storyboard
    storyboard = fallback_storyboard(idea, scenes=scenes, lang=lang)
    storyboard["source"] = "fallback"
    storyboard["idea"] = idea
    if failures:
        # прозрачность: почему фильм собран из шаблона, а не из модели
        storyboard["attempts_failed"] = failures
    return storyboard


# ----------------------------------------------------------------------- files


def write_storyboard(storyboard: dict[str, Any], path: str | Path) -> Path:
    """Write the storyboard as UTF-8 JSON and return the path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(storyboard, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_storyboard(path: str | Path, *, idea: str = "") -> dict[str, Any]:
    """Read and re-validate a storyboard from disk."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Раскадровка не найдена: {source}")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StoryError(f"Раскадровка «{source}» — некорректный JSON: {exc}") from exc
    return parse_storyboard(data, idea=idea or str(data.get('idea') or ''))


def scene_files(storyboard: dict[str, Any], directory: str | Path) -> list[Path]:
    """Write every scene as its own JSON (the unit the renderer consumes)."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, scene in enumerate(storyboard.get("scenes") or [], 1):
        paths.append(write_storyboard(scene, out / f"scene_{index:02d}.json"))
    return paths
