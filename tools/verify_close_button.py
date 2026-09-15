"""
End-to-end check of the reported bug: click the ball's "close" button, then
make sure the window actually goes away and STAYS away.

Usage:
    python tools/verify_close_button.py [path-to-exe]

## Why the ball must be DOCKED for this test to mean anything

main._ball_hover() starts with:

    if self.ball is None or self._ball_edge is None:
        return {"ok": True}

So when the ball is free-floating, the whole hover path -- the one that
triggers the reported failure -- is skipped. A first version of this script
launched the app with a fresh data file, which means a floating ball, and it
reported PASS. That pass meant nothing: the bug's trigger was never reached.

The reported case had "ball_edge": "right" -- docked. So this script seeds a
docked state first, and only then clicks.

## How it knows the click really landed on the button

Landing on "close" runs api.hide_ball(), which writes ball_enabled=false into
data.json. If that key did not flip, the click missed and the run is reported
INCONCLUSIVE rather than as a pass.

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXE = ROOT / "dist" / "时间规划表.exe"
BALL_TITLE = "时间规划表悬浮窗"

user32 = ctypes.windll.user32

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

# A collapsed dock tab is ~26x76 logical -> ~33x95 physical at 125%.
# An expanded card is 244x104 logical -> ~305x130 physical.
EXPANDED_MIN_WIDTH = 200


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def log(msg):
    print("[" + time.strftime("%H:%M:%S") + "] " + str(msg), flush=True)


def find_ball():
    return user32.FindWindowW(None, BALL_TITLE)


def rect_of(hwnd):
    r = RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
        return None
    return r


def rect_text(r):
    return "(" + str(r.left) + "," + str(r.top) + ") " \
        + str(r.right - r.left) + "x" + str(r.bottom - r.top)


def is_visible(hwnd):
    return bool(user32.IsWindowVisible(hwnd))


def move_to(x, y):
    user32.SetCursorPos(int(x), int(y))


def click_at(x, y):
    move_to(x, y)
    time.sleep(0.4)          # let the hover state settle
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.08)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def kill_all():
    subprocess.run(["taskkill", "/F", "/IM", "时间规划表.exe"], capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "时间规划表-debug.exe"], capture_output=True)
    time.sleep(1)


def launch(exe, env):
    proc = subprocess.Popen([str(exe)], env=env)
    hwnd = 0
    for _ in range(60):
        time.sleep(0.5)
        hwnd = find_ball()
        if hwnd:
            break
    return proc, hwnd


def main():
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXE
    if not exe.exists():
        log("FAIL: exe not found: " + str(exe))
        return 2

    # GetWindowRect and the cursor both live in physical pixels for a
    # per-monitor-aware process. Without this the rect comes back virtualized
    # and every computed click coordinate would be wrong.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    tmp = tempfile.mkdtemp(prefix="tt_e2e_")
    env = dict(os.environ)
    env["TIMETABLE_DATA_DIR"] = tmp
    data_file = Path(tmp) / "data.json"

    log("exe      = " + str(exe))
    log("data dir = " + tmp)

    kill_all()
    try:
        # ================= phase A: let the app write its own data file =====
        log("")
        log("--- phase A: first launch, just to seed a data file ---")
        proc, hwnd = launch(exe, env)
        if not hwnd:
            log("FAIL: ball window never appeared")
            return 1
        for _ in range(20):
            if data_file.exists() and read_json(data_file):
                break
            time.sleep(0.5)
        kill_all()
        log("seeded data.json: " + str(data_file.exists()))

        # ================= phase B: force the DOCKED state ==================
        data = read_json(data_file)
        if not isinstance(data, dict):
            log("FAIL: could not read seeded data.json")
            return 1
        data["ball_enabled"] = True
        data["ball_edge"] = "right"      # <-- the reported case
        data["ball_center"] = 600
        data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        log("patched  : ball_edge=right, ball_center=600")

        # ================= phase C: the actual test ==========================
        log("")
        log("--- phase C: docked ball, hover to expand, click close ---")
        proc, hwnd = launch(exe, env)
        if not hwnd:
            log("FAIL: ball window never appeared on second launch")
            return 1

        r = rect_of(hwnd)
        log("collapsed rect = " + rect_text(r) + "   visible=" + str(is_visible(hwnd)))

        # A freshly docked ball suppresses the first hover on purpose
        # (_ball_suppress_expand), so approach -> leave -> approach again.
        expanded = False
        for attempt in range(3):
            move_to(r.left + (r.right - r.left) / 2, r.top + (r.bottom - r.top) / 2)
            time.sleep(0.9)
            r = rect_of(hwnd)
            if (r.right - r.left) > EXPANDED_MIN_WIDTH:
                expanded = True
                break
            # move away to clear the suppress flag, then try again
            move_to(50, 50)
            time.sleep(1.0)
            log("  hover attempt " + str(attempt + 1) + ": still collapsed, retrying")

        r = rect_of(hwnd)
        log("after hover    = " + rect_text(r))
        if not expanded:
            log("INCONCLUSIVE: the ball never expanded, so there is no close button to click")
            return 3
        log("card is expanded -- the close button is now on screen")

        # ================= click the close button ==========================
        # From ball.html: card 244x104 logical, padding 9px 11px, .row3 has
        # padding-left 13px, two equal buttons, 6px gap; close is the right one.
        # As a ratio of the window box so it works at any DPI:
        #     x ~= (24 + 101.5 + 6 + 50.75) / 244 = 0.746
        #     y ~= (104 - 9 - 12) / 104           = 0.798
        w = r.right - r.left
        h = r.bottom - r.top
        cx = r.left + w * 0.746
        cy = r.top + h * 0.798
        log("clicking close at (" + str(int(cx)) + "," + str(int(cy)) + ")")
        click_at(cx, cy)

        # The collapse callback fires 380ms after mouseleave; give it room.
        time.sleep(2.5)

        after = read_json(data_file) or {}
        enabled_after = after.get("ball_enabled")
        visible_after = is_visible(hwnd)
        r_after = rect_of(hwnd)

        log("")
        log("ball_enabled after = " + str(enabled_after))
        log("visible after      = " + str(visible_after))
        if r_after:
            log("rect after         = " + rect_text(r_after))
        log("")

        if enabled_after is not False:
            log("INCONCLUSIVE: ball_enabled did not become false,")
            log("              so the click probably missed the button.")
            return 3

        log("click definitely landed on the close button (ball_enabled=false)")
        if visible_after:
            log("FAIL: the window is STILL VISIBLE -- bug reproduced")
            return 1

        log("PASS: window stayed hidden after close")
        return 0

    finally:
        kill_all()
        time.sleep(0.5)
        shutil.rmtree(tmp, ignore_errors=True)
        log("temp dir removed")


if __name__ == "__main__":
    raise SystemExit(main())
