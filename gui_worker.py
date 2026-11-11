"""Backend for ShortsFactory Studio (desktop_app).

Contains two kinds of code:

* **Pure helpers** (no Tk, no heavy imports): film-library scan,
  minimal ``music`` flag update for ``config/config.toml``, Ollama model
  list.  They are covered by ``tests/test_desktop_helpers.py``.
* **Subcommands** executed in a separate process so that the GUI stays
  responsive and PyInstaller does not pull LLM dependencies into the exe.
  Progress is streamed to stdout with the prefixes ``##STAGE##``,
  ``##TOKEN##``, ``##CHECK##``, ``##RESULT##`` and ``##ERROR##``.

Subcommands:
    film   --idea "..." --scenes N --lang ru [--seconds S] [--model M] [--episodes 1]
    doctor [--full]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUTPUT_DIR = ROOT / "output"
FILMS_DIR = OUTPUT_DIR / "blocky_films"
CONFIG_PATH = ROOT / "config" / "config.toml"

#: Dirs that contain one of these files next to a ``film_report.json``
#: (or alone) are treated as produced films by :func:`scan_film_library`.
FINAL_VIDEO_NAMES = ("final_audio.mp4", "final_acted_30s.mp4")
FILM_REPORT_NAME = "film_report.json"
SERIES_REPORT_NAME = "series_report.json"

DEFAULT_MODEL = "qwen2.5:7b"


def slugify(text: str) -> str:
    """Filesystem-safe slug for output folder names (cyrillic kept)."""
    s = re.sub(r"[^A-Za-zА-Яа-яЁё0-9]+", "_", (text or "").lower()).strip("_")
    return (s or "film")[:48]


# ------------------------------------------------------------------ library
def _read_film_report(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def scan_film_library(output_dir: str | Path = OUTPUT_DIR, max_depth: int = 2) -> list[dict[str, Any]]:
    """Scan ``output/`` for produced films (pure helper).

    A directory counts as a film when it contains ``film_report.json`` or one
    of :data:`FINAL_VIDEO_NAMES`.  Scans the top level and one nested level
    (covers ``output/blocky_films/<film>`` and ``output/<series>/ep_01``).
    Returns dicts sorted newest-first:
    ``{path, title, status, duration, video, contact_sheet, report}``.
    """
    root = Path(output_dir)
    if not root.exists():
        return []

    dirs: list[Path] = []
    try:
        level1 = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []
    dirs.extend(level1)
    if max_depth >= 2:
        for child in level1:
            try:
                dirs.extend(sorted(p for p in child.iterdir() if p.is_dir()))
            except OSError:
                continue

    films: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for d in dirs:
        # служебные папки и отдельные сцены (scene_XX) — не фильмы
        if d.name.startswith("_") or re.fullmatch(r"scene_\d+", d.name) \
                or d in seen:
            continue
        report = _read_film_report(d / FILM_REPORT_NAME)
        video = None
        if report:
            raw_video = report.get("final_audio") or report.get("video") or ""
            if raw_video:
                video = Path(str(raw_video))
                if not video.is_absolute():
                    video = d / video
                if not video.exists():
                    video = None
        if video is None:
            for name in FINAL_VIDEO_NAMES:
                candidate = d / name
                if candidate.exists():
                    video = candidate
                    break
        if report is None and video is None:
            continue
        seen.add(d)
        contact = d / "contact_sheet.jpg"
        mtimes = [0.0]
        for probe in (d / FILM_REPORT_NAME, video):
            if probe is not None and probe.exists():
                try:
                    mtimes.append(probe.stat().st_mtime)
                except OSError:
                    pass
        mtime = max(mtimes)
        films.append(
            {
                "path": d,
                "title": str((report or {}).get("title") or d.name),
                "status": str((report or {}).get("status") or ("OK" if video else "FAILED")),
                "duration": (report or {}).get("duration"),
                "video": video,
                "contact_sheet": contact if contact.exists() else None,
                "report": d / FILM_REPORT_NAME if report else None,
                "mtime": mtime,
            }
        )
    films.sort(key=lambda item: item["mtime"], reverse=True)
    return films


# ------------------------------------------------------------------- config
def read_config_music_flag(config_path: str | Path = CONFIG_PATH) -> bool:
    """Current ``[audio] music`` value (false when missing/unreadable)."""
    try:
        with Path(config_path).open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    audio = data.get("audio")
    return bool(audio.get("music", False)) if isinstance(audio, dict) else False


def set_config_music_flag(config_path: str | Path, enabled: bool) -> bool:
    """Minimal in-place TOML update of the ``music`` line under ``[audio]``.

    Deliberately a text-level edit (stdlib only): the rest of the file —
    comments, key order, formatting — is preserved byte-for-byte.  Limitation:
    the file must stay valid TOML; tables and arrays of tables are recognised
    by their ``[header]`` lines, so a quoted key containing ``]`` would confuse
    the section scan (the real config does not use those).

    Returns True when the file was written.
    """
    path = Path(config_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    value = "true" if enabled else "false"
    lines = text.splitlines()
    in_audio = False
    replaced = False
    section_end = None  # index just past the last line of the [audio] table
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("[["):
            if in_audio and not replaced:
                section_end = i  # insert before the next table header
            in_audio = stripped == "[audio]"
            continue
        if in_audio and re.match(r"^\s*music\s*=", line):
            lines[i] = re.sub(r"=.*$", f"= {value}", line, count=1)
            replaced = True
    if not replaced:
        if in_audio:
            # [audio] is the last table: append at the end of the file
            lines.append(f"music = {value}")
        elif section_end is not None:
            lines.insert(section_end, f"music = {value}")
        else:
            lines += ["", "[audio]", f"music = {value}"]
    try:
        path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") or not text else ""),
                        encoding="utf-8")
    except OSError:
        return False
    return True


def ollama_model_choices(config_path: str | Path = CONFIG_PATH) -> list[str]:
    """Model dropdown values: configured model + fallbacks + default."""
    choices: list[str] = []
    try:
        with Path(config_path).open("rb") as fh:
            data = tomllib.load(fh)
        llm = data.get("llm") or {}
        for item in [llm.get("model"), *(llm.get("fallback_models") or [])]:
            if item and str(item) not in choices:
                choices.append(str(item))
    except (OSError, tomllib.TOMLDecodeError):
        pass
    if DEFAULT_MODEL not in choices:
        choices.append(DEFAULT_MODEL)
    return choices


# ------------------------------------------------------------------ stream
def _emit(kind: str, payload: Any) -> None:
    print(f"##{kind}## {json.dumps(payload, ensure_ascii=False)}", flush=True)


def _emit_text(kind: str, message: str) -> None:
    print(f"##{kind}## {message}", flush=True)


# ------------------------------------------------------------------- film
def _series_report(storyboard: dict[str, Any], reports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "title": str(storyboard.get("title") or ""),
        "episodes": reports,
        "status": "OK" if all(rep.get("status") == "OK" for rep in reports) else "PARTIAL",
    }


def cmd_film(args: argparse.Namespace) -> int:
    """Idea → storyboard → (series split) → produce_film. Streams progress."""
    from shortsfactory.config import load_config
    from shortsfactory.generative.blocky_production import (
        produce_film,
        split_storyboard_episodes,
    )
    from shortsfactory.generative.blocky_story import generate_storyboard

    config = load_config(ROOT)
    idea = str(args.idea).strip()
    if not idea:
        _emit_text("ERROR", "Идея пустая.")
        return 2

    _emit_text("STAGE", "Генерация раскадровки (Ollama → fallback)…")

    def on_token(chunk: str) -> None:
        _emit_text("TOKEN", chunk)

    try:
        storyboard = generate_storyboard(
            idea,
            config=config,
            scenes=max(1, int(args.scenes)),
            seconds=float(args.seconds) if args.seconds else None,
            lang=args.lang,
            model=args.model or None,
            on_token=on_token,
            stream=True,
        )
    except Exception as exc:  # noqa: BLE001 — LLM/JSON/схема: контролируемая ошибка
        _emit_text("ERROR", f"Ошибка генерации сценария: {type(exc).__name__}: {exc}")
        return 3
    _emit_text(
        "STAGE",
        f"Раскадровка готова: источник {storyboard.get('source')}, "
        f"сцен {len(storyboard.get('scenes') or [])}",
    )

    out_dir = FILMS_DIR / slugify(str(storyboard.get("title") or idea))
    episodes = max(1, int(args.episodes))
    try:
        if episodes > 1:
            parts = split_storyboard_episodes(storyboard, episodes)
            if not parts:
                _emit_text("ERROR", "В раскадровке нет сцен: делить на серии нечего.")
                return 2
            reports: list[dict[str, Any]] = []
            for index, part in enumerate(parts, 1):
                _emit_text("STAGE", f"Серия {index:02d}/{len(parts)}: «{part.get('title')}» — производство…")
                reports.append(
                    produce_film(part, out_dir / f"ep_{index:02d}", voice=True, resume=True)
                )
                _emit_text(
                    "STAGE",
                    f"Серия {index:02d}/{len(parts)}: {reports[-1].get('status')}",
                )
            series = _series_report(storyboard, reports)
            out_dir.mkdir(parents=True, exist_ok=True)
            report_path = out_dir / SERIES_REPORT_NAME
            report_path.write_text(
                json.dumps(series, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            result = {"kind": "series", "series": series, "report": str(report_path),
                      "dir": str(out_dir)}
        else:
            _emit_text("STAGE", "Производство фильма (сцены → рендер → склейка)…")
            report = produce_film(storyboard, out_dir, voice=True, resume=True)
            result = {"kind": "film", "report": report, "dir": str(out_dir)}
    except Exception as exc:  # noqa: BLE001
        _emit_text("ERROR", f"Ошибка производства фильма: {type(exc).__name__}: {exc}")
        return 3
    _emit("RESULT", result)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Run src/shortsfactory/doctor.py checks and stream them as JSON rows."""
    from shortsfactory.config import load_config
    from shortsfactory.doctor import Doctor

    config = load_config(ROOT)
    result = Doctor(config).run(full=bool(args.full))
    for check in result.checks:
        _emit("CHECK", check.to_dict())
    _emit("RESULT", {
        "ok": result.ok,
        "failures": len(result.failures),
        "warnings": len(result.warnings),
        "logs_dir": str(config.logs_dir),
        "models_dir": str(config.models_dir),
        "output_dir": str(config.output_dir),
        "config_path": str(config.config_path),
        "fonts_dir": str(config.fonts_dir),
    })
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="gui_worker")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_film = sub.add_parser("film", help="idea → storyboard → film/series")
    p_film.add_argument("--idea", required=True)
    p_film.add_argument("--scenes", type=int, default=3)
    p_film.add_argument("--episodes", type=int, default=1)
    p_film.add_argument("--lang", default="ru", choices=["ru", "en"])
    p_film.add_argument("--seconds", type=float, default=0.0)
    p_film.add_argument("--model", default="")
    p_film.set_defaults(func=cmd_film)

    p_doc = sub.add_parser("doctor", help="environment diagnostics")
    p_doc.add_argument("--full", action="store_true")
    p_doc.set_defaults(func=cmd_doctor)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
