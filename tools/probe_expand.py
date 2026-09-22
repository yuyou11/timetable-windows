"""
悬浮窗**展开过程中**那块"临时区域"到底是什么颜色。

## 为什么要这个

用户报：浅色模式下弹出过程有一条黑边。已经猜过两个原因（resize/move 的
中间态外溢、主题没对上），都改了但现象还在 —— **再猜下去只是浪费时间**。

三种可能的成因，三种像素特征，一次分清：

    纯黑 (0, 0, 0)          WebView2 没画过的区域（合成器默认）
    #181b21 之类的深色实心   窗口底色（screen._bg_color 给的）
    一圈渐变、越远越淡       DWM 的系统阴影

所以这里抓展开过程中的几帧，并**逐帧打印窗口右半边的像素统计**：
主色、有多少纯黑、有多少接近 #181b21。数字说话，不用人眼猜。

顺带把每一帧存成 PNG —— 数字之外也留一份能看的。

用 `--src` 跑源码（不用重打包）：

    python tools/probe_expand.py --src

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USE_SOURCE = "--src" in sys.argv
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
EXE = Path(_args[0]) if _args else ROOT / "dist" / "时间规划表" / "时间规划表.exe"
SHOT_DIR = ROOT / "build-logs" / "shots"

BALL_TITLE = "时间规划表悬浮窗"
BALL_CENTER = 600
#: 抓帧的偏移（毫秒）。动画总时长 160ms，所以只看 0~180 这一段。
OFFSETS_MS = (10, 35, 60, 85, 110, 135, 160)

user32 = ctypes.windll.user32


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def log(msg):
    print("[" + time.strftime("%H:%M:%S") + "] " + str(msg), flush=True)


def rect_of(hwnd):
    r = RECT()
    user32.GetWindowRect(ctypes.wintypes.HWND(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def move_to(x, y):
    user32.SetCursorPos(int(x), int(y))


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def describe(tag, img, box):
    """打印 [box] 区域里的像素特征，并存一张 PNG。返回一段可读的结论。"""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    crop = img.crop(box)
    crop.save(SHOT_DIR / ("expand_" + tag + ".png"))

    px = list(crop.convert("RGB").getdata())
    total = len(px) or 1
    black = sum(1 for r, g, b in px if r < 12 and g < 12 and b < 12)
    # #181b21 是深色主题的 --surface；容差放宽到 10，抗锯齿会带偏一两格
    surf = sum(1 for r, g, b in px
               if abs(r - 0x18) < 10 and abs(g - 0x1b) < 10 and abs(b - 0x21) < 10)
    white = sum(1 for r, g, b in px if r > 245 and g > 245 and b > 245)
    top = Counter(px).most_common(3)

    verdict = []
    if black / total > 0.30:
        verdict.append("纯黑 -> WebView2 没画过的区域")
    if surf / total > 0.30:
        verdict.append("#181b21 -> 窗口底色（主题没对上）")
    if white / total > 0.30:
        verdict.append("白色 -> 卡片/页面底色")
    if not verdict:
        verdict.append("都不是 -> 看最常见色，多半是渐变阴影或中间态")

    log("  %s  尺寸 %dx%d  纯黑 %d%%  #181b21 %d%%  白 %d%%"
        % (tag, box[2] - box[0], box[3] - box[1],
           100 * black // total, 100 * surf // total, 100 * white // total))
    log("     最常见色: " + ", ".join(
        "#%02x%02x%02x (%d%%)" % (c[0], c[1], c[2], 100 * n // total)
        for c, n in top))
    log("     => " + " / ".join(verdict))


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    if not USE_SOURCE and not EXE.exists():
        log("FAIL: exe not found: " + str(EXE))
        return 2

    tmp = tempfile.mkdtemp(prefix="tt_expand_")
    env = dict(os.environ)
    env["TIMETABLE_DATA_DIR"] = tmp
    data_file = Path(tmp) / "data.json"

    procs = []
    try:
        # ---- 第一次启动：让 data.json 落盘，然后写死一个「贴右边 + 浅色」 ----
        log("target = " + ("SOURCE" if USE_SOURCE else str(EXE)))
        cmd = [sys.executable, "-m", "app.main"] if USE_SOURCE else [str(EXE)]
        p = subprocess.Popen(cmd, env=env, cwd=str(ROOT))
        procs.append(p)
        for _ in range(40):
            time.sleep(0.5)
            if data_file.exists() and read_json(data_file):
                break
        for p in procs:
            p.terminate()
        procs.clear()
        time.sleep(1.5)

        data = read_json(data_file) or {}
        data["ball_enabled"] = True
        data["ball_edge"] = "right"
        data["ball_center"] = BALL_CENTER
        data["theme"] = "light"          # ← 用户报问题的那一档
        data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

        # ---- 第二次启动：贴右边收起态 ----
        move_to(200, 200)
        time.sleep(0.3)
        p = subprocess.Popen(cmd, env=env, cwd=str(ROOT))
        procs.append(p)
        hwnd = 0
        for _ in range(60):
            time.sleep(0.5)
            hwnd = user32.FindWindowW(None, BALL_TITLE)
            if hwnd:
                break
        if not hwnd:
            log("FAIL: ball never appeared")
            return 1
        time.sleep(2.5)

        left, top, right, bottom = rect_of(hwnd)
        log("collapsed rect = %s" % (rect_of(hwnd),))
        full_box = (left - 60, top - 60, right + 60, bottom + 60)

        from PIL import ImageGrab

        # ---- 先看「静止的收起态」----
        #
        # 这一帧决定了后面那个临时区域的底色**是谁的**：
        #   收起标签是浅色  -> 页面已经是浅色，那深色只能来自别处
        #   收起标签是深色  -> 页面根本没切到浅色，data-theme 没生效
        move_to(200, 200)
        time.sleep(1.0)
        l0, t0, r0, b0 = rect_of(hwnd)
        img = ImageGrab.grab()
        log("")
        log("=== 静止收起态（还没 hover）===")
        # ⚠️ 严格取窗口本体，一个边距都不留 —— 留了就会把**桌面**采进来，
        # 而桌面颜色完全不受我们控制，会把结论带偏（第一次就是这么错的）。
        describe("rest", img, (l0, t0, min(img.size[0], r0), min(img.size[1], b0)))
        log("  data.json 里的 theme = " + repr(read_json(data_file).get("theme")))

        # ---- 逐个偏移抓一帧展开过程 ----
        for off in OFFSETS_MS:
            # 每次都从收起态重新展开，保证抓的是同一段
            move_to(200, 200)
            time.sleep(0.8)
            left, top, right, bottom = rect_of(hwnd)
            cx, cy = (left + right) // 2, (top + bottom) // 2
            move_to(cx, cy)
            time.sleep(off / 1000.0)
            img = ImageGrab.grab()
            l2, t2, r2, b2 = rect_of(hwnd)
            log("")
            log("offset %3dms  window=%dx%d @(%d,%d)"
                % (off, r2 - l2, b2 - t2, l2, t2))
            # 窗口右半条：用户说黑边在右边。
            #
            # ⚠️ 取样框必须夹在屏幕范围内。第一版写了 `r2 + 24`（想连窗口外的
            # 阴影一起看），而贴右边的窗口右缘正好是屏幕右缘 —— 裁出界时
            # PIL 会用**纯黑补边**，于是统计里恒定多出 14% 的"纯黑"，
            # 看起来就像发现了什么，其实是探针自己画上去的。
            # **测量工具自己造出来的信号，比没有信号更糟。**
            sw, sh = img.size
            band = (l2 + (r2 - l2) * 55 // 100, max(0, t2),
                    min(sw, r2 + 24), min(sh, b2))
            describe("t%03d" % off, img, band)

        log("")
        log("PNG saved to " + str(SHOT_DIR / "expand_*.png"))
        return 0
    finally:
        for p in procs:
            p.terminate()
        time.sleep(1.0)
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
