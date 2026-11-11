"""Unit tests for the pipeline contracts module (stage A2)."""
from __future__ import annotations

import pytest

from shortsfactory.generative.contracts import (
    Film,
    RenderJob,
    ScenePlan,
    StoryIdea,
    StoryScene,
    Storyboard,
    build_scene_plans,
    estimate_render_seconds,
    make_episodes,
    pick_render_settings,
)


def test_story_idea_rejects_empty():
    with pytest.raises(ValueError):
        StoryIdea("   ")
    with pytest.raises(ValueError):
        StoryIdea("x", scenes=0)


def test_storyboard_roundtrip():
    raw = {
        "title": "T",
        "logline": "L",
        "source": "fallback",
        "idea": "I",
        "scenes": [
            {"title": "S1", "detail": "D1", "actor": "alex"},
            {"title": "S2", "summary": "D2"},
        ],
    }
    sb = Storyboard.from_dict(raw)
    assert sb.scene_count == 2
    assert sb.scenes[0].actor == "alex"
    assert sb.scenes[1].actor == "steve"
    assert sb.to_dict()["scenes"][0]["index"] == 1


def test_build_scene_plans_proportional_and_respects_target():
    sb = Storyboard.from_dict({
        "title": "T", "scenes": [
            {"title": "A", "detail": "один"},
            {"title": "B", "detail": "два три четыре"},
        ],
    })
    plans = build_scene_plans(sb, target_s=60.0, min_scene_s=5.0)
    assert len(plans) == 2
    assert all(p.duration_s >= 5.0 for p in plans)
    assert abs(sum(p.duration_s for p in plans) - 60.0) < 1e-6
    assert plans[1].duration_s > plans[0].duration_s  # больше текста → дольше


def test_scene_plan_duration_and_job_frames():
    scene = StoryScene(index=1, title="S", summary="hi")
    plan = ScenePlan.from_story_scene(scene, duration_s=10.0)
    job = RenderJob(scene=plan, fps=12)
    assert job.frame_count() == 120


def test_estimate_render_seconds_scales():
    scene = StoryScene(index=1, title="S", summary="hi")
    job_lo = RenderJob(scene=ScenePlan.from_story_scene(scene, 10.0),
                       width=480, height=720, samples=8, fps=12)
    job_hi = RenderJob(scene=ScenePlan.from_story_scene(scene, 10.0),
                       width=1080, height=1920, samples=32, fps=30)
    t_lo = estimate_render_seconds(job_lo)
    t_hi = estimate_render_seconds(job_hi)
    assert t_lo > 0
    assert t_hi > t_lo * 8


def test_pick_render_settings_budget():
    assert pick_render_settings(3600, frames=120)["samples"] == 32
    assert pick_render_settings(10, frames=120)["samples"] == 8
    assert pick_render_settings(1, frames=120)["width"] == 480


def test_film_total_target_frames():
    film = Film(target_s=600.0)
    assert film.total_target_frames() == 18000


def test_make_episodes_splits_scenes_in_order():
    scenes = [StoryScene(index=i, title=f"s{i}") for i in range(1, 8)]
    sb = Storyboard(title="T", scenes=tuple(scenes))
    eps = make_episodes(sb, episodes=3, target_s=300.0)
    assert len(eps) == 3
    # порядок сюжета сохраняется
    flat = [p.index for ep in eps for p in ep.scenes]
    assert flat == [1, 2, 3, 4, 5, 6, 7]
    for ep in eps:
        assert ep.target_s == 300.0
        assert ep.total_scene_seconds() > 0


def test_make_episodes_empty_storyboard():
    sb = Storyboard(title="", scenes=())
    assert make_episodes(sb, episodes=2) == []
