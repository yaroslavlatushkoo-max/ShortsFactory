from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .config import Config
from .logging_setup import get_logger
from .models import Plan, Source
from .util import (
    atomic_write_json,
    atomic_write_text,
    clean_text,
    extract_numbers,
    is_cyrillic,
    now_iso,
    read_text,
)


class ResearchError(RuntimeError):
    pass


@dataclass
class ResearchResult:
    sources: list[Source]
    corpus: str
    topic_en: str
    warnings: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    shared_numbers: list[str] = field(default_factory=list)


class HttpJsonCache:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.cache_dir = config.workspace_dir / "_cache" / "http"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(hours=float(config.get("research", "cache_hours", 168)))
        self.timeout = float(config.get("research", "request_timeout", 25))
        self.user_agent = str(config.get("research", "user_agent", "ShortsFactoryPro/1.0"))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        key = hashlib.sha256(
            (url + "?" + json.dumps(params, sort_keys=True, ensure_ascii=False)).encode("utf-8")
        ).hexdigest()
        cache_path = self.cache_dir / f"{key}.json"
        if cache_path.exists():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                fetched = datetime.fromisoformat(payload["fetched_at"])
                if datetime.now(timezone.utc) - fetched < self.ttl:
                    return payload["data"]
            except (ValueError, KeyError, OSError):
                pass
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ResearchError(f"Сетевая ошибка при запросе {url}: {exc}") from exc
        if response.status_code != 200:
            raise ResearchError(f"{url} вернул HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ResearchError(f"{url} вернул не JSON") from exc
        atomic_write_json(
            cache_path,
            {"fetched_at": datetime.now(timezone.utc).isoformat(), "data": data},
        )
        return data


class ResearchService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.http = HttpJsonCache(config)
        self.logger = get_logger()
        self.max_sources = int(config.get("research", "max_sources", 3))
        self.max_chars = int(config.get("research", "max_chars_per_source", 6000))

    def collect(self, plan: Plan, workspace: Path) -> ResearchResult:
        research_dir = workspace / "research"
        research_dir.mkdir(parents=True, exist_ok=True)
        primary = "ru" if is_cyrillic(plan.topic) else "en"
        secondary = "en" if primary == "ru" else "ru"
        query_primary = plan.seed.get("query_ru" if primary == "ru" else "query_en") or plan.topic
        query_secondary = plan.seed.get("query_en" if secondary == "en" else "query_ru") or plan.topic_en or plan.topic
        sources: list[Source] = []
        warnings: list[str] = []
        queries: list[str] = []

        user_sources = self._user_sources(plan, workspace)
        for source in user_sources:
            sources.append(source)

        if user_sources and plan.script_path:
            warnings.append("Использован пользовательский сценарий; внешние факты не проверялись автоматически.")

        wiki_sources: list[Source] = []
        topic_en = plan.topic_en
        if len(sources) < self.max_sources:
            try:
                pages = self._search(primary, query_primary, secondary)
                queries.append(f"{primary}:{query_primary}")
                for page in pages[:2]:
                    if len(sources) + len(wiki_sources) >= self.max_sources:
                        break
                    source = self._page_to_source(page, primary)
                    if source and not any(s.url == source.url for s in sources + wiki_sources):
                        wiki_sources.append(source)
                    link_title = (page.get("langlinks") or [{}])[0].get("title", "")
                    if link_title and not topic_en:
                        topic_en = link_title
                    if link_title and len(sources) + len(wiki_sources) < self.max_sources:
                        counterpart = self._fetch_by_title(secondary, link_title)
                        if counterpart:
                            counter_source = self._page_to_source(counterpart, secondary)
                            if counter_source and not any(
                                s.url == counter_source.url for s in sources + wiki_sources
                            ):
                                wiki_sources.append(counter_source)
            except ResearchError as exc:
                warnings.append(f"Поиск в Wikipedia ({primary}) не удался: {exc}")

        if not wiki_sources and len(sources) < self.max_sources:
            fallback_query = query_secondary
            try:
                pages = self._search(secondary, fallback_query, primary)
                queries.append(f"{secondary}:{fallback_query}")
                for page in pages[:2]:
                    source = self._page_to_source(page, secondary)
                    if source and not any(s.url == source.url for s in sources + wiki_sources):
                        wiki_sources.append(source)
                if wiki_sources and not topic_en:
                    topic_en = wiki_sources[0].title
            except ResearchError as exc:
                warnings.append(f"Поиск в Wikipedia ({secondary}) не удался: {exc}")

        sources.extend(wiki_sources[: self.max_sources])
        sources = sources[: self.max_sources]

        if not sources:
            raise ResearchError(
                "Не удалось получить ни одного источника. Проверьте интернет или положите "
                f"проверенный текст в папку input/ (запрос: {primary}:{query_primary})."
            )

        languages = {source.language for source in sources}
        if len(languages) < 2:
            warnings.append("Источники только на одном языке; вторая языковая версия будет адаптацией.")
        if any(source.publisher.startswith("Wikipedia") for source in sources):
            warnings.append("Wikipedia — вторичный источник; важные утверждения стоит проверить вручную.")

        shared_numbers = self._compare_numbers(sources)
        if shared_numbers:
            warnings.append(f"Числа, встречающиеся в источниках на разных языках: {', '.join(shared_numbers[:8])}.")

        corpus = self._corpus(sources)
        if not corpus.strip():
            raise ResearchError("Источники получены, но текст пуст.")

        result = ResearchResult(
            sources=sources,
            corpus=corpus,
            topic_en=topic_en or plan.topic_en or plan.topic,
            warnings=warnings,
            queries=queries,
            shared_numbers=shared_numbers,
        )
        self._save(plan, workspace, result)
        return result

    # ------------------------------------------------------------------ Wikipedia
    def _search(self, lang: str, query: str, link_lang: str) -> list[dict[str, Any]]:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": "5",
            "gsrnamespace": "0",
            "prop": "extracts|info|langlinks|revisions",
            "exintro": "1",
            "explaintext": "1",
            "inprop": "url",
            "lllang": link_lang,
            "rvprop": "timestamp",
            "redirects": "1",
        }
        data = self.http.get_json(f"https://{lang}.wikipedia.org/w/api.php", params)
        pages = (data.get("query") or {}).get("pages") or []
        if isinstance(pages, dict):
            pages = list(pages.values())
        result = []
        for page in sorted(pages, key=lambda item: item.get("index", 999)):
            title = page.get("title", "")
            extract = clean_text(page.get("extract", ""))
            if not extract or len(extract) < 120:
                continue
            if re.search(r"disambiguation|неоднозначност", title, flags=re.IGNORECASE):
                continue
            result.append(page)
        return result

    def _fetch_by_title(self, lang: str, title: str) -> dict[str, Any] | None:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "titles": title,
            "prop": "extracts|info|revisions",
            "exintro": "1",
            "explaintext": "1",
            "inprop": "url",
            "rvprop": "timestamp",
            "redirects": "1",
        }
        try:
            data = self.http.get_json(f"https://{lang}.wikipedia.org/w/api.php", params)
        except ResearchError:
            return None
        pages = (data.get("query") or {}).get("pages") or []
        if isinstance(pages, dict):
            pages = list(pages.values())
        return pages[0] if pages else None

    def _page_to_source(self, page: dict[str, Any], lang: str) -> Source | None:
        extract = clean_text(page.get("extract", ""))
        if len(extract) < 120:
            return None
        title = page.get("title", "")
        revision = ""
        revisions = page.get("revisions") or []
        if revisions and isinstance(revisions[0], dict):
            revision = revisions[0].get("timestamp", "")
        url = page.get("fullurl") or f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}"
        return Source(
            id="",
            title=title,
            url=url,
            publisher=f"Wikipedia ({lang})",
            language=lang,
            accessed_at=now_iso(),
            revision_date=revision,
            license="CC BY-SA 4.0",
            extract=extract[: self.max_chars],
        )

    # ------------------------------------------------------------------- user input
    def _user_sources(self, plan: Plan, workspace: Path) -> list[Source]:
        candidates: list[Path] = []
        if plan.script_path:
            path = Path(plan.script_path)
            candidates.append(path if path.is_absolute() else self.config.root / path)
        input_dir = self.config.input_dir
        if input_dir.exists():
            for path in sorted(input_dir.glob("*.md")) + sorted(input_dir.glob("*.txt")):
                if path not in candidates and path.name.lower() not in {"readme.md", "readme.txt"}:
                    candidates.append(path)
        result: list[Source] = []
        for index, path in enumerate(candidates[:2], 1):
            text = read_text(path)
            if len(text.strip()) < 80:
                continue
            result.append(
                Source(
                    id=f"U{index}",
                    title=path.name,
                    url=path.resolve().as_uri(),
                    publisher="Пользовательский материал",
                    language="ru" if is_cyrillic(text) else "en",
                    accessed_at=now_iso(),
                    revision_date=datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                    license="user-provided",
                    extract=clean_text(text)[: self.max_chars * 2],
                )
            )
        return result

    def _compare_numbers(self, sources: list[Source]) -> list[str]:
        by_lang: dict[str, set[str]] = {}
        for source in sources:
            by_lang.setdefault(source.language, set()).update(extract_numbers(source.extract))
        if len(by_lang) < 2:
            return []
        sets = list(by_lang.values())
        shared = set.intersection(*sets)
        return sorted(shared, key=lambda value: (len(value), value))[:12]

    def _corpus(self, sources: list[Source]) -> str:
        parts: list[str] = []
        for source in sources:
            parts.append(
                f"[{source.id}] {source.publisher} — {source.title}\n"
                f"URL: {source.url}\n"
                f"Дата обращения: {source.accessed_at}; лицензия: {source.license}\n"
                f"{source.extract}\n"
            )
        return "\n".join(parts)

    def _save(self, plan: Plan, workspace: Path, result: ResearchResult) -> None:
        for index, source in enumerate(result.sources, 1):
            source.id = f"S{index}"
        corpus = self._corpus(result.sources)
        result.corpus = corpus
        research_dir = workspace / "research"
        atomic_write_json(
            research_dir / "sources.json",
            {
                "topic": plan.topic,
                "topic_en": result.topic_en,
                "generated_at": now_iso(),
                "queries": result.queries,
                "warnings": result.warnings,
                "shared_numbers": result.shared_numbers,
                "sources": [source.to_dict() for source in result.sources],
            },
        )
        atomic_write_text(research_dir / "corpus.txt", corpus)
        lines = [f"# Исследование: {plan.topic}", "", f"Дата: {now_iso()}", ""]
        if result.warnings:
            lines += ["## Предупреждения", ""] + [f"- {item}" for item in result.warnings] + [""]
        lines += ["## Источники", ""]
        for source in result.sources:
            lines += [
                f"### [{source.id}] {source.title}",
                "",
                f"- Издатель: {source.publisher}",
                f"- URL: {source.url}",
                f"- Язык: {source.language}",
                f"- Лицензия: {source.license}",
                f"- Дата обращения: {source.accessed_at}",
                f"- Ревизия: {source.revision_date or '—'}",
                "",
                source.extract[:2500] + ("…" if len(source.extract) > 2500 else ""),
                "",
            ]
        atomic_write_text(research_dir / "research.md", "\n".join(lines))
        atomic_write_json(
            research_dir / "research.json",
            {
                "topic": plan.topic,
                "topic_en": result.topic_en,
                "warnings": result.warnings,
                "queries": result.queries,
                "shared_numbers": result.shared_numbers,
                "source_count": len(result.sources),
                "generated_at": now_iso(),
            },
        )
        self.logger.info("Исследование: %s источников, %s символов", len(result.sources), len(corpus))
