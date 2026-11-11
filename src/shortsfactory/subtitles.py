from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from PIL import ImageFont

from .config import Config
from .models import Script
from .timeline import Timeline, sentence_windows
from .util import atomic_write_json, atomic_write_text, now_iso


class SubtitleError(RuntimeError):
    pass


SUBTITLE_MAX_WIDTH = 900
TITLE_MAX_WIDTH = 920
SUBTITLE_MAX_LINES = 2


@dataclass
class Cue:
    start: float
    end: float
    text: str
    scene_id: str
    kind: str = "subtitle"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def font_file(config: Config) -> Path:
    path = config.fonts_dir / "Montserrat-ExtraBold.ttf"
    if not path.exists():
        raise SubtitleError(f"Не найден шрифт: {path}")
    return path


def _font(config: Config, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_file(config)), size=size)


def wrap_lines(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: float,
    max_lines: int = 2,
) -> list[str] | None:
    # Split only on regular spaces: non-breaking spaces keep abbreviations such as
    # «н. э.» together on one line.
    words = [word for word in text.split(" ") if word]
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) >= max_lines:
                return None
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        return None
    if any(font.getlength(line) > max_width for line in lines):
        return None
    return lines


def _split_evenly(words: list[str], groups: int) -> list[list[str]]:
    groups = max(1, min(groups, len(words)))
    total = sum(len(word) + 1 for word in words)
    target = total / groups
    result: list[list[str]] = [[] for _ in range(groups)]
    index = 0
    current = 0
    for word in words:
        if index < groups - 1 and current >= target * (index + 1):
            index += 1
        result[index].append(word)
        current += len(word) + 1
    return [group for group in result if group]


_ABBREVIATION_PAIRS = {
    ("н.", "э."),
    ("г.", "э."),
    ("в.", "э."),
    ("т.", "е."),
    ("т.", "д."),
    ("т.", "п."),
    ("т.", "к."),
}


def _lexical_units(words: list[str]) -> list[str]:
    units: list[str] = []
    index = 0
    while index < len(words):
        pair = (
            words[index].lower(),
            words[index + 1].lower().rstrip(",;:"),
        ) if index + 1 < len(words) else ("", "")
        if pair in _ABBREVIATION_PAIRS:
            units.append(f"{words[index]}\u00a0{words[index + 1]}")
            index += 2
        else:
            units.append(words[index])
            index += 1
    return units


def _chunk_sentence(
    text: str,
    duration: float,
    font: ImageFont.FreeTypeFont,
    max_width: float,
) -> list[tuple[str, float]]:
    words = _lexical_units(text.split())
    if not words:
        return []
    groups = max(1, math.ceil(duration / 4.2), math.ceil(len(text) / 96))
    for _ in range(6):
        parts = _split_evenly(words, groups)
        chunks: list[tuple[str, float]] = []
        fit = True
        for part in parts:
            raw = " ".join(part)
            lines = wrap_lines(raw, font, max_width, SUBTITLE_MAX_LINES)
            if not lines:
                fit = False
                break
            chunks.append(("\n".join(lines), float(len(raw))))
        if fit:
            total_chars = sum(item[1] for item in chunks) or 1.0
            result: list[tuple[str, float]] = []
            cursor = 0.0
            for index, (chunk, weight) in enumerate(chunks):
                if index == len(chunks) - 1:
                    piece = max(0.2, duration - cursor)
                else:
                    piece = max(0.7, duration * weight / total_chars)
                    piece = min(piece, max(0.7, duration - cursor - 0.7 * (len(chunks) - index - 1)))
                result.append((chunk, piece))
                cursor += piece
            return result
        groups += 1
    # Last resort: one word per cue, no truncation.
    chunks = []
    for word in words:
        lines = wrap_lines(word, font, max_width, SUBTITLE_MAX_LINES)
        chunks.append(("\n".join(lines or [word]), max(1.0, len(word))))
    return chunks


def build_cues(
    script: Script,
    manifest: dict[str, Any],
    timeline: Timeline,
    config: Config,
) -> list[Cue]:
    size = 64 if script.lang == "ru" else 68
    font = _font(config, size)
    cues: list[Cue] = []
    for window in sentence_windows(manifest, timeline):
        text = window["text"].strip()
        if not text:
            continue
        duration = max(0.4, float(window["end"]) - float(window["start"]))
        chunks = _chunk_sentence(text, duration, font, SUBTITLE_MAX_WIDTH)
        cursor = float(window["start"])
        for chunk, piece in chunks:
            end = min(float(window["end"]), cursor + piece)
            if end - cursor < 0.3:
                end = min(float(window["end"]), cursor + 0.3)
            cues.append(Cue(start=round(cursor, 3), end=round(end, 3), text=chunk, scene_id=window["scene_id"]))
            cursor = end
        if cues:
            cues[-1].end = max(cues[-1].end, min(float(window["end"]), cues[-1].start + 0.3))
    cues.sort(key=lambda cue: cue.start)
    return cues


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:d}:{minutes:02d}:{secs:05.2f}"


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    millis = int(round(seconds * 1000))
    hours, rem = divmod(millis, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _escape_ass(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace("{", "(").replace("}", ")")
    return text.replace("\n", "\\N")


def _highlight_numbers(text: str) -> str:
    return re.sub(
        r"(?<![\w])(\d[\d.,:]*\s?(?:%|°)?)",
        r"{\\c&H0000D7FF&}\1{\\c&H00FFFFFF&}",
        text,
    )


def _scene_titles(script: Script, timeline: Timeline) -> list[Cue]:
    titles: list[Cue] = []
    for index, scene in enumerate(script.scenes):
        template = (scene.template or "").lower()
        if template.startswith("card_") or template in {"stat", "quote", "timeline", "diagram"}:
            continue
        text = (scene.on_screen or " ".join(scene.text.split()[:4])).strip()
        if not text:
            continue
        duration = timeline.timing(scene.id)
        if not duration:
            continue
        start = duration.video_start + 0.12
        end = min(duration.video_start + 2.6, duration.video_start + duration.scene_duration - 0.1)
        if end <= start:
            continue
        titles.append(Cue(start=round(start, 3), end=round(end, 3), text=text, scene_id=scene.id, kind="title"))
    return titles


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Subtitle,Montserrat ExtraBold,{sub_size},&H00FFFFFF,&H000000FF,&H00101418,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,80,80,300,204
Style: SceneTitle,Montserrat ExtraBold,{title_size},&H00FFFFFF,&H000000FF,&H00101418,&H70000000,-1,0,0,0,100,100,0,0,1,5,3,8,80,80,170,204
Style: Kicker,Montserrat SemiBold,30,&H00D8D8D8,&H000000FF,&H00101418,&H00000000,-1,0,0,0,100,100,4,0,1,2,1,8,80,80,100,204

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass(
    cues: list[Cue],
    titles: list[Cue],
    path: Path,
    config: Config,
    lang: str,
) -> Path:
    sub_size = 64 if lang == "ru" else 68
    title_size = 86 if lang == "ru" else 90
    lines = [ASS_HEADER.format(sub_size=sub_size, title_size=title_size)]
    for cue in cues:
        # Escape plain text first, then insert ASS override tags. Doing it in the
        # opposite order turns {\c...} into literal "(\\c...)" on screen.
        text = _highlight_numbers(_escape_ass(cue.text))
        lines.append(
            f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Subtitle,,0,0,0,,"
            f"{{\\fad(80,60)\\an2}}{text}"
        )
    for cue in titles:
        text = _escape_ass(cue.text.upper())
        lines.append(
            f"Dialogue: 1,{_ass_time(cue.start)},{_ass_time(cue.end)},SceneTitle,,0,0,0,,"
            f"{{\\fad(180,180)\\an8}}{text}"
        )
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def write_srt(cues: list[Cue], path: Path) -> Path:
    blocks: list[str] = []
    for index, cue in enumerate(cues, 1):
        blocks.append(
            f"{index}\n{_srt_time(cue.start)} --> {_srt_time(cue.end)}\n{cue.text}\n"
        )
    atomic_write_text(path, "\n".join(blocks))
    return path


def build_subtitles(
    script: Script,
    manifest: dict[str, Any],
    timeline: Timeline,
    workspace: Path,
    config: Config,
) -> dict[str, Any]:
    out_dir = workspace / "subtitles"
    out_dir.mkdir(parents=True, exist_ok=True)
    cues = build_cues(script, manifest, timeline, config)
    if not cues:
        raise SubtitleError("Не удалось построить ни одного субтитра")
    titles = _scene_titles(script, timeline)
    ass_path = out_dir / f"{script.lang}.ass"
    srt_path = out_dir / f"{script.lang}.srt"
    write_ass(cues, titles, ass_path, config, script.lang)
    write_srt(cues, srt_path)
    payload = {
        "lang": script.lang,
        "generated_at": now_iso(),
        "cue_count": len(cues),
        "title_count": len(titles),
        "cues": [cue.to_dict() for cue in cues],
        "titles": [cue.to_dict() for cue in titles],
        "ass": ass_path.name,
        "srt": srt_path.name,
    }
    atomic_write_json(out_dir / f"{script.lang}_cues.json", payload)
    return payload


def validate_subtitles(path: Path, duration: float, lang: str) -> list[str]:
    issues: list[str] = []
    if not path.exists():
        return [f"Нет файла субтитров {path}"]
    text = path.read_text(encoding="utf-8")
    dialogue = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    subtitles = [line for line in dialogue if ",Subtitle," in line]
    if not subtitles:
        issues.append("В ASS нет ни одной реплики субтитров")
    for line in subtitles:
        parts = line.split(",", 9)
        if len(parts) < 10:
            issues.append("Некорректная строка Dialogue")
            continue
        try:
            start = _parse_ass_time(parts[1])
            end = _parse_ass_time(parts[2])
        except ValueError:
            issues.append("Некорректный таймкод ASS")
            continue
        if not (0 <= start < end <= duration + 0.15):
            issues.append(f"Субтитр вне длительности: {start:.2f}–{end:.2f}")
        if end - start < 0.25:
            issues.append(f"Слишком короткий субтитр: {end - start:.2f} с")
        content = parts[9]
        content = re.sub(r"\{[^}]*\}", "", content)
        content = content.replace("\\N", "\n")
        if not content.strip():
            issues.append("Пустой текст субтитра")
        lines = content.split("\n")
        if len(lines) > SUBTITLE_MAX_LINES:
            issues.append(f"Больше {SUBTITLE_MAX_LINES} строк в субтитре")
        for item in lines:
            if len(item) > 60:
                issues.append(f"Слишком длинная строка субтитра: {len(item)} символов")
    return issues[:20]


def _parse_ass_time(value: str) -> float:
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def cue_sheet(workspace: Path, lang: str) -> dict[str, Any]:
    path = workspace / "subtitles" / f"{lang}_cues.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
