from __future__ import annotations

import json
import re
import statistics
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from .config import Config
from .logging_setup import get_logger
from .models import LanguageQC, Project, QualityCheck, Script
from .util import (
    dhash,
    content_tokens,
    ffmpeg_bin,
    ffprobe_json,
    format_duration,
    jaccard,
    normalize_match,
    now_iso,
    read_json,
    run,
    sha256_text,
)


class QualityError(RuntimeError):
    pass


def _fraction(value: str) -> float:
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            return float(numerator) / float(denominator or 1)
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _parse_ass(path: Path) -> dict[str, Any]:
    data = {"styles": {}, "events": [], "play_res": (1080, 1920)}
    text = path.read_text(encoding="utf-8", errors="replace")
    in_events = False
    fields = ["layer", "start", "end", "style", "name",
              "marginl", "marginr", "marginv", "effect", "text"]
    for line in text.splitlines():
        if line.startswith("PlayResX:"):
            try:
                data["play_res"] = (int(line.split(":", 1)[1]), data["play_res"][1])
            except ValueError:
                pass
        elif line.startswith("PlayResY:"):
            try:
                data["play_res"] = (data["play_res"][0], int(line.split(":", 1)[1]))
            except ValueError:
                pass
        elif line.strip() == "[Events]":
            in_events = True
        elif line.startswith("Format:") and in_events:
            fields = [item.strip().lower()
                      for item in line.split(":", 1)[1].split(",")]
        elif line.startswith("Style:"):
            parts = [item.strip() for item in line.split(":", 1)[1].split(",")]
            if parts:
                data["styles"][parts[0]] = parts
        elif line.startswith("Dialogue:") and in_events:
            body = line.split(":", 1)[1]
            # Text — последнее поле и может содержать запятые
            parts = [p.strip() for p in body.split(",", len(fields) - 1)]
            if len(parts) < len(fields):
                continue
            row = dict(zip(fields, parts))
            data["events"].append({
                "layer": row.get("layer", "0"),
                "start": row.get("start", ""),
                "end": row.get("end", ""),
                "style": row.get("style", ""),
                "text": row.get("text", ""),
            })
    return data


def _ass_seconds(value: str) -> float:
    match = re.match(r"(\d+):(\d+):(\d+)[.:](\d+)", value)
    if not match:
        return 0.0
    hours, minutes, seconds, fraction = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction) / (10 ** len(fraction))


class QualityService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = get_logger()
        self.tolerance = float(config.get("quality", "duration_tolerance", 0.12))
        self.black_start_max = float(config.get("quality", "black_start_max", 0.25))
        self.black_end_max = float(config.get("quality", "black_end_max", 0.25))
        self.min_audio = float(config.get("quality", "min_audio_lufs", -24))
        self.max_audio = float(config.get("quality", "max_audio_lufs", -9))
        self.manual_visual = bool(config.get("quality", "manual_visual_review", True))
        self.cfg_width = int(config.get("video", "width", 1080))
        self.cfg_height = int(config.get("video", "height", 1920))
        self.cfg_fps = float(config.get("video", "fps", 30))
        self.target_lufs = float(config.get("audio", "target_lufs", -14.0))
        self.target_true_peak = float(config.get("audio", "true_peak", -1.5))

    # ------------------------------------------------------------------ technical
    def _technical(
        self,
        video: Path,
        expected: float,
        subtitles: Path,
        lang: str,
        checks: list[QualityCheck],
        warnings: list[str],
    ) -> dict[str, Any]:
        def add(check_id: str, label: str, passed: bool, detail: str, level: str = "mandatory") -> None:
            checks.append(QualityCheck(check_id, label, level, passed, detail))

        if not video.exists() or not video.is_file():
            add("file", "Файл существует", False, f"Нет файла: {video}")
            return {}
        add("file", "Файл существует", True, f"{video.name}, {video.stat().st_size / 1024 / 1024:.1f} МБ")

        try:
            probe = ffprobe_json(video)
        except Exception as exc:
            add("container", "Контейнер читается", False, str(exc))
            return {}
        format_info = probe.get("format") or {}
        streams = probe.get("streams") or []
        add("container", "Контейнер читается", bool(format_info), str(format_info.get("format_name", "неизвестно")))

        video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
        audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
        add("video_stream", "Видеопоток присутствует", bool(video_stream), (video_stream or {}).get("codec_name", "нет"))
        add("audio_stream", "Аудиопоток присутствует", bool(audio_stream), (audio_stream or {}).get("codec_name", "нет"))

        duration = float(format_info.get("duration") or (video_stream or {}).get("duration") or 0)
        low, high = expected * (1 - self.tolerance), expected * (1 + self.tolerance)
        add(
            "duration",
            "Длительность корректна",
            low <= duration <= high,
            f"{duration:.2f} с при цели {expected:.1f} с (допуск {low:.1f}–{high:.1f})",
        )
        width = int((video_stream or {}).get("width") or 0)
        height = int((video_stream or {}).get("height") or 0)
        add(
            "resolution",
            "Разрешение корректно",
            width == self.cfg_width and height == self.cfg_height,
            f"{width}×{height}, ожидается {self.cfg_width}×{self.cfg_height}",
        )
        fps = _fraction(str((video_stream or {}).get("avg_frame_rate") or (video_stream or {}).get("r_frame_rate") or "0"))
        add("fps", "Частота кадров допустима", abs(fps - self.cfg_fps) <= 0.15, f"{fps:.2f} fps, ожидается {self.cfg_fps:g}")
        pix_fmt = str((video_stream or {}).get("pix_fmt", ""))
        audio_codec = str((audio_stream or {}).get("codec_name", ""))
        sample_rate = int((audio_stream or {}).get("sample_rate") or 0)
        channels = int((audio_stream or {}).get("channels") or 0)
        add(
            "profile",
            "Финальная сборка соответствует профилю",
            str((video_stream or {}).get("codec_name", "")) == "h264"
            and pix_fmt == "yuv420p"
            and audio_codec == "aac"
            and sample_rate == 48000
            and channels == 2,
            f"video={str((video_stream or {}).get('codec_name', 'нет'))}/{pix_fmt}, "
            f"audio={audio_codec}/{sample_rate} Гц/{channels} кан.",
        )

        # Full decode is a cheap independent integrity check.
        decode = run(
            [ffmpeg_bin(), "-hide_banner", "-v", "error", "-i", str(video), "-map", "0", "-f", "null", "-"],
            cwd=self.config.root,
            timeout=900,
        )
        add("decode", "Файл полностью декодируется", decode.returncode == 0 and not decode.stderr.strip(), (decode.stderr.strip() or "ошибок нет")[-500:])

        # Loudness of the actual mix.
        loud = run(
            [
                ffmpeg_bin(),
                "-hide_banner",
                "-nostats",
                "-i",
                str(video),
                "-map",
                "0:a:0",
                "-af",
                "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
                "-f",
                "null",
                "-",
            ],
            cwd=self.config.root,
            timeout=300,
        )
        input_i = None
        input_tp = None
        if loud.stderr:
            blocks = re.findall(r"\{[^{}]+\}", loud.stderr, flags=re.S)
            for block in reversed(blocks):
                try:
                    value = json.loads(block)
                    if "input_i" in value:
                        input_i = float(value["input_i"])
                        input_tp = float(value.get("input_tp", 0.0))
                        break
                except (ValueError, TypeError):
                    continue
        audio_ok = input_i is not None and abs(input_i - self.target_lufs) <= 1.5
        add(
            "audio_level",
            "Звук не отсутствует",
            audio_ok,
            "интегральная громкость: " + (f"{input_i:.1f} LUFS (цель {self.target_lufs:.1f}±1.5)" if input_i is not None else "не определена"),
        )
        tp_ok = input_tp is not None and input_tp <= -1.0
        add(
            "true_peak",
            "True peak имеет запас ниже 0 dBTP",
            tp_ok,
            (f"{input_tp:.2f} dBTP (нужно ≤ -1.0)" if input_tp is not None else "не измерен"),
        )

        black = run(
            [
                ffmpeg_bin(),
                "-hide_banner",
                "-i",
                str(video),
                "-vf",
                "blackdetect=d=0.10:pix_th=0.10",
                "-an",
                "-f",
                "null",
                "-",
            ],
            cwd=self.config.root,
            timeout=600,
        )
        intervals: list[tuple[float, float]] = []
        for match in re.finditer(r"black_start:([\d.]+)\s+black_end:([\d.]+)", black.stderr or ""):
            intervals.append((float(match.group(1)), float(match.group(2))))
        start_bad = any(start < self.black_start_max for start, _ in intervals)
        end_bad = any(end > duration - self.black_end_max for _, end in intervals)
        add(
            "black_start",
            "Нет неожиданного чёрного начала",
            not start_bad,
            f"чёрных интервалов: {len(intervals)}" + (f", первый с {intervals[0][0]:.2f} с" if intervals else ""),
        )
        add(
            "black_end",
            "Нет неожиданного чёрного конца",
            not end_bad,
            f"чёрных интервалов: {len(intervals)}" + (f", последний до {intervals[-1][1]:.2f} с" if intervals else ""),
        )

        # застывшие кадры (advisory)
        freeze = run(
            [
                ffmpeg_bin(),
                "-hide_banner",
                "-i",
                str(video),
                "-vf",
                "freezedetect=n=-50dB:d=2",
                "-an",
                "-f",
                "null",
                "-",
            ],
            cwd=self.config.root,
            timeout=600,
        )
        freeze_starts = re.findall(r"freeze_start:([\d.]+)", freeze.stderr or "")
        freeze_ends = re.findall(r"freeze_end:([\d.]+)", freeze.stderr or "")
        long_freezes = [
            (float(s), float(e))
            for s, e in zip(freeze_starts, freeze_ends)
            if float(e) - float(s) > 2.0
        ]
        if long_freezes:
            spans = ", ".join(f"{s:.1f}-{e:.1f}с" for s, e in long_freezes[:6])
            warnings.append(f"Застывшие участки >2с: {spans}")

        subs_ok = False
        subs_detail = "файл субтитров отсутствует"
        if subtitles.exists():
            try:
                ass = _parse_ass(subtitles)
                events = ass["events"]
                play_w, play_h = ass["play_res"]
                style = ass["styles"].get("Subtitle") or []
                font_size = float(style[2]) if len(style) > 2 and style[2].replace(".", "", 1).isdigit() else 0
                margin_v = float(style[21]) if len(style) > 21 and style[21].replace(".", "", 1).isdigit() else 0
                readable = (
                    bool(events)
                    and play_w == 1080
                    and play_h == 1920
                    and font_size >= 48
                    and margin_v >= 200
                    and all(_ass_seconds(event["end"]) <= duration + 0.25 for event in events)
                    and all(len(event["text"].replace("\\N", "\n").splitlines()) <= 2 for event in events)
                )
                subs_ok = readable
                subs_detail = f"событий: {len(events)}, кегль: {font_size:.0f}, нижний отступ: {margin_v:.0f}"
            except (OSError, ValueError) as exc:
                subs_detail = f"не удалось разобрать ASS: {exc}"
        add("subtitles", "Субтитры находятся в пределах кадра", subs_ok, subs_detail)
        warnings.append("Читаемость субтитров и художественная композиция требуют быстрой ручной проверки.")
        return {
            "duration": duration,
            "width": width,
            "height": height,
            "fps": fps,
            "size": int(format_info.get("size") or video.stat().st_size),
        }

    # -------------------------------------------------------------------- content
    def _content(
        self,
        project: Project,
        script: Script,
        lang: str,
        workspace: Path,
        checks: list[QualityCheck],
        warnings: list[str],
        expected_topic: str = "",
    ) -> None:
        def add(check_id: str, label: str, passed: bool, detail: str, level: str = "mandatory") -> None:
            checks.append(QualityCheck(check_id, label, level, passed, detail))

        script_path = workspace / "scripts" / f"{lang}.json"
        add("script", "Сценарий существует", script_path.exists(), script_path.as_posix())
        if not script_path.exists():
            return

        topic = expected_topic or project.topic
        topic_tokens = content_tokens(topic)
        script_tokens = content_tokens(f"{script.title} {script.all_text}")
        overlap = len(topic_tokens & script_tokens) / max(1, len(topic_tokens))
        topic_ok = overlap >= 0.10 or normalize_match(topic) in normalize_match(script.all_text)
        add(
            "topic",
            "Сценарий соответствует выбранной теме",
            topic_ok,
            f"совпадение ключевых слов: {overlap:.0%}",
        )

        sources_path = workspace / "research" / "sources.json"
        sources: list[dict[str, Any]] = []
        if sources_path.exists():
            try:
                payload = read_json(sources_path)
                if isinstance(payload, dict):
                    sources = list(payload.get("sources") or [])
                elif isinstance(payload, list):
                    sources = list(payload)
            except (OSError, ValueError, TypeError):
                sources = []
        add("sources", "Источники сохранены", bool(sources), f"{len(sources)} источник(ов)")
        if not sources:
            warnings.append("Источники отсутствуют: сценарий мог быть основан на пользовательском материале.")

        manifest_path = workspace / "audio" / lang / "manifest.json"
        narration_ok = False
        narration_detail = "манифест озвучки отсутствует"
        if manifest_path.exists():
            try:
                manifest = read_json(manifest_path)
                narration_ok = (
                    manifest.get("script_sha") == sha256_text(script.all_text)
                    and float(manifest.get("total_speech", 0)) > 0.5
                )
                narration_detail = f"{float(manifest.get('total_speech', 0)):.2f} с речи"
            except (OSError, ValueError, TypeError):
                narration_detail = "манифест озвучки повреждён"
        add("narration", "Озвучка соответствует сценарию", narration_ok, narration_detail)

        duplicate = 0.0
        for left_index, left in enumerate(script.scenes):
            for right in script.scenes[left_index + 1 :]:
                duplicate = max(duplicate, jaccard(left.text, right.text))
        add("repeats", "Нет очевидных повторов", duplicate < 0.82, f"максимальное сходство сцен: {duplicate:.0%}")

        temp_files = [
            item
            for item in workspace.rglob("*")
            if item.is_file()
            and (
                item.suffix.lower() in {".part", ".tmp", ".temp"}
                or item.name.startswith("~$")
                or item.name.lower() in {"thumbs.db", ".ds_store"}
            )
        ]
        add("temp_files", "Нет случайных временных файлов", not temp_files, f"найдено: {len(temp_files)}")

        # Script vs narration is checked by manifest hash; scene continuity is checked visually.

    # --------------------------------------------------------------------- visual
    def _contact_sheet(
        self,
        video: Path,
        lang: str,
        duration: float,
        workspace: Path,
        output_dir: Path,
        checks: list[QualityCheck],
        warnings: list[str],
    ) -> tuple[str, list[dict[str, Any]]]:
        frame_dir = workspace / "quality" / "frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        count = 6
        frames: list[Path] = []
        for index in range(count):
            position = min(max(duration * (index + 0.5) / count, 0.1), max(0.1, duration - 0.15))
            path = frame_dir / f"{lang}_{index + 1:02d}.jpg"
            command = [
                ffmpeg_bin(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{position:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(path),
            ]
            result = run(command, cwd=self.config.root, timeout=120)
            if result.returncode == 0 and path.exists():
                frames.append(path)
        if not frames:
            checks.append(QualityCheck("contact_sheet", "Контактный лист создан", "mandatory", False, "не удалось извлечь кадры"))
            return "", []

        frame_records: list[dict[str, Any]] = []
        for path in frames:
            try:
                with Image.open(path) as image:
                    image = image.convert("RGB")
                    gray = image.convert("L")
                    mean = sum(gray.getdata()) / max(1, gray.width * gray.height)
                    spread = statistics.pstdev(list(gray.getdata())[::17]) if gray.width * gray.height else 0
                    frame_records.append(
                        {"path": path.as_posix(), "mean": mean, "spread": spread, "dhash": f"{dhash(path):016x}"}
                    )
            except (OSError, UnidentifiedImageError, ValueError):
                continue

        output_dir.mkdir(parents=True, exist_ok=True)
        sheet_path = output_dir / "previews" / f"contact_sheet_{lang}.jpg"
        sheet_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_w, thumb_h = 320, 568
        margin = 30
        gap = 20
        header = 110
        sheet = Image.new("RGB", (thumb_w * 3 + margin * 2 + gap * 2, header + thumb_h * 2 + gap + margin), (8, 13, 22))
        draw = ImageDraw.Draw(sheet)
        font_path = self.config.fonts_dir / "Montserrat-Bold.ttf"
        try:
            title_font = ImageFont.truetype(str(font_path), 34)
            small_font = ImageFont.truetype(str(font_path), 20)
        except OSError:
            title_font = ImageFont.load_default()
            small_font = ImageFont.load_default()
        draw.text((margin, 28), f"КОНТАКТНЫЙ ЛИСТ · {lang.upper()}", font=title_font, fill=(255, 255, 255))
        draw.text(
            (margin, 72),
            f"{video.name} · {format_duration(duration)} · быстрая визуальная проверка",
            font=small_font,
            fill=(180, 195, 215),
        )
        for index, path in enumerate(frames[:6]):
            try:
                with Image.open(path) as source:
                    image = ImageOps.fit(source.convert("RGB"), (thumb_w, thumb_h), Image.Resampling.LANCZOS)
            except (OSError, UnidentifiedImageError):
                continue
            x = margin + (index % 3) * (thumb_w + gap)
            y = header + (index // 3) * (thumb_h + gap)
            sheet.paste(image, (x, y))
            draw.rectangle((x, y, x + thumb_w, y + thumb_h), outline=(70, 85, 105), width=2)
            draw.text((x + 10, y + 10), f"{index + 1}", font=small_font, fill=(255, 220, 100))
        sheet.save(sheet_path, "JPEG", quality=90, optimize=True)

        add_check = checks.append
        add_check(
            QualityCheck(
                "contact_sheet",
                "Контактный лист создан",
                "mandatory",
                sheet_path.exists(),
                sheet_path.as_posix(),
            )
        )
        black = [item for item in frame_records if item["mean"] < 8]
        add_check(
            QualityCheck(
                "black_frames",
                "Нет чёрных кадров в выборке",
                "advisory",
                not black,
                f"тёмных кадров: {len(black)} из {len(frame_records)}; проверьте визуалы вручную",
            )
        )
        duplicates = 0
        for left, right in zip(frame_records, frame_records[1:]):
            if (int(left["dhash"], 16) ^ int(right["dhash"], 16)).bit_count() <= 2:
                duplicates += 1
        add_check(
            QualityCheck(
                "duplicate_frames",
                "Нет подозрительно повторяющихся кадров",
                "advisory",
                duplicates <= 1,
                f"похожих соседних кадров: {duplicates}",
            )
        )
        flat = [item for item in frame_records if item["spread"] < 3]
        add_check(
            QualityCheck(
                "composition",
                "Композиция кадров не пустая",
                "mandatory",
                not flat,
                f"пустых/однотонных кадров: {len(flat)}",
            )
        )
        if black:
            warnings.append("В выборке есть очень тёмные кадры — проверьте начало/конец и переходы.")
        warnings.append("Визуальное соответствие сценарию и отсутствие артефактов проверяются вручную по контактному листу.")
        return sheet_path.as_posix(), frame_records

    # ----------------------------------------------------------------------- main
    def check(
        self,
        project: Project,
        script: Script,
        lang: str,
        video: Path,
        workspace: Path,
        output_dir: Path,
        expected_duration: float | None = None,
        expected_topic: str = "",
    ) -> LanguageQC:
        checks: list[QualityCheck] = []
        warnings: list[str] = []
        expected = float(expected_duration or project.duration or 40)
        subtitles = workspace / "subtitles" / f"{lang}.ass"
        video_info = self._technical(video, expected, subtitles, lang, checks, warnings)
        self._content(project, script, lang, workspace, checks, warnings, expected_topic)
        sheet, _ = self._contact_sheet(video, lang, float(video_info.get("duration") or expected), workspace, output_dir, checks, warnings)

        # Subtitle geometry/readability is partly automatic, partly a manual check.
        ass_ok = any(check.id == "subtitles" and check.passed for check in checks)
        readability_ok = ass_ok
        checks.append(
            QualityCheck(
                "subtitle_readability",
                "Субтитры читаемы (эвристика)",
                "advisory",
                readability_ok,
                "проверено: размер, отступ, число строк; окончательная оценка — вручную",
            )
        )
        if self.manual_visual:
            warnings.append("Обязательна быстрая ручная проверка контактного листа перед загрузкой.")

        mandatory_failed = [check for check in checks if check.level == "mandatory" and not check.passed]
        status = "ready" if not mandatory_failed else ("partial" if video.exists() else "failed")
        qc = LanguageQC(
            language=lang,
            status=status,
            checks=checks,
            warnings=warnings,
            manual_review=self.manual_visual,
            video=video.as_posix(),
            contact_sheet=sheet,
            generated_at=now_iso(),
        )
        self._write(workspace, output_dir, qc)
        return qc

    def _write(self, workspace: Path, output_dir: Path, qc: LanguageQC) -> None:
        report_dir = workspace / "quality"
        report_dir.mkdir(parents=True, exist_ok=True)
        data = qc.to_dict()
        (report_dir / f"{qc.language}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        out_reports = output_dir / "reports"
        out_reports.mkdir(parents=True, exist_ok=True)
        (out_reports / f"quality_{qc.language}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            f"# Контроль качества · {qc.language.upper()}",
            "",
            f"**Статус:** `{qc.status}`",
            f"**Видео:** `{qc.video}`",
            f"**Контактный лист:** `{qc.contact_sheet}`",
            "",
            "## Проверки",
            "",
            "| Проверка | Уровень | Результат | Детали |",
            "|---|---|---|---|",
        ]
        for check in qc.checks:
            lines.append(
                f"| {check.label} | {check.level} | {'PASS' if check.passed else 'FAIL'} | {check.detail.replace('|', '/')} |"
            )
        lines += ["", "## Предупреждения", ""]
        lines += [f"- {item}" for item in qc.warnings] or ["- нет"]
        lines += [
            "",
            "## Ручная проверка",
            "",
            "1. Откройте контактный лист и видео.",
            "2. Сверьте визуальные материалы со сценарием.",
            "3. Проверьте читаемость субтитров на телефоне.",
            "4. Проверьте отсутствие артефактов, повторяющихся кадров и обрезки.",
            "",
        ]
        (report_dir / f"{qc.language}.md").write_text("\n".join(lines), encoding="utf-8")
        (out_reports / f"quality_{qc.language}.md").write_text("\n".join(lines), encoding="utf-8")
