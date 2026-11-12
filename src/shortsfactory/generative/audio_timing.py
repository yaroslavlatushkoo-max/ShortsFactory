"""Тайминг речи: сжатие длинных внутренних пауз и room tone вместо цифровой тишины."""
from __future__ import annotations

import numpy as np


def compress_silence(
    x: np.ndarray,
    sr: int,
    *,
    max_gap_s: float = 0.40,
    thresh: float = 0.012,
    win_s: float = 0.02,
) -> np.ndarray:
    """Обрезает любые внутренние тишины длиннее max_gap_s.

    Чинит провал 3.75 с внутри реплики сцены 4, не трогая сам TTS.
    """
    if x.size == 0:
        return x
    win = max(1, int(sr * win_s))
    max_run = max(1, int(max_gap_s / win_s))
    keep: list[np.ndarray] = []
    run = 0
    for i in range(0, len(x), win):
        seg = x[i:i + win]
        quiet = float(np.sqrt((seg ** 2).mean())) < thresh
        if quiet:
            run += 1
            if run > max_run:
                continue
        else:
            run = 0
        keep.append(seg)
    return np.concatenate(keep) if keep else x


def room_tone(n_samples: int, sr: int, *, amp: float = 0.03, seed: int = 1234) -> np.ndarray:
    """Тихий сглаженный фон вместо np.zeros — чтобы хвост сцены не был мёртвой тишиной."""
    if n_samples <= 0:
        return np.zeros(0, dtype=np.float32)
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n_samples + 128)
    k = np.hanning(64)
    k /= k.sum()
    smooth = np.convolve(noise, k, mode="same")[:n_samples]
    peak = float(np.abs(smooth).max()) or 1.0
    return (smooth / peak * amp).astype(np.float32)
