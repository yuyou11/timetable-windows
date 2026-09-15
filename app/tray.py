"""
系统托盘图标。

## 为什么需要它

用户报过一个真实的困境：

    先把悬浮窗隐藏了 → 再把主窗口关掉 → **程序还在跑，但没有任何入口能叫回来**

（能在任务管理器里看到进程，就是点不到。）

托盘图标就是那个「永远都在的入口」—— 不管窗口藏到哪、关掉几次，
图标一直在右下角，随时能把它叫回来。桌面工具该有的兜底。

## 图标用和窗口 / exe 同一个

早期这里是拿 Pillow 现画一个圆角方块 + 「时」字。后来应用有了统一的图标
（`app/assets/icon.png`，由 `tools/gen_icon.py` 生成），托盘就跟着用它了 ——
托盘、标题栏、资源管理器里显示的是同一个图案，一眼认得出是同一个程序。

顺带解决了一个隐患：画「时」字要依赖系统里的中文字体，取不到就只剩一个
纯色块。直接读图片没有这个依赖。

万一图片读不到（打包漏了 assets），会**退回**原来那个纯色方块 ——
托盘图标缺失会让整个托盘功能失效，那是「关掉主窗口就再也叫不回来」的
那种严重问题，不能因为一张图读不到就发生。

## 回调在别的线程里跑

pystray 有自己的消息循环线程。好在 pywebview 的窗口方法内部做了
`InvokeRequired` 判断并会调度回 UI 线程（见 webview/platforms/winforms.py），
所以从托盘线程调 `window.show()` 是安全的。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

try:
    import pystray
    from pystray import Menu, MenuItem
    _HAS_PYSTRAY = True
except Exception:                                        # noqa: BLE001
    _HAS_PYSTRAY = False

try:
    from PIL import Image, ImageDraw
    _HAS_PIL = True
except Exception:                                        # noqa: BLE001
    _HAS_PIL = False


#: 读不到图标文件时的兜底底色
_ACCENT = (37, 99, 235)


def _make_icon(icon_path: Optional[Path] = None, size: int = 64):
    """
    托盘图标：优先用应用图标，读不到就退回一个纯色圆角方块。

    ## 为什么要先缩小

    `icon.png` 是 256×256，而托盘只需要 16–32px。PIL 的 `thumbnail()`
    会按比例缩到框内，质量比让系统自己缩要好。

    ## 为什么兜底是纯色块而不是直接失败

    托盘图标缺失会让整个托盘功能不可用（pystray 起不来），而托盘是
    「关掉所有窗口之后唯一的入口」。为了一张图读不到就失去这个兜底，
    代价完全不对等 —— **宁可朴素，不能没有**。
    """
    if not _HAS_PIL:
        return None

    if icon_path is not None:
        try:
            img = Image.open(icon_path).convert("RGBA")
            img.thumbnail((size, size), Image.LANCZOS)
            return img
        except Exception:
            # 读不到就往下走兜底，不要把异常抛出去
            pass

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(2, size // 5)
    # 圆角方块 + 中间留白，远看就是个图标，不依赖任何字体
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=_ACCENT)
    inset = size // 3
    draw.ellipse(
        [inset, inset, size - 1 - inset, size - 1 - inset],
        outline=(255, 255, 255, 255), width=max(2, size // 16),
    )
    return img


class Tray:
    """
    托盘图标。**所有回调都允许失败** —— 托盘是兜底功能，
    不该因为它让程序出问题。
    """

    def __init__(
        self,
        on_show_main: Callable[[], None],
        on_toggle_ball: Callable[[], bool],
        is_ball_enabled: Callable[[], bool],
        on_quit: Callable[[], None],
        icon_path: Optional[Path] = None,
    ) -> None:
        self._on_show_main = on_show_main
        self._on_toggle_ball = on_toggle_ball
        self._is_ball_enabled = is_ball_enabled
        self._on_quit = on_quit
        #: 应用图标（app/assets/icon.png）。调用方传进来而不是在这里算路径 ——
        #: 打包后要经过 sys._MEIPASS，那个逻辑只应该有一处（main.resource_path）
        self._icon_path = icon_path
        self._icon: Optional["pystray.Icon"] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def available(self) -> bool:
        return _HAS_PYSTRAY and _HAS_PIL

    def start(self) -> bool:
        """启动托盘。失败返回 False，调用方不必当回事。"""
        if not self.available:
            return False
        try:
            menu = Menu(
                MenuItem("打开主窗口", self._show_main, default=True),
                MenuItem("显示悬浮窗", self._toggle_ball, checked=self._ball_checked),
                Menu.SEPARATOR,
                MenuItem("退出", self._quit),
            )
            image = _make_icon(self._icon_path)
            if image is None:
                return False

            self._icon = pystray.Icon("timetable", image, "时间规划表", menu)
            # run_detached 会在自己的线程里跑消息循环，不挡住主线程
            self._icon.run_detached()
            return True
        except Exception:
            self._icon = None
            return False

    def stop(self) -> None:
        try:
            if self._icon is not None:
                self._icon.stop()
        except Exception:
            pass
        self._icon = None

    def refresh(self) -> None:
        """悬浮窗开关变了之后，让菜单里的勾选状态跟着变"""
        try:
            if self._icon is not None:
                self._icon.update_menu()
        except Exception:
            pass

    # ---------------- 菜单回调 ----------------

    def _ball_checked(self, item) -> bool:
        """菜单项的勾选状态。pystray 每次打开菜单都会调它，所以总是最新的"""
        try:
            return bool(self._is_ball_enabled())
        except Exception:
            return False

    def _show_main(self, icon=None, item=None) -> None:
        self._safe(self._on_show_main)

    def _toggle_ball(self, icon=None, item=None) -> None:
        self._safe(self._on_toggle_ball)
        self.refresh()

    def _quit(self, icon=None, item=None) -> None:
        # 先收掉托盘图标，否则图标会在程序退出后残留一会儿
        self.stop()
        self._safe(self._on_quit)

    @staticmethod
    def _safe(fn: Callable[[], None]) -> None:
        """
        执行回调，吞掉异常。

        托盘回调里抛异常会**直接杀掉 pystray 的消息循环** ——
        图标还留在托盘上，但从此点它没有任何反应。这种「看着还在、实际已死」
        比干脆消失更让人困惑，所以这里一律兜住。
        """
        try:
            fn()
        except Exception:
            pass
