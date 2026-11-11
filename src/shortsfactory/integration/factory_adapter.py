"""ShortsFactory adapter: compiled scene -> real Animation Engine render."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..animation import AnimationEngine, StoryboardError
from ..animation.storyboard import compile_storyboard
from .contracts import IntegrationError, read_json, write_json


@dataclass
class FactoryResult:
    storyboard_path: Path
    scene_path: Path
    video: Path
    contact_sheet: Path | None
    report_json: Path
    report_md: Path
    result: dict[str, Any]
    report: dict[str, Any]


class ShortsFactoryAdapter:
    """The only place that invokes the existing artistic renderer."""

    def __init__(self, config: Any = None):
        self.config = config

    def render(self, storyboard: dict[str, Any], out_dir: Path) -> FactoryResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        storyboard_path = write_json(out_dir / "storyboard.source.json", storyboard)
        try:
            compiled = compile_storyboard(storyboard)
        except StoryboardError as exc:
            raise IntegrationError(f"Factory не скомпилировал storyboard: {exc}") from exc
        except Exception as exc:
            raise IntegrationError(f"Ошибка компиляции storyboard: {exc}") from exc
        scene_path = write_json(out_dir / "scene.compiled.json", compiled)
        try:
            result = AnimationEngine(self.config).render(scene_path, out_dir)
        except Exception as exc:
            raise IntegrationError(f"Ошибка рендеринга ShortsFactory: {exc}") from exc
        result_dict = result.to_dict()
        report_path = Path(result.report_json)
        report = read_json(report_path) if report_path.is_file() else {}
        video = Path(result.video)
        if not video.is_file():
            raise IntegrationError(f"Рендерер не создал MP4: {video}")
        contact = Path(result.contact_sheet) if result.contact_sheet else None
        return FactoryResult(
            storyboard_path=storyboard_path,
            scene_path=scene_path,
            video=video,
            contact_sheet=contact if contact and contact.is_file() else None,
            report_json=report_path,
            report_md=Path(result.report),
            result=result_dict,
            report=report,
        )
