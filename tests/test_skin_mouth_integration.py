"""Integration tests: mouth_skin actor key -> payload['mouth_states']."""
from __future__ import annotations


from shortsfactory.generative.animation_pipeline import blocky_hero as bh


def _scene_frames(fps: int = 30) -> int:
    return int(2.0 * fps)


def _actor(key: str, text: str, lang: str, start: float, dur: float) -> dict:
    return {
        "character": key,
        "origin": (0.0, 0.0, 0.0),
        "actions": [{"action": "idle", "start": 0.0, "duration": 2.0}],
        "mouth_skin": {
            "text": text,
            "lang": lang,
            "start": start,
            "duration": dur,
        },
    }


def test_payload_has_mouth_states_for_each_actor(tmp_path):
    scene = {
        "actors": [
            _actor("steve", "Мама мыла раму. Урок окончен.", "ru", 0.3, 1.4),
            _actor("alex", "Hello, world! This is a test.", "en", 0.5, 1.0),
        ]
    }
    _, payload = bh.build_blocky_scene_payload(
        tmp_path,
        scene["actors"],
        scenario="skin_integration_test",
        fps=30,
    )
    states = payload.get("mouth_states")
    assert states is not None
    frames = _scene_frames()
    for key in ("steve", "alex"):
        assert key in states
        assert isinstance(states[key], dict)
        assert len(states[key]) == frames
        assert all(isinstance(f, int) for f in states[key])


def test_pauses_and_boundaries_are_closed(tmp_path):
    actor = _actor("steve", "Б. П. М. О. У. А. Я.", "ru", 0.0, 1.8)
    _, payload = bh.build_blocky_scene_payload(tmp_path, [actor], scenario="skin_pause_test", fps=30)
    st = payload["mouth_states"]["steve"]
    assert st[0] == "steve_mouth_closed.png"   # before speech starts
    assert st[59] == "steve_mouth_closed.png"  # after speech ends
    # during the speech window we must see at least one non-closed state
    assert any(not v.endswith("_closed.png") for v in st.values())


def test_actors_are_independent(tmp_path):
    actors = [
        _actor("steve", "Мама мыла раму.", "ru", 0.0, 1.2),
        _actor("alex", "Hello world test.", "en", 0.0, 1.2),
    ]
    _, payload = bh.build_blocky_scene_payload(tmp_path, actors, scenario="skin_indep_test", fps=30)
    st = payload["mouth_states"]
    # Russian open vowels -> at least one 'open' file; English also
    assert any(v.endswith("_open.png") for v in st["steve"].values())
    assert any(v.endswith("_open.png") for v in st["alex"].values())
    # and both have rounded vowels somewhere
    assert any(v.endswith("_round.png") for v in st["steve"].values())
    assert any(v.endswith("_round.png") for v in st["alex"].values())


def test_missing_mouth_skin_adds_no_states(tmp_path):
    actor = _actor("steve", "Мама мыла раму.", "ru", 0.0, 1.0)
    actor.pop("mouth_skin")
    _, payload = bh.build_blocky_scene_payload(tmp_path, [actor], scenario="skin_none_test", fps=30)
    assert "mouth_states" not in payload


def test_mouth_skin_segments_open_only_within_windows(tmp_path):
    actor = _actor("steve", "Привет, Алекс! Держи, проверь!", "ru",
                   0.0, 1.0)
    actor["mouth_skin"] = {
        "text": "Привет, Алекс! Держи, проверь!",
        "lang": "ru",
        "segments": [[0.3, 1.0], [1.3, 1.7]],
    }
    _, payload = bh.build_blocky_scene_payload(
        tmp_path, [actor], scenario="skin_segments_test", fps=30)
    st = payload["mouth_states"]["steve"]
    assert len(st) == 60
    assert st[0] == "steve_mouth_closed.png"      # before window 1
    assert st[35] == "steve_mouth_closed.png"     # in the gap (1.1 s)
    assert any(not v.endswith("_closed.png") for v in list(st.values())[9:30])
    assert st[55] == "steve_mouth_closed.png"     # after last window
