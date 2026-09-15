"""
Can a pywebview window be animated by repeatedly calling resize()+move()?

Before promising an animation, measure whether the native window can actually
keep up. Every resize/move is a cross-process call into the WinForms UI thread
(they go through Invoke), so a naive 60fps loop may be far too slow -- or may
stall when the UI thread is busy.

This measures, from a BACKGROUND THREAD (which is how the real code would do
it, since the hover/drag callbacks do not run on the UI thread):

  1. how long one resize+move pair takes
  2. the total time for a 12-step shrink (card size -> tab size)
  3. the same for a 12-step grow (tab -> card), i.e. both directions

It also destroys the window at the end -- an earlier experiment of mine forgot
that and hung forever, because webview.start() blocks until every window is
closed.

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import ctypes
import threading
import time

import webview

user32 = ctypes.windll.user32

TITLE = "AnimFpsExperiment"

# Same numbers as app/dock.py
CARD_W, CARD_H = 244, 104
TAB_SHORT, TAB_LONG = 26, 76

STEPS = 12


def log(msg):
    print("[" + time.strftime("%H:%M:%S") + "] " + str(msg), flush=True)


def find_window():
    for _ in range(50):
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            return hwnd
        time.sleep(0.2)
    return 0


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def rect_of(hwnd):
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def run_ramp(window, label, w0, h0, w1, h1, x, y):
    """Shrink or grow over STEPS frames, timing each frame."""
    times = []

    # put it at the start size first
    window.resize(w0, h0)
    window.move(x, y)
    time.sleep(0.4)
    start = time.perf_counter()

    for i in range(1, STEPS + 1):
        t = i / STEPS
        w = round(w0 + (w1 - w0) * t)
        h = round(h0 + (h1 - h0) * t)
        t0 = time.perf_counter()
        window.resize(w, h)
        window.move(x, y)
        times.append((time.perf_counter() - t0) * 1000.0)
        time.sleep(0.004)          # tiny yield; the UI thread needs a slot

    total = (time.perf_counter() - start) * 1000.0
    log("")
    log(label)
    log("  per-frame ms : min=%.1f  max=%.1f  avg=%.1f"
        % (min(times), max(times), sum(times) / len(times)))
    log("  total for %d steps : %.0f ms" % (STEPS, total))
    log("  => a %d ms animation window is %s"
        % (160, "FEASIBLE" if total < 160 else "TOO SLOW"))
    return total


def worker(window):
    time.sleep(2.0)
    hwnd = find_window()
    if not hwnd:
        log("FAIL: window not found")
        window.destroy()
        return

    log("hwnd = " + str(hwnd))
    log("this is running on a BACKGROUND THREAD, like the real code would")
    r = rect_of(hwnd)
    log("initial rect = (%d,%d) %dx%d"
        % (r.left, r.top, r.right - r.left, r.bottom - r.top))

    x, y = 900, 500

    # one single pair, to see the floor cost
    t0 = time.perf_counter()
    window.resize(CARD_W, CARD_H)
    window.move(x, y)
    log("one resize+move pair = %.1f ms" % ((time.perf_counter() - t0) * 1000))

    run_ramp(window, "SHRINK  card -> tab  (the docking animation)",
             CARD_W, CARD_H, TAB_SHORT, TAB_LONG, x, y)
    run_ramp(window, "GROW    tab -> card  (the hover-expand animation)",
             TAB_SHORT, TAB_LONG, CARD_W, CARD_H, x, y)

    # Can it be pushed harder? This is what a 60fps loop would demand.
    log("")
    log("stress: 30 frames with NO sleep (a 60fps loop would need 16.7 ms each)")
    times = []
    for i in range(30):
        t0 = time.perf_counter()
        window.resize(100 + i, 80 + i)
        window.move(x, y)
        times.append((time.perf_counter() - t0) * 1000.0)
    log("  per-frame ms : min=%.1f  max=%.1f  avg=%.1f"
        % (min(times), max(times), sum(times) / len(times)))
    log("  is that fast enough for 60fps? "
        + ("yes" if sum(times) / len(times) < 16.7 else "NO"))

    log("")
    log("done")
    window.destroy()


def main():
    window = webview.create_window(
        TITLE,
        html="<html><body style='margin:0;background:#181b21;color:#fff;"
             "font:13px sans-serif'>animation timing experiment</body></html>",
        width=CARD_W,
        height=CARD_H,
        frameless=True,
    )
    threading.Thread(target=worker, args=(window,), daemon=True).start()
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
