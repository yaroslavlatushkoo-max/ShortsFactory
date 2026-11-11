"""Tests for whole-body pose smoothing (Priority 1: cartoon motion)."""


from shortsfactory.generative.animation_pipeline.blocky_hero import ease_poses
from shortsfactory.generative.animation_pipeline.skinned_hero import PoseParams


def _spike_poses(n: int = 30) -> list[PoseParams]:
    """Idle poses with one sharp arm pop in the middle."""
    out = [PoseParams() for _ in range(n)]
    out[15].arm_l_pitch = 0.9
    out[16].arm_l_pitch = 0.2
    out[17].arm_l_pitch = 0.7
    return out


def test_ease_poses_preserves_length_and_is_deterministic():
    poses = _spike_poses(30)
    smoothed = ease_poses(poses, fps=30)
    assert len(smoothed) == len(poses)
    assert ease_poses(poses, fps=30) == smoothed


def test_ease_poses_reduces_sharp_jump():
    poses = _spike_poses(30)
    raw_max_delta = max(
        abs(poses[i + 1].arm_l_pitch - poses[i].arm_l_pitch)
        for i in range(len(poses) - 1)
    )
    smoothed = ease_poses(poses, fps=30, idle_motion=False)
    smooth_max_delta = max(
        abs(smoothed[i + 1].arm_l_pitch - smoothed[i].arm_l_pitch)
        for i in range(len(smoothed) - 1)
    )
    assert smooth_max_delta < raw_max_delta


def test_ease_poses_idle_motion_can_be_disabled():
    base = [PoseParams() for _ in range(30)]
    still = ease_poses(base, fps=30, idle_motion=False)
    assert all(abs(p.chest_pitch) < 1e-9 for p in still)
    moving = ease_poses(base, fps=30, idle_motion=True)
    assert any(abs(p.chest_pitch) > 1e-9 for p in moving)
