"""
End-to-end check of the hover fix, against the packaged exe.

Two cases, and BOTH matter:

  A) cursor parked FAR from the ball at launch
     -> the first hover must expand the tab   (this is the reported bug)

  B) cursor ALREADY on the ball at launch
     -> it must stay collapsed                (the legitimate suppress case)

Case B is the reason the guard exists: when the pointer is already sitting on
the ball, the page's synthetic mouseenter would otherwise pop the card open
without the user doing anything. The fix must keep that behaviour. If a change
makes case A pass but case B fail, it has just traded one bug for another.

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
EXE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "时间规划表.exe"

BALL_TITLE = "时间规划表悬浮窗"
EXPANDED_MIN_WIDTH = 200

# data.json is written in LOGICAL pixels; Win32 reports PHYSICAL ones.
# The display here is 2560x1600 @ 125%, so physical = logical * 1.25.
SCALE = 1.25
BALL_CENTER = 600          # logical, along the right edge
TAB_SHORT, TAB_LONG = 26, 76

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


def size_of(r):
    return r.right - r.left, r.bottom - r.top


def rect_text(r):
    return "(x=" + str(r.left) + ",y=" + str(r.top) + ") " + str(r.right - r.left) \
        + "x" + str(r.bottom - r.top)


def move_to(x, y):
    user32.SetCursorPos(int(x), int(y))


def kill_all():
    subprocess.run(["taskkill", "/F", "/IM", "时间规划表.exe"], capture_output=True)
    time.sleep(1)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def launch(env, cursor_at):
    """Start the app with the cursor placed at (x, y) first."""
    move_to(*cursor_at)
    time.sleep(0.4)
    proc = subprocess.Popen([str(EXE)], env=env)
    hwnd = 0
    for _ in range(80):
        time.sleep(0.5)
        hwnd = find_ball()
        if hwnd:
            break
    return proc, hwnd


def seed_state(tmp, data_file, env):
    """First launch just to get a data file written, then force a docked state."""
    proc, hwnd = launch(env, (200, 200))
    if not hwnd:
        return False
    for _ in range(30):
        if data_file.exists() and read_json(data_file):
            break
        time.sleep(0.5)
    kill_all()

    data = read_json(data_file)
    if not isinstance(data, dict):
        return False
    data["ball_enabled"] = True
    data["ball_edge"] = "right"
    data["ball_center"] = BALL_CENTER
    data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return True


def expected_tab_centre():
    """Where the collapsed tab will be, in physical pixels."""
    # work area right edge (logical) = 2560 / 1.25 = 2048
    area_right_logical = round(user32.GetSystemMetrics(0) / SCALE)
    x = area_right_logical - TAB_SHORT          # logical
    y = BALL_CENTER - TAB_LONG // 2             # logical
    return (round(x * SCALE) + round(TAB_SHORT * SCALE / 2),
            round(y * SCALE) + round(TAB_LONG * SCALE / 2))


def case_a(env, data_file):
    """Cursor far away at launch -> the first hover must expand."""
    log("")
    log("=== CASE A: cursor FAR from the ball at launch ===")
    proc, hwnd = launch(env, (200, 200))
    if not hwnd:
        log("  FAIL: ball never appeared")
        return None
    time.sleep(1.8)
    r = rect_of(hwnd)
    log("  at launch         : " + rect_text(r))

    w, h = size_of(r)
    cx, cy = r.left + w / 2, r.top + h / 2
    move_to(cx, cy)
    time.sleep(1.8)
    r2 = rect_of(hwnd)
    expanded = size_of(r2)[0] > EXPANDED_MIN_WIDTH
    log("  after first hover : " + rect_text(r2) + "  expanded=" + str(expanded))
    kill_all()
    return expanded


def case_b(env, data_file):
    """Cursor already on the tab at launch -> must stay collapsed."""
    log("")
    log("=== CASE B: cursor ALREADY on the tab at launch ===")
    cx, cy = expected_tab_centre()
    log("  parking cursor at (" + str(cx) + "," + str(cy) + ") before launch")
    proc, hwnd = launch(env, (cx, cy))
    if not hwnd:
        log("  FAIL: ball never appeared")
        return None
    time.sleep(2.5)          # give any spurious expand time to happen
    r = rect_of(hwnd)
    expanded = size_of(r)[0] > EXPANDED_MIN_WIDTH
    log("  state after launch: " + rect_text(r) + "  expanded=" + str(expanded))
    log("  (must be False -- the pointer was already there, so there is no")
    log("   genuine 'enter' to react to)")
    kill_all()
    return expanded


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    if not EXE.exists():
        log("FAIL: exe not found: " + str(EXE))
        return 2

    tmp = tempfile.mkdtemp(prefix="tt_hovere2e_")
    env = dict(os.environ)
    env["TIMETABLE_DATA_DIR"] = tmp
    data_file = Path(tmp) / "data.json"

    log("exe = " + str(EXE))
    kill_all()
    try:
        if not seed_state(tmp, data_file, env):
            log("FAIL: could not seed the docked state")
            return 1
        log("seeded: ball_edge=right, ball_center=" + str(BALL_CENTER))

        a = case_a(env, data_file)
        b = case_b(env, data_file)

        log("")
        log("================ SUMMARY ================")
        log("A) first hover expands (cursor was away) : " + str(a)
            + "   expected True")
        log("B) stays collapsed   (cursor was there)  : " + str(b)
            + "   expected False")
        log("")
        if a is True and b is False:
            log("PASS: the reported bug is fixed AND the legitimate suppress case")
            log("      still works.")
            return 0
        if a is None or b is None:
            log("INCONCLUSIVE: a case could not run.")
            return 3
        log("FAIL: one of the two behaviours is wrong.")
        return 1
    finally:
        kill_all()
        shutil.rmtree(tmp, ignore_errors=True)
        log("temp dir removed")


if __name__ == "__main__":
    raise SystemExit(main())
