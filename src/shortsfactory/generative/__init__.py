"""Generative video pipeline (Mission 3) + free local hybrid pipeline (Mission 5)."""

from __future__ import annotations

from .backend import BackendError, BackendNotConfigured, create_backend

# Importing the providers packages registers backend adapters (veo/kling/fal)
# and the text/voice/video providers into the unified AI registry.
from . import providers  # noqa: E402,F401
from . import text, voice, video  # noqa: E402,F401
from .pipeline import run_ai_create
from .animation_plan import AnimationPlan, PlanError
from .engine_dispatcher import render_plan
from .freeanim import FreeResult, build_plan_for_idea, run_free_create

try:  # optional adapter; importable without Blender
    from .blender_adapter import blender_available, build_blender_script, render_blender
except Exception:  # pragma: no cover
    blender_available = lambda: False  # type: ignore
    build_blender_script = None  # type: ignore
    render_blender = None  # type: ignore

__all__ = [
    "run_ai_create",
    "create_backend",
    "BackendError",
    "BackendNotConfigured",
    "AnimationPlan",
    "PlanError",
    "render_plan",
    "run_free_create",
    "build_plan_for_idea",
    "FreeResult",
    "blender_available",
    "build_blender_script",
    "render_blender",
]
