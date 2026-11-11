"""Scene assembly: concat generated clips + mix TTS audio + final MP4 (Mission 3 §3E).

Reuses FFmpeg (via util.run). On the order of scenes and the final master:
each scene clip is normalized to the project canvas, concatenated, then voice
segments are placed at scene offsets and mixed. Lip-sync is not claimed here —
audio is mixed at the timeline level.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..util import CommandError, ffmpeg_bin, ffprobe_bin, run

WIDTH = 1080
HEIGHT = 1920
FPS = 30
CRF = 18
PRESET = "medium"


def probe(path: Path) -> dict[str, Any]:
    proc = run([ffprobe_bin(), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    import json

    return json.loads(proc.stdout or "{}")


def _normalize_clip(src: Path, dest_dir: Path, idx: int) -> Path:
    out = dest_dir / f"norm_{idx:03d}.mp4"
    vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}"
    )
    run(
        [
            ffmpeg_bin(), "-y", "-i", str(src),
            "-vf", vf,
            "-c:v", "libx264", "-crf", str(CRF), "-preset", PRESET,
            "-pix_fmt", "yuv420p", "-an",
            str(out),
        ],
        timeout=600,
    )
    return out


def concat_clips(clips: list[str], dest_dir: Path) -> Path:
    if not clips:
        raise CommandError([], -1, "", "Нет клипов для сборки")
    dest_dir.mkdir(parents=True, exist_ok=True)
    norm: list[Path] = []
    for idx, clip in enumerate(clips):
        norm.append(_normalize_clip(Path(clip), dest_dir, idx))
    list_file = dest_dir / "concat_list.txt"
    list_file.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in norm), encoding="utf-8"
    )
    out = dest_dir / "concat.mp4"
    run(
        [
            ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", str(out),
        ],
        timeout=600,
    )
    return out


def mix_audio(
    video: Path,
    segments: list[tuple[float, str]],  # (start_sec, wav_path)
    dest_dir: Path,
    *,
    sample_rate: int = 48000,
    drop_source_audio: bool = False,
) -> Path:
    """Place TTS segments at offsets and mix them under the video. If segments
    are empty, keep the source audio unless drop_source_audio is set."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "final_audio.mp4"
    if not segments:
        if not drop_source_audio:
            return video
        # strip audio
        run(
            [ffmpeg_bin(), "-y", "-i", str(video), "-an", "-c:v", "copy", str(out)],
            timeout=300,
        )
        return out

    cmd: list[str] = [ffmpeg_bin(), "-y", "-i", str(video)]
    inputs = len(segments)
    for _start, wav in segments:
        cmd += ["-i", wav]
    labels: list[str] = []
    for i in range(inputs):
        ms = int(max(segments[i][0], 0.0) * 1000)
        labels.append(f"[{i + 1}:a]adelay={ms}|{ms}[a{i}]")
    mix_in = "".join(f"[a{i}]" for i in range(inputs))
    labels.append(f"{mix_in}amix=inputs={inputs}:duration=longest:dropout_transition=0[out]")
    cmd += ["-filter_complex", ";".join(labels)]
    cmd += ["-map", "0:v", "-map", "[out]"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(sample_rate)]
    cmd += [str(out)]
    run(cmd, timeout=600)
    return out


def build_final(
    clips: list[str],
    dest_dir: Path,
    segments: list[tuple[float, str]] | None = None,
    *,
    drop_source_audio: bool = False,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    video = concat_clips(clips, dest_dir)
    if segments is None:
        return video
    return mix_audio(video, segments, dest_dir, drop_source_audio=drop_source_audio)
