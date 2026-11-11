"""A deliberately small, deterministic text -> story specification parser.

This is not an LLM and it does not pretend to understand arbitrary prose.  It
recognises the entities and verbs that are actually wired to the Factory
renderer, and raises a clear error for everything else.  The resulting spec is
turned into a Story Studio ``Project`` by the Story Studio worker.
"""

from __future__ import annotations

import re
from typing import Any

from .contracts import IntegrationError

CHARACTERS: dict[str, dict[str, Any]] = {
    "milo": {
        "id": "char_milo",
        "name": "Мило",
        "look": "milo",
        "role": "любопытный изобретатель",
        "aliases": ("мило", "milo"),
        "appearance": {
            "silhouette": "невысокий круглоголовый механик",
            "color_palette": ["#f6c453", "#3c6e71", "#f4f1de"],
            "clothing": "жёлтый комбинезон и очки",
            "distinctive_features": ["очки", "непослушный вихор"],
            "facial_structure": "круглое открытое лицо",
        },
        "personality": ["любопытный", "добрый", "немного неуклюжий"],
        "default_emotion": "curious",
        "voice_profile": {
            "timbre": "тёплый",
            "pace": "medium",
            "pitch": "medium",
            "speech_quirks": ["лёгкая одышка от волнения"],
        },
        "movement_style": "быстрый, чуть пружинящий шаг",
    },
    "nina": {
        "id": "char_nina",
        "name": "Нина",
        "look": "nina",
        "role": "спокойная исследовательница",
        "aliases": ("нина", "nina"),
        "appearance": {
            "silhouette": "высокая собранная фигура",
            "color_palette": ["#8e6bbf", "#efe6ff", "#2f2440"],
            "clothing": "фиолетовое платье с поясом",
            "distinctive_features": ["длинные волосы", "серьги-звёздочки"],
            "facial_structure": "удлинённое лицо с мягкими чертами",
        },
        "personality": ["внимательная", "спокойная", "благодарная"],
        "default_emotion": "curious",
        "voice_profile": {
            "timbre": "мягкий",
            "pace": "slow",
            "pitch": "medium",
            "speech_quirks": ["пауза перед удивлением"],
        },
        "movement_style": "плавный, уверенный шаг",
    },
}

PROPS: dict[str, dict[str, Any]] = {
    "orb": {
        "id": "prop_orb",
        "name": "светящийся шар",
        "kind": "orb",
        "description": "небольшой тёплый светящийся шар",
        "aliases": ("светящийся шар", "светящийся", "шар", "сфера", "orb"),
        "color": "#ffd257",
    }
}

ZONE_POSITIONS: dict[str, tuple[float, float]] = {
    "left_workbench": (0.22, 0.65),
    "center_floor": (0.50, 0.65),
    "right_shelf": (0.78, 0.65),
    "entrance": (0.50, 0.65),
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


def _mentioned(normalized: str, aliases: tuple[str, ...]) -> bool:
    return any(alias in normalized for alias in aliases)


def _quoted_lines(text: str) -> list[str]:
    pattern = r"[«\"„“]([^»\"”“]+)[»\"”“]"
    return [match.group(1).strip() for match in re.finditer(pattern, text) if match.group(1).strip()]


def detect_motion_profile(text: str) -> str:
    """Map a script's explicit staging words to a Lab trajectory family."""
    normalized = _norm(text)
    if any(word in normalized for word in ("по дуге", "дугой", "arc", "полукруг")):
        return "arc"
    if any(word in normalized for word in ("зигзаг", "через стол", "waypoint", "по точкам", "обходя")):
        return "waypoints"
    if any(word in normalized for word in ("плавно", "кривой", "криво", "curve", "bezier", "огибая")):
        return "bezier3"
    return "linear"


def _speaker_for_quote(text: str, quote: str, fallback: str) -> str:
    if quote not in text:
        return fallback
    before = text[: text.find(quote)]
    positions = [
        (before.lower().rfind(alias), character["id"])
        for character in CHARACTERS.values()
        for alias in character["aliases"]
    ]
    positions = [item for item in positions if item[0] >= 0]
    return max(positions)[1] if positions else fallback


def _actor_state(character: dict[str, Any], zone: str, position: tuple[float, float], emotion: str) -> dict[str, Any]:
    return {
        "character_id": character["id"],
        "location_id": "loc_workshop",
        "zone": zone,
        "position": {"x": position[0], "y": position[1]},
        "posture": "standing",
        "emotion": emotion,
        "holding_prop_ids": [],
        "present_in_scene": True,
    }


def _world_state(
    actors: list[dict[str, Any]],
    prop: dict[str, Any] | None,
    *,
    owner_id: str | None = None,
    prop_state: str = "resting",
    notes: str = "",
) -> dict[str, Any]:
    props = []
    if prop:
        props.append(
            {
                "prop_id": prop["id"],
                "location_id": "loc_workshop",
                "zone": "left_workbench",
                "position": {"x": 0.22, "y": 0.60},
                "owner_id": owner_id,
                "state": prop_state,
            }
        )
    return {"actors": actors, "props": props, "notes": notes}


def _action(
    action_id: str,
    actor_id: str,
    action_type: str,
    start: float,
    end: float,
    *,
    target_id: str | None = None,
    gaze_target: str = "forward",
    emotion: str = "focused",
    parameters: dict[str, Any] | None = None,
    dependencies: list[str] | None = None,
    completion_condition: str = "action completed",
) -> dict[str, Any]:
    return {
        "id": action_id,
        "actor_id": actor_id,
        "action_type": action_type,
        "start_time": round(start, 3),
        "end_time": round(end, 3),
        "parameters": parameters or {},
        "target_id": target_id,
        "preconditions": [],
        "dependencies": dependencies or [],
        "emotion": emotion,
        "gaze_target": gaze_target,
        "completion_condition": completion_condition,
    }


def parse_story_text(text: str, *, title: str | None = None) -> dict[str, Any]:
    """Build the integration story spec from a short natural-language script.

    Supported vocabulary is intentionally narrow: notice/look, approach, take,
    hand over, surprise, thanks and leave.  The supported demo vocabulary is
    documented in ``docs/INTEGRATION_LIMITATIONS.md``.
    """
    original = text.strip()
    if len(original) < 5:
        raise IntegrationError("Сценарий слишком короткий: нужно хотя бы одно предложение.")
    normalized = _norm(original)
    characters = [c for c in CHARACTERS.values() if _mentioned(normalized, c["aliases"])]
    if not characters:
        known = ", ".join(c["name"] for c in CHARACTERS.values())
        raise IntegrationError(
            "В сценарии не найден ни один поддерживаемый персонаж. "
            f"Доступны: {known}. Произвольные имена пока не поддерживаются."
        )
    prop = next((p for p in PROPS.values() if _mentioned(normalized, p["aliases"])), None)

    has_notice = any(word in normalized for word in ("замеча", "видит", "увидел", "notice", "spot"))
    has_approach = any(word in normalized for word in ("подход", "идет", "идёт", "направля", "approach", "walk"))
    has_take = any(word in normalized for word in ("берет", "берёт", "поднима", "take", "pick"))
    has_give = any(word in normalized for word in ("переда", "отда", "вруча", "give", "hand"))
    has_surprise = any(word in normalized for word in ("удивля", "вздрагива", "surprise", "amazed"))
    has_thanks = any(word in normalized for word in ("благодар", "спасибо", "thank"))
    has_speech = has_thanks or any(word in normalized for word in ("говорит", "сказал", "спрашивает", "отвечает", "speak"))
    has_return = any(word in normalized for word in ("возвращ", "обратно", "return"))
    has_leave = any(word in normalized for word in ("уход", "уйти", "покида", "leave", "exit"))
    has_look = any(word in normalized for word in ("смотр", "рассматрива", "look", "глядит"))

    if not any((has_notice, has_approach, has_take, has_give, has_surprise, has_thanks, has_speech, has_leave, has_look)):
        raise IntegrationError(
            "Не удалось разобрать ни одного поддерживаемого действия. "
            "Поддерживаются: замечает/смотрит, подходит, берёт, передаёт, удивляется, благодарит, уходят."
        )
    if (has_take or has_give) and prop is None:
        raise IntegrationError("Действие с предметом упомянуто, но поддерживаемый предмет не найден.")
    if has_give and not has_take:
        # "Передаёт" necessarily implies that the giver already holds the prop.
        has_take = True

    primary = characters[0]
    secondary = characters[1] if len(characters) > 1 else None
    if has_give and secondary is None:
        raise IntegrationError("Для передачи предмета нужны два поддерживаемых персонажа.")

    actions: list[dict[str, Any]] = []
    dialogue: list[dict[str, Any]] = []
    last_by_actor: dict[str, str] = {}

    def add(
        actor: dict[str, Any],
        kind: str,
        start: float,
        end: float,
        **kwargs: Any,
    ) -> dict[str, Any]:
        action_id = f"act_{actor['id'].removeprefix('char_')}_{kind}_{len(actions) + 1}"
        item = _action(
            action_id,
            actor["id"],
            kind,
            start,
            end,
            dependencies=[last_by_actor[actor["id"]]] if actor["id"] in last_by_actor else [],
            **kwargs,
        )
        actions.append(item)
        last_by_actor[actor["id"]] = action_id
        return item

    # Deterministic vertical-slice timeline.  It is intentionally a rule-based
    # template, not a claim of general natural-language understanding.
    cursor = 0.0
    if has_notice or has_look:
        add(
            primary,
            "look_at",
            0.0,
            0.8,
            target_id=prop["id"] if prop else secondary["id"] if secondary else "forward",
            gaze_target=prop["id"] if prop else "forward",
            parameters={"intensity": "focused"},
        )
        cursor = 0.8
    if secondary and (has_notice or has_look):
        add(
            secondary,
            "look_at",
            0.0,
            0.8,
            target_id=primary["id"],
            gaze_target=primary["id"],
            parameters={"intensity": "glance"},
        )

    if has_approach or has_take or has_give:
        add(
            primary,
            "walk",
            cursor,
            cursor + 1.4,
            parameters={
                "from_zone": "center_floor" if primary["id"] == "char_milo" else "right_shelf",
                "to_zone": "left_workbench" if prop else "center_floor",
                "speed": "normal",
                "steps": 5,
            },
            gaze_target=prop["id"] if prop else "forward",
            emotion="curious",
        )
        cursor += 1.4
        if secondary:
            add(
                secondary,
                "walk",
                0.8,
                2.2,
                parameters={
                    "from_zone": "right_shelf" if secondary["id"] == "char_nina" else "center_floor",
                    "to_zone": "center_floor",
                    "speed": "normal",
                    "steps": 4,
                },
                gaze_target=primary["id"],
                emotion="curious",
            )
        cursor = max(cursor, 2.2)

    if has_take and prop:
        add(
            primary,
            "pick_up",
            cursor,
            cursor + 0.8,
            target_id=prop["id"],
            gaze_target=prop["id"],
            parameters={"hand": "right", "grip_style": "careful"},
            emotion="curious",
        )
        cursor += 0.8
        add(
            primary,
            "walk",
            cursor,
            cursor + 1.2,
            parameters={
                "from_zone": "left_workbench",
                "to_zone": "center_floor",
                "speed": "normal",
                "steps": 3,
            },
            gaze_target=secondary["id"] if secondary else "forward",
            emotion="happy",
        )
        cursor += 1.2

    if has_give and prop and secondary:
        give_start = cursor
        give_end = cursor + 0.8
        add(
            primary,
            "give",
            give_start,
            give_end,
            target_id=secondary["id"],
            gaze_target=secondary["id"],
            parameters={"prop_id": prop["id"], "receiver_id": secondary["id"]},
            emotion="happy",
        )
        add(
            secondary,
            "receive",
            give_start,
            give_start + 0.7,
            target_id=primary["id"],
            gaze_target=prop["id"],
            parameters={"prop_id": prop["id"], "giver_id": primary["id"]},
            emotion="surprised",
        )
        cursor = give_end
        if has_return:
            cursor += 0.3
            return_start = cursor
            return_end = cursor + 0.8
            add(
                secondary,
                "give",
                return_start,
                return_end,
                target_id=primary["id"],
                gaze_target=primary["id"],
                parameters={"prop_id": prop["id"], "receiver_id": primary["id"]},
                emotion="happy",
            )
            add(
                primary,
                "receive",
                return_start,
                return_start + 0.7,
                target_id=secondary["id"],
                gaze_target=prop["id"],
                parameters={"prop_id": prop["id"], "giver_id": secondary["id"]},
                emotion="surprised",
            )
            cursor = return_end

    if has_surprise and secondary:
        add(
            secondary,
            "react",
            cursor,
            cursor + 0.5,
            gaze_target=prop["id"] if prop else primary["id"],
            parameters={"reaction_style": "amazed_gasp", "intensity": 0.8},
            emotion="surprised",
        )
        cursor += 0.5
    if has_speech and secondary:
        lines = _quoted_lines(original)
        if not lines:
            lines = ["Спасибо тебе!" if has_thanks else "Смотри, как красиво!"]
        for index, line_text in enumerate(lines[:4], start=1):
            speaker_id = _speaker_for_quote(original, line_text, secondary["id"])
            speaker = next((c for c in characters if c["id"] == speaker_id), secondary)
            listener = primary if speaker is not primary else secondary
            line_duration = max(1.0, min(2.5, 0.55 + len(line_text) / 18))
            dialogue_id = f"dlg_{index}"
            dialogue.append(
                {
                    "id": dialogue_id,
                    "character_id": speaker["id"],
                    "text": line_text,
                    "language": "ru",
                    "start_time": round(cursor, 3),
                    "end_time": round(cursor + line_duration, 3),
                    "delivery": "warm",
                    "emotion": "happy" if has_thanks else "curious",
                    "expected_duration": round(line_duration, 3),
                    "pause_after": 0.15,
                }
            )
            add(
                speaker,
                "speak",
                cursor,
                cursor + line_duration,
                gaze_target=listener["id"],
                parameters={"dialogue_id": dialogue_id, "gesture_emphasis": "subtle"},
                emotion="happy" if has_thanks else "curious",
            )
            cursor += line_duration + 0.15
            if speaker is not primary and index == len(lines[:4]):
                add(
                    primary,
                    "react",
                    cursor,
                    cursor + 0.4,
                    gaze_target=speaker["id"],
                    parameters={"reaction_style": "proud_chest_up", "intensity": 0.7},
                    emotion="happy",
                )
                cursor += 0.4

    if has_leave:
        exit_start = cursor
        for actor in characters:
            add(
                actor,
                "exit",
                exit_start,
                exit_start + 1.7,
                gaze_target="forward",
                parameters={"exit_zone": "entrance"},
                emotion="happy",
            )
        cursor += 1.7

    if not actions:
        raise IntegrationError("Сценарий не содержит действий, которые можно передать в рендерер.")

    duration = max(5.0, round(cursor + 0.3, 2))
    initial_actors = []
    for character in characters:
        if character["id"] == "char_milo":
            zone, position = "center_floor", ZONE_POSITIONS["center_floor"]
        else:
            zone, position = "right_shelf", ZONE_POSITIONS["right_shelf"]
        initial_actors.append(_actor_state(character, zone, position, character["default_emotion"]))
    ending_actors = []
    final_owner_id: str | None = None
    if prop and has_give and secondary:
        final_owner_id = primary["id"] if has_return else secondary["id"]
    elif prop and has_take:
        final_owner_id = primary["id"]
    for character in characters:
        state = _actor_state(character, "entrance", ZONE_POSITIONS["entrance"], "happy")
        state["present_in_scene"] = not has_leave
        if prop and character["id"] == final_owner_id:
            state["holding_prop_ids"] = [prop["id"]]
        ending_actors.append(state)

    return {
        "title": title or ("Светящийся шар" if prop else "Короткая история"),
        "logline": original[:160],
        "synopsis": original,
        "language": "ru",
        "duration": duration,
        "genre": "comedy" if has_speech else "drama",
        "characters": [
            {
                **{key: value for key, value in character.items() if key not in ("aliases", "look")},
                "look": character["look"],
                "initial_zone": "center_floor" if character["id"] == "char_milo" else "right_shelf",
            }
            for character in characters
        ],
        "location": {
            "id": "loc_workshop",
            "name": "Мастерская",
            "description": "Тёплая мастерская со столом, полками и мягким светом.",
            "lighting": "тёплый вечерний свет",
            "atmosphere": "уютная и немного волшебная",
            "spatial_zones": ["left_workbench", "center_floor", "right_shelf", "entrance"],
            "prop_ids": [prop["id"]] if prop else [],
        },
        "prop": (
            {
                "id": prop["id"],
                "name": prop["name"],
                "description": prop["description"],
                "visual_attributes": ["светится", "тёплый жёлтый свет", "гладкая поверхность"],
                "interactive": True,
                "initial_location_id": "loc_workshop",
                "initial_owner_id": None,
                "initial_state": "resting",
                "factory_kind": prop["kind"],
                "color": prop["color"],
            }
            if prop
            else None
        ),
        "actions": actions,
        "dialogue": dialogue,
        "initial_state": _world_state(initial_actors, prop),
        "ending_state": _world_state(
            ending_actors,
            prop,
            owner_id=final_owner_id,
            prop_state="held" if final_owner_id else "resting",
            notes="Финальное состояние: персонажи ушли, шар у получателя." if has_leave else "Финальное состояние сцены.",
        ),
    }
