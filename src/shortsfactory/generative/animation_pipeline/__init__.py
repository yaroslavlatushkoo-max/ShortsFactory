"""animation_pipeline — изолированный прототип импорта моделей и клипов.

Mission 11: доказать, что реальный персонаж (Kenney Blocky Characters 2.0,
CC0) с настоящими клипами импортируется и воспроизводится в Blender 5.2.3.
Не используется в production-движке (интеграция запрещена до подтверждения).
"""

from __future__ import annotations

from .importer import inspect_model, ModelInspectionError
from .player import (
    AnimationPipelineError,
    build_play_script,
    build_schedule,
    render_play_script,
    PlayResult,
)
from .clips_map import semantic_clip_map, find_clip
from .locomotion import (
    compute_phases,
    build_locomotion_script,
    render_locomotion,
    LocomotionResult,
    LocomotionError,
    Phase,
)
from .locomotion_refined import (
    build_refined_locomotion_script,
    RefinedLocomotionResult,
)
from .full_body_rig import (
    build_full_body_script,
    render_full_body,
    FullBodyResult,
    detect_control_groups,
)
from .full_body_authored import (
    build_authored_rig_script,
    render_authored_rig,
)
from .mission14 import (
    build_mission14_script,
    Mission14Result,
)
from .skinned_hero import (
    BONES,
    BONE_BY_NAME,
    BONE_LEN,
    BONE_ORDER,
    CHARACTERS,
    MILO,
    NINA,
    CharacterSpec,
    Gait,
    PoseParams,
    PLAN_ACTION_STATES,
    VISEMES,
    apply_visemes,
    viseme_track,
    PLAN_OVERLAY_KEYS,
    EMOTIONS,
    SkinnedResult,
    SCENE_PREFIX,
    build_character_mesh,
    build_scene_payload,
    build_scene_script,
    build_skinned_payload,
    build_skinned_script,
    default_timeline,
    encode_frames,
    path_constant,
    render_skinned,
    solve_pose,
    stance_windows,
    timeline_from_actions,
    vlen,
    vsub,
)

__all__ = [
    "inspect_model",
    "ModelInspectionError",
    "build_play_script",
    "build_schedule",
    "render_play_script",
    "PlayResult",
    "AnimationPipelineError",
    "semantic_clip_map",
    "find_clip",
    "compute_phases",
    "build_locomotion_script",
    "render_locomotion",
    "LocomotionResult",
    "LocomotionError",
    "Phase",
    "build_refined_locomotion_script",
    "RefinedLocomotionResult",
    "build_mission14_script",
    "Mission14Result",
    "build_full_body_script",
    "render_full_body",
    "FullBodyResult",
    "detect_control_groups",
    "BONES",
    "BONE_BY_NAME",
    "BONE_LEN",
    "BONE_ORDER",
    "CHARACTERS",
    "MILO",
    "NINA",
    "CharacterSpec",
    "Gait",
    "PoseParams",
    "SkinnedResult",
    "SCENE_PREFIX",
    "build_character_mesh",
    "build_scene_payload",
    "build_scene_script",
    "build_skinned_payload",
    "build_skinned_script",
    "default_timeline",
    "encode_frames",
    "path_constant",
    "render_skinned",
    "solve_pose",
    "stance_windows",
    "timeline_from_actions",
    "PLAN_ACTION_STATES",
    "VISEMES",
    "apply_visemes",
    "viseme_track",
    "PLAN_OVERLAY_KEYS",
    "EMOTIONS",
    "vlen",
    "vsub",
]
