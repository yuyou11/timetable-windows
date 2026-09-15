"""
Experiment: does pywebview's move()/resize() re-show an already hidden window?

Suspicion:
    webview/platforms/winforms.py implements move() and resize() with
    SetWindowPos(..., SWP_SHOWWINDOW). SWP_SHOWWINDOW means "displays the
    window". If that is true, then calling ball.move() after ball.hide()
    brings the ball back -- which would explain the reported bug
    "clicked close, window flashed but stayed".

This script measures it instead of guessing:
    hide -> check IsWindowVisible -> move -> check IsWindowVisible
    hide -> check IsWindowVisible -> resize -> check IsWindowVisible

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import ctypes
import threading
import time

import webview

user32 = ctypes.windll.user32

TITLE = "SwpExperiment"

# Win32 constants, spelled out so the log can show them
SWP_SHOWWINDOW = 0x0040


def log(msg):
    print("[" + time.strftime("%H:%M:%S") + "] " + str(msg), flush=True)


def is_visible(hwnd):
    """True if the window is actually on screen right now."""
    return bool(user32.IsWindowVisible(hwnd))


def find_window():
    for _ in range(50):
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            return hwnd
        time.sleep(0.2)
    return 0


def worker(window):
    time.sleep(2.0)
    hwnd = find_window()
    if not hwnd:
        log("FAIL: window not found")
        return

    log("hwnd = " + str(hwnd))
    log("pywebview move() uses SetWindowPos with SWP_SHOWWINDOW = 0x%04X" % SWP_SHOWWINDOW)
    log("")

    # --- step 1: baseline. Window was created with hidden=True ---
    log("step 1  fresh window (created hidden=True)")
    log("        visible = " + str(is_visible(hwnd)))
    log("")

    # --- step 2: hide(), then move() ---
    window.hide()
    time.sleep(0.6)
    log("step 2  after window.hide()")
    log("        visible = " + str(is_visible(hwnd)))
    window.move(400, 400)
    time.sleep(0.6)
    log("        after window.move(400, 400)")
    log("        visible = " + str(is_visible(hwnd)) + "   <== the question")
    log("")

    # --- step 3: hide(), then resize() ---
    window.hide()
    time.sleep(0.6)
    log("step 3  after window.hide()")
    log("        visible = " + str(is_visible(hwnd)))
    window.resize(300, 150)
    time.sleep(0.6)
    log("        after window.resize(300, 150)")
    log("        visible = " + str(is_visible(hwnd)) + "   <== and this one too")
    log("")

    log("done")

    # MUST destroy the window to end the experiment.
    #
    # webview.start() blocks until every window is closed. The first version of
    # this script forgot this line, so after printing its results the window just
    # sat there forever and the run had to be killed by hand.
    window.destroy()


def main():
    window = webview.create_window(
        TITLE,
        html="<html><body style='font:14px sans-serif'>experiment</body></html>",
        width=200,
        height=100,
        frameless=True,
        hidden=True,
    )
    threading.Thread(target=worker, args=(window,), daemon=True).start()
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
