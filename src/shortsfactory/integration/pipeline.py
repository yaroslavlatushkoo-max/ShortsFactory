"""End-to-end orchestration and integration QC report."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shortsfactory.animation.paths import path_metrics

from .animation_plan import AnimationPlanAdapter, MotionResult, build_factory_storyboard
from .contracts import IntegrationError, IntegrationPaths, write_json
from .factory_adapter import FactoryResult, ShortsFactoryAdapter
from .story_adapter import StoryResult, StoryStudioAdapter
from .text_story import detect_motion_profile


STATUS_PASS = "PASS"
STATUS_WARNING = "WARNING"
STATUS_FAIL = "FAIL"
STATUS_NOT_CHECKED = "NOT_CHECKED"
_VALID_STATUSES = {STATUS_PASS, STATUS_WARNING, STATUS_FAIL, STATUS_NOT_CHECKED}


@dataclass
class IntegratedResult:
    status: str
    output_dir: Path
    video: Path
    contact_sheet: Path | None
    report_json: Path
    report_md: Path
    story: StoryResult
    motion: MotionResult
    factory: FactoryResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output_dir": str(self.output_dir),
            "video": str(self.video),
            "contact_sheet": str(self.contact_sheet) if self.contact_sheet else None,
            "report_json": str(self.report_json),
            "report_md": str(self.report_md),
            "story_project": str(self.story.project_path),
            "motion_plan": str(self.motion.plan_path),
            "factory_report": str(self.factory.report_json),
        }


def _check(
    check_id: str,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid QC status {status}")
    return {
        "id": check_id,
        "status": status,
        "message": message,
        "details": details or {},
    }


def _aggregate(checks: list[dict[str, Any]]) -> str:
    statuses = {item["status"] for item in checks}
    if STATUS_FAIL in statuses:
        return STATUS_FAIL
    if STATUS_WARNING in statuses or STATUS_NOT_CHECKED in statuses:
        return STATUS_WARNING
    return STATUS_PASS


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        for key in ("ok", "verified", "passed", "clean", "decode_full"):
            if key in value and isinstance(value[key], bool):
                return value[key]
    return None


def _factory_verified(factory: FactoryResult) -> dict[str, Any]:
    verified = factory.report.get("verified")
    return verified if isinstance(verified, dict) else {}


def _factory_check(factory: FactoryResult, name: str, check_id: str, label: str) -> dict[str, Any]:
    verified = _factory_verified(factory)
    value = verified.get(name)
    if name == "attachment":
        flag = _flag(value)
        if value is None:
            return _check(check_id, STATUS_NOT_CHECKED, f"{label}: метрика отсутствует.")
        if not isinstance(value, dict) or not value.get("enabled", False):
            return _check(check_id, STATUS_NOT_CHECKED, f"{label}: привязка не включена.")
        return _check(
            check_id,
            STATUS_PASS if flag else STATUS_FAIL,
            f"{label}: {'пройдена' if flag else 'не пройдена'}.",
            {"objects": value.get("objects", []), "note": value.get("note")},
        )
    if name == "motion" and isinstance(value, dict):
        flag = bool(value.get("limbs_move_without_camera"))
    elif name == "pose_quality" and isinstance(value, dict):
        flag = bool(value.get("enabled")) and not value.get("issues")
    else:
        flag = _flag(value)
    if flag is None:
        return _check(check_id, STATUS_NOT_CHECKED, f"{label}: метрика отсутствует.")
    details = value if isinstance(value, dict) else {}
    return _check(check_id, STATUS_PASS if flag else STATUS_FAIL, f"{label}: {'пройдена' if flag else 'не пройдена'}.", details)


def _lab_issues(motion: MotionResult) -> list[dict[str, Any]]:
    issues = motion.validation.get("issues", [])
    return [item for item in issues if isinstance(item, dict)]


def _lab_check(motion: MotionResult) -> dict[str, Any]:
    status = str(motion.validation.get("status", "")).lower()
    issues = _lab_issues(motion)
    severe = [
        issue
        for issue in issues
        if str(issue.get("severity", "")).lower() in {"error", "critical"}
    ]
    if status == "fail" or severe:
        mapped = STATUS_FAIL
    elif status in {"warnings", "warning"} or any(
        str(issue.get("severity", "")).lower() == "warning" for issue in issues
    ):
        mapped = STATUS_WARNING
    elif status == "pass":
        mapped = STATUS_PASS
    else:
        mapped = STATUS_NOT_CHECKED
    return _check(
        "motion_plan",
        mapped,
        f"Animation Lab: {status or 'нет статуса'}.",
        {"issues": issues, "issue_count": len(issues)},
    )


def _story_check(story: StoryResult) -> dict[str, Any]:
    errors = int(story.validation.get("error_count", 0))
    warnings = int(story.validation.get("warning_count", 0))
    if errors:
        return _check(
            "story_schema",
            STATUS_FAIL,
            f"Story Studio: {errors} ошибок.",
            {"validation": story.validation},
        )
    return _check(
        "story_schema",
        STATUS_WARNING if warnings else STATUS_PASS,
        f"Story Studio: {warnings} предупреждений, ошибок нет.",
        {"validation": story.validation},
    )


def _timing_check(story: StoryResult, motion: MotionResult, storyboard: dict[str, Any]) -> dict[str, Any]:
    scene = story.project["scenes"][0]
    duration = float(scene["end_time"])
    problems: list[str] = []
    for action in scene["actions"]:
        if float(action["end_time"]) > duration + 1e-6:
            problems.append(f"{action['id']} выходит за границу сцены")
        if float(action["end_time"]) <= float(action["start_time"]):
            problems.append(f"{action['id']} имеет неположительную длительность")
    for segment in motion.plan.get("motion_segments", []):
        if float(segment["end_time"]) > duration + 1e-6:
            problems.append(f"{segment['id']} выходит за границу плана")
    for beat in storyboard.get("beats", []):
        if float(beat.get("start", 0.0)) + float(beat.get("duration", 0.0)) > duration + 1e-6:
            problems.append(f"{beat.get('id')} выходит за границу storyboard")
    if problems:
        return _check("timings", STATUS_FAIL, "Нарушена непрерывность таймингов.", {"problems": problems})
    return _check(
        "timings",
        STATUS_PASS,
        f"Тайминги согласованы: {len(scene['actions'])} действий, {len(motion.plan.get('motion_segments', []))} сегментов.",
        {"duration": duration},
    )


def _mapping_check(story: StoryResult, motion: MotionResult, storyboard: dict[str, Any]) -> dict[str, Any]:
    plan_ids = set(motion.plan.get("characters", {}))
    story_ids = {character["id"] for character in story.project["characters"]}
    factory_ids = {character["id"] for character in storyboard.get("characters", [])}
    missing = sorted(story_ids - plan_ids)
    if missing or len(factory_ids) != len(story_ids):
        return _check(
            "mappings",
            STATUS_FAIL,
            "Потеряны идентификаторы персонажей при передаче.",
            {"story": sorted(story_ids), "lab": sorted(plan_ids), "factory": sorted(factory_ids)},
        )
    return _check(
        "mappings",
        STATUS_PASS,
        "Идентификаторы персонажей и предметов сохранены во всех адаптерах.",
        {"characters": sorted(story_ids), "objects": sorted(item["id"] for item in storyboard.get("objects", []))},
    )


def _interaction_check(motion: MotionResult, factory: FactoryResult) -> dict[str, Any]:
    codes = {str(issue.get("code")) for issue in _lab_issues(motion)}
    critical = {
        "DOUBLE_OWNERSHIP",
        "TARGET_UNREACHABLE",
        "TRAJECTORY_DISCONTINUITY",
        "POSITION_JUMP",
        "UNBACKED_EVENT_TRANSITION",
        "ILLEGAL_STATE_TRANSITION",
    }
    bad = sorted(codes & critical)
    if bad:
        return _check(
            "interactions",
            STATUS_FAIL,
            "Animation Lab обнаружил разрывы или конфликты взаимодействия.",
            {"codes": bad, "issues": _lab_issues(motion)},
        )
    attachment = _factory_verified(factory).get("attachment", {})
    attachment_ok = bool(attachment.get("verified")) if isinstance(attachment, dict) else False
    if not attachment_ok:
        return _check(
            "interactions",
            STATUS_FAIL,
            "Factory не подтвердил непрерывность привязки предмета.",
            {"attachment": attachment},
        )
    return _check(
        "interactions",
        STATUS_PASS,
        "Передача предмета непрерывна на математическом плане и в кадрах Factory.",
        {"attachment": attachment},
    )


def _video_check(factory: FactoryResult) -> dict[str, Any]:
    verified = _factory_verified(factory)
    result = factory.result
    decode = verified.get("decode_full")
    if decode is None:
        decode = result.get("decode_ok")
    if not decode:
        return _check("video", STATUS_FAIL, "Полное декодирование MP4 не прошло.", {"verified": verified})
    ffprobe = factory.report.get("ffprobe", {})
    streams = ffprobe.get("streams") if isinstance(ffprobe, dict) else None
    width = result.get("width")
    height = result.get("height")
    frames = result.get("frames")
    duration = result.get("duration")
    size = result.get("size_bytes")
    return _check(
        "video",
        STATUS_PASS,
        f"MP4 декодируется полностью: {width}x{height}, {frames} кадров, {duration}s.",
        {
            "ffprobe": ffprobe,
            "streams": streams,
            "size_bytes": size,
            "width": width,
            "height": height,
            "frames": frames,
            "duration": duration,
            "codecs": result.get("codecs"),
        },
    )


def _trajectory_check(
    motion: MotionResult, storyboard: dict[str, Any], motion_profile: str
) -> dict[str, Any]:
    segments = motion.plan.get("motion_segments", [])
    moving = [
        segment
        for segment in segments
        if str(segment.get("trajectory", {}).get("kind", "hold")) != "hold"
        and float(segment["end_state"]["position"]["x"])
        != float(segment["start_state"]["position"]["x"])
    ]
    beats_with_path = {
        str(beat.get("id")) for beat in storyboard.get("beats", []) if beat.get("path")
    }
    missing = [
        segment["id"]
        for segment in moving
        if f"b_{segment['id'].removeprefix('seg_')}" not in beats_with_path
    ]
    kinds = sorted({str(segment["trajectory"]["kind"]) for segment in moving})
    details = {
        "motion_profile": motion_profile,
        "trajectory_kinds": kinds,
        "moving_segments": len(moving),
        "beats_with_path": len(beats_with_path),
    }
    if missing:
        return _check(
            "trajectory_integration",
            STATUS_FAIL,
            "Не все траектории Lab дошли до storyboard/рендерера.",
            {**details, "missing": missing[:20]},
        )
    if not moving:
        return _check(
            "trajectory_integration",
            STATUS_NOT_CHECKED,
            "В сценарии нет перемещений, траекторный канал не задействован.",
            details,
        )
    return _check(
        "trajectory_integration",
        STATUS_PASS,
        f"Траектории переданы в Factory: {len(moving)} сегментов, профиль {motion_profile}, виды {', '.join(kinds)}.",
        details,
    )


def _silhouette_check(value: dict[str, Any]) -> dict[str, Any]:
    status = str(value.get("status", "")).upper()
    mapped = {
        "PASS": STATUS_PASS,
        "WARNING": STATUS_WARNING,
        "FAIL": STATUS_FAIL,
    }.get(status, STATUS_NOT_CHECKED)
    details = {
        "checked_frames": value.get("checked_frames"),
        "samples": value.get("samples"),
        "failure_count": value.get("failure_count"),
        "warning_count": value.get("warning_count"),
        "issues": value.get("issues", [])[:20],
        "diagnostics": value.get("diagnostics"),
        "note": value.get("note"),
    }
    return _check(
        "silhouette_intersections",
        mapped,
        f"Пересечения силуэтов: {status or 'нет статуса'} по {value.get('checked_frames', 0)} кадрам.",
        details,
    )


def build_qc_report(
    *,
    output_dir: Path,
    story: StoryResult,
    motion: MotionResult,
    storyboard: dict[str, Any],
    factory: FactoryResult,
    source_text: str,
    motion_profile: str = "linear",
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = [
        _story_check(story),
        _check(
            "storyboard_compile",
            STATUS_PASS,
            "Storyboard v1 скомпилирован существующим компилятором Factory.",
            {"path": str(factory.storyboard_path), "beats": len(storyboard.get("beats", []))},
        ),
        _lab_check(motion),
        _timing_check(story, motion, storyboard),
        _mapping_check(story, motion, storyboard),
        _interaction_check(motion, factory),
        _factory_check(factory, "motion", "motion", "Движение конечностей"),
        _factory_check(factory, "framing", "framing", "Framing"),
        _factory_check(factory, "pose_quality", "pose_quality", "Качество поз"),
        _video_check(factory),
    ]
    voice = factory.report.get("voice")
    if voice:
        checks.append(_factory_check(factory, "lip_sync", "lip_sync", "Lip-sync"))
    else:
        checks.append(_check("lip_sync", STATUS_NOT_CHECKED, "Lip-sync: голосовой трек отсутствует."))
    verified = _factory_verified(factory)
    silhouette = verified.get("silhouette_intersections")
    if silhouette is not None:
        checks.append(_silhouette_check(silhouette))
    else:
        checks.append(
            _check(
                "silhouette_intersections",
                STATUS_NOT_CHECKED,
                "Пересечения силуэтов: метрика отсутствует в отчёте Factory.",
            )
        )
    checks.append(_trajectory_check(motion, storyboard, motion_profile))
    status = _aggregate(checks)
    verified = _factory_verified(factory)
    determinism = verified.get("determinism") or []
    report = {
        "schema_version": "1.0.0",
        "integration": "shorts_factory_story_studio_animation_lab",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input": {
            "text": source_text,
            "characters": [c["id"] for c in story.project["characters"]],
            "motion_profile": motion_profile,
        },
        "stages": {
            "story_studio": {
                "status": "PASS" if int(story.validation.get("error_count", 0)) == 0 else "FAIL",
                "project": str(story.project_path),
                "validation": str(story.validation_path),
                "error_count": story.validation.get("error_count"),
                "warning_count": story.validation.get("warning_count"),
            },
            "animation_lab": {
                "status": motion.validation.get("status"),
                "plan": str(motion.plan_path),
                "validation": str(motion.validation_path),
                "issue_count": len(_lab_issues(motion)),
            },
            "factory": {
                "status": "PASS" if verified.get("decode_full") else "FAIL",
                "video": str(factory.video),
                "contact_sheet": str(factory.contact_sheet) if factory.contact_sheet else None,
                "report": str(factory.report_json),
                "decode_ok": factory.result.get("decode_ok"),
                "determinism": determinism,
            },
        },
        "artifacts": {
            "source_text": str(output_dir / "input" / "story.txt"),
            "story_spec": str(story.spec_path),
            "story_project": str(story.project_path),
            "story_validation": str(story.validation_path),
            "lab_scene": str(motion.lab_scene_path),
            "motion_plan": str(motion.plan_path),
            "lab_validation": str(motion.validation_path),
            "storyboard": str(factory.storyboard_path),
            "compiled_scene": str(factory.scene_path),
            "video": str(factory.video),
            "contact_sheet": str(factory.contact_sheet) if factory.contact_sheet else None,
            "factory_report": str(factory.report_json),
        },
        "checks": checks,
        "limitations": [
            "Текстовый парсер ограничен поддерживаемыми персонажами и глаголами; это не LLM.",
            "Animation Lab передаёт в Factory временную траекторию (linear/bezier3/waypoints/arc) в нормализованных координатах; вертикаль отображается ограниченным подъёмом корпуса, а не полноценным 3D-перемещением.",
            "Lab привязывает предмет к позиции владельца, Factory привязывает его к кисти; итоговая привязка проверяется по кадрам Factory.",
            "Кисти и пальцы управляются ригом Factory, а не суставными командами Lab.",
            "Пересечения силуэтов проверяются по альфа-маскам кадров; контакт руки с предметом допускается, недоказуемые случаи помечаются явно.",
            "Выход персонажа за кадр ограничен безопасными полями Factory; персонаж доходит до края сцены.",
        ],
    }
    return report


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Интеграционный QC-отчёт",
        "",
        f"- Статус: **{report['status']}**",
        f"- Сгенерировано: {report['generated_at']}",
        f"- Сценарий: {report['input']['text']}",
        "",
        "## Этапы",
        "",
        "| Этап | Статус | Артефакт |",
        "| --- | --- | --- |",
    ]
    for name, stage in report["stages"].items():
        artifact = stage.get("video") or stage.get("plan") or stage.get("project") or ""
        lines.append(f"| {name} | {stage.get('status')} | {artifact} |")
    lines.extend(
        [
            "",
            "## Проверки",
            "",
            "| Проверка | Статус | Сообщение |",
            "| --- | --- | --- |",
        ]
    )
    for check in report["checks"]:
        message = str(check["message"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {check['id']} | {check['status']} | {message} |")
    lines.extend(["", "## Ограничения", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.extend(["", "## Артефакты", ""])
    lines.extend(f"- {key}: `{value}`" for key, value in report["artifacts"].items() if value)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _failure_report(output_dir: Path, stage: str, error: str) -> tuple[Path, Path]:
    report = {
        "schema_version": "1.0.0",
        "integration": "shorts_factory_story_studio_animation_lab",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": STATUS_FAIL,
        "stage": stage,
        "input": {"text": "", "characters": []},
        "stages": {stage: {"status": STATUS_FAIL, "error": error}},
        "error": error,
        "checks": [_check(stage, STATUS_FAIL, error)],
        "limitations": [],
        "artifacts": {},
    }
    report_path = write_json(output_dir / "report.json", report)
    md_path = output_dir / "report.md"
    _write_markdown(report, md_path)
    return report_path, md_path


def run_integrated_create(
    script: str | Path,
    output: str | Path,
    *,
    config: Any = None,
    title: str | None = None,
    overwrite: bool = False,
) -> IntegratedResult:
    """Run text -> Story Studio -> Lab -> Factory and write the QC report."""
    output_dir = Path(output).expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise IntegrationError(
            f"Каталог результатов уже существует и не пуст: {output_dir}. "
            "Укажите новый --output или --overwrite."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    script_path = Path(script) if isinstance(script, (str, Path)) else None
    if script_path is not None and script_path.is_file():
        text = script_path.read_text(encoding="utf-8")
    else:
        text = str(script)
    (output_dir / "input").mkdir(parents=True, exist_ok=True)
    (output_dir / "input" / "story.txt").write_text(text.strip() + "\n", encoding="utf-8")

    stage = "dependencies"
    try:
        paths = IntegrationPaths.discover(getattr(config, "root", None))
        paths.check()
        motion_profile = detect_motion_profile(text)
        stage = "story_studio"
        story = StoryStudioAdapter(paths).create(text, output_dir / "story", title=title)
        stage = "animation_lab"
        motion = AnimationPlanAdapter(paths).build(
            story.project, output_dir / "motion", motion_profile=motion_profile
        )
        storyboard = build_factory_storyboard(story.project, motion.plan)
        trajectory_metrics = {
            "motion_profile": motion_profile,
            "beats": [
                {
                    "beat_id": beat["id"],
                    "actor": beat["actor"],
                    "start": beat["start"],
                    "duration": beat["duration"],
                    **path_metrics(beat["path"]),
                }
                for beat in storyboard["beats"]
                if beat.get("path") and beat["path"].get("kind") != "hold"
            ],
        }
        trajectory_metrics["summary"] = {
            "moving_beats": len(trajectory_metrics["beats"]),
            "max_deviation": max(
                (item["max_deviation"] for item in trajectory_metrics["beats"]), default=0.0
            ),
            "max_curvature_ratio": max(
                (item["curvature_ratio"] for item in trajectory_metrics["beats"]), default=1.0
            ),
            "max_endpoint_error": max(
                (item["endpoint_error"] for item in trajectory_metrics["beats"]), default=0.0
            ),
            "kinds": sorted({item["kind"] for item in trajectory_metrics["beats"]}),
        }
        write_json(output_dir / "motion" / "trajectory_metrics.json", trajectory_metrics)
        stage = "factory"
        factory = ShortsFactoryAdapter(config).render(storyboard, output_dir / "factory")
        report = build_qc_report(
            output_dir=output_dir,
            story=story,
            motion=motion,
            storyboard=storyboard,
            factory=factory,
            source_text=text,
            motion_profile=motion_profile,
        )
        report_path = write_json(output_dir / "report.json", report)
        md_path = output_dir / "report.md"
        _write_markdown(report, md_path)
        return IntegratedResult(
            status=report["status"],
            output_dir=output_dir,
            video=factory.video,
            contact_sheet=factory.contact_sheet,
            report_json=report_path,
            report_md=md_path,
            story=story,
            motion=motion,
            factory=factory,
        )
    except Exception as exc:
        _failure_report(output_dir, stage, str(exc))
        if isinstance(exc, IntegrationError):
            raise
        raise IntegrationError(f"{stage}: {exc}") from exc
