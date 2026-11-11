"""Каталог персонажей (этап C1).

Персонажи описываются данными, а не кодом: ключ, имя, стиль, база
скина, причёска, палитра, предпочитаемый голос.  Движок читает этот
каталог, чтобы строить сетки/скины/озвучку без захардкоженных веток.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

# --------------------------------------------------------------------------
# Спецификация
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CharacterSpecData:
    key: str
    display_name: str
    style: str = "blocky"
    hair_style: str = "blocky"
    skin_base: str = ""          # например "steve_mouth_closed.png"
    palette: Mapping[str, tuple[float, float, float]] = field(default_factory=dict)
    voice: Mapping[str, str] = field(default_factory=dict)   # lang -> onnx name
    default_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def skin_file(self) -> str:
        return self.skin_base or f"{self.key}_mouth_closed.png"

    def voice_for(self, lang: str) -> str | None:
        return self.voice.get(lang)


# --------------------------------------------------------------------------
# Каталог
# --------------------------------------------------------------------------

_SKINS_DIR = (
    Path(__file__).resolve().parents[3]
    / "blend3d" / "presets" / "textures" / "skins"
)
_PIPER_DIR = Path(__file__).resolve().parents[3] / ".." / ".." / ".." / "models" / "piper"

STEVE = CharacterSpecData(
    key="steve",
    display_name="Стив",
    skin_base="steve_mouth_closed.png",
    hair_style="blocky",
    palette={"skin": (0.72, 0.52, 0.36),
             "shirt": (0.0, 0.66, 0.66),
             "pants": (0.18, 0.24, 0.62),
             "hair": (0.26, 0.17, 0.10),
             "shoes": (0.35, 0.35, 0.38),
             "eye": (0.32, 0.44, 0.85)},
    voice={"ru": "ru_RU-dmitri-medium.onnx",
           "en": "en_US-ryan-high.onnx"},
)

ALEX = CharacterSpecData(
    key="alex",
    display_name="Алекс",
    skin_base="alex_mouth_closed.png",
    hair_style="blocky_long",
    palette={"skin": (0.85, 0.68, 0.55),
             "shirt": (0.35, 0.62, 0.28),
             "pants": (0.45, 0.32, 0.20),
             "hair": (0.72, 0.40, 0.16),
             "shoes": (0.45, 0.45, 0.48),
             "eye": (0.22, 0.42, 0.28)},
    voice={"ru": "ru_RU-irina-medium.onnx",
           "en": "en_US-libritts_r-medium.onnx"},
)

CHARACTERS: dict[str, CharacterSpecData] = {
    "steve": STEVE,
    "alex": ALEX,
}


def get_character(key: str) -> CharacterSpecData:
    try:
        return CHARACTERS[key]
    except KeyError as exc:
        raise KeyError(f"Неизвестный персонаж: {key}") from exc


def list_characters() -> list[str]:
    return sorted(CHARACTERS)


def resolve_skin_path(key: str, skins_dir: str | Path | None = None) -> Path | None:
    """Абсолютный путь к базовой текстуре (закрытый рот), если есть."""
    spec = get_character(key)
    base = Path(skins_dir) if skins_dir else _SKINS_DIR
    p = base / spec.skin_file
    return p if p.exists() else None


def resolve_voice_path(key: str, lang: str,
                      piper_dir: str | Path | None = None) -> Path | None:
    spec = get_character(key)
    name = spec.voice_for(lang)
    if not name:
        return None
    base = Path(piper_dir) if piper_dir else _PIPER_DIR
    p = base / name
    return p if p.exists() else None


def mouth_skin_bank(key: str, skins_dir: str | Path | None = None):
    """Возвращает ``MouthSkinBank`` для персонажа из каталога (C3).

    Работает для любого персонажа, у которого в папке скинов лежат
    ``{key}_mouth_closed/round/open.png`` — не только для Стива/Алекс.
    Импорт ленивый: MouthSkinBank не тянет тяжёлые зависимости.
    """
    from .skin_mouth import MouthSkinBank

    spec = get_character(key)
    base = Path(skins_dir) if skins_dir else _SKINS_DIR
    return MouthSkinBank(character=spec.key, skins_dir=base)
