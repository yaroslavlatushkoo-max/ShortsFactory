# blend3d/host/film_spec.py — declarative film specification, validation and
# deterministic shot planning from a plain-text idea (first automation layer).
# Pure python: no bpy, no Blender required.
from dataclasses import dataclass, field
import json

FPS = 30

BEATS = {
    # keyword → (beat_id, title, description, mood, camera_hint)
    "шахта": ("mine", "шахта", "герои спускаются в подземелье", "curious", "descend"),
    "пещер": ("mine", "пещера", "герои спускаются в подземелье", "curious", "descend"),
    "коп": ("mine", "добыча", "добыча ресурсов", "focused", "insert"),
    "алмаз": ("treasure", "находка", "находка ценного ресурса", "excited", "push_in"),
    "клад": ("treasure", "находка", "находка ценного ресурса", "excited", "push_in"),
    "храм": ("treasure", "храм", "древнее сооружение", "awed", "push_in"),
    "голем": ("threat", "пробуждение", "древний страж пробуждается", "tense", "low_angle"),
    "лав": ("danger", "опасность", "лава и обвал", "tense", "wide_shake"),
    "опасн": ("danger", "опасность", "угроза", "tense", "wide_shake"),
    "паден": ("danger", "срыв", "кто-то срывается", "tense", "wide_shake"),
    "спас": ("rescue", "спасение", "напарник спасает", "relieved", "two_shot"),
    "друж": ("bond", "дружба", "момент доверия", "warm", "two_shot"),
    "подар": ("bond", "подарок", "дарит находку", "warm", "medium"),
    "закат": ("ending", "финал", "герои уходят вместе", "peaceful", "pull_back"),
    "рассвет": ("ending", "финал", "герои уходят вместе", "peaceful", "pull_back"),
    "вместе": ("ending", "финал", "герои уходят вместе", "peaceful", "pull_back"),
}

@dataclass
class Beat:
    beat_id: str
    title: str
    mood: str
    camera_hint: str
    f0: int
    f1: int

@dataclass
class FilmSpec:
    title: str = "Короткометражка"
    idea: str = ""
    fps: int = FPS
    resolution: tuple = (1080, 1920)
    frames: int = 1500
    characters: list = field(default_factory=lambda: ["steve", "alex"])
    palette: str = "minecraft_night"
    lighting: str = "cave_night"
    beats: list = field(default_factory=list)

    def to_json(self) -> str:
        d = dict(self.__dict__)
        d["resolution"] = list(self.resolution)
        d["beats"] = [b.__dict__ for b in self.beats]
        return json.dumps(d, ensure_ascii=False, indent=1)

    @staticmethod
    def from_json(text: str) -> "FilmSpec":
        d = json.loads(text)
        beats = [Beat(**b) for b in d.pop("beats", [])]
        d["resolution"] = tuple(d.get("resolution", (1080, 1920)))
        spec = FilmSpec(beats=beats, **{k: v for k, v in d.items() if k in FilmSpec.__dataclass_fields__})
        validate(spec)
        return spec

def plan_from_idea(idea: str, duration_s: float = 50.0, title: str = "Короткометражка") -> FilmSpec:
    """Deterministic planner: idea text → ordered beats with shot hints.
    First automation layer; LLM-based planning plugs in above this."""
    text = (idea or "").lower()
    found = []
    for key, beat in BEATS.items():
        if key in text and beat[0] not in [b[0] for b in found]:
            found.append(beat)
    if not found:
        found = [BEATS["шахта"], BEATS["алмаз"], BEATS["спас"], BEATS["закат"]]
    mine_beat = next(b for b in BEATS.values() if b[0] == "mine")
    ending_beat = next(b for b in BEATS.values() if b[0] == "ending")
    if found[0][0] != "mine":
        found.insert(0, mine_beat)              # establish the world first
    if found[-1][0] != "ending":
        found.append(ending_beat)               # always resolve
    total = int(duration_s * FPS)
    n = len(found)
    bounds = [1 + total * i // n for i in range(n)] + [total + 1]
    beats = [Beat(b[0], b[1], b[2], b[3], bounds[i], bounds[i + 1] - 1)
             for i, b in enumerate(found)]
    return FilmSpec(title=title, idea=idea, frames=total, beats=beats)

def validate(spec: FilmSpec):
    if spec.fps <= 0: raise ValueError("fps must be positive")
    if spec.resolution not in ((1080, 1920), (720, 1280)): raise ValueError("unsupported resolution")
    if spec.frames < 10: raise ValueError("too short")
    if not spec.characters: raise ValueError("need at least one character")
    for b in spec.beats:
        if b.f1 < b.f0: raise ValueError(f"beat {b.beat_id}: f1 < f0")
    prev = 1
    for b in spec.beats:
        if b.f0 < prev: raise ValueError(f"beat {b.beat_id}: overlapping frames")
        prev = b.f1 + 1
    return True
