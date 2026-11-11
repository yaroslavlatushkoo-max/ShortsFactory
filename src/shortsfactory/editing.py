from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .logging_setup import get_logger
from .models import Scene, Script
from .timeline import Timeline
from .util import atomic_write_json, ffmpeg_bin, now_iso, run


class RenderError(RuntimeError):
    pass


@dataclass
class RenderResult:
    video: str
    lang: str
    duration: float
    width: int
    height: int
    fps: int
    size_bytes: int
    scenes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    command: str = ""
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["generated_at"] = self.generated_at or now_iso()
        return data


class RenderService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = get_logger()
        self.width = int(config.get("video", "width", 1080))
        self.height = int(config.get("video", "height", 1920))
        self.fps = int(config.get("video", "fps", 30))
        self.crf = int(config.get("video", "crf", 18))
        self.preset = str(config.get("video", "preset", "medium"))
        self.transition = float(config.get("video", "transition", 0.4))
        self.fonts_dir = config.fonts_dir

    # --------------------------------------------------------------------- scenes
    def _scene_manifest_path(self, workspace: Path, lang: str) -> Path:
        return workspace / "render" / lang / "scenes.json"

    def _motion(self, scene: Scene, duration: float, index: int, style: str, pace: str) -> tuple[str, str]:
        frames = max(1, round(duration * self.fps))
        rate = {"slow": 0.00035, "fast": 0.0012}.get(pace, 0.00075)
        if style in {"cinematic"}:
            rate *= 0.8
        variant = index % 4
        if variant == 0:
            zoom = f"min(1.03+{rate:.6f}*on,1.14)"
            x = "iw/2-(iw/zoom/2)"
            y = "ih/2-(ih/zoom/2)"
        elif variant == 1:
            zoom = f"max(1.14-{rate:.6f}*on,1.03)"
            x = "iw/2-(iw/zoom/2)"
            y = "ih/2-(ih/zoom/2)"
        elif variant == 2:
            zoom = "1.09"
            x = f"(iw-iw/zoom)*on/{frames}"
            y = "ih/2-(ih/zoom/2)"
        else:
            zoom = "1.10"
            x = f"(iw-iw/zoom)*(1-on/{frames})"
            y = "ih/2-(ih/zoom/2)"
        filter_text = (
            f"scale={int(self.width * 1.3)}:{int(self.height * 1.3)}:force_original_aspect_ratio=increase,"
            f"crop={int(self.width * 1.3)}:{int(self.height * 1.3)},"
            f"zoompan=z='{zoom}':x='{x}':y='{y}':d=1:s={self.width}x{self.height}:fps={self.fps},"
            "format=yuv420p,setsar=1"
        )
        return filter_text, f"motion-{variant}"

    def _render_scene(
        self,
        scene: Scene,
        duration: float,
        background: Path,
        out: Path,
        motion: str,
    ) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        frames = max(1, round(duration * self.fps))
        command = [
            ffmpeg_bin(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(self.fps),
            "-t",
            f"{duration:.3f}",
            "-i",
            str(background),
            "-vf",
            motion,
            "-frames:v",
            str(frames),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            self.preset,
            "-crf",
            str(self.crf),
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(self.fps),
            "-movflags",
            "+faststart",
            str(out),
        ]
        result = run(command, cwd=self.config.root, timeout=300)
        if result.returncode != 0 or not out.exists():
            raise RenderError(f"ffmpeg не собрал сцену {scene.id}: {result.stderr[-1200:]}")

    @staticmethod
    def _escape_filter_path(path: Path) -> str:
        value = path.as_posix().replace(":", r"\:")
        return f"'{value}'"

    def _ensure_scenes(
        self,
        script: Script,
        timeline: Timeline,
        visuals: dict[str, Any],
        style: str,
        pace: str,
        workspace: Path,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        visual_by_id = {str(item.get("scene_id")): item for item in visuals.get("scenes", [])}
        timing_by_id = {item.scene_id: item for item in timeline.scenes}
        script_sha = hashlib.sha256(script.all_text.encode("utf-8")).hexdigest()
        manifest_path = self._scene_manifest_path(workspace, script.lang)
        existing: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = {}
        valid = (
            existing.get("script_sha") == script_sha
            and existing.get("width") == self.width
            and existing.get("height") == self.height
            and existing.get("fps") == self.fps
            and existing.get("style") == style
            and existing.get("pace") == pace
        )
        cached_by_id = {str(item.get("scene_id")): item for item in existing.get("scenes", [])} if valid else {}
        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        for index, scene in enumerate(script.scenes):
            visual = visual_by_id.get(scene.id, {})
            background = Path(str(visual.get("background", "")))
            if not background.exists():
                raise RenderError(f"Нет фона для сцены {scene.id}: {background}")
            timing = timing_by_id.get(scene.id)
            if not timing:
                raise RenderError(f"Нет тайминга для сцены {scene.id}")
            background_sha = hashlib.sha256(background.read_bytes()).hexdigest()
            out = workspace / "render" / script.lang / "scenes" / f"{scene.id}.mp4"
            cached = cached_by_id.get(scene.id)
            use_cached = bool(
                cached
                and out.exists()
                and cached.get("background_sha") == background_sha
                and abs(float(cached.get("duration", 0)) - timing.scene_duration) < 0.03
            )
            if not use_cached:
                motion, label = self._motion(scene, timing.scene_duration, index, style, pace)
                self._render_scene(scene, timing.scene_duration, background, out, motion)
                self.logger.info("Сцена %s: %.2f с, %s", scene.id, timing.scene_duration, label)
            records.append(
                {
                    "scene_id": scene.id,
                    "duration": round(timing.scene_duration, 3),
                    "video": out.as_posix(),
                    "background_sha": background_sha,
                    "cached": use_cached,
                }
            )
        atomic_write_json(
            manifest_path,
            {
                "script_sha": script_sha,
                "lang": script.lang,
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "style": style,
                "pace": pace,
                "generated_at": now_iso(),
                "scenes": records,
            },
        )
        return records, warnings

    # -------------------------------------------------------------------- assemble
    def assemble(
        self,
        script: Script,
        timeline: Timeline,
        visuals: dict[str, Any],
        narration: Path,
        music: Path | None,
        subtitles: Path,
        style: str,
        pace: str,
        workspace: Path,
        output: Path,
    ) -> RenderResult:
        if not narration.exists():
            raise RenderError(f"Нет дорожки озвучки: {narration}")
        if not subtitles.exists():
            raise RenderError(f"Нет субтитров: {subtitles}")
        scenes, warnings = self._ensure_scenes(script, timeline, visuals, style, pace, workspace)

        filter_parts: list[str] = []
        for index, record in enumerate(scenes):
            filter_parts.append(f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS[v{index}]")
        current = "v0"
        for index in range(1, len(scenes)):
            offset = timeline.scenes[index].video_start
            label = f"x{index}"
            transition = "fade" if index % 3 else "smoothleft"
            filter_parts.append(
                f"[{current}][v{index}]xfade=transition={transition}:duration={self.transition:.3f}:offset={offset:.3f}[{label}]"
            )
            current = label
        chain_duration = sum(float(item["duration"]) for item in scenes) - self.transition * max(0, len(scenes) - 1)
        hold = max(0.0, timeline.total_video - chain_duration)
        if hold > 0.02:
            filter_parts.append(
                f"[{current}]tpad=stop_mode=clone:stop_duration={hold:.3f}:stop=-1[held]"
            )
            current = "held"
        ass_path = subtitles
        try:
            relative_ass = ass_path.relative_to(workspace)
            ass_value = relative_ass.as_posix()
        except ValueError:
            # абсолютный путь: экранируем ':' и спецсимволы фильтра
            ass_value = self._escape_filter_path(str(ass_path))
        fonts_value = self._escape_filter_path(self.fonts_dir)
        filter_parts.append(
            f"[{current}]ass={ass_value}:fontsdir={fonts_value},format=yuv420p[vout]"
        )

        audio_input_index = len(scenes)
        music_index = audio_input_index + 1
        filter_parts.append(
            f"[{audio_input_index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            "loudnorm=I=-14:TP=-1.5:LRA=11,apad[narr]"
        )
        if music:
            filter_parts.append(
                f"[{music_index}:a]atrim=0:{timeline.total_video:.3f},volume=0.16,"
                "aformat=sample_fmts=fltp:channel_layouts=stereo[music]"
            )
            filter_parts.append("[narr]asplit=2[n1][n2]")
            filter_parts.append(
                "[music][n2]sidechaincompress=threshold=0.035:ratio=7:attack=25:release=420[duck]"
            )
            filter_parts.append(
                "[n1][duck]amix=inputs=2:duration=first:dropout_transition=2,"
                "alimiter=limit=0.95[aout]"
            )
        else:
            filter_parts.append("[narr]alimiter=limit=0.95[aout]")

        filter_script = workspace / "render" / script.lang / "final_filter.txt"
        filter_script.parent.mkdir(parents=True, exist_ok=True)
        filter_script.write_text(";\n".join(filter_parts), encoding="utf-8")

        command = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y"]
        for record in scenes:
            command += ["-i", str(record["video"])]
        command += ["-i", str(narration)]
        if music:
            command += ["-stream_loop", "-1", "-i", str(music)]
        command += [
            "-filter_complex_script",
            str(filter_script),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-t",
            f"{timeline.total_video:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            self.preset,
            "-crf",
            str(self.crf),
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(self.fps),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(output),
        ]
        output.parent.mkdir(parents=True, exist_ok=True)
        result = run(command, cwd=workspace, timeout=1200)
        if result.returncode != 0 or not output.exists():
            raise RenderError(f"Финальная сборка не удалась: {result.stderr[-2000:]}")
        size = output.stat().st_size
        if size < 100 * 1024:
            raise RenderError(f"Итоговый файл подозрительно мал: {size} байт")
        self.logger.info("Собран %s: %.2f с, %.1f МБ", output.name, timeline.total_video, size / 1024 / 1024)
        return RenderResult(
            video=output.as_posix(),
            lang=script.lang,
            duration=round(timeline.total_video, 3),
            width=self.width,
            height=self.height,
            fps=self.fps,
            size_bytes=size,
            scenes=scenes,
            warnings=warnings,
            command=" ".join(str(item) for item in command),
            generated_at=now_iso(),
        )
