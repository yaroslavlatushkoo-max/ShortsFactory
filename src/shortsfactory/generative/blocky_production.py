"""Сториборд → несколько blocky-сцен → один смонтированный фильм.

Миссия D12: связать генератор истории (``blocky_story``) с рендером сцен
(``BlockyScene``) в повторяемый конвейер.  Каждая сцена рендерится в свою
папку, результат переиспользуется при повторном запуске (checkpoint), а в
конце сцены склеиваются ffmpeg в один MP4 с общими субтитрами.

Конвейер нарочно не прячет ошибки: сцена, которая не отрендерилась, остаётся в
отчёте со своим статусом, а фильм получает ``PARTIAL`` вместо ``OK``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Sequence

from ..subtitles import Cue, write_srt
from .blocky_scene import CHARACTERS, BlockyScene
from .blocky_story import scene_files, write_storyboard

__all__ = [
    "concat_videos",
    "film_contact_sheet",
    "probe_duration",
    "produce_film",
    "split_storyboard_episodes",
]

#: 4x3 плитки — тот же формат, что у сцены, чтобы листы сравнивались глазами.
TILE_COLS = 4
TILE_ROWS = 3


def probe_duration(path: str | Path) -> float:
    """Real duration of a media file in seconds (raises on a broken file)."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    text = (result.stdout or "").strip()
    if not text:
        raise ValueError(f"ffprobe не вернул длительность: {path}")
    return float(text)


def concat_videos(videos: Sequence[str | Path], target: str | Path) -> Path:
    """Join rendered scenes into one MP4 without re-encoding.

    All scenes come from the same template (h264 + aac 48 kHz, same size/fps),
    so the concat demuxer can stream-copy them.  A missing or empty list is an
    error: an empty film must never be reported as ready.
    """
    items = [Path(v) for v in videos]
    if not items:
        raise ValueError("Нечего склеивать: список сцен пуст.")
    missing = [str(path) for path in items if not path.exists()]
    if missing:
        raise FileNotFoundError("Нет файлов сцен: " + ", ".join(missing))
    out = Path(target)
    out.parent.mkdir(parents=True, exist_ok=True)
    listing = out.parent / "concat.txt"
    listing.write_text(
        "\n".join(f"file '{path.resolve().as_posix()}'" for path in items),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", str(out),
        ],
        check=True,
        shell=False,
    )
    return out


def film_contact_sheet(
    video: str | Path,
    duration: float,
    target: str | Path,
    *,
    cols: int = TILE_COLS,
    rows: int = TILE_ROWS,
) -> Path:
    """Contact sheet that covers the whole film, not just its first seconds."""
    out = Path(target)
    out.parent.mkdir(parents=True, exist_ok=True)
    rate = (cols * rows) / max(0.1, float(duration))
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
            "-vf", f"fps={rate:.6f},scale=300:-1,tile={cols}x{rows}",
            "-frames:v", "1", str(out),
        ],
        check=True,
        shell=False,
    )
    return out


def _cue_from_take(take: dict[str, Any], offset: float, scene_id: str = "") -> Cue | None:
    text = str(take.get("text") or "").strip()
    if not text:
        return None
    who = str(take.get("character") or "")
    name = CHARACTERS[who].name if who in CHARACTERS else who
    start = offset + float(take.get("start") or 0.0)
    duration = float(take.get("duration") or 0.0)
    if duration <= 0:
        return None
    return Cue(start=start, end=start + duration, text=f"{name}: {text}", scene_id=scene_id)


def _split_storyboard_episodes(
    storyboard: dict[str, Any], episodes: int,
) -> list[dict[str, Any]]:
    """Делит сториборд на ``episodes`` последовательных серий (D3/F6).

    Порядок сцен сохраняется; каждая серия — подмножество сцен с
    переопределённым заголовком (``title — серия N``).  Возвращает
    список под-сторибордов (dict).
    """
    scenes = [dict(scene) for scene in storyboard.get("scenes") or []]
    if not scenes:
        return []
    n = max(1, int(episodes))
    chunk = max(1, (len(scenes) + n - 1) // n)
    out: list[dict[str, Any]] = []
    base_title = str(storyboard.get("title") or "")
    for idx, i in enumerate(range(0, len(scenes), chunk), 1):
        part = scenes[i:i + chunk]
        if not part:
            continue
        ep = dict(storyboard)
        ep["scenes"] = part
        ep["title"] = f"{base_title} — серия {idx}" if base_title else f"Серия {idx}"
        out.append(ep)
    return out


def split_storyboard_episodes(
    storyboard: dict[str, Any], episodes: int,
) -> list[dict[str, Any]]:
    """Публичный адаптер Ч2.5: dict-сториборд → ``episodes`` серий.

    Работает на выходе ``blocky_story.parse_storyboard`` (словарь с ключом
    ``"scenes"``): сцены делятся на ``episodes`` последовательных групп
    (порядок сохраняется, каждый scene-dict переносится как есть), каждая
    серия наследует ``lang``/``source``/``idea`` исходного сториборда и
    получает заголовок ``«title — серия K»``.  Это словарная версия
    :func:`.contracts.make_episodes` (там — dataclass Storyboard → Episode);
    обхода через dataclasses здесь не нужно, blocky-конвейер живёт на dict.
    """
    return _split_storyboard_episodes(storyboard, episodes)


def produce_film(
    storyboard: dict[str, Any],
    out_dir: str | Path,
    *,
    voice: bool = True,
    timeout: int = 3600,
    resume: bool = True,
    episodes: int | None = None,
) -> dict[str, Any]:
    """Render every scene of ``storyboard`` and cut them into one film.

    A scene is reused when ``scene_XX/scene_report.json`` says ``OK`` and the
    mixed video still exists — this is the checkpoint that makes a 60-second
    film restartable after a crash.  ``resume=False`` forces a fresh render.
    """
    scenes = [dict(scene) for scene in storyboard.get("scenes") or []]
    if not scenes:
        raise ValueError("В раскадровке нет сцен.")
    out = Path(out_dir)
    if episodes and int(episodes) > 1 and len(scenes) > int(episodes):
        subs = _split_storyboard_episodes(storyboard, int(episodes))
        results: list[dict[str, Any]] = []
        status = "OK"
        for ep_i, sub in enumerate(subs, 1):
            rep = produce_film(
                sub, out / f"episode_{ep_i:02d}",
                voice=voice, timeout=timeout, resume=resume)
            results.append(rep)
            if rep.get("status") != "OK":
                status = "PARTIAL"
        return {
            "status": status,
            "episodes": [
                {"episode": i + 1, "status": r.get("status"),
                 "video": r.get("video")}
                for i, r in enumerate(results)
            ],
            "video": next((r.get("video") for r in results if r.get("video")), None),
            "story": str(storyboard.get("title") or ""),
        }
    out.mkdir(parents=True, exist_ok=True)
    write_storyboard(storyboard, out / "storyboard.json")
    paths = scene_files(storyboard, out / "scenes")

    # F1/RC1: подготовить сцены с учётом единого состояния мира между ними.
    # Состояние (положения, двери, предметы в руках) переносится из сцены в
    # сцену; подготовленная сцена записывается на место исходного файла.
    from .blocky_continuity import (
        FEATURE_VERSION,
        WorldState,
        hash_scene,
        prepare_scene_continuity,
        snapshot_final_state,
    )
    world = WorldState()
    continuity_issues: list[str] = []
    results: list[dict[str, Any]] = []
    videos: list[Path] = []
    cues: list[Cue] = []
    cursor = 0.0

    for index, (spath, raw) in enumerate(zip(paths, scenes), 1):
        if spath.exists():
            try:
                raw = json.loads(spath.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = dict(raw)
        prepared, issues = prepare_scene_continuity(raw, world, scene_index=index)
        continuity_issues.extend(f"Сцена {index}: {issue}" for issue in issues)
        spath.write_text(
            json.dumps(prepared, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        scene_out = out / f"scene_{index:02d}"
        report_path = scene_out / "scene_report.json"
        report: dict[str, Any] | None = None
        cached = False
        if resume and report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                report = None
            video = Path(str(report.get("final_audio") or "")) if report else None
            scene_qc_report = (report or {}).get("qc") or {}
            if (
                report
                and report.get("status") in {"OK", "PARTIAL"}
                and scene_qc_report.get("status") != "FAILED"
                and report.get("input_hash") == hash_scene(prepared)
                and video is not None
                and video.is_file()
            ):
                cached = True
            else:
                report = None
        if report is None:
            scene = BlockyScene.load(spath)
            report = scene.render(scene_out, timeout=timeout, voice=voice)
            report["input_hash"] = hash_scene(prepared)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        video_path = Path(str(report["final_audio"])) if report.get("final_audio") else None
        scene_status = str(report.get("status") or "FAILED")
        included = (
            scene_status in {"OK", "PARTIAL"}
            and video_path is not None and video_path.is_file()
        )
        scene_duration = float(report.get("duration") or 0.0)
        if included and not scene_duration:
            scene_duration = probe_duration(video_path)

        # Subtitle offsets follow only the clips actually included in the film.
        if included:
            for take in report.get("takes") or []:
                cue = _cue_from_take(take, cursor, f"scene_{index:02d}")
                if cue is not None:
                    cues.append(cue)
            videos.append(video_path)
        results.append(
            {
                "index": index,
                "title": str(prepared.get("title") or f"Сцена {index}"),
                "scene": str(spath),
                "output": str(scene_out),
                "status": scene_status,
                "error": report.get("error"),
                "cached": cached,
                "duration": round(scene_duration, 3),
                "video": str(video_path) if included else None,
                "dialogue_qc": report.get("dialogue_qc"),
                "camera_qc": report.get("camera_qc"),
                "qc": (report.get("qc") or {}).get("status"),
            }
        )
        if included:
            cursor += scene_duration

        # F1/RC1: после рендера переносим в мир ФАКТИЧЕСКОЕ место остановки
        # (final_root из отчёта), а не авторский origin, — иначе следующая сцена
        # той же локации начинается «с чистого листа» и персонаж телепортируется
        # обратно.
        actor_roots: dict[str, Any] = {}
        for d in (report.get("diagnostics") or {}).get("actors") or []:
            ch = d.get("character")
            fr = d.get("final_root")
            if ch and isinstance(fr, (list, tuple)) and len(fr) >= 3:
                actor_roots[str(ch)] = fr
        snapshot_final_state(
            prepared, world, scene_index=index,
            actor_roots=actor_roots or None,
        )

    report_out: dict[str, Any] = {
        "title": str(storyboard.get("title") or "Без названия"),
        "lang": str(storyboard.get("lang") or "ru"),
        "source": storyboard.get("source"),
        "scenes": results,
        "planned_duration": round(cursor, 3),
    }
    if not videos:
        report_out["status"] = "FAILED"
        report_out["error"] = "Ни одна сцена не отрендерилась."
        (out / "film_report.json").write_text(
            json.dumps(report_out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report_out

    film = concat_videos(videos, out / "film.mp4")
    duration = probe_duration(film)
    report_out["video"] = str(film)
    report_out["duration"] = round(duration, 3)
    report_out["status"] = (
        "OK" if len(videos) == len(scenes) else "PARTIAL"
    )
    if len(videos) != len(scenes):
        report_out["error"] = (
            f"Отрендерено {len(videos)} из {len(scenes)} сцен."
        )
    if cues:
        subtitles = out / "subtitles"
        subtitles.mkdir(parents=True, exist_ok=True)
        report_out["subtitles"] = str(
            write_srt(cues, subtitles / f"{report_out['lang']}.srt")
        )
        report_out["cues"] = len(cues)
    report_out["contact_sheet"] = str(
        film_contact_sheet(film, duration, out / "contact_sheet.jpg")
    )
    report_out["continuity_qc"] = {
        "ok": not continuity_issues,
        "issues": continuity_issues,
        "version": FEATURE_VERSION,
    }
    # F1-гейт: подтверждённые проблемы непрерывности не позволяют фильму
    # считаться полностью готовым (обнаружено, но не исправлено -> PARTIAL).
    if continuity_issues and report_out["status"] == "OK":
        report_out["status"] = "PARTIAL"
    # F1/B-07/B-20: обязательные QC-гейты фильма влияют на итоговый статус.
    from .blocky_qc import film_qc
    film_q = film_qc(
        report_out, srt_path=(report_out.get("subtitles") or "") or None
    )
    report_out["qc"] = film_q
    if len(videos) != len(scenes):
        report_out["status"] = "PARTIAL"
    elif film_q["status"] == "FAILED":
        report_out["status"] = "FAILED"
        report_out["error"] = (
            "QC-гейты фильма провалены: " + ", ".join(film_q["fails"])
        )
    elif film_q["status"] == "PARTIAL":
        report_out["status"] = "PARTIAL"
    else:
        report_out["status"] = "OK"
    (out / "film_report.json").write_text(
        json.dumps(report_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report_out
