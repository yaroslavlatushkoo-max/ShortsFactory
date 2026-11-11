"""Engine dispatcher.

Chooses a local rendering executor for a unified :class:`AnimationPlan`:

* ``procedural`` → built-in 2D Animation Engine (always available, offline);
* ``blender`` → optional Blender 3D engine (only if Blender is installed);
* ``voxel`` / ``asset`` → optional Blender voxel engines;
* ``skinned`` → continuous skinned character rig with leg IK
  (:mod:`.animation_pipeline.skinned_hero`), Blender required;
* ``auto`` → Blender when available, otherwise Animation Engine.

The dispatcher never touches cloud providers.  When the requested engine cannot
render (for example Blender is missing) it reports an honest ``PARTIAL``/``FAILED``
status and, only for ``auto``, falls back to the Animation Engine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .animation_plan import (
    ENGINE_ASSET,
    ENGINE_AUTO,
    ENGINE_BLENDER,
    ENGINE_PROCEDURAL,
    ENGINE_SKINNED,
    ENGINE_VOXEL,
    AnimationPlan,
    PlanError,
)

try:  # importable in any environment
    from .blender_adapter import (
        BlenderResult,
        blender_available,
        build_blender_script,
        render_blender,
    )
except Exception:  # pragma: no cover - defensive import guard
    BlenderResult = None  # type: ignore
    blender_available = lambda: False  # type: ignore
    build_blender_script = None  # type: ignore
    render_blender = None  # type: ignore

try:  # importable in any environment
    from .voxel_engine.voxel_renderer import render_voxel
except Exception:  # pragma: no cover - defensive import guard
    render_voxel = None  # type: ignore

try:  # importable in any environment
    from .voxel_engine.asset_pipeline import render_asset
except Exception:  # pragma: no cover - defensive import guard
    render_asset = None  # type: ignore

try:  # importable in any environment
    from .animation_pipeline.skinned_hero import (
        build_scene_script,
        build_skinned_script,
        render_skinned,
    )
except Exception:  # pragma: no cover - defensive import guard
    build_scene_script = None  # type: ignore
    build_skinned_script = None  # type: ignore
    render_skinned = None  # type: ignore


@dataclass
class DispatchResult:
    status: str  # OK | PARTIAL | FAILED
    engine_used: str = ""
    video: str = ""
    contact_sheet: str = ""
    scene_path: str = ""
    characters_path: str = ""
    report_json: str = ""
    render_dir: str = ""
    blender_script: str = ""
    notes: list[str] = field(default_factory=list)
    error: str = ""
    #: The raw AnimationResult (procedural path) for downstream QC.
    animation_result: Any = None


def _skinned_targets(plan: AnimationPlan) -> tuple[str, str]:
    """Pick the rigged character and the plan character driving ``ENGINE_SKINNED``.

    Returns ``(rig_key, plan_character_id)``.  The rig only knows Milo and Nina,
    so an unrecognised plan character is rendered as Milo and the caller has to
    say so in the notes instead of pretending the match was exact.
    """
    for character in plan.characters:
        label = f"{character.id} {character.name}".lower()
        if "nina" in label:
            return "nina", character.id
        if "milo" in label:
            return "milo", character.id
    return ("milo", plan.characters[0].id) if plan.characters else ("milo", "")


def _skinned_cast(plan: AnimationPlan) -> list[tuple[str, str]]:
    """Plan characters that the skinned rig can actually play, in plan order."""
    cast: list[tuple[str, str]] = []
    for character in plan.characters:
        label = f"{character.id} {character.name}".lower()
        if "nina" in label:
            cast.append(("nina", character.id))
        elif "milo" in label:
            cast.append(("milo", character.id))
    return cast


#: Rough speaking rate (characters per second) used to estimate how long a line
#: lasts.  Free-create renders *before* Piper synthesises the audio, so the rig
#: cannot know the real duration; the estimate is clamped and documented.
VOICE_CHARS_PER_SEC = 13.0


def _skinned_voice_lines(
    plan: AnimationPlan, character_id: str, *, limit: float = 0.0
) -> list[dict[str, Any]]:
    """Timed talking cues for the mouth rig, estimated from the line's length."""
    lines = [
        line
        for line in plan.voice_lines
        if not line.character or line.character == character_id or len(plan.characters) == 1
    ]
    out: list[dict[str, Any]] = []
    for line in lines:
        text = str(line.text or "").strip()
        if not text:
            continue
        start = max(0.0, float(line.start))
        duration = min(8.0, max(0.6, len(text) / VOICE_CHARS_PER_SEC))
        if limit > 0:
            duration = max(1.0 / max(1, plan.fps), min(duration, max(0.0, limit - start)))
        out.append({"start": round(start, 3), "duration": round(duration, 3), "text": text})
    return out


def _skinned_actions(plan: AnimationPlan, character_id: str) -> list[dict[str, Any]]:
    """Convert the plan's actions for one character into neutral timeline dicts."""
    chosen = [a for a in plan.actions if a.character == character_id]
    if not chosen:  # single-character fallback: do not silently drop the plan
        chosen = list(plan.actions)
    return [
        {
            "action": action.action,
            "start": float(action.start),
            "duration": float(action.duration),
            "params": dict(action.params),
        }
        for action in chosen
    ]


def render_plan(
    plan: AnimationPlan,
    config: Any,
    out_dir: str | Path,
    *,
    engine: str = ENGINE_AUTO,
) -> DispatchResult:
    """Render a plan with the selected local engine."""
    plan.ensure()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if engine not in (
        ENGINE_PROCEDURAL,
        ENGINE_BLENDER,
        ENGINE_VOXEL,
        ENGINE_ASSET,
        ENGINE_SKINNED,
        ENGINE_AUTO,
    ):
        raise PlanError(f"Неизвестный движок: {engine}.")

    if engine == ENGINE_ASSET:
        if render_asset is None or not blender_available():
            return DispatchResult(
                status="PARTIAL",
                engine_used=ENGINE_ASSET,
                render_dir=str(out_dir),
                notes=[
                    "Движок asset запрошен, но Blender не найден/недоступен. "
                    "Проверьте PATH (D:\\Blender) или BLENDER_EXECUTABLE."
                ],
            )
        from .voxel_engine.assets_catalog import AssetCatalog
        try:
            asset_res = render_asset(plan, out_dir, catalog=AssetCatalog.load())
        except Exception as exc:
            return DispatchResult(
                status="FAILED",
                engine_used=ENGINE_ASSET,
                render_dir=str(out_dir),
                error=str(exc)[-500:],
                notes=[f"Ассет-рендер не выполнен: {exc}"],
            )
        return DispatchResult(
            status=asset_res.status,
            engine_used=ENGINE_ASSET,
            video=asset_res.video,
            render_dir=str(out_dir),
            blender_script=asset_res.blender_script,
            notes=list(asset_res.notes),
            error=asset_res.error,
        )

    if engine == ENGINE_VOXEL:
        if render_voxel is None or not blender_available():
            return DispatchResult(
                status="PARTIAL",
                engine_used=ENGINE_VOXEL,
                render_dir=str(out_dir),
                notes=[
                    "Движок voxel запрошен, но Blender не найден/недоступен. "
                    "Проверьте PATH (D:\\Blender) или BLENDER_EXECUTABLE."
                ],
            )
        try:
            vox = render_voxel(plan, out_dir)
        except Exception as exc:  # voxel runtime problems
            return DispatchResult(
                status="FAILED",
                engine_used=ENGINE_VOXEL,
                render_dir=str(out_dir),
                error=str(exc)[-500:],
                notes=[f"Voxel-рендер не выполнен: {exc}"],
            )
        return DispatchResult(
            status=vox.status,
            engine_used=ENGINE_VOXEL,
            video=vox.video,
            render_dir=str(out_dir),
            blender_script=vox.blender_script,
            notes=list(vox.notes),
            error=vox.error,
        )

    if engine == ENGINE_SKINNED:
        if render_skinned is None or build_skinned_script is None or not blender_available():
            return DispatchResult(
                status="PARTIAL",
                engine_used=ENGINE_SKINNED,
                render_dir=str(out_dir),
                notes=[
                    "Движок skinned запрошен, но Blender не найден/недоступен. "
                    "Проверьте PATH (D:\\Blender) или BLENDER_EXECUTABLE."
                ],
            )
        rigged = _skinned_cast(plan)
        scene_cast = rigged if len(rigged) >= 2 else []
        character, plan_character = _skinned_targets(plan)
        actions = _skinned_actions(plan, plan_character)
        # A walking shot reads best in profile; a talking shot must face the
        # camera, otherwise the mouth is only a sliver on the silhouette.
        face_yaw = (
            math.pi / 2.0
            if any(a.get("action") in ("walk", "run") for a in actions)
            else 0.0
        )
        try:
            if scene_cast:
                # Milo walks towards Nina, so he starts far enough left that his
                # ~0.98 m of travel stops short of her instead of clipping her.
                stage = (
                    ((-1.05, 0.0, 0.0), math.pi / 2.0),
                    ((0.55, 0.0, 0.0), -math.pi / 2.0),
                )
                actors = [
                    {
                        "character": key,
                        "origin": origin,
                        "yaw": yaw,
                        "actions": _skinned_actions(plan, char_id),
                        "duration": plan.duration,
                        "voice_lines": _skinned_voice_lines(
                            plan, char_id, limit=plan.duration
                        ),
                    }
                    for (key, char_id), (origin, yaw) in zip(scene_cast, stage)
                ]
                script = build_scene_script(
                    out_dir / "skinned",
                    actors,
                    scenario="plan",
                    fps=plan.fps,
                    width=plan.width,
                    height=plan.height,
                )
            else:
                script = build_skinned_script(
                    out_dir / "skinned",
                    scenario="plan",
                    character=character,
                    fps=plan.fps,
                    width=plan.width,
                    height=plan.height,
                    actions=actions,
                    duration=plan.duration,
                    voice_lines=_skinned_voice_lines(
                        plan, plan_character, limit=plan.duration
                    ),
                    yaw=face_yaw,
                )
            skinned = render_skinned(script, timeout=3600)
        except Exception as exc:  # rig/render runtime problems
            return DispatchResult(
                status="FAILED",
                engine_used=ENGINE_SKINNED,
                render_dir=str(out_dir),
                error=str(exc)[-500:],
                notes=[f"Skinned-рендер не выполнен: {exc}"],
            )
        if scene_cast:
            diag = skinned.diagnostics or {}
            scene_notes = [
                "Сцена: "
                + ", ".join(f"{key} ({char_id})" for key, char_id in scene_cast)
                + f". Длительность: {plan.duration:.2f} с."
            ]
            actors_diag = diag.get("actors") or []
            if actors_diag:
                scene_notes.append(
                    "Персонажи: "
                    + ", ".join(
                        f"{a.get('name')} ({a.get('vert_end', 0) - a.get('vert_start', 0)} вершин)"
                        for a in actors_diag
                    )
                )
            pose = diag.get("pose_error") or {}
            if pose:
                scene_notes.append(
                    "Поза скелета совпала с расчётной."
                    if pose.get("passed")
                    else f"Поза скелета разошлась с расчётной: {pose.get('max_abs_matrix_diff')}"
                )
            foot = diag.get("foot_contact") or {}
            if foot:
                scene_notes.append(
                    f"Стопа: окон контакта {foot.get('windows', 0)}, "
                    f"дрейф {foot.get('max_drift')} м, "
                    f"проникновение {foot.get('max_penetration')} м."
                )
            blank = diag.get("blank_frames") or {}
            if blank:
                scene_notes.append(
                    f"Пустых кадров {blank.get('blank', 0)} из {blank.get('checked', 0)}."
                )
            return DispatchResult(
                status="OK" if skinned.status == "OK" else "FAILED",
                engine_used=ENGINE_SKINNED,
                video=skinned.video,
                render_dir=skinned.render_dir or str(out_dir),
                blender_script=skinned.script,
                notes=scene_notes,
                error=skinned.error,
            )

        notes = [
            f"Персонаж: {character}. Действий из плана: {len(actions)}. "
            f"Длительность: {plan.duration:.2f} с."
        ]
        if plan_character and plan_character.lower() not in (character,):
            notes.append(
                f"Персонаж плана «{plan_character}» отрисован ригом «{character}» "
                "(в риге есть только milo и nina)."
            )
        diag = skinned.diagnostics or {}
        pose = diag.get("pose_error") or {}
        if pose:
            notes.append(
                "Поза скелета совпала с расчётной."
                if pose.get("passed")
                else f"Поза скелета разошлась с расчётной: {pose.get('max_abs_matrix_diff')}"
            )
        foot = diag.get("foot_contact") or {}
        if foot:
            notes.append(
                f"Стопа: окон контакта {foot.get('windows', 0)}, "
                f"дрейф {foot.get('max_drift')} м, "
                f"проникновение {foot.get('max_penetration')} м."
            )
        blank = diag.get("blank_frames") or {}
        if blank:
            notes.append(
                f"Пустых кадров {blank.get('blank', 0)} из {blank.get('checked', 0)}."
            )
        return DispatchResult(
            status="OK" if skinned.status == "OK" else "FAILED",
            engine_used=ENGINE_SKINNED,
            video=skinned.video,
            render_dir=skinned.render_dir or str(out_dir),
            blender_script=skinned.script,
            notes=notes,
            error=skinned.error,
        )

    wants_blender = engine in (ENGINE_BLENDER, ENGINE_AUTO)
    blender_here = bool(blender_available()) if wants_blender else False

    if engine == ENGINE_BLENDER and not blender_here:
        # Explicitly requested but unavailable → honest PARTIAL, no fallback.
        return DispatchResult(
            status="PARTIAL",
            engine_used=ENGINE_BLENDER,
            render_dir=str(out_dir),
            notes=[
                "Движок Blender запрошен, но Blender не установлен. "
                "Используйте --engine proceduralural или --engine auto."
            ],
        )

    if wants_blender and blender_here and render_blender is not None:
        try:
            blender_result: BlenderResult = render_blender(plan, out_dir)
        except Exception as exc:  # Blender runtime problems
            return DispatchResult(
                status="FAILED",
                engine_used=ENGINE_BLENDER,
                render_dir=str(out_dir),
                error=str(exc)[-500:],
                notes=[f"Blender-рендер не выполнен: {exc}"],
            )
        script_path = build_blender_script(plan, out_dir)
        return DispatchResult(
            status=blender_result.status,
            engine_used=ENGINE_BLENDER,
            video=blender_result.video,
            contact_sheet=blender_result.contact_sheet,
            render_dir=str(out_dir),
            blender_script=str(script_path),
            notes=list(blender_result.notes),
            error=blender_result.error,
        )

    # ------------------------------------------------------------------ procedural
    from ..animation import AnimationEngine
    from ..animation.scene import SceneError, load_scene

    scene_dict = plan.to_scene_dict()
    scene_path = out_dir / f"{plan.scene_id}.json"
    import json
    scene_path.write_text(json.dumps(scene_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    render_dir = out_dir / "procedural"
    try:
        result = AnimationEngine(config).render(scene_path, render_dir)
    except (SceneError, ValueError) as exc:
        return DispatchResult(
            status="FAILED",
            engine_used=ENGINE_PROCEDURAL,
            scene_path=str(scene_path),
            render_dir=str(render_dir),
            error=str(exc)[-500:],
            notes=[f"Animation Engine не смог отрендерить сцену: {exc}"],
        )

    return DispatchResult(
        status="OK",
        engine_used=ENGINE_PROCEDURAL,
        video=result.video,
        contact_sheet=result.contact_sheet,
        scene_path=str(scene_path),
        characters_path=result.characters,
        report_json=result.report_json,
        render_dir=str(render_dir),
        notes=[],
        animation_result=result,
    )
