"""
Report how a window's corners are currently shaped.

    python tools/check_corners.py                      # 默认查悬浮窗
    python tools/check_corners.py "时间规划表提醒"

## 为什么需要它

圆角这件事**只能看出来**，但看不见屏幕就没法看。而且它特别容易
「以为改好了」—— CSS 里明明写了 border-radius，代码也没报错，
可窗口本身还是方的。

所以换个办法：直接问 Windows 两件事。

## 这个工具**能**回答什么

  1. **窗口有没有被裁成圆角** —— 读 DWM 的圆角偏好属性。
     这是我们唯一在用的机制（见 app/winutil.py 的 set_rounded_corners），
     读回来是 ROUND 就说明圆角设上了。
  2. **窗口有没有被自设的裁剪区域盖住** —— 读 GetWindowRgn。
     正常情况下应该永远是「没有区域」；有的话说明有段旧代码还在裁窗口，
     会把 DWM 的圆角整个盖掉。

## 这个工具**不能**回答什么

它证明不了「看起来好看」—— 半径合不合适、边缘有没有锯齿，
还是得人眼确认。别把这里的 OK 当成视觉验收。

## 为什么不做像素比对（踩过的坑）

前三版都是读窗口四角的像素、和参照色比，判断那个像素是不是窗口画的。
**三次都给出了错误但看起来很像结论的答案：**

  1. 参照色取窗口正中心 → 悬浮窗收起时中心正好画着一个方向箭头，
     于是整个窗口都被判成「不是窗口」，四个角全报「圆角」。
  2. 参照色改成「窗口内出现最多的颜色」→ 四个角全报「方角」，
     因为桌面深灰和卡片深灰差了不到容差。
  3. 参照色改成「从窗口外 60px 的桌面取」→ 那个位置正好压着旁边一个
     白色窗口，而卡片也是白的，于是又全报「方角」。

**同一件事错了三次，错的都是「在猜一个猜不准的东西」。**
而窗口形状本来就有一个确定答案可以问。换成问 API 之后，就没有
「猜错」这个失败模式了。

（如果将来真要验证视觉效果，正确做法是**控制背景** —— 让窗口铺满纯红、
衬在一块深色底上，就像 tools/exp_corner.py 里那样。有对照组的实验
比对着真实桌面猜可靠得多。）
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 量窗口几何必须在 DPI 感知的进程里做，否则拿到的是被系统虚拟化过的坐标。
# 真实程序在 run.py 里声明（见那边的注释），这里自己声明一次。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

DWMWA_WINDOW_CORNER_PREFERENCE = 33
CORNER_PREF = {
    0: "DEFAULT（交给系统决定）",
    1: "DONOTROUND（直角）",
    2: "ROUND（圆角）",
    3: "ROUNDSMALL（小圆角）",
}

ERROR = 0
REGION_KIND = {
    ERROR: "无区域 —— 正常情况",
    1: "空区域（窗口完全不可见）",
    2: "单块区域 —— ⚠️ 有代码在裁窗口",
    3: "多块区域 —— ⚠️ 有代码在裁窗口",
}


def _find(title: str) -> int:
    return ctypes.windll.user32.FindWindowW(None, title)


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _corner_pref(hwnd: int) -> str:
    got = ctypes.c_int(-1)
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(got), ctypes.sizeof(got),
        )
    except Exception:
        return "读不到（Win10 没有这个属性）"
    if hr != 0:
        return f"读不到（hr={hr}）"
    return CORNER_PREF.get(got.value, f"未知值 {got.value}")


def _region_kind(hwnd: int) -> str:
    gdi = ctypes.windll.gdi32
    hrgn = gdi.CreateRectRgn(0, 0, 1, 1)
    try:
        kind = ctypes.windll.user32.GetWindowRgn(hwnd, hrgn)
    finally:
        # ⚠️ 这里的区域是我们自己建的哑对象，GetWindowRgn 只是往里填，
        # 所有权没转移 —— 所以要自己删。这和 SetWindowRgn 相反
        # （那个是系统接管，删了会崩）。
        gdi.DeleteObject(hrgn)
    return REGION_KIND.get(kind, f"未知类型 {kind}")


def main() -> int:
    title = sys.argv[1] if len(sys.argv) > 1 else "时间规划表悬浮窗"
    hwnd = _find(title)
    if not hwnd:
        print(f"找不到窗口：{title}")
        print("（悬浮窗没开？或者标题写错了？）")
        return 1

    x, y, r, b = _rect(hwnd)
    visible = bool(ctypes.windll.user32.IsWindowVisible(hwnd))
    print(f"窗口 {title!r}")
    print(f"  位置 {x},{y}   尺寸 {r - x}×{b - y}   （物理像素）")
    # 可见性必须报。GetWindowRect 对**隐藏**的窗口照样返回矩形，
    # 所以光看尺寸分不清「它在那儿」和「它被藏起来了」——
    # 会让人以为"关不掉"（实际是关掉了，只是矩形还在）。
    print(f"  可见         ：{'是' if visible else '否（已隐藏）'}")
    print(f"  DWM 圆角偏好 ：{_corner_pref(hwnd)}")
    print(f"  形状裁剪区域 ：{_region_kind(hwnd)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
