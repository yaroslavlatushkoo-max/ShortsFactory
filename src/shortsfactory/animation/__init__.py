"""Animation Engine v2 — 2D cut-out animation with object attachments.

The engine renders a scene description (characters, environment, objects,
actions, attachment events, timeline) into an MP4 through the existing FFmpeg
toolchain. It does not replace the static-image pipeline: the visual production
mode is chosen per project (``images`` or ``animated``).

A storyboard (structured beats) can be compiled into the same scene format, see
:mod:`shortsfactory.animation.storyboard`.
"""

from .engine import AnimationEngine, AnimationResult
from .looks import LookError, list_looks, load_look, resolve_look
from .motion import POSES, UNSUPPORTED
from .renderer import RenderError
from .rig import RIG_POINTS
from .scene import (
    ACTIONS,
    RIGS,
    UNSUPPORTED_ACTIONS,
    ActionItem,
    AnimationScene,
    AttachmentEvent,
    Character,
    SceneError,
    SceneObject,
    load_scene,
)
from .storyboard import StoryboardError, compile_storyboard, load_storyboard

__all__ = [
    "ACTIONS",
    "POSES",
    "RIGS",
    "RIG_POINTS",
    "UNSUPPORTED",
    "UNSUPPORTED_ACTIONS",
    "ActionItem",
    "AnimationEngine",
    "AnimationResult",
    "AnimationScene",
    "AttachmentEvent",
    "Character",
    "LookError",
    "RenderError",
    "SceneError",
    "SceneObject",
    "StoryboardError",
    "compile_storyboard",
    "list_looks",
    "load_look",
    "load_scene",
    "load_storyboard",
    "resolve_look",
]
