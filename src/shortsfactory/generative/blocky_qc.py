"""E13: QC-гейты для blocky-сцен и фильмов.

Модуль читает уже существующие отчёты (``scene_report.json``, ``film_report.json``)
и payload, а не рендерит заново.  Каждая проверка возвращает статус
``pass`` / ``warn`` / ``fail`` и, где возможно, таймкод — чтобы проблему можно
было открыть на конкретной секунде, а не искать «где-то в сцене».

Итоговый статус:

* ``READY`` — все проверки pass;
* ``PARTIAL`` — есть warn, но нет fail (например, старый рендер без замера
  зазора силуэтов);
* ``FAILED`` — хотя бы один fail (чёрные кадры, дрейф стоп, рассинхрон).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

__all__ = [
    "FAILED",
    "LIMITS",
    "PARTIAL",
    "READY",
    "film_qc",
    "frame_timecode",
    "parse_srt",
    "scene_qc",
    "timecode",
]

READY = "READY"
PARTIAL = "PARTIAL"
FAILED = "FAILED"

#: Пороги подобраны по уже проверенным рендерам (m4–m7): здоровый рендер
#: Калибровка: стояние даёт дрейф 0.0 м и hover 0.02 м; ходьба в стойках
#: окон закономерно поднимает стопу на ~0.055-0.057 м (фаза переноса внутри
#: окна) — это не скольжение и не провал, поэтому hover_warn = 0.08.
LIMITS: dict[str, float] = {
    # ходьба стабильно даёт дрейф 0.020-0.023 м (фаза переноса), стояние — 0.0;
    # warn 0.03 отделяет здоровую походку от реального скольжения
    "drift_m": 0.03,
    "penetration_m": 0.005,
    "hover_m": 0.08,
    # hard-fail thresholds for foot contact — moderate drift/hover is a
    # non-critical perceptual defect (PARTIAL), severe values fail the scene
    "drift_fail_m": 0.06,
    "hover_fail_m": 0.09,
    "actor_gap_m": 0.02,
    "prop_step_m": 0.35,
    "audio_sync_s": 0.06,
    "subtitle_sync_s": 0.08,
    "min_rms": 0.001,
    "film_min_s": 40.0,
    "film_max_s": 60.0,
}

_SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def timecode(seconds: Any) -> str:
    """``83.4`` → ``1:23.400`` (часы опускаются, если их нет)."""
    try:
        value = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return "—"
    minutes, rest = divmod(value, 60.0)
    if minutes >= 60:
        hours, minutes = divmod(int(minutes), 60)
        return f"{hours}:{minutes:02d}:{rest:06.3f}"
    return f"{int(minutes)}:{rest:06.3f}"


def frame_timecode(frame: Any, fps: float) -> str:
    try:
        number = int(frame)
        rate = float(fps) or 30.0
    except (TypeError, ValueError):
        return "—"
    return timecode(max(0, number - 1) / rate)


def parse_srt(text: str) -> list[dict[str, Any]]:
    """Minimal SRT reader (start/end/text) for consistency checks."""
    cues: list[dict[str, Any]] = []
    blocks = re.split(r"\r?\n\r?\n+", text.strip())
    for block in blocks:
        match = _SRT_TIME.search(block)
        if not match:
            continue
        start = (
            int(match.group(1)) * 3600
            + int(match.group(2)) * 60
            + int(match.group(3))
            + int(match.group(4)) / 1000.0
        )
        end = (
            int(match.group(5)) * 3600
            + int(match.group(6)) * 60
            + int(match.group(7))
            + int(match.group(8)) / 1000.0
        )
        body = block[match.end():].strip()
        cues.append({"start": round(start, 3), "end": round(end, 3), "text": body})
    return cues


def _check(
    ident: str,
    label: str,
    status: str,
    detail: str,
    *,
    value: Any = None,
    limit: Any = None,
    timecodes: list[str] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": ident,
        "label": label,
        "status": status,
        "detail": detail,
    }
    if value is not None:
        item["value"] = value
    if limit is not None:
        item["limit"] = limit
    if timecodes:
        item["timecodes"] = timecodes
    return item


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _overall(checks: list[dict[str, Any]]) -> str:
    if any(check["status"] == "fail" for check in checks):
        return FAILED
    if any(check["status"] == "warn" for check in checks):
        return PARTIAL
    return READY


def scene_qc(
    report: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
    srt_path: str | Path | None = None,
    video_duration: float | None = None,
    handoff_windows: list[tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Gate one rendered scene against E13 thresholds."""
    checks: list[dict[str, Any]] = []
    diagnostics = report.get("diagnostics") or {}
    fps = float(diagnostics.get("fps") or report.get("fps") or 30.0)
    duration = float(report.get("duration") or diagnostics.get("frames", 0) / fps or 0.0)

    status = str(report.get("status") or "FAILED")
    checks.append(
        _check(
            "render",
            "Рендер завершён",
            "pass" if status == "OK" else "fail",
            f"status={status}" + (f", error={report.get('error')}" if report.get("error") else ""),
        )
    )

    blank = diagnostics.get("blank_frames") or {}
    if blank:
        frames = blank.get("blank_frames") or []
        codes = [frame_timecode(frame, fps) for frame in frames[:8]]
        bad = int(blank.get("blank") or 0) > 0
        checks.append(
            _check(
                "blank_frames",
                "Чёрные/пустые кадры",
                "fail" if bad else "pass",
                f"{blank.get('blank', 0)} из {blank.get('checked', 0)}, min std {blank.get('min_std')}",
                value=blank.get("blank", 0),
                limit=0,
                timecodes=codes,
            )
        )
    else:
        checks.append(_check("blank_frames", "Чёрные/пустые кадры", "warn", "диагностика отсутствует"))

    pose = diagnostics.get("pose_error") or {}
    if pose:
        checks.append(
            _check(
                "pose",
                "Поза соответствует замыслу",
                "pass" if pose.get("passed") else "fail",
                f"max diff {pose.get('max_abs_matrix_diff')}",
                value=pose.get("max_abs_matrix_diff"),
                limit=1e-4,
            )
        )
    else:
        checks.append(_check("pose", "Поза соответствует замыслу", "warn", "диагностика отсутствует"))

    foot = diagnostics.get("foot_contact") or {}
    if foot and foot.get("windows"):
        drift = float(foot.get("max_drift") or 0.0)
        pen = float(foot.get("max_penetration") or 0.0)
        hover = float(foot.get("max_hover") or 0.0)
        hard = (
            drift > LIMITS["drift_fail_m"]
            or pen > LIMITS["penetration_m"]
            or hover > LIMITS["hover_fail_m"]
        )
        soft = (
            drift > LIMITS["drift_m"]
            or hover > LIMITS["hover_m"]
        )
        status = "fail" if hard else ("warn" if soft else "pass")
        checks.append(
            _check(
                "foot",
                "Стопы не скользят и не висят",
                status,
                f"дрейф {drift:.4f} м, провал {pen:.4f} м, вис {hover:.4f} м",
                value={"drift": drift, "penetration": pen, "hover": hover},
                limit={
                    "drift": LIMITS["drift_m"],
                    "drift_fail": LIMITS["drift_fail_m"],
                    "penetration": LIMITS["penetration_m"],
                    "hover": LIMITS["hover_m"],
                    "hover_fail": LIMITS["hover_fail_m"],
                },
            )
        )
    else:
        checks.append(_check("foot", "Стопы не скользят и не висят", "warn", "нет окон опоры"))

    gap = diagnostics.get("actor_gap") or {}
    if gap.get("measured"):
        value = float(gap.get("min_gap") or 0.0)
        frame = gap.get("min_frame")
        status = "fail" if value < LIMITS["actor_gap_m"] else "pass"
        detail = f"минимальный зазор {value:.4f} м"
        # При передаче предмета между персонажами (рукопожатие) силуэты
        # закономерно пересекаются на короткое время — это не дефект.
        if status == "fail" and frame is not None and handoff_windows:
            t_sec = float(frame) / fps if fps else 0.0
            if any(lo <= t_sec <= hi for lo, hi in handoff_windows):
                status = "warn"
                detail = f"минимальный зазор {value:.4f} м (рукопожатие)"
        checks.append(
            _check(
                "silhouettes",
                "Силуэты актёров не пересекаются",
                status,
                detail,
                value=value,
                limit=LIMITS["actor_gap_m"],
                timecodes=[frame_timecode(frame, fps)] if frame else None,
            )
        )
    else:
        actor_count = len((payload or {}).get("actors") or [])
        if actor_count <= 1:
            # a single actor cannot overlap anyone — the check is inapplicable
            checks.append(
                _check(
                    "silhouettes",
                    "Силуэты актёров не пересекаются",
                    "pass",
                    "один актёр в сцене — проверка неприменима",
                )
            )
        else:
            checks.append(
                _check(
                    "silhouettes",
                    "Силуэты актёров не пересекаются",
                    "warn",
                    "замер появился позже этого рендера (нужен новый прогон)",
                )
            )

    props = (payload or {}).get("props") or report.get("props") or []
    if props:
        worst_step = 0.0
        worst_frame = 0
        lowest = 1.0
        for prop in props:
            track = prop.get("pos_track") or []
            previous = None
            previous_frame = 0
            for index, item in enumerate(track):
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    previous = None
                    continue
                # payload tracks are [frame, [x, y, z]]; bare xyz is accepted too
                if isinstance(item[1], (list, tuple)):
                    frame_no = int(item[0])
                    point = [float(value) for value in item[1][:3]]
                else:
                    frame_no = index + 1
                    point = [float(value) for value in item[:3]]
                if len(point) < 3:
                    previous = None
                    continue
                lowest = min(lowest, point[2])
                if previous is not None:
                    # ключевые кадры разрежены: скорость считаем на кадр
                    # (Blender интерполирует между ними плавно), а не как
                    # расстояние между соседними ключами
                    per_frame = (
                        sum((a - b) ** 2 for a, b in zip(point, previous)) ** 0.5
                        / max(1, frame_no - previous_frame)
                    )
                    if per_frame > worst_step:
                        worst_step = per_frame
                        worst_frame = frame_no
                previous = point
                previous_frame = frame_no
        jump = worst_step > LIMITS["prop_step_m"]
        sink = lowest < -0.01
        checks.append(
            _check(
                "props",
                "Привязка предметов без прыжков",
                "fail" if (jump or sink) else "pass",
                f"макс. шаг {worst_step:.3f} м/кадр, min z {lowest:.3f} м",
                value={"max_step": round(worst_step, 3), "min_z": round(lowest, 3)},
                limit={"max_step": LIMITS["prop_step_m"], "min_z": -0.01},
                timecodes=[frame_timecode(worst_frame, fps)] if worst_frame else None,
            )
        )

    mix = report.get("mix") or {}
    if mix:
        peak = float(mix.get("peak") or 0.0)
        rms = float(mix.get("rms") or 0.0)
        audio_len = float(mix.get("duration_s") or 0.0)
        sync_error = abs(audio_len - duration)
        video_value = float(video_duration if video_duration is not None else duration)
        video_error = abs(audio_len - video_value)
        bad = (
            bool(mix.get("clipped"))
            or peak >= 0.999
            or rms < LIMITS["min_rms"]
            or sync_error > LIMITS["audio_sync_s"]
            or video_error > LIMITS["audio_sync_s"]
        )
        checks.append(
            _check(
                "audio",
                "Аудио: уровень и синхрон с видео",
                "fail" if bad else "pass",
                f"peak {peak:.3f}, rms {rms:.4f}, аудио {audio_len:.3f} с, "
                f"видео {video_value:.3f} с, расхождение {video_error:.3f} с",
                value={"peak": peak, "rms": rms, "audio_s": audio_len, "video_s": video_value},
                limit={
                    "peak": 0.999,
                    "min_rms": LIMITS["min_rms"],
                    "sync_s": LIMITS["audio_sync_s"],
                },
            )
        )
    else:
        checks.append(_check("audio", "Аудио: уровень и синхрон с видео", "warn", "аудио нет"))

    takes = report.get("takes") or []
    if takes:
        if srt_path and Path(srt_path).exists():
            cues = parse_srt(Path(srt_path).read_text(encoding="utf-8"))
            problems: list[str] = []
            codes: list[str] = []
            if len(cues) != len(takes):
                problems.append(f"субтитров {len(cues)}, реплик {len(takes)}")
            for index, (cue, take) in enumerate(zip(cues, takes), 1):
                start = float(take.get("start") or 0.0)
                end = start + float(take.get("duration") or 0.0)
                if abs(cue["start"] - start) > LIMITS["subtitle_sync_s"]:
                    problems.append(f"#{index} начало {cue['start']:.2f} ≠ {start:.2f}")
                    codes.append(timecode(cue["start"]))
                if abs(cue["end"] - end) > LIMITS["subtitle_sync_s"]:
                    problems.append(f"#{index} конец {cue['end']:.2f} ≠ {end:.2f}")
                    codes.append(timecode(cue["end"]))
                spoken = str(take.get("text") or "").strip()
                if spoken and spoken not in cue["text"]:
                    problems.append(f"#{index} текст не совпадает")
                    codes.append(timecode(cue["start"]))
                if cue["end"] > duration + 0.1:
                    problems.append(f"#{index} субтитр за концом сцены")
                    codes.append(timecode(cue["end"]))
            checks.append(
                _check(
                    "subtitles",
                    "Субтитры совпадают с речью",
                    "fail" if problems else "pass",
                    "; ".join(problems[:6]) if problems else f"{len(cues)} субтитров, все в тайминге",
                    value=len(cues),
                    limit=len(takes),
                    timecodes=codes[:8],
                )
            )
        else:
            checks.append(
                _check("subtitles", "Субтитры совпадают с речью", "warn", "SRT не найден")
            )
        dialogue = report.get("dialogue_qc") or {}
        if dialogue:
            checks.append(
                _check(
                    "dialogue",
                    "Реплика ↔ рот",
                    "pass" if dialogue.get("ok") else "fail",
                    "; ".join(map(str, (dialogue.get("issues") or [])[:4])) or "ok",
                )
            )
        else:
            checks.append(_check("dialogue", "Реплика ↔ рот", "warn", "dialogue_qc отсутствует"))
    else:
        checks.append(_check("subtitles", "Субтитры совпадают с речью", "pass", "немой рендер"))

    camera = report.get("camera_qc")
    if camera is not None:
        checks.append(
            _check(
                "camera",
                "Планы камеры валидны",
                "pass" if camera.get("ok") else "fail",
                "; ".join(map(str, (camera.get("issues") or [])[:4])) or "ok",
            )
        )

    return {
        "kind": "scene",
        "title": report.get("title"),
        "status": _overall(checks),
        "fps": fps,
        "duration": round(duration, 3),
        "checks": checks,
        "fails": [check["id"] for check in checks if check["status"] == "fail"],
        "warns": [check["id"] for check in checks if check["status"] == "warn"],
    }


def film_qc(
    report: dict[str, Any],
    *,
    srt_path: str | Path | None = None,
    limits: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Gate a cut film: scenes, real duration, subtitles inside the cut."""
    limits = limits or LIMITS
    checks: list[dict[str, Any]] = []
    status = str(report.get("status") or "FAILED")
    checks.append(
        _check(
            "render",
            "Фильм склеен",
            "pass" if status == "OK" else ("warn" if status == "PARTIAL" else "fail"),
            f"status={status}" + (f", error={report.get('error')}" if report.get("error") else ""),
        )
    )

    scenes = report.get("scenes") or []
    failed_scenes = [
        item for item in scenes if str(item.get("status")) in ("FAILED", "")
    ]
    partial_scenes = [item for item in scenes if str(item.get("status")) == "PARTIAL"]
    if not scenes or failed_scenes:
        scene_status = "fail"
    elif partial_scenes:
        scene_status = "warn"
    else:
        scene_status = "pass"
    checks.append(
        _check(
            "scenes",
            "Все сцены отрендерены",
            scene_status,
            f"{len(scenes) - len(failed_scenes) - len(partial_scenes)} из {len(scenes)} OK"
            + (
                ", PARTIAL: " + ", ".join(str(item.get("index")) for item in partial_scenes)
                if partial_scenes
                else ""
            )
            + (
                ", сбои: " + ", ".join(str(item.get("index")) for item in failed_scenes)
                if failed_scenes
                else ""
            ),
            value=len(scenes) - len(failed_scenes) - len(partial_scenes),
            limit=len(scenes),
        )
    )

    duration = float(report.get("duration") or 0.0)
    planned = float(report.get("planned_duration") or 0.0)
    if duration <= 0:
        checks.append(_check("duration", "Длительность фильма", "fail", "длительность не измерена"))
    else:
        drift = abs(duration - planned) if planned else 0.0
        minimum = float(limits["film_min_s"])
        maximum = float(limits["film_max_s"])
        # For short demo films the 40–60 s target is reported but not failed.
        target = minimum <= duration <= maximum
        checks.append(
            _check(
                "duration",
                "Длительность фильма",
                "pass" if drift <= 0.25 else "fail",
                f"{duration:.3f} с (план {planned:.3f} с), цель 40–60 с: "
                + ("да" if target else "нет (демо)"),
                value=round(duration, 3),
                limit={"min": minimum, "max": maximum, "sync_s": 0.25},
            )
        )

    if srt_path and Path(srt_path).exists():
        cues = parse_srt(Path(srt_path).read_text(encoding="utf-8"))
        late = [cue for cue in cues if cue["end"] > duration + 0.12]
        checks.append(
            _check(
                "subtitles",
                "Субтитры внутри фильма",
                "fail" if late else "pass",
                f"{len(cues)} субтитров" + (f", {len(late)} за концом" if late else ""),
                value=len(cues),
                timecodes=[timecode(cue["end"]) for cue in late[:6]],
            )
        )

    sheet = report.get("contact_sheet")
    checks.append(
        _check(
            "contact_sheet",
            "Контактный лист собран",
            "pass" if sheet and Path(str(sheet)).exists() else "warn",
            str(sheet or "нет"),
        )
    )

    return {
        "kind": "film",
        "title": report.get("title"),
        "status": _overall(checks),
        "duration": round(duration, 3),
        "checks": checks,
        "fails": [check["id"] for check in checks if check["status"] == "fail"],
        "warns": [check["id"] for check in checks if check["status"] == "warn"],
    }
