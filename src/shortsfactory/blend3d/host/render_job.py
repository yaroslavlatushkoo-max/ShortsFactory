# blend3d/host/render_job.py — build and validate commands for the Blender-side
# driver. This module constructs argv lists; execution belongs to the pipeline
# (jobs.py / CLI / operator), which keeps security scanning and retries layered.
from pathlib import Path

MODES = ("build", "preview", "render")

def validate_script(script_path) -> Path:
    p = Path(script_path)
    if not p.is_absolute() or p.suffix != ".py":
        raise ValueError(f"expected an absolute .py path, got {p}")
    if not p.is_file():
        raise FileNotFoundError(f"driver script not found: {p}")
    return p

def build_blender_argv(blender_exe, script_path, mode="build") -> list:
    """argv to build the scene / render previews / render the full animation.
    The driver script reads mode from sys.argv after '--'."""
    exe = Path(blender_exe)
    if not exe.is_file():
        raise FileNotFoundError(f"blender exe not found: {exe}")
    sp = validate_script(script_path)
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    return [str(exe), "-b", "-P", str(sp), "--", mode]

def build_preview_argv(blender_exe, script_path) -> list:
    return build_blender_argv(blender_exe, script_path, "preview")

def build_render_argv(blender_exe, script_path) -> list:
    return build_blender_argv(blender_exe, script_path, "render")

def frames_done(frames_dir, prefix="f") -> int:
    """Count rendered frames (f0001.jpg ...)."""
    d = Path(frames_dir)
    if not d.is_dir(): return 0
    return len(list(d.glob(prefix + "*.jpg")))

def assemble_argv(frames_dir, wav_path, out_mp4, fps=30, crf=17, preset="medium") -> list:
    """argv to mux the JPEG sequence + WAV into the final H.264/AAC MP4."""
    fd, wp = Path(frames_dir), Path(wav_path)
    out = Path(out_mp4)
    if not fd.is_dir(): raise FileNotFoundError(f"frames dir missing: {fd}")
    if not wp.is_file(): raise FileNotFoundError(f"wav missing: {wp}")
    if out.suffix.lower() != ".mp4": raise ValueError("out must be .mp4")
    if not (fd / (f"f0001.jpg")).exists() and not (fd / "f0001.png").exists():
        raise FileNotFoundError("no f0001 frame found")
    return ["ffmpeg", "-y", "-framerate", str(int(fps)),
            "-i", str(fd / "f%04d.jpg"),
            "-i", str(wp),
            "-filter_complex", "[0:v]vignette=angle=PI/4.8,format=yuv420p[v]",
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-crf", str(int(crf)), "-preset", preset,
            "-r", str(int(fps)), "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", str(out)]
