"""
从**外部**列出这个程序创建的所有窗口，以及它们的真实位置和可见性。

## 为什么需要它

用户报「一打开软件悬浮窗就不出现，可能跑到右边界以外去了」。
这种情况下猜是没用的 —— 得知道：

    · 窗口到底**存在不存在**？
    · 如果存在，它在**哪**（坐标多少）？
    · 它**可见吗**（IsWindowVisible）？

这三个答案能把问题范围一下子缩到很小。

## 用法

    python tools/inspect_windows.py            # 检查当前所有相关窗口
    python tools/inspect_windows.py --watch    # 每 2 秒刷新一次，边操作边看

## 关于坐标

脚本自己声明 DPI 感知，这样拿到的坐标和程序内部用的是同一套（物理像素）。
不声明的话读出来是虚拟化的值，跟程序里的对不上，等于白看。
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

user32 = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

#: 只看标题里带这些字的窗口，免得把系统窗口全列出来
KEYWORDS = ("时间规划表", "时间规", "Timetable")


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def work_area() -> tuple[int, int, int, int]:
    r = RECT()
    if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
        return r.left, r.top, r.right, r.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def title_of(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def collect() -> list[dict]:
    found: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        title = title_of(hwnd)
        if not any(k in title for k in KEYWORDS):
            return True

        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        ex = get_long(hwnd, GWL_EXSTYLE)

        found.append({
            "hwnd": hwnd,
            "title": title,
            "rect": (rect.left, rect.top, rect.right, rect.bottom),
            "w": rect.right - rect.left,
            "h": rect.bottom - rect.top,
            "visible": bool(user32.IsWindowVisible(hwnd)),
            "iconic": bool(user32.IsIconic(hwnd)),
            "tool": bool(ex & WS_EX_TOOLWINDOW),
            "appwin": bool(ex & WS_EX_APPWINDOW),
        })
        return True

    user32.EnumWindows(cb, 0)
    return found


def report() -> None:
    wl, wt, wr, wb = work_area()
    print(f"工作区：({wl},{wt}) – ({wr},{wb})   {wr - wl}×{wb - wt}")

    wins = collect()
    if not wins:
        print("  ★ 一个相关窗口都没找到 —— 程序可能没在运行")
        return

    for w in wins:
        x, y, r, b = w["rect"]
        flags = []
        if w["visible"]:
            flags.append("可见")
        else:
            flags.append("★隐藏★")
        if w["iconic"]:
            flags.append("最小化")
        if w["tool"]:
            flags.append("不进任务栏")
        if w["appwin"]:
            flags.append("进任务栏")

        # 判断有没有跑到屏幕外
        offscreen = []
        if r <= wl:
            offscreen.append("全在左边外")
        if x >= wr:
            offscreen.append("★全在右边外★")
        if b <= wt:
            offscreen.append("全在上边外")
        if y >= wb:
            offscreen.append("全在下边外")
        if not offscreen and (x < wl or y < wt or r > wr or b > wb):
            offscreen.append("部分超出")

        print(f"\n  「{w['title']}」 hwnd={w['hwnd']}")
        print(f"     位置 ({x},{y}) – ({r},{b})   尺寸 {w['w']}×{w['h']}")
        print(f"     状态 {' / '.join(flags)}")
        if offscreen:
            print(f"     ★★★ {'；'.join(offscreen)} ★★★")
        else:
            print("     在屏幕范围内 ✓")


def check() -> int:
    """
    自动判定：返回 0 = 一切正常，1 = 有问题。

    检查两件事：
      1. 该出现的窗口都在（主窗口 + 悬浮窗）
      2. 没有任何窗口跑到工作区外面

    为什么要做成「能自动判定」的形式？因为「窗口跑到屏幕外」这个问题
    **单元测试抓不到** —— 它出在 dock.py 和 pywebview 的接缝上，
    只有真的把窗口建出来才看得见。而我又看不见屏幕。
    所以把这个检查做成可脚本化的，每次改完窗口相关的代码跑一遍。
    """
    wl, wt, wr, wb = work_area()
    wins = collect()
    problems: list[str] = []

    print(f"工作区（物理像素）：({wl},{wt}) – ({wr},{wb})   {wr - wl}×{wb - wt}")

    if not wins:
        print("  ✗ 一个相关窗口都没找到 —— 程序没在运行？")
        return 1

    titles = {w["title"]: w for w in wins}

    for w in wins:
        x, y, r, b = w["rect"]
        name = w["title"]
        # 有些窗口本来就该是隐藏的，对它们的可见性不做要求：
        #   · 提醒提示条 —— 只在提醒到点时才弹出来
        #   · Pillow（托盘图标用）会在后台开一个 1×1 的 GDI+ 窗口，那是它自己的事
        expect_hidden = ("提醒" in name) or ("GDI+" in name)

        if x >= wr or r <= wl or y >= wb or b <= wt:
            problems.append(f"「{name}」完全在屏幕外：({x},{y})–({r},{b})")
        elif x < wl or y < wt or r > wr or b > wb:
            problems.append(f"「{name}」部分超出工作区：({x},{y})–({r},{b})")
        elif not w["visible"] and not expect_hidden:
            problems.append(f"「{name}」被隐藏了（位置正常）")
        elif w["visible"] and expect_hidden:
            problems.append(f"「{name}」本该隐藏却露出来了（一开机就弹提示条）")

        print(f"  「{name}」 ({x},{y})–({r},{b}) {w['w']}×{w['h']} "
              f"{'可见' if w['visible'] else '隐藏'}")

    # 悬浮窗必须在
    if not any("悬浮窗" in t for t in titles):
        problems.append("找不到悬浮窗（它没被创建出来？）")

    print()
    if problems:
        for p in problems:
            print(f"  ✗ {p}")
        return 1

    print("  ✓ 所有窗口都在工作区内，该有的都在")
    return 0


def main() -> int:
    if "--check" in sys.argv:
        return check()

    watch = "--watch" in sys.argv
    if not watch:
        report()
        return 0

    print("每 2 秒刷新一次，按 Ctrl+C 结束。现在可以去操作程序，观察窗口变化。\n")
    try:
        while True:
            print("=" * 60)
            report()
            print()
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
