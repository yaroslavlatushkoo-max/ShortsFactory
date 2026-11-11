"""Технический отчёт по всем mp4 в output/ — для аудита качества."""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
report = []
for mp4 in sorted((ROOT / "output").rglob("*.mp4")):
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(mp4)],
        capture_output=True, text=True)
    info = json.loads(probe.stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), {})
    item = {
        "file": str(mp4.relative_to(ROOT)),
        "duration_s": round(float(info["format"]["duration"]), 2),
        "size_mb": round(mp4.stat().st_size / 1e6, 2),
        "total_kbps": round(int(info["format"]["bit_rate"]) / 1000),
        "video": f'{v["codec_name"]} {v.get("profile")} {v["width"]}x{v["height"]} '
                 f'fps={v.get("r_frame_rate")} pix={v.get("pix_fmt")}',
        "audio": f'{a.get("codec_name")} {a.get("sample_rate")}Hz {a.get("channels")}ch',
    }
    # громкость (только аудио-декод — быстро)
    loud = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(mp4),
         "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    tail = loud[loud.rfind("Summary:"):]
    for key, pat in [("lufs_i", r"I:\s+(-?[\d.]+) LUFS"),
                     ("lra", r"LRA:\s+(-?[\d.]+) LU"),
                     ("true_peak_dbtp", r"Peak:\s+(-?[\d.]+) dBFS")]:
        m = re.search(pat, tail)
        item[key] = float(m.group(1)) if m else None
    # чёрные и застывшие кадры (полный декод — медленнее)
    det = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(mp4),
         "-vf", "blackdetect=d=0.1:pix_th=0.10,freezedetect=n=-60dB:d=1.5",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    item["black_spans"] = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", det)
    item["freeze_spans"] = re.findall(r"freeze_start:\s*([\d.]+)", det)
    report.append(item)
    print(json.dumps(item, ensure_ascii=False))

out = ROOT / "output" / "video_audit.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("\nСохранено:", out)
