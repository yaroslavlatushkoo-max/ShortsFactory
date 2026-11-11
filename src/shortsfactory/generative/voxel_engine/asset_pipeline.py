"""Asset-based Voxel production pipeline (Mission 8).

A thin orchestration layer that produces an ``AnimationPlan`` referencing known
asset ids (character, environment, props, clips, shots) from the curated catalog
(``assets/voxel/catalog.json``), validates those references, and renders the
episode with the Voxel engine using catalog-driven camera shots and clips.

This is the *asset-based* production path; it does not replace the lower-level
``voxel`` engine (which remains available via ``--engine voxel``).
"""

from __future__ import annotations

from pathlib import Path

from ..animation_plan import (
    ENGINE_ASSET,
    AnimationPlan,
    PlanAction,
    PlanCameraKey,
    PlanCharacter,
    PlanEvent,
    PlanObject,
    PlanError,
)
from .assets_catalog import AssetCatalog
from .voxel_renderer import VoxelRenderResult, build_voxel_script, render_voxel

#: Default camera shots (start_frac, end_frac) mapped to catalog shot ids.
DEFAULT_SHOT_PLAN = (("three_quarter", 0.0, 0.80), ("closeup", 0.80, 1.0))


def _shots_table(catalog: AssetCatalog, shot_plan=DEFAULT_SHOT_PLAN) -> tuple:
    rows = []
    for (shot_id, s, e) in shot_plan:
        shot = catalog.get("shots", shot_id)
        ox, oy, oz = shot["offset"]
        lens = float(shot["lens"])
        rows.append((float(s), float(e), float(ox), float(oy), float(oz), lens))
    return tuple(rows)


def build_asset_plan(duration: float = 9.0, width: int = 1080, height: int = 1920,
                     seed: int = 20261027) -> AnimationPlan:
    """Deterministic episode plan for 'Hero finds a mysterious cube'."""
    total = min(max(float(duration), 8.0), 10.0)
    hero = PlanCharacter(
        id="hero", name="Герой", rig="humanoid_v1",
        x=0.42, y=0.60, scale=1.0, facing=1, z=20, appear_at=0.0,
        palette={"skin": "#e8b48c", "hair": "#3a2a20", "shirt": "#2a6b5e",
                 "pants": "#1f4f46", "eyes": "#4a6b8a", "shoes": "#22242c",
                 "trim": "#1a3a34"},
    )
    cube = PlanObject(id="glow_cube", kind="gem", z=15, appear_at=0.0,
                      color="#8df0b4",
                      keyframes=[{"t": 0.0, "x": 0.62, "y": 0.55, "scale": 0.5, "rotation": 0.0},
                                 {"t": 6.9, "x": 0.62, "y": 0.55, "scale": 0.5, "rotation": 0.0}])
    t_idle0, t_idle1 = 0.0, 1.6
    t_look0, t_look1 = 1.6, 2.4
    t_turn0, t_turn1 = 2.4, 3.4
    t_walk0, t_walk1 = 3.4, 4.8
    t_stop0, t_stop1 = 4.8, 5.2
    t_bend0, t_bend1 = 5.2, 6.2
    t_pick0, t_pick1 = 6.2, 7.4
    t_attach = 6.9
    t_react0, t_react1 = 7.4, 8.4
    t_hold0, t_hold1 = 8.4, total
    actions = [
        PlanAction(character="hero", action="idle", start=t_idle0, duration=t_idle1 - t_idle0, params={"sway": 0.03}),
        PlanAction(character="hero", action="look_at", start=t_look0, duration=t_look1 - t_look0, params={"to_facing": 1}),
        PlanAction(character="hero", action="turn", start=t_turn0, duration=t_turn1 - t_turn0, params={"to_facing": 1}),
        PlanAction(character="hero", action="walk", start=t_walk0, duration=t_walk1 - t_walk0,
                   params={"to_x": 0.62, "cycles": 2.4, "stride": 0.45}),
        PlanAction(character="hero", action="stop", start=t_stop0, duration=t_stop1 - t_stop0, params={}),
        PlanAction(character="hero", action="bend", start=t_bend0, duration=t_bend1 - t_bend0, params={"intensity": 1.0}),
        PlanAction(character="hero", action="pickup", start=t_pick0, duration=t_pick1 - t_pick0, params={"intensity": 1.0}),
        PlanAction(character="hero", action="react", start=t_react0, duration=t_react1 - t_react0, params={"intensity": 1.0}),
        PlanAction(character="hero", action="hold", start=t_hold0, duration=t_hold1 - t_hold0, params={"intensity": 1.0}),
    ]
    events = [PlanEvent(t=t_attach, kind="attach", object="glow_cube", character="hero",
                       point="hand_r", offset=(0.0, 0.0), scale=0.5)]
    camera = [PlanCameraKey(t=0.0, x=0.50, y=0.55, zoom=1.0),
              PlanCameraKey(t=t_walk0, x=0.52, y=0.55, zoom=1.0),
              PlanCameraKey(t=t_attach, x=0.55, y=0.58, zoom=1.05),
              PlanCameraKey(t=t_react1, x=0.54, y=0.57, zoom=1.1),
              PlanCameraKey(t=total - 0.2, x=0.54, y=0.57, zoom=1.15)]
    production = {
        "character": "hero_v1",
        "environment": "forest_blocky",
        "props": ["glow_cube"],
        "clips": ["idle", "look_at", "turn", "walk", "stop", "bend", "pickup", "react", "hold"],
        "shots": ["three_quarter", "closeup"],
    }
    return AnimationPlan(
        scene_id="asset_cube_01",
        title="Герой находит загадочный куб",
        duration=round(total, 2),
        fps=30, width=width, height=height,
        environment="forest_blocky",
        characters=[hero], objects=[cube],
        actions=actions, events=events, camera=camera,
        contact_times=[t_attach, t_react0, t_hold0],
        blink_times=[1.0, 3.0, 6.0, 8.0],
        seed=seed, blend=0.12,
        extra_actions={"bend", "hold", "look_at"},
        production=production,
    )


def validate_asset_plan(plan: AnimationPlan, catalog: AssetCatalog | None = None) -> list[str]:
    """Base plan validation + catalog reference validation + clip coverage."""
    errors = list(plan.validate())
    catalog = catalog or AssetCatalog.load()
    if plan.production:
        errors.extend(catalog.validate_refs(plan.production))
        clip_ids = set(catalog.ids("clips"))
        for a in plan.actions:
            if a.action not in clip_ids:
                errors.append(f"Действие «{a.action}» не является подготовленным клипом каталога.")
    return errors


def ensure_asset_plan(plan: AnimationPlan, catalog: AssetCatalog | None = None) -> AnimationPlan:
    errors = validate_asset_plan(plan, catalog)
    if errors:
        raise PlanError("Ассет-план некорректн:\n- " + "\n- ".join(errors))
    return plan


def render_asset(plan: AnimationPlan, out_dir: str | Path, catalog: AssetCatalog | None = None) -> VoxelRenderResult:
    """Render an asset-based plan with the Voxel engine (catalog-driven shots)."""
    catalog = catalog or AssetCatalog.load()
    try:
        ensure_asset_plan(plan, catalog)
    except PlanError as exc:
        return VoxelRenderResult(status="FAILED", engine=ENGINE_ASSET, error=str(exc),
                                 notes=[f"Валидация ассет-плана не пройдена: {exc}"])
    shots = _shots_table(catalog)
    try:
        script_path = build_voxel_script(plan, out_dir, shots=shots)
    except PlanError as exc:
        return VoxelRenderResult(status="FAILED", engine=ENGINE_ASSET, error=str(exc),
                                 notes=[f"Валидация плана не пройдена: {exc}"])
    # reuse the existing voxel runner for the actual Blender render
    result = render_voxel(plan, out_dir)
    result.engine = ENGINE_ASSET
    result.blender_script = str(script_path)
    return result
