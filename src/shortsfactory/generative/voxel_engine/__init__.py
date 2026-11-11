"""Voxel Animation Engine v1.

A procedural 3D engine for original cubic (Minecraft-style, but fully
original geometry/materials) short animations, rendered locally via Blender.
It consumes the shared :class:`AnimationPlan` and never touches cloud providers.
"""

from __future__ import annotations

from .voxel_renderer import (
    ENGINE_VOXEL,
    VoxelRenderResult,
    build_voxel_script,
    render_voxel,
)
from .voxel_validation import ensure_voxel_plan, validate_voxel_plan
from .assets_catalog import AssetCatalog, AssetError
from .asset_pipeline import (
    build_asset_plan,
    ensure_asset_plan,
    render_asset,
    validate_asset_plan,
)

__all__ = [
    "ENGINE_VOXEL",
    "VoxelRenderResult",
    "build_voxel_script",
    "render_voxel",
    "ensure_voxel_plan",
    "validate_voxel_plan",
    "AssetCatalog",
    "AssetError",
    "build_asset_plan",
    "ensure_asset_plan",
    "render_asset",
    "validate_asset_plan",
]
