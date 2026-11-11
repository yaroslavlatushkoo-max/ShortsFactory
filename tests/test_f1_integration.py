"""F1: межсценовая непрерывность, обязательные QC-гейты и надёжность CLI."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from shortsfactory.generative.blocky_continuity import (
    FEATURE_VERSION,
    WorldState,
    hash_scene,
    prepare_scene_continuity,
    snapshot_final_state,
    validate_scene_continuity,
)
from shortsfactory.generative.blocky_production import produce_film
from shortsfactory.generative.blocky_scene import BlockyScene
from shortsfactory.generative.blocky_story import normalize_scene

ROOT = Path(__file__).resolve().parents[1]


def _door(name="Door", **extra):
    entry = {"name": name, "preset": "door", "x": 0.0, "y": 1.6}
    entry.update(extra)
    return entry


def _scene(actors=None, props=None, environment="cave", duration=4.0, **extra):
    scene = {
        "title": "t",
        "duration": duration,
        "fps": 30,
        "environment": environment,
        "ambience": "",
        "actors": actors or [
            {"character": "steve", "origin": [-0.6, 0.9, 0.0],
             "actions": [{"action": "idle", "start": 0.0, "duration": duration}]}
        ],
        "props": props or [],
        "dialogue": [],
    }
    scene.update(extra)
    return scene


def _empty_world() -> WorldState:
    return WorldState()


# ------------------------------------------------------------------ B-01/RC1


def test_door_closed_stays_closed_without_event():
    world = _empty_world()
    scene1 = normalize_scene(_scene(props=[_door()]))
    prepared1, issues1 = prepare_scene_continuity(scene1, world, scene_index=1)
    assert issues1 == []
    snapshot_final_state(prepared1, world, scene_index=1)
    assert world.props["Door"]["open"] is False

    scene2 = normalize_scene(_scene(props=[_door()]))
    prepared2, issues2 = prepare_scene_continuity(scene2, world, scene_index=2)
    assert issues2 == []
    door2 = next(p for p in prepared2["props"] if p.get("name") == "Door")
    assert "open_frame" not in door2
    assert door2.get("state", "closed") == "closed"


def test_door_opens_after_event_and_stays_open_across_three_scenes():
    world = _empty_world()
    # сцена 1: есть событие открытия (open_frame=60 → 2.0 с из 4 с)
    s1 = normalize_scene(_scene(props=[_door(open_frame=60)]))
    p1, i1 = prepare_scene_continuity(s1, world, scene_index=1)
    assert i1 == []
    snapshot_final_state(p1, world, scene_index=1)
    assert world.props["Door"]["open"] is True

    # сцена 2 и 3: события нет, дверь должна остаться открытой и статичной
    for idx in (2, 3):
        s = normalize_scene(_scene(props=[_door()]))
        p, issues = prepare_scene_continuity(s, world, scene_index=idx)
        assert issues == []
        door = next(prop for prop in p["props"] if prop.get("name") == "Door")
        assert door.get("state") == "open"
        assert "open_frame" not in door
        snapshot_final_state(p, world, scene_index=idx)
        assert world.props["Door"]["open"] is True


def test_door_conflicting_events_rejected():
    world = _empty_world()
    scene = normalize_scene(_scene(props=[_door(state="open", open_frame=60)]))
    _, issues = prepare_scene_continuity(scene, world, scene_index=1)
    assert any("конфликт событий" in issue for issue in issues)


def test_door_duplicate_in_one_scene_rejected():
    scene = normalize_scene(_scene(props=[_door(), _door()]))
    assert any("продублирован" in issue for issue in validate_scene_continuity(scene))


# ------------------------------------------------------------------ props/actors


def test_carried_prop_continues_in_next_scene():
    world = _empty_world()
    carry = {"character": "steve", "bone": "hand_R", "start": 0.0, "release": -1.0}
    s1 = normalize_scene(_scene(props=[_door(name="Cube", preset="glowing_cube", carry=carry)]))
    p1, _ = prepare_scene_continuity(s1, world, scene_index=1)
    snapshot_final_state(p1, world, scene_index=1)
    assert world.carried["Cube"] == "steve"

    s2 = normalize_scene(_scene(props=[_door(name="Cube", preset="glowing_cube")]))
    p2, issues = prepare_scene_continuity(s2, world, scene_index=2)
    assert issues == []
    cube = next(p for p in p2["props"] if p.get("name") == "Cube")
    assert cube["carry"]["character"] == "steve"
    assert cube["carry"]["start"] == 0.0


def test_no_teleport_within_same_environment():
    world = _empty_world()
    s1 = normalize_scene(_scene(actors=[
        {"character": "steve", "origin": [-0.6, 0.9, 0.0],
         "actions": [{"action": "idle", "start": 0.0, "duration": 4.0}]}
    ]))
    p1, _ = prepare_scene_continuity(s1, world, scene_index=1)
    snapshot_final_state(p1, world, scene_index=1)

    s2 = normalize_scene(_scene(actors=[
        {"character": "steve", "origin": [1.8, 0.9, 0.0],
         "actions": [{"action": "idle", "start": 0.0, "duration": 4.0}]}
    ]))
    p2, issues = prepare_scene_continuity(s2, world, scene_index=2)
    # авто-исправление: положение возвращено к предыдущему, никакой
    # телепортации в итоговом ролике — issue не возникает
    assert not any("телепортируется" in issue for issue in issues)
    steve = next(a for a in p2["actors"] if a["character"] == "steve")
    assert steve["origin"][0] == pytest.approx(-0.6)


def test_intentional_location_change_is_allowed():
    world = _empty_world()
    s1 = normalize_scene(_scene(environment="cave"))
    p1, _ = prepare_scene_continuity(s1, world, scene_index=1)
    snapshot_final_state(p1, world, scene_index=1)

    s2 = normalize_scene(_scene(environment="forest", actors=[
        {"character": "steve", "origin": [2.0, 1.2, 0.0],
         "actions": [{"action": "idle", "start": 0.0, "duration": 4.0}]}
    ]))
    p2, issues = prepare_scene_continuity(s2, world, scene_index=2)
    assert not any("телепортируется" in issue for issue in issues)


# ------------------------------------------------------------------ pipeline gates


def _tiny_video(path: Path, seconds: float = 4.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:s=64x64:r=30:d={seconds:.3f}",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(path),
        ],
        check=True,
    )
    return path


def _fake_render_factory(out_root: Path, *, fail_scene: int | None = None):
    """Factory returning a patched BlockyScene.render."""
    counter = {"n": 0}

    def fake_render(self, scene_out, *, timeout=3600, voice=True):
        counter["n"] += 1
        scene_out.mkdir(parents=True, exist_ok=True)
        if fail_scene is not None and self.title == f"fail{int(fail_scene)}":
            return {
                "title": self.title, "status": "FAILED",
                "error": "рендер упал (тест)",
            }
        video = _tiny_video(scene_out / "final_audio.mp4", seconds=self.duration)
        return {
            "title": self.title, "status": "OK", "error": None,
            "duration": self.duration, "video": str(video),
            "final_audio": str(video),
            "qc": {"status": "READY", "fails": [], "warns": []},
            "takes": [], "mix": None, "diagnostics": {},
            "input_hash": hash_scene(self.to_dict()) if hasattr(self, "to_dict") else "x",
        }

    return fake_render, counter


def _storyboard():
    scenes = []
    for i, (title, env) in enumerate(
        [("s1", "cave"), ("s2", "cave"), ("s3", "cave")], 1
    ):
        scenes.append(
            normalize_scene(
                _scene(actors=[
                    {"character": "steve", "origin": [-0.6, 0.9, 0.0],
                     "actions": [{"action": "idle", "start": 0.0, "duration": 4.0}]}
                ], props=[_door(name=f"Door{i}")], environment=env, duration=4.0)
            )
        )
        scenes[-1]["title"] = f"fail{i}" if i == 2 else title
    return {"title": "F1", "lang": "ru", "source": "authored", "scenes": scenes}


@pytest.fixture
def tiny_storyboard():
    return _storyboard()


def test_produce_film_builds_mp4_and_report(tmp_path, monkeypatch, tiny_storyboard):
    fake_render, _ = _fake_render_factory(tmp_path)
    monkeypatch.setattr(BlockyScene, "render", fake_render)
    report = produce_film(tiny_storyboard, tmp_path / "out", voice=False, resume=True)
    assert report["status"] == "OK"
    assert report["qc"]["status"] == "READY"
    assert (tmp_path / "out" / "film.mp4").exists()
    assert report["continuity_qc"]["ok"] is True
    assert report["scenes"][0]["qc"] == "READY"


def test_render_failure_blocks_ready(tmp_path, monkeypatch, tiny_storyboard):
    fake_render, _ = _fake_render_factory(tmp_path, fail_scene=2)
    monkeypatch.setattr(BlockyScene, "render", fake_render)
    report = produce_film(tiny_storyboard, tmp_path / "out", voice=False)
    assert report["status"] == "PARTIAL"
    assert report["qc"] is not None
    assert report["scenes"][1]["status"] == "FAILED"
    assert report["scenes"][1]["video"] is None
    assert report["duration"] == pytest.approx(8.0, abs=0.2)
    saved = json.loads((tmp_path / "out/film_report.json").read_text(encoding="utf-8"))
    assert saved == report


def test_resume_reuses_unchanged_good_scene(tmp_path, monkeypatch, tiny_storyboard):
    fake_render, counter = _fake_render_factory(tmp_path)
    monkeypatch.setattr(BlockyScene, "render", fake_render)
    out = tmp_path / "out"
    first = produce_film(tiny_storyboard, out, voice=False, resume=False)
    assert first["status"] == "OK"
    assert counter["n"] == 3
    second = produce_film(tiny_storyboard, out, voice=False, resume=True)
    assert second["status"] == "OK"
    for item in second["scenes"]:
        assert item["cached"] is True
    assert counter["n"] == 3  # ни одной перерисовки


def test_resume_rerenders_failed_qc_scene(tmp_path, monkeypatch, tiny_storyboard):
    fake_render, counter = _fake_render_factory(tmp_path)
    monkeypatch.setattr(BlockyScene, "render", fake_render)
    out = tmp_path / "out"
    produce_film(tiny_storyboard, out, voice=False, resume=False)

    # подделать scene_01: status OK, но QC FAILED + неверный hash
    report_path = out / "scene_01" / "scene_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["qc"] = {"status": "FAILED", "fails": ["audio"]}
    report["input_hash"] = "mismatch"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    before = counter["n"]
    rerun = produce_film(tiny_storyboard, out, voice=False, resume=True)
    assert rerun["scenes"][0]["cached"] is False
    assert counter["n"] == before + 1


def test_cli_empty_idea_is_clean_error(tmp_path):
    env = dict(os.environ if (os := __import__("os")) else {})
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "shortsfactory", "blocky-create"],
        cwd=ROOT, env=env, capture_output=True, timeout=120,
    )
    assert proc.returncode == 2

    def _decode(raw: bytes) -> str:
        for encoding in ("utf-8", "cp1251", "cp866"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    stderr = _decode(proc.stderr)
    assert "идея пустая" in stderr
    assert "Traceback" not in stderr


# ------------------------------------------------------------------ F1 gating: PARTIAL


def test_partial_scene_joins_film_as_partial(tmp_path, monkeypatch, tiny_storyboard):
    out = tmp_path / "out"

    def fake_render(self, scene_out, *, timeout=3600, voice=True):
        video = _tiny_video(scene_out / "final_audio.mp4", seconds=self.duration)
        if self.title == "fail2":
            return {
                "title": self.title, "status": "PARTIAL", "error": None,
                "duration": self.duration, "video": str(video),
                "final_audio": str(video),
                "qc": {"status": "PARTIAL", "fails": [], "warns": ["foot"]},
                "takes": [], "mix": None, "diagnostics": {},
                "input_hash": "x",
            }
        return {
            "title": self.title, "status": "OK", "error": None,
            "duration": self.duration, "video": str(video),
            "final_audio": str(video),
            "qc": {"status": "READY", "fails": [], "warns": []},
            "takes": [], "mix": None, "diagnostics": {},
            "input_hash": "x",
        }

    monkeypatch.setattr(BlockyScene, "render", fake_render)
    report = produce_film(tiny_storyboard, out, voice=False)
    assert (out / "film.mp4").exists()
    assert report["status"] == "PARTIAL"
    assert report["qc"]["status"] == "PARTIAL"
    assert report["scenes"][1]["status"] == "PARTIAL"
    assert len(report["scenes"]) == 3


# ------------------------------------------------------------------ F1: actor separation


def test_actor_separation_applied_when_overlapping():
    world = _empty_world()
    scene = normalize_scene(_scene(actors=[
        {"character": "steve", "origin": [0.0, 0.9, 0.0],
         "actions": [{"action": "idle", "start": 0.0, "duration": 4.0}]},
        {"character": "alex", "origin": [0.0, 0.9, 0.0],
         "actions": [{"action": "idle", "start": 0.0, "duration": 4.0}]},
    ]))
    prepared, issues = prepare_scene_continuity(scene, world, scene_index=1)
    xs = sorted(float(a["origin"][0]) for a in prepared["actors"])
    assert xs[1] - xs[0] >= 1.0
    assert not any("телепортируется" in issue for issue in issues)


def test_single_actor_silhouette_check_is_pass():
    from shortsfactory.generative.blocky_qc import scene_qc

    report = {
        "title": "one",
        "status": "OK",
        "duration": 4.0,
        "takes": [],
        "mix": {"duration_s": 4.0, "peak": 0.90, "rms": 0.05, "clipped": False},
        "diagnostics": {
            "fps": 30,
            "blank_frames": {"checked": 5, "blank": 0, "min_std": 0.2, "blank_frames": []},
            "pose_error": {"passed": True, "max_abs_matrix_diff": 1e-6},
            "foot_contact": {
                "windows": 2, "max_drift": 0.0,
                "max_penetration": 0.0, "max_hover": 0.02,
            },
            # actor_gap отсутствует — замер неприменим
        },
        "dialogue_qc": {"ok": True, "issues": []},
        "camera_qc": {"ok": True, "issues": []},
        "props": [],
    }
    payload = {"actors": [{"character": "steve"}]}
    qc = scene_qc(report, payload=payload)
    silhouettes = next(c for c in qc["checks"] if c["id"] == "silhouettes")
    assert silhouettes["status"] == "pass"
    assert qc["status"] == "READY"


def test_actor_reappears_as_statist_in_same_location():
    world = _empty_world()
    s1 = normalize_scene(_scene(actors=[
        {"character": "steve", "origin": [-0.6, 0.9, 0.0],
         "actions": [{"action": "idle", "start": 0.0, "duration": 4.0}]},
        {"character": "alex", "origin": [0.6, 0.9, 0.0],
         "actions": [{"action": "idle", "start": 0.0, "duration": 4.0}]},
    ]))
    p1, _ = prepare_scene_continuity(s1, world, scene_index=1)
    snapshot_final_state(p1, world, scene_index=1)

    s2 = normalize_scene(_scene(actors=[
        {"character": "alex", "origin": [0.6, 0.9, 0.0],
         "actions": [{"action": "idle", "start": 0.0, "duration": 4.0}]},
    ]))
    p2, issues = prepare_scene_continuity(s2, world, scene_index=2)
    # авто-восстановление: Стив снова в кадре как статист, issue нет
    assert issues == []
    chars = [a["character"] for a in p2["actors"]]
    assert "steve" in chars
    steve = next(a for a in p2["actors"] if a["character"] == "steve")
    assert steve["actions"] and steve["actions"][0]["action"] == "idle"


def test_continuity_issues_gate_film_to_partial(tmp_path, monkeypatch, tiny_storyboard):
    """F1-гейт: подтверждённые проблемы непрерывности -> PARTIAL, не OK."""
    fake_render, _ = _fake_render_factory(tmp_path)
    monkeypatch.setattr(BlockyScene, "render", fake_render)
    storyboard = _storyboard()
    # дубликат пропса в первой сцене — реальная проблема непрерывности
    dup = copy.deepcopy(storyboard["scenes"][0]["props"][0])
    dup["open_frame"] = None
    storyboard["scenes"][0]["props"].append(dup)
    report = produce_film(storyboard, tmp_path / "out", voice=False, resume=True)
    assert report["continuity_qc"]["ok"] is False
    assert report["continuity_qc"]["issues"]
    assert report["status"] == "PARTIAL"
