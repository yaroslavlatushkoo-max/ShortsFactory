import subprocess, sys, json
from pathlib import Path
import numpy as np

def grab(path, n, w, h):
    r = subprocess.run(
        ["ffmpeg","-v","error","-i",str(path),"-vf",f"select=eq(n\\,{n})","-frames:v","1",
         "-f","rawvideo","-pix_fmt","gray","-"], capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.uint8)
    return a.reshape((h,w)) if a.size == w*h else None

def is_blank(frame, threshold=0.002):
    """True if the frame carries no usable signal (blank/black render).

    Mission 16 root cause: an unlit render produced an empty image, so
    ``mean(abs(a-b))`` was identically 0 and was misread as "skinning does
    not deform the mesh".  A blank frame must never be scored as "no change".
    """
    return frame is None or float(np.std(frame.astype(float))) / 255.0 < threshold


def analyze(video, w=1080, h=1920, out=None):
    W,H = w,h
    # regions for a centered upright character (portrait)
    # assume character occupies middle ~40% width, middle ~55% height
    cx = W//2
    halfw = int(W*0.20)
    top = int(H*0.28); bot = int(H*0.92)
    mid = int(H*0.55)
    legs = (int(H*0.68), bot, cx-halfw, cx+halfw)
    arms = (mid, int(H*0.68), cx-halfw, cx+halfw)
    full = (0,H,0,W)
    ids = {
        "idle_a": (10, 14),      # frames within first idle
        "walk_a": (106, 136),     # within travel walk
        "walk_b": (110, 112),     # adjacent mid-travel
        "walk_loop": (112, 128),  # ~1 cycle later
    }
    res = {}
    for name,(n1,n2) in ids.items():
        a = grab(video,n1,W,H); b = grab(video,n2,W,H)
        if a is None or b is None:
            res[name] = {"error":"frame-extract"}
            continue
        if is_blank(a) or is_blank(b):
            # do NOT report MAE 0.0 for a blank render: it is unmeasured
            res[name] = {
                "error": "blank-frame",
                "std_a": round(float(np.std(a.astype(float))), 3),
                "std_b": round(float(np.std(b.astype(float))), 3),
            }
            continue
        def mae(rg):
            y0,y1,x0,x1 = rg
            return float(np.mean(np.abs(a[y0:y1,x0:x1].astype(int)-b[y0:y1,x0:x1].astype(int))))
        res[name] = {"full": mae(full), "arms": mae(arms), "legs": mae(legs)}
    if out:
        Path(out).write_text(json.dumps(res, indent=2), encoding="utf-8")
    return res

if __name__ == "__main__":
    video = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(analyze(video, out=out), ensure_ascii=False, indent=2))
