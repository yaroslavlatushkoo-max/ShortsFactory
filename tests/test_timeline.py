from shortsfactory.models import Scene, Script
from shortsfactory.timeline import SceneTiming, Timeline, build


def _script() -> Script:
    return Script(
        lang="ru",
        title="Тест",
        hook="Первый кадр.",
        scenes=[
            Scene(id="scene_01", text="Первая сцена."),
            Scene(id="scene_02", text="Вторая сцена."),
        ],
    )


def _manifest() -> dict:
    return {
        "scenes": [
            {"scene_id": "scene_01", "duration": 3.0},
            {"scene_id": "scene_02", "duration": 4.0},
        ]
    }


def test_timeline_fits_target_and_keeps_order(config):
    script = _script()
    timeline = build(script, _manifest(), config, target=10.0)
    assert abs(timeline.total_video - 10.0) <= 0.1
    assert timeline.scenes[0].video_start == 0
    assert timeline.scenes[1].video_start > 0
    assert timeline.scenes[1].audio_start > timeline.scenes[0].audio_start
    assert script.scenes[0].scene_duration > 0
    assert timeline.fit_note


def test_timeline_keeps_long_speech_instead_of_forcing_speed(config):
    script = _script()
    timeline = build(script, _manifest(), config, target=5.0)
    assert timeline.total_video > 5.0
    assert "не ускорялся" in timeline.fit_note


def test_timeline_roundtrip():
    timeline = Timeline(
        lang="en",
        scenes=[SceneTiming("scene_01", 2.0, 2.75, 0.0, 0.15, 0.6)],
        total_video=2.75,
        total_audio=2.75,
    )
    restored = Timeline.from_dict(timeline.to_dict())
    assert restored.lang == "en"
    assert restored.scenes[0].scene_id == "scene_01"
    assert abs(restored.total_video - 2.75) < 1e-6
