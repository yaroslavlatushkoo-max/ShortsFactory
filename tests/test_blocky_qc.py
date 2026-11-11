"""E13: QC-гейты ловят чёрные кадры, дрейф, рассинхрон и прыжки привязки."""

from __future__ import annotations

from pathlib import Path

from shortsfactory.generative.blocky_qc import (
    FAILED,
    PARTIAL,
    READY,
    film_qc,
    frame_timecode,
    parse_srt,
    scene_qc,
    timecode,
)


def _report(**extra):
    report = {
        "title": "Тестовая сцена",
        "status": "OK",
        "duration": 8.0,
        "takes": [
            {"character": "steve", "text": "Привет", "start": 2.2, "duration": 1.1}
        ],
        "mix": {"duration_s": 8.0, "peak": 0.95, "rms": 0.1, "clipped": False},
        "diagnostics": {
            "fps": 30,
            "blank_frames": {"checked": 12, "blank": 0, "min_std": 0.2, "blank_frames": []},
            "pose_error": {"passed": True, "max_abs_matrix_diff": 1e-06},
            "foot_contact": {
                "windows": 4,
                "max_drift": 0.0,
                "max_penetration": 0.0,
                "max_hover": 0.02,
            },
            "actor_gap": {"measured": True, "min_gap": 0.55, "min_frame": 40},
        },
        "dialogue_qc": {"ok": True, "issues": []},
        "camera_qc": {"ok": True, "issues": []},
        "props": [
            {
                "preset": "spring_box",
                "pos_track": [[-0.45, 0.35, 0.16]] * 30,
            }
        ],
    }
    report.update(extra)
    return report


def _srt(tmp_path: Path, start: str = "00:00:02,200", end: str = "00:00:03,300") -> Path:
    path = tmp_path / "ru.srt"
    path.write_text(f"1\n{start} --> {end}\nSteve: Привет\n", encoding="utf-8")
    return path


def _by_id(qc: dict) -> dict:
    return {check["id"]: check for check in qc["checks"]}


# ------------------------------------------------------------------ formatting


def test_timecodes_are_human_readable():
    assert timecode(83.4) == "1:23.400"
    assert timecode(3600) == "1:00:00.000"
    assert frame_timecode(31, 30) == "0:01.000"


def test_parse_srt_reads_cues():
    cues = parse_srt("1\n00:00:02,200 --> 00:00:03,300\nПривет\n")
    assert cues == [{"start": 2.2, "end": 3.3, "text": "Привет"}]


# ------------------------------------------------------------------ happy path


def test_healthy_scene_is_ready(tmp_path):
    qc = scene_qc(_report(), srt_path=_srt(tmp_path))
    assert qc["status"] == READY, qc
    assert qc["fails"] == [] and qc["warns"] == []


# ------------------------------------------------------------------ failure modes


def test_blank_frame_fails_with_timecode(tmp_path):
    report = _report()
    report["diagnostics"]["blank_frames"].update(
        {"blank": 1, "blank_frames": [31], "min_std": 0.001}
    )
    qc = scene_qc(report, srt_path=_srt(tmp_path))
    assert qc["status"] == FAILED
    assert "blank_frames" in qc["fails"]
    assert _by_id(qc)["blank_frames"]["timecodes"] == ["0:01.000"]


def test_foot_drift_fails(tmp_path):
    report = _report()
    report["diagnostics"]["foot_contact"]["max_drift"] = 0.08
    qc = scene_qc(report, srt_path=_srt(tmp_path))
    assert qc["status"] == FAILED
    assert "foot" in qc["fails"]


def test_actor_overlap_fails(tmp_path):
    report = _report()
    report["diagnostics"]["actor_gap"].update({"min_gap": 0.0, "min_frame": 100})
    qc = scene_qc(report, srt_path=_srt(tmp_path))
    assert qc["status"] == FAILED
    assert _by_id(qc)["silhouettes"]["timecodes"] == ["0:03.300"]


def test_prop_binding_jump_fails(tmp_path):
    report = _report()
    report["props"] = [
        {"pos_track": [[0, 0, 0.16], [0.9, 0, 0.16], [0.9, 0, 0.16]]}
    ]
    qc = scene_qc(report, srt_path=_srt(tmp_path))
    assert qc["status"] == FAILED
    assert "props" in qc["fails"]


def test_subtitle_out_of_sync_fails(tmp_path):
    qc = scene_qc(_report(), srt_path=_srt(tmp_path, start="00:00:01,000"))
    assert qc["status"] == FAILED
    assert "subtitles" in qc["fails"]


def test_silent_clip_fails(tmp_path):
    report = _report()
    report["mix"].update({"peak": 0.0, "rms": 0.0})
    qc = scene_qc(report, srt_path=_srt(tmp_path))
    assert qc["status"] == FAILED
    assert "audio" in qc["fails"]


def test_audio_shorter_than_video_fails(tmp_path):
    report = _report()
    report["mix"]["duration_s"] = 6.0
    qc = scene_qc(report, srt_path=_srt(tmp_path))
    assert qc["status"] == FAILED
    assert "audio" in qc["fails"]


# ------------------------------------------------------------------ old renders


def test_old_render_without_silhouette_probe_is_partial(tmp_path):
    report = _report()
    report["diagnostics"].pop("actor_gap")
    # два актёра без замера -> предупреждение (PARTIAL)
    payload = {"actors": [{"character": "steve"}, {"character": "alex"}]}
    qc = scene_qc(report, payload=payload, srt_path=_srt(tmp_path))
    assert qc["status"] == PARTIAL
    assert qc["warns"] == ["silhouettes"]


# ------------------------------------------------------------------ films


def _film(tmp_path: Path, **extra):
    sheet = tmp_path / "contact_sheet.jpg"
    sheet.write_bytes(b"jpeg")
    report = {
        "title": "Фильм",
        "status": "OK",
        "duration": 45.0,
        "planned_duration": 45.0,
        "scenes": [{"index": 1, "status": "OK"}, {"index": 2, "status": "OK"}],
        "contact_sheet": str(sheet),
    }
    report.update(extra)
    return report


def test_healthy_film_is_ready(tmp_path):
    srt = tmp_path / "ru.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:44,000\nSteve: Привет\n", encoding="utf-8")
    qc = film_qc(_film(tmp_path), srt_path=srt)
    assert qc["status"] == READY, qc


def test_film_with_failed_scene_fails(tmp_path):
    report = _film(tmp_path, scenes=[{"index": 1, "status": "OK"}, {"index": 2, "status": "FAILED"}])
    qc = film_qc(report)
    assert qc["status"] == FAILED
    assert "scenes" in qc["fails"]


def test_film_subtitle_past_the_end_fails(tmp_path):
    srt = tmp_path / "ru.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:46,000\nSteve: Привет\n", encoding="utf-8")
    qc = film_qc(_film(tmp_path), srt_path=srt)
    assert qc["status"] == FAILED
    assert "subtitles" in qc["fails"]


def test_silhouette_overlap_during_handoff_is_partial_not_failed():
    report = _report()
    report["diagnostics"]["actor_gap"] = {"measured": True, "min_gap": 0.001, "min_frame": 100}
    qc = scene_qc(report, handoff_windows=[(3.0, 5.0)])
    sil = next(c for c in qc["checks"] if c["id"] == "silhouettes")
    assert sil["status"] == "warn"
    assert qc["status"] == PARTIAL


def test_silhouette_overlap_without_handoff_is_failed():
    report = _report()
    report["diagnostics"]["actor_gap"] = {"measured": True, "min_gap": 0.001, "min_frame": 100}
    qc = scene_qc(report)
    sil = next(c for c in qc["checks"] if c["id"] == "silhouettes")
    assert sil["status"] == "fail"
    assert qc["status"] == FAILED
