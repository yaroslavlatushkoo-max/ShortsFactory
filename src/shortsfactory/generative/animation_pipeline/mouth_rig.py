"""Mouth Rig — reusable cartoon mouth animation controller for any character.

The rig is deliberately decoupled from the mesh builders: it only decides
*what shape* the mouth should have at every frame of a dialogue line and
returns a track of ``(frame_index, shape_name)`` pairs.  The pipeline then
maps the shape name to the ``(jaw_open, mouth_wide, mouth_round)`` channels
that the skinned/blocky solvers already understand.

Shapes
------
- closed        — thin dark line (default, silence)
- slightly_open — small dark opening
- open          — regular speech opening
- wide_open     — large opening (excited / loud)
- round         — rounded opening (``O`` / ``U`` vowels)

Synchronisation
---------------
* If the line text is available, grapheme->viseme mapping (the same table the
  lip-sync engine uses) picks a shape per letter.
* If ``segments`` (word/phone timestamps) are given, speech is confined to
  those spans; elsewhere the mouth stays closed.
* If only a WAV is available (fallback), an RMS envelope is analysed and the
  mouth opens inside detected speech windows — never randomly.
"""

from __future__ import annotations

import math
import wave
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import numpy as np

#: shape name -> (jaw_open, mouth_wide, mouth_round)
MOUTH_SHAPES: dict[str, tuple[float, float, float]] = {
    "closed": (0.0, 0.0, 0.0),
    "slightly_open": (0.04, 0.22, 0.06),
    "open": (0.10, 0.34, 0.03),
    "wide_open": (0.17, 0.52, 0.00),
    "round": (0.08, -0.12, 0.55),
}

#: viseme letter -> mouth shape (approximate but deterministic).
VISEME_TO_SHAPE: dict[str, str] = {
    "rest": "closed",
    "M": "slightly_open",
    "F": "slightly_open",
    "A": "open",
    "E": "wide_open",
    "I": "open",
    "O": "round",
    "U": "round",
}


#: High-level expression presets: (brow_lift, brow_tilt, mouth_round,
#: jaw_open) offsets layered on top of lip-sync while speaking.
EXPRESSIONS: dict[str, tuple[float, float, float, float]] = {
    "neutral": (0.000, 0.00, 0.00, 0.00),
    "happy": (0.012, -0.12, 0.10, 0.06),
    "sad": (-0.004, 0.18, 0.00, 0.00),
    "angry": (-0.006, -0.45, 0.00, -0.04),
    "surprised": (0.014, 0.00, 0.45, 0.20),
    "confused": (-0.002, 0.30, 0.05, 0.02),
    "scared": (0.010, 0.25, 0.15, 0.10),
    "excited": (0.012, -0.18, 0.10, 0.10),
}


def emotion_overlay(poses, emotion: str, *, start: int = 0, duration: int | None = None,
                    fps: int = 30, strength: float = 1.0):
    """Blend a high-level expression into the pose channel list.

    Only the brow and viseme channels are touched (PoseParams brow_lift /
    brow_tilt / jaw_open / mouth_round), so lip-sync and body motion remain
    intact; the expression rides on top of them.
    """
    base = EXPRESSIONS.get(emotion, EXPRESSIONS["neutral"])
    if duration is None:
        duration = len(poses) - start
    end = min(len(poses), start + max(0, duration))
    out = list(poses)
    for i in range(max(0, start), end):
        p = out[i]
        u = 0.5 - 0.5 * __import__("math").cos(min(1.0, (i - start) / max(1, end - start - 1)) * 3.141592653589793)
        bl = p.brow_lift + base[0] * u * strength
        bt = p.brow_tilt + base[1] * u * strength
        mr = max(0.0, min(1.0, p.mouth_round + base[2] * u * strength))
        jo = max(0.0, min(0.6, p.jaw_open + base[3] * u * strength))
        out[i] = replace(p, brow_lift=bl, brow_tilt=bt, mouth_round=mr, jaw_open=jo)
    return out


def idle_gaze(poses, *, fps: int = 30):
    """Add a subtle, deterministic head sway (micro-motion while idle)."""
    import math
    out = []
    for i, p in enumerate(poses):
        t = i / max(1, fps)
        out.append(replace(
            p,
            head_pitch=p.head_pitch + 0.012 * math.sin(2 * math.pi * t / 2.3),
            head_yaw=p.head_yaw + 0.02 * math.sin(2 * math.pi * t / 3.7),
        ))
    return out


def smooth_mouth_channels(poses, *, kernel: int = 3):
    """Ease the jaw/wide/round channels across frames for organic lip motion.

    The animation pipeline may switch between MOUTH_SHAPES on frame
    boundaries; smoothing blends the three viseme channels so the mouth does
    not snap.  ``poses`` is a sequence of PoseParams; the number of frames
    is unchanged.
    """
    if kernel < 2 or len(poses) < 3:
        return list(poses)
    fields = ("jaw_open", "mouth_wide", "mouth_round")
    half = kernel // 2
    n = len(poses)
    out: list = []
    for i, p in enumerate(poses):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        blended = {
            f: sum(getattr(poses[j], f) for j in range(lo, hi)) / (hi - lo)
            for f in fields
        }
        out.append(replace(p, **blended))
    return out


def _frame_count(duration: float, fps: int) -> int:
    return max(1, int(round(duration * fps)))


def _speech_segments_from_wav(
    path: str | Path,
    *,
    win_ms: float = 10.0,
    threshold: float = 0.12,
    min_len: float = 0.08,
    merge_gap: float = 0.12,
) -> list[tuple[float, float]]:
    """Locate speech windows from a mono/stereo 16-bit WAV via RMS envelope."""
    path = Path(path)
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        frames = wav.getnframes()
        raw = wav.readframes(frames)
    if width != 2:
        raise ValueError("MouthRig: support only 16-bit WAV")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if channels > 1:
        data = data[::channels]
    win = max(1, int(round(rate * win_ms / 1000.0)))
    n_win = max(1, len(data) // win)
    rms = np.array(
        [np.sqrt(np.mean(data[i * win:(i + 1) * win] ** 2)) for i in range(n_win)],
        dtype=np.float64,
    )
    peak = float(rms.max())
    if peak <= 1e-6:
        return []
    rms /= peak
    active = rms > threshold
    # build spans from consecutive active windows
    spans: list[list[float]] = []
    start = None
    for i, on in enumerate(active):
        t0 = i * win / rate
        if on and start is None:
            start = t0
        elif not on and start is not None:
            spans.append([start, t0])
            start = None
    if start is not None:
        spans.append([start, n_win * win / rate])
    # drop hair-line segments and merge close neighbours
    merged: list[tuple[float, float]] = []
    for s, e in spans:
        if e - s < min_len:
            continue
        if merged and s - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _shape_for_viseme(v: str) -> str:
    return VISEME_TO_SHAPE.get(v, "open")


def _smooth_track(shapes: list[str]) -> list[str]:
    """Remove single-frame flicker: a lone frame equal to its right neighbour
    is snapped to the previous state."""
    out = list(shapes)
    for i in range(1, len(out) - 1):
        if out[i] != out[i - 1] and out[i] == out[i + 1]:
            out[i] = out[i - 1]
    return out


def plan_mouth_track(
    text: str | None = None,
    *,
    wav_path: str | Path | None = None,
    duration: float,
    fps: int,
    segments: Sequence[Sequence[float]] | None = None,
) -> list[tuple[int, str]]:
    """Return ``[(frame_index, shape), ...]`` covering every frame.

    Deterministic: closed while silent, shaped by visemes while speaking, and
    only ever opens inside real speech windows (text/segments or WAV RMS).
    """
    n = _frame_count(duration, fps)
    shapes = ["closed"] * n

    speech: list[tuple[float, float]] = []
    if segments:
        for seg in segments:
            try:
                speech.append((max(0.0, float(seg[0])), float(seg[1])))
            except (TypeError, ValueError, IndexError):
                continue
    if not speech and wav_path is not None:
        speech = _speech_segments_from_wav(wav_path)
    if not speech and text:
        # no external timings: assume the whole line is spoken (fallback)
        speech = [(0.0, max(0.0, duration))]

    if text:
        from .skinned_hero import viseme_track  # lazy: avoid import cycles
        for s, e in speech:
            if e <= s:
                continue
            keys = viseme_track(text, start=s, duration=e - s, fps=fps, segments=[(s, e)])
            for t, viseme in keys:
                f = int(round(t * fps))
                if 0 <= f < n:
                    shapes[f] = _shape_for_viseme(viseme)
    else:
        # fallback: WAV loudness only — open inside speech windows
        for s, e in speech:
            s_i = max(0, int(round(s * fps)))
            e_i = min(n, int(math.ceil(e * fps)))
            for f in range(s_i, e_i):
                shapes[f] = "open"

    shapes = _smooth_track(shapes)
    shapes[0] = "closed"
    shapes[-1] = "closed"
    return [(i, shapes[i]) for i in range(n)]
