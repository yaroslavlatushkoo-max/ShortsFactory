"""Пре-рендер QC через AnimationLab (этап E3/интеграция).

Использует ``animation_lab`` (D:\\ShortsFactory\\Animation_Intelligence_Lab) как внешний
аналитический слой: планировщик движения, симулятор и проверки
коллизий/досягаемости.  Импорт ленивый — тяжёлые зависимости не
тянутся при обычном импорте ShortsFactory.

Основной вход — словарь-описание сцены (см. ``build_scene_description``),
который валидируется через ``SceneDescription`` AnimationLab.  Выход —
отчёт вида::

    {
      "status": "pass" | "warn" | "fail",
      "issues": [...],
      "frames": int,
      "event_count": int,
      "plan_validation": "pass" | ...,
      "plan_notes": [...],
    }
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

#: Кандидаты каталога с пакетом animation_lab.
_ANIMLAB_SRC_CANDIDATES = (
    Path("D:/ShortsFactory/Animation_Intelligence_Lab/src"),
    Path(__file__).resolve().parents[3] / "Animation_Intelligence_Lab" / "src",
)


class QCError(RuntimeError):
    """AnimationLab недоступен или описание сцены отвергнуто."""


def _ensure_animlab():
    """Возвращает модуль animation_lab (ленивый импорт).

    Сначала пробуем обычный импорт; при неудаче — добавляем в sys.path
    каталог-кандидат с пакетом.
    """
    try:
        import animation_lab  # noqa: F401
        return sys.modules["animation_lab"]
    except ImportError:
        pass
    for candidate in _ANIMLAB_SRC_CANDIDATES:
        if (candidate / "animation_lab").is_dir():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            try:
                import animation_lab  # noqa: F401
                return sys.modules["animation_lab"]
            except ImportError:
                continue
    raise QCError(
        "Пакет animation_lab не найден. Укажите путь к "
        "Animation_Intelligence_Lab/src или установите его.")


# ---------------------------------------------------------------------------
# Построение описания сцены
# ---------------------------------------------------------------------------


def build_scene_description(
    *,
    name: str = "scene",
    fps: int = 30,
    duration_seconds: float,
    characters: Mapping[str, Mapping[str, Any]],
    props: Mapping[str, Mapping[str, Any]] | None = None,
    segments: Sequence[Mapping[str, Any]] | None = None,
    interactions: Sequence[Mapping[str, Any]] | None = None,
):
    """Собирает валидированное ``SceneDescription`` AnimationLab.

    ``characters``: ``{cid: {"position": [x, y], "rotation": 0.0,
    "scale": 1.0, "emotion": "neutral"}}``.

    ``props``: ``{pid: {"position": [x, y], "owner_id": None,
    "state": "free", "attachment_point": None}}``.

    ``segments``: последовательность ``{id, character_id, action_type,
    start_time, end_time, start, end}``, где ``start``/``end`` — такие же
    словари, как элементы ``characters``.

    ``interactions``: ``{id, kind, time, actor_id, prop_id,
    receiver_id?, attachment_point?}``.
    """
    _ensure_animlab()
    models = importlib.import_module("animation_lab.models")

    char_states: dict[str, Any] = {}
    for cid, raw in characters.items():
        x, y = raw.get("position", [0.0, 0.0])
        char_states[cid] = models.CharacterState(
            character_id=str(cid),
            position=models.Vec2Field(x=float(x), y=float(y)),
            rotation=float(raw.get("rotation", 0.0)),
            scale=float(raw.get("scale", 1.0)),
            emotion=str(raw.get("emotion", "neutral")),
            current_action=raw.get("action"),
        )

    prop_states: dict[str, Any] = {}
    for pid, raw in (props or {}).items():
        x, y = raw.get("position", [0.0, 0.0])
        prop_states[pid] = models.PropState(
            prop_id=str(pid),
            position=models.Vec2Field(x=float(x), y=float(y)),
            owner_id=raw.get("owner_id"),
            attachment_point=raw.get("attachment_point"),
            state=str(raw.get("state", "free")),
        )

    seg_objs = []
    for seg in segments or ():
        start_raw = seg["start"]
        end_raw = seg["end"]
        start_state = models.CharacterState(
            character_id=str(seg["character_id"]),
            position=models.Vec2Field(x=float(start_raw.get("position", [0, 0])[0]),
                                     y=float(start_raw.get("position", [0, 0])[1])),
            rotation=float(start_raw.get("rotation", 0.0)),
            scale=float(start_raw.get("scale", 1.0)),
            emotion=str(start_raw.get("emotion", "neutral")),
        )
        end_state = models.CharacterState(
            character_id=str(seg["character_id"]),
            position=models.Vec2Field(x=float(end_raw.get("position", [0, 0])[0]),
                                     y=float(end_raw.get("position", [0, 0])[1])),
            rotation=float(end_raw.get("rotation", 0.0)),
            scale=float(end_raw.get("scale", 1.0)),
            emotion=str(end_raw.get("emotion", "neutral")),
        )
        seg_objs.append(models.MotionSegment(
            id=str(seg["id"]),
            character_id=str(seg["character_id"]),
            action_type=str(seg["action_type"]),
            start_time=float(seg["start_time"]),
            end_time=float(seg["end_time"]),
            start_state=start_state,
            end_state=end_state,
        ))

    inter_objs = []
    for inter in interactions or ():
        inter_objs.append(models.Interaction(
            id=str(inter["id"]),
            kind=str(inter["kind"]),
            time=float(inter["time"]),
            actor_id=str(inter["actor_id"]),
            prop_id=str(inter["prop_id"]),
            receiver_id=inter.get("receiver_id"),
            attachment_point=inter.get("attachment_point"),
        ))

    return models.SceneDescription(
        name=name,
        fps=int(fps),
        duration_seconds=float(duration_seconds),
        characters=char_states,
        props=prop_states,
        motion_segments=tuple(seg_objs),
        interactions=tuple(inter_objs),
    )


# ---------------------------------------------------------------------------
# QC
# ---------------------------------------------------------------------------


def run_pre_render_qc(description, *, fps: int | None = None,
                      reach: float = 0.18,
                      min_gap: float = 0.05) -> dict[str, Any]:
    """Прогоняет планировщик + симулятор + коллизии/досягаемость.

    ``description`` — объект ``SceneDescription`` (или словарь, который
    будет передан в ``build_scene_description`` через ``**``).
    """
    _ensure_animlab()
    if isinstance(description, dict):
        description = build_scene_description(**description)
    motion_planner = importlib.import_module("animation_lab.motion_planner")
    simulation = importlib.import_module("animation_lab.simulation")
    collision = importlib.import_module("animation_lab.collision")
    state_machine = importlib.import_module("animation_lab.state_machine")

    # --- планирование ----------------------------------------------------
    plan, log = motion_planner.build_plan(description, fps=fps)

    # --- симуляция --------------------------------------------------------
    result = simulation.Simulator(plan).run()

    issues: list[dict[str, Any]] = []
    # continuity gaps из лога планировщика
    for cid, t0, t1 in log.continuity_gaps:
        issues.append({
            "severity": "warning",
            "code": "continuity_gap",
            "message": f"персонаж {cid}: разрыв {t0:.2f}–{t1:.2f}с",
            "char": cid,
            "t0": round(t0, 3), "t1": round(t1, 3),
        })
    for note in log.notes:
        issues.append({
            "severity": "error" if "failed" in note else "warning",
            "code": "plan_note",
            "message": str(note),
        })

    # --- проверки на кадрах ------------------------------------------------
    for frame in result.frames:
        char_ids = sorted(frame.characters)
        # попарные пересечения персонажей
        for i in range(len(char_ids)):
            for j in range(i + 1, len(char_ids)):
                a = frame.characters[char_ids[i]]
                b = frame.characters[char_ids[j]]
                if collision.character_overlap(a, b):
                    issues.append({
                        "severity": "error",
                        "code": "character_overlap",
                        "message": (
                            f"персонажи {char_ids[i]} и {char_ids[j]} "
                            f"пересекаются в кадре t={frame.time:.2f}с"),
                        "t": round(frame.time, 3),
                    })
        # пропсы: владелец должен дотягиваться
        for pid, prop in frame.props.items():
            if prop.owner_id and prop.owner_id in frame.characters:
                actor = frame.characters[prop.owner_id]
                target = (prop.position.x, prop.position.y)
                if not collision.within_arm_reach(actor, target, reach=reach):
                    issues.append({
                        "severity": "error",
                        "code": "prop_out_of_reach",
                        "message": (
                            f"предмет {pid} вне досягаемости {prop.owner_id} "
                            f"в t={frame.time:.2f}с"),
                        "t": round(frame.time, 3),
                    })
        # минимальная дистанция между персонажами
        if len(char_ids) >= 2:
            d = collision.min_character_distance(list(frame.characters.values()))
            if d is not None and d < min_gap:
                issues.append({
                    "severity": "error",
                    "code": "characters_too_close",
                    "message": (
                        f"персонажи слишком близко (d={d:.3f}) в "
                        f"t={frame.time:.2f}с"),
                    "t": round(frame.time, 3),
                })

    # FSM-проверка последовательности действий по сегментам
    action_to_state = {
        "idle": "idle", "walk": "walking", "run": "running",
        "turn": "turning", "reach": "reaching", "hold": "holding",
        "speak": "speaking", "react": "reacting", "jump": "jumping",
        "fall": "falling", "land": "landing", "sit": "sitting",
        "stand": "standing",
    }
    steps = [
        (action_to_state[s.action_type], s.start_time)
        for s in sorted(plan.motion_segments, key=lambda s: s.start_time)
        if action_to_state.get(s.action_type)
    ]
    for prob in _validate_fsm(steps):
        issues.append({
            "severity": "error",
            "code": "fsm_illegal_transition",
            "message": prob,
        })

    status = "pass"
    if any(i["severity"] == "error" for i in issues):
        status = "fail"
    elif issues:
        status = "warn"

    return {
        "status": status,
        "issues": issues,
        "frames": len(result.frames),
        "event_count": len(result.events),
        "plan_validation": plan.validation_report.status,
        "plan_notes": list(log.notes),
    }


def _validate_fsm(steps: Sequence[tuple[str, float]]) -> list[str]:
    """Проверяет последовательность ``(state, t)`` через CharacterFSM.

    Первый шаг задаёт стартовое состояние (не переход); повторные
    одинаковые состояния не считаются переходами.
    """
    _ensure_animlab()
    sm = importlib.import_module("animation_lab.state_machine")
    problems: list[str] = []
    fsm = sm.CharacterFSM(character_id="probe")
    last_time = -1.0
    first = True
    for state, t in steps:
        if t < last_time:
            problems.append(f"time regression at {t}: {t} < {last_time}")
        last_time = t
        if first:
            fsm.state = state
            first = False
            continue
        if state == fsm.state:
            continue
        if not fsm.can_transition(state):
            problems.append(
                f"illegal transition {fsm.state!r} -> {state!r} at t={t}")
            continue
        fsm.transition(state, t)
    return problems


def fsm_check(steps: Sequence[tuple[str, float]]) -> list[str]:
    """Валидирует хронологическую последовательность ``(state, t)``."""
    return _validate_fsm(steps)


__all__ = [
    "QCError",
    "build_scene_description",
    "run_pre_render_qc",
    "fsm_check",
]
