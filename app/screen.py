"""
跟系统环境打交道的小工具：屏幕、DPI 缩放、系统主题、资源路径。

这些函数原本住在 `main.py` 里，和窗口管理、提醒线程、退出流程混在一起 ——
于是「读一下屏幕有多大」这么单纯的事，也得先把整个窗口系统拖进来才能测。

搬出来之后有两点好处：

  1. **可以单测了**。这个模块**不导入 webview**，所以能在没有界面的情况下
     直接跑（见 tests/test_main_helpers.py）。
  2. `main.py` 少了一层职责 —— 它现在只管窗口和进程。

## ⚠️ 这个文件必须留在 `app/` 这一层

别把它挪进子包（比如 `app/ui/screen.py`）。`resource_path()` 用
`Path(__file__).resolve().parent` 当基准目录，而它找到的那个目录要和
`--add-data` 铺开的结构对上（见 build.py）。放进子包，基准就变了，
资源**一个都找不到** —— 而且不会有任何报错，只是窗口一片空白、图标变回默认。

`tests/test_main_helpers.py::test_finds_real_files` 会在那种情况下当场失败。

## ⚠️ `main.py` 里必须保留再导出

`tools/exp_dock.py`、`tools/exp_windows.py`、`tools/diag_web.py` 都是
`from app.main import ...` 的写法。它们是排查窗口问题的诊断工具，平时不跑，
坏了很久都不会有人发现。所以 `main.py` 里有一行显式的再导出
（**不能**写成 `from .screen import *`：星号导入会跳过下划线开头的名字，
`_bg_color` 会静默消失）。

## 单位约定

`work_area()` / `screen_size()` 返回的都是**逻辑像素**。
唯一的物理→逻辑换算发生在 `work_area()` 内部，用的是 `ui_scale()` ——
那是它**唯一**该被用到的地方（理由见 `ui_scale` 的 docstring）。
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from . import dock


def is_dark_mode() -> bool:
    """
    Windows 是不是深色主题。

    为什么需要它：悬浮窗和提示条这两个小窗口**没有透明背景可以依赖**
    （见下面 _bg_color 的注释），所以得自己挑一个和页面一致的颜色，
    否则深色主题下会在深色卡片四周露出一圈浅色边框。

    读注册表就够了，不用引入任何依赖。读不到就当作浅色。
    """
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return value == 0
    except Exception:
        return False


def _bg_color() -> str:
    """
    小窗口的底色，跟随系统主题。

    ## 为什么不用透明窗口了

    第一版给悬浮窗设了 `transparent=True`，想让圆球直接浮在桌面上。
    实际跑起来发现：**WebView2 在部分环境下不会把页面背景真正渲染成透明**，
    于是窗口露出默认底色 —— 屏幕上出现一个突兀的黑白方框。

    而且透明窗口还牵扯出一个更严重的问题：pywebview 在无边框窗口上
    遍历无障碍对象时会无限递归（详见 api.py 里那段注释）。

    所以现在改成**不透明的小卡片**：页面自己画一个带边框的卡片，
    窗口底色和卡片一致，看起来就是一整块。不依赖任何透明能力。

    ⚠️ 另外记一笔：pywebview 的 `background_color` **只接受 6 位十六进制**
    （#RRGGBB）。写 8 位带透明度的（#RRGGBBAA）会直接抛
    "is not a valid hex triplet color"，程序起不来。
    """
    return "#181b21" if is_dark_mode() else "#ffffff"


def resource_path(*parts: str) -> Path:
    """
    取资源文件的真实路径。

    **PyInstaller 打包后，代码和资源会被解压到一个临时目录**，
    那个目录的路径在 sys._MEIPASS 里。所以不能简单用 __file__ 拼路径 ——
    开发时能跑，打包后就找不到文件了。这是打包新手最常踩的坑之一。

    用法是把**打包前后的相对结构**写进参数里：

        resource_path("web", "index.html")     →  app/web/index.html
        resource_path("assets", "icon.ico")    →  app/assets/icon.ico

    注意第一段写的是 `web` / `assets`，而不是 `app/web` ——
    因为 `--add-data` 的 dest 参数就是把它们铺在这一层（见 build.py）。

    ## 为什么要在几个候选路径里找

    开发时资源在 `app/web/`，打包后取决于 `--add-data` 的 dest 参数写的是什么。
    这两者很容易配错 —— 而配错的后果是**静默失效**，没有任何报错：

        · 界面文件找不到 → 窗口一片空白（webview 只是显示不出来）
        · 图标文件找不到 → 用回默认图标

    所以这里主动试几个候选路径，找到就用。多几行代码，换掉一类「静默失效」。
    """
    candidates = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.append(Path(base).joinpath(*parts))
        candidates.append(Path(base).joinpath("app", *parts))
    here = Path(__file__).resolve().parent
    candidates.append(here.joinpath(*parts))
    candidates.append(here.parent.joinpath(*parts))

    for c in candidates:
        if c.exists():
            return c
    # 都不存在就返回第一个 —— 让后续的报错发生在「读文件」那一步，
    # 报错信息里会带上完整路径，比在这里抛一句「找不到」更有用
    return candidates[0]


# ============================================================
#  屏幕尺寸（只用标准库，不引入额外依赖）
# ============================================================

def ui_scale() -> float:
    """
    DPI 缩放系数：1.0 = 100%，1.25 = 125%，1.5 = 150%。

    ## 这个值**只在一个地方用得上**

    把 Win32 拿到的**物理像素**工作区，换算成 pywebview 要的**逻辑像素**。

    ⚠️ **除此之外任何地方都不要拿它去乘尺寸或坐标。**

    我一开始的理解是反的：以为声明了 DPI 感知之后所有东西都变成物理像素，
    于是把界面尺寸都乘了 1.25「补回来」。结果 pywebview 内部也乘了一次 ——
    **双重缩放**：悬浮窗要 305 宽变成 381、要放在 x=2241 变成了 x=2801，
    直接跑到屏幕外面去了（工作区右边界才 2560）。

    pywebview 的 Windows 后端整个公开 API 都是逻辑像素，
    DPI 换算它自己会做。详见 dock.py 开头的说明。

    ## 为什么两条路都试

    `GetDpiForSystem` 只在 Win10 1607+ 有；`GetDeviceCaps` 要自己管 DC 释放。
    都拿不到就返回 1.0 —— 缩放算错顶多是位置差一点，不该因此起不来。
    """
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi:
            return dpi / 96.0
    except Exception:
        pass

    try:
        hdc = ctypes.windll.user32.GetDC(0)
        try:
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)   # LOGPIXELSX
            if dpi:
                return dpi / 96.0
        finally:
            ctypes.windll.user32.ReleaseDC(0, hdc)
    except Exception:
        pass

    return 1.0


def work_area() -> dock.Area:
    """
    屏幕上的**可用区域**（扣掉任务栏那块），单位是**逻辑像素**。

    为什么不用整个屏幕？因为悬浮窗要贴着边缘停靠。用整屏的边界算，
    卡片会贴到任务栏**底下**去，被压住看不见。

    ## 这里必须做一次单位换算

    `SPI_GETWORKAREA` 返回的是**物理像素**（因为 run.py 声明了 DPI 感知），
    而 pywebview 的坐标系统是**逻辑像素**。不换算的话，在 125% 的屏上
    右边界会从 2048 变成 2560，所有位置计算全错。

    这也是 ui_scale() 唯一该被用到的地方。

    SPI_GETWORKAREA 同时给出原点（left/top）和右下角 ——
    任务栏在左边或上边时原点不是 (0,0)，这点很容易忽略。
    """
    scale = ui_scale() or 1.0
    phys = _work_area_physical()
    return dock.Area(
        round(phys.left / scale), round(phys.top / scale),
        round(phys.right / scale), round(phys.bottom / scale),
    )


def _work_area_physical() -> dock.Area:
    """工作区的物理像素值。拿不到就退回一个常见分辨率。"""
    try:
        import ctypes.wintypes as wt
        rect = wt.RECT()
        SPI_GETWORKAREA = 0x0030
        if ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
        ):
            area = dock.Area(rect.left, rect.top, rect.right, rect.bottom)
            if area.width > 200 and area.height > 200:
                return area
    except Exception:
        pass

    try:
        scale = ui_scale() or 1.0
        return dock.Area(
            0, 0,
            round(ctypes.windll.user32.GetSystemMetrics(0) / scale),
            round(ctypes.windll.user32.GetSystemMetrics(1) / scale),
        )
    except Exception:
        return dock.default_area()


def screen_size() -> tuple[int, int]:
    """工作区的宽高（逻辑像素）"""
    area = work_area()
    return area.width, area.height
