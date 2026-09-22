"""
程序入口：窗口管理 + 提醒调度。

## 三个窗口

    main    主窗口，带系统标题栏。三个页面都在这里。
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

from . import dock, engine, slots, winutil
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
    is_dark_mode,
    resolve_theme,
    resource_path,
    screen_size,
    ui_scale,
    work_area,
)
from .store import Store
from .tray import Tray

APP_TITLE = "时间规划表"
BALL_W, BALL_H = 244, 104       # 悬浮窗（常驻小卡片，不是圆球）
TOAST_W, TOAST_H = 330, 108

#: 等窗口真正出现的上限（秒）。见 `winutil.wait_for_window` 的说明 ——
#: 它取代了原来 `_polish` 里那句「睡 0.9 秒」，因为那句的余量只有 0.1 秒。
HWND_TIMEOUT = 10.0


# ============================================================
#  悬浮窗收起/展开动画
# ============================================================

#: 一次收起（或展开）的总时长（毫秒）
#:
#: 160ms 是**测出来**够用的，不是拍的：`tools/exp_anim_fps.py` 在真窗口上量过，
#: 单次 resize+move 平均 1–3 ms（首次冷启动 6.5 ms），12 帧走完约 65–72 ms，
#: 30 帧不留间隔的压测也只有平均 2.0 ms/帧 —— 60fps 的预算是 16.7 ms/帧，
#: 所以余量很大。**这里的瓶颈从来不是"做不做得到"，而是内容被挤扁的问题**，
#: 那部分交给网页那边的淡出处理（见 ball.html 里的说明）。
BALL_ANIM_MS = 160

#: 动画分几帧走完。160 / 16 = 每帧 10 ms（上限约 100fps）。
#:
#: 为什么不用满 60fps（即 10 帧）：多几帧能让窗口边缘走得更匀，
#: 而代价只是几毫秒 —— 实测每帧才 2 ms 左右。留出余量也更从容，
#: 不会让 WinForms 的 UI 线程一直忙于跨进程 Invoke。
#:
#: 反过来说，帧数也不是越多越好：每帧都是一次真实的 SetWindowPos，
#: 16 帧已经看不出台阶了，再翻倍只是白白增加系统调用。
BALL_ANIM_STEPS = 16


def _ease_out(t: float) -> float:
    """
    缓动曲线：开始快、结尾慢（三次方的 ease-out）。

    为什么要缓动而不是匀速：匀速移动看起来"机械"，而且结尾是**突然停住**的，
    观感上会顿一下。缓出会让它像被边缘「吸」进去，收尾柔和。

    这里用三次方而不是二次方：三次方收尾更明显，短动画（160ms）里
    这点差别是看得出来的 —— 二次方还是有点"直"。

    曲线本身：t=0 → 0，t=1 → 1，中点 t=0.5 → 0.875（已经走了 87.5%），
    所以大部分位移发生在前半段，后半段在慢慢贴上去。
    """
    return 1.0 - (1.0 - t) ** 3


# ============================================================
#  应用
# ============================================================

class App:

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

        # ---- 悬浮窗的停靠状态 ----
        # 这几项是「窗口该在哪」的唯一真相来源。
        # 为什么不直接读窗口的 x/y/w/h？因为实测发现 pywebview 报回来的
        # width/height 和实际设置的值对不上（疑似 DPI 缩放或客户区口径不同）。
        # 位置 x/y 是准的，尺寸就自己记着，免得反复展开收起时越算越偏。
        self._ball_edge: Optional[str] = None      # None = 自由浮动
        self._ball_center: int = 0                 # 贴边时那条要保住的中线
        self._ball_collapsed: bool = False
        self._ball_w: int = dock.CARD_W
        self._ball_h: int = dock.CARD_H
        #: 自由浮动时的位置。**自己记着，不回读窗口。**
        #:
        #: 为什么不直接读 self.ball.x？因为**创建窗口后的短时间内它不可靠** ——
        #: 实测同一份代码跑两次，读回来的位置一次对一次错（窗口还没摆好就去读，
        #: 拿到的是 WinForms 的默认位置，然后又被当成真值用来算下一步，越走越偏）。
        #:
        #: 所以：**我们算出来的位置才是唯一真相**，窗口只是被我们摆过去而已。
        #: 这和 _ball_w/_ball_h 的处理是一致的 —— 那对尺寸也早就改成自己记了，
        #: 因为 pywebview 报回来的尺寸也对不上。
        self._ball_x: int = 0
        self._ball_y: int = 0

        #: 悬浮窗有没有已经设成圆角。
        #:
        #: 记着它是为了**避免重复的系统调用** —— 拖动时 _apply_ball_geometry
        #: 每秒会被调几十次。详见 _ensure_ball_rounded。
        self._ball_rounded: bool = False

        #: 刚停靠时把「自动弹出」压住一会儿。
        #:
        #: 松手停靠的那一刻，鼠标就在边上 —— 如果不压住，窗口刚缩进去
        #: 就会因为鼠标还悬在标签上而立刻弹出来，看起来像什么都没发生。
        #: 等鼠标真正离开一次之后，再允许弹出。
        self._ball_suppress_expand: bool = False

        #: 收起/展开动画的**当前进度**：0.0 = 展开的卡片，1.0 = 收起的小方框。
        #:
        #: 平时它不是 0 就是 1（对应两种静止形态），只有动画进行中才是中间值。
        #: 记着它的用处是：**动画可以被中途打断并从当前位置接着走**。
        #: 鼠标刚移开又移回来时，如果每次都从"另一端"重新开始，
        #: 窗口会先跳过去再走回来 —— 那样很难看。
        self._ball_progress: float = 0.0

        #: 动画作废令牌。
        #:
        #: 每次要开始一个新动画（或者直接落到最终位置）就 +1。
        #: 正在跑的动画线程每帧对一次，发现自己手里的号过期了就**立刻退出**。
        #:
        #: 为什么需要它：动画跑在独立线程里，而触发它的事件（悬停、拖动、
        #: 设置变更）随时可能再来一次。两个动画同时改同一个窗口，
        #: 结果就是窗口在两组坐标之间来回抽搐。
        #:
        #: 为什么用自增整数而不是 threading.Event：读一个 int、比一下、写一个 int
        #: 在 CPython 里都是原子操作（GIL 保证），不需要加锁，
        #: 也不存在"重置 Event 时把新动画一起干掉"那种时序陷阱。
        #: 这个项目里已经有一个"标志只在某处清除、结果永久卡住"的教训了
        #: （见 _ball_suppress_expand），所以这里刻意选了不会卡住的写法。
        self._ball_anim_token: int = 0

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

        self.main = webview.create_window(
            winutil.TITLE_MAIN,
            str(resource_path("web", "index.html")),
            js_api=self.api,
            # 这里写的是**逻辑像素**，不要乘 DPI 系数 ——
            # pywebview 内部会自己换算。乘了就是双重缩放，
            # 窗口会大出一圈（实测要 1000 结果变成 1562）。详见 dock.py 开头。
            width=1000,
            height=720,
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

    def _create_ball(self, sw: int, sh: int) -> None:
        if self.ball is not None:
            return

        # ---- 算出它该出现在哪 ----
        # 上次的停靠状态优先。用户在右边缩起来的卡片，下次打开应该还在那，
        # 每次都回到默认位置会让人反复调整，很烦。
        area = work_area()
        saved_edge = self.store.ball_edge

        if saved_edge:
            self._ball_edge = saved_edge
            self._ball_center = self.store.ball_center
            self._ball_collapsed = True
            # 这里**不能**直接压住自动弹出 —— 见 _polish 里那段的说明。
            # 压不压取决于「启动时光标是不是恰好压在悬浮窗上」，
            # 而那要等窗口真正摆好位置才知道，所以判断挪到 _polish 里。
            self._ball_suppress_expand = False
            x, y, w, h = dock.docked_geometry(
                saved_edge, self._ball_center, area, True
            )
        else:
            self._ball_edge = None
            self._ball_collapsed = False
            px, py = self.store.ball_pos
            if px < 0:
                px = area.right - dock.CARD_W - 14
                py = area.bottom - dock.CARD_H - 14
            x, y, w, h = dock.clamp_to_area(px, py, dock.CARD_W, dock.CARD_H, area)

        # ⚠️ 这四个都要赋值，一个都不能漏。
        #
        # 踩过的坑：只赋了 _ball_w/_ball_h，忘了 _ball_x/_ball_y。
        # 结果 _polish 里调用 _apply_ball_geometry 时读到的位置是初始值 (0,0)，
        # 于是把已经摆好的窗口又挪到了屏幕左上角 ——
        # 表现就是「一打开软件悬浮窗不见了」（其实贴在左上角，很不起眼）。
        #
        # 教训：**改用「自己记状态」这个模式时，要把所有读取点都找出来改成新变量。**
        # 漏一个，它就会读到初始值，而且不报错、只是行为莫名其妙。
        self._ball_w, self._ball_h = w, h
        self._ball_x, self._ball_y = x, y

        # 记一笔几何计算的输入输出。
        #
        # 这行是排查「窗口跑到屏幕外」时留下的 —— 那个问题出在我们和
        # pywebview 的单位换算接缝上，光看最终位置分不清是算错了还是
        # 对方没照做，只有把中间值摊开才看得清。
        # **只在创建时打一次**，悬停展开那种高频路径上不能打，否则日志会被刷爆。
        try:
            self.api.log_error(
                f"悬浮窗初始几何：scale={ui_scale()} "
                f"area=({area.left},{area.top},{area.right},{area.bottom}) "
                f"→ ({x},{y},{w},{h})"
            )
        except Exception:
            pass

        self.ball = webview.create_window(
            winutil.TITLE_BALL,
            str(resource_path("web", "ball.html")),
            js_api=self.api,
            width=w,
            height=h,
            x=x,
            y=y,
            # ⚠️ 这一行不能省。pywebview 的 min_size 默认是 (200, 100)，
            # 不放开的话窗口永远缩不到 200 以下 —— 「缩成一条边」根本做不到。
            # 实测就是这么发现的：resize(24, 72) 出来还是 200×100。
            min_size=(dock.TAB_SHORT, dock.TAB_SHORT),
            frameless=True,
            on_top=True,
            easy_drag=False,        # 拖动自己实现，见 ball.js（用 easy_drag 会让按钮点不动）
            background_color=_bg_color(self.store.theme),
        )
        self.api._ball_window = self.ball

        # 页面加载完再把布局推过去。
        # 创建窗口时页面还没加载，那时候 evaluate_js 会石沉大海。
        self.ball.events.loaded += self._push_ball_layout

        # ⚠️ 必须订阅 closing，否则**外部**（任务栏右键 → 关闭、Alt+F4）
        # 能把这个窗口真的销毁掉，之后再也唤不回来。
        # 详见 _on_ball_closing 的说明。
        self.ball.events.closing += self._on_ball_closing

        # ---- 窗口的「外观整形」----
        #
        # 这几步必须等窗口真正创建出来（HWND 存在）之后才能做，
        # 所以放在一个短延时线程里。
        #
        # 做三件事：
        #   1. 变成工具窗口 —— 不进任务栏。悬浮提示本来就不该占一个任务栏按钮。
        #   2. 关掉系统阴影 —— 用户反馈太深，而那个阴影只能开或关，改不了浓淡。
        #   3. 强制应用一次几何 —— create_window 传的尺寸不可靠，
        #      实测传 305×130 读回来是 290×92。调一次 resize() 才对得上。
        #
        # ⚠️ **顺序有讲究，不要随手调换。**
        #
        # 关阴影的副作用是把圆角也一起关掉了（原因见 winutil.disable_shadow
        # 的注释），所以「关阴影」和「要圆角」必须挨着做，而且圆角要放在后面。
        # 几何必须放在**最后** —— 因为 _apply_ball_geometry 会按「贴不贴边」
        # 再定一次悬浮窗的圆角，那才是最终该生效的那个值。
        # （第一版把几何放在最前面，圆角刚设好就被覆盖，白改。）
        def _polish() -> None:
            # ⚠️ 这里原来是 `time.sleep(0.9)` 硬等。
            #
            # 实测（见 tools/exp_toolwin.py）：窗口大约 **0.80 秒**才出现，
            # 固定睡 0.9 秒的余量**只有 0.1 秒**。冷启动时（WebView2 首次
            # 初始化、杀毒软件扫一遍）轻易就会超过它，于是按标题找窗口失败。
            #
            # 而失败是**静默**的：WS_EX_APPWINDOW 摘不掉，
            # **悬浮窗从此永久地留在任务栏上**（用户报的就是这个）。
            #
            # 换成轮询之后就没有这个窗口期了：窗口一出现就立刻返回，
            # 慢机器上多等一会儿也不会失败。
            ball_ready = bool(winutil.wait_for_window(winutil.TITLE_BALL, HWND_TIMEOUT))
            toast_ready = bool(winutil.wait_for_window(winutil.TITLE_TOAST, HWND_TIMEOUT))

            # 失败必须留下痕迹。原来是「找不到就当没这回事」，于是任务栏上
            # 多出来的按钮查无实据 —— 和 README 第 4.9 条（_apply_ball_geometry
            # 里那个 except: pass）是同一类错误：
            # **静默失败和直接崩掉一样难查，区别只是它不留下崩溃报告。**
            if not ball_ready:
                self.api.log_error(
                    f"等悬浮窗 HWND 超时（{HWND_TIMEOUT:.0f} 秒）："
                    f"WS_EX_TOOLWINDOW 没设上，它可能会出现在任务栏里"
                )
            if not toast_ready:
                self.api.log_error(
                    f"等提示条 HWND 超时（{HWND_TIMEOUT:.0f} 秒）：窗口样式没设上"
                )

            # ⚠️ 顺序有讲究，不要随手调换（见上面那段顺序说明）。
            if ball_ready:
                winutil.make_tool_window(winutil.TITLE_BALL)
                winutil.disable_shadow(winutil.TITLE_BALL)

            if toast_ready:
                winutil.make_tool_window(winutil.TITLE_TOAST)
                winutil.disable_shadow(winutil.TITLE_TOAST)
                # 提示条离屏幕边缘有 18px / 100px 的余量，永远不会贴边，
                # 所以它不需要 _apply_ball_geometry 那种「按位置决定」的逻辑
                winutil.set_rounded_corners(winutil.TITLE_TOAST, True)

            # 主题的强制档现在推一次；`boot()` 那边还会再推一次兜底
            # （这里可能赶在页面加载完成之前，那一次会被 window.__applyTheme
            # 还没定义而静默跳过 —— 所以不能只有这一处）。
            self._apply_theme()

            if not ball_ready:
                return

            # 强制重设一次 —— 上面的 disable_shadow 已经把圆角干掉了，
            # 把缓存清掉可以让 _ensure_ball_rounded 重新设一遍
            self._ball_rounded = False
            try:
                self._apply_ball_geometry()
            except Exception:
                pass

            # ---- 启动时要不要压住「自动弹出」 ----
            #
            # 背景：贴边停靠的悬浮窗启动时是「收起的小方框」，鼠标移上去
            # 应该自动展开成卡片（由 _ball_hover 的 entered 分支负责）。
            #
            # 而 `_ball_suppress_expand` 是给**拖动停靠**那一下用的：
            # 松手时光标就贴在屏幕边上，不压住的话窗口刚缩进去就会立刻弹回来。
            # 这个标志**只在一处解除** —— _ball_hover 的「鼠标离开」分支。
            #
            # ⚠️ 原来的写法是在 _create_ball 里无条件设成 True
            # （注释写「刚启动先别弹，等鼠标靠近再说」），但那句注释是错的：
            # **压根不会有人来解除它。** 启动时光标通常离悬浮窗很远，
            # 页面既不会发 mouseenter 也不会发 mouseleave，于是这个标志
            # 一直挂着，把**用户的第一次悬停整个吃掉** ——
            # 表现就是「鼠标移上去没反应，点一下才弹出来」，
            # 而点一下之所以管用，是因为点击会把它当成拖动起点，
            # 走 _ball_drag_start 那条路把这个标志清掉了。
            #
            # 正确做法：**只在光标真的压在窗口上时才压住。**
            # 压住的理由本来就是这个 —— 光标已经在窗口里了，
            # 再让页面补发的 enter 把它弹出来，等于用户什么都没做它就自己弹了。
            # 光标在别处的话，用户的第一次悬停就是一个**真实的进入动作**，
            # 必须响应。
            #
            # 时机也有讲究：必须在窗口摆好位置之后再判断，
            # 否则读到的矩形还是创建时那个不准的（见上面 resize 的注释）。
            self._ball_suppress_expand = self._cursor_over_ball()

        threading.Thread(target=_polish, daemon=True).start()

    # ---------------- 悬浮窗：停靠 / 展开 / 收起 ----------------

    def _apply_ball_geometry(self, animate: bool = False) -> None:
        """
        把当前状态（停靠方向、收起与否）真正作用到窗口上。

        [animate] True 时，若悬浮窗正贴边停靠，则**逐步**过渡到目标形态
        （见 `_animate_ball`）；否则立刻落位。默认不动画，保持原来的行为。

        为什么默认 False 而不是 True：这个方法有 6 个调用点，
        其中「启动时摆好位置」那处**不能**有动画（一打开就看到窗口在动很奇怪），
        另外几处是给窗口设尺寸的辅助步骤。让动画成为**显式选择**，
        比让每个调用点去关掉它更不容易出错。
        """
        if self.ball is None:
            return

        # ⚠️ 悬浮窗在设置里是关闭的 → 一律不许动它的几何。
        #
        # 为什么这条不能省：**pywebview 的 move() 和 resize() 会把窗口显示出来。**
        # 它们内部走的是 SetWindowPos，而传进去的 flag 里有 SWP_SHOWWINDOW
        # （见 webview/platforms/winforms.py 的 BrowserView.move / resize，
        # resize 那处甚至直接把裸数字 64 = 0x40 当 flag 传）。
        #
        # 于是「hide() 之后又有人调了一次几何」= 窗口自己冒回来。
        # 用户看到的现象是：点「关闭」→ 窗口闪一下没关掉 → 再点没反应
        # （按钮已经把自己禁用了，见 ball.js 的 closing）。重启后它又是关着的，
        # 因为配置确实写对了 —— 只有窗口没真的藏住。
        #
        # 具体的触发者是鼠标：窗口在光标底下消失会让 WebView2 发 mouseleave，
        # 380ms 后前端回调 _ball_hover(false)，那边收起卡片时会调到这里
        # （见 ball.js 的 scheduleCollapse 和下面 _ball_hover 的 else 分支）。
        #
        # 修在这里而不是修在那一个调用点上：这是一次性的收口，
        # 任何路径想让已关闭的悬浮窗「动一下」，都会在这里被挡住。
        if not self.store.ball_enabled:
            return

        # 让所有在跑的动画作废。下面要么起一个新动画、要么直接落到最终位置，
        # 两种情况下旧动画接着跑都是错的 —— 它会把窗口拉回中途的尺寸。
        self._ball_anim_token += 1

        # 贴边停靠 + 要求动画 → 交给动画线程逐步走
        #
        # 自由浮动（_ball_edge is None）没有"收起"这回事，不适用动画，
        # 直接落到下面即时处理的路径。
        if animate and self._ball_edge is not None:
            self._animate_ball(1.0 if self._ball_collapsed else 0.0)
            return

        area = work_area()

        if self._ball_edge is None:
            x, y, w, h = dock.clamp_to_area(
                self._ball_x, self._ball_y, dock.CARD_W, dock.CARD_H, area
            )
        else:
            x, y, w, h = dock.docked_geometry(
                self._ball_edge, self._ball_center, area, self._ball_collapsed
            )

        self._ball_w, self._ball_h = w, h
        self._ball_x, self._ball_y = x, y

        # 直接落位 = 进度就是两端之一。
        # 记下来是为了让**中途被打断的动画知道从哪儿接着走** ——
        # 比如用户在收起的半路上又把鼠标移回来，那次展开必须从
        # 当前这个中间尺寸开始，而不是先跳回小方框再长大。
        self._ball_progress = 1.0 if self._ball_collapsed else 0.0

        # 这里**不能**打日志 —— 悬停展开/收起都会走到这里，
        # 每条都记的话日志会被瞬间刷爆。只在创建窗口时记一次就够了。

        # 圆角要在 resize/move **之前**定好：窗口先变成正确的形状再摆过去，
        # 中间不会闪一下「方形卡片出现在新位置」。
        self._ensure_ball_rounded()

        try:
            self.ball.resize(w, h)
            self.ball.move(x, y)
        except Exception as e:
            # 改窗口尺寸失败不该让程序崩 —— 顶多是这次动画没生效。
            #
            # 但**必须记下来**。第一版这里是 `except: pass`，
            # 结果「拖到右边不缩回去」查了半天毫无线索 ——
            # 异常被自己吞了，日志里什么都没有。
            # 吞异常和静默失败是同一类错误，代价是排查时间。
            try:
                self.api.log_error(
                    f"悬浮窗几何应用失败 {w}x{h}@({x},{y})：{type(e).__name__}: {e}"
                )
            except Exception:
                pass
        self._push_ball_layout()

    def _ensure_ball_rounded(self) -> None:
        """
        保证悬浮窗是圆角的。

        ## 为什么需要主动设一次

        因为 Win11 虽然**默认**就给无边框窗口加圆角，但我们在 `_polish`
        里调的 `disable_shadow()` 会把整个非客户区渲染关掉，圆角跟着一起没。
        所以必须显式再要求一次。详见 winutil.disable_shadow 的注释。

        ## 为什么四个角都圆，而不是贴边那侧留直角

        一开始试过「贴着屏幕的那一侧保持直角」—— 理由是圆角会把窗口的角
        裁掉，贴边时裁掉的正好是屏幕边缘那一侧，屏幕上会露出两个小豁口。

        实际用下来不需要这么讲究：**四个角都圆就行。** 于是整套「按边裁
        形状区域」的代码都拆掉了，换来两件事 —— 圆角边缘有抗锯齿
        （区域硬裁是没有的），以及不用在每次改尺寸时重设区域。

        ## 为什么缓存

        拖动悬浮窗时这个函数每秒会被调用几十次，而值一旦设上就不会再变。
        缓存住，避免几十次多余的跨进程系统调用。
        """
        if self._ball_rounded:
            return
        self._ball_rounded = True
        winutil.set_rounded_corners(winutil.TITLE_BALL, True)

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

    def _animate_ball(self, target: float) -> None:
        """
        把悬浮窗从**当前进度**动画到 [target]（0.0 = 卡片，1.0 = 小方框）。

        跑在一个 daemon 线程里：整个过程要 160ms，不能占着调用它的那条线程
        （那些是 pywebview 的桥接回调线程，占住它们会把界面卡住）。

        ## 三个必须做对的地方

        1. **每帧都要检查作废令牌和 `ball_enabled`。**
           前者防两个动画同时改窗口；后者是踩过的坑 ——
           pywebview 的 `resize()`/`move()` 会把窗口显示出来（详见
           `_apply_ball_geometry` 里的注释），所以一个还在跑的动画线程
           足以把**刚被隐藏的悬浮窗又弄回来**。

        2. **最后一帧要用精确值收口**，而不是"循环跑完就完事"。
           逐帧插值会累积浮点误差，收口时调用一次即时路径，
           保证最终尺寸就是 `dock.docked_geometry` 算出来的那个整数，
           一个像素都不差。

        3. **中途被打断就从当前进度接着走。** 起点取 `self._ball_progress`
           而不是 0 或 1，这样"鼠标移出去又马上移回来"不会让窗口
           先跳回小方框再重新长大。
        """
        if self.ball is None or self._ball_edge is None:
            return

        self._ball_anim_token += 1
        token = self._ball_anim_token

        start = self._ball_progress
        edge = self._ball_edge
        center = self._ball_center

        # 目标方向：1.0 表示正在收起，0.0 表示正在展开。
        # 网页那边靠它决定是"淡出卡片内容"还是"淡入"。
        collapsing = target > start

        # 先把网页切到「动画中」的样子：强制卡片布局（面板必须一直可见，
        # 否则一开始就没东西可淡出），并按方向设置淡入/淡出。
        self._eval_js(
            self.ball,
            f"window.ballAnim && window.ballAnim('{edge}', "
            f"{'true' if collapsing else 'false'})",
        )

        def _run() -> None:
            total_s = BALL_ANIM_MS / 1000.0
            t0 = time.perf_counter()

            for i in range(1, BALL_ANIM_STEPS + 1):
                # 见上面第 1 条：令牌或开关一变，立刻收手
                if token != self._ball_anim_token or not self.store.ball_enabled:
                    return

                t = _ease_out(i / BALL_ANIM_STEPS)
                progress = start + (target - start) * t

                try:
                    x, y, w, h = dock.tween_docked_geometry(
                        edge, center, work_area(), progress
                    )
                except Exception:
                    return

                self._ball_progress = progress
                self._ball_w, self._ball_h = w, h
                self._ball_x, self._ball_y = x, y

                try:
                    self.ball.resize(w, h)
                    self.ball.move(x, y)
                except Exception:
                    # 窗口没了（正在关闭）—— 安静退出，不要把异常抛进线程
                    return

                # 按**真实时间轴**对齐，而不是每帧固定睡 10ms。
                #
                # 每帧的 resize+move 本身要花 1–3ms（跨进程调用，实测），
                # 固定睡 10ms 的话一帧实际是 11–13ms，16 帧下来
                # 总时长会从 160ms 涨到将近 200ms —— 而网页那边的淡出
                # 是按 160ms 走的（CSS 过渡），两边就对不上了。
                #
                # 所以这里算「离这一帧该出现的时间点还差多少」，只睡差额。
                # 机器快就睡得多、慢就睡得少，总时长始终是 BALL_ANIM_MS。
                due = t0 + total_s * (i / BALL_ANIM_STEPS)
                slack = due - time.perf_counter()
                if slack > 0:
                    time.sleep(slack)

            # 见上面第 2 条：用精确值收口
            if token == self._ball_anim_token and self.store.ball_enabled:
                self._apply_ball_geometry()

        threading.Thread(target=_run, daemon=True, name="ball-anim").start()

    def _push_ball_layout(self) -> None:
        """告诉网页该显示「展开的卡片」还是「收起的小方框」"""
        edge = self._ball_edge or ""
        collapsed = "true" if (self._ball_edge and self._ball_collapsed) else "false"
        self._eval_js(self.ball, f"applyDock('{edge}', {collapsed})")

    def _reset_ball_close_button(self) -> None:
        """
        把悬浮窗上的「关闭」按钮恢复成可用。

        ## 为什么每次 show() 之后都要调

        悬浮窗是**长期存在**的窗口，关掉只是 hide()，窗口对象还在。
        而「关闭」按钮点过一次后会禁用自己（防连点，见 ball.js 的 closing）。

        于是一旦用户在设置里关掉再打开，按钮还停在禁用状态 ——
        表现是「悬浮窗关不掉了」，而且**没有任何报错，只是点了没反应**。
        这种"静默失效"最难查，所以在这里显式恢复一次。

        调用点是所有会让悬浮窗重新出现的地方，漏一个就会留下
        「某条路径打开后按钮是死的」这种间歇性问题。
        """
        if self.ball is None:
            return
        self._eval_js(self.ball, "window.ballShown && window.ballShown()")

    def _on_ball_closing(self) -> bool:
        """
        有人想关掉悬浮窗窗口（任务栏右键 → 关闭、Alt+F4……）。

        ## 返回值的含义（pywebview 这里很容易看反）

            True  → 允许关闭
            False → **取消**关闭

        内部实现是 `should_cancel = closing.set()`，而 `Event.set()` 在
        **没有订阅者**的时候返回 False —— 也就是说，**没订阅 closing 的
        窗口会被直接关掉**。悬浮窗原来就没订阅，所以在任务栏上不小心点了
        关闭，是真能把它销毁的。

        ## 为什么必须拦下来

        程序的设计是「关闭 = 隐藏」（见 `api.hide_ball`）。而窗口一旦被真的
        销毁，`App.ball` 这个引用还指着它（**不是 None**），会连锁出三件事：

          · `_toggle_ball` / `_on_settings_changed` 都只在 `ball is None` 时重建
          · 于是每个「打开悬浮窗」的入口都去调 `self.ball.show()`
          · 在已销毁的 Form 上调用会抛异常，而调用点全是 `except: pass`

        结果就是**重启之前无论如何都唤不回来** —— 用户报的第二个现象。

        ## 为什么交给 hide_ball，而不是在这里自己写一遍

        外部关闭和点悬浮窗上那个「关闭」按钮，**语义上是同一件事**，
        所以直接复用那条已经测过的路径（关设置 + 隐藏 + 刷新各处开关）。
        另写一段「差不多的」逻辑，只会多一处需要同步维护的地方。

        ## ⚠️ 退出流程必须放行

        `_shutdown()` 靠 `w.destroy()` 收场，而 pywebview 的
        `destroy_window()` 实现就是 `i.Close()` —— **会再触发一次
        FormClosing**。这里要是无条件取消，程序就**永远退不掉**了。

        所以和 `_on_main_closing` 一样，认 `_shutting_down` 这个标志放行。
        """
        if self._shutting_down:
            return True

        try:
            self.api.hide_ball()
        except Exception as e:
            self.api.log_error(f"拦截悬浮窗关闭时出错：{type(e).__name__}: {e}")

        # 取消这次关闭：按设计它只该「隐藏」，窗口本身要留着。
        return False

    def _ball_alive(self) -> bool:
        """
        悬浮窗窗口是不是**真的还在**。

        不能只看 `self.ball is not None` —— 窗口可能已经被外部销毁
        （任务栏关闭、Alt+F4、系统强制回收），而 Python 这边的引用还指着它。

        所以去问系统：按标题还能不能找到这个窗口。
        **拿窗口的真实状态，而不是我们记忆里的状态** —— 这也正是
        `winutil.window_rect` 存在的意义（那份记忆值不可靠，
        见它和 `_apply_ball_geometry` 里的说明）。
        """
        if self.ball is None:
            return False
        try:
            return winutil.window_rect(winutil.TITLE_BALL) is not None
        except Exception:
            return False

    def _ensure_ball(self) -> bool:
        """
        确保有一个**可用的**悬浮窗窗口，必要时重建它。

        这是「无论如何都唤不回来」的兜底：即使窗口被某种没拦住的方式
        （异常、系统强制销毁、竞态）干掉了，下次要显示时也能重新建出来，
        而不是对着一个死引用反复调 `show()` 然后静默失败。

        ⚠️ 重建前必须把 `self.ball` 置成 None：`_create_ball` 第一句就是
        `if self.ball is not None: return`，不清掉的话它会直接返回，
        等于什么都没重建 —— 而且**不报错**。
        """
        if self._ball_alive():
            return True

        if self.ball is not None:
            self.api.log_error("悬浮窗窗口已失效（被外部关掉了？），重新创建")

        self.ball = None
        try:
            sw, sh = screen_size()
            self._create_ball(sw, sh)
        except Exception as e:
            self.api.log_error(f"重建悬浮窗失败：{type(e).__name__}: {e}")
            return False

        return self.ball is not None

    def _ball_drag_end(self) -> dict:
        """
        用户把悬浮窗拖完松手了。

        判断它有没有被拖到边缘附近：是的话就停靠（并立刻缩起来），
        不是的话就自由浮动。整套几何计算在 dock.py 里，那边有测试。
        """
        if self.ball is None:
            return {"ok": False}

        x, y = self._ball_x, self._ball_y

        area = work_area()
        edge = dock.compute_dock(x, y, self._ball_w, self._ball_h, area)

        if edge:
            self._ball_edge = edge
            self._ball_center = dock.center_of(edge, x, y, self._ball_w, self._ball_h)
            self._ball_collapsed = True
            # 松手时光标就贴在屏幕边上，压住自动弹出 ——
            # 否则刚缩进去就会因为光标还悬在标签上而立刻弹回来，
            # 看起来像什么都没发生。
            #
            # 压不压同样是**看光标的实际位置**（见 _polish 里那段长注释）：
            # 光标在窗口里才压。这样「光标停在别处、用户主动移上去」
            # 这种真实的进入动作不会被误伤。
            self._ball_suppress_expand = self._cursor_over_ball()
        else:
            self._ball_edge = None
            self._ball_collapsed = False
            self.store.ball_pos = (x, y)

        # 这里就是要动画的那一下：拖到边缘松手 → 卡片顺滑地收成小方框。
        # 自由浮动时 animate 会被 _apply_ball_geometry 忽略（那边没有"收起"），
        # 所以这一个调用点同时覆盖了两种情况，不需要在这里分支。
        self._apply_ball_geometry(animate=True)
        self.store.ball_edge = self._ball_edge or ""
        self.store.ball_center = self._ball_center
        return {"ok": True, "edge": self._ball_edge}

    def _cursor_over_ball(self) -> bool:
        """光标此刻是不是压在悬浮窗上（用窗口的真实矩形判断）。"""
        try:
            return winutil.cursor_in_window(winutil.TITLE_BALL)
        except Exception:
            return False

    def _ball_hover(self, entered: bool) -> dict:
        """鼠标进出悬浮窗。停靠着的时候，进就弹出来，出就缩回去。"""
        if self.ball is None or self._ball_edge is None:
            return {"ok": True}

        # 悬浮窗已经关掉了 → 鼠标进出跟它没关系，连收起状态都不要改。
        #
        # 为什么连状态也要一起挡住：窗口刚被藏起来的那一刻，光标正好在它上面，
        # 于是 WebView2 会补发一次 mouseleave，前端 380ms 后回调到这里。
        # 如果只挡 _apply_ball_geometry 不挡这里，`_ball_collapsed` 会偷偷翻成
        # True 而几何没跟着变 —— 等用户在设置里把悬浮窗重新打开，
        # 就会看到一个 244×104 的窗口里画着那张「收起的小方框」，
        # 状态和实际尺寸对不上。
        #
        # 一句话：**关掉之后，这个东西就当不存在。**
        if not self.store.ball_enabled:
            return {"ok": True}

        if entered:
            if self._ball_suppress_expand:
                # 刚停靠或刚启动，先不动 —— 等鼠标真正离开一次再说
                return {"ok": True}
            if self._ball_collapsed:
                self._ball_collapsed = False
                self._apply_ball_geometry(animate=True)
        else:
            # 鼠标离开了，解除压制：下次靠近就应该弹出来
            self._ball_suppress_expand = False
            if not self._ball_collapsed:
                self._ball_collapsed = True
                self._apply_ball_geometry(animate=True)

        return {"ok": True}

    def _ball_slide_out(self) -> None:
        """手动把收起的悬浮窗拉出来（设置里切换开关时用）"""
        if self.ball is None or self._ball_edge is None:
            return
        self._ball_collapsed = False
        self._ball_suppress_expand = False
        self._apply_ball_geometry(animate=True)

    def _toggle_ball(self) -> None:
        """
        托盘菜单里的「显示悬浮窗」开关。

        注意这里同时处理四种情况：
          · 悬浮窗关着（配置层面）→ 打开并创建它
          · 悬浮窗开着但窗口还没建出来 → 建出来
          · 悬浮窗开着、但窗口**已经被销毁**（被外部关过）→ 重建
          · 悬浮窗开着且已存在 → 切换显示/隐藏
        """
        want = not self.store.ball_enabled
        self.store.ball_enabled = want

        if want:
            # 交给 _ensure_ball：它会把「窗口已失效」这种情况一起处理掉。
            # 直接对着一个失效的引用调 show() 会抛异常，而下面那个 try
            # 会把它吞干净 —— 表现就是「点了没反应」（用户报过这个）。
            if not self._ensure_ball():
                return
            try:
                self.ball.show()
                self._ball_suppress_expand = False
                if self._ball_edge:
                    self._ball_collapsed = False
                    self._apply_ball_geometry()
                self._push_ball_layout()
                self._reset_ball_close_button()
            except Exception:
                pass
        else:
            try:
                if self.ball is not None:
                    self.ball.hide()
            except Exception:
                pass

        # 界面上的开关要跟着变
        self._eval_js(self.main, "window.refreshAll && window.refreshAll()")

    def _ball_move(self, dx: float, dy: float) -> dict:
        """
        拖动中：挪窗口，但**不让它跑出屏幕**。

        用户反馈「拖到右边之后还能继续往右拖」—— 拖出去就看不见了，
        只剩任务栏里一个按钮，很容易以为程序坏了。

        所以这里把目标位置夹在工作区内：**窗口的四条边永远不越界**。
        贴边的时候正好停在边缘上（这也是我们想要的停靠位置），
        但再也推不出去了。
        """
        if self.ball is None:
            return {"ok": False}

        area = work_area()
        x, y = self._ball_x, self._ball_y

        nx, ny, _, _ = dock.clamp_to_area(
            x + dx, y + dy, self._ball_w, self._ball_h, area
        )

        # 位置没变就别调 move 了 —— 拖动时这个函数每秒被调几十次，
        # 每次都是一次跨进程调用，能省则省
        if nx == x and ny == y:
            return {"ok": True}

        self._ball_x, self._ball_y = nx, ny
        try:
            self.ball.move(nx, ny)
        except Exception:
            return {"ok": False}
        return {"ok": True}

    def _ball_drag_start(self) -> None:
        """
        开始拖动悬浮窗。

        如果它正处于「贴边收起」状态，**先弹出来再拖**。

        为什么？收起时窗口只有 26×76，是一个几乎看不见的小条。
        用户按住的其实是那个小条，拖起来手感很怪 ——
        而且拖到中间松手之前，他一直看到的是一条细线，
        很容易以为「拖没了」。
        （用户反馈原话：「拖到边缘再拖出去后就自己消失了」。）

        先展开再拖，就一直是那张完整的卡片在跟着手走。
        """
        if self.ball is None:
            return
        if self._ball_edge and self._ball_collapsed:
            self._ball_collapsed = False
            self._ball_suppress_expand = False
            self._apply_ball_geometry()

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
    # 单实例检查用不着 —— 同时开两个也不会互相破坏数据，
    # 顶多是两个悬浮窗。为这点小事引入文件锁不值得。
    App().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
