"""Job orchestration: create projects, run stages, resume and repair."""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path
from typing import Any, Iterable

from .animation.engine import AnimationEngine
from .animation.scene import AnimationScene
from .animation.storyboard import compile_storyboard
from .config import Config
from .editing import RenderError, RenderService
from .logging_setup import get_logger, project_logger
from .media import MediaService
from .metadata import MetadataService
from .models import (
    STAGES,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PARTIAL,
    STATUS_QUEUED,
    STATUS_READY,
    STATUS_RUNNING,
    VISUAL_MODES,
    LanguageQC,
    Plan,
    Project,
    QualityCheck,
    Script,
    Source,
)
from .music import MusicService
from .quality import QualityService
from .reporting import ReportService
from .research import ResearchResult, ResearchService
from .scripts import ScriptService
from .storage import Storage
from .subtitles import build_subtitles
from .timeline import Timeline, build as build_timeline, write_narration
from .topics import choose_topic, detect_profile, is_generic_topic, normalize_topic
from .tts import TTSService, load_manifest
from .util import (
    CommandError,
    atomic_write_json,
    atomic_write_text,
    now_iso,
    project_id,
    safe_filename,
    slugify,
)
from .visuals import VisualService

LANGUAGES = ("ru", "en")

#: Stages that a storyboard-driven animated project actually needs. Static
#: research/script/localization/asset/voice/editing stages are skipped.
ANIMATED_STAGES = ("PLANNING", "RENDERING", "METADATA", "QUALITY_CHECK")
FIXABLE_TECHNICAL = {
    "file",
    "container",
    "video_stream",
    "audio_stream",
    "duration",
    "resolution",
    "fps",
    "decode",
    "audio_level",
    "black_start",
    "black_end",
    "profile",
}


class JobError(RuntimeError):
    pass


class JobService:
    def __init__(self, config: Config, store: Storage | None = None) -> None:
        self.config = config
        self.store = store or Storage(config)
        self._owns_store = store is None
        self.logger = get_logger()
        self.reporter = ReportService(config)

    # ------------------------------------------------------------------ create
    def create(
        self,
        topic: str = "",
        *,
        count: int = 1,
        languages: Iterable[str] | None = None,
        duration: float = 40,
        profile: str = "",
        style: str = "",
        pace: str = "",
        script_path: str = "",
        visual_mode: str = "images",
        animation_storyboard: str = "",
        command: str = "",
        run: bool = True,
    ) -> list[Project]:
        count = int(count)
        if count < 1 or count > 10:
            raise CommandError("Количество роликов должно быть от 1 до 10.")
        duration = float(duration)
        if duration < 15 or duration > 180:
            raise CommandError("Длительность должна быть от 15 до 180 секунд.")
        if visual_mode and visual_mode not in VISUAL_MODES:
            raise CommandError(
                f"Неизвестный режим визуала: {visual_mode}. Доступны: {', '.join(VISUAL_MODES)}."
            )
        mode = visual_mode or "images"
        if mode == "animated":
            if not animation_storyboard:
                raise CommandError(
                    "Для visual_mode=animated нужен storyboard: "
                    "--storyboard animation/storyboards/pilot_room_v3.json."
                )
            board = Path(animation_storyboard)
            if not board.is_absolute():
                board = self.config.root / board
            if not board.is_file():
                raise CommandError(f"Storyboard не найден: {board}")
            animation_storyboard = str(board)
            # The v3 vertical slice is authored for the Russian voice track.
            # A separate English adaptation with its own timing is future work.
            langs = ["ru"]
        else:
            langs = self._languages(languages)
        requested = (topic or "").strip()
        if profile and profile not in self.config.profiles:
            raise CommandError(
                f"Неизвестный тематический профиль: {profile}. "
                f"Доступны: {', '.join(sorted(self.config.profiles))}."
            )
        selected_profile = profile or detect_profile(requested, self.config, "science")
        rng = random.Random()

        chosen: list[dict[str, Any]] = []
        used: set[str] = set()
        thematic = self._is_theme(requested, selected_profile)
        for index in range(count):
            base_topic = requested
            if count > 1 and thematic:
                base_topic = ""
            elif count > 1 and index > 0:
                base_topic = ""
            pick = choose_topic(
                self.config,
                self.store,
                base_topic,
                selected_profile,
                rng,
            )
            topic_key = normalize_topic(str(pick.get("topic", "")))
            attempts = 0
            while topic_key in used and attempts < 20:
                pick = choose_topic(self.config, self.store, "", selected_profile, rng)
                topic_key = normalize_topic(str(pick.get("topic", "")))
                attempts += 1
            used.add(topic_key)
            chosen.append(pick)

        projects: list[Project] = []
        for pick in chosen:
            projects.append(
                self._create_one(
                    pick,
                    langs=langs,
                    duration=duration,
                    profile=selected_profile,
                    style=style,
                    pace=pace,
                    script_path=script_path,
                    visual_mode=visual_mode,
                    animation_storyboard=animation_storyboard,
                    command=command,
                )
            )

        if run:
            for project in projects:
                try:
                    self.run(project)
                except JobError as exc:
                    self.logger.error("Задание %s остановлено: %s", project.id, exc)
        return projects

    def _create_one(
        self,
        pick: dict[str, Any],
        *,
        langs: list[str],
        duration: float,
        profile: str,
        style: str,
        pace: str,
        script_path: str,
        visual_mode: str,
        animation_storyboard: str = "",
        command: str,
    ) -> Project:
        topic = str(pick.get("topic", "")).strip()
        profile_data = self.config.profile(profile)
        now = now_iso()
        pid = self._unique_id(topic)
        workspace = self.config.workspace_dir / pid
        output = self.config.output_dir / pid
        (workspace / "logs").mkdir(parents=True, exist_ok=True)
        (workspace / "scripts").mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)

        plan = Plan(
            project_id=pid,
            topic=topic,
            topic_en=str(pick.get("topic_en", "") or ""),
            profile=profile,
            languages=langs,
            duration=duration,
            style=style or str(profile_data.get("style", "auto")),
            pace=pace or str(profile_data.get("pace", "medium")),
            visual_density=str(profile_data.get("visual_density", "medium")),
            visual_mode=visual_mode or "images",
            seed=dict(pick.get("seed") or {}),
            voice_ru=str(self.config.get("tts", "voice_ru", "")),
            voice_en=str(self.config.get("tts", "voice_en", "")),
            script_path=script_path,
            animation_storyboard=animation_storyboard,
            created_at=now,
            notes=(
                [
                    "visual_mode=animated: вертикальный срез рендерит анимационную сцену "
                    "из указанного storyboard. Статический сценарий, локализация и "
                    "раскадровка из текста пока не используются; доступен русский звук."
                ]
                if (visual_mode or "images") == "animated"
                else []
            ),
        )
        if not plan.topic_en:
            plan.topic_en = str((plan.seed or {}).get("en", "") or "")

        params: dict[str, Any] = {
            "requested_topic": topic,
            "profile": profile,
            "languages": langs,
            "duration": duration,
            "style": plan.style,
            "pace": plan.pace,
            "visual_density": plan.visual_density,
            "visual_mode": plan.visual_mode,
            "seed": plan.seed,
            "script_path": script_path,
            "animation_storyboard": animation_storyboard,
            "root": str(self.config.root),
        }
        if plan.visual_mode == "animated" and plan.animation_storyboard:
            try:
                storyboard = json.loads(
                    Path(plan.animation_storyboard).read_text(encoding="utf-8")
                )
                scene_duration = float(storyboard["scene"]["duration"])
            except (OSError, KeyError, TypeError, ValueError):
                scene_duration = 0.0
            if scene_duration > 0:
                plan.duration = scene_duration
                params["duration"] = scene_duration
        project = Project(
            id=pid,
            topic=topic,
            profile=profile,
            languages=langs,
            duration=duration,
            status=STATUS_QUEUED,
            stage="PLANNING",
            workspace=workspace.as_posix(),
            output=output.as_posix(),
            command=command or f'create --topic "{topic}" --duration {duration:g}',
            created_at=now,
            updated_at=now,
            error="",
            params=params,
        )
        self.store.create_project(project)
        atomic_write_text(workspace / "command.txt", project.command + "\n")
        atomic_write_json(workspace / "request.json", project.params)
        atomic_write_json(workspace / "plan.json", plan.to_dict())
        self.store.record_event(project.id, "PLANNING", "info", f"Создано задание: {topic}")
        self.logger.info("Создано задание %s: %s", pid, topic)
        return project

    def _unique_id(self, topic: str) -> str:
        base = project_id(topic)
        candidate = base
        counter = 2
        while self.store.get_project(candidate) or (self.config.workspace_dir / candidate).exists():
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    # --------------------------------------------------------------------- run
    def run(self, project: Project, *, force_from: str = "") -> Project:
        self.config.ensure_tree()
        if project.status == STATUS_READY and not force_from:
            return project
        plan = self._load_plan(project)
        start_index = 0
        if force_from:
            if force_from not in STAGES:
                raise CommandError(f"Неизвестный этап: {force_from}")
            start_index = STAGES.index(force_from)
        project_logger(Path(project.workspace), project.id)
        self.store.set_status(project, STATUS_RUNNING, project.stage)
        self.store.record_event(project.id, "PIPELINE", "info", "Запуск производства")
        try:
            for index, stage in enumerate(STAGES):
                if index < start_index:
                    continue
                if (
                    force_from == ""
                    and start_index == 0
                    and self.store.stage_status(project.id, stage) == "done"
                    and self._artifacts_ok(project, stage, plan)
                ):
                    continue
                self._execute(project, stage, plan)
            self._finish(project, plan)
        except (JobError, CommandError):
            raise
        except Exception as exc:  # pragma: no cover - defensive boundary
            message = f"{type(exc).__name__}: {exc}"
            self.store.set_status(project, STATUS_FAILED, project.stage, message)
            self.store.record_event(project.id, project.stage, "error", message)
            self.logger.exception("Задание %s завершилось ошибкой", project.id)
            raise JobError(message) from exc
        return project

    def _execute(self, project: Project, stage: str, plan: Plan) -> None:
        if plan.visual_mode == "animated" and stage not in ANIMATED_STAGES:
            self.store.start_stage(project, stage)
            self.store.finish_stage(project, stage, "пропущено: режим animated")
            self.store.record_event(project.id, stage, "info", "Этап не требуется для animated")
            return
        handler = {
            "PLANNING": self._stage_planning,
            "RESEARCH": self._stage_research,
            "SCRIPTING": self._stage_scripting,
            "LOCALIZATION": self._stage_localization,
            "ASSETS": self._stage_assets,
            "VOICE": self._stage_voice,
            "EDITING": self._stage_editing,
            "RENDERING": self._stage_rendering,
            "METADATA": self._stage_metadata,
            "QUALITY_CHECK": self._stage_quality,
        }[stage]
        self.store.start_stage(project, stage)
        self.store.record_event(project.id, stage, "info", "Этап начат")
        self.logger.info("[%s] %s", project.id, stage)
        try:
            detail = handler(project, plan) or ""
        except Exception as exc:
            message = f"{stage}: {exc}"
            self.store.fail_stage(project, stage, message)
            self.store.set_status(project, STATUS_FAILED, stage, message)
            self.store.record_event(project.id, stage, "error", message)
            self.logger.error("[%s] %s", project.id, message)
            raise JobError(message) from exc
        self.store.finish_stage(project, stage, str(detail)[:500])
        self.store.record_event(project.id, stage, "info", f"Этап завершён: {detail}")
        self.logger.info("[%s] %s завершён: %s", project.id, stage, detail)

    # ------------------------------------------------------------------ stages
    def _stage_planning(self, project: Project, plan: Plan) -> str:
        environment = self.reporter.environment()
        atomic_write_json(Path(project.workspace) / "environment.json", environment)
        self._save_plan(project, plan)
        return f"{plan.topic} / {plan.profile} / {plan.duration:g} с"

    def _stage_research(self, project: Project, plan: Plan) -> str:
        workspace = Path(project.workspace)
        result = self._load_research(workspace, plan)
        if result is None:
            result = ResearchService(self.config).collect(plan, workspace)
        if result.topic_en and result.topic_en != plan.topic_en:
            plan.topic_en = result.topic_en
            self._save_plan(project, plan)
        return f"{len(result.sources)} источников, {len(result.corpus)} символов"

    def _stage_scripting(self, project: Project, plan: Plan) -> str:
        workspace = Path(project.workspace)
        sources = self._sources(workspace)
        if not sources:
            raise JobError("Нет источников для сценария")
        service = ScriptService(self.config)
        primary = "ru" if "ru" in plan.languages else "en"
        script = service.generate(plan, sources, primary, workspace)
        if not script.scenes:
            raise JobError("Сценарий получился пустым")
        return f"{primary}: {script.word_count} слов, {len(script.scenes)} сцен, {script.engine}"

    def _stage_localization(self, project: Project, plan: Plan) -> str:
        workspace = Path(project.workspace)
        scripts = ScriptService(self.config)
        if "ru" in plan.languages:
            ru = scripts.load(workspace, "ru")
            if ru is None:
                raise JobError("Русский сценарий не найден")
            if "en" in plan.languages:
                en = scripts.load(workspace, "en")
                if en is None or not en.scenes:
                    en = scripts.adapt(plan, self._sources(workspace), ru, workspace)
                return f"RU {ru.word_count} слов; EN {en.word_count} слов, {en.engine}"
            return f"RU {ru.word_count} слов"
        en = scripts.load(workspace, "en")
        if en is None:
            raise JobError("Английский сценарий не найден")
        return f"EN {en.word_count} слов"

    def _stage_assets(self, project: Project, plan: Plan) -> str:
        workspace = Path(project.workspace)
        media = MediaService(self.config)
        visuals = VisualService(self.config)
        profile = self.config.profile(plan.profile)
        details: list[str] = []
        for lang in plan.languages:
            script = self._script(workspace, lang)
            manifest = media.fetch_for_script(
                script, plan.topic_en or plan.topic, plan.profile, lang, workspace
            )
            visual_manifest = visuals.build(script, manifest, profile, workspace)
            cards = sum(1 for item in manifest.get("scenes", []) if item.get("kind") == "card")
            details.append(f"{lang.upper()}: {len(visual_manifest.get('scenes', []))} сцен, карточек {cards}")
        return "; ".join(details)

    def _stage_voice(self, project: Project, plan: Plan) -> str:
        workspace = Path(project.workspace)
        tts = TTSService(self.config)
        details: list[str] = []
        for lang in plan.languages:
            script = self._script(workspace, lang)
            manifest = load_manifest(workspace, lang)
            override = plan.voice_ru if lang == "ru" else plan.voice_en
            if not manifest or manifest.get("script_sha") != __import__("hashlib").sha256(
                script.all_text.encode("utf-8")
            ).hexdigest():
                manifest = tts.generate(script, lang, workspace, voice_override=override)
            timeline = build_timeline(script, manifest, self.config, target=plan.duration)
            narration = write_narration(workspace, lang, manifest, timeline)
            atomic_write_json(Path(project.workspace) / "audio" / lang / "timeline.json", timeline.to_dict())
            details.append(f"{lang.upper()}: речь {timeline.total_audio:.1f} с, видео {timeline.total_video:.1f} с")
            if not narration.exists():
                raise JobError(f"Не создана озвучка {lang}")
        return "; ".join(details)

    def _stage_editing(self, project: Project, plan: Plan) -> str:
        workspace = Path(project.workspace)
        profile = self.config.profile(plan.profile)
        music = MusicService(self.config)
        details: list[str] = []
        for lang in plan.languages:
            script = self._script(workspace, lang)
            visual_manifest = self._visuals(workspace, lang)
            self._apply_templates(script, visual_manifest)
            manifest = load_manifest(workspace, lang)
            timeline = self._timeline(workspace, lang)
            if not manifest or not timeline:
                raise JobError(f"Нет тайминга озвучки {lang}. Запусти этап VOICE.")
            payload = build_subtitles(script, manifest, timeline, workspace, self.config)
            track = music.generate(profile, timeline.total_video, workspace, lang)
            atomic_write_json(
                workspace / "edit" / f"{lang}.json",
                {
                    "lang": lang,
                    "music": track,
                    "subtitles": payload,
                    "timeline": timeline.to_dict(),
                    "generated_at": now_iso(),
                },
            )
            details.append(f"{lang.upper()}: {payload.get('cue_count', 0)} субтитров, музыка {'да' if track else 'нет'}")
        return "; ".join(details)

    def _stage_rendering(self, project: Project, plan: Plan) -> str:
        if plan.visual_mode == "animated":
            detail = self._render_animated(project, plan)
            self._save_params(project)
            return detail
        details: list[str] = []
        for lang in plan.languages:
            result = self._render_lang(project, plan, lang)
            details.append(f"{lang.upper()}: {result.duration:.1f} с, {result.size_bytes / 1_048_576:.1f} МБ")
        project.params["videos"] = {
            lang: self._output_video(project, plan, lang).as_posix() for lang in plan.languages
        }
        self._save_params(project)
        return "; ".join(details)

    # ------------------------------------------------------------- animated mode
    @staticmethod
    def _srt_timestamp(seconds: float) -> str:
        total = max(0, int(round(seconds * 1000)))
        hours, rest = divmod(total, 3_600_000)
        minutes, rest = divmod(rest, 60_000)
        secs, millis = divmod(rest, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _render_animated(self, project: Project, plan: Plan) -> str:
        """Render an authored storyboard through the real animation engine.

        This is the v3 vertical slice: the storyboard owns choreography and
        Russian voice lines, while the project still owns checkpoints, metadata,
        subtitles and quality gates.  Static images are never substituted.
        """
        workspace = Path(project.workspace)
        output = Path(project.output)
        source = Path(plan.animation_storyboard)
        if not source.is_file():
            raise JobError(f"Сториборд анимации не найден: {source}")
        try:
            document = json.loads(source.read_text(encoding="utf-8"))
            compiled = compile_storyboard(document)
            scene = AnimationScene.from_dict(compiled)
            scene.resolve_dependencies()
            scene.validate()
        except Exception as exc:
            raise JobError(f"Сториборд анимации не прошёл проверку: {exc}") from exc
        if scene.duration > 60:
            raise JobError("Animated-вертикальный срез поддерживает сцены до 60 секунд.")
        animation_dir = workspace / "animation"
        animation_dir.mkdir(parents=True, exist_ok=True)
        compiled_path = animation_dir / "scene.compiled.json"
        atomic_write_json(compiled_path, compiled)
        shutil.copy2(source, animation_dir / "storyboard.source.json")
        result = AnimationEngine(self.config).render(compiled_path, animation_dir)
        if not result.decode_ok:
            raise JobError("Анимация не прошла полное декодирование MP4.")
        motion = result.motion_checks or {}
        if not motion.get("limbs_move_without_camera"):
            raise JobError("Анимация не прошла проверку движения конечностей.")
        attachment = result.attachment_checks or {}
        if attachment.get("enabled") and not attachment.get("verified"):
            raise JobError("Анимация не прошла проверку привязки предметов.")

        reports = output / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (workspace / "scripts").mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            workspace / "scripts" / "ru.json",
            {
                "title": plan.topic or scene.title,
                "language": "ru",
                "scenes": [],
                "animated": True,
                "storyboard": source.as_posix(),
            },
        )

        video = self._output_video(project, plan, "ru")
        video.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result.video, video)
        if result.report_json:
            shutil.copy2(result.report_json, reports / "animation_ru.json")
        if result.contact_sheet:
            shutil.copy2(result.contact_sheet, reports / "contact_sheet.jpg")

        subtitles = workspace / "subtitles"
        subtitles.mkdir(parents=True, exist_ok=True)
        speech = {
            round(float(item.get("start", -1.0)), 3): float(item.get("duration", 0.0))
            for item in (result.lip_sync or {}).get("lines", [])
        }
        blocks: list[str] = []
        for index, line in enumerate(scene.voice_lines, 1):
            duration = speech.get(round(line.start, 3), 0.0)
            end = min(scene.duration, line.start + max(0.45, duration))
            blocks.append(
                f"{index}\n{self._srt_timestamp(line.start)} --> "
                f"{self._srt_timestamp(end)}\n{line.text}\n"
            )
        (subtitles / "ru.srt").write_text("\n".join(blocks), encoding="utf-8")

        project.params["videos"] = {"ru": video.as_posix()}
        project.params["animation"] = {
            "storyboard": source.as_posix(),
            "duration": scene.duration,
            "frames": result.frames,
            "size_bytes": result.size_bytes,
            "report": (reports / "animation_ru.json").as_posix(),
            "contact_sheet": (reports / "contact_sheet.jpg").as_posix(),
        }
        return (
            f"RU: {scene.duration:.1f} с, {result.size_bytes / 1_048_576:.1f} МБ, "
            f"{result.frames} кадров"
        )

    def _metadata_animated(self, project: Project, plan: Plan) -> str:
        workspace = Path(project.workspace)
        output = Path(project.output)
        (output / "subtitles").mkdir(parents=True, exist_ok=True)
        (output / "metadata").mkdir(parents=True, exist_ok=True)
        video = self._output_video(project, plan, "ru")
        payload = {
            "title": plan.topic or "Анимационный пилот",
            "topic": plan.topic,
            "language": "ru",
            "duration": plan.duration,
            "video": video.as_posix(),
            "animated": True,
            "storyboard": plan.animation_storyboard,
            "contact_sheet": (output / "reports" / "contact_sheet.jpg").as_posix(),
            "report": (output / "reports" / "animation_ru.json").as_posix(),
        }
        atomic_write_json(output / "metadata" / "metadata_ru.json", payload)
        srt = workspace / "subtitles" / "ru.srt"
        if srt.exists():
            shutil.copy2(srt, output / "subtitles" / "ru.srt")
        return f"RU: {payload['title']}"

    def _quality_animated(self, project: Project, plan: Plan) -> str:
        output = Path(project.output)
        report_path = output / "reports" / "animation_ru.json"
        if not report_path.exists():
            raise JobError("Отчёт анимации не найден, контроль качества невозможен.")
        document = json.loads(report_path.read_text(encoding="utf-8"))
        verified = document.get("verified") or {}
        motion = verified.get("motion") or {}
        attachment = verified.get("attachment") or {}
        framing = verified.get("framing") or {}
        lip_sync = verified.get("lip_sync") or {}
        assets = (document.get("assets") or {}).get("characters") or []
        decode_ok = bool(verified.get("decode_full"))
        motion_ok = bool(motion.get("limbs_move_without_camera"))
        attachment_ok = (not attachment.get("enabled")) or bool(attachment.get("verified"))
        framing_ok = bool(framing.get("ok"))
        assets_ok = bool(assets) and all(
            item.get("asset_system") != "legacy_primitives" for item in assets
        )
        lip_ok = (not lip_sync.get("enabled")) or bool(lip_sync.get("verified"))
        checks = [
            QualityCheck(
                "decode", "Полное декодирование", "mandatory", decode_ok,
                "MP4 декодируется полностью" if decode_ok else "Ошибка декодирования",
            ),
            QualityCheck(
                "motion", "Движение конечностей", "mandatory", motion_ok,
                "Движение подтверждено" if motion_ok else "Конечности не двигаются",
            ),
            QualityCheck(
                "attachment", "Привязка предметов", "mandatory", attachment_ok,
                "Предметы удерживаются" if attachment_ok else "Предмет отрывается",
            ),
            QualityCheck(
                "framing", "Кадрирование", "mandatory", framing_ok,
                "Персонажи в кадре" if framing_ok else "; ".join(framing.get("issues") or []),
            ),
            QualityCheck(
                "assets", "Графические ассеты", "mandatory", assets_ok,
                "SVG-ассеты загружены" if assets_ok else "Использованы legacy-примитивы",
            ),
            QualityCheck(
                "lip_sync", "Липсинк", "mandatory", lip_ok,
                "Синхронизация с речью" if lip_ok else "Речь без артикуляции",
            ),
        ]
        warnings = list(framing.get("issues") or [])
        warnings.extend(
            item.get("warning", "") for item in assets if item.get("warning")
        )
        status = "ready" if not any(check.level == "mandatory" and not check.passed for check in checks) else "review"
        video = self._output_video(project, plan, "ru")
        qc = LanguageQC(
            language="ru",
            status=status,
            checks=checks,
            warnings=[warning for warning in warnings if warning],
            manual_review=True,
            video=video.as_posix(),
            contact_sheet=(output / "reports" / "contact_sheet.jpg").as_posix(),
            generated_at=now_iso(),
        )
        atomic_write_json(output / "reports" / "quality_ru.json", qc.to_dict())
        project.params["qc_repairs"] = []
        self._save_params(project)
        return f"RU: {status}"

    def _stage_metadata(self, project: Project, plan: Plan) -> str:
        if plan.visual_mode == "animated":
            return self._metadata_animated(project, plan)
        workspace = Path(project.workspace)
        output = Path(project.output)
        (output / "subtitles").mkdir(parents=True, exist_ok=True)
        (output / "metadata").mkdir(parents=True, exist_ok=True)
        sources = self._sources(workspace)
        profile = self.config.profile(plan.profile)
        service = MetadataService(self.config)
        details: list[str] = []
        for lang in plan.languages:
            script = self._script(workspace, lang)
            video = self._output_video(project, plan, lang)
            payload = service.build(project, plan, script, sources, workspace, output, video, profile)
            shutil.copy2(workspace / "subtitles" / f"{lang}.srt", output / "subtitles" / f"{lang}.srt")
            shutil.copy2(workspace / "subtitles" / f"{lang}.ass", output / "subtitles" / f"{lang}.ass")
            shutil.copy2(workspace / "scripts" / f"{lang}.md", output / "metadata" / f"script_{lang}.md")
            details.append(f"{lang.upper()}: {payload.get('title', '')}")
        research_report = workspace / "research" / "research.md"
        if research_report.exists():
            shutil.copy2(research_report, output / "metadata" / "sources.md")
        return "; ".join(details)

    def _stage_quality(self, project: Project, plan: Plan) -> str:
        if plan.visual_mode == "animated":
            return self._quality_animated(project, plan)
        workspace = Path(project.workspace)
        output = Path(project.output)
        service = QualityService(self.config)
        results: dict[str, LanguageQC] = {}
        repairs: list[str] = []
        for lang in plan.languages:
            script = self._script(workspace, lang)
            video = self._output_video(project, plan, lang)
            if not video.exists():
                raise JobError(f"Финальное видео {lang} не найдено: {video}")
            qc = service.check(
                project, script, lang, video, workspace, output, plan.duration,
                expected_topic=plan.topic_en if lang == "en" else plan.topic,
            )
            if qc.mandatory_failed:
                fixable = [check.id for check in qc.mandatory_failed if check.id in FIXABLE_TECHNICAL]
                if fixable:
                    repairs.append(f"{lang}: {', '.join(fixable)}")
                    self.store.record_event(
                        project.id, "QUALITY_CHECK", "warning",
                        f"Повторный рендер {lang}: {', '.join(fixable)}",
                    )
                    try:
                        self._render_lang(project, plan, lang)
                    except RenderError as exc:
                        repairs.append(f"{lang}: повторный рендер не удался ({exc})")
                    qc = service.check(
                project, script, lang, video, workspace, output, plan.duration,
                expected_topic=plan.topic_en if lang == "en" else plan.topic,
            )
            results[lang] = qc
        project.params["qc_repairs"] = repairs
        self._save_params(project)
        if not results:
            raise JobError("Контроль качества не выполнены")
        summary = {
            lang: {
                "status": qc.status,
                "mandatory_failed": [check.id for check in qc.mandatory_failed],
                "warnings": len(qc.warnings),
            }
            for lang, qc in results.items()
        }
        atomic_write_json(output / "reports" / "quality_summary.json", summary)
        return "; ".join(f"{lang.upper()}: {qc.status}" for lang, qc in results.items())

    # ------------------------------------------------------------------ finish
    def _finish(self, project: Project, plan: Plan) -> None:
        qcs = [self._load_qc(project, lang) for lang in plan.languages]
        present = [qc for qc in qcs if qc is not None]
        if not present:
            status = STATUS_FAILED
            error = "Результаты контроля качества отсутствуют."
        elif all(qc.status == "ready" for qc in present) and len(present) == len(plan.languages):
            status = STATUS_READY
            error = ""
        else:
            status = STATUS_PARTIAL
            failed = [
                f"{qc.language}: " + ", ".join(check.id for check in qc.mandatory_failed)
                for qc in present
                if qc.mandatory_failed
            ]
            error = "Требуется ручная проверка. " + ("; ".join(failed) if failed else "")
        self.store.set_status(project, status, "QUALITY_CHECK", error.strip())
        self.reporter.write_project(project, plan, present)
        self.store.record_event(project.id, "QUALITY_CHECK", "info", f"Итоговый статус: {status}")
        project.params["finished_at"] = now_iso()
        self._save_params(project)

    # ----------------------------------------------------------------- helpers
    def prepare_retry(self, project: Project, from_stage: str = "") -> Project:
        if project.status == STATUS_RUNNING:
            self.store.set_status(project, STATUS_INTERRUPTED, project.stage,
                                  "Производство было прервано. Продолжите командой retry.")
        start = from_stage
        if not start:
            for stage in STAGES:
                status = self.store.stage_status(project.id, stage)
                if status != "done":
                    start = stage
                    break
        if not start:
            start = "QUALITY_CHECK"
        if start not in STAGES:
            raise CommandError(f"Неизвестный этап: {start}")
        index = STAGES.index(start)
        self.store.reset_stages(project.id, STAGES[index:])
        project.params["retried_from"] = start
        project.params["retried_at"] = now_iso()
        self.store.set_status(project, STATUS_QUEUED, start, "")
        self.store.record_event(project.id, start, "warning", f"Повтор с этапа {start}")
        self._save_params(project)
        return project

    def remake(self, project: Project, **overrides: Any) -> Project:
        plan = self._load_plan(project)
        changed: list[str] = []
        for key in ("style", "pace", "duration", "voice_ru", "voice_en"):
            value = overrides.get(key)
            if value not in (None, ""):
                setattr(plan, key, value)
                project.params[key] = value
                changed.append(key)
        voice_changed = any(key in changed for key in ("voice_ru", "voice_en"))
        visual_changed = any(key in changed for key in ("style", "pace", "duration"))
        if plan.duration != project.duration:
            project.duration = float(plan.duration)
        if changed:
            project.params["last_remake"] = {
                "at": now_iso(),
                "changes": {key: getattr(plan, key) for key in changed},
            }
        self._save_plan(project, plan)
        self._save_params(project)
        from_stage = "VOICE" if voice_changed and not visual_changed else "ASSETS"
        if "duration" in changed:
            self._archive_scripts(project)
            from_stage = "SCRIPTING"
        return self.prepare_retry(project, from_stage)

    @staticmethod
    def _archive_scripts(project: Project) -> None:
        """Preserve old scripts before a duration change forces regeneration."""
        workspace = Path(project.workspace)
        scripts = workspace / "scripts"
        if not scripts.exists():
            return
        target = workspace / "archive" / "scripts" / now_iso().replace(":", "").replace("+", "_")
        moved = False
        for path in list(scripts.glob("*.*")):
            if path.suffix.lower() not in {".json", ".md"}:
                continue
            target.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target / path.name))
            moved = True
        if moved:
            get_logger().info("Старые сценарии сохранены в %s", target)

    def _render_lang(self, project: Project, plan: Plan, lang: str):
        workspace = Path(project.workspace)
        script = self._script(workspace, lang)
        visual_manifest = self._visuals(workspace, lang)
        self._apply_templates(script, visual_manifest)
        timeline = self._timeline(workspace, lang)
        manifest = load_manifest(workspace, lang)
        subtitles = workspace / "subtitles" / f"{lang}.ass"
        narration = workspace / "audio" / lang / "narration.wav"
        music_meta = self._read_json(workspace / "audio" / "music.json") or {}
        music_path = Path(str(music_meta.get("path", ""))) if music_meta.get("path") else None
        if music_path and not music_path.exists():
            music_path = None
        if timeline is None or manifest is None:
            raise RenderError(f"Нет тайминга для {lang}")
        if not subtitles.exists():
            raise RenderError(f"Нет субтитров для {lang}")
        output = workspace / "renders" / f"{lang}.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        result = RenderService(self.config).assemble(
            script,
            timeline,
            visual_manifest,
            narration,
            music_path,
            subtitles,
            plan.style,
            plan.pace,
            workspace,
            output,
        )
        final = self._output_video(project, plan, lang)
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output, final)
        atomic_write_json(
            Path(project.output) / "reports" / f"render_{lang}.json",
            {
                "language": lang,
                "video": result.video,
                "final": final.as_posix(),
                "duration": result.duration,
                "width": result.width,
                "height": result.height,
                "fps": result.fps,
                "size_bytes": result.size_bytes,
                "scenes": result.scenes,
                "warnings": result.warnings,
                "generated_at": result.generated_at,
            },
        )
        return result

    def _output_video(self, project: Project, plan: Plan, lang: str) -> Path:
        script_path = Path(project.workspace) / "scripts" / f"{lang}.json"
        title = ""
        try:
            data = json.loads(script_path.read_text(encoding="utf-8"))
            title = str(data.get("title", ""))
        except (OSError, ValueError):
            pass
        slug = safe_filename(slugify(title), max_len=70) or f"short_{lang}"
        return Path(project.output) / lang.upper() / f"{lang.upper()}_{slug}.mp4"

    def _load_plan(self, project: Project) -> Plan:
        path = Path(project.workspace) / "plan.json"
        if path.exists():
            try:
                return Plan.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                raise JobError(f"Не удалось прочитать план задания: {exc}") from exc
        raise JobError("План задания не найден")

    def _save_plan(self, project: Project, plan: Plan) -> None:
        atomic_write_json(Path(project.workspace) / "plan.json", plan.to_dict())

    def _save_params(self, project: Project) -> None:
        atomic_write_json(Path(project.workspace) / "params.json", project.params)
        self.store.update_project(project)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _load_research(self, workspace: Path, plan: Plan) -> ResearchResult | None:
        path = workspace / "research" / "sources.json"
        if not path.exists():
            return None
        data = self._read_json(path)
        if not data:
            return None
        sources = [Source.from_dict(item) for item in data.get("sources", [])]
        if not sources:
            return None
        corpus_path = workspace / "research" / "corpus.txt"
        corpus = corpus_path.read_text(encoding="utf-8") if corpus_path.exists() else ""
        return ResearchResult(
            sources=sources,
            corpus=corpus,
            topic_en=str(data.get("topic_en", "") or plan.topic_en),
            warnings=list(data.get("warnings", [])),
            queries=list(data.get("queries", [])),
            shared_numbers=list(data.get("shared_numbers", [])),
        )

    def _sources(self, workspace: Path) -> list[Source]:
        data = self._read_json(workspace / "research" / "sources.json") or {}
        return [Source.from_dict(item) for item in data.get("sources", [])]

    def _script(self, workspace: Path, lang: str) -> Script:
        script = ScriptService(self.config).load(workspace, lang)
        if script is None or not script.scenes:
            raise JobError(f"Сценарий {lang} не найден")
        return script

    def _visuals(self, workspace: Path, lang: str) -> dict[str, Any]:
        data = self._read_json(workspace / "assets" / lang / "visuals.json")
        if not data:
            raise JobError(f"Визуальный план {lang} не найден")
        return data

    @staticmethod
    def _apply_templates(script: Script, visual_manifest: dict[str, Any]) -> None:
        mapping = {str(item.get("scene_id")): item for item in visual_manifest.get("scenes", [])}
        for scene in script.scenes:
            item = mapping.get(scene.id)
            if item and item.get("template"):
                scene.template = str(item["template"])

    def _timeline(self, workspace: Path, lang: str) -> Timeline | None:
        path = workspace / "audio" / lang / "timeline.json"
        data = self._read_json(path)
        return Timeline.from_dict(data) if data else None

    def _load_qc(self, project: Project, lang: str) -> LanguageQC | None:
        data = self._read_json(Path(project.output) / "reports" / f"quality_{lang}.json")
        if not data:
            return None
        from .models import QualityCheck

        checks = [
            QualityCheck(
                id=str(item.get("id", "")),
                label=str(item.get("label", "")),
                level=str(item.get("level", "")),
                passed=bool(item.get("passed")),
                detail=str(item.get("detail", "")),
            )
            for item in data.get("checks", [])
        ]
        return LanguageQC(
            language=str(data.get("language", lang)),
            status=str(data.get("status", "failed")),
            checks=checks,
            warnings=list(data.get("warnings", [])),
            manual_review=bool(data.get("manual_review", True)),
            video=str(data.get("video", "")),
            contact_sheet=str(data.get("contact_sheet", "")),
            generated_at=str(data.get("generated_at", "")),
        )

    def _artifacts_ok(self, project: Project, stage: str, plan: Plan) -> bool:
        workspace = Path(project.workspace)
        if stage == "PLANNING":
            return (workspace / "plan.json").exists()
        if stage == "RESEARCH":
            return (workspace / "research" / "sources.json").exists()
        if stage == "SCRIPTING":
            return any(
                (workspace / "scripts" / f"{lang}.json").exists() for lang in plan.languages
            )
        if stage == "LOCALIZATION":
            return all((workspace / "scripts" / f"{lang}.json").exists() for lang in plan.languages)
        if stage == "ASSETS":
            return all(
                (workspace / "assets" / lang / "visuals.json").exists()
                for lang in plan.languages
            )
        if stage == "VOICE":
            return all(
                (workspace / "audio" / lang / "narration.wav").exists()
                and (workspace / "audio" / lang / "timeline.json").exists()
                for lang in plan.languages
            )
        if stage == "EDITING":
            return all(
                (workspace / "subtitles" / f"{lang}.ass").exists() for lang in plan.languages
            )
        if stage == "RENDERING":
            return all(self._output_video(project, plan, lang).exists() for lang in plan.languages)
        if stage == "METADATA":
            return all(
                (Path(project.output) / "metadata" / f"{lang}.json").exists()
                for lang in plan.languages
            )
        if stage == "QUALITY_CHECK":
            return all(
                (Path(project.output) / "reports" / f"quality_{lang}.json").exists()
                for lang in plan.languages
            )
        return True

    # -------------------------------------------------------------- accessors
    def latest(self, statuses: Iterable[str] | None = None) -> Project | None:
        return self.store.latest_project(statuses)

    def write_report(self, project: Project) -> Path:
        plan = self._load_plan(project)
        qcs = [qc for lang in plan.languages if (qc := self._load_qc(project, lang)) is not None]
        return self.reporter.write_project(project, plan, qcs)

    def get(self, project_id_value: str = "", latest: bool = False) -> Project:
        project = self.store.get_project(project_id_value) if project_id_value else None
        if project is None and latest:
            project = self.store.latest_project()
        if project is None:
            raise CommandError("Задание не найдено. Укажите ID или используйте --latest.")
        return project

    def list(self, limit: int = 50) -> list[Project]:
        return self.store.list_projects(limit)

    def resume_interrupted(self) -> list[Project]:
        self.store.import_workspace_projects()
        self.store.recover_interrupted()
        projects = self.store.list_projects(statuses=[STATUS_INTERRUPTED, STATUS_QUEUED])
        resumed: list[Project] = []
        for project in projects:
            try:
                self.prepare_retry(project)
                self.run(project)
                resumed.append(project)
            except JobError as exc:
                get_logger().error(
                    "Не удалось продолжить проект %s: %s", project.id, exc
                )
                continue
        return projects

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    @staticmethod
    def _languages(values: Iterable[str] | None) -> list[str]:
        if not values:
            return ["ru", "en"]
        result: list[str] = []
        for value in values:
            for part in str(value).replace(",", " ").split():
                code = part.strip().lower()
                if code not in LANGUAGES:
                    raise CommandError(f"Поддерживаются языки ru и en, получено: {value}")
                if code not in result:
                    result.append(code)
        return [code for code in LANGUAGES if code in result] or ["ru", "en"]

    def _is_theme(self, topic: str, profile: str) -> bool:
        normalized = normalize_topic(topic)
        if not normalized or is_generic_topic(normalized):
            return True
        data = self.config.profile(profile)
        keywords = {
            normalize_topic(str(item))
            for key in ("keywords_ru", "keywords_en")
            for item in data.get(key, [])
        }
        keywords.add(normalize_topic(str(data.get("label_ru", ""))))
        keywords.add(normalize_topic(str(data.get("label_en", ""))))
        return normalized in keywords
