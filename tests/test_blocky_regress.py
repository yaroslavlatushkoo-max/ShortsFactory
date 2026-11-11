"""E14: эталонные сцены ловят непреднамеренные изменения постановки."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from shortsfactory.generative.blocky_regress import (
    GOLDEN_NAME,
    build_golden,
    compare_scene,
    run_regression,
    scene_metrics,
)

ROOT = Path(__file__).resolve().parents[1]
SCENE = ROOT / "scenes" / "blocky" / "props_demo.json"


def _root(tmp_path: Path, name: str = "props_demo") -> Path:
    target = tmp_path / "scenes" / "blocky"
    target.mkdir(parents=True)
    shutil.copy(SCENE, target / f"{name}.json")
    return tmp_path


def test_metrics_are_stable_and_sorted():
    metrics = scene_metrics(SCENE)
    assert metrics["fps"] == 30
    assert metrics["actors"] == ["alex", "steve"]
    assert metrics["cut_frames"] == sorted(metrics["cut_frames"])
    assert metrics["shots"] == len(metrics["cut_frames"])


def test_update_then_pass(tmp_path):
    root = _root(tmp_path)
    updated = run_regression(root, update=True)
    assert updated["status"] == "UPDATED"
    assert (root / "scenes" / "blocky" / GOLDEN_NAME).exists()

    result = run_regression(root)
    assert result["status"] == "PASS", result
    assert result["checked"] == 1


def test_changed_camera_cut_fails(tmp_path):
    root = _root(tmp_path)
    run_regression(root, update=True)
    golden_path = root / "scenes" / "blocky" / GOLDEN_NAME
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    golden["scenes"]["props_demo"]["cut_frames"][1] = 26
    golden_path.write_text(json.dumps(golden), encoding="utf-8")

    result = run_regression(root)
    assert result["status"] == "FAILED"
    assert any("cut_frames" in issue for issue in result["issues"])


def test_missing_scene_fails(tmp_path):
    root = _root(tmp_path)
    run_regression(root, update=True)
    (root / "scenes" / "blocky" / "props_demo.json").unlink()

    result = run_regression(root)
    assert result["status"] == "FAILED"
    assert any("пропала сцена" in issue for issue in result["issues"])


def test_new_scene_without_golden_is_reported(tmp_path):
    root = _root(tmp_path)
    run_regression(root, update=True)
    shutil.copy(SCENE, root / "scenes" / "blocky" / "second.json")

    result = run_regression(root)
    assert result["status"] == "FAILED"
    assert any("без эталона" in issue for issue in result["issues"])


def test_missing_golden_does_not_crash(tmp_path):
    result = run_regression(_root(tmp_path))
    assert result["status"] == "FAILED"
    assert any("нет эталона" in issue for issue in result["issues"])


def test_compare_ignores_untracked_and_compares_tracked():
    expected = {"duration": 8.0, "shots": 5, "noise": 1}
    actual = {"duration": 8.0, "shots": 6, "noise": 2}
    issues = compare_scene("demo", expected, actual)
    assert issues == ["demo: shots 5 → 6"]


def test_build_golden_skips_the_golden_file(tmp_path):
    root = _root(tmp_path)
    (root / "scenes" / "blocky" / GOLDEN_NAME).write_text("{}", encoding="utf-8")
    built = build_golden(root)
    assert "regression" not in built["scenes"]
