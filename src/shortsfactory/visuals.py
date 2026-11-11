from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .config import Config
from .logging_setup import get_logger
from .models import Scene, Script
from .util import atomic_write_json, clean_text, extract_numbers, now_iso


class VisualError(RuntimeError):
    pass


WIDTH, HEIGHT = 1080, 1920
SUBTITLE_SAFE = 360


def _rgb(value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    try:
        return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[index] + (b[index] - a[index]) * t) for index in range(3))  # type: ignore[return-value]


def _vertical_gradient(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    array = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for y in range(HEIGHT):
        array[y, :, :] = _mix(top, bottom, y / max(1, HEIGHT - 1))
    return Image.fromarray(array, "RGB")


def _dark_gradient(alpha_top: int, alpha_bottom: int) -> Image.Image:
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    pixels = overlay.load()
    for y in range(HEIGHT):
        if y < HEIGHT * 0.32:
            alpha = int(alpha_top * (1 - y / (HEIGHT * 0.32)))
        elif y > HEIGHT * 0.55:
            alpha = int(alpha_bottom * ((y - HEIGHT * 0.55) / (HEIGHT * 0.45)))
        else:
            alpha = 0
        for x in range(WIDTH):
            pixels[x, y] = (4, 6, 12, max(0, min(255, alpha)))
    return overlay


class VisualService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = get_logger()
        self.font_path = config.fonts_dir / "Montserrat-ExtraBold.ttf"
        self.semi_path = config.fonts_dir / "Montserrat-SemiBold.ttf"
        if not self.font_path.exists():
            raise VisualError(f"Не найден шрифт: {self.font_path}")
        self._fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    # --------------------------------------------------------------------- fonts
    def _font(self, size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
        path = self.font_path if bold else self.semi_path
        key = (str(path), size)
        if key not in self._fonts:
            self._fonts[key] = ImageFont.truetype(str(path), size)
        return self._fonts[key]

    def _wrap(
        self, text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int
    ) -> list[str] | None:
        words = clean_text(text).split()
        if not words:
            return []
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if font.getlength(candidate) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        if len(lines) > max_lines or any(font.getlength(line) > max_width for line in lines):
            return None
        return lines

    def _fit_lines(
        self,
        text: str,
        max_width: int,
        max_lines: int,
        start_size: int,
        min_size: int = 36,
        bold: bool = True,
    ) -> tuple[list[str], ImageFont.FreeTypeFont, int]:
        size = start_size
        while size >= min_size:
            font = self._font(size, bold)
            lines = self._wrap(text, font, max_width, max_lines)
            if lines:
                return lines, font, size
            size -= 4
        font = self._font(min_size, bold)
        words = clean_text(text).split()
        if not words:
            return [], font, min_size
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if font.getlength(candidate) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            tail = lines[-1]
            while tail and font.getlength(tail + "…") > max_width:
                tail = tail.rsplit(" ", 1)[0] if " " in tail else ""
            lines[-1] = (tail.rstrip(" ,.;:") + "…") if tail else "…"
        return lines, font, min_size

    def _draw_center(
        self,
        draw: ImageDraw.ImageDraw,
        lines: list[str],
        font: ImageFont.FreeTypeFont,
        cx: int,
        cy: int,
        fill: tuple[int, int, int, int],
        spacing: float = 1.12,
    ) -> None:
        line_height = int(font.size * spacing)
        total = line_height * len(lines)
        y = cy - total // 2
        for line in lines:
            draw.text((cx, y), line, font=font, anchor="ma", fill=fill)
            y += line_height

    # --------------------------------------------------------------------- cards
    def _card(self, scene: Scene, profile: dict[str, Any], template: str, index: int, total: int) -> Image.Image:
        palette = profile.get("palette") or ["#07111F", "#102A43", "#4FC3F7", "#F5C542", "#FFFFFF"]
        colors = [_rgb(str(item), (10, 20, 35)) for item in palette]
        while len(colors) < 5:
            colors.append(colors[-1])
        base = _vertical_gradient(colors[0], colors[1])
        overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Soft architecture: rings or diagonal bands, deterministic per scene.
        seed = int(hashlib.sha256(scene.id.encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        if template == "diagram":
            cx, cy = WIDTH // 2, 830
            for radius, alpha in ((430, 40), (330, 55), (230, 70), (135, 90)):
                draw.ellipse(
                    (cx - radius, cy - radius, cx + radius, cy + radius),
                    outline=(*colors[2], alpha),
                    width=2,
                )
            for _ in range(14):
                angle = float(rng.uniform(0, 6.283))
                radius = float(rng.uniform(180, 430))
                x = int(cx + np.cos(angle) * radius)
                y = int(cy + np.sin(angle) * radius)
                draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(*colors[3], 190))
        else:
            for offset in range(-300, WIDTH + 300, 120):
                draw.line(
                    (offset, HEIGHT, offset + 520, 0),
                    fill=(*colors[2], 16),
                    width=2,
                )
            draw.ellipse((-260, 1120, 520, 1900), fill=(*colors[2], 22))
            draw.ellipse((820, 120, 1320, 620), fill=(*colors[3], 18))

        base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(base)
        label = str(profile.get("label_en" if self._is_ascii(scene.on_screen) else "label_ru", ""))
        kicker = f"{label.upper()} · {index:02d}/{total:02d}"
        draw.text((80, 120), kicker, font=self._font(30, False), fill=(210, 220, 235))

        accent = colors[3]
        draw.rectangle((80, 190, 80 + 120, 196), fill=accent)

        if template == "stat":
            numbers = sorted(extract_numbers(scene.text), key=len, reverse=True)
            big = numbers[0] if numbers else ""
            if big:
                size = 280 if len(big) <= 4 else 210
                font = self._font(size)
                draw.text((WIDTH // 2, 760), big, font=font, anchor="mm", fill=(255, 255, 255))
                label_lines, label_font, _ = self._fit_lines(
                    scene.on_screen, 860, 2, 66, 42, bold=False
                )
                self._draw_center(draw, label_lines, label_font, WIDTH // 2, 1010, (*colors[2], 255))
                draw.line((300, 1120, 780, 1120), fill=(*accent, 220), width=4)
            else:
                lines, font, _ = self._fit_lines(scene.on_screen, 880, 3, 116, 68)
                self._draw_center(draw, lines, font, WIDTH // 2, 860, (255, 255, 255))
        elif template == "timeline":
            years = sorted(extract_numbers(scene.text), key=lambda value: len(value), reverse=True)[:3]
            if len(years) < 2:
                years = ["01", "02", "03"]
            draw.line((160, 930, 920, 930), fill=(*colors[2], 220), width=5)
            for index_y, year in enumerate(years):
                x = int(160 + index_y * (760 / max(1, len(years) - 1)))
                draw.ellipse((x - 16, 914, x + 16, 946), fill=accent)
                draw.text((x, 820), year, font=self._font(58), anchor="mm", fill=(255, 255, 255))
            lines, font, _ = self._fit_lines(scene.on_screen, 820, 2, 72, 44)
            self._draw_center(draw, lines, font, WIDTH // 2, 1090, (255, 255, 255))
        elif template == "quote":
            draw.text((90, 470), "“", font=self._font(260), fill=(*accent, 180))
            lines, font, _ = self._fit_lines(scene.on_screen or scene.text, 840, 4, 104, 56)
            self._draw_center(draw, lines, font, WIDTH // 2, 900, (255, 255, 255))
            draw.line((340, 1200, 740, 1200), fill=(*colors[2], 220), width=4)
        elif template == "diagram":
            lines, font, _ = self._fit_lines(scene.on_screen, 760, 3, 100, 58)
            self._draw_center(draw, lines, font, WIDTH // 2, 830, (255, 255, 255))
            draw.text(
                (WIDTH // 2, 1190),
                "SCIENCE / EXPLAINED",
                font=self._font(30, False),
                anchor="mm",
                fill=(*colors[2], 230),
            )
        else:
            lines, font, _ = self._fit_lines(scene.on_screen or scene.text, 870, 4, 124, 62)
            self._draw_center(draw, lines, font, WIDTH // 2, 850, (255, 255, 255))
            draw.text(
                (WIDTH // 2, 1120),
                label.upper()[:40],
                font=self._font(32, False),
                anchor="mm",
                fill=(*colors[2], 230),
            )

        draw.rectangle((0, HEIGHT - 14, WIDTH, HEIGHT), fill=accent)
        return base

    @staticmethod
    def _is_ascii(text: str) -> bool:
        return not re.search(r"[\u0400-\u04FF]", text or "")

    # -------------------------------------------------------------------- images
    def _image_background(self, image_path: Path, template: str, accent: tuple[int, int, int]) -> Image.Image:
        try:
            with Image.open(image_path) as source:
                source.load()
                image = ImageOps.exif_transpose(source).convert("RGB")
        except (OSError, ValueError) as exc:
            raise VisualError(f"Не открывается изображение {image_path}: {exc}") from exc
        image = ImageEnhance.Color(image).enhance(1.06)
        image = ImageEnhance.Contrast(image).enhance(1.04)
        cover = ImageOps.fit(image, (WIDTH, HEIGHT), Image.Resampling.LANCZOS, centering=(0.5, 0.42))
        if template == "frame":
            background = cover.filter(ImageFilter.GaussianBlur(28))
            background = ImageEnhance.Brightness(background).enhance(0.45)
            foreground = ImageOps.contain(image, (920, 980), Image.Resampling.LANCZOS)
            x = (WIDTH - foreground.width) // 2
            y = 470
            shadow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(shadow)
            shadow_draw.rounded_rectangle(
                (x - 20, y - 20, x + foreground.width + 20, y + foreground.height + 20),
                radius=36,
                fill=(0, 0, 0, 150),
            )
            shadow = shadow.filter(ImageFilter.GaussianBlur(24))
            background = Image.alpha_composite(background.convert("RGBA"), shadow).convert("RGB")
            mask = Image.new("L", foreground.size, 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle((0, 0, foreground.width, foreground.height), radius=26, fill=255)
            background.paste(foreground, (x, y), mask)
            draw = ImageDraw.Draw(background)
            draw.rounded_rectangle(
                (x - 3, y - 3, x + foreground.width + 3, y + foreground.height + 3),
                radius=29,
                outline=(*accent, 255),
                width=3,
            )
        else:
            background = cover
        background = Image.alpha_composite(
            background.convert("RGBA"), _dark_gradient(155, 235)
        ).convert("RGB")
        return background

    # -------------------------------------------------------------------- compose
    def build(
        self,
        script: Script,
        media_manifest: dict[str, Any],
        profile: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        lang = script.lang
        out_dir = workspace / "assets" / lang / "backgrounds"
        out_dir.mkdir(parents=True, exist_ok=True)
        script_sha = hashlib.sha256(script.all_text.encode("utf-8")).hexdigest()
        media_signature = hashlib.sha256(
            "\n".join(
                f"{item.get('scene_id')}|{item.get('kind')}|{item.get('image_path')}|{item.get('dhash')}"
                for item in media_manifest.get("scenes", [])
            ).encode("utf-8")
        ).hexdigest()
        manifest_path = workspace / "assets" / lang / "visuals.json"
        if manifest_path.exists():
            try:
                import json

                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (
                    existing.get("script_sha") == script_sha
                    and existing.get("media_sha") == media_signature
                    and all(
                        Path(item.get("background", "")).exists()
                        for item in existing.get("scenes", [])
                    )
                ):
                    for item in existing.get("scenes", []):
                        scene = next((s for s in script.scenes if s.id == item["scene_id"]), None)
                        if scene:
                            scene.template = item.get("template", scene.template)
                    self.logger.info("Визуалы %s уже готовы, используется кэш", lang)
                    return existing
            except (OSError, ValueError):
                pass

        media_by_id = {str(item.get("scene_id")): item for item in media_manifest.get("scenes", [])}
        palette = profile.get("palette") or ["#07111F", "#102A43", "#4FC3F7", "#F5C542", "#FFFFFF"]
        accent = _rgb(str(palette[3] if len(palette) > 3 else "#F5C542"), (245, 197, 66))
        warnings: list[str] = []
        scenes: list[dict[str, Any]] = []

        for index, scene in enumerate(script.scenes, 1):
            media = media_by_id.get(scene.id, {})
            has_image = media.get("kind") == "image"
            image_path = Path(str(media.get("image_path", "")))
            if has_image and not image_path.exists():
                has_image = False
            template = self.choose_template(scene, index, len(script.scenes), profile, has_image)
            try:
                if has_image:
                    background = self._image_background(image_path, template, accent)
                else:
                    background = self._card(scene, profile, template, index, len(script.scenes))
            except VisualError:
                background = self._card(scene, profile, "title", index, len(script.scenes))
                template = "card_title"
                warnings.append(f"{scene.id}: изображение не удалось обработать, создана карточка.")
            scene.template = template
            background_path = out_dir / f"{scene.id}.png"
            background.save(background_path, "PNG", optimize=True)
            scenes.append(
                {
                    "scene_id": scene.id,
                    "template": template,
                    "background": background_path.as_posix(),
                    "kind": "image" if has_image else "card",
                    "image_path": image_path.as_posix() if has_image else "",
                    "credit": (media.get("candidate") or {}).get("attribution", ""),
                    "query": media.get("query", ""),
                    "license": (media.get("candidate") or {}).get("license", ""),
                }
            )

        manifest = {
            "lang": lang,
            "script_sha": script_sha,
            "media_sha": media_signature,
            "generated_at": now_iso(),
            "scenes": scenes,
            "warnings": warnings,
        }
        atomic_write_json(manifest_path, manifest)
        return manifest

    @staticmethod
    def choose_template(
        scene: Scene, index: int, total: int, profile: dict[str, Any], has_image: bool
    ) -> str:
        style = str(profile.get("style", "explainer"))
        if has_image:
            if style in {"historical", "documentary"} and index % 4 == 2:
                return "frame"
            if style in {"scientific", "explainer"} and index % 4 == 3:
                return "frame"
            return "hero"
        years = [value for value in extract_numbers(scene.text) if re.fullmatch(r"(1[5-9]\d\d|20\d\d)", value)]
        numbers = extract_numbers(scene.text)
        if style == "historical" and len(years) >= 2:
            return "timeline"
        if numbers and style in {"scientific", "explainer", "tech"} and index % 3 != 0:
            return "stat"
        if style in {"scientific", "tech", "explainer"} and index % 2 == 0:
            return "diagram"
        if len(scene.on_screen) > 18:
            return "quote"
        return "title"
