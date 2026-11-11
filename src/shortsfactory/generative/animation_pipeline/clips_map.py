"""Map canonical semantic actions to available imported clips.

Mission 11: normalise the huge set of clip names into the small set of
semantic actions the pipeline uses (idle, walk, run, turn, stop, bend,
reach, pickup, hold, inspect, react, jump, fall).  Matching is a simple,
documented substring heuristic; anything unmatched is reported honestly.
"""

from __future__ import annotations

#: canonical action -> list of clip-name substrings that qualify.
SEMANTIC_ALIASES: dict[str, tuple[str, ...]] = {
    "idle": ("idle", "static"),
    "walk": ("walk",),
    "run": ("sprint", "run"),
    "turn": ("turn", "rotate"),
    "stop": ("stop",),
    "bend": ("sit", "crouch", "bend"),
    "reach": ("reach", "interact", "point"),
    "pickup": ("pick-up", "pickup", "pick_up", "grab"),
    "hold": ("holding-both", "holding-left", "holding-right", "hold"),
    "inspect": ("inspect", "look", "examine"),
    "react": ("emote-yes", "emote-no", "emote", "react", "wave", "cheer"),
    "jump": ("jump",),
    "fall": ("fall", "die"),
    "attack": ("attack", "melee", "kick"),
}


def semantic_clip_map(clip_names: list[str]) -> dict[str, list[str]]:
    """Return {canonical_action: [clip_names...]} for available clips."""
    result: dict[str, list[str]] = {}
    for action, aliases in SEMANTIC_ALIASES.items():
        hits = [n for n in clip_names if any(al in n.lower() for al in aliases)]
        if hits:
            result[action] = hits
    return result


def find_clip(clip_names: list[str], action: str) -> str | None:
    """Return the best clip name for a canonical action, or None."""
    mapping = semantic_clip_map(clip_names)
    hits = mapping.get(action)
    if not hits:
        return None
    # prefer the shortest name (usually the canonical clip itself)
    return sorted(hits, key=len)[0]
