"""Human-readable project reports and environment snapshots."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import Config
from .logging_setup import get_logger
from .models import LanguageQC, Plan, Project, Script, Source
from .util import ffmpeg_bin, ffprobe_bin, human_size, now_iso

try:  # requests is a hard dependency, but reports must never crash because of it
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]


class ReportError(RuntimeError):
    pass


class ReportService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = get_logger()

    # ------------------------------------------------------------- environment
    def environment(self) -> dict[str, Any]:
        root = self.config.root
        usage = shutil.disk_usage(root)
        data: dict[str, Any] = {
            "generated_at": now_iso(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "python_executable": sys.executable,
            "cpu_count": __import__("os").cpu_count(),
            "root": root.as_posix(),
            "disk_free_gb": round(usage.free / 1_073_741_824, 2),
            "disk_total_gb": round(usage.total / 1_073_741_824, 2),
            "ffmpeg": self._tool_version(ffmpeg_bin(), "-version"),
            "ffprobe": self._tool_version(ffprobe_bin(), "-version"),
            "packages": self._packages(),
            "fonts": sorted(path.name for path in self.config.fonts_dir.glob("*.ttf")),
            "piper_models": sorted(path.name for path in self.config.models_dir.glob("*.onnx")),
        }
        data["ollama"] = self._ollama()
        return data

    @staticmethod
    def _tool_version(binary: str | None, flag: str) -> dict[str, Any]:
        if not binary:
            return {"available": False, "version": ""}
        try:
            result = subprocess.run(
                [binary, flag],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            first = (result.stdout or result.stderr or "").splitlines()[0:1]
            return {"available": result.returncode == 0, "version": first[0] if first else ""}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"available": False, "version": "", "error": str(exc)}

    @staticmethod
    def _packages() -> dict[str, str]:
        result: dict[str, str] = {}
        for name in ("piper-tts", "Pillow", "numpy", "requests", "onnxruntime", "shortsfactory"):
            try:
                result[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                result[name] = ""
        return result

    def _ollama(self) -> dict[str, Any]:
        base = str(self.config.get("llm", "base_url", "http://127.0.0.1:11434")).rstrip("/")
        data: dict[str, Any] = {"url": base, "available": False, "models": []}
        if requests is None:
            return data
        try:
            response = requests.get(f"{base}/api/tags", timeout=2)
            response.raise_for_status()
            payload = response.json()
            data["available"] = True
            data["models"] = sorted(item.get("name", "") for item in payload.get("models", []))
        except Exception as exc:  # noqa: BLE001 - optional local service
            data["error"] = str(exc)
        return data

    # ------------------------------------------------------------------ report
    def write_project(
        self,
        project: Project,
        plan: Plan,
        qcs: list[LanguageQC],
    ) -> Path:
        workspace = Path(project.workspace)
        output = Path(project.output)
        scripts = self._scripts(workspace, plan)
        sources = self._sources(workspace)
        environment = self._read(workspace / "environment.json") or self.environment()
        media = self._media(workspace, plan)
        files = self._files(output)

        lines = [
            f"# ShortsFactory: {plan.topic}",
            "",
            f"**Статус:** `{project.status}`",
            f"**Задание:** `{project.id}`",
            f"**Команда:** `{project.command}`",
            *(
                [f"**Remake:** {', '.join(f'{key}={value}' for key, value in project.params['last_remake']['changes'].items())} "
                 f"({project.params['last_remake'].get('at', '')})"]
                if isinstance(project.params.get("last_remake"), dict)
                and project.params["last_remake"].get("changes")
                else []
            ),
            f"**Тема:** {plan.topic}" + (f" / {plan.topic_en}" if plan.topic_en else ""),
            f"**Профиль:** {self.config.profile(plan.profile).get('label_ru', plan.profile)} "
            f"(`{plan.profile}`)",
            f"**Языки:** {', '.join(code.upper() for code in plan.languages)}",
            f"**Целевая длительность:** {plan.duration:g} с",
            f"**Стиль / темп:** {plan.style} / {plan.pace}",
            f"**Создано:** {project.created_at}",
            f"**Обновлено:** {project.updated_at}",
            "",
        ]
        if project.error:
            lines += [f"> {project.error}", ""]

        lines += ["## Готовые файлы", "", "| Файл | Размер |", "|---|---|"]
        for item in files:
            lines.append(f"| `{item['path']}` | {human_size(item['size'])} |")
        if not files:
            lines.append("| — | — |")
        lines.append("")

        lines += ["## Контроль качества", "", "| Язык | Статус | Проверок | Обязательных ошибок | Контактный лист |", "|---|---|---|---|---|"]
        for qc in qcs:
            failed = ", ".join(check.id for check in qc.mandatory_failed) or "—"
            lines.append(
                f"| {qc.language.upper()} | `{qc.status}` | {len(qc.checks)} | {failed} | "
                f"`{Path(qc.contact_sheet).name if qc.contact_sheet else '—'}` |"
            )
        lines.append("")
        for qc in qcs:
            lines += [f"### {qc.language.upper()}", ""]
            if qc.warnings:
                lines += [f"- {item}" for item in qc.warnings] + [""]
            lines += ["| Проверка | Уровень | Результат | Детали |", "|---|---|---|---|"]
            for check in qc.checks:
                detail = check.detail.replace("|", "/").replace("\n", " ")[:220]
                lines.append(
                    f"| `{check.id}` | {check.level} | {'PASS' if check.passed else 'FAIL'} | {detail} |"
                )
            lines.append("")

        lines += ["## Сценарии", ""]
        for lang, script in scripts.items():
            lines += [
                f"### {lang.upper()}: {script.title}",
                "",
                f"- Слов: {script.word_count}; сцен: {len(script.scenes)}; движок: `{script.engine or '—'}`",
                f"- Модель: `{script.model or '—'}`; версия промпта: `{script.prompt_version or '—'}`",
            ]
            if script.warnings:
                lines += [f"- Предупреждение: {item}" for item in script.warnings[:10]]
            lines += ["", script.markdown(sources), ""]

        lines += ["## Источники", ""]
        if sources:
            for source in sources:
                lines.append(
                    f"- [{source.id}] [{source.title}]({source.url}) — {source.publisher}, "
                    f"{source.accessed_at}; лицензия: {source.license or 'не указана'}"
                )
        else:
            lines.append("- Источники не сохранены.")
        lines.append("")

        lines += ["## Медиа и лицензии", ""]
        if media:
            for item in media:
                lines.append(
                    f"- {', '.join(item.get('scenes', []))} — {item.get('provider', '')}: "
                    f"{item.get('title', '')}; автор: {item.get('author', '—')}; "
                    f"лицензия: {item.get('license', '—')}; {item.get('source_page', '')}"
                )
        else:
            lines.append("- Использованы только программно созданные карточки.")
        lines.append("")

        lines += [
            "## Ручная проверка перед загрузкой",
            "",
            "1. Откройте видео и контактный лист, проверьте соответствие кадров тексту.",
            "2. Проверьте читаемость субтитров на телефоне.",
            "3. Проверьте факты и формулировки по списку источников.",
            "4. Проверьте титул, описание, хештеги и обложку в `metadata/`.",
            "5. Загрузите MP4 вручную; система не публикует видео автоматически.",
            "",
            "## Окружение",
            "",
            f"- Python: {environment.get('python', '—')}",
            f"- Платформа: {environment.get('platform', '—')}",
            f"- FFmpeg: {environment.get('ffmpeg', {}).get('version', '—')}",
            f"- Свободно на диске: {environment.get('disk_free_gb', '—')} ГБ",
            f"- Модели Ollama: {', '.join(environment.get('ollama', {}).get('models', [])) or '—'}",
            "",
            f"_Отчёт сформирован: {now_iso()}_",
            "",
        ]
        text = "\n".join(lines)
        payload = {
            "project": project.to_dict(),
            "plan": plan.to_dict(),
            "status": project.status,
            "quality": [qc.to_dict() for qc in qcs],
            "sources": [source.to_dict() for source in sources],
            "scripts": {lang: script.to_dict() for lang, script in scripts.items()},
            "media": media,
            "files": files,
            "environment": environment,
            "generated_at": now_iso(),
        }

        output_reports = output / "reports"
        output_reports.mkdir(parents=True, exist_ok=True)
        report_path = output_reports / "report.md"
        report_path.write_text(text, encoding="utf-8")
        (output_reports / "report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        workspace_reports = workspace / "reports"
        workspace_reports.mkdir(parents=True, exist_ok=True)
        (workspace_reports / "report.md").write_text(text, encoding="utf-8")
        self.logger.info("Отчёт: %s", report_path)
        return report_path

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            import json as _json

            data = _json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _scripts(self, workspace: Path, plan: Plan) -> dict[str, Script]:
        result: dict[str, Script] = {}
        for lang in plan.languages:
            path = workspace / "scripts" / f"{lang}.json"
            try:
                result[lang] = Script.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                continue
        return result

    @staticmethod
    def _sources(workspace: Path) -> list[Source]:
        try:
            data = json.loads((workspace / "research" / "sources.json").read_text(encoding="utf-8"))
            return [Source.from_dict(item) for item in data.get("sources", [])]
        except (OSError, ValueError, TypeError):
            return []

    def _media(self, workspace: Path, plan: Plan) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        for lang in plan.languages:
            try:
                data = json.loads(
                    (workspace / "assets" / lang / "media_manifest.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            for scene in data.get("scenes", []):
                credit = scene.get("candidate") or scene.get("credit") or {}
                if credit:
                    key = (
                        str(credit.get("provider", "")),
                        str(credit.get("title", "")),
                        str(credit.get("source_page", "")),
                        str(credit.get("license", "")),
                        str(credit.get("author", "")),
                    )
                else:
                    key = (
                        "ShortsFactory",
                        "Программная графика",
                        "",
                        "Собственная графика (сгенерирована локально)",
                        "ShortsFactory",
                    )
                item = grouped.setdefault(
                    key,
                    {
                        "provider": key[0],
                        "title": key[1],
                        "source_page": key[2],
                        "license": key[3],
                        "author": key[4],
                        "scenes": [],
                    },
                )
                item["scenes"].append(f"{lang}:{scene.get('scene_id', '')}")
        return list(grouped.values())

    @staticmethod
    def _files(output: Path) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if not output.exists():
            return result
        for path in sorted(output.rglob("*")):
            if path.is_file():
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                result.append(
                    {
                        "path": path.relative_to(output).as_posix(),
                        "size": size,
                    }
                )
        return result
