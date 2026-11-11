from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from .config import Config
from .logging_setup import get_logger
from .models import Plan, Project, Script, Source
from .util import (
    atomic_write_json,
    clean_text,
    ffmpeg_bin,
    ffprobe_json,
    normalize_match,
    now_iso,
    run,
    sentence_split,
)


class MetadataError(RuntimeError):
    pass


class MetadataService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = get_logger()
        self.font_regular = config.fonts_dir / "Montserrat-SemiBold.ttf"
        self.font_bold = config.fonts_dir / "Montserrat-ExtraBold.ttf"

    # --------------------------------------------------------------------- text
    @staticmethod
    def _clip(text: str, limit: int) -> str:
        text = clean_text(text)
        if len(text) <= limit:
            return text
        cut = text[: limit - 1].rsplit(" ", 1)[0]
        return (cut or text[: limit - 1]).rstrip(" ,.;:") + "…"

    @classmethod
    def _summarize(cls, text: str, limit: int) -> str:
        """Use complete sentences; clip only when a single sentence exceeds the limit."""
        text = clean_text(text)
        if len(text) <= limit:
            return text
        parts: list[str] = []
        total = 0
        for sentence in sentence_split(text):
            sentence = clean_text(sentence)
            if not sentence:
                continue
            needed = len(sentence) + (1 if parts else 0)
            if total + needed > limit:
                if not parts:
                    return cls._clip(sentence, limit)
                break
            parts.append(sentence)
            total += needed
        return " ".join(parts) if parts else cls._clip(text, limit)

    @staticmethod
    def _dedupe_hook(hook: str, body: str) -> str:
        """Remove sentences already spoken in the hook from the description body."""
        hook_norm = normalize_match(hook)
        kept: list[str] = []
        for sentence in sentence_split(body):
            norm = normalize_match(sentence)
            if len(norm) >= 20 and (norm in hook_norm or hook_norm in norm):
                continue
            kept.append(clean_text(sentence))
        return " ".join(item for item in kept if item)

    @staticmethod
    def _video_duration(video: Path) -> float:
        try:
            data = ffprobe_json(video)
            return round(float(data["format"]["duration"]), 2)
        except (OSError, ValueError, KeyError, TypeError):
            return 0.0

    def _hashtags(self, script: Script, plan: Plan, profile: dict[str, Any]) -> list[str]:
        key = "hashtags_ru" if script.lang == "ru" else "hashtags_en"
        raw = list(profile.get(key) or [])
        raw += ["#shorts", "#shortsvideo"] if script.lang == "en" else ["#shorts", "#вертикальноевидео"]
        result: list[str] = []
        for item in raw:
            value = str(item).strip()
            if not value:
                continue
            if not value.startswith("#"):
                value = "#" + re.sub(r"\s+", "", value)
            if value.lower() not in {existing.lower() for existing in result}:
                result.append(value)
        return result[:8]

    @classmethod
    def _description_core(cls, script: Script) -> str:
        """Hook and summary without sources, hashtags or the image-license footer."""
        intro = clean_text(script.hook or script.title)
        body = " ".join(scene.text for scene in script.scenes[:3])
        body = cls._dedupe_hook(intro, body) or body
        summary = cls._summarize(body, 420)
        return f"{intro}\n\n{summary}" if summary and normalize_match(summary) not in normalize_match(intro) else intro

    @classmethod
    def _description(
        cls, script: Script, sources: list[Source], hashtags: list[str], lang: str
    ) -> str:
        header = cls._description_core(script)
        if lang == "ru":
            source_header = "Источники и материалы:"
            footer = "Изображения: Wikimedia Commons / NASA / Openverse; лицензии указаны в отчёте проекта."
        else:
            source_header = "Sources and materials:"
            footer = "Images: Wikimedia Commons / NASA / Openverse; licenses are listed in the project report."
        lines = [header, "", source_header]
        if sources:
            for source in sources[:6]:
                publisher = f" — {source.publisher}" if source.publisher else ""
                lines.append(f"- {source.title}{publisher}: {source.url}")
        else:
            lines.append("- User-provided or locally verified material (see project report).")
        lines += ["", footer, "", " ".join(hashtags)]
        return "\n".join(lines).strip()[:4900]

    # ----------------------------------------------------------------- thumbnail
    def _extract_frame(self, video: Path, destination: Path) -> bool:
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = run(
            [
                ffmpeg_bin(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                "1.8",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(destination),
            ],
            cwd=self.config.root,
            timeout=120,
        )
        return result.returncode == 0 and destination.exists()

    def _thumbnail(self, video: Path, script: Script, out: Path, lang: str) -> str:
        frame = out.parent.parent / "quality" / "frames" / f"thumbnail_source_{lang}.jpg"
        if not self._extract_frame(video, frame):
            raise MetadataError(f"Не удалось извлечь кадр для обложки: {video}")
        try:
            with Image.open(frame) as source:
                image = ImageOps.fit(source.convert("RGB"), (1280, 720), Image.Resampling.LANCZOS, centering=(0.5, 0.45))
        except OSError as exc:
            raise MetadataError(f"Не открывается кадр обложки: {exc}") from exc
        image = ImageEnhance.Contrast(image).enhance(1.04)
        shade = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shade_draw = ImageDraw.Draw(shade)
        for x in range(1280):
            alpha = int(190 * (1 - x / 1280) ** 0.65)
            shade_draw.line((x, 0, x, 720), fill=(4, 8, 14, alpha))
        image = Image.alpha_composite(image.convert("RGBA"), shade).convert("RGB")
        draw = ImageDraw.Draw(image)
        title = self._clip(script.title, 64)
        font_path = self.font_bold
        size = 82
        font = ImageFont.truetype(str(font_path), size)
        while size > 44:
            words = title.split()
            lines: list[str] = []
            current = ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if font.getlength(candidate) <= 640:
                    current = candidate
                else:
                    if current:
                        lines.append(current)
                    current = word
            if current:
                lines.append(current)
            if len(lines) <= 3:
                break
            size -= 4
            font = ImageFont.truetype(str(font_path), size)
        y = 300 - (len(lines) - 1) * int(size * 0.55)
        for line in lines:
            draw.text((70, y), line, font=font, fill=(255, 255, 255), stroke_width=5, stroke_fill=(6, 10, 18))
            y += int(size * 1.02)
        draw.rectangle((70, 250, 190, 258), fill=(245, 197, 66))
        label_font = ImageFont.truetype(str(self.font_regular), 28)
        draw.text((70, 190), "SHORTSFACTORY", font=label_font, fill=(235, 240, 250))
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(out, "JPEG", quality=92, optimize=True)
        return out.as_posix()

    # ---------------------------------------------------------------------- main
    def build(
        self,
        project: Project,
        plan: Plan,
        script: Script,
        sources: list[Source],
        workspace: Path,
        output_dir: Path,
        video: Path,
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        lang = script.lang
        title = self._clip(script.title, 95)
        hashtags = self._hashtags(script, plan, profile)
        tags = [item.lstrip("#") for item in hashtags]
        description_core = self._description_core(script)
        description = self._description(script, sources, hashtags, lang)
        thumbnail = self._thumbnail(video, script, output_dir / "previews" / f"thumbnail_{lang}.jpg", lang)
        duration = self._video_duration(video) or project.duration

        meta_dir = output_dir / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "language": lang,
            "title": title,
            "description": description,
            "hashtags": hashtags,
            "tags": tags,
            "video": video.as_posix(),
            "thumbnail": thumbnail,
            "duration": duration,
            "project": project.id,
            "topic": project.topic,
            "profile": plan.profile,
            "sources": [source.to_dict() for source in sources],
            "generated_at": now_iso(),
        }
        atomic_write_json(meta_dir / f"{lang}.json", payload)
        lines = [
            f"# {title}",
            "",
            f"**Язык:** {lang.upper()}  ",
            f"**Видео:** `{video}`  ",
            f"**Обложка:** `{thumbnail}`  ",
            f"**Длительность:** {duration:g} с",
            "",
            "## Описание",
            "",
            description_core,
            "",
            "## Хештеги",
            "",
            " ".join(hashtags),
            "",
            "## Источники",
            "",
        ]
        if sources:
            for source in sources:
                lines.append(
                    f"- [{source.title}]({source.url}) — {source.publisher or 'источник'}, "
                    f"дата обращения {source.accessed_at}, лицензия: {source.license or 'не указана'}"
                )
        else:
            lines.append("- Пользовательский проверенный материал.")
        (meta_dir / f"{lang}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

        upload = [
            f"TITLE ({lang.upper()}):",
            title,
            "",
            "DESCRIPTION:",
            description,
            "",
            "TAGS:",
            ", ".join(tags),
            "",
            f"VIDEO FILE: {video}",
            f"THUMBNAIL: {thumbnail}",
            f"SUBTITLES: {output_dir / 'subtitles' / (lang + '.srt')}",
            "",
            "Загрузите видео в YouTube Shorts вручную; автоматическая публикация не выполняется.",
        ]
        (meta_dir / f"upload_{lang}.txt").write_text("\n".join(upload), encoding="utf-8")
        self.logger.info("Метаданные %s: %s", lang, title)
        return payload
