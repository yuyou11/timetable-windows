"""
Does the ball actually animate, or does it still snap?

"Looks smooth" is not evidence. The measurable difference is whether the native
window passes through INTERMEDIATE sizes:

    animation  -> 305, 268, 224, 180, 140, 104, 74, 52, 40, 33   (a ramp)
    hard cut   -> 305, 33                                        (two values)

So this samples GetWindowRect at high frequency while the ball collapses (and
again while it expands) and prints the widths it saw.

It also saves a few mid-animation screenshots so the look can be checked
instead of guessed about.

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import ctypes
import ctypes.wintypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: --src 用源码直接跑（python -m app.main），迭代时不用每次重打包
USE_SOURCE = "--src" in sys.argv
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
EXE = Path(_args[0]) if _args else ROOT / "dist" / "时间规划表.exe"
SHOT_DIR = ROOT / "build-logs" / "shots"

BALL_TITLE = "时间规划表悬浮窗"
BALL_CENTER = 600

SCALE = 1.25
CARD_PHYS_W = round(244 * SCALE)     # 305
TAB_PHYS_W = round(26 * SCALE)       # 33

user32 = ctypes.windll.user32


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def log(msg):
    print("[" + time.strftime("%H:%M:%S") + "] " + str(msg), flush=True)


def find_ball():
    return user32.FindWindowW(None, BALL_TITLE)


def rect_of(hwnd):
    r = RECT()
    user32.GetWindowRect(ctypes.wintypes.HWND(hwnd), ctypes.byref(r))
    return r


def width_of(hwnd):
    r = rect_of(hwnd)
    return r.right - r.left


def move_to(x, y):
    user32.SetCursorPos(int(x), int(y))


def kill_all():
    subprocess.run(["taskkill", "/F", "/IM", "时间规划表.exe"], capture_output=True)
    if USE_SOURCE:
        # the source run is a python.exe whose window title is the ball title
        subprocess.run(
            ["taskkill", "/F", "/IM", "python.exe", "/FI",
             "WINDOWTITLE eq " + BALL_TITLE],
            capture_output=True,
        )
    time.sleep(1)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def shot(tag):
    try:
        from PIL import ImageGrab
    except Exception:
        return
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    full = ImageGrab.grab()
    # a band around the docked position; wide margins so tooltips are not a mystery
    box = (full.width - 420, 500, full.width, 950)
    full.crop(box).save(SHOT_DIR / ("anim_" + tag + ".png"))


def sample(hwnd, ms, label):
    """Watch the window width for [ms] milliseconds. Returns the widths seen."""
    widths = []
    t_end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < t_end:
        w = width_of(hwnd)
        if not widths or widths[-1] != w:
            widths.append(w)
    log("  " + label + " widths: " + " -> ".join(str(w) for w in widths))
    return widths


def launch(env):
    if USE_SOURCE:
        cmd = [sys.executable, "-m", "app.main"]
        cwd = str(ROOT)
    else:
        cmd = [str(EXE)]
        cwd = None
    proc = subprocess.Popen(cmd, env=env, cwd=cwd)
    hwnd = 0
    for _ in range(80):
        time.sleep(0.5)
        hwnd = find_ball()
        if hwnd:
            break
    return proc, hwnd


def judge(widths, label):
    """Did we see a ramp, or just the two endpoints?"""
    distinct = set(widths)
    intermediate = [w for w in distinct
                    if TAB_PHYS_W + 3 < w < CARD_PHYS_W - 3]
    if len(intermediate) >= 3:
        log("  " + label + ": ANIMATED (" + str(len(intermediate))
            + " intermediate widths)")
        return True
    log("  " + label + ": NOT ANIMATED (only "
        + str(len(distinct)) + " distinct widths, "
        + str(len(intermediate)) + " in between)")
    return False


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    if not USE_SOURCE and not EXE.exists():
        log("FAIL: exe not found: " + str(EXE))
        return 2

    tmp = tempfile.mkdtemp(prefix="tt_anim_")
    env = dict(os.environ)
    env["TIMETABLE_DATA_DIR"] = tmp
    data_file = Path(tmp) / "data.json"

    log("target = " + ("SOURCE (python -m app.main)" if USE_SOURCE else str(EXE)))
    log("expecting: expanded=" + str(CARD_PHYS_W) + "px, collapsed="
        + str(TAB_PHYS_W) + "px (physical, at " + str(SCALE) + "x scale)")

    kill_all()
    try:
        # ---- seed a docked state ----
        proc, hwnd = launch(env)
        if not hwnd:
            log("FAIL: ball never appeared")
            return 1
        for _ in range(30):
            if data_file.exists() and read_json(data_file):
                break
            time.sleep(0.5)
        kill_all()

        data = read_json(data_file) or {}
        data["ball_enabled"] = True
        data["ball_edge"] = "right"
        data["ball_center"] = BALL_CENTER
        data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

        # ---- relaunch with the cursor far away ----
        move_to(200, 200)
        time.sleep(0.3)
        proc, hwnd = launch(env)
        if not hwnd:
            log("FAIL: ball never appeared on 2nd launch")
            return 1
        time.sleep(2.0)

        log("")
        log("startup width = " + str(width_of(hwnd)) + "px (collapsed tab expected)")
        shot("00_startup")

        # ---- EXPAND ----
        log("")
        log("=== expand (hover onto the tab) ===")
        r = rect_of(hwnd)
        cx, cy = r.left + (r.right - r.left) / 2, r.top + (r.bottom - r.top) / 2
        move_to(cx, cy)
        widths_grow = sample(hwnd, 450, "grow")
        shot("01_expanded")
        expanded_ok = judge(widths_grow, "expand")

        # ---- COLLAPSE ----
        log("")
        log("=== collapse (move the cursor away) ===")
        # the collapse is scheduled 380ms after mouseleave (see ball.js)
        move_to(400, 400)
        time.sleep(0.30)
        widths_shrink = sample(hwnd, 600, "shrink")
        shot("02_collapsed")
        collapsed_ok = judge(widths_shrink, "collapse")

        # ---- grab frames from the MIDDLE of the animation ----
        #
        # The numbers above prove the window moves. They say nothing about
        # whether the content fade works -- if it does not, you would see the
        # text crunching into a sliver. So re-run the collapse and shoot at a
        # few offsets. (ImageGrab itself takes tens of ms, so these are
        # approximate moments -- good enough to eyeball the look.)
        log("")
        log("=== capturing frames during a collapse ===")
        for off in (400, 440, 480, 520, 560, 620):
            r = rect_of(hwnd)
            cx, cy = r.left + (r.right - r.left) / 2, r.top + (r.bottom - r.top) / 2
            move_to(cx, cy)          # expand again
            time.sleep(0.8)
            move_to(400, 400)        # leave -> collapse starts ~380ms later
            time.sleep(off / 1000.0)
            shot("collapse_%03dms" % off)
            log("  shot at ~" + str(off) + "ms after mouseleave")
            time.sleep(0.5)

        log("")
        log("================ SUMMARY ================")
        log("expand animated   : " + str(expanded_ok))
        log("collapse animated : " + str(collapsed_ok))
        log("final width       : " + str(width_of(hwnd)) + "px")
        log("")
        if expanded_ok and collapsed_ok:
            log("PASS: both directions pass through intermediate sizes.")
            return 0
        log("FAIL: at least one direction still snaps.")
        return 1

    finally:
        kill_all()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
