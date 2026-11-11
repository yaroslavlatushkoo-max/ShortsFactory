"""Command line interface for ShortsFactory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .config import ConfigError, load_config
from .doctor import Doctor
from .jobs import JobError, JobService
from .logging_setup import setup_logging
from .models import STAGES
from .nlp import clarification, parse
from .storage import Storage
from .util import CommandError, human_size, open_path


def _stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def build_parser() -> argparse.ArgumentParser:
    from .generative.animation_plan import ENGINE_AUTO, ENGINE_CHOICES
    from .generative.freeanim import DEFAULT_QUALITIES

    parser = argparse.ArgumentParser(
        prog="shortsfactory",
        description=(
            "ShortsFactory Pro — локальная фабрика вертикальных видео.\n"
            "Создаёт сценарии, озвучку, субтитры, монтаж и отчёты для RU и EN."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            '  shortsfactory create --topic "Космос" --duration 40\n'
            '  shortsfactory create --topic "История" --languages ru en\n'
            '  shortsfactory batch --topic "Технологии" --count 3\n'
            "  shortsfactory status\n"
            "  shortsfactory inspect --latest\n"
            "  shortsfactory retry --latest\n"
            "  shortsfactory report --latest\n"
            "  shortsfactory preview --latest\n"
            "  shortsfactory doctor\n"
            '  shortsfactory ask "Создай два Shorts о космосе"\n\n'
            "Запуск из PowerShell: .\\run.ps1 <команда>\n"
            "Запуск без параметров: .\\ShortsFactory.cmd"
        ),
    )
    parser.add_argument("--version", action="version", version=f"ShortsFactory {__version__}")
    parser.add_argument("--root", default="", help="Корень проекта (по умолчанию D:\\ShortsFactory)")
    parser.add_argument("--json", action="store_true", help="Машиночитаемый вывод, где применимо")

    sub = parser.add_subparsers(dest="command")

    create = sub.add_parser("create", help="Создать один ролик")
    create.add_argument("--topic", required=True, help="Тема или широкая тематика")
    create.add_argument("--duration", type=float, default=40, help="Длительность в секундах (15–180)")
    create.add_argument("--languages", nargs="+", default=["ru", "en"], help="Языки: ru en")
    create.add_argument("--profile", default="", help="Профиль тематики (science, history, ...)")
    create.add_argument("--style", default="", help="Визуальный стиль (auto по профилю)")
    create.add_argument("--pace", default="", help="Темп: slow, medium, fast")
    create.add_argument("--script", default="", help="Готовый сценарий .md/.json в input/")
    create.add_argument("--visual-mode", default="images", choices=["images", "animated"],
                        help="Режим визуала: images (статичные картинки) или animated")
    create.add_argument("--storyboard", default="",
                        help="Сториборд .json для animated (обязателен в этом режиме)")
    create.add_argument("--episodes", type=int, default=0,
                        help="Число серий (0 = без разбиения)")
    create.add_argument("--no-run", action="store_true", help="Только создать задание, без производства")

    batch = sub.add_parser("batch", help="Создать несколько роликов")
    batch.add_argument("--topic", required=True, help="Тема или широкая тематика")
    batch.add_argument("--count", type=int, default=3, help="Количество роликов (1–10)")
    batch.add_argument("--duration", type=float, default=40, help="Длительность каждого ролика")
    batch.add_argument("--languages", nargs="+", default=["ru", "en"], help="Языки: ru en")
    batch.add_argument("--profile", default="", help="Профиль тематики")
    batch.add_argument("--style", default="", help="Визуальный стиль")
    batch.add_argument("--pace", default="", help="Темп")
    batch.add_argument("--no-run", action="store_true", help="Только создать задания")

    ask = sub.add_parser("ask", help="Естественно-языковая команда")
    ask.add_argument("text", nargs="+", help='Например: "Создай два Shorts о космосе"')
    ask.add_argument("--dry-run", action="store_true", help="Показать разобранный план без запуска")
    ask.add_argument("--yes", action="store_true", help="Не задавать уточняющий вопрос")

    status = sub.add_parser("status", help="Показать задания")
    status.add_argument("--limit", type=int, default=20, help="Сколько заданий показать")
    status.add_argument("--all", action="store_true", help="Показать все статусы")

    inspect = sub.add_parser("inspect", help="Подробности задания")
    inspect.add_argument("project_id", nargs="?", default="")
    inspect.add_argument("--latest", action="store_true", help="Последнее задание")

    retry = sub.add_parser("retry", help="Повторить или продолжить задание")
    retry.add_argument("project_id", nargs="?", default="")
    retry.add_argument("--latest", action="store_true", help="Последнее задание")
    retry.add_argument("--from", dest="from_stage", default="", choices=["", *STAGES],
                       help="Начать с указанного этапа")

    report = sub.add_parser("report", help="Показать отчёт задания")
    report.add_argument("project_id", nargs="?", default="")
    report.add_argument("--latest", action="store_true", help="Последнее задание")
    report.add_argument("--open", action="store_true", help="Открыть отчёт в программе по умолчанию")

    preview = sub.add_parser("preview", help="Открыть превью и видео")
    preview.add_argument("project_id", nargs="?", default="")
    preview.add_argument("--latest", action="store_true", help="Последнее задание")
    preview.add_argument("--no-open", action="store_true", help="Только показать пути")

    doctor = sub.add_parser("doctor", help="Проверить окружение")
    doctor.add_argument("--full", action="store_true", help="Синтезировать тестовую речь и проверить LLM")
    doctor.add_argument("--json", action="store_true", help="JSON для автоматизации")

    resume = sub.add_parser("resume", help="Продолжить незавершённые задания")
    resume.add_argument("--latest", action="store_true", help="Только последнее незавершённое")

    remake = sub.add_parser("remake", help="Переделать последний ролик")
    remake.add_argument("project_id", nargs="?", default="")
    remake.add_argument("--latest", action="store_true", help="Последнее задание")
    remake.add_argument("--pace", default="", help="Новый темп")
    remake.add_argument("--style", default="", help="Новый стиль")
    remake.add_argument("--duration", type=float, default=0, help="Новая длительность")
    remake.add_argument("--voice-en", default="", help="Другой голос EN (имя .onnx)")
    remake.add_argument("--voice-ru", default="", help="Другой голос RU (имя .onnx)")

    animate = sub.add_parser("animate", help="Отрендерить анимированную 2D-сцену (Animation Engine v4)")
    animate.add_argument("--scene", default="", help="Путь к JSON-описанию сцены")
    animate.add_argument("--storyboard", default="", help="Путь к JSON-сториборду (компилируется в сцену)")
    animate.add_argument("--out", default="", help="Папка результата (по умолчанию output/_animation_engine_v1)")
    animate.add_argument("--open", action="store_true", help="Открыть итоговое видео")
    animate.add_argument("--json", action="store_true", dest="json_out", help="Машиночитаемый JSON-вывод")

    integrated = sub.add_parser(
        "integrated-create",
        help="Сквозной конвейер: текст → Story Studio → Animation Lab → Animation Engine",
    )
    integrated.add_argument("--script", required=True, help="Текст сценария или путь к .txt/.md файлу")
    integrated.add_argument("--output", default="", help="Папка результата (по умолчанию output/integrated_pilot)")
    integrated.add_argument("--title", default="", help="Необязательный заголовок истории")
    integrated.add_argument("--overwrite", action="store_true", help="Разрешить запись в непустую папку")
    integrated.add_argument("--json", action="store_true", dest="json_out", help="Машиночитаемый JSON-вывод")

    ai = sub.add_parser(
        "ai-create",
        help="Генеративный/гибридный конвейер: текст→сценарии→кинематограф→видео→аудио→MP4",
    )
    ai.add_argument("--script", required=True, help="Текст сценария или путь к .txt/.md")
    ai.add_argument("--output", default="", help="Папка результата (по умолчанию output/mission3_ai_video)")
    ai.add_argument("--title", default="", help="Заголовок истории")
    ai.add_argument(
        "--preset", default="cinematic3d",
        help="Пресет: cinematic3d/stylized_film/premium2d/realistic_env/painterly_fantasy/documentary",
    )
    ai.add_argument("--mode", default="generative", choices=["procedural", "generative", "hybrid"])
    ai.add_argument(
        "--provider", default="disabled",
        help="Видеобэкенд (псевдоним --video-provider): disabled/veo/kling/fal",
    )
    ai.add_argument("--video-provider", default="",
                    help="Видеопровайдер: disabled/veo/kling/fal (приоритетнее --provider)")
    ai.add_argument("--video-model", default="", help="Модель видеопровайдера (если поддерживается)")
    ai.add_argument("--text-provider", default="",
                    help="Текстовый провайдер: ollama/gemini/openai_compat (пусто → авто)")
    ai.add_argument("--text-model", default="", help="Модель текстового провайдера")
    ai.add_argument("--voice-provider", default="",
                    help="Голосовой провайдер: piper/elevenlabs (пусто → piper)")
    ai.add_argument("--dry-run", action="store_true",
                    help="Построить план и оценить стоимость БЕЗ генерации/платных запросов")
    ai.add_argument("--max-generations", type=int, default=0,
                    help="Лимит числа генерируемых сцен (0 = без лимита)")
    ai.add_argument("--reference", action="append", default=[],
                    help="Путь к reference-изображению (можно повторять)")
    ai.add_argument("--no-audio", action="store_true", help="Не добавлять озвучку")
    ai.add_argument("--poll-interval", type=float, default=5.0, help="Интервал опроса провайдера, с")
    ai.add_argument("--poll-timeout", type=float, default=600.0, help="Таймаут ожидания рендера, с")
    ai.add_argument("--overwrite", action="store_true", help="Разрешить запись в непустую папку")
    ai.add_argument("--json", action="store_true", dest="json_out", help="Машиночитаемый JSON-вывод")

    free = sub.add_parser(
        "free-create",
        help="Полностью бесплатный локальный конвейер: идея→сценарий→анимация→озвучка→субтитры→MP4 (без облаков)",
    )
    free.add_argument("--script", required=True, help="Идея или путь к .txt/.md файлу")
    free.add_argument("--title", default="", help="Заголовок истории")
    free.add_argument("--output", default="", help="Папка результата (по умолчанию output/free_demo)")
    free.add_argument("--engine", default=ENGINE_AUTO, choices=ENGINE_CHOICES,
                      help="Движок: procedural (2D Animation Engine) / blender / "
                           "voxel / asset / skinned (скин-риг Milo/Nina) / auto")
    free.add_argument("--ollama-model", default="", help="Локальная модель Ollama для истории")
    free.add_argument("--piper-voice", default="", help="Голос Piper (пусто → по умолчанию)")
    free.add_argument("--duration", type=float, default=15.0, help="Длительность, с (6..30)")
    free.add_argument("--quality", default="high", choices=DEFAULT_QUALITIES,
                      help="Качество рендера")
    free.add_argument("--resume", action="store_true",
                      help="Возобновить: переиспользовать готовый рендер")
    free.add_argument("--overwrite", action="store_true",
                      help="Разрешить запись в непустую папку")
    free.add_argument("--json", action="store_true", dest="json_out", help="Машиночитаемый JSON-вывод")

    bfilm = sub.add_parser(
        "blocky-film",
        help="Блочный мультфильм из JSON-сцены (Стив/Алекс, реквизит, камера, SFX) — без изменения кода",
    )
    bfilm.add_argument("--scene", required=True,
                       help="Путь к JSON-сцене (см. scenes/blocky/*.json)")
    bfilm.add_argument("--output", default="", help="Папка результата (по умолчанию output/blocky_<название>)")
    bfilm.add_argument("--json", action="store_true", dest="json_out", help="Машиночитаемый JSON-вывод")

    bcreate = sub.add_parser(
        "blocky-create",
        help="Мультфильм из текстовой идеи: storyboard → сцены → рендер → склейка",
    )
    bcreate.add_argument("--idea", default="",
                         help="Идея/сценарий текстом (или путь к .txt)")
    bcreate.add_argument("--storyboard", default="",
                         help="Готовая раскадровка JSON: пропустить LLM")
    bcreate.add_argument("--output", default="", help="Папка результата (по умолчанию output/blocky_films/<название>)")
    bcreate.add_argument("--scenes", type=int, default=3, help="Сколько сцен запросить у модели")
    bcreate.add_argument(
        "--episodes", type=int, default=1,
        help="Разбить сториборд на N серий (папки ep_01..ep_N + series_report.json)",
    )
    bcreate.add_argument("--seconds", type=float, default=0.0, help="Желаемая длительность, с (0 — на усмотрение)")
    bcreate.add_argument("--lang", default="ru", help="Язык озвучки и субтитров (ru/en)")
    bcreate.add_argument("--model", default="", help="Модель Ollama (по умолчанию из config.toml)")
    bcreate.add_argument("--no-voice", action="store_true", help="Без TTS (немой рендер)")
    bcreate.add_argument("--resume", action="store_true", default=True,
                         help="Переиспользовать готовые сцены (включено по умолчанию)")
    bcreate.add_argument("--json", action="store_true", dest="json_out", help="Машиночитаемый JSON-вывод")

    bqc = sub.add_parser(
        "blocky-qc",
        help="QC-гейты E13: пустые кадры, стопы, силуэты, привязка, аудио, субтитры",
    )
    bqc.add_argument("--report", required=True, help="scene_report.json или film_report.json")
    bqc.add_argument("--film", action="store_true", help="Проверить film_report.json")
    bqc.add_argument("--srt", default="", help="Путь к SRT (по умолчанию из отчёта)")
    bqc.add_argument("--payload", default="", help="payload_*.json сцены (для проверки привязки)")
    bqc.add_argument("--json", action="store_true", dest="json_out", help="Машиночитаемый JSON-вывод")

    breg = sub.add_parser(
        "blocky-regress",
        help="Регрессия E14: эталонные сцены и стабильные метрики",
    )
    breg.add_argument("--golden", default="", help="Путь к эталону (по умолчанию scenes/blocky/regression.json)")
    breg.add_argument("--update", action="store_true", help="Перезаписать эталон текущими сценами")
    breg.add_argument("--json", action="store_true", dest="json_out", help="Машиночитаемый JSON-вывод")

    providers = sub.add_parser("providers", help="Список ИИ-провайдеров и их capabilities")
    providers.add_argument("--capability", default="",
                           help="Фильтр по capability: text_generation/story_planning/video_generation/...")
    providers.add_argument("--json", action="store_true", dest="json_out", help="JSON-вывод")

    pcheck = sub.add_parser("providers-check", help="Проверить конфигурацию/доступность провайдеров")
    pcheck.add_argument("--provider", default="", help="Проверить только указанного провайдера")
    pcheck.add_argument("--allow-paid-cost", action="store_true",
                        help="Разрешить бесплатные проверки; платные вызовы ВСЕГДА запрещены")
    pcheck.add_argument("--json", action="store_true", dest="json_out", help="JSON-вывод")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    try:
        config = load_config(Path(args.root) if args.root else None)
        setup_logging(config, level="INFO")
        # Load .env once so registry providers (Gemini/Veo/Kling/...) see it.
        # Real exported env vars win over .env values.
        from .generative.backend import _load_dotenv

        _load_dotenv(config.root)
        store = Storage(config)
        jobs = JobService(config, store)
    except (ConfigError, OSError, ValueError) as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "create":
            projects = jobs.create(
                args.topic,
                count=1,
                languages=args.languages,
                duration=args.duration,
                profile=args.profile,
                style=args.style,
                pace=args.pace,
                script_path=args.script,
                visual_mode=args.visual_mode,
                animation_storyboard=args.storyboard,
                command=" ".join(sys.argv[1:]),
                run=not args.no_run,
            )
            _print_projects(projects, args.json)
            return 0 if all(project.status == "READY" for project in projects) else 3

        if args.command == "batch":
            projects = jobs.create(
                args.topic,
                count=args.count,
                languages=args.languages,
                duration=args.duration,
                profile=args.profile,
                style=args.style,
                pace=args.pace,
                command=" ".join(sys.argv[1:]),
                run=not args.no_run,
            )
            _print_projects(projects, args.json)
            return 0 if all(project.status == "READY" for project in projects) else 3

        if args.command == "ask":
            text = " ".join(args.text)
            intent = parse(text, config)
            if args.dry_run:
                print(intent.plan_text())
                for note in intent.notes:
                    print(f"- {note}")
                return 0
            if intent.needs_clarification:
                print(clarification(intent, config))
                return 2
            if intent.action in {"create", "batch"}:
                projects = jobs.create(
                    intent.topic,
                    count=intent.count,
                    languages=intent.languages,
                    duration=intent.duration,
                    profile=intent.profile,
                    style=intent.style if intent.style != "auto" else "",
                    pace=intent.pace,
                    script_path=intent.script_path,
                    command=text,
                    run=True,
                )
                _print_projects(projects, args.json)
                return 0 if all(project.status == "READY" for project in projects) else 3
            if intent.action == "remake":
                project = jobs.get(latest=True)
                overrides: dict[str, Any] = {}
                if "pace" in intent.remake_overrides:
                    overrides["pace"] = intent.remake_overrides["pace"]
                if "duration" in intent.remake_overrides:
                    overrides["duration"] = float(intent.remake_overrides["duration"])
                jobs.remake(project, **overrides)
                jobs.run(project)
                _print_projects([project], args.json)
                return 0 if project.status == "READY" else 3
            return _dispatch_simple(jobs, intent.action, args.json)

        if args.command == "status":
            projects = jobs.list(1000 if args.all else args.limit)
            _print_status(projects, args.json)
            return 0

        if args.command == "inspect":
            project = jobs.get(args.project_id, latest=args.latest or not args.project_id)
            _print_inspect(project, jobs, args.json)
            return 0

        if args.command == "retry":
            project = jobs.get(args.project_id, latest=args.latest or not args.project_id)
            jobs.prepare_retry(project, args.from_stage)
            jobs.run(project)
            _print_projects([project], args.json)
            return 0 if project.status == "READY" else 3

        if args.command == "report":
            project = jobs.get(args.project_id, latest=args.latest or not args.project_id)
            path = jobs.write_report(project)
            print(path)
            if args.json:
                print(json.dumps({"project": project.id, "report": path.as_posix()}, ensure_ascii=False))
            if args.open:
                open_path(path)
            return 0

        if args.command == "preview":
            project = jobs.get(args.project_id, latest=args.latest or not args.project_id)
            paths = _preview_paths(project)
            for path in paths:
                print(path)
            if args.json:
                print(json.dumps([path.as_posix() for path in paths], ensure_ascii=False))
            if not args.no_open:
                if paths:
                    open_path(paths[0])
                else:
                    open_path(Path(project.output))
            return 0

        if args.command == "doctor":
            result = Doctor(config).run(full=args.full)
            if args.json or getattr(args, "json", False):
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            else:
                _print_doctor(result)
            return 0 if result.ok else 1

        if args.command == "resume":
            projects = jobs.store.list_projects(statuses=["INTERRUPTED", "QUEUED"])
            if args.latest and projects:
                projects = projects[:1]
            if not projects:
                print("Незавершённых заданий нет.")
                return 0
            failures = 0
            for project in projects:
                try:
                    jobs.prepare_retry(project)
                    jobs.run(project)
                    _print_projects([project], args.json)
                    if project.status != "READY":
                        failures += 1
                except JobError as exc:
                    print(f"{project.id}: {exc}", file=sys.stderr)
                    failures += 1
            return 0 if failures == 0 else 3

        if args.command == "remake":
            project = jobs.get(args.project_id, latest=args.latest or not args.project_id)
            overrides: dict[str, Any] = {}
            for key in ("pace", "style", "duration", "voice_ru", "voice_en"):
                value = getattr(args, key)
                if value not in ("", 0, None):
                    overrides[key] = value
            jobs.remake(project, **overrides)
            jobs.run(project)
            _print_projects([project], args.json)
            return 0 if project.status == "READY" else 3

        if args.command == "animate":
            return _run_animate(config, args)

        if args.command == "integrated-create":
            return _run_integrated_create(config, args)

        if args.command == "ai-create":
            return _run_ai_create(config, args)

        if args.command == "free-create":
            return _run_free_create(config, args)
        if args.command == "blocky-film":
            return _run_blocky_film(config, args)

        if args.command == "blocky-create":
            return _run_blocky_create(config, args)

        if args.command == "blocky-qc":
            return _run_blocky_qc(config, args)

        if args.command == "blocky-regress":
            return _run_blocky_regress(config, args)

        if args.command == "providers":
            return _run_providers(args)

        if args.command == "providers-check":
            return _run_providers_check(args)

        parser.print_help()
        return 0
    except (CommandError, JobError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Прервано пользователем. Задание можно продолжить командой retry.", file=sys.stderr)
        return 130
    finally:
        store.close()


def _dispatch_simple(jobs: JobService, action: str, as_json: bool) -> int:
    project = jobs.latest()
    if action == "status":
        _print_status(jobs.list(20), as_json)
    elif action == "inspect":
        _print_inspect(jobs.get(latest=True), jobs, as_json)
    elif action == "report":
        print(jobs.write_report(jobs.get(latest=True)))
    elif action == "preview":
        if project is None:
            raise CommandError("Заданий пока нет.")
        for path in _preview_paths(project):
            print(path)
    elif action in {"resume", "retry"}:
        if project is None:
            raise CommandError("Заданий пока нет.")
        jobs.prepare_retry(project)
        jobs.run(project)
        _print_projects([project], as_json)
    elif action == "doctor":
        result = Doctor(jobs.config).run()
        _print_doctor(result)
    else:
        raise CommandError(f"Действие «{action}» пока не поддерживается через ask.")
    return 0


def _run_animate(config: Any, args: argparse.Namespace) -> int:
    from .animation import AnimationEngine, SceneError, StoryboardError, load_storyboard
    from .animation.renderer import RenderError

    out_dir = Path(args.out) if args.out else Path(config.output_dir) / "_animation_engine_v1"
    scene_path = Path(args.scene) if args.scene else None
    try:
        if getattr(args, "storyboard", ""):
            if scene_path is not None:
                print("Укажите либо --scene, либо --storyboard, но не оба сразу.", file=sys.stderr)
                return 2
            compiled = load_storyboard(args.storyboard)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "storyboard.source.json").write_text(
                Path(args.storyboard).read_text(encoding="utf-8"), encoding="utf-8"
            )
            scene_path = out_dir / "scene.compiled.json"
            scene_path.write_text(
                json.dumps(compiled, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        if scene_path is None:
            print("Укажите --scene или --storyboard.", file=sys.stderr)
            return 2
        result = AnimationEngine(config).render(scene_path, out_dir)
    except (SceneError, StoryboardError) as exc:
        print(f"Ошибка сцены: {exc}", file=sys.stderr)
        return 2
    except RenderError as exc:
        print(f"Ошибка рендера: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False) or getattr(args, "json_out", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("Animation Engine v4")
        print(f"Сцена: {result.scene_id}")
        print(f"Видео: {result.video} ({human_size(result.size_bytes)})")
        print(f"{result.width}x{result.height}, {result.fps} fps, {result.duration:.2f} с, кадров: {result.frames}")
        print(f"Кодеки: {result.codecs}")
        print(f"Полное декодирование: {'OK' if result.decode_ok else 'ОШИБКА'}")
        print(
            "Движение частей тела без камеры: "
            f"{'подтверждено' if result.motion_checks.get('limbs_move_without_camera') else 'НЕ подтверждено'}"
        )
        if result.attachment_checks.get("enabled"):
            print(
                "Привязка объектов к ригу: "
                f"{'подтверждена' if result.attachment_checks.get('verified') else 'НЕ подтверждена'}"
            )
        print(f"Контактный лист: {result.contact_sheet}")
        print(f"Отчёт: {result.report}")
    if args.open:
        open_path(Path(result.video))
    ok = result.decode_ok and bool(result.motion_checks.get("limbs_move_without_camera"))
    return 0 if ok else 3


def _run_integrated_create(config: Any, args: argparse.Namespace) -> int:
    from .integration import IntegrationError, run_integrated_create

    output = Path(args.output) if args.output else Path(config.output_dir) / "integrated_pilot"
    try:
        result = run_integrated_create(
            args.script,
            output,
            config=config,
            title=args.title or None,
            overwrite=args.overwrite,
        )
    except IntegrationError as exc:
        print(f"Ошибка интеграции: {exc}", file=sys.stderr)
        return 2
    payload = result.to_dict()
    if getattr(args, "json", False) or getattr(args, "json_out", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("Интегрированный конвейер ShortsFactory")
        print(f"Статус QC: {result.status}")
        print(f"Видео: {result.video}")
        print(f"Контактный лист: {result.contact_sheet}")
        print(f"Отчёт: {result.report_json}")
        print(f"Markdown: {result.report_md}")
        print(f"Story Studio Project: {result.story.project_path}")
        print(f"Motion plan: {result.motion.plan_path}")
    return 0 if result.status in {"PASS", "WARNING"} else 3


def _run_blocky_film(config: Any, args: argparse.Namespace) -> int:
    from .generative.blocky_scene import load_and_render

    try:
        scene = _load_blocky_scene(args.scene)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    output = Path(args.output) if args.output else Path(config.output_dir) / "blocky_films" / _scene_slug(scene.title)
    print(f"Сцена: {scene.title or args.scene}")
    print(f"Рендер в: {output}")
    try:
        report = load_and_render(args.scene, output)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Ошибка валидации сцены: {exc}", file=sys.stderr)
        return 2

    if getattr(args, "json_out", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("status") == "OK" else 3

    print(f"Статус: {report.get('status')}")
    if report.get("error"):
        print(f"Ошибка: {report['error']}", file=sys.stderr)
    print(f"Видео: {report.get('final_audio') or report.get('video') or '—'}")
    if report.get("contact_sheet"):
        print(f"Контактный лист: {report['contact_sheet']}")
    diag = report.get("diagnostics") or {}
    if diag:
        pose = diag.get("pose_error") or {}
        foot = diag.get("foot_contact") or {}
        blank = diag.get("blank_frames") or {}
        timings = diag.get("timings") or {}
        if pose:
            print(f"Поза: passed={pose.get('passed')} (max diff {pose.get('max_abs_matrix_diff')})")
        if foot:
            print(f"Стопы: окон {foot.get('windows')}, дрейф {foot.get('max_drift')} м")
        if blank:
            print(f"Пустые кадры: {blank.get('blank', 0)} из {blank.get('checked', 0)}")
        if timings:
            print(f"Рендер: {timings.get('render_s')} c ({timings.get('sec_per_frame')} с/кадр)")
    return 0 if report.get("status") == "OK" else 3


def _run_blocky_qc(config: Any, args: argparse.Namespace) -> int:
    """Read an existing report and gate it; rendering is never redone here."""
    from .generative.blocky_qc import FAILED, READY, film_qc, scene_qc

    path = Path(args.report)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Ошибка: не читается отчёт {path}: {exc}", file=sys.stderr)
        return 2

    if args.film:
        srt = args.srt or report.get("subtitles") or ""
        qc = film_qc(report, srt_path=srt or None)
    else:
        payload = None
        payload_path = Path(args.payload) if args.payload else None
        if payload_path is None:
            candidates = sorted((path.parent / "render").glob("payload_*.json"))
            payload_path = candidates[0] if candidates else None
        if payload_path and payload_path.exists():
            try:
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = None
        srt = args.srt or report.get("subtitles") or ""
        qc = scene_qc(
            report,
            payload=payload,
            srt_path=srt or None,
        )

    if getattr(args, "json_out", False):
        print(json.dumps(qc, ensure_ascii=False, indent=2))
    else:
        print(f"QC: {qc['status']} — {qc.get('title') or path.name}")
        for check in qc["checks"]:
            mark = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}[check["status"]]
            line = f"  [{mark}] {check['label']}: {check['detail']}"
            if check.get("timecodes"):
                line += f"  @ {', '.join(check['timecodes'][:6])}"
            print(line)
    if qc["status"] == READY:
        return 0
    return 3 if qc["status"] == FAILED else 1


def _run_blocky_regress(config: Any, args: argparse.Namespace) -> int:
    from .generative.blocky_regress import run_regression

    result = run_regression(
        Path(config.root),
        golden=args.golden or None,
        update=bool(args.update),
    )
    if getattr(args, "json_out", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Регрессия: {result['status']} — сцен {len(result.get('scenes') or [])}")
        print(f"Эталон: {result.get('golden')}")
        for issue in result.get("issues") or []:
            print(f"  ! {issue}")
    return 0 if result["status"] in {"PASS", "UPDATED"} else 3


def _produce_series(
    storyboard: dict[str, Any],
    out: Path,
    *,
    episodes: int,
    voice: bool = True,
    resume: bool = True,
) -> dict[str, Any]:
    """Ч2.5: сториборд → N серий → сводный отчёт сериала.

    Сториборд режется на последовательные серии
    (:func:`.generative.blocky_production.split_storyboard_episodes`),
    каждая рендерится через ``produce_film`` в свою папку ``ep_XX``
    (нумерация с 01).  Сводный отчёт ``{"episodes": [...], "status": ...}``
    пишется в ``series_report.json``: ``OK``, только когда готовы все
    серии, иначе ``PARTIAL``.
    """
    from .generative.blocky_production import (
        produce_film,
        split_storyboard_episodes,
    )

    parts = split_storyboard_episodes(storyboard, episodes)
    if not parts:
        raise ValueError("В раскадровке нет сцен: делить на серии нечего.")
    reports: list[dict[str, Any]] = []
    for index, part in enumerate(parts, 1):
        print(f"Серия {index:02d}/{len(parts)}: {part.get('title')}")
        reports.append(
            produce_film(part, out / f"ep_{index:02d}", voice=voice, resume=resume)
        )
    series: dict[str, Any] = {
        "title": str(storyboard.get("title") or ""),
        "episodes": reports,
        "status": (
            "OK" if all(rep.get("status") == "OK" for rep in reports) else "PARTIAL"
        ),
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "series_report.json").write_text(
        json.dumps(series, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return series


def _run_blocky_create(config: Any, args: argparse.Namespace) -> int:
    """Idea -> storyboard -> rendered scenes -> one film, resumable per scene."""
    from .generative.blocky_production import produce_film
    from .generative.blocky_story import generate_storyboard, load_storyboard

    if getattr(args, "storyboard", ""):
        try:
            storyboard = load_storyboard(
                args.storyboard, idea=getattr(args, "idea", "") or ""
            )
        except (OSError, ValueError) as exc:
            print(f"Ошибка раскадровки: {exc}", file=sys.stderr)
            return 2
        raw = str(storyboard.get("title") or Path(args.storyboard).stem)
        idea = raw
    else:
        raw = args.idea
        path = Path(raw)
        if path.exists() and path.is_file():
            try:
                idea = path.read_text(encoding="utf-8")
            except OSError:
                idea = raw
        else:
            idea = raw
        if not str(idea).strip():
            print("Ошибка: идея пустая (укажите --idea или --storyboard).", file=sys.stderr)
            return 2
        try:
            storyboard = generate_storyboard(
                idea,
                config=config,
                scenes=max(1, int(args.scenes)),
                seconds=float(args.seconds) or None,
                lang=args.lang,
                model=getattr(args, "model", "") or None,
            )
        except Exception as exc:  # LLM, JSON, схема — контролируемая ошибка
            print(f"Ошибка генерации сценария: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 3
    first_line = str(idea).strip().splitlines()[0] if str(idea).strip() else ""
    print(f"Идея: {first_line[:120]}")
    print(
        f"Источник: {storyboard.get('source')} "
        f"(сцен: {len(storyboard.get('scenes') or [])})"
    )
    out = Path(args.output) if args.output else (
        Path(config.output_dir)
        / "blocky_films"
        / _scene_slug(storyboard.get("title") or idea)
    )
    episodes = int(getattr(args, "episodes", 1) or 1)
    try:
        if episodes > 1:
            # Ч2.5: сериал — каждая серия в своей папке ep_XX
            report = _produce_series(
                storyboard, out, episodes=episodes,
                voice=not args.no_voice, resume=bool(args.resume),
            )
        else:
            report = produce_film(
                storyboard, out, voice=not args.no_voice, resume=bool(args.resume),
            )
    except (ValueError, FileNotFoundError) as exc:
        print(f"Ошибка производства фильма: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"Ошибка производства фильма: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 3

    if getattr(args, "json_out", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("status") == "OK" else 3

    print(f"Статус: {report.get('status')}")
    if report.get("error"):
        print(f"Ошибка: {report['error']}", file=sys.stderr)
    if episodes > 1:
        # сводка сериала (Ч2.5): статус каждой серии + путь к сводному отчёту
        for index, episode in enumerate(report.get("episodes") or [], 1):
            print(
                f"Серия {index:02d}: {episode.get('status')} — "
                f"{episode.get('video') or '—'}"
            )
        print(f"Отчёт сериала: {out / 'series_report.json'}")
        return 0 if report.get("status") == "OK" else 3
    print(f"Видео: {report.get('video') or '—'}")
    if report.get("duration"):
        print(f"Длительность: {report['duration']} c")
    print(f"Сцены: {len(report.get('scenes') or [])}, субтитров: {report.get('cues', 0)}")
    if report.get("contact_sheet"):
        print(f"Контактный лист: {report['contact_sheet']}")
    return 0 if report.get("status") == "OK" else 3


def _scene_slug(title: str) -> str:
    slug = "".join(c if c.isalnum() else "_" for c in (title or "scene")).strip("_").lower()
    return slug or "scene"


def _load_blocky_scene(scene_path: str) -> Any:
    """Load a scene document and return the validated BlockyScene."""
    from .generative.blocky_scene import BlockyScene

    scene = BlockyScene.load(scene_path)
    scene.validate()
    return scene


def _run_free_create(config: Any, args: argparse.Namespace) -> int:
    from .generative.freeanim import run_free_create

    output = Path(args.output) if args.output else Path(config.output_dir) / "free_demo"
    script_arg = args.script
    path = Path(script_arg)
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = script_arg
    else:
        text = script_arg
    if not str(text).strip():
        print("Ошибка: идея пустая.", file=sys.stderr)
        return 2
    try:
        result = run_free_create(
            text,
            output,
            config,
            title=args.title,
            engine=args.engine,
            ollama_model=args.ollama_model,
            piper_voice=args.piper_voice,
            duration=args.duration,
            quality=args.quality,
            resume=args.resume,
            overwrite=args.overwrite,
        )
    except (FileExistsError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2

    if getattr(args, "json", False) or getattr(args, "json_out", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.status != "FAILED" else 3

    print("Free-create — полностью бесплатный локальный конвейер (free_local)")
    print(f"Статус: {result.status}")
    print(f"Движок: {result.engine}")
    print(f"Видео: {result.video or '—'}")
    if result.contact_sheet:
        print(f"Контактный лист: {result.contact_sheet}")
    if result.subtitles_srt:
        print(f"Субтитры: {result.subtitles_srt}")
    print(f"Время рендера: {result.render_time_s} c")
    if result.qc.decode_ok:
        print(f"QC: {result.qc.width}x{result.qc.height} @ {result.qc.fps} fps, "
              f"{result.qc.duration_s} с, decode OK, unique-frames {result.qc.unique_frame_fraction}")
    for note in result.notes + result.qc.notes:
        print(f"- {note}")
    return 0 if result.status in {"READY", "PARTIAL", "WARNING"} else 3


def _run_ai_create(config: Any, args: argparse.Namespace) -> int:
    from .generative import BackendError, run_ai_create

    output = Path(args.output) if args.output else Path(config.output_dir) / "mission3_ai_video"
    script_arg = args.script
    path = Path(script_arg)
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = script_arg
    else:
        text = script_arg
    video_provider = args.video_provider or args.provider
    try:
        result = run_ai_create(
            text,
            output,
            config,
            preset_id=args.preset,
            mode=args.mode,
            provider=video_provider,
            video_model=args.video_model,
            title=args.title,
            references=list(args.reference or []),
            with_audio=not args.no_audio,
            poll_interval=args.poll_interval,
            poll_timeout=args.poll_timeout,
            overwrite=args.overwrite,
            text_provider=args.text_provider,
            text_model=args.text_model,
            voice_provider=args.voice_provider,
            dry_run=args.dry_run,
            max_generations=args.max_generations or None,
        )
    except (FileExistsError, ValueError, BackendError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2

    payload = result.to_dict()
    if getattr(args, "json", False) or getattr(args, "json_out", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if result.status != "ERROR" else 3

    print("AI-create — генеративный конвейер ShortsFactory")
    print(f"Режим: {result.mode}; провайдер: {result.provider or '—'}")
    print(f"Статус: {result.status}")
    print(f"Видео: {result.video or '—'}")
    if result.contact_sheet:
        print(f"Контактный лист: {result.contact_sheet}")
    if result.cost and result.cost.estimated_usd is not None:
        print(f"Оценка стоимости (полный план): ${result.cost.estimated_usd:.4f} ({result.cost.source})")
    if result.cost_limited and result.cost_limited.estimated_usd is not None:
        print(
            f"Оценка стоимости (с лимитом): ${result.cost_limited.estimated_usd:.4f} "
            f"({result.cost_limited.source})"
        )
    for note in result.notes:
        print(f"- {note}")
    if result.error:
        print(f"Ошибка: {result.error}", file=sys.stderr)
    return 0 if result.status in {"OK", "WARNING", "PLANNED"} else 3


def _run_providers(args: argparse.Namespace) -> int:
    from .generative.registry import Capability, registry as get_registry

    reg = get_registry()
    providers = reg.list_providers()
    cap_filter = args.capability
    if cap_filter:
        try:
            cap = Capability(cap_filter)
        except ValueError:
            print(f"Неизвестная capability: {cap_filter}. Допустимо: "
                  f"{', '.join(c.value for c in Capability)}", file=sys.stderr)
            return 2

    rows = []
    for p in providers:
        if cap_filter:
            if cap not in p.supported_capabilities:
                continue
            caps = [r for r in p.capability_reports() if r.capability == cap]
        else:
            caps = p.capability_reports()
        rows.append({"provider": p.describe(), "capabilities": [c.to_dict() for c in caps]})

    if getattr(args, "json", False) or getattr(args, "json_out", False):
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print("ИИ-провайдеры ShortsFactory (ключи не показываются)")
    print("=" * 88)
    for row in rows:
        d = row["provider"]
        print(f"\n* {d['id']} — {d['name']} [{d['type']}]")
        print(f"  Настроен: {'да' if d['configured'] else 'нет'} | платный: "
              f"{'да' if d['paid'] else 'нет'}")
        for c in row["capabilities"]:
            models = ", ".join(c["models"]) or "—"
            print(f"  • {c['capability']}: модели({models}); "
                  f"ключи({', '.join(c['key_names']) or '—'}); "
                  f"настроен={c['configured']}; проверка без платы={c['fee_free_check']}")
        if d["configured_key"]:
            print(f"  Ключ присутствует в переменной {d['configured_key']} (значение скрыто)")
    return 0


def _run_providers_check(args: argparse.Namespace) -> int:
    from .generative.registry import registry as get_registry

    reg = get_registry()
    providers = reg.list_providers()
    if args.provider:
        providers = [p for p in providers if p.id == args.provider]
        if not providers:
            print(f"Провайдер не найден: {args.provider}", file=sys.stderr)
            return 2

    results = []
    for p in providers:
        r = p.check_availability()
        fee_free = all(p.fee_free_check(c) for c in p.supported_capabilities) if p.supported_capabilities else True
        caps = [c.to_dict() for c in p.capability_reports()]
        entry = {
            "provider": r.provider,
            "ok": r.ok,
            "mode": r.mode,
            "cost_incurred": r.cost_incurred,
            "detail": r.detail,
            "last_status": r.last_status,
            "key_present": p.is_configured(),
            "import_ok": True,  # only stdlib/requests used
            "capabilities": caps,
            "fee_free_check": fee_free,
        }
        if not fee_free:
            entry["detail"] += " (проверка действительности ключа/API может стоить денег — не выполнялась)"
        results.append(entry)

    if getattr(args, "json", False) or getattr(args, "json_out", False):
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if all(e["ok"] for e in results) else 1

    print("Диагностика провайдеров (без платных генераций)")
    print("=" * 88)
    for e in results:
        status = "OK" if e["ok"] else ("WARN" if e["last_status"] == "warning" else "?")
        caps_str = ", ".join(f"{c['capability']}[{','.join(c['models']) or '-'}]" for c in e["capabilities"])
        print(f"[{status:<4}] {e['provider']}: key_present={e['key_present']} "
              f"| connection={e['mode']} | generation=not-verified")
        print(f"    detail: {e['detail']}")
        print(f"    capabilities: {caps_str or '-'} | import_ok={e['import_ok']}")
    print("\nСтатусы: key_present — ключ задан; connection — config (проверено локально) "
          "или api (запрос выполнен);")
    print("generation — реальная генерация не запускалась.")
    print("«ключ присутствует» ≠ «ключ действителен», «API доступен» ≠ «генерация работает».")
    print("Проверка действительности ключа/API может стоить денег и выполняется только по явному разрешению.")
    return 0 if all(e["ok"] for e in results) else 1


def _print_status(projects: list[Any], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps([project.to_dict() for project in projects], ensure_ascii=False, indent=2))
        return
    if not projects:
        print("Заданий нет. Создайте первое: shortsfactory create --topic \"Космос\"")
        return
    print(f"{'ID':<30} {'СТАТУС':<10} {'ЭТАП':<14} ТЕМА")
    print("-" * 92)
    for project in projects:
        print(f"{project.id:<30} {project.status:<10} {project.stage:<14} {project.topic[:34]}")
        if project.error:
            print(f"{'':<30} {project.error[:90]}")


def _print_projects(projects: list[Any], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps([project.to_dict() for project in projects], ensure_ascii=False, indent=2))
        return
    for project in projects:
        output = Path(project.output)
        print("")
        print(f"Задание: {project.id}")
        print(f"Тема: {project.topic}")
        print(f"Статус: {project.status}")
        print(f"Папка: {output}")
        videos = sorted(output.glob("*/*.mp4"))
        for video in videos:
            print(f"  Видео: {video.name} ({human_size(video.stat().st_size)})")
        if not videos and project.status != "QUEUED":
            print("  Видео пока нет.")
        summary = output / "reports" / "quality_summary.json"
        if summary.exists():
            try:
                data = json.loads(summary.read_text(encoding="utf-8"))
                for lang, value in data.items():
                    print(f"  QC {lang.upper()}: {value.get('status')}")
            except (OSError, ValueError):
                pass
        report = output / "reports" / "report.md"
        if report.exists():
            print(f"  Отчёт: {report}")
        if project.error:
            print(f"  Примечание: {project.error}")


def _print_inspect(project: Any, jobs: JobService, as_json: bool = False) -> None:
    plan_path = Path(project.workspace) / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
    stages = [record.to_dict() for record in jobs.store.stage_records(project.id)]
    data = {
        "project": project.to_dict(),
        "plan": plan,
        "stages": stages,
        "files": [
            {"path": path.relative_to(Path(project.output)).as_posix(), "size": path.stat().st_size}
            for path in sorted(Path(project.output).rglob("*"))
            if path.is_file()
        ],
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"Задание: {project.id}")
    print(f"Тема: {project.topic} ({project.profile})")
    print(f"Статус: {project.status}; этап: {project.stage}")
    print(f"Языки: {', '.join(project.languages)}; длительность: {project.duration:g} с")
    print(f"Команда: {project.command}")
    print(f"Workspace: {project.workspace}")
    print(f"Output: {project.output}")
    if project.error:
        print(f"Ошибка: {project.error}")
    if plan:
        print(f"Стиль: {plan.get('style')}; темп: {plan.get('pace')}; seed: {plan.get('seed', {}).get('ru', '—')}")
    print("")
    print(f"{'ЭТАП':<16} {'СТАТУС':<12} НАЧАЛО / КОНЕЦ")
    for record in stages:
        print(f"{record['name']:<16} {record['status']:<12} {record['started_at']} / {record['finished_at']}")
    print("")
    print("Файлы:")
    for item in data["files"]:
        if item["size"] > 1_000_000 or item["path"].endswith(".mp4"):
            print(f"  {item['path']} ({human_size(item['size'])})")


def _preview_paths(project: Any) -> list[Path]:
    output = Path(project.output)
    result: list[Path] = []
    videos = sorted(output.glob("*/*.mp4"))
    result.extend(videos)
    previews = output / "previews"
    if previews.exists():
        result.extend(sorted(previews.glob("*.jpg")))
    report = output / "reports" / "report.md"
    if report.exists():
        result.append(report)
    return result


def _print_doctor(result: Any) -> None:
    for check in result.checks:
        mark = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}.get(check.status, check.status.upper())
        print(f"[{mark:<4}] {check.name:<18} {check.detail}")
        if check.fix and check.status != "ok":
            print(f"         → {check.fix}")
    print("")
    print(
        f"Итог: {'готово' if result.ok else 'есть ошибки'}; "
        f"предупреждений: {len(result.warnings)}; ошибок: {len(result.failures)}"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
