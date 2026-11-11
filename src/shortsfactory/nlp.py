from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .config import Config
from .topics import detect_profile, is_generic_topic, normalize_topic, suggestions


NUMBER_WORDS = {
    "один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
    "девять": 9, "десять": 10,
}


@dataclass
class Intent:
    action: str = "create"
    count: int = 1
    topic: str = ""
    duration: float = 40.0
    duration_range: tuple[float, float] | None = None
    languages: list[str] = field(default_factory=lambda: ["ru", "en"])
    profile: str = ""
    style: str = "auto"
    pace: str = "medium"
    visual_density: str = "medium"
    script_path: str = ""
    remake_overrides: dict[str, str] = field(default_factory=dict)
    confidence: float = 1.0
    notes: list[str] = field(default_factory=list)
    raw: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["duration_range"] = list(self.duration_range) if self.duration_range else None
        return data

    @property
    def needs_clarification(self) -> bool:
        if self.action not in {"create", "batch", "remake"}:
            return False
        if self.action == "remake":
            return False
        return not self.topic and not self.script_path

    def plan_text(self) -> str:
        lang = "+".join(item.upper() for item in self.languages)
        topic = self.topic or "автовыбор темы"
        duration = (
            f"{self.duration_range[0]:g}–{self.duration_range[1]:g} с"
            if self.duration_range
            else f"{self.duration:g} с"
        )
        return (
            f"Действие: {self.action}; тема: {topic}; роликов: {self.count}; "
            f"длительность: {duration}; языки: {lang}; профиль: {self.profile or 'авто'}; "
            f"темп: {self.pace}; визуал: {self.visual_density}"
        )


def _extract_duration(text: str) -> tuple[float, tuple[float, float] | None, str]:
    lowered = text.lower().replace("–", "-").replace("—", "-")
    range_match = re.search(
        r"(\d{1,3})\s*-\s*(\d{1,3})\s*(?:сек|секунд|с\b|seconds?|sec\b)",
        lowered,
    )
    if range_match:
        low, high = sorted((float(range_match.group(1)), float(range_match.group(2))))
        return (low + high) / 2, (low, high), text
    seconds = re.search(r"(\d{1,3})\s*(?:сек|секунд|seconds?|sec\b|с\b)", lowered)
    if seconds:
        value = float(seconds.group(1))
        return value, None, text
    minutes = re.search(r"(\d{1,2})\s*(?:мин|минут|minutes?|min\b)", lowered)
    if minutes:
        value = float(minutes.group(1)) * 60
        return value, None, text
    # "длительность 40" without a unit
    bare = re.search(r"(?:длительность|duration|хронометраж)\D{0,8}(\d{1,3})", lowered)
    if bare:
        return float(bare.group(1)), None, text
    return 40.0, None, text


def _extract_count(text: str) -> int:
    lowered = text.lower()
    match = re.search(
        r"(\d{1,2})\s+(?:[а-яa-z\-]+\s+){0,3}(?:shorts?|шорт[а-я]*|ролик[а-я]*|видео|video)",
        lowered,
    )
    if match:
        return max(1, min(20, int(match.group(1))))
    word_match = re.search(
        r"\b(" + "|".join(NUMBER_WORDS) + r")\s+(?:[а-я]+\s+){0,2}(?:shorts?|шорт[а-я]*|ролик[а-я]*|видео)",
        lowered,
    )
    if word_match:
        return max(1, min(20, NUMBER_WORDS[word_match.group(1)]))
    return 1


def _extract_languages(text: str) -> list[str]:
    lowered = text.lower()
    both_markers = [
        "русск" in lowered and "англ" in lowered,
        bool(re.search(r"\bru\b.*\ben\b|\ben\b.*\bru\b", lowered)),
        "двух язык" in lowered or "оба язык" in lowered or "both languages" in lowered,
        "на двух языках" in lowered,
    ]
    if any(both_markers):
        return ["ru", "en"]
    if re.search(r"только рус|на русском|русскоязыч|russian only|только ru", lowered):
        return ["ru"]
    if re.search(r"только англ|на английском|english only|только en", lowered):
        return ["en"]
    return ["ru", "en"]


def _extract_topic(text: str, config: Config) -> tuple[str, str]:
    original = text.strip()
    patterns = [
        r"(?:на тему|по теме|о теме)\s*[:—-]?\s*[«\"']?([^»\"'.;,\n]+)",
        r"(?:о|об|про|about)\s+[«\"']?([^»\"'.;,\n]+)",
        r"(?:тема|topic)\s*[:—-]\s*[«\"']?([^»\"'.;,\n]+)",
    ]
    candidate = ""
    for pattern in patterns:
        match = re.search(pattern, original, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1)
            break
    if not candidate:
        return "", ""
    # Stop at a clause that starts a new instruction.
    candidate = re.split(
        r"\s+(?:длительн|хронометраж|на\s+\d|язык|секунд|минут|профессиональн|динамичн|"
        r"сам\s+выбери|подготовь|сделай|используй|шорт|short|ролик|видео)\b",
        candidate,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    candidate = normalize_topic(candidate.strip(" ,.-—«»\"'"))
    if not candidate or is_generic_topic(candidate):
        return "", detect_profile(candidate or text, config, "")
    profile = detect_profile(candidate, config, "")
    return candidate, profile


def _extract_script_path(text: str) -> str:
    lowered = text.lower()
    if "входной папк" in lowered or "input" in lowered or "из файла" in lowered:
        quoted = re.search(r"[«\"']([^«»\"']+\.(?:md|txt|json))[»\"']", text, flags=re.IGNORECASE)
        if quoted:
            return quoted.group(1)
        return "input/script.md"
    return ""


def parse(text: str, config: Config) -> Intent:
    raw = text.strip()
    lowered = raw.lower().replace("ё", "е")
    intent = Intent(raw=raw)

    action_patterns: list[tuple[str, str]] = [
        ("doctor", r"\bdoctor\b|диагност|проверь систему|проверка системы"),
        ("status", r"\bstatus\b|статус|какие задания|что готово|что требует внимания|покажи.*(?:задани|очередь)"),
        ("report", r"\breport\b|отчет|отчёт|покажи.*отчет|покажи.*отчёт"),
        ("preview", r"\bpreview\b|превью|покажи.*(?:ролик|видео|результат)|открой.*(?:папку|ролик)"),
        ("inspect", r"\binspect\b|детали проекта|покажи.*проект"),
        ("resume", r"продолж|возобнов|восстанов|resume|продолжи производство"),
        ("retry", r"\bretry\b|проверь готовые|исправь|повтори.*(?:ошиб|проверк)|доделай"),
        ("remake", r"переделай|remake|пересобери|смени.*голос|другой голос|быстрее темп|больше графики"),
        ("create", r"создай|сделай|запусти|произведи|сгенерируй|\bcreate\b|сними"),
    ]
    for action, pattern in action_patterns:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            intent.action = action
            break
    else:
        intent.action = "create"
        intent.confidence = 0.45
        intent.notes.append("Действие не распознано явно; предполагаю создание ролика.")

    intent.count = _extract_count(raw)
    intent.duration, intent.duration_range, _ = _extract_duration(raw)
    intent.languages = _extract_languages(raw)
    topic, profile = _extract_topic(raw, config)
    intent.topic = topic
    intent.profile = profile
    intent.script_path = _extract_script_path(raw)

    if "только рус" in lowered or "russian only" in lowered:
        intent.languages = ["ru"]
    elif "только англ" in lowered or "english only" in lowered:
        intent.languages = ["en"]

    if any(word in lowered for word in ("динамичн", "быстр", "энергичн", "fast")):
        intent.pace = "fast"
    if any(word in lowered for word in ("спокойн", "размеренн", "calm")):
        intent.pace = "slow"
    if any(word in lowered for word in ("больше графики", "график", "анимац", "motion")):
        intent.visual_density = "high"
    if any(word in lowered for word in ("меньше графики", "минимум графики", "фото")):
        intent.visual_density = "low"
    if "документальн" in lowered:
        intent.style = "documentary"
    elif "научн" in lowered:
        intent.style = "scientific"
    elif "кинематограф" in lowered:
        intent.style = "cinematic"
    elif "историческ" in lowered:
        intent.style = "historical"

    if intent.action == "remake":
        if any(word in lowered for word in ("быстрее", "динамичн")):
            intent.remake_overrides["pace"] = "fast"
        if "другой голос" in lowered or "смени голос" in lowered:
            intent.remake_overrides["voice"] = "alternate"
        if "больше графики" in lowered:
            intent.remake_overrides["visual_density"] = "high"
        if "короче" in lowered:
            intent.duration = max(15.0, intent.duration - 5)
            intent.remake_overrides["duration"] = f"{intent.duration:g}"
        intent.count = 1

    if intent.action in {"create", "batch"} and intent.count > 1:
        intent.action = "batch"

    if intent.action in {"create", "batch"} and not intent.topic and not intent.script_path:
        intent.confidence = min(intent.confidence, 0.5)
        intent.notes.append("Тема не указана.")
    if intent.duration < 10 or intent.duration > 180:
        intent.notes.append("Длительность вне обычного диапазона 10–180 с; проверь параметр.")
        intent.confidence = min(intent.confidence, 0.6)
    if intent.profile:
        intent.profile = detect_profile(intent.profile, config, intent.profile)
    return intent


def clarification(intent: Intent, config: Config) -> str:
    lines = ["Не хватает темы для производства. Уточни, например:"]
    for value in suggestions(config, intent.profile or "science", 5):
        lines.append(f"  • {value}")
    if intent.script_path:
        lines.append(f"Или положи сценарий в {intent.script_path}.")
    lines.append('Пример: ask "Создай 2 Shorts о космосе, 40 секунд, RU и EN"')
    return "\n".join(lines)
