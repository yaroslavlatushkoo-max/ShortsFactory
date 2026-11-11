"""2.4: пре-рендер QC-гейт ловит пересечения персонажей, недосягаемые пропсы
и обрезанные payload-кадры до запуска Blender."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shortsfactory.generative.animation_pipeline import blocky_qc_gate as gate


#: Кости достаточно для геометрии: бёдра и обе руки.
BONES = ("hips", "hand_L", "hand_R")


def _bone_value(x: float, y: float, z: float) -> list[float]:
    """``[x, y, z]`` + единичная матрица поворота (row-major), как в payload."""
    return [x, y, z, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]


def _actor_frame(key: str, x: float, y: float) -> dict[str, list[float]]:
    z = {"hips": 0.9, "hand_L": 0.7, "hand_R": 0.7}
    return {f"{key}:{bone}": _bone_value(x, y, z[bone]) for bone in BONES}


def _payload(
    *,
    frames: list[dict[str, list[float]]] | None = None,
    actors: list[dict] | None = None,
    props: list[dict] | None = None,
    fps: int = 30,
) -> dict:
    return {
        "character": "scene",
        "fps": fps,
        "frames": frames if frames is not None else [],
        "actors": actors if actors is not None else [],
        **({"props": props} if props else {}),
    }


def _two_actors(
    steve_x: float = 0.0,
    alex_x: float = 1.5,
    n_frames: int = 6,
) -> dict:
    frames = []
    for _ in range(n_frames):
        frame = _actor_frame("steve", steve_x, 0.0)
        frame.update(_actor_frame("alex", alex_x, 0.0))
        frames.append(frame)
    return _payload(
        frames=frames,
        actors=[{"character": "steve"}, {"character": "alex"}],
    )


def _write_payload(tmp_path: Path, payload: dict, scenario: str = "test") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"payload_{scenario}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ------------------------------------------------------------- чистая проверка


def test_clean_scene_passes():
    report = gate.check_payload(_two_actors())
    assert report["status"] == "pass", report["issues"]
    assert report["issues"] == []
    assert report["checked_frames"] == 2  # 6 кадров / шаг 3


def test_overlapping_actors_fail_and_report_is_written(tmp_path: Path):
    payload = _two_actors(steve_x=0.0, alex_x=0.03)
    _write_payload(tmp_path, payload)
    with pytest.raises(RuntimeError, match="пересекаются"):
        gate.enforce_pre_render_gate(tmp_path, "test", label="тест")
    report = json.loads((tmp_path / "qc_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    codes = {issue["code"] for issue in report["issues"]}
    assert "char_overlap" in codes
    overlap = next(i for i in report["issues"] if i["code"] == "char_overlap")
    assert set(overlap["actors"]) == {"steve", "alex"}
    assert overlap["distance_m"] < gate.OVERLAP_LIMIT_M


def test_too_close_hips_warn(tmp_path: Path):
    # 40 кадров при шаге 3 => 14 проверенных: бёдра 0.3 м весь кадр < порога 10
    payload = _two_actors(steve_x=0.0, alex_x=0.30, n_frames=40)
    _write_payload(tmp_path, payload)
    report = gate.enforce_pre_render_gate(tmp_path, "test")
    assert report is not None and report["status"] == "warn", report["issues"]
    codes = {issue["code"] for issue in report["issues"]}
    assert "char_too_close" in codes
    # предупреждение не останавливает рендер: RuntimeError не бросился
    assert (tmp_path / "qc_report.json").exists()


def test_short_too_close_run_is_silent():
    # 4 проверенных кадра вплотную — короче TOO_CLOSE_MIN_RUN, предупреждения нет
    payload = _two_actors(steve_x=0.0, alex_x=0.30, n_frames=12)
    report = gate.check_payload(payload)
    assert report["status"] == "pass", report["issues"]


def test_out_of_reach_prop_fails(tmp_path: Path):
    frames = [_actor_frame("steve", 0.0, 0.0) for _ in range(45)]
    payload = _payload(
        frames=frames,
        actors=[
            {
                "character": "steve",
                "actions": [
                    {"action": "grab", "start": 0.0, "duration": 1.0,
                     "params": {"prop": "Lantern"}},
                ],
            }
        ],
        props=[{"name": "Lantern", "center": [2.0, 0.0, 1.0], "size": [0.2, 0.2, 0.3]}],
    )
    _write_payload(tmp_path, payload)
    with pytest.raises(RuntimeError, match="не достать никому"):
        gate.enforce_pre_render_gate(tmp_path, "test")
    report = json.loads((tmp_path / "qc_report.json").read_text(encoding="utf-8"))
    issue = next(i for i in report["issues"] if i["code"] == "prop_out_of_reach")
    assert issue["prop"] == "Lantern"
    assert issue["distance_m"] > gate.PROP_REACH_LIMIT_M


def test_reachable_prop_passes_and_carry_is_skipped():
    frames = [_actor_frame("steve", 0.0, 0.0) for _ in range(3)]
    payload = _payload(
        frames=frames,
        actors=[
            {
                "character": "steve",
                "actions": [
                    {"action": "grab", "start": 0.0, "duration": 0.1,
                     "params": {"prop": "Box"}},
                ],
            }
        ],
        props=[
            # рука на (0.262, 0, 0.7)-ish: пропс рядом — досягаем
            {"name": "Box", "center": [0.3, 0.0, 0.7], "size": [0.2, 0.2, 0.2]},
            # носимый пропс на другом конце света — проверка его пропускает
            {"name": "Bag", "center": [9.0, 9.0, 1.0], "size": [0.2, 0.2, 0.2],
             "carry": {"actor": "steve", "action": "grab"}},
        ],
    )
    report = gate.check_payload(payload)
    assert report["status"] == "pass", report["issues"]


def test_one_actor_pantomime_is_only_a_warn():
    # lost_diamond-кейс: один дотянулся, второй пантомима — провала нет
    frames = [_actor_frame("steve", 0.0, 0.0) for _ in range(4)]
    for frame in frames:
        frame.update(_actor_frame("alex", 3.0, 0.0))
    payload = _payload(
        frames=frames,
        actors=[
            {"character": "steve", "actions": [
                {"action": "interact", "start": 0.0, "duration": 0.2,
                 "params": {"prop": "Gem"}}]},
            {"character": "alex", "actions": [
                {"action": "interact", "start": 0.0, "duration": 0.2,
                 "params": {"prop": "Gem"}}]},
        ],
        props=[{"name": "Gem", "center": [0.3, 0.0, 0.7], "size": [0.1, 0.1, 0.1]}],
    )
    report = gate.check_payload(payload)
    assert report["status"] == "warn", report["issues"]
    assert {i["code"] for i in report["issues"]} == {"prop_reach_miss"}


def test_truncated_frames_fail():
    payload = _two_actors(n_frames=4)
    del payload["frames"][-1]["alex:hips"]
    report = gate.check_payload(payload)
    assert report["status"] == "fail", report["issues"]
    assert any(i["code"] == "frames_mismatch" for i in report["issues"])


def test_empty_frames_fail():
    report = gate.check_payload(_payload(actors=[{"character": "steve"}]))
    assert report["status"] == "fail"
    assert any(i["code"] == "frames_empty" for i in report["issues"])


# --------------------------------------------------------------- обвязка гейта


def test_env_var_disables_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    payload = _two_actors(steve_x=0.0, alex_x=0.01)  # пересечение
    _write_payload(tmp_path, payload)
    monkeypatch.setenv(gate.QC_GATE_ENV, "0")
    assert gate.gate_enabled() is False
    assert gate.enforce_pre_render_gate(tmp_path, "test") is None
    assert not (tmp_path / "qc_report.json").exists()


def test_module_constant_disables_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    payload = _two_actors(steve_x=0.0, alex_x=0.01)
    _write_payload(tmp_path, payload)
    monkeypatch.setattr(gate, "QC_GATE_ENABLED", False)
    assert gate.enforce_pre_render_gate(tmp_path, "test") is None


def test_missing_payload_warns_and_writes_report(tmp_path: Path):
    report = gate.enforce_pre_render_gate(tmp_path, "absent")
    assert report is not None and report["status"] == "warn"
    assert report["issues"][0]["code"] == "payload_missing"
    assert (tmp_path / "qc_report.json").exists()


def test_gate_error_becomes_warn_not_crash():
    report = gate.check_payload({"frames": 5, "actors": None})
    assert report["status"] == "warn"
    assert report["issues"][0]["code"] == "gate_error"
