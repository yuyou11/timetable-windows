"""
Experiment: how do we get rounded corners on the desktop ball window?

## Why this file measures itself

The first version of this experiment asked a human to look at four windows
side by side and decide which were rounded. It failed twice, for two
different reasons -- both worth remembering:

  1. A WHITE page on a LIGHT desktop has no visible window edge. Everything
     "looked rounded" because the CSS inside was rounded; the experiment
     proved nothing about the window.
  2. A screenshot is in PHYSICAL pixels, but the crop math multiplied by the
     DPI scale first. So the "corners" being compared were not the window's
     corners, and the nonsense result still looked plausible.

Fixes for both: the page is SOLID RED (nothing inside can be mistaken for a
rounded corner), and the program reads pixels back itself and prints a map.
No eyes, no cropping, no units to get wrong.

## How to read the map

Each window prints a small grid of its top-left corner, one character per
pixel, starting exactly at the window's own top-left:

    R = this pixel is the window's red background  -> window paints here
    . = something else is visible                  -> corner was cut away

A square window prints a solid block of R. A rounded window prints a block
with a bite taken out of the corner.

## The control group matters

`DONOTROUND` is in the list on purpose. If BOTH "control" and "ROUND" come
out rounded, we have not learned that ROUND works -- we have learned that
the default is already round, and the attribute is doing nothing.
Two active groups plus a no-op group is what makes the difference visible.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webview  # noqa: E402

# The real app declares DPI awareness in run.py before any window is created.
# Bypass run.py and the coordinates land somewhere else than in the real app.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

DWMWA_WINDOW_CORNER_PREFERENCE = 33     # Windows 11 build 22000+
DWMWA_NCRENDERING_POLICY = 2
DWMNCRP_DISABLED = 1
DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3

W, H = 180, 110
TOP = 430
LEFT0 = 30
GAP = 20
RADIUS = 16                             # what we would actually want on screen
SAMPLES = 16                            # size of the corner map, in pixels

# (title, label, corner preference or None, kill shadow?, use GDI region?)
#
# This is a MATRIX, not a list of ideas. The point of each row is the cell it
# fills -- a missing cell is how the first version of this experiment fooled
# itself. Specifically, the real app calls disable_shadow() and never touches
# the corner preference at all. Testing "ROUND + no shadow" (rounded) while
# never testing "no shadow, no preference" would have hidden the one
# combination that actually matters.
CONFIGS = [
    ("cx-1-control",     "1 control",            None,              False, False),
    ("cx-2-nosdw",       "2 shadow off only",    None,              True,  False),
    ("cx-3-dnr",         "3 DONOTROUND",         DWMWCP_DONOTROUND, False, False),
    ("cx-4-dnr-nosdw",   "4 DONOTROUND+nosdw",   DWMWCP_DONOTROUND, True,  False),
    ("cx-5-round",       "5 ROUND",              DWMWCP_ROUND,      False, False),
    ("cx-6-round-nosdw", "6 ROUND+nosdw",        DWMWCP_ROUND,      True,  False),
    ("cx-7-region",      "7 SetWindowRgn",       None,              False, True),
    ("cx-8-region-nosdw", "8 SetWindowRgn+nosdw", None,             True,  True),
]


def _hwnd(title: str) -> int:
    return ctypes.windll.user32.FindWindowW(None, title)


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _pixel(x: int, y: int) -> tuple[int, int, int]:
    """Colour of one screen pixel. Physical pixels."""
    hdc = ctypes.windll.user32.GetDC(0)
    try:
        # GetPixel returns COLORREF = 0x00BBGGRR -- note the byte order.
        v = ctypes.windll.gdi32.GetPixel(hdc, x, y)
        if v == 0xFFFFFFFF:            # CLR_INVALID
            return (-1, -1, -1)
        return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
    finally:
        ctypes.windll.user32.ReleaseDC(0, hdc)


def _is_window_red(c: tuple[int, int, int]) -> bool:
    return c[0] > 180 and c[1] < 90 and c[2] < 90


def set_corner(hwnd: int, pref: int) -> str:
    value = ctypes.c_int(pref)
    hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
        ctypes.byref(value), ctypes.sizeof(value),
    )
    got = ctypes.c_int(-1)
    ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
        ctypes.byref(got), ctypes.sizeof(got),
    )
    # hr == 0 only means "call understood"; the readback is the real evidence
    return f"hr={hr} readback={got.value}"


def no_shadow(hwnd: int) -> str:
    value = ctypes.c_int(DWMNCRP_DISABLED)
    hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd, DWMWA_NCRENDERING_POLICY, ctypes.byref(value), ctypes.sizeof(value)
    )
    return f"hr={hr}"


def round_region(hwnd: int, radius: int) -> str:
    """
    Clip the window to a rounded rectangle with a GDI region.

    The diameter arguments are TWICE the radius, and they are DEVICE pixels.
    Unlike everything else in this project (see app/dock.py), SetWindowRgn is a
    raw Win32 call -- no DPI conversion happens for us, so pass physical pixels.
    """
    x, y, r, b = _rect(hwnd)
    w, h = r - x, b - y
    rgn = ctypes.windll.gdi32.CreateRoundRectRgn(
        0, 0, w + 1, h + 1, radius * 2, radius * 2
    )
    if not rgn:
        return "CreateRoundRectRgn FAILED"
    # 1 = redraw now. The system takes ownership of the region: do NOT delete it.
    ctypes.windll.user32.SetWindowRgn(hwnd, rgn, 1)
    return f"radius={radius}px (window {w}x{h})"


PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html,body { width:100%%; height:100%%; overflow:hidden; background:#ff0000; }
  /* NO border-radius: we are measuring the WINDOW, not the CSS. Keeping the
     page square means the window is the only thing that can round a corner. */
  div { width:100%%; height:100%%; }
</style></head><body><div></div></body></html>"""


def corner_map(hwnd: int, n: int = SAMPLES) -> tuple[list[str], tuple[int, int, int, int]]:
    """ASCII map of the top-left n x n pixels, plus the window rect."""
    x, y, r, b = _rect(hwnd)
    rows = []
    for j in range(n):
        row = "".join("R" if _is_window_red(_pixel(x + i, y + j)) else "." for i in range(n))
        rows.append(row)
    return rows, (x, y, r, b)


def report() -> None:
    """
    Print one compact line per window.

    Instead of dumping 8 corner maps, print the *profile* of the corner: how
    many pixels are cut away on each of the first N rows going down from the
    window's top edge.

    Reading the profile:
      · all zeros            -> SQUARE, nothing was cut
      · largest at the top,
        shrinking to zero     -> ROUNDED; the first number is roughly the
                                 corner radius in pixels

    A row of dots is unmistakable, and comparing rows side by side is exactly
    the comparison this experiment exists to make.
    """
    print("\n" + "=" * 78)
    print("corner profile = how many pixels are cut away, per row from the top")
    print("-" * 78)
    results = {}
    for title, label, _pref, _sdw, _rgn in CONFIGS:
        hwnd = _hwnd(title)
        if not hwnd:
            print(f"{label:<24} NO WINDOW")
            continue
        rows, rect = corner_map(hwnd)
        x, y, r, b = rect
        # Row 0 is the window's own 1px edge and is never pure red; the shape
        # starts at row 1. Skip row 0 so the profile is about shape only.
        profile = [len(row) - len(row.lstrip(".")) for row in rows[1:11]]
        radius = max(profile) if profile else 0
        verdict = "SQUARE" if radius <= 1 else f"ROUNDED ~{radius}px"
        results[label] = verdict
        print(f"{label:<24} {str(profile):<40} {verdict}")
        print(f"{'':<24} window {x},{y} {r-x}x{b-y}")
    print("-" * 78)
    for label, verdict in results.items():
        print(f"  {label:<24} {verdict}")
    print("=" * 78)


def work() -> None:
    time.sleep(3.0)

    for title, label, pref, shadow, region in CONFIGS:
        hwnd = _hwnd(title)
        if not hwnd:
            print(f"{label}: no hwnd")
            continue
        if region:
            print(f"{label:20} region -> {round_region(hwnd, RADIUS)}")
        if shadow:
            print(f"{label:20} shadow -> {no_shadow(hwnd)}")
        if pref is not None:
            print(f"{label:20} corner -> {set_corner(hwnd, pref)}")
        time.sleep(0.2)

    time.sleep(1.5)          # let DWM finish composing before reading pixels
    report()

    print("\nclosing in 25s")
    time.sleep(25)
    for win in webview.windows:
        try:
            win.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    for i, (title, label, _p, _s, _r) in enumerate(CONFIGS):
        webview.create_window(
            title,
            html=PAGE,
            width=W, height=H,
            x=LEFT0 + i * (W + GAP), y=TOP,
            frameless=True,
            on_top=True,
            background_color="#ff0000",
        )

    threading.Thread(target=work, daemon=True).start()
    webview.start()
