from __future__ import annotations

import hashlib
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Config
from .logging_setup import get_logger
from .models import Script
from .util import atomic_write_json, clean_text, content_tokens, dhash, now_iso, strip_html


class MediaError(RuntimeError):
    pass


# Increment when the query construction changes so old manifests are refreshed.
MEDIA_QUERY_VERSION = 2

ALLOWED_LICENSE = re.compile(r"(?i)(public\s*domain|\bcc0\b|\bcc[ -]?by(?:[ -]?\d|$))")
BAD_LICENSE = re.compile(r"(?i)(\bsa\b|\bnc\b|\bnd\b|share[ -]?alike|non[ -]?commercial|no[ -]?deriv)")


@dataclass
class MediaCandidate:
    provider: str
    title: str
    download_url: str
    source_page: str
    author: str
    license: str
    license_url: str
    width: int = 0
    height: int = 0
    description: str = ""
    query: str = ""
    score: float = 0.0

    def attribution(self) -> str:
        author = self.author or "автор не указан"
        return f"{author} / {self.provider} / {self.license}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["attribution"] = self.attribution()
        return data


@dataclass
class MediaResult:
    scenes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def license_allowed(value: str) -> bool:
    text = strip_html(value)
    if not text:
        return False
    if BAD_LICENSE.search(text):
        # "CC BY 4.0" contains no bad token; "CC BY-SA" / "NC" / "ND" are rejected.
        return False
    return bool(ALLOWED_LICENSE.search(text))


def _safe_query(query: str) -> str:
    value = re.sub(r"[^\w\s,'\-]", " ", query or "", flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:90] or "abstract background"


class MediaService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = get_logger()
        self.timeout = float(config.get("research", "request_timeout", 25))
        self.max_bytes = int(float(config.get("media", "max_download_mb", 30)) * 1024 * 1024)
        self.min_width = int(config.get("media", "min_width", 800))
        self.min_height = int(config.get("media", "min_height", 600))
        self.limit = int(config.get("media", "candidates", 8))
        self.cache_dir = config.workspace_dir / "_cache" / "media"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": str(config.get("research", "user_agent", "ShortsFactoryPro/1.0"))}
        )

    # ------------------------------------------------------------------ providers
    def search(self, query: str, profile: str) -> list[MediaCandidate]:
        query = _safe_query(query)
        candidates: list[MediaCandidate] = []
        providers: list[str] = []
        if profile in {"science", "tech", "explainer"} and self.config.get("media", "nasa_enabled", True):
            providers.append("NASA")
        if self.config.get("media", "commons_enabled", True):
            providers.append("Wikimedia Commons")
        if self.config.get("media", "openverse_enabled", True):
            providers.append("Openverse")
        for provider in providers:
            try:
                if provider == "NASA":
                    candidates.extend(self._nasa(query))
                elif provider == "Wikimedia Commons":
                    candidates.extend(self._commons(query))
                else:
                    candidates.extend(self._openverse(query))
            except (requests.RequestException, MediaError, ValueError) as exc:
                self.logger.warning("Поиск медиа %s не удался: %s", provider, exc)
            time.sleep(0.15)
        seen: set[str] = set()
        unique: list[MediaCandidate] = []
        for candidate in candidates:
            key = candidate.download_url or candidate.source_page
            if key and key not in seen:
                seen.add(key)
                unique.append(candidate)
        unique.sort(key=lambda item: item.score, reverse=True)
        return unique[: self.limit]

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 200:
                    return response.json()
                if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                    time.sleep(1.5)
                    continue
                raise MediaError(f"{url} вернул HTTP {response.status_code}")
            except requests.RequestException as exc:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                raise MediaError(f"Сетевая ошибка {url}: {exc}") from exc
        raise MediaError(f"Не удалось получить {url}")

    def _nasa(self, query: str) -> list[MediaCandidate]:
        data = self._get_json(
            "https://images-api.nasa.gov/search",
            {"q": query, "media_type": "image", "page_size": str(self.limit)},
        )
        items = (data.get("collection") or {}).get("items") or []
        result: list[MediaCandidate] = []
        for item in items:
            entries = item.get("data") or []
            if not entries:
                continue
            meta = entries[0]
            links = item.get("links") or []
            href = ""
            for link in links:
                candidate = str(link.get("href", ""))
                if candidate.lower().endswith((".jpg", ".jpeg", ".png")):
                    if "~orig" in candidate or "~large" in candidate:
                        href = candidate
                        break
                    if not href:
                        href = candidate
            if not href:
                continue
            nasa_id = str(meta.get("nasa_id", ""))
            title = clean_text(str(meta.get("title", "")))
            description = clean_text(str(meta.get("description", "")))
            score = self._score(query, f"{title} {description}") + 0.18
            result.append(
                MediaCandidate(
                    provider="NASA",
                    title=title or nasa_id,
                    download_url=href,
                    source_page=f"https://images.nasa.gov/details/{nasa_id}" if nasa_id else href,
                    author="NASA",
                    license="Public domain (NASA)",
                    license_url="https://www.nasa.gov/nasa-brand-center/images-and-media/",
                    description=description,
                    query=query,
                    score=score,
                )
            )
        return result

    def _commons(self, query: str) -> list[MediaCandidate]:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": "6",
            "gsrlimit": str(self.limit),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": "1600",
        }
        data = self._get_json("https://commons.wikimedia.org/w/api.php", params)
        pages = (data.get("query") or {}).get("pages") or []
        result: list[MediaCandidate] = []
        for page in pages:
            info = (page.get("imageinfo") or [{}])[0]
            mime = str(info.get("mime", ""))
            if not mime.startswith("image/"):
                continue
            meta = info.get("extmetadata") or {}
            license_raw = strip_html(str((meta.get("LicenseShortName") or {}).get("value", "")))
            if not license_allowed(license_raw):
                continue
            width = int(info.get("width", 0) or 0)
            height = int(info.get("height", 0) or 0)
            if width < self.min_width or height < self.min_height:
                continue
            download = str(info.get("thumburl") or info.get("url") or "")
            if not download:
                continue
            title = clean_text(str(page.get("title", "")).replace("File:", ""))
            description = strip_html(str((meta.get("ImageDescription") or {}).get("value", "")))
            author = strip_html(str((meta.get("Artist") or {}).get("value", ""))) or "Wikimedia contributor"
            license_url = strip_html(str((meta.get("LicenseUrl") or {}).get("value", "")))
            score = self._score(query, f"{title} {description}") + min(width, 4000) / 20000
            result.append(
                MediaCandidate(
                    provider="Wikimedia Commons",
                    title=title,
                    download_url=download,
                    source_page=str(info.get("descriptionurl") or ""),
                    author=author,
                    license=license_raw,
                    license_url=license_url,
                    width=width,
                    height=height,
                    description=description,
                    query=query,
                    score=score,
                )
            )
        return result

    def _openverse(self, query: str) -> list[MediaCandidate]:
        data = self._get_json(
            "https://api.openverse.org/v1/images/",
            {
                "q": query,
                "license": "cc0,pdm,by",
                "page_size": str(self.limit),
                "mature": "false",
            },
        )
        result: list[MediaCandidate] = []
        for item in data.get("results") or []:
            url = str(item.get("url") or "")
            if not url:
                continue
            license_name = str(item.get("license", "")).lower()
            if license_name not in {"cc0", "pdm", "by"}:
                continue
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
            if width < self.min_width or height < self.min_height:
                continue
            version = str(item.get("license_version", "") or "")
            if license_name == "pdm":
                license_url = "https://creativecommons.org/publicdomain/mark/1.0/"
            else:
                license_url = f"https://creativecommons.org/licenses/{license_name}/{version}/".rstrip("/") + "/"
            title = clean_text(str(item.get("title", "")))
            description = clean_text(str(item.get("description", "") or ""))
            author = clean_text(str(item.get("creator", "") or "")) or "Openverse contributor"
            score = self._score(query, f"{title} {description}") + min(width, 4000) / 20000
            result.append(
                MediaCandidate(
                    provider=f"Openverse/{item.get('source', 'source')}",
                    title=title or "Openverse image",
                    download_url=url,
                    source_page=str(item.get("foreign_landing_url") or ""),
                    author=author,
                    license=license_name.upper() + (f" {version}" if version else ""),
                    license_url=license_url,
                    width=width,
                    height=height,
                    description=description,
                    query=query,
                    score=score,
                )
            )
        return result

    @staticmethod
    def _score(query: str, text: str) -> float:
        q = content_tokens(query)
        t = content_tokens(text)
        if not q or not t:
            return 0.0
        return len(q & t) / max(1, len(q))

    # ------------------------------------------------------------------- download
    def _download(self, candidate: MediaCandidate, scene_id: str, dest_dir: Path) -> dict[str, Any] | None:
        url = candidate.download_url
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        cached = self.cache_dir / f"{key}.img"
        if not cached.exists() or cached.stat().st_size < 1024:
            try:
                response = self.session.get(url, timeout=60, stream=True)
                if response.status_code != 200:
                    self.logger.warning("Медиа %s вернуло HTTP %s", url, response.status_code)
                    return None
                content_type = response.headers.get("Content-Type", "")
                if content_type and not content_type.lower().startswith("image/"):
                    self.logger.warning("Медиа %s имеет тип %s", url, content_type)
                    return None
                temp = cached.with_suffix(".part")
                size = 0
                with temp.open("wb") as handle:
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise MediaError(f"Файл больше {self.max_bytes // 1024 // 1024} МБ: {url}")
                        handle.write(chunk)
                if size < 1024:
                    temp.unlink(missing_ok=True)
                    return None
                temp.replace(cached)
            except (requests.RequestException, OSError) as exc:
                self.logger.warning("Не удалось скачать медиа %s: %s", url, exc)
                cached.with_suffix(".part").unlink(missing_ok=True)
                return None

        try:
            with Image.open(cached) as image:
                image.load()
                if image.width < self.min_width or image.height < self.min_height:
                    return None
                image = ImageOps.exif_transpose(image).convert("RGB")
                prepared = ImageOps.contain(image, (2400, 2400), Image.Resampling.LANCZOS)
                dest_dir.mkdir(parents=True, exist_ok=True)
                image_path = dest_dir / f"{scene_id}.jpg"
                prepared.save(image_path, "JPEG", quality=92, optimize=True)
                hash_value = dhash(image_path)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            self.logger.warning("Битое изображение %s: %s", url, exc)
            return None

        return {
            "kind": "image",
            "image_path": image_path.as_posix(),
            "width": prepared.width,
            "height": prepared.height,
            "dhash": f"{hash_value:016x}",
            "candidate": candidate.to_dict(),
        }

    # -------------------------------------------------------------------- project
    @staticmethod
    def _query_for_scene(query: str, topic: str) -> str:
        """Keep specific queries, but add topic context to short generic ones."""
        value = clean_text(query).strip() or clean_text(topic).strip()
        if not value:
            return "abstract background"
        topic_tokens = content_tokens(topic)
        query_tokens = content_tokens(value)
        if topic_tokens and len(query_tokens) <= 2 and not (query_tokens & topic_tokens):
            value = f"{value} {topic}".strip()
        return value

    def fetch_for_script(
        self,
        script: Script,
        topic: str,
        profile: str,
        lang: str,
        workspace: Path,
    ) -> dict[str, Any]:
        manifest_path = workspace / "assets" / lang / "media_manifest.json"
        script_sha = hashlib.sha256(script.all_text.encode("utf-8")).hexdigest()
        if manifest_path.exists():
            try:
                import json

                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (
                    existing.get("script_sha") == script_sha
                    and existing.get("query_version") == MEDIA_QUERY_VERSION
                ):
                    if all(
                        item.get("kind") == "card"
                        or Path(str(item.get("image_path", ""))).exists()
                        for item in existing.get("scenes", [])
                    ):
                        self.logger.info("Медиа %s уже подобраны, используется кэш", lang)
                        return existing
            except (OSError, ValueError):
                pass

        used_hashes: list[int] = []
        scenes: list[dict[str, Any]] = []
        warnings: list[str] = []
        for scene in script.scenes:
            query = self._query_for_scene(scene.visual_query or scene.on_screen, topic)
            candidates = self.search(query, profile)
            chosen: dict[str, Any] | None = None
            for candidate in candidates:
                result = self._download(candidate, scene.id, workspace / "assets" / lang / "images")
                if not result:
                    continue
                value = int(result["dhash"], 16)
                if any((value ^ previous).bit_count() <= 5 for previous in used_hashes):
                    continue
                used_hashes.append(value)
                chosen = result
                chosen["scene_id"] = scene.id
                chosen["query"] = query
                break
            if not chosen:
                warnings.append(f"{scene.id}: не найдено подходящего медиа, будет программная графика.")
                chosen = {"scene_id": scene.id, "kind": "card", "query": query}
            scenes.append(chosen)

        manifest = {
            "lang": lang,
            "script_sha": script_sha,
            "query_version": MEDIA_QUERY_VERSION,
            "topic": topic,
            "profile": profile,
            "generated_at": now_iso(),
            "scenes": scenes,
            "warnings": warnings,
        }
        atomic_write_json(manifest_path, manifest)
        return manifest
