"""ShortsFactory Studio — desktop shell for the ShortsFactory pipelines.

Запуск:
    cd D:\\ShortsFactory
    .venv\\Scripts\\python.exe desktop_app.py

Structure:
    * palette / theme (ttk over 'clam', dark)
    * Montserrat registration (fonts/, private GDI) with Segoe UI fallback
    * App shell: header (version + environment dots), sidebar navigation,
      five screens (Фильм / Сериалы / Библиотека / Настройки / Диагностика),
      status bar with spinner
    * all long work runs in worker threads / the ``gui_worker.py``
      subprocess; UI is updated through a single queue
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import tomllib
from pathlib import Path

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

try:  # Pillow is available in the project venv; GUI degrades without it
    from PIL import Image as PILImage
    from PIL import ImageTk
except Exception:  # noqa: BLE001 - pragma: no cover
    PILImage = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]


def _base_dir() -> Path:
    """Project root: next to the frozen exe, or the script's folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _python_executable() -> str:
    """The real interpreter used to spawn subprocesses (frozen-build aware)."""
    if getattr(sys, "frozen", False):
        candidates = [
            Path(sys.prefix) / "python.exe",
            Path(sys.executable).resolve().parent.parent / "python.exe",
            Path(sys.executable).resolve().parent / "python.exe",
        ]
        for cand in candidates:
            try:
                if cand.exists():
                    return str(cand)
            except Exception:  # noqa: BLE001
                continue
        try:
            import winreg  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            winreg = None
        if winreg is not None:
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, r"Software\Python\PythonCore") as k:
                        idx = 0
                        while True:
                            try:
                                ver = winreg.EnumKey(k, idx)
                            except OSError:
                                break
                            idx += 1
                            try:
                                with winreg.OpenKey(
                                    hive, rf"Software\Python\PythonCore\{ver}\InstallPath"
                                ) as vk:
                                    base, _ = winreg.QueryValueEx(vk, "")
                            except OSError:
                                continue
                            exe = Path(base) / "python.exe"
                            if exe.exists():
                                return str(exe)
                except OSError:
                    continue
        return getattr(sys, "_base_executable", sys.executable)
    return sys.executable


ROOT = _base_dir()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import gui_worker  # noqa: E402  (helpers + subprocess backends)

GUI_WORKER = ROOT / "gui_worker.py"

# ------------------------------------------------------------------ version
VERSION_FALLBACK = "1.0.0"  # keep in sync with config.toml [app] / pyproject


def _read_version() -> str:
    try:
        with (ROOT / "config" / "config.toml").open("rb") as fh:
            data = tomllib.load(fh)
        return str(data.get("app", {}).get("version") or VERSION_FALLBACK)
    except Exception:  # noqa: BLE001
        return VERSION_FALLBACK


VERSION = _read_version()

# ----------------------------------------------------------------- palette
BG = "#1e1f24"        # app background
PANEL = "#26282e"     # header / sidebar
CARD = "#2c2f36"      # cards and inputs
INPUT = "#232529"     # text/entry fields
EDGE = "#3a3d45"      # borders
ACCENT = "#58c04d"    # Minecraft grass green
ACCENT_D = "#3f9a36"
CYAN = "#4fc3f7"      # secondary accent
TEXT = "#e8eaed"
MUT = "#9aa0a6"
OK = "#4cc38a"
WARN = "#ffb020"
ERR = "#ff6b6b"
SEL = "#41454e"

APP_TITLE = "ShortsFactory Studio"
FONT_FAMILY = "Segoe UI"  # replaced by Montserrat when registration succeeds


def _register_fonts() -> str:
    """Register Montserrat (Regular/SemiBold/Bold) privately for this process.

    Returns the Tk family name to use, or 'Segoe UI' when unavailable.
    """
    global FONT_FAMILY
    if sys.platform != "win32":
        return FONT_FAMILY
    try:
        import ctypes

        FR_PRIVATE = 0x10
        registered = False
        for name in (
            "Montserrat-Regular.ttf",
            "Montserrat-SemiBold.ttf",
            "Montserrat-Bold.ttf",
        ):
            path = ROOT / "fonts" / name
            if path.exists() and ctypes.windll.gdi32.AddFontResourceExW(
                str(path), FR_PRIVATE, 0
            ):
                registered = True
        families = set(tkfont.families())
        if "Montserrat" in families:
            FONT_FAMILY = "Montserrat"
            return FONT_FAMILY
        if registered and "Montserrat SemiBold" in families:
            FONT_FAMILY = "Montserrat SemiBold"
            return FONT_FAMILY
    except Exception:  # noqa: BLE001 - any GDI/Tk failure keeps Segoe UI
        pass
    return FONT_FAMILY


def fnt(size: int, bold: bool = False) -> tuple[str, int, str]:
    return (FONT_FAMILY, size, "bold" if bold else "normal")


# ------------------------------------------------------------------- theme
def apply_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=BG)
    root.option_add("*TCombobox*Listbox.background", INPUT)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SEL)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*Font", fnt(10))

    style.configure(".", background=BG, foreground=TEXT, font=fnt(10),
                    bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE)

    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Card.TFrame", background=CARD)

    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT)
    style.configure("Muted.TLabel", background=CARD, foreground=MUT, font=fnt(9))
    style.configure("MutedBG.TLabel", background=BG, foreground=MUT, font=fnt(9))
    style.configure("CardTitle.TLabel", background=CARD, foreground=MUT,
                    font=(FONT_FAMILY, 9, "bold"))
    style.configure("H1.TLabel", background=PANEL, foreground=TEXT,
                    font=(FONT_FAMILY, 15, "bold"))
    style.configure("H2.TLabel", background=PANEL, foreground=MUT, font=fnt(9))

    style.configure("TButton", background=CARD, foreground=TEXT,
                    bordercolor=EDGE, padding=(12, 6), focuscolor=CARD,
                    font=fnt(10))
    style.map("TButton",
              background=[("active", SEL), ("disabled", "#26282e")],
              foreground=[("disabled", "#6b6f78")])
    style.configure("Accent.TButton", background=ACCENT, foreground="#12250f",
                    font=(FONT_FAMILY, 11, "bold"), padding=(16, 8),
                    bordercolor=ACCENT_D)
    style.map("Accent.TButton",
              background=[("active", ACCENT_D), ("disabled", "#2f4a2b")],
              foreground=[("disabled", "#5d6b5a")])

    style.configure("TEntry", fieldbackground=INPUT, foreground=TEXT,
                    insertcolor=TEXT, bordercolor=EDGE, lightcolor=INPUT,
                    darkcolor=INPUT, padding=5)
    style.map("TEntry", bordercolor=[("focus", CYAN)])
    style.configure("TSpinbox", fieldbackground=INPUT, foreground=TEXT,
                    insertcolor=TEXT, bordercolor=EDGE, arrowcolor=TEXT,
                    lightcolor=INPUT, darkcolor=INPUT, padding=(6, 3))
    style.configure("TCombobox", fieldbackground=INPUT, background=CARD,
                    foreground=TEXT, arrowcolor=TEXT, bordercolor=EDGE,
                    lightcolor=INPUT, darkcolor=INPUT, padding=5)
    style.map("TCombobox",
              fieldbackground=[("readonly", INPUT), ("disabled", "#26282e")],
              foreground=[("disabled", "#6b6f78")])

    style.configure("TCheckbutton", background=CARD, foreground=TEXT,
                    focuscolor=CARD, indicatorcolor=INPUT,
                    indicatorbackground=INPUT, indicatorforeground=ACCENT)
    style.map("TCheckbutton",
              background=[("active", CARD)],
              foreground=[("disabled", "#6b6f78")])

    style.configure("Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=INPUT, bordercolor=INPUT, lightcolor=ACCENT,
                    darkcolor=ACCENT)

    style.configure("TScrollbar", background=CARD, troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUT, lightcolor=CARD,
                    darkcolor=CARD)
    style.map("TScrollbar", background=[("active", SEL)])

    style.configure("Treeview", background=INPUT, fieldbackground=INPUT,
                    foreground=TEXT, bordercolor=EDGE, font=fnt(10),
                    rowheight=30)
    style.map("Treeview",
              background=[("selected", SEL)],
              foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", background=CARD, foreground=MUT,
                    font=(FONT_FAMILY, 9, "bold"), bordercolor=EDGE,
                    relief="flat", padding=(8, 5))
    style.map("Treeview.Heading", background=[("active", SEL)])


# ------------------------------------------------------------------ widgets
def make_text(parent: tk.Misc, height: int = 6, mono: bool = False,
              read_only: bool = False, bg: str = INPUT) -> tk.Text:
    text = tk.Text(
        parent, bg=bg, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
        highlightthickness=1, highlightbackground=EDGE, highlightcolor=CYAN,
        font=("Consolas", 9) if mono else fnt(10), wrap="word" if not mono else "char",
        height=height, state="disabled" if read_only else "normal",
        padx=8, pady=6, undo=True,
    )
    return text


def make_log_view(parent: tk.Misc, height: int = 10) -> tk.Text:
    """Monospace live-log with a thin scrollbar and autoscroll."""
    frame = tk.Frame(parent, bg=CARD)
    text = make_text(frame, height=height, mono=True, read_only=True)
    bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    return text


def status_color(status: str) -> str:
    return {"OK": OK, "READY": OK, "PASS": OK,
            "PARTIAL": WARN, "WARNING": WARN,
            "FAILED": ERR, "FAIL": ERR}.get(str(status).upper(), MUT)


def fmt_duration(seconds: object) -> str:
    try:
        value = float(seconds)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    return f"{value:.1f} c" if value > 0 else "—"


class Tooltip:
    """Small dark tooltip shown on hover (works for disabled widgets too)."""

    def __init__(self, widget: tk.Misc, get_text):
        self.widget = widget
        self.get_text = get_text
        self.tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _event: tk.Event) -> None:
        text = self.get_text()
        if not text:
            return
        self._hide()
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        rx, ry = self.widget.winfo_rootx(), self.widget.winfo_rooty()
        self.tip.wm_geometry(f"+{rx}+{ry + self.widget.winfo_height() + 6}")
        tk.Label(self.tip, text=text, bg="#16171b", fg=TEXT, justify="left",
                 font=fnt(9), padx=8, pady=5, relief="solid", bd=1,
                 highlightthickness=0).pack()

    def _hide(self, *_args: tk.Event) -> None:
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


def paint_dot(canvas: tk.Canvas, color: str, size: int = 12) -> None:
    canvas.delete("all")
    canvas.create_oval(2, 2, size - 2, size - 2, fill=color, outline="")


# ------------------------------------------------------------------ screens
class Screen(ttk.Frame):
    """Base screen: a full-area frame with a card helper."""

    def __init__(self, parent: tk.Misc, app: "App") -> None:
        super().__init__(parent, style="TFrame")
        self.app = app
        self.build()

    def build(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


def card(parent: tk.Misc, title: str | None = None,
         padding: int = 12) -> tuple[tk.Frame, tk.Frame]:
    """Dark card: returns (outer, inner). Title, if any, sits above inner."""
    outer = tk.Frame(parent, bg=CARD, highlightbackground=EDGE,
                     highlightthickness=1)
    if title:
        tk.Label(outer, text=title.upper(), bg=CARD, fg=MUT,
                 font=(FONT_FAMILY, 9, "bold")).pack(
            anchor="w", padx=padding, pady=(padding, 2))
    inner = tk.Frame(outer, bg=CARD)
    inner.pack(fill="both", expand=True, padx=padding,
               pady=(2 if title else padding, padding))
    return outer, inner


class FilmScreen(Screen):
    def build(self) -> None:
        app = self.app
        grid = tk.Frame(self, bg=BG)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(1, weight=1)
        grid.rowconfigure(0, weight=1)

        # ------------------------------------------------ left: parameters
        left = tk.Frame(grid, bg=BG)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        form_outer, form = card(left, "Параметры фильма")
        form_outer.pack(fill="both", expand=True)
        form.configure(width=380)
        form.pack_propagate(False)

        tk.Label(form, text="Идея", bg=CARD, fg=MUT,
                 font=fnt(9)).pack(anchor="w")
        self.idea = make_text(form, height=6)
        self.idea.pack(fill="x", pady=(2, 8))

        row1 = tk.Frame(form, bg=CARD)
        row1.pack(fill="x")
        tk.Label(row1, text="Сцен", bg=CARD, fg=MUT, font=fnt(9)).pack(
            side="left")
        self.scenes = tk.StringVar(value="3")
        ttk.Spinbox(row1, from_=1, to=6, textvariable=self.scenes, width=5,
                    font=fnt(10)).pack(side="left", padx=(4, 14))
        tk.Label(row1, text="Язык", bg=CARD, fg=MUT, font=fnt(9)).pack(
            side="left")
        self.lang = tk.StringVar(value="ru")
        ttk.Combobox(row1, textvariable=self.lang, values=("ru", "en"),
                     state="readonly", width=5, font=fnt(10)).pack(
            side="left", padx=4)

        row2 = tk.Frame(form, bg=CARD)
        row2.pack(fill="x", pady=(8, 0))
        tk.Label(row2, text="Длительность, с", bg=CARD, fg=MUT,
                 font=fnt(9)).pack(side="left")
        self.seconds = tk.StringVar()
        ttk.Entry(row2, textvariable=self.seconds, width=7,
                  font=fnt(10)).pack(side="left", padx=4)
        tk.Label(row2, text="(пусто — на усмотрение модели)", bg=CARD,
                 fg=MUT, font=fnt(8)).pack(side="left")

        tk.Label(form, text="Модель Ollama", bg=CARD, fg=MUT,
                 font=fnt(9)).pack(anchor="w", pady=(8, 0))
        self.model = tk.StringVar(
            value=(gui_worker.ollama_model_choices() or [gui_worker.DEFAULT_MODEL])[0])
        ttk.Combobox(
            form, textvariable=self.model,
            values=gui_worker.ollama_model_choices(), state="readonly",
            font=fnt(10)).pack(fill="x", pady=(2, 8))

        self.music = tk.BooleanVar(
            value=gui_worker.read_config_music_flag())
        ttk.Checkbutton(form, text="Фоновая музыка (config [audio] music)",
                        variable=self.music).pack(anchor="w", pady=(0, 10))

        buttons = tk.Frame(form, bg=CARD)
        buttons.pack(fill="x", side="bottom")
        self.create_btn = ttk.Button(buttons, text="Создать фильм",
                                     style="Accent.TButton",
                                     command=self.start, state="disabled")
        self.create_btn.pack(fill="x")
        self.cancel_btn = ttk.Button(buttons, text="Отменить",
                                     command=app.cancel_job, state="disabled")
        self.cancel_btn.pack(fill="x", pady=(6, 0))
        Tooltip(self.create_btn, app.pipeline_block_reason)

        # ------------------------------------------------- right: log + result
        right = tk.Frame(grid, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        log_outer, log_holder = card(right, "Журнал")
        log_outer.grid(row=0, column=0, sticky="nsew")
        self.progress = ttk.Progressbar(log_holder, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 6))
        self.log = make_log_view(log_holder, height=10)
        self.log.pack(fill="both", expand=True)
        app.register_log(self.log)

        res_outer, res = card(right, "Результат")
        res_outer.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.result_title = tk.Label(
            res, text="Готового фильма пока нет — запустите производство.",
            bg=CARD, fg=MUT, font=fnt(10), anchor="w")
        self.result_title.pack(fill="x")
        self.result_status = tk.Label(res, text="", bg=CARD, fg=MUT,
                                      font=fnt(10, True), anchor="w")
        self.result_status.pack(fill="x", pady=(4, 0))
        self.result_info = tk.Label(res, text="", bg=CARD, fg=MUT,
                                    font=fnt(9), anchor="w", justify="left")
        self.result_info.pack(fill="x", pady=(2, 6))
        res_btns = tk.Frame(res, bg=CARD)
        res_btns.pack(fill="x")
        self.open_dir_btn = ttk.Button(res_btns, text="Открыть папку",
                                       command=lambda: app.open_path(self.last_dir),
                                       state="disabled")
        self.open_dir_btn.pack(side="left")
        self.sheet_btn = ttk.Button(res_btns, text="Показать контактный лист",
                                    command=self.show_sheet, state="disabled")
        self.sheet_btn.pack(side="left", padx=6)
        self.open_video_btn = ttk.Button(res_btns, text="Открыть видео",
                                         command=lambda: app.open_path(self.last_video),
                                         state="disabled")
        self.open_video_btn.pack(side="left")
        self.last_dir: Path | None = None
        self.last_video: Path | None = None
        self.last_sheet: Path | None = None

    def start(self) -> None:
        idea = self.idea.get("1.0", "end").strip()
        if not idea:
            self.app.log("Введите идею фильма.")
            return
        try:
            scenes = max(1, min(6, int(self.scenes.get())))
        except ValueError:
            scenes = 3
        args = ["film", "--idea", idea, "--scenes", str(scenes),
                "--lang", self.lang.get()]
        seconds = self.seconds.get().strip()
        if seconds:
            try:
                float(seconds)
                args += ["--seconds", seconds]
            except ValueError:
                self.app.log("Длительность игнорируется: не число.")
        model = self.model.get().strip()
        if model:
            args += ["--model", model]
        self.app.run_pipeline(args, job="film", music_flag=self.music.get())

    def show_result(self, report: dict) -> None:
        status = str(report.get("status") or "?")
        self.result_title.configure(
            text=str(report.get("title") or "Фильм"),
            fg=TEXT, font=fnt(11, True))
        self.result_status.configure(text=f"Статус: {status}",
                                     fg=status_color(status))
        if report.get("dir"):
            self.last_dir = Path(str(report["dir"]))
        sheet = report.get("contact_sheet")
        video = report.get("video")
        self.last_video = Path(str(video)) if video else None
        self.last_sheet = Path(str(sheet)) if sheet else None
        self.result_info.configure(text=(
            f"Видео: {video or '—'}\n"
            f"Длительность: {fmt_duration(report.get('duration'))}   "
            f"Сцены: {len(report.get('scenes') or [])}   "
            f"Субтитры: {report.get('cues', 0)}   "
            f"QC: {(report.get('qc') or {}).get('status') or '—'}"
        ))
        for btn, enabled in ((self.open_dir_btn, bool(self.last_dir)),
                             (self.open_video_btn, bool(self.last_video)),
                             (self.sheet_btn, bool(self.last_sheet))):
            btn.configure(state="normal" if enabled else "disabled")

    def show_sheet(self) -> None:
        if self.last_sheet:
            self.app.show_contact_sheet(self.last_sheet)

    def set_busy(self, busy: bool) -> None:
        # create_btn is re-enabled by App._refresh_gate_buttons when idle
        self.create_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal" if busy else "disabled")


class SeriesScreen(Screen):
    def build(self) -> None:
        app = self.app
        grid = tk.Frame(self, bg=BG)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(1, weight=1)
        grid.rowconfigure(0, weight=1)

        left = tk.Frame(grid, bg=BG)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        form_outer, form = card(left, "Параметры сериала")
        form_outer.pack(fill="both", expand=True)
        form.configure(width=380)
        form.pack_propagate(False)

        tk.Label(form, text="Идея", bg=CARD, fg=MUT, font=fnt(9)).pack(
            anchor="w")
        self.idea = make_text(form, height=6)
        self.idea.pack(fill="x", pady=(2, 8))

        row1 = tk.Frame(form, bg=CARD)
        row1.pack(fill="x")
        tk.Label(row1, text="Сцен всего", bg=CARD, fg=MUT,
                 font=fnt(9)).pack(side="left")
        self.scenes = tk.StringVar(value="6")
        ttk.Spinbox(row1, from_=1, to=6, textvariable=self.scenes, width=5,
                    font=fnt(10)).pack(side="left", padx=(4, 14))
        tk.Label(row1, text="Серий", bg=CARD, fg=MUT, font=fnt(9)).pack(
            side="left")
        self.episodes = tk.StringVar(value="2")
        ttk.Spinbox(row1, from_=2, to=8, textvariable=self.episodes, width=5,
                    font=fnt(10)).pack(side="left", padx=4)

        row2 = tk.Frame(form, bg=CARD)
        row2.pack(fill="x", pady=(8, 0))
        tk.Label(row2, text="Язык", bg=CARD, fg=MUT, font=fnt(9)).pack(
            side="left")
        self.lang = tk.StringVar(value="ru")
        ttk.Combobox(row2, textvariable=self.lang, values=("ru", "en"),
                     state="readonly", width=5, font=fnt(10)).pack(
            side="left", padx=4)

        tk.Label(form, text="Модель Ollama", bg=CARD, fg=MUT,
                 font=fnt(9)).pack(anchor="w", pady=(8, 0))
        self.model = tk.StringVar(
            value=(gui_worker.ollama_model_choices() or [gui_worker.DEFAULT_MODEL])[0])
        ttk.Combobox(form, textvariable=self.model,
                     values=gui_worker.ollama_model_choices(),
                     state="readonly", font=fnt(10)).pack(fill="x", pady=(2, 8))

        self.music = tk.BooleanVar(value=gui_worker.read_config_music_flag())
        ttk.Checkbutton(form, text="Фоновая музыка (config [audio] music)",
                        variable=self.music).pack(anchor="w", pady=(0, 10))

        buttons = tk.Frame(form, bg=CARD)
        buttons.pack(fill="x", side="bottom")
        self.create_btn = ttk.Button(buttons, text="Создать сериал",
                                     style="Accent.TButton",
                                     command=self.start, state="disabled")
        self.create_btn.pack(fill="x")
        self.cancel_btn = ttk.Button(buttons, text="Отменить",
                                     command=app.cancel_job, state="disabled")
        self.cancel_btn.pack(fill="x", pady=(6, 0))
        Tooltip(self.create_btn, app.pipeline_block_reason)

        right = tk.Frame(grid, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        res_outer, res = card(right, "Результаты серий")
        res_outer.grid(row=0, column=0, sticky="ew")
        self.series_status = tk.Label(res, text="Сериалов пока нет.",
                                      bg=CARD, fg=MUT, font=fnt(10),
                                      anchor="w")
        self.series_status.pack(fill="x")
        cols = ("ep", "status", "duration", "video")
        self.table = ttk.Treeview(res, columns=cols, show="headings", height=6)
        for col, text, width, anchor in (
            ("ep", "Серия", 70, "center"),
            ("status", "Статус", 90, "center"),
            ("duration", "Длительность", 100, "center"),
            ("video", "Видео", 380, "w"),
        ):
            self.table.heading(col, text=text)
            self.table.column(col, width=width, anchor=anchor,
                              stretch=(col == "video"))
        self.table.tag_configure("OK", foreground=OK)
        self.table.tag_configure("PARTIAL", foreground=WARN)
        self.table.tag_configure("FAILED", foreground=ERR)
        self.table.pack(fill="x", pady=(6, 4))
        self.report_label = tk.Label(res, text="", bg=CARD, fg=MUT,
                                     font=fnt(9), anchor="w")
        self.report_label.pack(fill="x", pady=(0, 4))
        self.open_dir_btn = ttk.Button(res, text="Открыть папку сериала",
                                       command=lambda: app.open_path(self.last_dir),
                                       state="disabled")
        self.open_dir_btn.pack(anchor="w", pady=(0, 4))
        self.last_dir: Path | None = None

        log_outer, log_holder = card(right, "Журнал")
        log_outer.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.log = make_log_view(log_holder, height=8)
        self.log.pack(fill="both", expand=True)
        app.register_log(self.log)

    def start(self) -> None:
        idea = self.idea.get("1.0", "end").strip()
        if not idea:
            self.app.log("Введите идею сериала.")
            return
        try:
            scenes = max(1, min(6, int(self.scenes.get())))
        except ValueError:
            scenes = 6
        try:
            episodes = max(2, min(8, int(self.episodes.get())))
        except ValueError:
            episodes = 2
        args = ["film", "--idea", idea, "--scenes", str(scenes),
                "--episodes", str(episodes), "--lang", self.lang.get()]
        model = self.model.get().strip()
        if model:
            args += ["--model", model]
        self.app.run_pipeline(args, job="series", music_flag=self.music.get())

    def show_result(self, result: dict) -> None:
        series = result.get("series") or {}
        status = str(series.get("status") or "?")
        self.series_status.configure(
            text=f"«{series.get('title') or 'Сериал'}» — статус: {status}",
            fg=status_color(status))
        self.table.delete(*self.table.get_children())
        for index, episode in enumerate(series.get("episodes") or [], 1):
            self.table.insert("", "end", iid=str(index), values=(
                f"{index:02d}",
                episode.get("status") or "—",
                fmt_duration(episode.get("duration")),
                episode.get("video") or "—",
            ), tags=(str(episode.get("status") or ""),))
        self.report_label.configure(
            text=f"Отчёт сериала: {result.get('report') or '—'}")
        if result.get("dir"):
            self.last_dir = Path(str(result["dir"]))
        self.open_dir_btn.configure(
            state="normal" if self.last_dir else "disabled")

    def set_busy(self, busy: bool) -> None:
        # create_btn is re-enabled by App._refresh_gate_buttons when idle
        self.create_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal" if busy else "disabled")


class LibraryScreen(Screen):
    THUMB_W, THUMB_H = 176, 122

    def build(self) -> None:
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", pady=(0, 10))
        self.count_label = tk.Label(top, text="Библиотека фильмов", bg=BG,
                                    fg=MUT, font=fnt(10))
        self.count_label.pack(side="left")
        self.refresh_btn = ttk.Button(top, text="Обновить",
                                      command=self.app.refresh_library)
        self.refresh_btn.pack(side="right")
        Tooltip(self.refresh_btn, lambda: "" if self.app.busy else
                "Пересканировать output/")

        holder = tk.Frame(self, bg=BG)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.thumbs: list[object] = []

    def _on_inner_configure(self, _e: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, e: tk.Event) -> None:
        self.canvas.itemconfigure("all", width=e.width)

    def _on_wheel(self, e: tk.Event) -> None:
        if self.winfo_ismapped():
            self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def refresh(self, films: list[dict]) -> None:
        for child in self.inner.winfo_children():
            child.destroy()
        self.thumbs.clear()
        if not films:
            tk.Label(self.inner,
                     text="Фильмов пока нет. Создайте первый на вкладке «Фильм».",
                     bg=BG, fg=MUT, font=fnt(11)).pack(pady=48)
        for film in films:
            self._add_card(film)
        self.count_label.configure(
            text=f"Библиотека: {len(films)} "
                 f"{'фильм' if len(films) % 10 == 1 and len(films) != 11 else 'фильмов'}")

    def _add_card(self, film: dict) -> None:
        app = self.app
        row = tk.Frame(self.inner, bg=CARD, highlightbackground=EDGE,
                       highlightthickness=1)
        row.pack(fill="x", padx=2, pady=4)

        thumb = self._load_thumb(film.get("contact_sheet"))
        tk.Label(row, image=thumb, bg=CARD).pack(side="left", padx=10,
                                                 pady=10)
        self.thumbs.append(thumb)

        info = tk.Frame(row, bg=CARD)
        info.pack(side="left", fill="both", expand=True, padx=(0, 10),
                  pady=10)
        title = tk.Label(info, text=str(film.get("title") or film["path"].name),
                         bg=CARD, fg=TEXT, font=fnt(11, True), anchor="w",
                         wraplength=560, justify="left")
        title.pack(fill="x")
        status = str(film.get("status") or "?")
        tk.Label(info, text=status, bg=status_color(status),
                 fg="#141712", font=fnt(9, True), padx=8, pady=2).pack(
            anchor="w", pady=(6, 2))
        meta = f"Папка: {film['path']}"
        if film.get("duration"):
            meta = f"Длительность: {fmt_duration(film['duration'])}   " + meta
        tk.Label(info, text=meta, bg=CARD, fg=MUT, font=fnt(9),
                 anchor="w", wraplength=560, justify="left").pack(fill="x")

        btns = tk.Frame(row, bg=CARD)
        btns.pack(side="right", padx=10, pady=10)
        ttk.Button(btns, text="Открыть папку",
                   command=lambda: app.open_path(film["path"])).pack(
            fill="x", pady=(0, 6))
        video_btn = ttk.Button(btns, text="Открыть видео",
                               command=lambda: app.open_path(film.get("video")))
        video_btn.pack(fill="x")
        if not film.get("video"):
            video_btn.configure(state="disabled")

    def _load_thumb(self, path: Path | None) -> tk.PhotoImage:
        if path is not None and PILImage is not None and ImageTk is not None:
            try:
                img = PILImage.open(path).convert("RGB")
                img.thumbnail((self.THUMB_W, self.THUMB_H))
                return ImageTk.PhotoImage(img)  # type: ignore[return-value]
            except Exception:  # noqa: BLE001
                pass
        ph = tk.PhotoImage(width=self.THUMB_W, height=self.THUMB_H)
        ph.put(("#232529",), to=(0, 0, self.THUMB_W, self.THUMB_H))
        return ph

    def set_busy(self, busy: bool) -> None:
        self.refresh_btn.configure(state="disabled" if busy else "normal")


class SettingsScreen(Screen):
    def build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = tk.Frame(self, bg=BG)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)

        music_outer, music = card(top, "Фоновая музыка")
        music_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.music = tk.BooleanVar(value=gui_worker.read_config_music_flag())
        ttk.Checkbutton(music, text="Включена (config [audio] music)",
                        variable=self.music).pack(anchor="w")
        tk.Label(music, text=(
            "Флаг применяется ко всем новым фильмам (процедурная подложка\n"
            "в blocky-сценах). Обновление TOML точечное: меняется только\n"
            "строка music= под [audio], остальные ключи и комментарии\n"
            "сохраняются как есть."
        ), bg=CARD, fg=MUT, font=fnt(9), justify="left").pack(
            anchor="w", pady=(6, 8))
        ttk.Button(music, text="Сохранить в config.toml",
                   command=self.save_music).pack(anchor="w")

        env_outer, env = card(top, "Пути и окружение")
        env_outer.grid(row=0, column=1, sticky="nsew")
        self.paths = tk.Label(env, text="Загрузка…", bg=CARD, fg=MUT,
                              font=fnt(9), justify="left", anchor="w")
        self.paths.pack(fill="x")
        ttk.Button(env, text="Проверить окружение",
                   command=self.app.start_doctor).pack(anchor="w", pady=(8, 0))
        Tooltip(self.paths, lambda: str(self.paths["text"]))

        log_outer, logc = card(self, "Журнал настроек")
        log_outer.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.log = make_log_view(logc, height=8)
        self.log.pack(fill="both", expand=True)
        self.app.register_log(self.log)

    def save_music(self) -> None:
        ok = gui_worker.set_config_music_flag(
            gui_worker.CONFIG_PATH, self.music.get())
        if ok:
            self.app.log(
                f"config.toml обновлён: [audio] music = "
                f"{'true' if self.music.get() else 'false'}")
            self.app.set_status("Настройки сохранены")
        else:
            self.app.log("Не удалось записать config.toml — проверьте права.")

    def show_paths(self, info: dict) -> None:
        lines = [
            f"Blender:   {info.get('blender') or 'не найден'}",
            f"FFmpeg:    {info.get('ffmpeg') or 'не найден'}",
            f"Модели Piper:  {info.get('models_dir') or gui_worker.ROOT / 'models' / 'piper'}",
            f"Папка output:  {info.get('output_dir') or gui_worker.OUTPUT_DIR}",
            f"Конфиг:    {info.get('config_path') or gui_worker.CONFIG_PATH}",
            f"Журналы:   {info.get('logs_dir') or gui_worker.ROOT / 'logs'}",
        ]
        self.paths.configure(text="\n".join(lines))


class DiagnosticsScreen(Screen):
    def build(self) -> None:
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", pady=(0, 10))
        ttk.Button(top, text="Проверить окружение",
                   command=self.app.start_doctor).pack(side="left")
        ttk.Button(top, text="Открыть журнал",
                   command=lambda: self.app.open_path(self.logs_dir)
                   ).pack(side="left", padx=6)
        self.summary = tk.Label(top, text="Диагностика ещё не запускалась.",
                                bg=BG, fg=MUT, font=fnt(10))
        self.summary.pack(side="left", padx=12)

        holder = tk.Frame(self, bg=BG)
        holder.pack(fill="both", expand=True)
        cols = ("component", "status", "detail")
        self.table = ttk.Treeview(holder, columns=cols, show="headings")
        for col, text, width, anchor in (
            ("component", "Компонент", 180, "w"),
            ("status", "Статус", 90, "center"),
            ("detail", "Детали", 620, "w"),
        ):
            self.table.heading(col, text=text)
            self.table.column(col, width=width, anchor=anchor,
                              stretch=(col == "detail"))
        self.table.tag_configure("ok", foreground=OK)
        self.table.tag_configure("warn", foreground=WARN)
        self.table.tag_configure("fail", foreground=ERR)
        bar = ttk.Scrollbar(holder, orient="vertical",
                            command=self.table.yview)
        self.table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.table.pack(side="left", fill="both", expand=True)
        self.logs_dir: Path | None = gui_worker.ROOT / "logs"

    def add_check(self, check: dict) -> None:
        status = str(check.get("status") or "?")
        self.table.insert("", "end", tags=(status,), values=(
            check.get("name") or "—", status.upper(),
            str(check.get("detail") or "")))
        if status == "fail" and check.get("fix"):
            self.table.insert("", "end", tags=("warn",), values=(
                "", "", f"→ {check['fix']}"))

    def finish(self, info: dict) -> None:
        ok = bool(info.get("ok"))
        self.summary.configure(
            text=f"Итог: {'готово' if ok else 'есть проблемы'}   "
                 f"(ошибок: {info.get('failures', '?')}, "
                 f"предупреждений: {info.get('warnings', '?')})",
            fg=OK if ok else WARN)
        if info.get("logs_dir"):
            self.logs_dir = Path(str(info["logs_dir"]))


# --------------------------------------------------------------------- app
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
NAV_ITEMS = (
    ("film", "Фильм"),
    ("series", "Сериалы"),
    ("library", "Библиотека"),
    ("settings", "Настройки"),
    ("diag", "Диагностика"),
)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        _register_fonts()
        self.title(APP_TITLE)
        self.geometry("1280x800")
        self.minsize(1120, 720)
        self.configure(bg=BG)
        apply_theme(self)

        self.queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.cancel = threading.Event()
        self.busy = False
        self.env: dict = {}
        self.log_views: list[tk.Text] = []
        self.screens: dict[str, Screen] = {}
        self.library_screen: LibraryScreen | None = None
        self.settings_screen: SettingsScreen | None = None
        self.diag_screen: DiagnosticsScreen | None = None
        self._spinner_after: str | None = None
        self._spinner_i = 0

        self._build_header()
        self._build_body()
        self._build_statusbar()
        self.after(80, self._drain_queue)
        self.after(250, self._start_env_check)
        self.after(1400, self.refresh_library)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------- header
    def _build_header(self) -> None:
        header = tk.Frame(self, bg=PANEL, highlightbackground=EDGE,
                          highlightthickness=0)
        header.pack(fill="x")
        logo = tk.Frame(header, bg=PANEL)
        logo.pack(side="left", padx=18, pady=12)
        row = tk.Frame(logo, bg=PANEL)
        row.pack(anchor="w")
        tk.Label(row, text="ShortsFactory", bg=PANEL, fg=ACCENT,
                 font=(FONT_FAMILY, 16, "bold")).pack(side="left")
        tk.Label(row, text="Studio", bg=PANEL, fg=CYAN,
                 font=(FONT_FAMILY, 16, "bold")).pack(side="left", padx=(6, 0))
        tk.Label(row, text=f"v{VERSION}", bg=PANEL, fg=MUT,
                 font=fnt(10)).pack(side="left", padx=(10, 0), pady=(4, 0))
        tk.Label(logo, text="Локальная фабрика блочных мультфильмов",
                 bg=PANEL, fg=MUT, font=fnt(9)).pack(anchor="w")

        envbox = tk.Frame(header, bg=PANEL)
        envbox.pack(side="right", padx=18)
        self.env_dots: dict[str, tuple[tk.Canvas, tk.Label]] = {}
        for key, label in (("blender", "Blender"), ("ffmpeg", "FFmpeg"),
                           ("ollama", "Ollama")):
            box = tk.Frame(envbox, bg=PANEL)
            box.pack(side="left", padx=(14, 0))
            dot = tk.Canvas(box, width=12, height=12, bg=PANEL,
                            highlightthickness=0)
            dot.pack(side="left")
            lbl = tk.Label(box, text=label, bg=PANEL, fg=MUT, font=fnt(9))
            lbl.pack(side="left", padx=(5, 0))
            paint_dot(dot, "#3a3d45")
            self.env_dots[key] = (dot, lbl)
            Tooltip(dot, lambda k=key: self._env_tip(k))
            Tooltip(lbl, lambda k=key: self._env_tip(k))

    def _env_tip(self, key: str) -> str:
        value = self.env.get(key)
        if key == "ollama":
            return "Ollama: " + ("запущена (127.0.0.1:11434)" if value
                                 else "не отвечает — будет fallback-сценарий")
        name = "Blender" if key == "blender" else "FFmpeg"
        return f"{name}: {value or 'не найден'}"

    # --------------------------------------------------------------- body
    def _build_body(self) -> None:
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=12)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        sidebar = tk.Frame(body, bg=PANEL, width=190,
                           highlightbackground=EDGE, highlightthickness=1)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="НАВИГАЦИЯ", bg=PANEL, fg=MUT,
                 font=(FONT_FAMILY, 9, "bold")).pack(
            anchor="w", padx=16, pady=(14, 8))
        self.nav_buttons: dict[str, tk.Canvas] = {}
        self.nav_labels: dict[str, tk.Label] = {}
        for key, label in NAV_ITEMS:
            item = tk.Frame(sidebar, bg=PANEL)
            item.pack(fill="x")
            bar = tk.Canvas(item, width=4, height=30, bg=PANEL,
                            highlightthickness=0)
            bar.pack(side="left", fill="y")
            lbl = tk.Label(item, text=label, bg=PANEL, fg=MUT, anchor="w",
                           font=(FONT_FAMILY, 11), padx=14, pady=7)
            lbl.pack(side="left", fill="x", expand=True)
            for w in (item, lbl):
                w.bind("<Button-1>", lambda e, k=key: self.show_screen(k))
            lbl.bind("<Enter>", lambda e, l=lbl: l.configure(fg=TEXT))
            lbl.bind("<Leave>", lambda e, k=key: self._nav_repaint(k))
            self.nav_buttons[key] = bar
            self.nav_labels[key] = lbl
        self._nav_repaint("film")

        container = tk.Frame(body, bg=BG)
        container.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        for key, cls in (("film", FilmScreen), ("series", SeriesScreen),
                         ("library", LibraryScreen),
                         ("settings", SettingsScreen),
                         ("diag", DiagnosticsScreen)):
            screen = cls(container, self)
            screen.grid(row=0, column=0, sticky="nsew")
            self.screens[key] = screen
        self.library_screen = self.screens["library"]  # type: ignore[assignment]
        self.settings_screen = self.screens["settings"]  # type: ignore[assignment]
        self.diag_screen = self.screens["diag"]  # type: ignore[assignment]
        self.screens["film"].tkraise()
        self.current = "film"

    def _nav_repaint(self, active: str) -> None:
        for key, lbl in self.nav_labels.items():
            is_active = key == active
            lbl.configure(
                bg="#32353d" if is_active else PANEL,
                fg=ACCENT if is_active else (TEXT if not self.busy else MUT),
                font=(FONT_FAMILY, 11, "bold" if is_active else "normal"))
        for key, bar in self.nav_buttons.items():
            bar.configure(bg=ACCENT if key == active else PANEL)

    def show_screen(self, key: str) -> None:
        self.screens[key].tkraise()
        self.current = key
        self._nav_repaint(key)
        if key == "library":
            self.refresh_library()

    # ------------------------------------------------------------ statusbar
    def _build_statusbar(self) -> None:
        bar = tk.Frame(self, bg=PANEL, highlightbackground=EDGE,
                       highlightthickness=0)
        bar.pack(fill="x", side="bottom")
        self.status = tk.StringVar(value="Готово")
        tk.Label(bar, textvariable=self.status, bg=PANEL, fg=MUT,
                 font=fnt(9), anchor="w", padx=16, pady=6).pack(
            side="left", fill="x", expand=True)
        self.spinner_label = tk.Label(bar, text="", bg=PANEL, fg=ACCENT,
                                      font=fnt(10))
        self.spinner_label.pack(side="right", padx=16)

    def _animate_spinner(self) -> None:
        if not self.busy:
            self.spinner_label.configure(text="")
            self._spinner_after = None
            return
        self.spinner_label.configure(text=SPINNER[self._spinner_i])
        self._spinner_i = (self._spinner_i + 1) % len(SPINNER)
        self._spinner_after = self.after(90, self._animate_spinner)

    # ------------------------------------------------------------- queue
    def register_log(self, view: tk.Text) -> None:
        self.log_views.append(view)

    def log(self, msg: str) -> None:
        self.queue.put(("log", msg))

    def set_status(self, msg: str) -> None:
        self.queue.put(("status", msg))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                handler = getattr(self, f"_on_{kind}", None)
                if handler is None:
                    continue
                try:
                    handler(payload)
                except Exception as exc:  # noqa: BLE001 - one bad event
                    # must never stop the drain loop
                    self._append_log(
                        f"Ошибка обработки события {kind}: "
                        f"{type(exc).__name__}: {exc}")
        except queue.Empty:
            pass
        self.after(80, self._drain_queue)

    def _append_log(self, msg: str) -> None:
        line = msg.rstrip("\n")
        for view in self.log_views:
            view.configure(state="normal")
            view.insert("end", line + "\n")
            if view.winfo_ismapped():
                view.see("end")
            view.configure(state="disabled")

    def _on_log(self, msg: object) -> None:
        self._append_log(str(msg))

    def _on_status(self, msg: object) -> None:
        self.status.set(str(msg))

    def _on_busy(self, busy: object) -> None:
        self.busy = bool(busy)
        for screen in self.screens.values():
            if hasattr(screen, "set_busy"):
                screen.set_busy(self.busy)
        self._refresh_gate_buttons()
        if self.busy and self._spinner_after is None:
            self._spinner_after = self.after(90, self._animate_spinner)
        self._nav_repaint(self.current)

    def _on_env(self, env: object) -> None:
        self.env = dict(env)  # type: ignore[arg-type]
        colors = {"blender": "#3a3d45", "ffmpeg": "#3a3d45",
                  "ollama": "#3a3d45"}
        if self.env.get("blender"):
            colors["blender"] = OK
        if self.env.get("ffmpeg"):
            colors["ffmpeg"] = OK
        colors["ollama"] = OK if self.env.get("ollama") else MUT
        for key, (dot, lbl) in self.env_dots.items():
            paint_dot(dot, colors[key])
        self._refresh_gate_buttons()

    def _refresh_gate_buttons(self) -> None:
        ready = bool(self.env.get("blender")) and bool(self.env.get("ffmpeg"))
        if self.busy:
            return
        for screen in self.screens.values():
            btn = getattr(screen, "create_btn", None)
            if btn is not None:
                btn.configure(state="normal" if ready else "disabled")

    def pipeline_block_reason(self) -> str:
        if self.busy:
            return "Задание уже выполняется."
        missing = [name for name, key in (("Blender", "blender"),
                                          ("FFmpeg", "ffmpeg"))
                   if not self.env.get(key)]
        if missing:
            return ("Недоступно: " + ", ".join(missing) +
                    ". Рендер невозможен — установите компонент и "
                    "перезапустите приложение.")
        return ""

    # ------------------------------------------------------------ threading
    def run_worker(self, fn) -> None:
        def wrapper() -> None:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - GUI must not crash
                self.log(f"Внутренняя ошибка: {type(exc).__name__}: {exc}")
                self.set_status("Ошибка")
            finally:
                self.queue.put(("busy", False))

        threading.Thread(target=wrapper, daemon=True).start()

    def _start_env_check(self) -> None:
        self.run_worker(self._worker_env)

    def _worker_env(self) -> None:
        blender = shutil.which("blender")
        if not blender:
            base = Path("C:/Program Files/Blender Foundation")
            try:
                if base.exists():
                    blender = next((str(p) for p in base.glob("*/blender.exe")
                                    if p.exists()), None)
            except OSError:
                blender = None
        try:
            s = socket.create_connection(("127.0.0.1", 11434), timeout=0.8)
            s.close()
            ollama = True
        except OSError:
            ollama = False
        self.env = {"blender": blender, "ffmpeg": shutil.which("ffmpeg"),
                    "ollama": ollama}
        self.queue.put(("env", self.env))
        self.set_status("Окружение проверено")
        if not self.env["blender"] or not self.env["ffmpeg"]:
            self.log("Внимание: без " + ", ".join(
                name for name, key in (("Blender", "blender"),
                                       ("FFmpeg", "ffmpeg"))
                if not self.env[key]) + " производство фильмов отключено.")

    def refresh_library(self) -> None:
        if self.busy:
            return
        self.run_worker(self._worker_library)

    def _worker_library(self) -> None:
        films = gui_worker.scan_film_library(gui_worker.OUTPUT_DIR)
        self.queue.put(("library", films))
        self.set_status(f"Библиотека: найдено {len(films)}")

    def _on_library(self, films: object) -> None:
        if self.library_screen is not None:
            self.library_screen.refresh(list(films))  # type: ignore[arg-type]

    def run_pipeline(self, args: list[str], *, job: str,
                     music_flag: bool) -> None:
        if self.busy:
            self.log("Уже выполняется задание — дождитесь завершения.")
            return
        if not (self.env.get("blender") and self.env.get("ffmpeg")):
            self.log(self.pipeline_block_reason())
            return
        self.cancel.clear()
        self.queue.put(("busy", True))
        self.set_status("Подготовка…")
        self.run_worker(lambda: self._worker_pipeline(args, job, music_flag))

    def _worker_pipeline(self, args: list[str], job: str,
                         music_flag: bool) -> None:
        if not gui_worker.set_config_music_flag(gui_worker.CONFIG_PATH,
                                                music_flag):
            self.log("Не удалось обновить [audio] music в config.toml — "
                     "фильм запускается с текущим флагом конфига.")
        cmd = [_python_executable(), str(GUI_WORKER), *args]
        self.log("Запуск: " + " ".join(args))
        self.proc = None
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1)
        except OSError as exc:
            self.log(f"Не удалось запустить worker: {exc!r}")
            self.set_status("Ошибка запуска")
            return
        self.proc = proc
        result: dict | None = None
        for line in proc.stdout or []:
            if self.cancel.is_set():
                proc.kill()
                self.log("Отменено пользователем.")
                self.set_status("Отменено")
                self.proc = None
                return
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("##STAGE## "):
                msg = line[len("##STAGE## "):]
                self.set_status(msg)
                self.log(f"— {msg}")
            elif line.startswith("##TOKEN## "):
                self.log(f"[сториборд] {line[len('##TOKEN## '):]}")
            elif line.startswith("##RESULT## "):
                try:
                    result = json.loads(line[len("##RESULT## "):])
                except ValueError:
                    self.log("Не удалось разобрать RESULT.")
            elif line.startswith("##ERROR## "):
                self.log("ОШИБКА: " + line[len("##ERROR## "):])
            else:
                self.log(line)
        rc = proc.wait()
        self.proc = None
        if result is not None:
            kind = str(result.get("kind"))
            self.queue.put((f"{kind}_result", result))
            self.set_status("Готово")
            self.queue.put(("log", f"Процесс завершён (код {rc})."))
            self.refresh_library()
        elif not self.cancel.is_set():
            self.log(f"Процесс завершён без результата (код {rc}).")
            self.set_status("Ошибка")

    def _on_film_result(self, result: object) -> None:
        data = dict(result)  # type: ignore[arg-type]
        self.screens["film"].show_result(data.get("report") or {})  # type: ignore[union-attr]

    def _on_series_result(self, result: object) -> None:
        self.screens["series"].show_result(dict(result))  # type: ignore[arg-type]

    def cancel_job(self) -> None:
        if not self.busy:
            return
        self.cancel.set()
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        self.set_status("Отменено")

    # -------------------------------------------------------------- doctor
    def start_doctor(self) -> None:
        if self.busy:
            self.log("Дождитесь завершения текущего задания.")
            return
        self.queue.put(("busy", True))
        self.set_status("Диагностика окружения…")
        if self.diag_screen is not None:
            self.diag_screen.table.delete(*self.diag_screen.table.get_children())
        self.run_worker(self._worker_doctor)

    def _worker_doctor(self) -> None:
        cmd = [_python_executable(), str(GUI_WORKER), "doctor"]
        info: dict = {}
        for kind, payload in self._stream_lines(cmd):
            if kind == "check":
                self.queue.put(("doctor_check", payload))
            elif kind == "result":
                info = payload  # type: ignore[assignment]
            elif kind == "line":
                self.log(str(payload))
            elif kind == "error":
                self.log("ОШИБКА: " + str(payload))
        self.queue.put(("doctor_done", info))

    def _stream_lines(self, cmd: list[str]):
        """Yield (kind, payload) from a gui_worker subprocess."""
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1)
        except OSError as exc:
            yield "error", f"Не удалось запустить worker: {exc!r}"
            return
        self.proc = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            if self.cancel.is_set():
                proc.kill()
                yield "error", "Отменено пользователем."
                self.proc = None
                return
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("##CHECK## "):
                try:
                    yield "check", json.loads(line[len("##CHECK## "):])
                except ValueError:
                    yield "line", line
            elif line.startswith("##RESULT## "):
                try:
                    yield "result", json.loads(line[len("##RESULT## "):])
                except ValueError:
                    yield "line", line
            elif line.startswith("##ERROR## "):
                yield "error", line[len("##ERROR## "):]
            else:
                yield "line", line
        rc = proc.wait()
        self.proc = None
        if rc not in (0, 1):
            yield "error", f"worker завершился с кодом {rc}"

    def _on_doctor_check(self, check: object) -> None:
        if self.diag_screen is not None:
            self.diag_screen.add_check(dict(check))  # type: ignore[arg-type]
        data = dict(check)  # type: ignore[arg-type]
        mark = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}.get(
            str(data.get("status")), str(data.get("status")))
        self._append_log(f"[{mark}] {data.get('name')}: {data.get('detail')}")

    def _on_doctor_done(self, info: object) -> None:
        data = dict(info)  # type: ignore[arg-type]
        if self.diag_screen is not None:
            self.diag_screen.finish(data)
        if self.settings_screen is not None:
            self.settings_screen.show_paths({**self.env, **data})
        self.set_status("Диагностика завершена")

    # ------------------------------------------------------------ utilities
    def open_path(self, path: Path | str | None) -> None:
        if not path:
            self.log("Путь пустой — открывать нечего.")
            return
        target = Path(path)
        try:
            if target.is_dir():
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif target.exists():
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                os.startfile(str(target.parent))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            self.log(f"Не удалось открыть {target}: {exc!r}")

    def show_contact_sheet(self, path: Path) -> None:
        if PILImage is None or ImageTk is None:
            self.log("Pillow недоступен — открываю файл напрямую.")
            self.open_path(path)
            return
        try:
            img = PILImage.open(path).convert("RGB")
        except Exception as exc:  # noqa: BLE001
            self.log(f"Контактный лист не читается: {exc!r}")
            return
        win = tk.Toplevel(self)
        win.title(f"Контактный лист — {path.parent.name}")
        win.configure(bg=BG)
        img.thumbnail((900, 640))
        photo = ImageTk.PhotoImage(img)
        tk.Label(win, image=photo, bg=BG).pack(padx=14, pady=14)
        tk.Label(win, text=str(path), bg=BG, fg=MUT, font=fnt(9)).pack(
            pady=(0, 10))
        win._photo = photo  # keep a reference
        win.transient(self)

    def _on_close(self) -> None:
        if self.busy:
            if not messagebox.askyesno(
                    APP_TITLE,
                    "Задание ещё выполняется. Прервать его и закрыть окно?"):
                return
            self.cancel.set()
            if self.proc is not None:
                try:
                    self.proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
        self.destroy()


# --------------------------------------------------------------------- main
def _single_instance_lock() -> socket.socket | None:
    """Return a bound socket if this is the only instance, else None."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47653))
        s.listen(1)
        return s
    except OSError:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass
        return None


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    if "--version" in argv:
        print(VERSION)
        return 0
    if "--smoke" in argv:
        # headless-ish guard: build the whole UI, pump events, tear down
        app = App()
        app.update_idletasks()
        app.update()
        names = sorted(app.screens)
        app.destroy()
        print("SMOKE OK screens=" + ",".join(names))
        return 0
    _lock = _single_instance_lock()
    if _lock is None:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None, "Приложение уже запущено.", APP_TITLE, 0x40)
        except Exception:  # noqa: BLE001
            pass
        return 0
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
