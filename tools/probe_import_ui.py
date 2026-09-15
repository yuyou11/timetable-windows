"""
Drive the REAL import flow through the REAL UI.

    python tools/probe_import_ui.py [--src] [steps]

Why: the `_parsed` bug was invisible to every automated check I had, because
they all stopped short of the actual button click. The only thing that proves
the import works for the user is... doing what the user does.

This launches the app with an ISOLATED data dir, moves the main window to a
fixed position (so click coordinates are stable), and screenshots at each step.
Coordinates are passed in so the first run can be used just to LOOK.

steps (default "shot"):
    shot          just screenshot the main window
    click:x,y     click that point in screen pixels
    key:text      type text then press Enter

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
SHOT_DIR = ROOT / "build-logs" / "shots"

MAIN_TITLE = "时间规划表"
BALL_TITLE = "时间规划表悬浮窗"

# fixed window rect so screenshots and clicks are reproducible
WIN_X, WIN_Y, WIN_W, WIN_H = 80, 60, 1180, 860

user32 = ctypes.windll.user32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def log(msg):
    print("[" + time.strftime("%H:%M:%S") + "] " + str(msg), flush=True)


def find(title):
    return user32.FindWindowW(None, title)


def rect_of(hwnd):
    r = RECT()
    user32.GetWindowRect(ctypes.wintypes.HWND(hwnd), ctypes.byref(r))
    return r


def force_foreground(hwnd):
    """
    Bring the window to the front AND actually give it keyboard focus.

    SetForegroundWindow alone is not enough: Windows refuses it when the
    calling process does not own the current foreground window (that is an
    anti-focus-stealing rule). The standard workaround is the
    AttachThreadInput trick -- attach our input queue to the foreground
    thread, then SetForegroundWindow succeeds, then detach.

    Without this the click lands on whatever window IS on top. I hit exactly
    that: my screenshot came back showing the chat app instead of the
    program under test.
    """
    k32 = ctypes.windll.kernel32
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True

    target_thread = user32.GetWindowThreadProcessId(fg, None)
    my_thread = k32.GetCurrentThreadId()
    attached = False
    try:
        if target_thread and target_thread != my_thread:
            attached = bool(user32.AttachThreadInput(my_thread, target_thread, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(my_thread, target_thread, False)

    time.sleep(0.4)
    return user32.GetForegroundWindow() == hwnd


def move_window(hwnd, x, y, w, h, scale):
    """Place the window at a fixed spot (physical px) and bring it to front."""
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE (in case it is minimised)
    time.sleep(0.3)
    user32.SetWindowPos(hwnd, -1,       # HWND_TOPMOST
                        int(x), int(y), int(w * scale), int(h * scale),
                        0x0040)         # SWP_SHOWWINDOW
    time.sleep(0.3)
    got = force_foreground(hwnd)
    # keep it topmost for the whole run: the point is to test OUR window,
    # and a chat app stealing focus mid-run would silently invalidate the shots
    user32.SetWindowPos(hwnd, -1, int(x), int(y),
                        int(w * scale), int(h * scale), 0x0040)
    time.sleep(0.5)
    log("  focus acquired: " + str(got)
        + "   foreground is now '" + window_text(user32.GetForegroundWindow()) + "'")
    return got


def window_text(hwnd):
    try:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value
    except Exception:
        return "?"


def open_windows_of(pid):
    """
    Top-level window titles belonging to our process.

    Used to answer "did the click actually do something?" -- e.g. after
    clicking 选择文件导入, a file-open dialog should appear. Screenshots alone
    are not evidence (they can show the wrong window entirely).
    """
    titles = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def cb(hwnd, _):
        wpid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd):
            t = window_text(hwnd)
            if t:
                titles.append(t)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return titles


def click(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.25)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.08)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.6)


def send_text(text):
    import ctypes.wintypes
    # Use the clipboard + Ctrl+V: SendKeys mangles non-ASCII paths.
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    k32 = ctypes.windll.kernel32
    k32.OpenClipboard(None)
    k32.EmptyClipboard()
    data = text.encode("utf-16-le") + b"\x00\x00"
    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    p = k32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)
    k32.SetClipboardData(CF_UNICODETEXT, h)
    k32.CloseClipboard()

    time.sleep(0.3)
    VK_CONTROL, VK_V, VK_RETURN = 0x11, 0x56, 0x0D
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 2, 0)
    user32.keybd_event(VK_CONTROL, 0, 2, 0)
    time.sleep(0.5)
    user32.keybd_event(VK_RETURN, 0, 0, 0)
    user32.keybd_event(VK_RETURN, 0, 2, 0)
    time.sleep(0.8)


def shot(tag, box=None):
    try:
        from PIL import ImageGrab
    except Exception:
        log("  (no PIL, skipping shot)")
        return
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    img = ImageGrab.grab()
    if box:
        img = img.crop(box)
    path = SHOT_DIR / ("importui_" + tag + ".png")
    img.save(path)
    log("  shot -> " + str(path))


def kill_all():
    subprocess.run(["taskkill", "/F", "/IM", "时间规划表.exe"], capture_output=True)
    if USE_SOURCE:
        subprocess.run(["taskkill", "/F", "/IM", "python.exe", "/FI",
                        "WINDOWTITLE eq " + BALL_TITLE], capture_output=True)
        subprocess.run(["taskkill", "/F", "/IM", "python.exe", "/FI",
                        "WINDOWTITLE eq " + MAIN_TITLE], capture_output=True)
    time.sleep(1)


def launch(env):
    if USE_SOURCE:
        proc = subprocess.Popen([sys.executable, "-m", "app.main"],
                                cwd=str(ROOT), env=env)
    else:
        exe = ROOT / "dist" / "时间规划表" / "时间规划表.exe"
        proc = subprocess.Popen([str(exe)], env=env)
    hwnd = 0
    for _ in range(90):
        time.sleep(0.5)
        hwnd = find(MAIN_TITLE)
        if hwnd:
            break
    return proc, hwnd


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    steps = sys.argv[1:]
    steps = [s for s in steps if not s.startswith("--")] or ["shot"]

    tmp = tempfile.mkdtemp(prefix="tt_importui_")
    env = dict(os.environ)
    env["TIMETABLE_DATA_DIR"] = tmp
    data_file = Path(tmp) / "data.json"

    kill_all()
    try:
        log("launching (isolated dir: " + tmp + ")")
        proc, hwnd = launch(env)
        if not hwnd:
            log("FAIL: main window never appeared")
            return 1

        # let the app finish booting and seed its data file
        for _ in range(40):
            if data_file.exists() and json.loads(
                    data_file.read_text(encoding="utf-8")):
                break
            time.sleep(0.5)

        scale = 1.0
        try:
            scale = user32.GetDpiForSystem() / 96.0
        except Exception:
            pass

        move_window(hwnd, WIN_X, WIN_Y, WIN_W, WIN_H, scale)
        time.sleep(1.0)

        r = rect_of(hwnd)
        log("main window rect = (" + str(r.left) + "," + str(r.top) + ") "
            + str(r.right - r.left) + "x" + str(r.bottom - r.top))
        log("cursor is at " + str(user32.GetCursorPos(ctypes.byref(
            ctypes.wintypes.POINT())) or "?"))

        for step in steps:
            if step == "shot":
                shot("main", (r.left, r.top, r.right, r.bottom))
            elif step.startswith("click:"):
                _, xy = step.split(":", 1)
                x, y = (int(v) for v in xy.split(","))
                log("click (" + str(x) + "," + str(y) + ")")
                click(x, y)
                time.sleep(1.0)
                rr = rect_of(hwnd)
                shot("after_click_" + str(x) + "_" + str(y),
                     (rr.left, rr.top, rr.right, rr.bottom))
            elif step.startswith("key:"):
                text = step.split(":", 1)[1]
                log("typing path into the dialog: " + text)
                send_text(text)
                time.sleep(2.5)
                shot("after_key", (r.left, r.top, r.right, r.bottom))
                log("  dialogs now open: " + str([window_text(h) for h in
                                                  open_windows_of(proc.pid)]))
            elif step.startswith("full:"):
                shot("full_" + step.split(":", 1)[1] if ":" in step else "full")
            elif step == "fullshot":
                shot("full", None)
            elif step == "wait":
                time.sleep(2)
            elif step == "state":
                log("data.json courses = "
                    + str(len(json.loads(data_file.read_text(encoding='utf-8'))
                              .get("courses", []))))

        log("")
        log("data.json now: " + str(data_file.stat().st_size) + " bytes")
        d = json.loads(data_file.read_text(encoding="utf-8"))
        log("courses in data.json: " + str(len(d.get("courses", []))))
        return 0
    finally:
        kill_all()
        time.sleep(0.5)
        shutil.rmtree(tmp, ignore_errors=True)
        log("cleaned up")


if __name__ == "__main__":
    raise SystemExit(main())
