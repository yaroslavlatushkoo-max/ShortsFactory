"""Quality checks for generative video (Mission 3 §7, §12).

These are lightweight, honest checks: decode, stream sanity, flicker (mean
normalized luminance change between sampled frames) and cross-clip continuity.
They do NOT claim to prove subjective visual quality — a contact sheet and the
videos themselves are produced for human review.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from ..util import CommandError, ffmpeg_bin, ffprobe_bin, run
from .models import QCMetrics

_NULL = "NUL" if __import__("os").name == "nt" else "/dev/null"


def probe_media(path: Path) -> dict:
    proc = run([ffprobe_bin(), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    return json.loads(proc.stdout or "{}")


def _video_stream(info: dict) -> dict | None:
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return None


def _decode_ok(path: Path) -> bool:
    try:
        run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "null", _NULL], timeout=600)
        return True
    except CommandError:
        return False


def extract_luminance_frames(path: Path, out_dir: Path, fps: float = 2.0) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    probe_path = out_dir / "f_%04d.png"
    run(
        [ffmpeg_bin(), "-v", "error", "-i", str(path), "-vf", f"fps={fps},scale=160:-2,format=gray",
         "-frames:v", "60", str(probe_path)],
        timeout=300,
    )
    return sorted(out_dir.glob("f_*.png"))


def flicker_metric(frames: list[Path]) -> float:
    if len(frames) < 2:
        return 0.0
    prev = None
    total = 0.0
    count = 0
    for frame in frames:
        with Image.open(frame) as img:
            cur = list(img.getdata())
        if prev is not None:
            total += sum(abs(a - b) for a, b in zip(prev, cur)) / max(len(prev), 1)
            count += 1
        prev = cur
    if count == 0:
        return 0.0
    return total / count / 255.0  # normalized 0..1


def contact_sheet(clips: list[str], out_dir: Path) -> Path:
    if not clips:
        return Path("")
    out_dir.mkdir(parents=True, exist_ok=True)
    sheet = out_dir / "contact_sheet.jpg"
    # one representative frame per clip
    inputs: list[str] = []
    for clip in clips:
        inputs += ["-ss", "0.5", "-i", clip]
    vf = "select=eq(n\\,0),scale=180:-2,tile=4x4"
    cmd = [ffmpeg_bin(), "-y", *inputs, "-frames:v", "1", "-vf", vf, str(sheet)]
    try:
        run(cmd, timeout=300)
    except CommandError:
        return Path("")
    return sheet


def run_qc(clips: list[str], video: Path, out_dir: Path) -> QCMetrics:
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = QCMetrics()
    frame_dir = out_dir / "qc_frames"

    if video.exists():
        info = probe_media(video)
        metrics.decode_ok = _decode_ok(video)
        v = _video_stream(info)
        if v:
            metrics.width = int(v.get("width", 0))
            metrics.height = int(v.get("height", 0))
            metrics.fps = float(v.get("avg_frame_rate", "0").split("/")[0]) if "/" in str(v.get("avg_frame_rate")) else float(v.get("avg_frame_rate", 0) or 0)
            metrics.height_ok = True
        metrics.duration_s = float(info.get("format", {}).get("duration", 0) or 0)
        metrics.file_bytes = video.stat().st_size
        try:
            frames = extract_luminance_frames(video, frame_dir)
            metrics.flicker = round(flicker_metric(frames), 4)
        except CommandError:
            metrics.notes.append("не удалось извлечь кадры для flicker-метрики")

    metrics.scenes_count = len(clips)
    metrics.clips_ok = 0
    dims = set()
    for clip in clips:
        if not Path(clip).exists():
            metrics.notes.append(f"клип отсутствует: {clip}")
            continue
        if not _decode_ok(Path(clip)):
            metrics.notes.append(f"клип не декодируется: {clip}")
            continue
        metrics.clips_ok += 1
        v = _video_stream(probe_media(Path(clip)))
        if v:
            dims.add((v.get("width"), v.get("height")))
    if len(dims) > 1:
        metrics.resolution_consistent = False
        metrics.notes.append("разрешения клипов различаются")
    contact_sheet(clips, out_dir)
    metrics.manual_review = True
    return metrics
