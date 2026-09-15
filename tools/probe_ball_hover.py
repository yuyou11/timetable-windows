"""
Probe how the docked ball responds to a genuine mouse approach.

Reported: on a fresh launch the collapsed dock tab does NOT expand when the
mouse is moved onto it; clicking it does expand.

This script reproduces that and also grabs screenshots so the rendering can be
looked at instead of guessed about.

Sequence:
  1. seed a DOCKED state (ball_edge=right) -- matching the reported case
  2. park the cursor far away, launch
  3. move the cursor onto the tab      -> does it expand?   (reported: no)
  4. leave, then re-enter              -> does it expand?   (expect yes)
  5. click it                          -> does it expand?   (reported: yes)

Each step logs the window rect and saves a cropped screenshot.

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

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parent.parent
EXE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "时间规划表.exe"
SHOT_DIR = ROOT / "build-logs" / "shots"

BALL_TITLE = "时间规划表悬浮窗"
TAG = sys.argv[2] if len(sys.argv) > 2 else "probe"

user32 = ctypes.windll.user32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

# collapsed tab is ~26x76 logical; expanded card is 244x104 logical
EXPANDED_MIN_WIDTH = 200


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def log(msg):
    print("[" + time.strftime("%H:%M:%S") + "] " + str(msg), flush=True)


def find_ball():
    return user32.FindWindowW(None, BALL_TITLE)


def rect_of(hwnd):
    r = RECT()
    user32.GetWindowRect(ctypes.wintypes.HWND(hwnd), ctypes.byref(r))
    return r


def rect_text(r):
    return "(x=" + str(r.left) + ",y=" + str(r.top) + ") "\
        + str(r.right - r.left) + "x" + str(r.bottom - r.top)


def size_of(r):
    return r.right - r.left, r.bottom - r.top


def visible(hwnd):
    return bool(user32.IsWindowVisible(hwnd))


def cursor():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def move_to(x, y):
    user32.SetCursorPos(int(x), int(y))


def click_at(x, y):
    move_to(x, y)
    time.sleep(0.35)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.08)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def shot(hwnd, tag):
    """Grab the whole window plus a generous margin.

    A tight crop hides what the user actually sees -- in particular a Windows
    TOOLTIP is drawn OUTSIDE the window (below-right of the cursor), so a
    small crop renders it as an unexplained coloured block. That cost me a
    wrong first guess, so take a wide shot.
    """
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    r = rect_of(hwnd)
    full = ImageGrab.grab()
    m = 160
    box = (max(0, r.left - m), max(0, r.top - m),
           min(full.width, r.right + m), min(full.height, r.bottom + m))
    crop = full.crop(box)
    path = SHOT_DIR / (TAG + "_" + tag + ".png")
    crop.save(path)
    log("  screenshot -> " + str(path))
    return path


def kill_all():
    subprocess.run(["taskkill", "/F", "/IM", "时间规划表.exe"], capture_output=True)
    time.sleep(1)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    if not EXE.exists():
        log("FAIL: exe not found: " + str(EXE))
        return 2

    tmp = tempfile.mkdtemp(prefix="tt_hover_")
    env = dict(os.environ)
    env["TIMETABLE_DATA_DIR"] = tmp
    data_file = Path(tmp) / "data.json"

    start_cursor = cursor()
    log("exe = " + str(EXE))
    log("cursor at start of run = " + str(start_cursor))
    kill_all()

    try:
        # ---------- seed ----------
        log("")
        log("--- seeding a docked state ---")
        proc = subprocess.Popen([str(EXE)], env=env)
        hwnd = 0
        for _ in range(60):
            time.sleep(0.5)
            hwnd = find_ball()
            if hwnd:
                break
        if not hwnd:
            log("FAIL: ball never appeared")
            return 1
        for _ in range(20):
            if data_file.exists() and read_json(data_file):
                break
            time.sleep(0.5)
        kill_all()

        data = read_json(data_file) or {}
        data["ball_enabled"] = True
        data["ball_edge"] = "right"
        data["ball_center"] = 600
        data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

        # ---------- launch with the cursor FAR away ----------
        log("")
        log("--- launching with the cursor parked far from the ball ---")
        move_to(200, 200)
        time.sleep(0.4)
        proc = subprocess.Popen([str(EXE)], env=env)
        hwnd = 0
        for _ in range(60):
            time.sleep(0.5)
            hwnd = find_ball()
            if hwnd:
                break
        if not hwnd:
            log("FAIL: ball never appeared on second launch")
            return 1

        time.sleep(1.5)          # let it settle, cursor still far away
        r = rect_of(hwnd)
        w, h = size_of(r)
        log("state 0  fresh launch, cursor far away : " + rect_text(r)
            + "  visible=" + str(visible(hwnd)))
        shot(hwnd, "0_launched")

        # ---------- step 1: approach from outside ----------
        log("")
        log("--- step 1: move the cursor onto the tab (ONE continuous approach) ---")
        cx = r.left + w / 2
        cy = r.top + h / 2
        log("  tab centre = (" + str(int(cx)) + "," + str(int(cy)) + ")")
        move_to(cx, cy)
        time.sleep(1.5)
        r1 = rect_of(hwnd)
        w1, h1 = size_of(r1)
        log("  after hover : " + rect_text(r1)
            + "  expanded=" + str(w1 > EXPANDED_MIN_WIDTH))
        shot(hwnd, "1_hover")
        expanded_on_first_hover = w1 > EXPANDED_MIN_WIDTH

        # ---------- step 2: leave, then re-enter ----------
        log("")
        log("--- step 2: leave, then re-enter ---")
        move_to(400, 400)
        time.sleep(1.5)
        r_mid = rect_of(hwnd)
        log("  after leaving : " + rect_text(r_mid))
        move_to(cx, cy)
        time.sleep(1.5)
        r2 = rect_of(hwnd)
        w2, h2 = size_of(r2)
        log("  after re-enter : " + rect_text(r2)
            + "  expanded=" + str(w2 > EXPANDED_MIN_WIDTH))
        shot(hwnd, "2_reenter")
        expanded_on_reenter = w2 > EXPANDED_MIN_WIDTH

        # ---------- step 3: click ----------
        if not expanded_on_reenter:
            log("")
            log("--- step 3: it is still collapsed, so click it ---")
            click_at(cx, cy)
            time.sleep(1.5)
            r3 = rect_of(hwnd)
            w3, h3 = size_of(r3)
            log("  after click : " + rect_text(r3)
                + "  expanded=" + str(w3 > EXPANDED_MIN_WIDTH))
            shot(hwnd, "3_click")
            expanded_on_click = w3 > EXPANDED_MIN_WIDTH
        else:
            expanded_on_click = None

        log("")
        log("================ SUMMARY ================")
        log("expanded on FIRST hover (fresh launch) : " + str(expanded_on_first_hover))
        log("expanded on re-enter                   : " + str(expanded_on_reenter))
        log("expanded on click                      : " + str(expanded_on_click))
        log("")
        if not expanded_on_first_hover and expanded_on_reenter:
            log("REPRODUCED: the very first hover after launch is swallowed.")
            return 1
        if expanded_on_first_hover:
            log("NOT reproduced: the first hover did expand.")
            return 0
        log("INCONCLUSIVE")
        return 3

    finally:
        kill_all()
        move_to(*start_cursor)
        shutil.rmtree(tmp, ignore_errors=True)
        log("cursor restored to " + str(start_cursor))


if __name__ == "__main__":
    raise SystemExit(main())
