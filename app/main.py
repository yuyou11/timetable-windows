"""
程序入口：窗口管理 + 提醒调度。

## 三个窗口

    main    主窗口，带系统标题栏。三个页面都在这里。
          （悬浮窗这个窗口本身的控制在 app/ballwin.py，页面在 web/ball.html）
    ball    悬浮窗。无边框、置顶、透明、不占任务栏。
    toast   课前提醒用的小提示条。启动时就创建好、平时藏起来。

    为什么 toast 要提前创建，而不是到点再建？
    因为 pywebview 的窗口必须在主线程创建，而提醒是在后台线程里触发的。
    提前建好、到点只做 show/hide，就绕开了这个限制。
    **遇到「只能在主线程做」的限制时，把它挪到启动阶段，比想办法跨线程安全得多。**

## 提醒为什么要单独开一个线程

因为 webview.start() 会**阻塞**住主线程直到所有窗口关闭。
不另开线程的话，就没有任何代码能在程序运行期间「到点做事」了。

这个线程的做法和手机版一模一样：**算出下一个需要醒来的时刻，然后睡到那时候**。
不做每秒轮询。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import webview

from . import dock, skin, slots, winutil
# 悬浮窗的窗口控制整体住在 app/ballwin.py（BallMixin，App 多继承它）。
# place_window / _ease_out 在这里**再导出**一次 —— 理由和下面 screen 那组
# 一样：tests/test_ball_placement.py 是 `from app.main import place_window`，
# tests/test_ball_ui.py 的 TestEaseOut 取的是 main._ease_out。
from .ballwin import HWND_TIMEOUT, BallMixin, _ease_out, place_window  # noqa: F401
from .api import Api
# ⚠️ 下面这些「读系统环境」的工具函数已经搬到 app/screen.py，这里**再导出**一次。
#
#    为什么不干脆搬走：tools/exp_dock.py、tools/exp_windows.py、tools/diag_web.py
#    和 run.py 都是 `from app.main import ...` 的写法。它们是排查窗口问题的
#    诊断工具，平时不跑 —— 直接搬走会让它们全部 ImportError，而且很久都不会
#    有人发现，直到真正需要它们的那一天。
#
#    必须写成**显式的名字列表**，不能用 `from .screen import *`：
#    星号导入不导入下划线开头的名字，`_bg_color` 会静默消失，
#    而 tools/exp_dock.py 正依赖它。
#    tests/test_main_helpers.py 的 test_main_does_not_star_import_screen 盯着这条。
from .reminders import ReminderScheduler
from .screen import (  # noqa: F401
    _bg_color,
    _titlebar_colors,
    is_dark_mode,
    monitor_rect,
    resolve_theme,
    resource_path,
    screen_size,
    ui_scale,
    work_area,
)
from .store import Store
from .tray import Tray

APP_TITLE = "时间规划表"
TOAST_W, TOAST_H = 330, 108

# ============================================================
#  标题栏主题：改写 pywebview 的判定，让它跟应用主题走
# ============================================================

#: 标题栏应该用深色还是浅色，build() 时按应用主题定（见 _pin_titlebar_to_app_theme）。
#: None = 还没定。
_APP_TITLEBAR_DARK: Optional[bool] = None

#: pywebview 原本的系统主题判定，patch 时存下来，给「值还没定」的回退路径用
_original_is_dark_theme = None


def _pin_titlebar_to_app_theme() -> str:
    """
    把 pywebview 的「标题栏跟系统主题」改成「跟应用主题」。返回结果说明。

    ## 为什么要 patch 第三方

    pywebview 的 WinForms 后端（winforms.BrowserView）在**窗口构造函数里**
    就调一次 update_title_bar_theme()，按 **系统** 主题改写标题栏深浅，
    还在 UserPreferenceChanged 事件（壁纸、IME……任何系统设置变动都触发）
    上注册了同一个改写器。应用侧「启动后自己再设一次」的写法赢不了这个时序 ——
    系统深色 + 应用浅色时，标题栏被它反复抹成黑色（用户报的正是这个）。

    所以直接改写它的**判定** `is_dark_theme()`：让它返回应用主题的深浅。
    它原本的改写逻辑（写哪些 DWM 属性、构造时写、事件里重写）一概保留 ——
    **写的极性对了，时序就无所谓了**。

    ⚠️ 这是对第三方内部类的 monkeypatch，pywebview 升级改名就会失效 ——
    所以有 getattr 守卫，失效时返回说明（调用方记日志），症状退回
    「标题栏跟系统主题走」，程序照常能用。不整个替换
    update_title_bar_theme 是因为那边还顺手写了窗口边框颜色（属性 38），
    只换判定比照抄整段逻辑稳。

    必须在 `webview.start()`（窗口真正创建）**之前**调用 ——
    构造函数里就要用上改好的判定。

    ⚠️ 目标是 **BrowserView.BrowserForm**：窗体方法全在 BrowserView 里
    的嵌套类 BrowserForm 上，BrowserView 本体没有 is_dark_theme
    （第一版 patch 到本体上，hasattr 悄悄是 False，整个改写静默没生效 ——
    这也是「返回值必须可读、日志必须留」的又一例证）。
    """
    global _APP_TITLEBAR_DARK, _original_is_dark_theme
    try:
        from webview.platforms import winforms as _wf
    except Exception as e:
        return f"未启用（导入 winforms 失败：{type(e).__name__}: {e}）"

    view = getattr(_wf, "BrowserView", None)
    form = getattr(view, "BrowserForm", None) if view is not None else None
    if form is None or not hasattr(form, "is_dark_theme"):
        return "未启用（pywebview 没有 BrowserView.BrowserForm.is_dark_theme，版本变了？）"
    if getattr(form, "is_dark_theme") is _app_is_dark:
        return "已启用（重复调用，跳过）"

    _original_is_dark_theme = form.is_dark_theme
    form.is_dark_theme = _app_is_dark
    return "已启用：标题栏深浅改由应用主题决定"


def _app_is_dark(self) -> bool:
    """顶替 pywebview 的 BrowserView.is_dark_theme —— 按应用主题回答"""
    if _APP_TITLEBAR_DARK is None:
        # 理论上到不了：build() 先于 webview.start 定好这个值。
        # 真到不了就退回 pywebview 原判定（跟系统），别把标题栏搞没了。
        if _original_is_dark_theme is not None:
            return _original_is_dark_theme(self)
        return False
    return _APP_TITLEBAR_DARK


# ============================================================
#  应用
# ============================================================

class App(BallMixin):

    def __init__(self) -> None:
        self.store = Store()
        self.api = Api(store=self.store)

        self.main: Optional[webview.Window] = None
        self.ball: Optional[webview.Window] = None
        self.toast: Optional[webview.Window] = None
        self.tray: Optional[Tray] = None

        #: 防止退出流程重入。见 _on_main_closing 的注释
        self._shutting_down = False

        self.reminder = ReminderScheduler(self.store, self._fire_reminder)

        # 悬浮窗的停靠/动画状态在 BallMixin._init_ball_state（app/ballwin.py）
        self._init_ball_state()

        self._wire_api()

    def _wire_api(self) -> None:
        """
        把 Api 需要的回调注进去。

        ## 为什么要单独一个方法

        这些槽位是「桥接层反向调用窗口层」的唯一通道 —— Api 不该知道 App
        的存在，所以由 App 主动把自己挂上去。这块有 8 行、名字还都以 `_on_`
        开头，挤在 `__init__` 里会把「状态初始化」这件事淹掉。

        ## ⚠️ 这些名字不能改，也不能去掉下划线

        pywebview 建窗口时会递归遍历 `js_api` 对象上的**公开**属性。
        去掉下划线，扫描器就会钻进窗口对象的
        `native.AccessibilityObject.Handle.Zero.Zero...` ——
        那是一条**无限属性链**，进程以 `0xC0000409` 硬崩、日志里一个字都没有。

        见 api.py 里那段长注释，以及 tests/test_frozen_contracts.py。
        """
        api = self.api
        api._on_settings_changed = self._on_settings_changed
        api._on_show_main = self._show_main
        api._on_quit = self._quit
        api._on_ball_drag_end = self._ball_drag_end
        api._on_ball_drag_start = self._ball_drag_start
        api._on_ball_move = self._ball_move
        api._on_ball_hover = self._ball_hover
        api._on_ball_slide_out = self._ball_slide_out

    # ---------------- 创建窗口 ----------------

    def build(self) -> None:
        sw, sh = screen_size()

        # 标题栏深浅改由**应用主题**决定（见 _pin_titlebar_to_app_theme 的说明 ——
        # 必须在 webview.start 创建窗口之前钉上，构造函数里就要用上）。
        global _APP_TITLEBAR_DARK
        _APP_TITLEBAR_DARK = resolve_theme(self.store.theme) == "dark"
        detail = _pin_titlebar_to_app_theme()
        # 失败不拦着启动：症状退回「标题栏跟系统主题」，能用
        self.api.log_error(f"[标题栏] 判定改写：{detail}")

        self.main = webview.create_window(
            winutil.TITLE_MAIN,
            str(resource_path("web", "index.html")),
            js_api=self.api,
            # 这里写的是**逻辑像素**，不要乘 DPI 系数 ——
            # pywebview 内部会自己换算。乘了就是双重缩放，
            # 窗口会大出一圈（实测要 1000 结果变成 1562）。详见 dock.py 开头。
            #
            # 默认尺寸按「内容列 max-width 900px + 侧栏 208px 能完整展开」
            # 给的（1000 宽时右侧只剩约 790px，900 的内容列被截着）；
            # 小屏幕上钳回工作区内 —— 宁可小一点，也别开出超出屏幕的窗口。
            width=min(1200, sw - 60),
            height=min(840, sh - 100),
            min_size=(820, 560),
            background_color="#f5f6f8",
            text_select=False,
        )
        self.api._window = self.main
        self.main.events.closing += self._on_main_closing

        if self.store.ball_enabled:
            self._create_ball(sw, sh)

        # 提醒提示条：提前建好，平时藏着。
        #
        # 为什么要提前建而不是到点再建？因为 pywebview 的窗口必须在主线程创建，
        # 而提醒是在后台线程里触发的。提前建好、到点只做 show/hide，
        # 就绕开了这个限制。
        # **遇到「只能在主线程做」的限制时，把它挪到启动阶段，
        # 比想办法跨线程安全得多。**
        self.toast = webview.create_window(
            winutil.TITLE_TOAST,
            str(resource_path("web", "toast.html")),
            js_api=self.api,
            width=TOAST_W,
            height=TOAST_H,
            x=max(0, sw - TOAST_W - 18),
            y=max(0, sh - TOAST_H - 100),
            frameless=True,
            easy_drag=True,
            on_top=True,
            hidden=True,
            # 底色跟随**设置里的主题**（不是直接跟系统）—— 和页面颜色必须同源，
            # 否则四个圆角处会露出一圈反色。理由见 screen._bg_color。
            background_color=_bg_color(self.store.theme),
        )

        # 页面加载完成后再补推一次主题。_polish_chrome 里那次推送有竞态：
        # 窗口句柄一出现它就跑，而页面可能还没解析到 __applyTheme，
        # 推送会静默落空 —— 落空的窗口就只能跟着系统的 prefers-color-scheme
        # 走，系统深色时就是一块深色卡片配浅色主窗口（用户撞到的）。
        # 主窗口有 app.js boot() 的兜底，悬浮窗和提示条没有，所以这里把
        # 主窗口和提示条都挂上（悬浮窗的在 _create_ball 里挂）：
        # loaded 每次触发都按设置推一遍，幂等无害。
        self.main.events.loaded += self._apply_theme
        self.toast.events.loaded += self._apply_theme
        # 自定义外观同理：polish 里推一次 + loaded 再兜一次（幂等）。
        self.main.events.loaded += self._push_skin
        self.toast.events.loaded += self._push_skin

        # ---- 托盘图标 ----
        #
        # 这是「永远都在的入口」：不管悬浮窗被隐藏、还是主窗口被关掉，
        # 右下角的图标一直能把它叫回来。
        #
        # 用户报过一个真实的困境：先隐藏悬浮窗、再关主窗口，
        # 程序还在跑但**没有任何入口能叫回来**（任务管理器里能看到进程）。
        # 托盘就是修这个的。
        self.tray = Tray(
            on_show_main=self._show_main,
            on_toggle_ball=self._toggle_ball,
            is_ball_enabled=lambda: self.store.ball_enabled,
            on_quit=self._quit,
            icon_path=resource_path("assets", "icon.png"),
        )
        if not self.tray.start():
            # 托盘起不来（缺库、被安全软件拦）不该影响使用，
            # 只是少了个兜底入口
            self.tray = None

        # ---- 主窗口/提示条的「外观整形」+ 标题栏配色 ----
        #
        # **与悬浮窗无关，悬浮窗关着也要做** —— 以前这套塞在悬浮窗的
        # polish 线程里，悬浮窗在设置里关掉时整段都不会跑，
        # 标题栏配色、提示条样式就全部没人管了（标题栏发黑的成因之一）。
        # 见 _polish_chrome。
        threading.Thread(target=self._polish_chrome, daemon=True,
                         name="chrome-polish").start()

    def _polish_chrome(self) -> None:
        """
        主窗口标题栏 + 提示条外观 + 主题推送。**与悬浮窗无关，总是要跑。**

        为什么从悬浮窗的 polish 里拆出来：这套东西原来塞在 `_create_ball`
        的 polish 线程里 —— 悬浮窗关掉时 `_create_ball` 整个不走，
        标题栏和提示条就没人管了。窗口整形必须等 HWND 出现，
        所以还是等一轮窗口，只是等的东西和悬浮窗解耦。
        """
        bg, fg = _titlebar_colors(self.store.theme)
        main_hwnd = winutil.wait_for_window(winutil.TITLE_MAIN, HWND_TIMEOUT)
        detail = winutil.set_titlebar_theme(
            main_hwnd, bg, fg, resolve_theme(self.store.theme) == "dark")
        # 失败**不能**默默吞掉。这个功能坏了的表现是「就是不变、没有任何
        # 报错」，那种问题最难查 —— 所以留一行，翻日志就知道走到哪一步了。
        self.api.log_error(
            f"[标题栏] {detail}（hwnd={main_hwnd}, 主题={self.store.theme}, "
            f"解析后={resolve_theme(self.store.theme)}, 目标={bg}/{fg}）"
        )

        # ⚠️ 上面这次设置本身没问题，但**单靠它赢不了时序**：
        #    pywebview 在窗口构造函数里就直接按系统主题改写一次，
        #    还在 UserPreferenceChanged 事件上注册了改写器 —— 我们的
        #    事件处理器注册得比它晚或早都不稳。真正的对策在
        #    `_pin_titlebar_to_app_theme`（build() 里调用）：把 pywebview
        #    的判定函数改成按**应用主题**回答，它每次改写都替我们写。

        # ---- 提示条 ----
        toast_ready = bool(winutil.wait_for_window(winutil.TITLE_TOAST, HWND_TIMEOUT))
        if toast_ready:
            winutil.make_tool_window(winutil.TITLE_TOAST)
            winutil.disable_shadow(winutil.TITLE_TOAST)
            # 提示条离屏幕边缘有 18px / 100px 的余量，永远不会贴边，
            # 所以它不需要 _apply_ball_geometry 那种「按位置决定」的逻辑
            winutil.set_rounded_corners(winutil.TITLE_TOAST, True)
        else:
            self.api.log_error(
                f"等提示条 HWND 超时（{HWND_TIMEOUT:.0f} 秒）：窗口样式没设上"
            )

        # 主题的强制档现在推一次；`boot()` 那边还会再推一次兜底
        # （这里可能赶在页面加载完成之前，那一次会被 window.__applyTheme
        # 还没定义而静默跳过 —— 所以不能只有这一处）。
        self._apply_theme()
        self._push_skin()

    @staticmethod
    def _eval_js(window, code: str) -> None:
        """
        往一个窗口里注入一段 JS。窗口不在（或已经销毁）就当没这回事。

        这段 try/except 样板原本在好几个地方各写一遍。收在一处之后，
        「注入失败怎么办」只有一个地方要决定。

        ## ⚠️ 目前是**静默**吞掉的

        README 踩坑第 4.9 条正好是这件事的反面教材：`_apply_ball_geometry`
        里一个 `except: pass`，让「拖到右边不缩回去」查了很久没有任何线索。

        下面这几处目前也是静默的 —— **本轮重构保持原样，没有改行为**。
        要不要统一改成写 error.log，是个独立的决定（会多出日志行），
        留待单独处理。

        ## 两个细节，改的时候别踩

        1. 参数必须**按位置传**给 `evaluate_js`。测试里的假窗口签名是
           `evaluate_js(self, script)`，用关键字传参会直接 TypeError。
        2. `_show_toast` **故意没有**用它：那边 `evaluate_js` 和
           `show()`、自动隐藏线程写在**同一个 try** 里 —— JS 注入失败时
           后面两件事会被一起跳过。换成这个 helper 会让 `show()` 照常执行，
           那是行为变化。
        """
        if window is None:
            return
        try:
            window.evaluate_js(code)
        except Exception:
            pass

    # ---------------- 事件 ----------------

    def _on_main_closing(self) -> bool:
        """
        关闭主窗口时的行为。返回 False 会**取消关闭**。

        规则：
          · 悬浮窗（或托盘）还在 → 只把主窗口藏起来，程序留在后台。
            这正是悬浮窗的意义：关掉大窗口，小卡片还在报「现在该做什么」。
          · 都没有 → 真的退出。

        ## ⚠️ 这里有一个踩过的坑，改这个函数前务必读完

        最初的写法是「要退出就直接调 _quit()」，而 _quit() 会销毁**所有**窗口，
        **包括正在关闭的这个主窗口**。

        结果：销毁 → 再次触发 FormClosing → 又调 _quit() → 再销毁……
        无限递归，直到把调用栈撑爆。用户看到的是这个 Windows 错误框：

            System.InsufficientExecutionStackException: 堆栈空间不足
            在 System.Windows.Forms.Form.OnFormClosing

        触发条件很具体：**悬浮窗被关掉之后（ball_enabled = false）再关主窗口**。
        因为只有走「真的退出」这个分支才会碰 _quit()。
        悬浮窗开着的话走的是 hide 分支，反而不会崩 ——
        所以这个 bug 藏了很久才被撞出来。

        **规矩：永远不要在窗口自己的关闭事件里销毁它自己。**
        """
        if self._shutting_down:
            # 已经在退出流程里了，别再来一轮
            return True

        if self.store.ball_enabled or self.tray is not None:
            # 留在后台。托盘图标是这时候唯一的入口，所以必须有。
            if self.main is not None:
                self.main.hide()
            return False

        self._shutdown(keep=self.main)
        return True

    def _show_main(self) -> None:
        if self.main is None:
            return
        self.main.show()
        self.main.restore()
        self.main.on_top = True     # 顶到最前面一下，再取消，避免被别的窗口压住
        self.main.on_top = False

    def _on_settings_changed(self) -> None:
        """设置变了 → 同步悬浮窗的显示状态 + 让提醒线程重算"""
        want_ball = self.store.ball_enabled
        try:
            if want_ball:
                # 用 _ensure_ball：它连「窗口被外部销毁过」这种情况一起处理。
                self._ensure_ball()
        except Exception:
            # 运行中途新建窗口在部分环境下不被支持，
            # 那就等下次启动再生效，不要让程序崩掉
            pass

        try:
            if self.ball is not None:
                if want_ball:
                    self.ball.show()
                    self._push_ball_layout()
                    self._reset_ball_close_button()
                else:
                    self.ball.hide()
        except Exception:
            pass

        self.reminder.kick()

        # 托盘菜单里的勾选状态跟着变
        try:
            if self.tray is not None:
                self.tray.refresh()
        except Exception:
            pass

        # 通知主窗口刷新（悬浮窗那边可能改了设置）
        self._eval_js(self.main, "window.refreshAll && window.refreshAll()")
        # 外观类的设置也从这里走（set_accent / set_card_opacity /
        # choose_background_image 都会触发 _notify_settings_changed）——
        # 这就是它们「立即生效」的那一步。重复推幂等无害。
        self._push_skin()

    def _fire_reminder(self, moment, when_text: str) -> None:
        """提醒到点：响一声 + 弹出提示条"""
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

        self._show_toast(
            f"{when_text}：{moment.title}",
            f"{slots.fmt(moment.start)}–{slots.fmt(moment.end)}"
            + (f"　·　{moment.place}" if moment.place else ""),
        )

        # 悬浮窗也跳一下，让注意力落到「现在该做什么」上
        self._eval_js(self.ball, "window.pulse && window.pulse()")


    def _apply_theme(self) -> None:
        """
        把设置里选的主题推给三个页面。只在「始终浅色 / 始终深色」时才推 ——
        跟随系统是纯 CSS 就成立的，去碰它只会把简单的事情搞复杂
        （理由见 style.css 顶部那段）。

        ## 为什么是「推」，而不是页面自己去问

        页面去问要跨桥调后端，那是**异步**的，赶不上首次绘制。
        推也有代价：强制档在启动瞬间会先按系统画一次、再切过去，
        有几十毫秒的换色。用「跟随系统」的人一次都不会闪。

        ## 为什么值必须和窗口底色同源

        小窗口的**窗口底色**只能在创建时定，运行期改不了 —— 而它会在四个
        圆角处露出来（见 `screen._bg_color`）。页面颜色和它对不上，
        就是圆角处一圈反色边。

        所以两处都走 `resolve_theme(store.theme)`。⚠️ 这里改了、
        `_bg_color` 没改（或者反过来），得到的是**只在某一个主题下看得见**
        的 bug —— 换个主题就正常了，几乎没法自查。
        """
        if self.store.theme == "system":
            return
        theme = resolve_theme(self.store.theme)
        for w in (self.main, self.ball, self.toast):
            if w is None:
                continue
            try:
                w.evaluate_js(f"window.__applyTheme && window.__applyTheme('{theme}')")
            except Exception:
                # 一个窗口失败不该连累另外两个 —— 分开 try
                self.api.log_error(f"推主题失败：{theme}")

    def _push_skin(self) -> None:
        """
        把自定义外观推给各窗口。设置里改主色调 / 卡片透明度 / 背景图时
        （以及启动、页面加载完成时）都会走到这里。

        · 主窗口推**全套**（主色调 + 半透明底色，见 skin.main_payload）——
          它是矩形窗口，页面铺满客户区，半透明没有底色可露。
        · 悬浮窗只推**主色调**。它的窗口底色创建时就定死了，透明卡片
          会在圆角处露出一圈实色（skin.py 开头有完整说明）；
        · 提示条连主色调都没用上（配色只有 surface/text/danger），不用推。

        背景图不走这里：它是几 MB 的 data URL，由前端在 boot() 和设置变更后
        用 `get_background_image` 自己来拉 —— 推大块数据过桥不划算，
        而且只有主窗口需要它。

        ⚠️ 值按**启动时解析的深浅档**算（和 _apply_theme 同一约束）：
        「跟随系统」时系统中途换深浅档，覆盖色不会跟着重算 ——
        主题档本来就要重启才整套生效，这里保持同一口径。
        """
        import json

        theme = resolve_theme(self.store.theme)
        payload = json.dumps(skin.main_payload(
            theme, self.store.accent,
            self.store.card_opacity, self.store.bg_image,
            self.store.text_color, self.store.sidebar_opacity,
        ))
        self._eval_js(self.main,
                      f"window.__applySkin && window.__applySkin({payload})")

        if self.ball is not None:
            accent = json.dumps(skin.accent_payload(self.store.accent, theme))
            self._eval_js(self.ball,
                          f"window.__applyAccent && window.__applyAccent({accent})")

    def _show_toast(self, title: str, body: str) -> None:
        if self.toast is None:
            return
        try:
            import json
            payload = json.dumps({"title": title, "body": body}, ensure_ascii=False)
            self.toast.evaluate_js(f"window.showToast && window.showToast({payload})")
            self.toast.show()

            def auto_hide() -> None:
                time.sleep(9)
                try:
                    if self.toast is not None:
                        self.toast.hide()
                except Exception:
                    pass

            threading.Thread(target=auto_hide, daemon=True).start()
        except Exception:
            pass

    def _quit(self) -> None:
        """从托盘菜单或界面里主动退出"""
        self._shutdown(keep=None)

    def _shutdown(self, keep=None) -> None:
        """
        收尾：停掉后台线程、收掉托盘、销毁窗口。

        [keep] 是**不要销毁**的那个窗口 —— 当这个流程是从某个窗口的
        关闭事件里触发的时候，必须把那个窗口传进来。

        为什么？销毁一个正在关闭的窗口会让它再触发一次 FormClosing，
        又回到这里，又销毁一次…… 无限递归把调用栈撑爆。
        详见 _on_main_closing 的注释。
        """
        if self._shutting_down:
            return
        self._shutting_down = True

        self.reminder.stop()

        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass

        for w in (self.ball, self.toast, self.main):
            if w is None or w is keep:
                continue
            try:
                w.destroy()
            except Exception:
                pass

    # ---------------- 启动 ----------------

    def run(self) -> None:
        self.build()

        # 悬浮窗没开、也没设提醒时，主窗口关掉程序就该结束。
        # debug=False 让打包后的程序不弹开发者工具。
        #
        # icon 是**全局**的，管的是所有窗口的标题栏左上角图标，不是单个窗口的。
        # pywebview 走的是 Windows Forms 的 Form.Icon，所以必须给 .ico
        # （多尺寸的那种），PNG 在 Windows 侧会被忽略。
        #
        # ⚠️ 路径必须用 resource_path() 而不是 __file__ 直接拼 ——
        # 打包后资源在 sys._MEIPASS 的临时目录里，用 __file__ 会找不到，
        # 而找不到的后果不是报错，是**静默地用回默认图标**。
        # 这也是 build.py 的 --add-data 必须把 assets 一起带上的原因。
        icon = resource_path("assets", "icon.ico")
        webview.start(
            debug=os.environ.get("TIMETABLE_DEBUG") == "1",
            icon=str(icon) if icon.exists() else None,
        )


def main() -> int:
    # 单实例检测不在这里 —— 它在 run.py 的最前面（app/instance.py）：
    # 越早拦越好，第二个实例不该把 webview 这一套都拉起来才发现重复。
    App().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
