"""Automatic mouth-texture switching for blocky skins.

Each character ships three 64x64 skins that are identical except for the
mouth area: ``mouth_closed.png``, ``mouth_round.png`` and ``mouth_open.png``.
During speech the engine composites the mouth region of the matching variant
onto the base skin and drives the material image per audio frame, so the
speaker's mouth visibly changes with the phonemes without touching geometry,
UVs or the rest of the skin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# phoneme classification (grapheme approximation, no phoneme model required)
# ---------------------------------------------------------------------------

#: letter-sets per language.  Order of checking: closed first, then round,
#: then open (open is the loudest so it wins ties via explicit checks below).
RU_CLOSED = set("\u043c\u0431\u043f")          # м б п
RU_ROUND = set("\u043e\u0451\u0443\u044e")      # о ё у ю
RU_OPEN = set("\u0430\u044d\u0435\u0438\u044b\u044f")  # а э е и ы я

EN_CLOSED = set("mbp")
EN_ROUND = set("ouw")
EN_OPEN = set("aeiy")

_PAUSE = set(" \t\n.,!?…:;—-\"'()")


def classify_mouth(lang: str, char: str, prev: str = "", next_: str = "") -> str:
    """Pick one of closed/round/open for a grapheme.

    Consonants borrow the neighbouring vowel's shape; with no vowel nearby
    the mouth stays closed (bilabial consonants M/B/P are always closed).
    """
    ch = char.lower()
    if ch in _PAUSE:
        return "closed"
    closed = RU_CLOSED if lang == "ru" else EN_CLOSED
    round_ = RU_ROUND if lang == "ru" else EN_ROUND
    open_ = RU_OPEN if lang == "ru" else EN_OPEN
    if ch in closed:
        return "closed"
    if ch in round_:
        return "round"
    if ch in open_:
        return "open"
    # consonant: follow the nearest vowel in the window
    window = (prev.lower() + next_.lower()).strip()
    for probe in window:
        if probe in round_:
            return "round"
        if probe in open_:
            return "open"
    return "closed"


# ---------------------------------------------------------------------------
# skin bank: loads/caches the three variants once per character
# ---------------------------------------------------------------------------

#: default directory of the 64x64 skins (matches presets/textures/skins).
DEFAULT_SKINS_DIR = (
    Path(__file__).resolve().parents[2]
    / "blend3d" / "presets" / "textures" / "skins"
)


@dataclass
class MouthSkinBank:
    character: str
    skins_dir: Path = DEFAULT_SKINS_DIR
    closed_name: str = ""
    round_name: str = ""
    open_name: str = ""
    base_name: str = ""

    _base: np.ndarray = field(default=None, repr=False)
    _closed: np.ndarray = field(default=None, repr=False)
    _round: np.ndarray = field(default=None, repr=False)
    _open: np.ndarray = field(default=None, repr=False)
    _mask: np.ndarray = field(default=None, repr=False)

    def __post_init__(self) -> None:
        prefix = f"{self.character}_"
        self.closed_name = self.closed_name or f"{prefix}mouth_closed.png"
        self.round_name = self.round_name or f"{prefix}mouth_round.png"
        self.open_name = self.open_name or f"{prefix}mouth_open.png"
        if not self.base_name:
            self.base_name = f"{prefix}mouth_closed.png"

    def _load(self, name: str) -> np.ndarray:
        path = Path(self.skins_dir) / name
        if not path.exists():
            raise FileNotFoundError(f"Skin variant not found: {path}")
        with Image.open(path) as im:
            arr = np.asarray(im.convert("RGBA"), dtype=np.uint8)
        if arr.shape[0] != 64 or arr.shape[1] != 64:
            raise ValueError(f"Skin must be 64x64: {path} ({arr.shape})")
        return arr

    def load_all(self) -> "MouthSkinBank":
        """Load base + variants once, cache the mouth-diff mask."""
        self._base = self._load(self.base_name)
        self._closed = self._load(self.closed_name)
        self._round = self._load(self.round_name)
        self._open = self._load(self.open_name)
        # mouth area = pixels where the round variant differs from closed,
        # restricted to the head/face half of the sheet (rows 0..31)
        diff = np.any(self._round != self._closed, axis=-1)
        mask = np.zeros_like(diff)
        mask[0:32, :] = diff[0:32, :]
        self._mask = mask
        return self

    @property
    def ready(self) -> bool:
        return self._mask is not None

    def variant(self, name: str) -> np.ndarray:
        if not self.ready:
            self.load_all()
        if name == "closed":
            return self._closed
        if name == "round":
            return self._round
        if name == "open":
            return self._open
        raise KeyError(name)

    def composite(self, name: str) -> np.ndarray:
        """Base skin with the mouth region taken from the named variant."""
        if not self.ready:
            self.load_all()
        img = self._base.copy()
        img[self._mask] = self.variant(name)[self._mask]
        return img

    def composite_png(self, name: str) -> bytes:
        arr = self.composite(name)
        import io

        buf = io.BytesIO()
        Image.fromarray(arr, "RGBA").save(buf, format="PNG")
        return buf.getvalue()


# ---------------------------------------------------------------------------
# planner: text -> per-frame mouth variant (shared with existing visemes)
# ---------------------------------------------------------------------------

def _letter_units(text: str) -> list[tuple[str, float]]:
    units: list[tuple[str, float]] = []
    for ch in text.lower():
        if ch in _PAUSE:
            units.append((" ", 0.4))
        elif ch.isalpha():
            units.append((ch, 1.0 if ch in "aeiouyаеёиоуыэюя" else 0.7))
    return units


def plan_mouth_texture(
    text: str,
    lang: str = "ru",
    *,
    duration: float,
    fps: int = 30,
    segments: Sequence[Sequence[float]] | None = None,
    min_hold_frames: int = 6,
) -> list[str]:
    """Return one mouth variant label per frame of ``duration`` seconds.

    ``min_hold_frames`` prolongs short runs of an open/round mouth so
    articulation does not flicker too quickly; ``closed`` (silence) runs
    are never extended.
    """
    total_frames = max(1, int(round(duration * fps)))
    if not text.strip():
        return ["closed"] * total_frames
    units = _letter_units(text)
    if not units:
        return ["closed"] * total_frames
    if segments is None:
        spans: list[tuple[float, float]] = [(0.0, duration)]
    else:
        spans = [
            (max(0.0, float(s[0])), min(duration, float(s[1])))
            for s in segments
            if len(s) >= 2 and float(s[1]) > float(s[0])
        ]
        if not spans:
            return ["closed"] * total_frames
    span_total = sum(b - a for a, b in spans)
    total_weight = sum(w for _, w in units)

    # spread unit weights across speech spans on the audio timeline
    events: list[tuple[float, str]] = []
    cursor = spans[0][0]
    span_i = 0
    take = 0
    while take < len(units) and span_i < len(spans):
        a, b = spans[span_i]
        share = (b - a) / span_total
        count = max(1, int(round(len(units) * share)))
        count = min(count, len(units) - take)
        chunk = units[take:take + count]
        take += count
        local_weight = sum(w for _, w in chunk) or total_weight
        for idx, (ch, w) in enumerate(chunk):
            prev = chunk[idx - 1][0] if idx > 0 else ""
            nxt = chunk[idx + 1][0] if idx + 1 < len(chunk) else ""
            events.append((cursor, classify_mouth(lang, ch, prev, nxt)))
            cursor += (b - a) * w / local_weight
        span_i += 1
        if span_i < len(spans):
            cursor = spans[span_i][0]

    frames = ["closed"] * total_frames
    for t, shape in events:
        f = int(round(t * fps))
        if 0 <= f < total_frames:
            frames[f] = shape

    return _apply_min_hold(frames, min_hold_frames)


def _apply_min_hold(frames: Sequence[str], hold: int) -> list[str]:
    """Prolong short non-closed runs to at least ``hold`` frames."""
    hold = max(1, int(hold))
    if hold <= 1 or len(frames) < 2:
        return list(frames)
    n = len(frames)
    out: list[str] = []
    i = 0
    while i < n:
        j = i
        while j < n and frames[j] == frames[i]:
            j += 1
        run_len = j - i
        if frames[i] != "closed" and run_len < hold:
            ext = min(hold - run_len, n - j)
            out.extend([frames[i]] * (run_len + ext))
            i = j + ext
        else:
            out.extend(frames[i:j])
            i = j
    return out[:n]
