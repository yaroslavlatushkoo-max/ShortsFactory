from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


class CommandError(RuntimeError):
    def __init__(self, command: Sequence[str], returncode: int, stdout: str, stderr: str) -> None:
        self.command = list(command)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"Команда завершилась с кодом {returncode}: {' '.join(map(str, command))}\n{stderr[-4000:]}"
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_match(text: str) -> str:
    text = clean_text(text).lower().replace("ё", "е")
    text = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def slugify(text: str, max_len: int = 42) -> str:
    text = normalize_match(text)
    out: list[str] = []
    for char in text:
        if char in TRANSLIT:
            out.append(TRANSLIT[char])
        elif char.isascii() and (char.isalnum() or char in "- "):
            out.append(char)
        elif char.isspace() or char in "_-":
            out.append("-")
    slug = "".join(out)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        slug = "shorts"
    return slug[:max_len].strip("-") or "shorts"


def project_id(topic: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{slugify(topic, 36)}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return default


def word_count(text: str) -> int:
    return len(re.findall(r"[\w’'-]+", text, flags=re.UNICODE))


def sentence_split(text: str) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    # A sentence normally starts with a capital letter or a digit. This avoids
    # treating abbreviations such as «н. э.» or «т. д.» as sentence boundaries.
    parts = re.split(r"(?<=[.!?…])\s+(?=[A-ZА-ЯЁ«\"(0-9])", text)
    result: list[str] = []
    for part in parts:
        part = part.strip()
        if part:
            result.append(part)
    return result


def truncate_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]).rstrip(",.;:") + "…"


def extract_numbers(text: str) -> set[str]:
    values: set[str] = set()
    for match in re.finditer(
        r"(?<![\w])(\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)*)(\s?%|°)?(?!\d)",
        text,
    ):
        number = match.group(1).replace(" ", "").replace("\u00a0", "")
        # A dot or comma before a full three-digit group is a thousands separator, not a
        # decimal point: "11,034", "11.034" and "11 034" normalize to "11034". This also
        # handles mixed forms such as "1,071.8" -> "1071.8".
        number = re.sub(r"(?<=\d)[.,](?=\d{3}(?:[.,]|$))", "", number)
        number = number.replace(",", ".")
        if match.group(2):
            number += match.group(2).strip()
        values.add(number)
    return values


_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_ROMAN_TOKEN = re.compile(r"\b([IVXLCDM]{1,7})\b")
_ROMAN_YEAR_MARK = re.compile(
    r"^\s*(?:в\.|в\b|век|г\.|год|centur|a\.?d\.?|b\.?c\.?|ce\b|st\b|nd\b|rd\b|th\b)",
    flags=re.IGNORECASE,
)


def _roman_to_int(token: str) -> int:
    total = 0
    previous = 0
    for char in reversed(token):
        value = _ROMAN_VALUES[char]
        if value < previous:
            total -= value
        else:
            total += value
            previous = value
    return total


def roman_numbers(text: str) -> set[str]:
    """Arabic equivalents of Roman numerals that denote centuries/years in the text.

    Sources often write "V век" or "II век", while the model writes "5 век" or
    "2nd century"; extracting both sides as digits prevents false "invented number" errors.
    """
    values: set[str] = set()
    for match in _ROMAN_TOKEN.finditer(text):
        token = match.group(1)
        following = text[match.end(): match.end() + 12]
        # Only treat a Roman token as a number when it is followed by a century/year
        # marker. Otherwise uppercase words such as "MIX" become false numbers.
        if not _ROMAN_YEAR_MARK.match(following):
            continue
        values.add(str(_roman_to_int(token)))
    return values


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return clean_text(html.unescape(text))


def is_cyrillic(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if "\u0400" <= c <= "\u04ff") / len(letters) > 0.5


def jaccard(a: str, b: str) -> float:
    aw = {w for w in normalize_match(a).split() if len(w) > 2}
    bw = {w for w in normalize_match(b).split() if len(w) > 2}
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / len(aw | bw)


def content_tokens(text: str) -> set[str]:
    stop_ru = {
        "это", "как", "что", "для", "или", "его", "она", "они", "был", "была",
        "было", "были", "при", "над", "под", "без", "через", "после", "перед",
        "также", "только", "очень", "который", "которая", "которые", "может",
        "можно", "есть", "нет", "the", "and", "for", "with", "that", "this",
        "from", "were", "was", "are", "is", "has", "have", "not", "but", "its",
    }
    tokens = {w for w in normalize_match(text).split() if len(w) > 3 and w not in stop_ru}
    return {w[:5] for w in tokens}


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"


def which(name: str) -> str | None:
    return shutil.which(name)


def ffmpeg_bin() -> str:
    value = (
        os.environ.get("SHORTSFACTORY_FFMPEG")
        or os.environ.get("FFMPEG_BIN")
        or which("ffmpeg")
    )
    if not value:
        raise FileNotFoundError("FFmpeg не найден в PATH. Установите FFmpeg или задайте FFMPEG_BIN/SHORTSFACTORY_FFMPEG.")
    return value


def ffprobe_bin() -> str:
    value = (
        os.environ.get("SHORTSFACTORY_FFPROBE")
        or os.environ.get("FFPROBE_BIN")
        or which("ffprobe")
    )
    if not value:
        raise FileNotFoundError("ffprobe не найден в PATH. Установите FFmpeg или задайте FFPROBE_BIN/SHORTSFACTORY_FFPROBE.")
    return value


def run(
    command: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    timeout: float = 900,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    cmd = [str(part) for part in command]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandError(cmd, -1, "", f"Таймаут {timeout:.0f} с") from exc
    if check and proc.returncode != 0:
        raise CommandError(cmd, proc.returncode, proc.stdout or "", proc.stderr or "")
    return proc


def ffprobe_json(path: Path) -> dict[str, Any]:
    proc = run(
        [ffprobe_bin(), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        timeout=120,
    )
    return json.loads(proc.stdout or "{}")


def open_path(path: Path) -> bool:
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except OSError:
        return False


def dhash(path: Path, size: int = 8) -> int:
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
        pixels = list(img.getdata())
    value = 0
    for row in range(size):
        for col in range(size):
            left = pixels[row * (size + 1) + col]
            right = pixels[row * (size + 1) + col + 1]
            value = (value << 1) | (1 if left > right else 0)
    return value


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def safe_filename(text: str, max_len: int = 80) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text or "")
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:max_len].strip() or "short"


def chunks(items: Sequence[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield list(items[index:index + size])


def unique_preserve(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item.strip())
    return result


def relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
