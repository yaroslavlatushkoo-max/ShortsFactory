"""QC метрик лип-синка для blocky-фильмов: payload + озвучка.

Модуль маленький и независимый от сборки сцены: он сверяет уже собранный
payload (``mouth_states``) с реальным WAV фильма.  Сегменты речи извлекаются
тем же алгоритмом, что и в ``mouth_rig._speech_segments_from_wav``
(RMS-огибающая, порог 0.12, минимум 0.08 с, склейка через 0.12 с), поэтому
метрики честно показывают, двигается ли рот там, где звучит голос.

Используется как пост-рендер проверка в ``skinned_hero.render_skinned``:
результат пишется в diagnostics JSON под ключом ``"lip_sync"`` и никогда не
ломает сам рендер — любая проблема вырождается в
``{"status": "skipped", "reason": ...}``.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from . import mouth_rig

#: пороги вердикта: доля закрытого рта во время речи и доля кадров,
#: когда рот «хлопает» в тишине (от общего числа кадров сцены)
RATIO_CLOSED_WARN = 0.35
FLAPPING_WARN = 0.15

_PAYLOAD_RE = re.compile(r"PAYLOAD\s*=\s*Path\(\s*(r?['\"].*?['\"])\s*\)")


def _state_of(raw: Any) -> str:
    """Извлечь состояние («closed»/«round»/«open») из имени файла-состояния."""
    if not raw:
        return "closed"
    stem = str(raw).replace("\\", "/").rsplit("/", 1)[-1]
    if stem.lower().endswith(".png"):
        stem = stem[:-4]
    return stem.rsplit("_", 1)[-1].lower()


def verify_lip_sync(payload: dict[str, Any], wav_path: str | Path) -> dict[str, Any]:
    """Посчитать метрики лип-синка по персонажам: payload против WAV.

    Для каждого персонажа из ``payload["mouth_states"]`` возвращаются:
    ``frames_speaking`` (кадры внутри его сегментов речи),
    ``frames_closed_while_speaking`` и ``ratio_closed``,
    ``frames_flapping_while_silent`` и ``ratio_flapping``, а также
    ``verdict`` — «warn», если рот закрыт больше чем в 35% речи или хлопает
    в тишине больше чем в 15% кадров.

    Сегменты речи берутся из ``payload["mouth_segments"][char]`` (измерены по
    WAV самой реплики), а глобальный RMS всего микса — только фолбэк: в миксе
    присутствуют ambience и SFX, из-за которых «речь» по RMS завышается.
    """
    fps = int(payload.get("fps") or 30)
    total_frames = len(payload.get("frames") or [])
    segments = mouth_rig._speech_segments_from_wav(wav_path)
    measured = payload.get("mouth_segments") or {}
    states = payload.get("mouth_states") or {}
    characters: dict[str, dict[str, Any]] = {}
    for char, table in states.items():
        table = table or {}
        own = (measured.get(char) or {}).get("segments") or []
        if own:
            char_segments = [(float(a), float(b)) for a, b in own if float(b) > float(a)]
            speech_source = "mouth_segments"
        else:
            char_segments = segments
            speech_source = "wav_rms"
        frames_speaking = 0
        closed_while_speaking = 0
        flapping = 0
        for fi in range(total_frames):
            t = fi / max(1, fps)
            speaking = any(a <= t < b for a, b in char_segments)
            state = _state_of(table.get(fi, table.get(str(fi))))
            open_mouth = state != "closed"
            if speaking:
                frames_speaking += 1
                if not open_mouth:
                    closed_while_speaking += 1
            elif open_mouth:
                flapping += 1
        ratio_closed = (
            closed_while_speaking / frames_speaking if frames_speaking else 0.0
        )
        ratio_flapping = flapping / total_frames if total_frames else 0.0
        characters[char] = {
            "frames_speaking": frames_speaking,
            "frames_closed_while_speaking": closed_while_speaking,
            "ratio_closed": round(ratio_closed, 4),
            "frames_flapping_while_silent": flapping,
            "ratio_flapping": round(ratio_flapping, 4),
            "speech_source": speech_source,
            "verdict": (
                "warn"
                if ratio_closed > RATIO_CLOSED_WARN
                or ratio_flapping > FLAPPING_WARN
                else "pass"
            ),
        }
    return {
        "status": "ok" if characters else "skipped",
        "wav": str(wav_path),
        "fps": fps,
        "speech_segments": [[round(a, 4), round(b, 4)] for a, b in segments],
        "characters": characters,
    }


def find_film_wav(out_dir: str | Path) -> Path | None:
    """Найти WAV фильма рядом с payload: микс озвучки или sfx-дорожка.

    Порядок: ``<render>/sfx.wav`` (его пишет mux_audio рядом с видео),
    ``<film>/audio/mix.wav`` (микс BlockyScene), ``<render>/mix.wav``.
    """
    render_dir = Path(out_dir)
    candidates = (
        render_dir / "sfx.wav",
        render_dir.parent / "audio" / "mix.wav",
        render_dir / "mix.wav",
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _payload_path_from_script(script_path: Path) -> Path | None:
    """Достать путь к payload из сгенерированного Blender-скрипта."""
    try:
        match = _PAYLOAD_RE.search(script_path.read_text(encoding="utf-8"))
        literal = match.group(1).strip() if match else ""
        # в шаблоне путь вставлен как r"..." — разбираем литерал Python'а
        if literal:
            return Path(ast.literal_eval(literal))
    except Exception:
        return None
    return None


def attach_lip_sync_qc(
    out_dir: str | Path,
    script_path: str | Path,
    diag_path: str | Path,
    diag: dict[str, Any],
) -> dict[str, Any]:
    """Добавить метрики лип-синка в diagnostics JSON рендера.

    Ищет payload (по Blender-скрипту) и WAV фильма рядом с рендером.
    Никогда не бросает исключений: если payload или WAV не найдены, в
    diagnostics попадает ``{"status": "skipped", "reason": ...}``.
    """
    result: dict[str, Any]
    try:
        payload_path = _payload_path_from_script(Path(script_path))
        if payload_path is None or not payload_path.is_file():
            result = {"status": "skipped", "reason": "payload JSON не найден рядом с рендером"}
        else:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            wav = find_film_wav(out_dir)
            if wav is None:
                result = {
                    "status": "skipped",
                    "reason": "WAV озвучки не найден (ни sfx.wav, ни audio/mix.wav)",
                    "payload": payload_path.name,
                }
            else:
                result = verify_lip_sync(payload, wav)
                result["payload"] = payload_path.name
    except Exception as exc:  # QC обязана не ломать рендер
        result = {"status": "skipped", "reason": str(exc)[-300:]}
    diag["lip_sync"] = result
    try:
        Path(diag_path).write_text(
            json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    return result
