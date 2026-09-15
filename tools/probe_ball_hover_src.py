"""
Run the hover probe against the SOURCE tree (python -m app.main) instead of the
packaged exe. Same checks, but no PyInstaller round trip, so the fix can be
iterated in seconds.

Usage: python tools/probe_ball_hover_src.py [tag]

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
TAG = sys.argv[1] if len(sys.argv) > 1 else "src"
SHOT_DIR = ROOT / "build-logs" / "shots"

BALL_TITLE = "时间规划表悬浮窗"
EXPANDED_MIN_WIDTH = 200

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


def size_of(r):
    return r.right - r.left, r.bottom - r.top


def rect_text(r):
    return "(x=" + str(r.left) + ",y=" + str(r.top) + ") " + str(r.right - r.left) \
        + "x" + str(r.bottom - r.top)


def move_to(x, y):
    user32.SetCursorPos(int(x), int(y))


def click_at(x, y):
    move_to(x, y)
    time.sleep(0.35)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.08)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def shot(hwnd, tag):
    """Wide shot: a tight crop turns a Windows tooltip into a mystery block."""
    try:
        from PIL import ImageGrab
    except Exception:
        log("  (PIL not available, skipping screenshot)")
        return None
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    r = rect_of(hwnd)
    full = ImageGrab.grab()
    m = 160
    box = (max(0, r.left - m), max(0, r.top - m),
           min(full.width, r.right + m), min(full.height, r.bottom + m))
    path = SHOT_DIR / (TAG + "_" + tag + ".png")
    full.crop(box).save(path)
    log("  screenshot -> " + str(path))
    return path


def kill_all():
    subprocess.run(["taskkill", "/F", "/IM", "时间规划表.exe"],
                   capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "python.exe", "/FI",
                    "WINDOWTITLE eq 时间规划表悬浮窗"], capture_output=True)
    time.sleep(1)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def launch(env):
    proc = subprocess.Popen([sys.executable, "-m", "app.main"],
                            cwd=str(ROOT), env=env)
    hwnd = 0
    for _ in range(80):
        time.sleep(0.5)
        hwnd = find_ball()
        if hwnd:
            break
    return proc, hwnd


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    tmp = tempfile.mkdtemp(prefix="tt_src_")
    env = dict(os.environ)
    env["TIMETABLE_DATA_DIR"] = tmp
    data_file = Path(tmp) / "data.json"

    kill_all()
    procs = []
    try:
        # ---------- seed ----------
        log("--- seeding a docked state (via the source tree) ---")
        proc, hwnd = launch(env)
        procs.append(proc)
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
        data["ball_center"] = 600
        data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

        # ---------- launch with the cursor FAR from the ball ----------
        log("")
        log("--- launching with the cursor parked far away ---")
        move_to(200, 200)
        time.sleep(0.4)
        proc, hwnd = launch(env)
        procs.append(proc)
        if not hwnd:
            log("FAIL: ball never appeared on second launch")
            return 1

        time.sleep(1.8)
        r = rect_of(hwnd)
        w, h = size_of(r)
        log("state 0  fresh launch, cursor far : " + rect_text(r))
        shot(hwnd, "0_launched")

        # ---------- the reported move: one continuous approach ----------
        log("")
        log("--- step 1: move the cursor onto the tab ---")
        cx = r.left + w / 2
        cy = r.top + h / 2
        log("  tab centre = (" + str(int(cx)) + "," + str(int(cy)) + ")")
        move_to(cx, cy)
        time.sleep(1.8)
        r1 = rect_of(hwnd)
        w1, _ = size_of(r1)
        first = w1 > EXPANDED_MIN_WIDTH
        log("  after hover : " + rect_text(r1) + "  expanded=" + str(first))
        shot(hwnd, "1_hover")

        log("")
        log("================ SUMMARY ================")
        log("expanded on FIRST hover (fresh launch) : " + str(first))
        if first:
            log("FIXED: the first hover expands the tab.")
            return 0
        log("STILL BROKEN: the first hover is swallowed.")
        return 1

    finally:
        kill_all()
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
