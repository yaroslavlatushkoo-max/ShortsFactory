"""Пре-рендер геометрический QC-гейт для blocky-payload (план 2.4).

До запуска Blender проверяет собранный payload («frames» + «actors» + «props»)
на очевидные геометрические ошибки, которые иначе видно только после 10+
минут рендера: пересекающихся персонажей, недосягаемые пропсы и обрезанные
кадры.  Модуль самодостаточен: только ``numpy``, без внешней зависимости от
Animation_Intelligence_Lab (в отличие от ``animlab_qc.py``).

Что проверяется (см. константы порогов ниже):

* ``char_overlap`` — провал: минимальное расстояние между головами костей
  двух актёров на любом проверенном кадре меньше ``OVERLAP_LIMIT_M``;
* ``char_too_close`` — предупреждение: бёдра (``hips``) двух актёров ближе
  ``TOO_CLOSE_LIMIT_M`` дольше ``TOO_CLOSE_MIN_RUN`` подряд проверенных
  кадров (стоят вплотную, но не пересекаются);
* ``prop_out_of_reach`` — провал: ни один актёр, ссылающийся на пропс
  действием ``grab`` / ``pickup`` / ``interact`` / ``use``, не приближает руку
  к центру пропса ближе ``PROP_REACH_LIMIT_M`` в течение окна действия.
  Актёры, не дотянувшиеся до уже досягаемого пропса, дают ``warn``
  (``prop_reach_miss``).  Ограничение: пропсы с ``carry`` пропускаются — их
  фактическую позицию считает carry-логика, а не авторский ``center``;
  пропсы с ``pos_track`` проверяются по авторскому ``center``;
* ``frames_mismatch`` / ``actor_bones_missing`` — провал: у кадров разные
  наборы костей (обрезанный payload) или у актёра нет ни одной кости.

Формат кадра: ``{"актёр:кость": [x, y, z, r00..r22]}`` — первые три числа
голова кости в мировых координатах (метры).

Отчёт: ``{"status": "pass"|"warn"|"fail", "issues": [...], "checked_frames": N}``
пишется функцией :func:`write_report` в ``qc_report.json`` рядом с payload.

Отключение: константа ``QC_GATE_ENABLED = False`` в этом модуле или переменная
окружения ``SHORTSFACTORY_QC_GATE=0`` (например, для отладки конкретного
рендера).  Без отключения :func:`enforce_pre_render_gate` бросает
``RuntimeError`` при статусе ``fail``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "FAIL",
    "INTERACT_VERBS",
    "OVERLAP_LIMIT_M",
    "PASS",
    "PROP_REACH_LIMIT_M",
    "QC_GATE_ENABLED",
    "QC_GATE_ENV",
    "TOO_CLOSE_LIMIT_M",
    "TOO_CLOSE_MIN_RUN",
    "WARN",
    "check_payload",
    "enforce_pre_render_gate",
    "gate_enabled",
    "load_payload",
    "write_report",
]

#: Мастер-выключатель гейта.  ``False`` полностью отключает проверки
#: (env-переменная ``SHORTSFACTORY_QC_GATE=0`` делает то же самое без правки кода).
QC_GATE_ENABLED = True

#: Имя переменной окружения, отключающей гейт (``=0``).
QC_GATE_ENV = "SHORTSFACTORY_QC_GATE"

#: Статусы отчёта.
PASS = "pass"
WARN = "warn"
FAIL = "fail"

#: Проверяется каждый N-й кадр: геометрия меняется плавно, а полный проход
#: по всем кадрам двух актёров × ~25 костей замедляет сборку сцены.
FRAME_SAMPLE_STEP = 3

#: Провал «персонажи пересекаются»: головы костей двух разных актёров ближе
#: 5 см — это уже взаимопроникновение мешей, кадр будет испорчен.
OVERLAP_LIMIT_M = 0.05

#: Предупреждение «стоят вплотную»: бёдра ближе 40 см.  Разговорная сцена
#: обычно держит 0.6-1.0 м, ближе 0.4 м силуэты сливаются.
TOO_CLOSE_LIMIT_M = 0.40

#: Сколько подряд проверенных кадров (шаг 3) бёдра могут быть ближе порога,
#: прежде чем это станет предупреждением (короткий проход при ходьбе — норма).
TOO_CLOSE_MIN_RUN = 10

#: Провал «пропс не достать»: ни одна рука ни в одном кадре окна действия не
#: подошла к центру пропса ближе 35 см.  Порог = половина типичного пропса
#: (до ~0.3 м) плюс допуск кисти (~5 см); длина руки не моделируется.
PROP_REACH_LIMIT_M = 0.35

#: Глаголы действий, для которых проверяется досягаемость пропса.
INTERACT_VERBS = ("grab", "pickup", "interact", "use")

#: Максимум записей о несовпадающих кадрах в отчёте (дальше только счётчик).
_MAX_FRAME_ISSUES = 5


def gate_enabled() -> bool:
    """Гейт включён, если не выключен константой и env-переменной."""
    return QC_GATE_ENABLED and os.environ.get(QC_GATE_ENV, "") != "0"


def check_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Чистая проверка payload без записи файлов.  Возвращает отчёт.

    Отчёт: ``{"status": "pass"|"warn"|"fail", "issues": [...],
    "checked_frames": int}``.  Никогда не бросает исключений: любой сбой
    внутри превращается в ``warn``-запись, чтобы гейт не ронял продакшн
    из-за собственных ограничений.
    """
    issues: list[dict[str, Any]] = []
    checked_frames = 0
    try:
        frames = payload.get("frames") or []
        actors = payload.get("actors") or []
        fps = float(payload.get("fps") or 30.0)
        sampled = list(frames[::FRAME_SAMPLE_STEP])
        checked_frames = len(sampled)
        _check_frame_sanity(frames, actors, issues)
        positions = _actor_bone_positions(sampled, actors)
        _check_actor_overlap(positions, issues)
        _check_actors_too_close(positions, issues)
        _check_prop_reach(frames, payload, fps, issues)
    except Exception as exc:  # гейт не должен ломать продакшн
        issues.append(
            {
                "severity": WARN,
                "code": "gate_error",
                "message": f"QC-гейт не смог проверить payload: {exc}",
            }
        )
    status = FAIL if any(i["severity"] == FAIL for i in issues) else (
        WARN if any(i["severity"] == WARN for i in issues) else PASS
    )
    return {"status": status, "issues": issues, "checked_frames": checked_frames}


def write_report(payload_dir: str | Path, report: dict[str, Any]) -> Path:
    """Пишет отчёт в ``qc_report.json`` рядом с payload и возвращает путь."""
    path = Path(payload_dir) / "qc_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def load_payload(payload_dir: str | Path, scenario: str) -> dict[str, Any] | None:
    """Читает payload сцены из каталога рендера (или ``None``, если нет).

    Сначала точное имя ``payload_{scenario}.json`` (так пишет
    ``build_blocky_scene_payload``), затем первый по алфавиту
    ``payload_*.json`` — на случай переименованного сценария.
    """
    directory = Path(payload_dir)
    exact = directory / f"payload_{scenario}.json"
    for candidate in (exact, *sorted(directory.glob("payload_*.json"))):
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def enforce_pre_render_gate(
    payload_dir: str | Path, scenario: str, *, label: str = ""
) -> dict[str, Any] | None:
    """Продакшн-обвязка: проверить payload и остановить рендер при провале.

    Возвращает отчёт (или ``None``, если гейт выключен / payload не найден —
    тогда в каталоге всё равно остаётся отчёт с предупреждением).  При статусе
    ``fail`` бросает ``RuntimeError`` с путём к отчёту и первым сообщением.
    """
    if not gate_enabled():
        return None
    where = f" ({label})" if label else ""
    payload = load_payload(payload_dir, scenario)
    if payload is None:
        report = {
            "status": WARN,
            "issues": [
                {
                    "severity": WARN,
                    "code": "payload_missing",
                    "message": (
                        f"QC-гейт не нашёл payload_{scenario}.json в "
                        f"{Path(payload_dir)} — геометрическая проверка "
                        "пропущена."
                    ),
                }
            ],
            "checked_frames": 0,
        }
        write_report(payload_dir, report)
        return report
    report = check_payload(payload)
    report_path = write_report(payload_dir, report)
    if report["status"] == FAIL:
        first = report["issues"][0]["message"]
        raise RuntimeError(
            f"Пре-рендер QC провален{where}, рендер остановлен. "
            f"Отчёт: {report_path}. Первая проблема: {first}"
        )
    return report


# --------------------------------------------------------------------------
# отдельные проверки
# --------------------------------------------------------------------------


def _check_frame_sanity(
    frames: list[dict[str, Any]],
    actors: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    """Кадр-санити: одинаковые наборы костей во всех кадрах и у всех актёров."""
    if not actors and not frames:
        return
    if not frames:
        issues.append(
            {
                "severity": FAIL,
                "code": "frames_empty",
                "message": "В payload нет кадров — рендерить нечего.",
            }
        )
        return
    reference = set(frames[0])
    namespaced = any(":" in key for key in reference)
    mismatches = 0
    for index, frame in enumerate(frames[1:], start=1):
        keys = set(frame)
        if keys == reference:
            continue
        mismatches += 1
        if mismatches <= _MAX_FRAME_ISSUES:
            missing = sorted(reference - keys)
            extra = sorted(keys - reference)
            issues.append(
                {
                    "severity": FAIL,
                    "code": "frames_mismatch",
                    "message": (
                        f"Кадр {index}: набор костей не совпадает с кадром 0 "
                        f"(нет {len(missing)}, лишних {len(extra)}) — payload "
                        "обрезан или повреждён."
                    ),
                    "frame": index,
                    "missing": missing[:10],
                    "extra": extra[:10],
                }
            )
    if mismatches > _MAX_FRAME_ISSUES:
        issues.append(
            {
                "severity": FAIL,
                "code": "frames_mismatch",
                "message": (
                    f"Ещё {mismatches - _MAX_FRAME_ISSUES} кадров с неполным "
                    "набором костей (полный список см. в payload)."
                ),
            }
        )
    if not namespaced:
        return  # payload без неймспейса «актёр:кость» — принадлежность не проверить
    for actor in actors:
        key = str(actor.get("character") or "")
        prefix = f"{key}:"
        if key and not any(bone.startswith(prefix) for bone in reference):
            issues.append(
                {
                    "severity": FAIL,
                    "code": "actor_bones_missing",
                    "message": (
                        f"Актёр «{key}»: в кадрах нет ни одной его кости "
                        f"(ожидался префикс «{prefix}»)."
                    ),
                }
            )


def _actor_bone_positions(
    sampled: list[dict[str, Any]],
    actors: list[dict[str, Any]],
) -> dict[str, tuple[list[str], np.ndarray] | None]:
    """Головы костей каждого актёра: ``{ключ: (имена костей, (кадры, кости, 3))}``."""
    if not sampled:
        return {}
    reference = set(sampled[0])
    positions: dict[str, tuple[list[str], np.ndarray] | None] = {}
    for actor in actors:
        key = str(actor.get("character") or "")
        prefix = f"{key}:"
        bones = sorted(bone for bone in reference if bone.startswith(prefix))
        if not bones:
            positions[key] = None
            continue
        stack = np.empty((len(sampled), len(bones), 3), dtype=np.float64)
        for i, frame in enumerate(sampled):
            for j, bone in enumerate(bones):
                vals = frame.get(bone)
                if vals is None:
                    stack[i, j] = np.nan
                else:
                    stack[i, j] = vals[:3]
        positions[key] = (bones, stack)
    return positions


def _check_actor_overlap(
    positions: dict[str, tuple[list[str], np.ndarray] | None],
    issues: list[dict[str, Any]],
) -> None:
    """Провал: минимальное расстояние между костями двух актёров слишком мало."""
    keys = [k for k, item in positions.items() if item is not None]
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = positions[keys[i]][1], positions[keys[j]][1]
            best_dist, best_frame = np.inf, -1
            for fi in range(a.shape[0]):
                diff = a[fi][:, None, :] - b[fi][None, :, :]
                dist = float(np.sqrt((diff * diff).sum(-1)).min())
                if dist < best_dist:
                    best_dist, best_frame = dist, fi
            if best_dist < OVERLAP_LIMIT_M:
                issues.append(
                    {
                        "severity": FAIL,
                        "code": "char_overlap",
                        "message": (
                            f"Персонажи «{keys[i]}» и «{keys[j]}» пересекаются: "
                            f"минимальное расстояние между костями "
                            f"{best_dist:.3f} м < {OVERLAP_LIMIT_M} м "
                            f"(проверенный кадр {best_frame * FRAME_SAMPLE_STEP}, "
                            "счёт с 0)."
                        ),
                        "actors": [keys[i], keys[j]],
                        "frame": best_frame * FRAME_SAMPLE_STEP,
                        "distance_m": round(best_dist, 4),
                    }
                )


def _check_actors_too_close(
    positions: dict[str, tuple[list[str], np.ndarray] | None],
    issues: list[dict[str, Any]],
) -> None:
    """Предупреждение: бёдра двух актёров вплотную слишком долго."""
    hips: dict[str, int] = {}
    for key, (bones, _arr) in positions.items():
        if f"{key}:hips" in bones:
            hips[key] = bones.index(f"{key}:hips")
    keys = sorted(hips)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a = positions[keys[i]][1][:, hips[keys[i]], :]
            b = positions[keys[j]][1][:, hips[keys[j]], :]
            diff = a - b
            dists = np.sqrt((diff * diff).sum(-1))
            run = best_run = best_start = 0
            for fi, dist in enumerate(dists):
                if dist < TOO_CLOSE_LIMIT_M:
                    if run == 0:
                        run_start = fi
                    run += 1
                    if run > best_run:
                        best_run, best_start = run, run_start
                else:
                    run = 0
            if best_run > TOO_CLOSE_MIN_RUN:
                issues.append(
                    {
                        "severity": WARN,
                        "code": "char_too_close",
                        "message": (
                            f"Персонажи «{keys[i]}» и «{keys[j]}» стоят вплотную: "
                            f"бёдра ближе {TOO_CLOSE_LIMIT_M} м на протяжении "
                            f"{best_run} проверенных кадров (с кадра "
                            f"{best_start * FRAME_SAMPLE_STEP})."
                        ),
                        "actors": [keys[i], keys[j]],
                        "frames": best_run,
                    }
                )


def _check_prop_reach(
    frames: list[dict[str, Any]],
    payload: dict[str, Any],
    fps: float,
    issues: list[dict[str, Any]],
) -> None:
    """Провал: пропс, до которого не дотянулся ни один актёр.

    Для каждого действия ``grab``/``pickup``/``interact``/``use`` берётся окно
    кадров действия, а не только кадр старта: рука доходит до пропса в середине
    действия, и калибровка по готовым сценам (``lost_diamond``, ``tricky_door``,
    ``honey_trouble``) показала, что старт-кадр всегда даёт ложный провал.
    Пропс проваливает проверку, только если **ни один** ссылающийся на него
    актёр не приблизил руку к центру ближе ``PROP_REACH_LIMIT_M`` — значит,
    пропс стоит вне игровой зоны и рендер будет испорчен.  Актёры, которые
    сами не дотянулись, пока кто-то другой достал, получают ``warn``
    (пантомима, обычна для сцены с двумя «исследователями»).
    """
    props = {
        str(prop.get("name") or ""): prop
        for prop in (payload.get("props") or [])
        if isinstance(prop, dict)
    }
    if not props or not frames:
        return
    refs: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for actor in payload.get("actors") or []:
        key = str(actor.get("character") or "")
        for action in actor.get("actions") or []:
            verb = str(action.get("action") or "").strip().lower()
            if verb not in INTERACT_VERBS:
                continue
            prop_name = str((action.get("params") or {}).get("prop") or "")
            if prop_name in props:
                refs.setdefault(prop_name, []).append((key, verb, action))
    for prop_name, pairs in refs.items():
        prop = props[prop_name]
        if prop.get("carry"):
            # носимый пропс двигает carry-логика: авторский center в этот
            # момент не соответствует фактической позиции пропса
            continue
        center = prop.get("center")
        if not center:
            continue
        target = np.asarray(center, dtype=np.float64)
        attempts: list[dict[str, Any]] = []
        for key, verb, action in pairs:
            best = _min_hand_distance(frames, key, target, action, fps)
            if best is None:
                issues.append(
                    {
                        "severity": WARN,
                        "code": "prop_no_hand",
                        "message": (
                            f"Актёр «{key}»: в кадрах нет костей рук — "
                            f"досягаемость пропса «{prop_name}» не проверить."
                        ),
                    }
                )
                continue
            attempts.append({"actor": key, "verb": verb, **best})
        if not attempts:
            continue
        reached = [a for a in attempts if a["distance_m"] <= PROP_REACH_LIMIT_M]
        if not reached:
            closest = min(attempts, key=lambda a: a["distance_m"])
            issues.append(
                {
                    "severity": FAIL,
                    "code": "prop_out_of_reach",
                    "message": (
                        f"Пропс «{prop_name}» не достать никому: ближе всего "
                        f"актёр «{closest['actor']}» (действие "
                        f"«{closest['verb']}», кадр {closest['frame']}) — "
                        f"{closest['distance_m']:.3f} м при лимите "
                        f"{PROP_REACH_LIMIT_M} м."
                    ),
                    "prop": prop_name,
                    "frame": closest["frame"],
                    "distance_m": closest["distance_m"],
                    "actors": sorted(a["actor"] for a in attempts),
                }
            )
            continue
        for miss in attempts:
            if miss["distance_m"] <= PROP_REACH_LIMIT_M:
                continue
            issues.append(
                {
                    "severity": WARN,
                    "code": "prop_reach_miss",
                    "message": (
                        f"Актёр «{miss['actor']}» не дотянулся до пропса "
                        f"«{prop_name}» (действие «{miss['verb']}», кадр "
                        f"{miss['frame']}): {miss['distance_m']:.3f} м — "
                        "дотягивается другой актёр, возможно пантомима."
                    ),
                    "actor": miss["actor"],
                    "prop": prop_name,
                    "frame": miss["frame"],
                    "distance_m": miss["distance_m"],
                }
            )


def _min_hand_distance(
    frames: list[dict[str, Any]],
    key: str,
    target: np.ndarray,
    action: dict[str, Any],
    fps: float,
) -> dict[str, Any] | None:
    """Минимальное расстояние руки актёра до ``target`` в окне действия.

    Возвращает ``{"distance_m", "frame"}`` или ``None``, если у актёра нет
    костей рук в проверяемых кадрах.
    """
    start = float(action.get("start") or 0.0)
    duration = float(action.get("duration") or 0.0)
    last = len(frames) - 1
    f0 = min(max(int(round(start * fps)), 0), last)
    f1 = min(max(int(round((start + duration) * fps)), f0), last)
    best: dict[str, Any] | None = None
    for fi in range(f0, f1 + 1):
        frame = frames[fi]
        hands = [
            np.asarray(frame[f"{key}:hand_{side}"][:3], dtype=np.float64)
            for side in ("L", "R")
            if f"{key}:hand_{side}" in frame
        ]
        if not hands:
            continue
        dist = min(float(np.sqrt(((hand - target) ** 2).sum())) for hand in hands)
        if best is None or dist < best["distance_m"]:
            best = {"distance_m": round(dist, 4), "frame": fi}
    return best
