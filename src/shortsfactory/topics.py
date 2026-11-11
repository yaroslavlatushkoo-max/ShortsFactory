from __future__ import annotations

import random
import re
from typing import Any

from .config import Config
from .util import clean_text, normalize_match, unique_preserve


TOPIC_NORMALIZATION = {
    "космосе": "космос", "космоса": "космос", "космосу": "космос",
    "истории": "история", "историю": "история",
    "технологиях": "технологии", "технологий": "технологии", "технологию": "технологии",
    "природе": "природа", "природу": "природа",
    "животных": "животные", "животным": "животные",
    "географии": "география", "географию": "география",
    "путешествиях": "путешествия", "путешествий": "путешествия",
    "биографиях": "биографии", "биографий": "биографии",
    "культуре": "культура", "культуру": "культура",
    "играх": "игры", "игр": "игры",
    "науке": "наука", "науку": "наука",
    "древней истории": "древняя история",
    "древнюю историю": "древняя история",
}

GENERIC_TOPICS = {
    "интересное научное открытие", "научное открытие", "научные открытия",
    "разные популярные темы", "популярные темы", "любые темы", "разные темы",
    "интересные темы", "популярная тема", "что-нибудь интересное",
    "интересный факт", "интересные факты",
}


def normalize_topic(topic: str) -> str:
    value = clean_text(topic).strip(" .,:;!?«»\"'")
    value = re.sub(r"^(?:о|об|про|на тему|по теме|about)\s+", "", value, flags=re.IGNORECASE)
    key = normalize_match(value)
    if key in TOPIC_NORMALIZATION:
        return TOPIC_NORMALIZATION[key]
    return value


def is_generic_topic(topic: str) -> bool:
    return normalize_match(topic) in GENERIC_TOPICS


def detect_profile(text: str, config: Config, default: str = "") -> str:
    haystack = normalize_match(text)
    best = (0, default)
    for name in config.profile_names():
        profile = config.profile(name)
        keywords = list(profile.get("keywords_ru", [])) + list(profile.get("keywords_en", []))
        score = 0
        for keyword in keywords:
            key = normalize_match(str(keyword))
            if key and key in haystack:
                score += max(2, len(key))
        if score > best[0]:
            best = (score, name)
    return best[1]


def match_seed(topic: str, config: Config, profile: str) -> dict[str, Any]:
    key = normalize_match(topic)
    profile_data = config.profile(profile)
    best: tuple[float, dict[str, Any]] = (0.0, {})
    for seed in profile_data.get("seeds", []):
        candidates = [seed.get("ru", ""), seed.get("en", ""), seed.get("query_ru", ""), seed.get("query_en", "")]
        score = 0.0
        for candidate in candidates:
            candidate_key = normalize_match(str(candidate))
            if not candidate_key:
                continue
            if candidate_key == key:
                score = max(score, 1.0)
            elif key and (key in candidate_key or candidate_key in key):
                score = max(score, 0.75)
            else:
                words = set(key.split())
                candidate_words = set(candidate_key.split())
                if words and candidate_words:
                    score = max(score, len(words & candidate_words) / len(words | candidate_words))
        if score > best[0]:
            best = (score, seed)
    return best[1] if best[0] >= 0.34 else {}


def choose_topic(
    config: Config,
    store,
    topic: str = "",
    profile: str = "",
    rng: random.Random | None = None,
) -> dict[str, Any]:
    rng = rng or random.Random()
    normalized = normalize_topic(topic) if topic else ""
    generic = not normalized or is_generic_topic(normalized)
    if profile and profile not in config.profiles:
        raise ValueError(f"Неизвестный профиль: {profile}")
    if not profile:
        profile = detect_profile(normalized, config, "explainer") if normalized else "science"

    recent = {normalize_match(item) for item in store.recent_topics(30)}
    if generic:
        profile_data = config.profile(profile)
        seeds = [seed for seed in profile_data.get("seeds", [])
                 if normalize_match(seed.get("ru", "")) not in recent]
        if not seeds:
            seeds = list(profile_data.get("seeds", []))
        if not seeds:
            raise ValueError(f"В профиле {profile} нет тем")
        seed = rng.choice(seeds)
        return {
            "topic": seed["ru"],
            "topic_en": seed.get("en", ""),
            "profile": profile,
            "seed": dict(seed),
        }

    if not profile:
        profile = detect_profile(normalized, config, "explainer")
    seed = match_seed(normalized, config, profile)
    topic_en = seed.get("en", "") if seed else ""
    if not topic_en and normalized.isascii():
        topic_en = normalized
    return {
        "topic": normalized,
        "topic_en": topic_en,
        "profile": profile,
        "seed": dict(seed) if seed else {},
    }


def suggestions(config: Config, profile: str = "science", limit: int = 5) -> list[str]:
    data = config.profile(profile)
    seeds = data.get("seeds", [])[:limit]
    return [seed.get("ru", "") for seed in seeds if seed.get("ru")]


def all_profile_keywords(config: Config) -> list[str]:
    values: list[str] = []
    for name in config.profile_names():
        profile = config.profile(name)
        values.extend(profile.get("keywords_ru", []))
        values.extend(profile.get("keywords_en", []))
    return unique_preserve(values)
