"""
Verify the DRAG-TO-EDGE docking animation -- the specific thing that was asked
for: "drag the ball to the edge and it shrinks into the tab".

This is a different code path from hover expand/collapse (it goes through
_ball_drag_end), so the hover probe passing does NOT prove this one animates.

Sequence:
  1. seed a FREE-FLOATING ball (ball_edge empty) somewhere in the middle
  2. launch, hover the card so it is expanded
  3. mousedown on the card, drag toward the right edge, mouseup near it
  4. sample the window width -> it should ramp 305 -> ... -> 32, not jump

Usage: python tools/probe_dock_anim.py [--src] [path-to-exe]

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
USE_SOURCE = "--src" in sys.argv
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
EXE = Path(_args[0]) if _args else ROOT / "dist" / "时间规划表.exe"
SHOT_DIR = ROOT / "build-logs" / "shots"

BALL_TITLE = "时间规划表悬浮窗"
SCALE = 1.25
CARD_PHYS_W = round(244 * SCALE)      # 305
TAB_PHYS_W = round(26 * SCALE)        # 33

user32 = ctypes.windll.user32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


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
        subprocess.run(["taskkill", "/F", "/IM", "python.exe", "/FI",
                        "WINDOWTITLE eq " + BALL_TITLE], capture_output=True)
    time.sleep(1)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def work_area_right_physical():
    return user32.GetSystemMetrics(0)


def launch(env):
    if USE_SOURCE:
        proc = subprocess.Popen([sys.executable, "-m", "app.main"],
                                cwd=str(ROOT), env=env)
    else:
        proc = subprocess.Popen([str(EXE)], env=env)
    hwnd = 0
    for _ in range(80):
        time.sleep(0.5)
        hwnd = find_ball()
        if hwnd:
            break
    return proc, hwnd


def shot(tag):
    try:
        from PIL import ImageGrab
    except Exception:
        return
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    full = ImageGrab.grab()
    full.crop((full.width - 460, 400, full.width, 1000)).save(
        SHOT_DIR / ("dockanim_" + tag + ".png"))


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    if not USE_SOURCE and not EXE.exists():
        log("FAIL: exe not found: " + str(EXE))
        return 2

    tmp = tempfile.mkdtemp(prefix="tt_dockanim_")
    env = dict(os.environ)
    env["TIMETABLE_DATA_DIR"] = tmp
    data_file = Path(tmp) / "data.json"

    log("target = " + ("SOURCE" if USE_SOURCE else str(EXE)))
    kill_all()
    try:
        # ---- seed ----
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
        data["ball_edge"] = ""              # free floating: no dock, no animation
        data["ball_center"] = 0
        # logical coords, middle of a 2048x1280 logical work area
        data["ball_pos"] = [500, 400]
        data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        log("seeded free-floating ball at logical (500,400)")

        # ---- launch ----
        move_to(60, 60)
        time.sleep(0.3)
        proc, hwnd = launch(env)
        if not hwnd:
            log("FAIL: ball never appeared on 2nd launch")
            return 1
        time.sleep(1.5)

        r = rect_of(hwnd)
        w = r.right - r.left
        log("card rect = (" + str(r.left) + "," + str(r.top) + ") "
            + str(w) + "x" + str(r.bottom - r.top))
        if w < 200:
            log("NOTE: expected an expanded card (free floating). Got a narrow window;")
            log("      the drag test may not be meaningful.")
        shot("00_start")

        # ---- drag it to the right edge and release ----
        cx = r.left + (r.right - r.left) * 0.35      # left part of the card, away from buttons
        cy = r.top + (r.bottom - r.top) * 0.25       # top row area
        target_x = work_area_right_physical() - 24   # basically against the right edge

        log("")
        log("=== dragging from (" + str(int(cx)) + "," + str(int(cy)) + ") to the right edge ===")
        move_to(cx, cy)
        time.sleep(0.4)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.15)

        steps = 12
        for i in range(1, steps + 1):
            x = cx + (target_x - cx) * (i / steps)
            move_to(x, cy)
            time.sleep(0.03)
        time.sleep(0.2)
        log("  releasing at x=" + str(int(target_x)))
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

        # ---- sample the width right after release ----
        widths = []
        t_end = time.perf_counter() + 0.9
        while time.perf_counter() < t_end:
            wv = width_of(hwnd)
            if not widths or widths[-1] != wv:
                widths.append(wv)

        log("  widths after release: " + " -> ".join(str(v) for v in widths))
        shot("01_after_release")

        intermediate = [v for v in set(widths) if TAB_PHYS_W + 3 < v < CARD_PHYS_W - 3]
        log("")
        log("================ SUMMARY ================")
        log("distinct widths : " + str(len(set(widths))))
        log("intermediate    : " + str(len(intermediate)) + " of them")
        log("final width     : " + str(width_of(hwnd)) + "px (tab expected "
            + str(TAB_PHYS_W) + "px)")
        log("")
        if len(intermediate) >= 3:
            log("PASS: the dock-on-release animated (ramp, not a jump).")
            return 0
        log("FAIL: the dock-on-release snapped instead of animating.")
        return 1

    finally:
        kill_all()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
