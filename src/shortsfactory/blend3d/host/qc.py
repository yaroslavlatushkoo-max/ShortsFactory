# blend3d/host/qc.py — technical QC for rendered shorts.
# Pure python (PIL + wave + numpy): no subprocess calls, no Blender required.
# Checks: frame sequence integrity, black/frozen frames, WAV audio levels.
# The muxed-MP4 checks (codec/fps/duration) are performed by the operator or a
# higher-level pipeline stage and passed in via `mp4_facts`.
import wave
from pathlib import Path
import numpy as np
from PIL import Image

def _frames(frames_dir):
    d = Path(frames_dir)
    files = sorted(d.glob("*.jpg")) + sorted(d.glob("*.png"))
    if not files:
        raise FileNotFoundError(f"no frames in {d}")
    return files

def qc_frames(frames_dir, fps=30, expect_size=(1080, 1920), sample_step=5):
    """Scan the rendered frame sequence: count, size, black and frozen frames.
    sample_step=5 inspects every 5th frame (fast); set 1 for exhaustive."""
    files = _frames(frames_dir)
    issues = []
    first = Image.open(files[0])
    if first.size != tuple(expect_size):
        issues.append(f"CRITICAL: frame size {first.size} != {tuple(expect_size)}")
    n = len(files)
    if n < 10:
        issues.append(f"CRITICAL: only {n} frames")
    lumas = []
    prev_full = None
    frozen = 0
    dark = []
    for i in range(0, n, sample_step):
        im = Image.open(files[i]).convert("L")
        arr = np.asarray(im, dtype=np.float32)
        luma = float(arr.mean())
        small = np.asarray(im.resize((24, 42)), dtype=np.float32)
        lumas.append((files[i].name, luma))
        if luma < 14 and i < n - int(fps * 3):
            dark.append((files[i].name, round(luma, 1)))
        if prev_full is not None:
            if prev_full.shape == arr.shape and float(np.abs(arr - prev_full).mean()) < 0.02:
                frozen += 1
        prev_full = arr
    if dark:
        issues.append(f"CRITICAL: black frames at {dark[:4]}")
    if frozen > max(2, n // (sample_step * 30)):
        issues.append(f"WARN: {frozen} frozen samples")
    return {"frames": n, "issues": issues, "size": first.size, "lumas": lumas[:6],
            "dark": dark[:6], "frozen_samples": frozen}

def qc_wav(wav_path, expect_duration=None, expect_sr=48000):
    """Validate the audio WAV: format, duration, peak/mean levels (dBFS)."""
    w = wave.open(str(wav_path), "rb")
    sr, ch, sw, nfr = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    w.close()
    issues = []
    if sr != expect_sr: issues.append(f"WARN: sample rate {sr} != {expect_sr}")
    if ch != 2: issues.append(f"WARN: channels {ch} != 2")
    raw = wave.open(str(wav_path), "rb").readframes(nfr)
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0
    if ch == 2: x = x.reshape(-1, 2).mean(axis=1)
    dur = nfr / sr
    if expect_duration and abs(dur - expect_duration) > 0.2:
        issues.append(f"WARN: wav duration {dur:.2f}s != expected {expect_duration}s")
    peak = float(np.abs(x).max()) if len(x) else 0.0
    rms = float(np.sqrt((x ** 2).mean())) if len(x) else 0.0
    db = lambda v: round(20 * np.log10(max(v, 1e-9)), 1)
    if peak > 0.999: issues.append("WARN: clipping (peak >= 0 dBFS)")
    if peak < 0.01: issues.append("CRITICAL: audio silent")
    return {"duration": round(dur, 2), "sr": sr, "channels": ch,
            "peak_dbfs": db(peak), "rms_dbfs": db(rms), "issues": issues}

def verdict(frames_dir, wav_path=None, fps=30, expect_size=(1080, 1920),
            expect_duration=None, mp4_facts=None):
    """Compose READY/PARTIAL/FAILED like ShortsFactory QC gates.
    mp4_facts: dict from an external ffprobe step (operator or pipeline)."""
    issues = []
    fr = qc_frames(frames_dir, fps=fps, expect_size=expect_size)
    issues += fr["issues"]
    audio = None
    if wav_path:
        audio = qc_wav(wav_path, expect_duration=expect_duration)
        issues += audio["issues"]
    if mp4_facts:
        fps_got = mp4_facts.get("fps")
        if fps_got is not None:
            try:
                num, _, den = str(fps_got).partition("/")
                fps_val = float(num) / (float(den) if den else 1.0)
                if abs(fps_val - float(fps)) > 0.01:
                    issues.append(f"CRITICAL: mp4 fps={fps_got} != {fps}")
            except ValueError:
                issues.append(f"CRITICAL: mp4 fps unparsable: {fps_got}")
        for k, want, got in (("width", expect_size[0], mp4_facts.get("width")),
                             ("height", expect_size[1], mp4_facts.get("height"))):
            if got is not None and str(got) != str(want):
                issues.append(f"CRITICAL: mp4 {k}={got} != {want}")
        if mp4_facts.get("audio_ok") is False:
            issues.append("CRITICAL: mp4 has no AAC audio")
    status = "FAILED" if any(i.startswith("CRITICAL") for i in issues) else ("PARTIAL" if issues else "READY")
    return {"status": status, "issues": issues, "frames": fr,
            "audio": audio, "mp4_facts": mp4_facts}
