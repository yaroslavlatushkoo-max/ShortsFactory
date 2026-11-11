"""Story Studio adapter: text -> validated semantic Project."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import IntegrationError, IntegrationPaths, run_json_worker, write_json
from .text_story import parse_story_text


@dataclass
class StoryResult:
    spec: dict[str, Any]
    project: dict[str, Any]
    validation: dict[str, Any]
    spec_path: Path
    project_path: Path
    validation_path: Path


class StoryStudioAdapter:
    """Owns semantic parsing and Story Studio validation."""

    def __init__(self, paths: IntegrationPaths):
        self.paths = paths
        self.worker = Path(__file__).parent / "workers" / "story_studio_worker.py"

    def create(self, text: str, out_dir: Path, *, title: str | None = None) -> StoryResult:
        spec = parse_story_text(text, title=title)
        spec_path = write_json(out_dir / "story_spec.json", spec)
        payload = {
            "story_src": str(self.paths.story_studio_root / "src"),
            "spec": spec,
        }
        result = run_json_worker(
            self.paths.story_python,
            self.worker,
            payload,
            cwd=self.paths.story_studio_root,
            env={"PYTHONPATH": str(self.paths.story_studio_root / "src")},
            timeout=120,
        )
        project = result["project"]
        validation = result["validation"]
        project_path = write_json(out_dir / "project.json", project)
        validation_path = write_json(out_dir / "validation.json", validation)
        if int(validation.get("error_count", 0)) > 0:
            messages = [
                f"{issue.get('code')}: {issue.get('message')}"
                for issue in validation.get("issues", [])
                if issue.get("severity") == "ERROR"
            ]
            raise IntegrationError(
                "Story Studio отклонил сценарий: " + "; ".join(messages[:8])
            )
        return StoryResult(
            spec=spec,
            project=project,
            validation=validation,
            spec_path=spec_path,
            project_path=project_path,
            validation_path=validation_path,
        )
