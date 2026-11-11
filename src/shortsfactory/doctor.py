"""Environment diagnostics for a local ShortsFactory installation."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import Config
from .logging_setup import get_logger
from .util import ffmpeg_bin, ffprobe_bin

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DoctorResult:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(check.status == "fail" for check in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if check.status == "fail"]

    @property
    def warnings(self) -> list[Check]:
        return [check for check in self.checks if check.status == "warn"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "failures": len(self.failures),
            "warnings": len(self.warnings),
        }


class Doctor:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = get_logger()

    def run(self, *, full: bool = False) -> DoctorResult:
        result = DoctorResult()
        self._python(result)
        self._writable(result)
        self._ffmpeg(result)
        self._ffmpeg_capabilities(result)
        self._ffmpeg_version(result)
        self._fonts(result)
        self._tts_models(result)
        self._llm(result)
        self._disk(result)
        self._internet(result)
        if full:
            self._tts_probe(result)
            self._llm_probe(result)
        return result

    # ------------------------------------------------------------------ checks
    @staticmethod
    def _python(result: DoctorResult) -> None:
        version = sys.version_info
        status = "ok" if version >= (3, 11) else "fail"
        result.checks.append(
            Check(
                "python",
                status,
                f"Python {platform.python_version()} ({sys.executable})",
                "" if status == "ok" else "Установите Python 3.11 или новее.",
            )
        )

    def _writable(self, result: DoctorResult) -> None:
        try:
            self.config.ensure_tree()
            probe = self.config.workspace_dir / "_tmp" / ".doctor-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            result.checks.append(Check("writable", "ok", f"Каталог доступен: {self.config.root}"))
        except OSError as exc:
            result.checks.append(
                Check("writable", "fail", f"Нет записи в {self.config.root}: {exc}", "Проверьте права на D:\\ShortsFactory.")
            )

    def _ffmpeg(self, result: DoctorResult) -> None:
        for name, binary in (("ffmpeg", ffmpeg_bin()), ("ffprobe", ffprobe_bin())):
            if not binary:
                result.checks.append(
                    Check(name, "fail", "Не найден", "Установите FFmpeg и добавьте его в PATH.")
                )
                continue
            try:
                completed = subprocess.run(
                    [binary, "-version"], capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=20,
                )
                line = (completed.stdout or completed.stderr or "").splitlines()[0]
                result.checks.append(
                    Check(name, "ok" if completed.returncode == 0 else "fail", line, "")
                )
            except (OSError, subprocess.SubprocessError) as exc:
                result.checks.append(Check(name, "fail", str(exc), "Проверьте установку FFmpeg."))

    def _ffmpeg_capabilities(self, result: DoctorResult) -> None:
        binary = ffmpeg_bin()
        if not binary:
            return
        required_filters = ["ass", "zoompan", "xfade", "loudnorm", "sidechaincompress"]
        required_encoders = ["libx264", "aac"]
        try:
            filters = subprocess.run(
                [binary, "-hide_banner", "-filters"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            ).stdout
            encoders = subprocess.run(
                [binary, "-hide_banner", "-encoders"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            result.checks.append(Check("ffmpeg_capabilities", "fail", str(exc)))
            return
        missing_filters = [item for item in required_filters if f" {item} " not in filters]
        missing_encoders = [item for item in required_encoders if f" {item} " not in encoders]
        if missing_filters or missing_encoders:
            result.checks.append(
                Check(
                    "ffmpeg_capabilities",
                    "fail",
                    f"Нет фильтров: {missing_filters or '—'}; кодировщиков: {missing_encoders or '—'}",
                    "Установите полную сборку FFmpeg.",
                )
            )
        else:
            result.checks.append(
                Check("ffmpeg_capabilities", "ok", "ass, zoompan, xfade, loudnorm, libx264, aac доступны")
            )

    def _ffmpeg_version(self, result: DoctorResult) -> None:
        """adelay=all / amix=normalize / alimiter=level требуют FFmpeg >= 4.4."""
        binary = ffmpeg_bin()
        if not binary:
            return  # отсутствие уже отметил _ffmpeg
        try:
            proc = subprocess.run(
                [binary, "-version"], capture_output=True, text=True,
                encoding="utf-8", timeout=10,
            )
            match = re.search(r"ffmpeg version n?(\d+)\.(\d+)", proc.stdout)
            if not match:
                result.checks.append(Check(
                    "ffmpeg_version", "warn",
                    "Не удалось определить версию FFmpeg",
                    "Проверьте вывод ffmpeg -version вручную",
                ))
                return
            major, minor = int(match.group(1)), int(match.group(2))
            if (major, minor) < (4, 4):
                result.checks.append(Check(
                    "ffmpeg_version", "fail",
                    f"FFmpeg {major}.{minor} — слишком старый (adelay=all, amix=normalize, alimiter=level не поддерживаются)",
                    "Установите FFmpeg >= 4.4 (рекомендуется 6+)",
                ))
            elif (major, minor) < (6, 0):
                result.checks.append(Check(
                    "ffmpeg_version", "warn",
                    f"FFmpeg {major}.{minor} — работает, но рекомендуется 6+",
                ))
            else:
                result.checks.append(Check("ffmpeg_version", "ok", f"FFmpeg {major}.{minor}"))
        except (OSError, subprocess.TimeoutExpired) as exc:
            result.checks.append(Check("ffmpeg_version", "warn", str(exc)))

    def _fonts(self, result: DoctorResult) -> None:
        required = [
            "Montserrat-Regular.ttf",
            "Montserrat-Bold.ttf",
            "Montserrat-SemiBold.ttf",
            "Montserrat-ExtraBold.ttf",
        ]
        missing = [name for name in required if not (self.config.fonts_dir / name).exists()]
        license_ok = (self.config.fonts_dir / "OFL.txt").exists()
        if missing:
            result.checks.append(
                Check("fonts", "fail", f"Нет шрифтов: {', '.join(missing)}", "Скачайте Montserrat (SIL OFL).")
            )
        elif not license_ok:
            result.checks.append(Check("fonts", "warn", "Шрифты есть, файл лицензии OFL.txt отсутствует"))
        else:
            result.checks.append(Check("fonts", "ok", "Montserrat: кириллица и латиница, OFL"))

    def _tts_models(self, result: DoctorResult) -> None:
        for lang, key in (("ru", "voice_ru"), ("en", "voice_en")):
            name = str(self.config.get("tts", key, ""))
            path = self.config.models_dir / name
            if not name:
                result.checks.append(Check(f"piper_{lang}", "fail", "Голос не указан в config.toml"))
                continue
            if not path.exists():
                result.checks.append(
                    Check(
                        f"piper_{lang}",
                        "fail",
                        f"Нет модели: {path}",
                        "Скачайте голос Piper в models/piper и проверьте config.toml.",
                    )
                )
                continue
            config_path = path.with_suffix(".onnx.json")
            if not config_path.exists():
                result.checks.append(Check(f"piper_{lang}", "fail", f"Нет конфигурации: {config_path.name}"))
                continue
            result.checks.append(
                Check(f"piper_{lang}", "ok", f"{path.name} ({path.stat().st_size / 1_048_576:.0f} МБ), офлайн")
            )

    def _llm(self, result: DoctorResult) -> None:
        from .llm import OllamaClient

        try:
            status = OllamaClient(self.config).status()
            if status.get("available"):
                result.checks.append(
                    Check(
                        "ollama",
                        "ok" if status.get("model_present") else "warn",
                        f"{status.get('base_url')}: модель {status.get('configured_model')} "
                        f"{'найдена' if status.get('model_present') else 'не найдена'}; "
                        f"доступно {len(status.get('models', []))}",
                        "" if status.get("model_present") else "Запустите ollama pull qwen2.5:7b.",
                    )
                )
            else:
                result.checks.append(
                    Check(
                        "ollama",
                        "warn",
                        f"Локальный LLM недоступен: {status.get('base_url')}. Будет использован экстрактивный резерв.",
                        "Запустите Ollama, если нужны авторские сценарии.",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            result.checks.append(Check("ollama", "warn", str(exc), "Проверьте Ollama."))

    def _disk(self, result: DoctorResult) -> None:
        try:
            usage = shutil.disk_usage(self.config.root)
            free = usage.free / 1_073_741_824
            status = "ok" if free >= 3 else ("warn" if free >= 1 else "fail")
            result.checks.append(
                Check(
                    "disk",
                    status,
                    f"Свободно {free:.1f} ГБ из {usage.total / 1_073_741_824:.1f} ГБ",
                    "" if status == "ok" else "Освободите место на диске D:.",
                )
            )
        except OSError as exc:
            result.checks.append(Check("disk", "warn", str(exc)))

    def _internet(self, result: DoctorResult) -> None:
        if requests is None:
            result.checks.append(Check("internet", "warn", "Модуль requests недоступен"))
            return
        checks = [
            ("wikipedia", "https://ru.wikipedia.org/w/api.php?action=query&meta=siteinfo&format=json"),
            ("nasa", "https://images-api.nasa.gov/search?q=mars&media_type=image&page_size=1"),
            ("openverse", "https://api.openverse.org/v1/images/?q=mars&page_size=1"),
        ]
        reachable: list[str] = []
        for name, url in checks:
            try:
                response = requests.get(url, timeout=6, headers={"User-Agent": "ShortsFactory/1.0"})
                if response.status_code < 500:
                    reachable.append(name)
            except Exception:  # noqa: BLE001
                continue
        if len(reachable) == len(checks):
            result.checks.append(Check("internet", "ok", "Wikipedia, NASA, Openverse доступны"))
        elif reachable:
            result.checks.append(
                Check("internet", "warn", f"Доступны не все источники: {', '.join(reachable)}")
            )
        else:
            result.checks.append(
                Check(
                    "internet",
                    "warn",
                    "Внешние источники недоступны; возможен режим пользовательского сценария.",
                    "Подключите интернет или положите сценарий в input/.",
                )
            )

    def _tts_probe(self, result: DoctorResult) -> None:
        from .tts import TTSService

        service = TTSService(self.config)
        phrases = {"ru": "Проверка синтеза русской речи.", "en": "English speech synthesis check."}
        for lang, text in phrases.items():
            try:
                info = service.probe(lang, text)
                result.checks.append(
                    Check(
                        f"tts_{lang}",
                        "ok" if info.get("duration", 0) > 0.3 else "fail",
                        f"{info.get('voice')}: {info.get('duration')} с, {info.get('sample_rate')} Гц",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                result.checks.append(
                    Check(f"tts_{lang}", "fail", str(exc), "Проверьте модель Piper и зависимости.")
                )

    def _llm_probe(self, result: DoctorResult) -> None:
        from .llm import LLMError, OllamaClient

        try:
            client = OllamaClient(self.config)
            client.resolve_model()
            reply = client.generate(
                "Reply with the single word: OK",
                system="You are a health check. Reply with one word.",
                temperature=0,
                num_predict=8,
            )
            result.checks.append(Check("llm_probe", "ok", f"Ответ модели: {reply[:60]}"))
        except (LLMError, Exception) as exc:  # noqa: BLE001
            result.checks.append(Check("llm_probe", "warn", f"LLM не ответил: {exc}"))
