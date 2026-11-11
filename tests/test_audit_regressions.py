"""Regressions for the four November audit findings; no Blender or paid APIs."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest

from shortsfactory import api
from shortsfactory.generative import blocky_production as production
from shortsfactory.generative import blocky_scene as scenes
from shortsfactory.generative.animation_pipeline import blocky_hero as bh
from shortsfactory.integration import pipeline
from shortsfactory.integration.contracts import IntegrationError, IntegrationPaths


@pytest.fixture
def renderer(monkeypatch, tmp_path):
    calls = []

    def build(out, actors, **kwargs):
        return Path(out) / "render.py"

    def render(script, **kwargs):
        calls.append(script)
        video = tmp_path / "rendered.mp4"
        video.write_bytes(b"rendered video")
        return SimpleNamespace(status="OK", video=str(video), error=None)

    monkeypatch.setattr(bh, "build_blocky_scene_script", build)
    monkeypatch.setattr(bh, "render_skinned", render)
    return calls


def plan():
    return api.ScenePlan(1, "steve", "Hello", 4.0)


def test_api_cache_reuses_identical_request(tmp_path, renderer):
    first = api.render_scene_clip(plan(), tmp_path)
    assert api.render_scene_clip(plan(), tmp_path) == first
    assert len(renderer) == 1


@pytest.mark.parametrize("change", [
    {"text": "Changed"}, {"actor": "alex"}, {"duration_s": 6.0},
    {"closeup": True}, {"fps": 24}, {"width": 720},
    {"height": 1280}, {"samples": 16},
])
def test_api_cache_invalidates_changed_inputs(tmp_path, renderer, change):
    api.render_scene_clip(plan(), tmp_path)
    scene = {k: v for k, v in change.items() if hasattr(plan(), k)}
    settings = {k: v for k, v in change.items() if k not in scene}
    api.render_scene_clip(replace(plan(), **scene), tmp_path, **settings)
    assert len(renderer) == 2


@pytest.mark.parametrize("cache_state", ["legacy", "broken", "empty", "directory"])
def test_api_cache_rejects_invalid_artifacts(tmp_path, renderer, cache_state):
    out = api.render_scene_clip(plan(), tmp_path)
    metadata = out.with_suffix(".cache.json")
    if cache_state == "legacy":
        metadata.unlink(missing_ok=True)
    elif cache_state == "broken":
        metadata.write_text("{broken", encoding="utf-8")
    elif cache_state == "empty":
        out.write_bytes(b"")
    else:
        # A directory is not a cached video. Remove it in the fake renderer
        # so the subsequent successful render can write its output.
        out.unlink()
        out.mkdir()
        original = bh.render_skinned

        def render(*args, **kwargs):
            out.rmdir()
            return original(*args, **kwargs)

        from unittest.mock import patch
        with patch.object(bh, "render_skinned", render):
            api.render_scene_clip(plan(), tmp_path)
        assert len(renderer) == 2
        return
    api.render_scene_clip(plan(), tmp_path)
    assert len(renderer) == 2


def test_api_cache_force_render(tmp_path, renderer):
    api.render_scene_clip(plan(), tmp_path)
    api.render_scene_clip(plan(), tmp_path, use_cache=False)
    assert len(renderer) == 2


def test_api_failed_refresh_does_not_cache_old_video(tmp_path, renderer, monkeypatch):
    out = api.render_scene_clip(plan(), tmp_path)
    original = bh.render_skinned
    monkeypatch.setattr(bh, "render_skinned", lambda *a, **kw:
                        SimpleNamespace(status="OK", video="", error=None))
    with pytest.raises(RuntimeError):
        api.render_scene_clip(plan(), tmp_path, use_cache=False)
    monkeypatch.setattr(bh, "render_skinned", original)
    api.render_scene_clip(plan(), tmp_path)
    assert out.is_file()
    assert len(renderer) == 2


def scene_data():
    return {
        "title": "audit", "duration": 1.0, "fps": 30, "ambience": "",
        "actors": [{"character": "steve", "origin": [0.0, 0.9, 0.0],
                    "actions": [{"action": "idle", "start": 0.0, "duration": 1.0}]}],
        "dialogue": [],
    }


@pytest.mark.parametrize("video", [None, "", ".", "missing.mp4"])
def test_failed_scene_without_video_writes_film_report(tmp_path, monkeypatch, video):
    def fail(self, out, **kwargs):
        out.mkdir(parents=True, exist_ok=True)
        return {"status": "FAILED", "error": "render failed", "final_audio": video}

    monkeypatch.setattr(scenes.BlockyScene, "render", fail)
    monkeypatch.setattr(production, "probe_duration", lambda *a:
                        pytest.fail("failed scene must not be probed"))
    report = production.produce_film({"scenes": [scene_data()]}, tmp_path, voice=False)
    assert report["status"] == "FAILED"
    assert report["scenes"][0]["video"] is None
    assert report["scenes"][0]["duration"] == 0
    assert json.loads((tmp_path / "film_report.json").read_text(encoding="utf-8")) == report


@pytest.mark.parametrize("failure", ["discover", "check"])
def test_missing_components_write_failure_reports(tmp_path, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise IntegrationError("component unavailable")

    if failure == "discover":
        monkeypatch.setattr(IntegrationPaths, "discover", fail)
    else:
        missing = IntegrationPaths(tmp_path, tmp_path / "missing_story",
                                   tmp_path / "missing_lab", "python", "python")
        monkeypatch.setattr(IntegrationPaths, "discover", lambda *a: missing)
    with pytest.raises(IntegrationError):
        pipeline.run_integrated_create("A short story", tmp_path / "out")
    report = json.loads((tmp_path / "out/report.json").read_text(encoding="utf-8"))
    assert report["status"] == pipeline.STATUS_FAIL
    assert report["stage"] == "dependencies"
    assert report["error"]
    assert (tmp_path / "out/report.md").is_file()


def test_scene_rechecks_lip_sync_after_mix(tmp_path, monkeypatch):
    """Exercise real lip-sync QC through BlockyScene.render, with a real WAV."""
    scene = scenes.BlockyScene.from_dict(scene_data())
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    diag_path = render_dir / f"{scene._slug()}_diagnostics.json"
    diagnostics = {"lip_sync": {"status": "skipped"}, "retained": "yes"}
    diag_path.write_text(json.dumps(diagnostics), encoding="utf-8")
    payload = render_dir / "payload_audit.json"
    payload.write_text(json.dumps({
        "fps": 30, "frames": [{}] * 30,
        "mouth_states": {"steve": {str(i): "steve_mouth_closed.png" for i in range(30)}},
    }), encoding="utf-8")
    script = render_dir / "blocky_audit.py"
    script.write_text(f'PAYLOAD = Path(r"{payload}")\n', encoding="utf-8")
    monkeypatch.setattr(scenes, "build_blocky_scene_script", lambda *a, **kw: script)
    monkeypatch.setattr(scenes, "render_skinned", lambda *a, **kw: SimpleNamespace(
        status="OK", video=str(render_dir / "final.mp4"), diagnostics=diagnostics, error=None))
    monkeypatch.setattr(scenes.BlockyScene, "_scene_mix_inputs", lambda *a, **kw:
                        [SimpleNamespace(to_dict=lambda: {})])

    def mix(path, *args, **kwargs):
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\x00\x20\x00\xe0" * 11025)

    monkeypatch.setattr(scenes, "mix_scene_audio", mix)
    monkeypatch.setattr(scenes, "mux_audio", lambda video, data, out: out.write_bytes(b"video"))
    monkeypatch.setattr(scenes, "mix_report", lambda *a: {})
    monkeypatch.setattr(scenes.subprocess, "run", lambda *a, **kw: None)
    report = scene.render(tmp_path, voice=False)
    qc = report["lip_sync_qc"]
    assert qc["status"] == "ok"
    assert qc["characters"]["steve"]["frames_speaking"] > 0
    assert qc["characters"]["steve"]["verdict"] == "warn"
    assert report["diagnostics"]["lip_sync"] == qc
    saved = json.loads(diag_path.read_text(encoding="utf-8"))
    assert saved["lip_sync"] == qc
    assert saved["retained"] == "yes"
