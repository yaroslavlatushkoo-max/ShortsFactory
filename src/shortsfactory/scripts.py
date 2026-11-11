from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .config import Config
from .llm import LLMError, OllamaClient
from .logging_setup import get_logger
from .models import Plan, Scene, Script, Source
from .util import (
    atomic_write_json,
    atomic_write_text,
    clean_text,
    content_tokens,
    extract_numbers,
    jaccard,
    now_iso,
    roman_numbers,
    sentence_split,
    word_count,
)


PROMPT_VERSION = "script-v5"
SCENE_TARGET = 5
MIN_SENTENCES = 4
MAX_SENTENCES = 7
# Caps used by timeline.py when it stretches scene pauses to reach the target duration.
MAX_SCENE_TAIL = 1.35
MAX_LAST_TAIL = 3.0
MAX_HOLD = 3.0
CLICKBAIT = re.compile(
    r"(?i)(шок|сенсац|вы не поверите|секрет, который|shocking|you won'?t believe|"
    r"unbelievable|must see|gone wrong|умопомрачител|скрывают|запрещ[её]нн)"
)
EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F1E0-\U0001F1FF]",
    flags=re.UNICODE,
)


_DATE_CAPTION = re.compile(
    r"^(?:(?:В|Во|К|С|Со)\s+)?[IVXLCDM\d]+(?:\s*(?:[–—-]|до)\s*[IVXLCDM\d]+)?"
    r"(?:\s*(?:век[а-яё]*|год[а-яё]*|гг\.))?"
    r"(?:\s*(?:до\s*н\.\s*э\.|н\.\s*э\.))?",
    flags=re.IGNORECASE,
)
_CAPTION_TAIL = {
    "а", "без", "в", "во", "для", "до", "за", "и", "из", "к", "на", "над",
    "о", "об", "от", "по", "под", "при", "с", "со", "у", "через", "это",
}


def _clip_caption(text: str, limit: int = 34) -> str:
    text = text.strip(" .,;:!?")
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return (cut or text[: limit - 1]).rstrip(" .,;:!?") + "…"


def _caption_from_text(text: str, lang: str) -> str:

    """Short readable caption without cutting a date/era phrase in half."""
    text = clean_text(text)
    if not text:
        return ""
    match = _DATE_CAPTION.match(text)
    if match:
        # Keep the final dot of the abbreviation "н. э."; it is not sentence punctuation.
        candidate = match.group(0).rstrip(" ,;:!?")
        words = candidate.split()
        has_marker = bool(re.search(r"(?i)век|год|гг\.|н\.\s*э\.", candidate))
        if len(words) >= 2 and (has_marker or len(extract_numbers(candidate)) >= 2):
            if len(candidate) <= 34:
                return candidate
    words = [word.strip(".,;:!?") for word in text.split()]
    if lang == "ru":
        while len(words) > 2 and words[-1].lower() in _CAPTION_TAIL:
            words.pop()
    candidate = " ".join(words[:5]).strip(" .,;:!?")
    return _clip_caption(candidate)


class ScriptError(RuntimeError):
    pass


class ScriptValidationError(ScriptError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def word_budget(config: Config, lang: str, duration: float, scene_count: int = SCENE_TARGET) -> tuple[int, int]:
    """Spoken-word range that still renders inside the target duration tolerance.

    The bounds are derived from the same scene-pause caps the timeline uses to stretch a
    short script and from the QC duration tolerance, so any accepted script yields a video
    within ``duration * (1 ± duration_tolerance)`` instead of falling back to extraction.
    """
# Words per second of synthesized narration, calibrated from the E2E runs
    # (RU 71 words / 24.6 s, EN 135 words / 41.5 s).
    rate = 2.885 if lang == "ru" else 3.26
    xfade = float(config.get("video", "transition", 0.4))
    lead = float(config.get("video", "lead_in", 0.15))
    tail = float(config.get("video", "scene_tail", 0.6))
    last_tail = float(config.get("video", "last_scene_tail", 1.4))
    tolerance = float(config.get("quality", "duration_tolerance", 0.12))
    count = max(1, scene_count)
    base_overhead = count * lead + (count - 1) * (tail - xfade) + last_tail
    max_stretch = (
        (count - 1) * (MAX_SCENE_TAIL - tail) + (MAX_LAST_TAIL - last_tail) + MAX_HOLD
    )
    # Keep a margin: the pause caps are theoretical maxima, and a script at the lower
    # bound must still clear the QC duration tolerance.
    min_seconds = max(6.0, duration * (1 - tolerance) - base_overhead - max_stretch + 1.2)
    max_seconds = max(8.0, duration * (1 + tolerance) - base_overhead)
    low = max(35, int(math.ceil(min_seconds * rate)))
    high = max(low + 8, int(math.floor(max_seconds * rate)))
    return low, high


def scene_word_targets(config: Config, lang: str, duration: float, scene_count: int = SCENE_TARGET) -> tuple[int, int]:
    """Per-scene word band that hits the exact target duration with no pause stretching.

    ``word_budget`` is the acceptance window (a script may be stretched up to the pause caps);
    this band is what the model is asked for, so its answer lands comfortably inside the window.
    """
    rate = 2.885 if lang == "ru" else 3.26
    xfade = float(config.get("video", "transition", 0.4))
    lead = float(config.get("video", "lead_in", 0.15))
    tail = float(config.get("video", "scene_tail", 0.6))
    last_tail = float(config.get("video", "last_scene_tail", 1.4))
    count = max(1, scene_count)
    base_overhead = count * lead + (count - 1) * (tail - xfade) + last_tail
    ideal = max(1.0, duration - base_overhead) * rate
    per = max(16, int(round(ideal / count)))
    return max(12, per - 2), per + 4


def _title_ok(title: str) -> list[str]:
    errors: list[str] = []
    title = title.strip()
    if not 8 <= len(title) <= 92:
        errors.append("Заголовок должен быть длиной 8–92 символа")
    if "\n" in title:
        errors.append("Заголовок не должен содержать перевод строки")
    if CLICKBAIT.search(title):
        errors.append("Заголовок содержит кликбейт/обещание сенсации")
    if title.count("!") > 1 or title.count("?") > 1:
        errors.append("Слишком много восклицательных/вопросительных знаков в заголовке")
    letters = [c for c in title if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.65:
        errors.append("Заголовок почти целиком в верхнем регистре")
    if EMOJI.search(title):
        errors.append("Эмодзи в заголовке не поддерживаются TTS/шрифтом")
    return errors


def validate_payload(
    payload: dict[str, Any],
    *,
    sources: list[Source],
    lang: str,
    plan: Plan,
    config: Config,
) -> Script:
    errors: list[str] = []
    warnings: list[str] = []
    title = clean_text(str(payload.get("title", ""))).strip()
    hook = clean_text(str(payload.get("hook", ""))).strip()
    errors.extend(_title_ok(title))
    if not 8 <= len(hook) <= 260:
        errors.append("Hook должен быть длиной 8–260 символов")

    raw_scenes = payload.get("scenes")
    if not isinstance(raw_scenes, list) or not 4 <= len(raw_scenes) <= 7:
        errors.append("Нужно 4–7 сцен")
        raw_scenes = raw_scenes if isinstance(raw_scenes, list) else []

    valid_source_ids = {source.id for source in sources}
    corpus_text = "\n".join(source.extract for source in sources)
    corpus_numbers = extract_numbers(corpus_text)
    scenes: list[Scene] = []
    for index, raw in enumerate(raw_scenes, 1):
        if not isinstance(raw, dict):
            errors.append(f"Сцена {index}: ожидался объект")
            continue
        text = clean_text(str(raw.get("text", "")))
        text = EMOJI.sub("", text).strip()
        if not 5 <= word_count(text) <= 42:
            errors.append(f"Сцена {index}: текст должен содержать 5–42 слова")
        source_ids = [str(item) for item in raw.get("source_ids", []) if str(item) in valid_source_ids]
        if not source_ids:
            if sources:
                source_ids = [sources[0].id]
                warnings.append(f"Сцена {index}: модель не указала источник, назначен {sources[0].id}.")
            else:
                errors.append(f"Сцена {index}: нет источника")
        visual = clean_text(str(raw.get("visual", "")))
        visual_query = clean_text(str(raw.get("visual_query", ""))).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ,'\-]{2,70}", visual_query):
            errors.append(f"Сцена {index}: отсутствует или некорректный visual_query")
            visual_query = ""
        on_screen = clean_text(str(raw.get("on_screen", ""))).strip().strip(".,;:!?")
        if not on_screen:
            errors.append(f"Сцена {index}: отсутствует on_screen")
        elif lang == "ru" and not re.search(r"[\u0400-\u04FF]", on_screen):
            on_screen = _caption_from_text(text, lang)
        elif lang != "ru" and re.search(r"[\u0400-\u04FF\u4E00-\u9FFF]", on_screen):
            on_screen = _caption_from_text(text, lang)
        on_screen = " ".join(on_screen.split()[:6])
        if len(on_screen) > 34:
            on_screen = _caption_from_text(text, lang)
        scene = Scene(
            id=f"scene_{index:02d}",
            text=text,
            visual=visual or text,
            visual_query=visual_query,
            on_screen=on_screen,
            source_ids=source_ids,
            transition="fade",
            template=str(raw.get("template", "") or ""),
        )
        scenes.append(scene)

    total_words = sum(word_count(scene.text) for scene in scenes)
    low, high = word_budget(config, lang, plan.duration, max(1, len(scenes) or 5))
    if total_words < low:
        errors.append(f"Слишком короткий сценарий: {total_words} слов, нужно не меньше {low}")
    if total_words > high:
        errors.append(f"Слишком длинный сценарий: {total_words} слов, нужно не больше {high}")

    supported_numbers = corpus_numbers | roman_numbers(corpus_text)
    for scene in scenes:
        scene_numbers = extract_numbers(scene.text) | roman_numbers(scene.text)
        bad = sorted(scene_numbers - supported_numbers)
        if bad:
            errors.append(f"{scene.id}: числа/годы отсутствуют в источниках: {', '.join(bad)}")
    for value in extract_numbers(title + " " + hook) | roman_numbers(title + " " + hook):
        if value not in supported_numbers and value not in {"1", "2", "3", "4", "5"}:
            warnings.append(f"Число {value} в заголовке/hook не найдено в источниках.")

    narration = " ".join(s.text for s in scenes)
    cyrillic = len(re.findall(r"[\u0400-\u04FF]", narration))
    letters = len(re.findall(r"[A-Za-z\u0400-\u04FF]", narration))
    if lang == "en" and cyrillic:
        errors.append("В английском сценарии осталась кириллица")
    if lang == "ru" and (letters == 0 or cyrillic / letters < 0.5):
        errors.append("Русский сценарий не содержит кириллицы")

    seen: list[str] = []
    for scene in scenes:
        for previous in seen:
            if jaccard(previous, scene.text) > 0.72:
                errors.append(f"Повтор текста между сценами: {scene.id}")
        seen.append(scene.text)

    if corpus_text and scenes:
        ratio = len(content_tokens(" ".join(s.text for s in scenes)) & content_tokens(corpus_text)) / max(
            1, len(content_tokens(" ".join(s.text for s in scenes)))
        )
        if ratio < 0.12:
            warnings.append("Низкая текстуальная связь с источниками; проверьте факты вручную.")
    else:
        ratio = 0.0

    cta = clean_text(str(payload.get("cta", ""))).strip()
    if cta:
        if word_count(cta) > 18:
            warnings.append("CTA слишком длинный и не добавлен в озвучку.")
        elif scenes and total_words + word_count(cta) <= high:
            scenes[-1].text = f"{scenes[-1].text} {cta}".strip()
            total_words += word_count(cta)
        else:
            warnings.append("CTA не добавлен: не хватает запаса по длительности.")

    if errors:
        raise ScriptValidationError(errors)
    return Script(
        lang=lang,
        title=title,
        hook=hook,
        scenes=scenes,
        cta=cta,
        engine="",
        model="",
        prompt_version=PROMPT_VERSION,
        warnings=warnings,
        word_count=total_words,
        target_words=low,
        created_at=now_iso(),
    )


class ScriptService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = get_logger()
        self.llm = OllamaClient(config)

    def script_path(self, workspace: Path, lang: str) -> Path:
        return workspace / "scripts" / f"{lang}.json"

    def load(self, workspace: Path, lang: str) -> Script | None:
        path = self.script_path(workspace, lang)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Script.from_dict(data)
        except (OSError, ValueError, TypeError):
            return None

    def save(self, workspace: Path, script: Script, sources: list[Source]) -> Path:
        path = self.script_path(workspace, script.lang)
        atomic_write_json(path, script.to_dict())
        atomic_write_text(workspace / "scripts" / f"{script.lang}.md", script.markdown(sources))
        return path

    @staticmethod
    def _sources_for_text(text: str, sources: list[Source]) -> list[str]:
        """Pick the source(s) that actually contain the sentence's numbers/terms."""
        if not sources:
            return []
        numbers = extract_numbers(text) | roman_numbers(text)
        tokens = content_tokens(text)
        scored: list[tuple[Source, int, int]] = []
        for source in sources:
            source_numbers = extract_numbers(source.extract) | roman_numbers(source.extract)
            scored.append((source, len(numbers & source_numbers), len(tokens & content_tokens(source.extract))))
        if numbers and any(item[1] for item in scored):
            ranked = sorted(scored, key=lambda item: (item[1], item[2]), reverse=True)
            chosen = [ranked[0][0].id]
            for source, number_hits, token_hits in ranked[1:]:
                if number_hits and token_hits >= 3:
                    chosen.append(source.id)
                    break
            return chosen
        ranked = sorted(scored, key=lambda item: item[2], reverse=True)
        return [ranked[0][0].id] if ranked and ranked[0][2] else [sources[0].id]

    @staticmethod
    def _sentence_payload(payload: dict[str, Any], sources: list[Source]) -> dict[str, Any]:
        """Convert the compact five-scene answer into validated scenes."""
        if not isinstance(payload, dict):
            payload = {"sentences": payload}
        raw_scenes = payload.get("scenes")
        if (
            isinstance(raw_scenes, list)
            and raw_scenes
            and all(isinstance(item, dict) for item in raw_scenes)
        ):
            scenes: list[dict[str, Any]] = []
            for item in raw_scenes:
                text = clean_text(str(item.get("text", "")))
                if not text:
                    continue
                query = clean_text(str(item.get("visual_query", "")))
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ,'\-]{2,70}", query):
                    query = ""
                screen = clean_text(str(item.get("on_screen", "")))
                scenes.append(
                    {
                        "text": text,
                        "visual": query or text,
                        "visual_query": query,
                        "on_screen": screen,
                        "source_ids": ScriptService._sources_for_text(text, sources),
                    }
                )
            if MIN_SENTENCES <= len(scenes) <= MAX_SENTENCES:
                normalized = dict(payload)
                normalized["scenes"] = scenes
                if not str(normalized.get("hook", "")).strip():
                    normalized["hook"] = scenes[0]["text"]
                return normalized
            raise ScriptValidationError(
                [f"Нужно {MIN_SENTENCES}–{MAX_SENTENCES} сцен, получено {len(scenes)}."]
            )
        raw = payload.get("sentences")
        if isinstance(raw, str):
            sentences = sentence_split(clean_text(raw))
        elif isinstance(raw, dict):
            sentences = [clean_text(str(item)) for item in raw.values()]
        elif isinstance(raw, list):
            sentences = [clean_text(str(item)) for item in raw]
        elif isinstance(payload.get("narration"), str):
            sentences = sentence_split(clean_text(str(payload.get("narration"))))
        else:
            sentences = []
        sentences = [item for item in sentences if item]
        if not MIN_SENTENCES <= len(sentences) <= MAX_SENTENCES:
            raise ScriptValidationError(
                [f"Нужно {MIN_SENTENCES}–{MAX_SENTENCES} предложений, получено {len(sentences)}. "
                 "Каждое предложение — законченная мысль для отдельной сцены."]
            )
        queries = payload.get("visual_queries")
        screens = payload.get("on_screen")
        queries = queries if isinstance(queries, list) else []
        screens = screens if isinstance(screens, list) else []
        scenes: list[dict[str, Any]] = []
        for index, text in enumerate(sentences):
            query = clean_text(str(queries[index])) if index < len(queries) else ""
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ,'\-]{2,70}", query):
                query = ""
            screen = clean_text(str(screens[index])) if index < len(screens) else ""
            scenes.append(
                {
                    "text": text,
                    "visual": query or text,
                    "visual_query": query,
                    "on_screen": screen,
                    "source_ids": ScriptService._sources_for_text(text, sources),
                }
            )
        normalized = dict(payload)
        normalized["scenes"] = scenes
        if not str(normalized.get("hook", "")).strip():
            normalized["hook"] = sentences[0]
        return normalized

    def generate(self, plan: Plan, sources: list[Source], lang: str, workspace: Path) -> Script:
        existing = self.load(workspace, lang)
        if existing and existing.engine != "pending":
            return existing
        if plan.script_path and lang == ("ru" if re.search(r"[\u0400-\u04FF]", plan.topic) else "en"):
            user_script = self.load_user_script(plan, sources, lang, workspace)
            if user_script:
                return user_script
        low, high = word_budget(self.config, lang, plan.duration, SCENE_TARGET)
        per_low, per_high = scene_word_targets(self.config, lang, plan.duration, SCENE_TARGET)
        prompt = self._prompt(plan, sources, lang, low, high, per_low, per_high)
        errors: list[str] = []
        model = ""
        for attempt in range(int(self.config.get("llm", "max_retries", 1)) + 1):
            try:
                model = self.llm.resolve_model()
                current = prompt
                if errors:
                    current += (
                        "\n\nПредыдущая попытка отклонена. Исправь строго:\n- "
                        + "\n- ".join(errors[:10])
                        + f"\nОбязательно ровно {SCENE_TARGET} сцен с полями text, visual_query, on_screen; "
                        + f"в каждом text {per_low}–{per_high} слов. "
                        + "Удали из текста все числа, перечисленные в ошибках, и не заменяй их "
                        + "другими выдуманными числами или датами из общих знаний. "
                        + "Не сокращай предложения до 5–10 слов; всего должно быть "
                        + f"{low}–{high} слов. "
                        + (
                            "Все предложения — строго на русском языке, кириллицей. "
                            if lang == "ru"
                            else "All sentences must be in English. "
                        )
                        + "Верни только исправленный JSON."
                    )
                payload = self.llm.generate_json(
                    current,
                    system=self._system(lang),
                    temperature=0.5,
                )
                payload = self._sentence_payload(payload, sources)
                script = validate_payload(
                    payload, sources=sources, lang=lang, plan=plan, config=self.config
                )
                script.engine = f"ollama:{model}"
                script.model = model
                script.prompt_version = PROMPT_VERSION
                if errors:
                    script.warnings.append(
                        "Предыдущие попытки отклонены; итоговый сценарий прошёл проверки."
                    )
                self.save(workspace, script, sources)
                self.logger.info("Сценарий %s: %s слов, %s сцен", lang, script.word_count, len(script.scenes))
                return script
            except (LLMError, ScriptValidationError, ValueError) as exc:
                if isinstance(exc, ScriptValidationError):
                    errors = exc.errors
                else:
                    errors = [str(exc)]
                self.logger.warning("Попытка сценария %s не удалась: %s", lang, "; ".join(errors[:5]))
        script = self.extractive_fallback(plan, sources, lang, workspace)
        script.warnings = [f"LLM не дал валидный сценарий: {'; '.join(errors[:3])}"] + script.warnings
        self.save(workspace, script, sources)
        return script

    def adapt(self, plan: Plan, sources: list[Source], ru_script: Script, workspace: Path) -> Script:
        existing = self.load(workspace, "en")
        if existing:
            return existing
        low, high = word_budget(self.config, "en", plan.duration, SCENE_TARGET)
        per_low, per_high = scene_word_targets(self.config, "en", plan.duration, SCENE_TARGET)
        mid = (low + high) // 2
        digest = self._digest(sources[:2], 1800)
        corpus_text = " ".join(source.extract for source in sources)
        allowed_numbers = sorted(
            extract_numbers(corpus_text + " " + ru_script.all_text + " " + ru_script.title)
            | roman_numbers(corpus_text + " " + ru_script.all_text + " " + ru_script.title)
        )
        allowed_text = ", ".join(allowed_numbers) or "none"
        payload = {
            "title": ru_script.title,
            "hook": ru_script.hook,
            "scenes": [{"text": scene.text} for scene in ru_script.scenes],
        }
        prompt = f"""You are a native English short-form science editor.
Adapt the Russian script below into natural, spoken English. Do not translate word-for-word.
Keep every fact and every number identical. Do not add facts that are not in the Russian script or sources.
Keep centuries as centuries (write "the 5th century BC", never "500 BC").
Target duration: {plan.duration:g} seconds. Use {low}–{high} spoken words total.
Allowed numbers and years: {allowed_text}. Do not mention any number outside this list.

Main requirement: exactly {SCENE_TARGET} scenes, each with one English sentence of at least {per_low} words (aim for {per_low}–{per_high}), {low}–{high} words total (about {mid}).
Count the words before answering. Sentences of 5–10 words are rejected.
Keep the scenes in the same order as the Russian script. Every scene object must include text, visual_query and on_screen; missing fields are rejected. For every scene write an English visual query (2–5 words) that illustrates that scene's sentence and an English on-screen caption (2–5 words). Never copy the full sentence into visual_query. Do not move metadata to another scene.
The result must sound like an English creator wrote it, not like machine translation.

Return STRICT JSON:
{{"title": "...", "hook": "...", "scenes": [{{"text": "...", "visual_query": "2-5 English words", "on_screen": "2-5 words"}}, {{"text": "...", "visual_query": "...", "on_screen": "..."}}, {{"text": "...", "visual_query": "...", "on_screen": "..."}}, {{"text": "...", "visual_query": "...", "on_screen": "..."}}, {{"text": "...", "visual_query": "...", "on_screen": "..."}}], "cta": ""}}

Russian script:
{json.dumps(payload, ensure_ascii=False, indent=1)}

Source excerpts:
{digest}
"""
        errors: list[str] = []
        for attempt in range(int(self.config.get("llm", "max_retries", 1)) + 1):
            try:
                model = self.llm.resolve_model()
                current = prompt
                if errors:
                    current += (
                        "\n\nRejected: " + "; ".join(errors[:10])
                        + f"\nReturn exactly {SCENE_TARGET} scene objects in the original order, "
                        + "each with text, visual_query and on_screen. JSON only."
                    )
                raw = self.llm.generate_json(current, system=self._system("en"), temperature=0.55)
                raw = self._sentence_payload(raw, sources)
                script = validate_payload(raw, sources=sources, lang="en", plan=plan, config=self.config)
                ru_text = ru_script.all_text + " " + ru_script.title
                corpus_text = " ".join(source.extract for source in sources)
                ru_numbers = extract_numbers(ru_text) | roman_numbers(ru_text)
                en_numbers = extract_numbers(script.all_text + " " + script.title)
                corpus_numbers = extract_numbers(corpus_text) | roman_numbers(corpus_text)
                unsupported = sorted(en_numbers - ru_numbers - corpus_numbers)
                if unsupported:
                    raise ScriptValidationError([f"Английская версия добавила числа: {', '.join(unsupported)}"])
                script.engine = f"ollama:{model}:adapt"
                script.model = model
                script.prompt_version = PROMPT_VERSION
                if errors:
                    script.warnings.append(
                        "Предыдущие попытки адаптации отклонены; итоговый сценарий прошёл проверки."
                    )
                self.save(workspace, script, sources)
                self.logger.info("Адаптация EN: %s слов, %s сцен", script.word_count, len(script.scenes))
                return script
            except (LLMError, ScriptValidationError, ValueError) as exc:
                errors = exc.errors if isinstance(exc, ScriptValidationError) else [str(exc)]
                self.logger.warning("Попытка адаптации EN не удалась: %s", "; ".join(errors[:5]))
        script = self.extractive_fallback(plan, sources, "en", workspace)
        script.warnings = [f"LLM-адаптация не удалась: {'; '.join(errors[:3])}"] + script.warnings
        self.save(workspace, script, sources)
        return script

    # --------------------------------------------------------------------- prompts
    def _system(self, lang: str) -> str:
        if lang == "ru":
            return (
                "Ты — редактор научно-популярного YouTube Shorts. Пишешь только по фактам из "
                "предоставленных источников и только на русском языке кириллицей. Не придумываешь "
                "числа, даты, цитаты, исследования и события. Пишешь живой разговорной речью, без "
                "канцелярита и кликбейта. Возвращаешь только JSON."
            )
        return (
            "You are an editor for a factual YouTube Shorts channel. Use only facts from the provided "
            "sources. Never invent numbers, quotes, studies, or events. Write natural spoken English, "
            "no clickbait. Return JSON only."
        )

    def _prompt(
        self, plan: Plan, sources: list[Source], lang: str, low: int, high: int,
        per_low: int = 0, per_high: int = 0,
    ) -> str:
        profile = self.config.profile(plan.profile)
        label = profile.get("label_ru" if lang == "ru" else "label_en", plan.profile)
        tone = profile.get("tone_ru" if lang == "ru" else "tone_en", "")
        per_low = per_low or max(12, low // SCENE_TARGET)
        per_high = per_high or max(per_low + 2, high // SCENE_TARGET + 2)
        mid = (low + high) // 2
        digest = self._digest(sources, 2200)
        corpus_text = " ".join(source.extract for source in sources)
        allowed_numbers = sorted(extract_numbers(corpus_text) | roman_numbers(corpus_text))
        allowed_text = ", ".join(allowed_numbers) or "нет"
        if lang == "ru":
            return f"""Создай сценарий вертикального YouTube Shorts на русском языке.
Тема: {plan.topic}
Тематический профиль: {label}. Тон: {tone}.
Целевая длительность: {plan.duration:g} секунд. Нужно {low}–{high} слов озвучки.
Разрешённые числа и годы: {allowed_text}. Не используй числа вне этого списка.

Источники (используй только эти факты; не добавляй ничего от себя):
{digest}

Главное требование: ровно {SCENE_TARGET} сцен. В каждой сцене одно законченное предложение, в нём не меньше {per_low} слов (ориентир {per_low}–{per_high}), всего {low}–{high} слов (примерно {mid}).
Посчитай слова перед ответом. Короткие предложения из 5–10 слов не принимаются.
Каждая сцена — законченная мысль. В каждом объекте scene обязательны поля text, visual_query и on_screen; отсутствие поля — ошибка. Визуальный запрос и титр должны относиться именно к тексту этой же сцены, не к соседней. Не копируй текст сцены в visual_query.

Требования:
- Сильный, но правдивый первый кадр: без «вы не поверите» и ложных обещаний.
- Естественные переходы между предложениями, финал без навязчивого призыва подписаться.
- Каждое число, год и процент должны буквально встречаться в источниках.
- visual_query: короткий поисковый запрос НА АНГЛИЙСКОМ (2–5 слов) для этой сцены.
- on_screen: короткий титр (2–5 слов) для этой сцены.
- Не повторяй одну и ту же фразу. Не выдумывай цитаты.
- title: 30–85 символов, без кликбейта; hook: 1 короткое предложение-тизер.

Верни строго JSON:
{{"title": "...", "hook": "...", "scenes": [{{"text": "предложение", "visual_query": "short english query", "on_screen": "Короткий титр"}}, {{"text": "...", "visual_query": "...", "on_screen": "..."}}, {{"text": "...", "visual_query": "...", "on_screen": "..."}}, {{"text": "...", "visual_query": "...", "on_screen": "..."}}, {{"text": "...", "visual_query": "...", "on_screen": "..."}}], "cta": ""}}
"""
        return f"""Write a vertical YouTube Shorts script in natural English.
Topic: {plan.topic_en or plan.topic}
Profile: {label}. Tone: {tone}.
Target duration: {plan.duration:g} seconds. Use {low}–{high} spoken words.
Allowed numbers and years: {allowed_text}. Do not mention any number outside this list.

Sources (use only these facts; add nothing):
{digest}

Main requirement: exactly {SCENE_TARGET} sentences, each with at least {per_low} words (aim for {per_low}–{per_high}), {low}–{high} words total (about {mid}).
Count the words before answering. Sentences of 5–10 words are rejected.
Each sentence is one complete thought for one scene.

Requirements:
- Strong but truthful opening; no "you won't believe" or false promises.
- Natural transitions, a clean ending with no forced subscribe CTA.
- Every number, year, and percentage must appear verbatim in the sources.
- visual_queries: exactly {SCENE_TARGET} short English search queries (2–5 words).
- on_screen: exactly {SCENE_TARGET} short captions (2–5 words).
- Do not repeat sentences. Do not invent quotes.
- title: 30–85 characters, no clickbait; hook: one short teaser sentence.

Return strict JSON:
{{"title": "...", "hook": "...", "sentences": ["...", "...", "...", "...", "..."], "visual_queries": ["short english query", "...", "...", "...", "..."], "on_screen": ["short caption", "...", "...", "...", "..."], "cta": ""}}
"""

    def _digest(self, sources: list[Source], per_source: int) -> str:
        parts = []
        for source in sources:
            parts.append(f"[{source.id}] {source.title}\n{source.extract[:per_source]}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------- fallback
    def extractive_fallback(
        self, plan: Plan, sources: list[Source], lang: str, workspace: Path
    ) -> Script:
        pool = [source for source in sources if source.language == lang]
        if not pool:
            pool = sources
        if not pool:
            raise ScriptError(f"Нет источников для языка {lang}")
        candidates: list[tuple[float, str, Source]] = []
        title_tokens = content_tokens(plan.topic if lang == "ru" else (plan.topic_en or plan.topic))
        for source in pool:
            for sentence in sentence_split(source.extract):
                sentence = sentence.strip()
                words = word_count(sentence)
                if not 10 <= words <= 38:
                    continue
                if sentence.startswith("==") or "http" in sentence.lower():
                    continue
                score = 0.0
                if extract_numbers(sentence):
                    score += 2.0
                score += len(content_tokens(sentence) & title_tokens) * 1.5
                score += min(words, 30) / 30
                if sentence.endswith("?") or sentence.endswith("!"):
                    score -= 0.4
                candidates.append((score, sentence, source))
        candidates.sort(key=lambda item: item[0], reverse=True)
        low, high = word_budget(self.config, lang, plan.duration, SCENE_TARGET)
        chosen: list[tuple[str, Source]] = []
        total_words = 0
        for _, sentence, source in candidates:
            words = word_count(sentence)
            if len(chosen) >= MAX_SENTENCES:
                break
            if total_words >= low and len(chosen) >= MIN_SENTENCES:
                break
            # Never exceed the acceptance window, otherwise the rendered duration fails QC.
            if total_words + words > high:
                continue
            if any(jaccard(sentence, previous) > 0.55 for previous, _ in chosen):
                continue
            chosen.append((sentence, source))
            total_words += words
        if len(chosen) < 3:
            raise ScriptError("В источниках слишком мало пригодных предложений для сценария.")
        scenes: list[Scene] = []
        for index, (sentence, source) in enumerate(chosen, 1):
            words = sentence.split()
            on_screen = " ".join(words[:4]).strip(".,;:!?")
            scenes.append(
                Scene(
                    id=f"scene_{index:02d}",
                    text=sentence,
                    visual=sentence,
                    visual_query=plan.seed.get("query_en") or plan.topic_en or "abstract background",
                    on_screen=on_screen,
                    source_ids=[source.id],
                    transition="fade",
                )
            )
        title = plan.topic if lang == "ru" else (plan.topic_en or plan.topic)
        title = title.strip()[:88]
        if title:
            title = title[0].upper() + title[1:]
        if len(title) < 8:
            title = f"{title}: коротко о главном"
        script = Script(
            lang=lang,
            title=title,
            hook=scenes[0].text,
            scenes=scenes,
            engine="extractive-fallback",
            model="none",
            prompt_version=PROMPT_VERSION,
            warnings=[
                "Использован экстрактивный сценарий из источников; проверьте связность и факты вручную."
            ],
            word_count=sum(word_count(scene.text) for scene in scenes),
            target_words=word_budget(self.config, lang, plan.duration)[0],
            created_at=now_iso(),
        )
        return script

    # ---------------------------------------------------------------- user script
    def load_user_script(
        self, plan: Plan, sources: list[Source], lang: str, workspace: Path
    ) -> Script | None:
        path = Path(plan.script_path)
        if not path.is_absolute():
            path = self.config.root / path
        if not path.exists():
            self.logger.warning("Пользовательский сценарий не найден: %s", path)
            return None
        if path.suffix.lower() == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                script = Script.from_dict(data)
                script.lang = lang
                script.engine = "user-script"
                return script
            except (OSError, ValueError, TypeError):
                return None
        text = path.read_text(encoding="utf-8")
        title = plan.topic
        sections: list[str] = []
        current: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                title = stripped[2:].strip()
            elif stripped.startswith("## "):
                if current:
                    sections.append("\n".join(current).strip())
                current = []
            elif stripped and not stripped.startswith("**") and not stripped.startswith("---"):
                current.append(stripped)
        if current:
            sections.append("\n".join(current).strip())
        if not sections:
            sections = [part.strip() for part in re.split(r"\n\s*\n", text) if len(part.strip()) > 60]
        scenes: list[Scene] = []
        for index, section in enumerate(sections[:8], 1):
            body = re.sub(r"\s+", " ", section).strip()
            if len(body) < 20:
                continue
            scenes.append(
                Scene(
                    id=f"scene_{index:02d}",
                    text=body,
                    visual=body,
                    visual_query=plan.seed.get("query_en") or plan.topic_en or "abstract background",
                    on_screen=" ".join(body.split()[:4]).strip(".,;:!?"),
                    source_ids=[sources[0].id] if sources else [],
                )
            )
        if not scenes:
            return None
        script = Script(
            lang=lang,
            title=title or plan.topic,
            hook=scenes[0].text,
            scenes=scenes,
            engine="user-script",
            model="none",
            prompt_version="user",
            warnings=["Пользовательский сценарий: факты не проверялись автоматически."],
            word_count=sum(word_count(scene.text) for scene in scenes),
            created_at=now_iso(),
        )
        return script
