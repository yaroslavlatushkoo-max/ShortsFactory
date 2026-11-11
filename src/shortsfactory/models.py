from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Source:
    id: str
    title: str
    url: str
    publisher: str
    language: str
    accessed_at: str
    revision_date: str = ""
    license: str = ""
    extract: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Source":
        return cls(**{key: data.get(key, "") for key in cls.__dataclass_fields__})


@dataclass
class Scene:
    id: str
    text: str
    visual: str = ""
    visual_query: str = ""
    on_screen: str = ""
    source_ids: list[str] = field(default_factory=list)
    transition: str = "fade"
    template: str = ""
    speech_duration: float = 0.0
    scene_duration: float = 0.0
    video_start: float = 0.0
    audio_start: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scene":
        return cls(
            id=str(data.get("id", "")),
            text=str(data.get("text", "")),
            visual=str(data.get("visual", "")),
            visual_query=str(data.get("visual_query", "")),
            on_screen=str(data.get("on_screen", "")),
            source_ids=[str(item) for item in data.get("source_ids", [])],
            transition=str(data.get("transition", "fade")),
            template=str(data.get("template", "")),
            speech_duration=float(data.get("speech_duration", 0.0) or 0.0),
            scene_duration=float(data.get("scene_duration", 0.0) or 0.0),
            video_start=float(data.get("video_start", 0.0) or 0.0),
            audio_start=float(data.get("audio_start", 0.0) or 0.0),
        )


@dataclass
class Script:
    lang: str
    title: str
    hook: str
    scenes: list[Scene]
    cta: str = ""
    engine: str = ""
    model: str = ""
    prompt_version: str = ""
    warnings: list[str] = field(default_factory=list)
    word_count: int = 0
    target_words: int = 0
    created_at: str = ""

    @property
    def all_text(self) -> str:
        return " ".join(scene.text for scene in self.scenes)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scenes"] = [scene.to_dict() for scene in self.scenes]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Script":
        scenes = [Scene.from_dict(item) for item in data.get("scenes", [])]
        return cls(
            lang=data.get("lang", ""),
            title=data.get("title", ""),
            hook=data.get("hook", ""),
            scenes=scenes,
            cta=data.get("cta", ""),
            engine=data.get("engine", ""),
            model=data.get("model", ""),
            prompt_version=data.get("prompt_version", ""),
            warnings=list(data.get("warnings", [])),
            word_count=int(data.get("word_count", 0)),
            target_words=int(data.get("target_words", 0)),
            created_at=data.get("created_at", ""),
        )

    def markdown(self, sources: list[Source] | None = None) -> str:
        lines = [f"# {self.title}", "", f"**Hook:** {self.hook}", ""]
        for index, scene in enumerate(self.scenes, 1):
            lines += [
                f"## Сцена {index}. {scene.on_screen}".strip(),
                "",
                scene.text,
                "",
                f"*Визуал:* {scene.visual}",
                f"*Поиск:* `{scene.visual_query}` · *шаблон:* `{scene.template or 'auto'}`",
                f"*Источники:* {', '.join(scene.source_ids) or '—'}",
                "",
            ]
        if self.cta:
            lines += [f"**Финал:** {self.cta}", ""]
        if sources:
            lines += ["## Источники", ""]
            for source in sources:
                lines.append(f"- [{source.id}] [{source.title}]({source.url}) — {source.publisher}")
        lines += [
            "",
            "---",
            f"Движок: `{self.engine}` · модель: `{self.model}` · слов: {self.word_count}",
        ]
        return "\n".join(lines) + "\n"


@dataclass
class Plan:
    project_id: str
    topic: str
    topic_en: str
    profile: str
    languages: list[str]
    duration: float
    style: str
    pace: str
    visual_density: str
    visual_mode: str = "images"
    seed: dict[str, Any] = field(default_factory=dict)
    voice_ru: str = ""
    voice_en: str = ""
    script_path: str = ""
    animation_storyboard: str = ""
    based_on: str = ""
    created_at: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Plan":
        allowed = cls.__dataclass_fields__
        kwargs: dict[str, Any] = {}
        for key in allowed:
            if key in data:
                kwargs[key] = data[key]
        kwargs.setdefault("project_id", "")
        kwargs.setdefault("topic", "")
        kwargs.setdefault("topic_en", "")
        kwargs.setdefault("profile", "explainer")
        kwargs.setdefault("languages", ["ru", "en"])
        kwargs.setdefault("duration", 40)
        kwargs.setdefault("style", "auto")
        kwargs.setdefault("pace", "medium")
        kwargs.setdefault("visual_density", "medium")
        kwargs.setdefault("visual_mode", "images")
        return cls(**kwargs)


@dataclass
class Project:
    id: str
    topic: str
    profile: str
    languages: list[str]
    duration: float
    status: str
    stage: str
    workspace: str
    output: str
    command: str
    created_at: str
    updated_at: str
    error: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["languages"] = list(self.languages)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        return cls(
            id=data.get("id", ""),
            topic=data.get("topic", ""),
            profile=data.get("profile", "explainer"),
            languages=list(data.get("languages", ["ru", "en"])),
            duration=float(data.get("duration", 40)),
            status=data.get("status", "QUEUED"),
            stage=data.get("stage", "PLANNING"),
            workspace=data.get("workspace", ""),
            output=data.get("output", ""),
            command=data.get("command", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            error=data.get("error", ""),
            params=dict(data.get("params", {})),
        )


@dataclass
class StageRecord:
    project_id: str
    name: str
    status: str
    started_at: str = ""
    finished_at: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QualityCheck:
    id: str
    label: str
    level: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LanguageQC:
    language: str
    status: str
    checks: list[QualityCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manual_review: bool = True
    video: str = ""
    contact_sheet: str = ""
    generated_at: str = ""

    @property
    def mandatory_failed(self) -> list[QualityCheck]:
        return [check for check in self.checks if check.level == "mandatory" and not check.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
            "warnings": self.warnings,
            "manual_review": self.manual_review,
            "video": self.video,
            "contact_sheet": self.contact_sheet,
            "generated_at": self.generated_at,
        }


STAGES = [
    "PLANNING",
    "RESEARCH",
    "SCRIPTING",
    "LOCALIZATION",
    "ASSETS",
    "VOICE",
    "EDITING",
    "RENDERING",
    "METADATA",
    "QUALITY_CHECK",
]

#: Selectable visual production modes. ``images`` is the existing static-image
#: pipeline; ``animated`` renders an authored 2D animation scene instead.
VISUAL_MODES = ["images", "animated"]

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_READY = "READY"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"
STATUS_INTERRUPTED = "INTERRUPTED"
