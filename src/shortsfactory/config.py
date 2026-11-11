from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(os.environ.get("SHORTSFACTORY_ROOT", Path(__file__).resolve().parents[2])).resolve()


class ConfigError(RuntimeError):
    pass


class Config:
    """Configuration loaded from config/config.toml and config/profiles.toml."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or ROOT).resolve()
        self.config_path = Path(os.environ.get("SHORTSFACTORY_CONFIG", self.root / "config" / "config.toml"))
        self.profiles_path = Path(
            os.environ.get("SHORTSFACTORY_PROFILES", self.root / "config" / "profiles.toml")
        )
        self.data = self._load(self.config_path)
        self.profiles = self._load(self.profiles_path)

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise ConfigError(f"Не найден конфигурационный файл: {path}")
        try:
            with path.open("rb") as fh:
                return tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Ошибка TOML в {path}: {exc}") from exc

    def section(self, name: str) -> dict[str, Any]:
        value = self.data.get(name, {})
        if not isinstance(value, dict):
            raise ConfigError(f"Секция [{name}] должна быть таблицей")
        return value

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.section(section).get(key, default)

    def profile(self, name: str) -> dict[str, Any]:
        value = self.profiles.get(name)
        if not isinstance(value, dict):
            raise ConfigError(f"Неизвестный тематический профиль: {name}")
        return value

    def profile_names(self) -> list[str]:
        return sorted(self.profiles.keys())

    def path(self, name: str, default: str) -> Path:
        raw = str(self.get("paths", name, default))
        path = Path(raw)
        return path if path.is_absolute() else (self.root / path)

    @property
    def fonts_dir(self) -> Path:
        return self.path("fonts", "fonts")

    @property
    def models_dir(self) -> Path:
        return self.path("models", "models/piper")

    @property
    def workspace_dir(self) -> Path:
        return self.path("workspace", "workspace")

    @property
    def output_dir(self) -> Path:
        return self.path("output", "output")

    @property
    def input_dir(self) -> Path:
        return self.path("input", "input")

    @property
    def logs_dir(self) -> Path:
        return self.path("logs", "logs")

    @property
    def database_path(self) -> Path:
        return self.path("database", "database/shortsfactory.db")

    def resolve(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (self.root / path)

    def ensure_tree(self) -> None:
        directories = [
            self.workspace_dir,
            self.output_dir,
            self.output_dir / "RU",
            self.output_dir / "EN",
            self.output_dir / "previews",
            self.output_dir / "metadata",
            self.output_dir / "subtitles",
            self.output_dir / "reports",
            self.input_dir,
            self.logs_dir,
            self.database_path.parent,
            self.fonts_dir,
            self.models_dir,
            self.workspace_dir / "_cache",
            self.workspace_dir / "_tmp",
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)


def load_config(root: Path | None = None) -> Config:
    config = Config(root)
    config.ensure_tree()
    return config
