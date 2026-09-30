"""
悬浮窗的窗口控制 —— 感知在 JS（ball.js），判断和动画在这里。

## 它管什么、不管什么

    管    悬浮窗这个 Win32 窗口的生死与几何：创建、外观整形（工具窗口 /
          去阴影 / 圆角）、贴边停靠的滑出滑入动画、拖动落位、显示/隐藏。
    不管  悬浮窗**页面里**的事（卡片布局、按钮、悬停感知）—— 那些在
          ball.html / ball.js，两边靠 applyDock 和 _on_ball_* 回调通话。

## 为什么从 main.py 搬出来

main.py 原本 1600 行，窗口生命周期、悬浮球动画、托盘、提醒挤在一起。
reminders.py / screen.py 的拆分已经证明「纯逻辑下沉成独立模块就能单测」，
悬浮窗这坨是最后一块大的：搬走之后 main.py 只剩「三个窗口的编排」，
这边只剩「悬浮窗这一个窗口的一切」—— 每个文件的职责都能一句话说完。

## 形态：Mixin，而不是独立类

悬浮窗的控制和 App 的其余部分共享大量状态（self.store、self.api、
self.main、退出标志 _shutting_down），拆成独立对象就得把这些挨个倒手，
接缝比收益大。所以用 Mixin：`class App(BallMixin)` —— 方法还是 App 的
方法，测试里 `app._ball_hover(...)` 这些调用一行不用改，只是**代码的
住处**分开了。

（tests/test_ball_ui.py 用正则钉着这个文件里的几处关键写法；
tools/verify_anim_guards.py 的 M5 变异的也是这里。动 `_polish` 的
等待方式、closing 订阅之前，先看那几条护栏。）
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import webview

from . import dock, winutil
from .screen import (
    _bg_color,
    monitor_rect,
    resource_path,
    screen_size,
    ui_scale,
    work_area,
)

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


def place_window(win, x: int, y: int, w: int, h: int,
                 prev_x: int, prev_y: int, prev_w: int, prev_h: int,
                 atomic=None) -> None:
    """
    把窗口挪到 (x, y) 并改成 w × h。**先挪还是先缩，取决于这一帧是长大还是缩小。**

    ## 悬浮窗已改为固定尺寸，这里对它只剩 move

    悬浮窗窗口现在**从不 resize**（收起/展开 = 整窗平移，见 dock.py 开头的
    说明），所以走这条路径时 w/h 恒等于上一帧，顺序规则自然不会触发。
    函数保持原样：它还要服务别的调用形态，而 resize 路径留着也是兜底 ——
    万一未来有谁恢复「改变尺寸」的做法，这里的坑位说明还在。

    ## 为什么顺序要紧

    `move()` 和 `resize()` 是两次独立的跨线程调用，中间窗口会经过一个
    「位置和尺寸不配套」的中间态。这个中间态一旦**超出新旧两个矩形的并集**，
    多出来的那块就是一片没画过的窗口区域 —— 它的颜色和卡片没关系。

    深色主题下那块和卡片同色，看不出来；**浅色主题下就是卡片边上一条黑边**。
    用户报的现象是「从收缩状态弹出的过程中有黑色拖尾，原来黑色的时候看不出来」——
    正是这个。

    判据就一句话：**挑那个中间态落在「新旧矩形的并集」里的顺序。**
    并集里的像素是刚刚画过的，露出来也看不出来。

        先 resize 再 move  出问题 ⟺ 某一轴「在长大，而位置在朝负方向挪」
                              （中间态的右/下边缘会临时外溢）
        先 move 再 resize  出问题 ⟺ 某一轴「在缩小，而位置在朝正方向挪」
                              （中间态的右/下边缘会临时外溢）

    对悬浮窗来说，贴边收起/展开时**各个轴是同步变大或同步变小的**，
    于是退化成好记的两条：

        长大   先 move 再 resize
        缩小   先 resize 再 move

    ⚠️ 别图省事改成固定顺序 —— 那只是把问题从一个方向挪到另一个方向：
    展开修好了，收起又会露。也别只看「长大还是缩小」——一轴变大、
    一轴变小时那个判断是错的，所以这里逐轴检查。

    ## 为什么不用一次 SetWindowPos 把两件事合成

    那确实更干净也更快，但要绕过 `win`（pywebview 的窗口对象）直接去拿 HWND。
    而 `tests/test_ball_ui.py` 的 FakeWindow 正是靠记录 `move`/`resize` 来模拟
    「pywebview 这两个调用会把窗口显示出来」那个副作用 —— 那是几条护栏的
    探针。绕过去它们就变成空断言了。等那套测试改成盯 Win32 调用，再合成不迟。
    """
    # 首选：一次 SetWindowPos 合成到位（见 winutil.set_window_rect 的说明 ——
    # 只有这样才能让新长出来的那块显示**窗口底色**而不是桌面）。
    #
    # `atomic` 由调用方注入，测试里不传 —— 那样就不会去 FindWindowW 碰真实
    # 窗口（按标题找是全机器的，两个实例并存会串到别人的窗口上，踩过）。
    if atomic is not None and atomic(x, y, w, h):
        return

    # 退路：拿不到 HWND 时（测试、窗口刚销毁）走两次调用。
    # 下面这套顺序规则只是让中间态**别超出新旧矩形的并集**，
    # 治不了「新长出来的区域本来就没被画过」那一类问题。
    resize_first_bad = (w > prev_w and x < prev_x) or (h > prev_h and y < prev_y)
    move_first_bad = (prev_w > w and x > prev_x) or (prev_h > h and y > prev_y)

    if resize_first_bad and not move_first_bad:
        win.move(x, y)
        win.resize(w, h)
    else:
        # 两种都安全时也走这条（和改动前的行为一致）；
        # 极端情况下两种都不安全（一轴长大且左移、另一轴缩小且右移）也走这条。
        win.resize(w, h)
        win.move(x, y)


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


class BallMixin:
    """
    悬浮窗的窗口控制（见模块文档）。由 main.App 多继承混入。

    ⚠️ 这里的方法都依赖宿主（App）提供的属性：
       self.store / self.api / self.ball / self.main / self._shutting_down
       以及 main.py 里的 _eval_js。悬浮窗自己的状态在 _init_ball_state。
    """

    def _init_ball_state(self) -> None:
        """悬浮窗的停靠/动画状态。App.__init__ 里调用，集中一处好对账。"""
        # ---- 悬浮窗的停靠状态 ----
        # 这几项是「窗口该在哪」的唯一真相来源。
        # 为什么不直接读窗口的 x/y/w/h？因为实测发现 pywebview 报回来的
        # width/height 和实际设置的值对不上（疑似 DPI 缩放或客户区口径不同）。
        # 位置 x/y 是准的，尺寸就自己记着，免得反复展开收起时越算越偏。
        self._ball_edge: Optional[str] = None      # None = 自由浮动
        self._ball_center: int = 0                 # 贴边时那条要保住的中线
        self._ball_collapsed: bool = False
        # 窗口尺寸是**恒定**的（dock.WINDOW_W × WINDOW_H，永不 resize ——
        # 这是「整窗滑出」方案的前提，见 dock.py 开头的说明）。
        # 记在这里只是为了统一口径：所有几何调用都从这对状态拿尺寸。
        self._ball_w: int = dock.WINDOW_W
        self._ball_h: int = dock.WINDOW_H
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

        #: 悬浮窗的窗口句柄，只在 `_polish` 里拿到过一次。
        #: 落位用它而不是按标题找 —— 理由见 `_set_ball_rect`。
        #: 0 = 还没拿到，那时落位会自动退回两次调用的做法。
        self._ball_hwnd = 0

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

    # ---------------- 创建窗口 ----------------

    def _create_ball(self, sw: int, sh: int) -> None:
        if self.ball is not None:
            return

        # ---- 算出它该出现在哪 ----
        # 上次的停靠状态优先。用户在右边缩起来的卡片，下次打开应该还在那，
        # 每次都回到默认位置会让人反复调整，很烦。
        area = work_area()
        monitor = monitor_rect()
        saved_edge = self.store.ball_edge

        if saved_edge:
            self._ball_edge = saved_edge
            self._ball_center = self.store.ball_center
            self._ball_collapsed = True
            # 这里**不能**直接压住自动弹出 —— 见 _polish 里那段的说明。
            # 压不压取决于「启动时光标是不是恰好压在悬浮窗上」，
            # 而那要等窗口真正摆好位置才知道，所以判断挪到 _polish 里。
            self._ball_suppress_expand = False
            # 收起态：大部分窗口在屏幕外，只留把手那条（dock.slide_geometry）
            x, y, w, h = dock.slide_geometry(
                saved_edge, self._ball_center, area, monitor, True
            )
        else:
            self._ball_edge = None
            self._ball_collapsed = False
            px, py = self.store.ball_pos
            if px < 0:
                px = area.right - dock.WINDOW_W - 14
                py = area.bottom - dock.WINDOW_H - 14
            x, y = dock.clamp_free(px, py, area)
            w, h = dock.WINDOW_W, dock.WINDOW_H

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
            # 不放开的话窗口建不到 200 以下。现在窗口尺寸固定不会再 resize，
            # 这条只防 pywebview 内部逻辑拿默认值捣乱，留着无害。
            min_size=(dock.SLIDE_W, dock.SLIDE_W),
            frameless=True,
            on_top=True,
            easy_drag=False,        # 拖动自己实现，见 ball.js（用 easy_drag 会让按钮点不动）
            background_color=_bg_color(self.store.theme),
        )
        self.api._ball_window = self.ball

        # 页面加载完再把布局推过去。
        # 创建窗口时页面还没加载，那时候 evaluate_js 会石沉大海。
        self.ball.events.loaded += self._push_ball_layout
        # 主题同理：polish 里那一次可能赶在页面加载完成之前（竞态说明见
        # main.build() 里 main/toast 的订阅处），这里在页面加载完再补推一次。
        self.ball.events.loaded += self._apply_theme
        self.ball.events.loaded += self._push_skin

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
            # 悬浮窗专属的外观整形。主窗口标题栏、提示条样式、主题推送
            # 在 main.py 的 _polish_chrome 里（与悬浮窗无关，关着也要做）。
            #
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
            #
            # ⚠️ **必须先等窗口真正建出来**。`create_window()` 只是登记一下，
            # 真正的 WinForms 窗口要等 `webview.start()` 才出现；而这个线程
            # 在 `_create_ball` 末尾就起了，那一刻一个窗口都还没有。
            #
            # 下面两句是故意写成两句的：
            #   第一句拿 HWND（落位要用，见 _set_ball_rect）；
            #   第二句**那一行的写法被 tests/test_ball_ui.py 用正则钉住**
            #   （必须长成 `ball_ready = bool(winutil.wait_for_window(`），
            #   改成 `bool(hwnd)` 会让那条「必须轮询、不许睡固定时长」的护栏
            #   当场变红。窗口这时一定在，第二次是即时返回，代价可以忽略。
            self._ball_hwnd = winutil.wait_for_window(winutil.TITLE_BALL, HWND_TIMEOUT)
            ball_ready = bool(winutil.wait_for_window(winutil.TITLE_BALL, HWND_TIMEOUT))

            # 失败必须留下痕迹。原来是「找不到就当没这回事」，于是任务栏上
            # 多出来的按钮查无实据 —— 和 README 第 4.9 条（_apply_ball_geometry
            # 里那个 except: pass）是同一类错误：
            # **静默失败和直接崩掉一样难查，区别只是它不留下崩溃报告。**
            if not ball_ready:
                self.api.log_error(
                    f"等悬浮窗 HWND 超时（{HWND_TIMEOUT:.0f} 秒）："
                    f"WS_EX_TOOLWINDOW 没设上，它可能会出现在任务栏里"
                )

            # ⚠️ 顺序有讲究，不要随手调换（见上面那段顺序说明）。
            if ball_ready:
                winutil.make_tool_window(winutil.TITLE_BALL)
                winutil.disable_shadow(winutil.TITLE_BALL)

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
        monitor = monitor_rect()

        if self._ball_edge is None:
            x, y = dock.clamp_free(self._ball_x, self._ball_y, area)
            w, h = dock.WINDOW_W, dock.WINDOW_H
        else:
            x, y, w, h = dock.slide_geometry(
                self._ball_edge, self._ball_center, area, monitor,
                self._ball_collapsed
            )

        # 先把「改之前的尺寸」留在手里 —— place_window 要靠它判断这一帧
        # 是长大还是缩小（顺序不同，见那边的说明）。别在它之前覆盖掉。
        prev_x, prev_y, prev_w, prev_h = (
            self._ball_x, self._ball_y, self._ball_w, self._ball_h)
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
            place_window(self.ball, x, y, w, h, prev_x, prev_y, prev_w, prev_h,
                         atomic=self._set_ball_rect)
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
        # 关阴影和要圆角**必须成对、且顺序固定**：disable_shadow 会把圆角
        # 一起干掉（见 winutil.disable_shadow 的说明），所以先关阴影、再要圆角。
        # 这两句以前分在两个地方（_polish 里关阴影、这里要圆角），
        # 各自看着都对，凑一起就没人保证顺序了 —— 收在一处。
        #
        if self._ball_rounded:
            return
        self._ball_rounded = True
        # 顺序不能反（理由见上），两件都要做，做完就缓存住。
        #
        # ⚠️ 别改成「每次都重调」。这个函数在拖动时每秒会被调几十次，
        # 不缓存就是每秒几十次 FindWindowW + 2 次 DWM + 2 次窗口样式读写。
        # 我试过改成不缓存（想着「阴影被还原了就再关一次」），代价立刻显形：
        # 后台线程的 _find 调用串进了 tests/test_ball_ui.py 打的桩里，
        # 让一条毫不相干的测试从 3 次变成 4 次。**测试串味往往是性能问题的信号**。
        winutil.disable_shadow(winutil.TITLE_BALL)
        winutil.set_rounded_corners(winutil.TITLE_BALL, True)

    def _animate_ball(self, target: float) -> None:
        """
        把悬浮窗从**当前进度**动画到 [target]（0.0 = 展开，1.0 = 收起）。

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
           保证最终位置就是 `dock.slide_geometry` 算出来的那个整数，
           一个像素都不差。

        3. **中途被打断就从当前进度接着走。** 起点取 `self._ball_progress`
           而不是 0 或 1，这样"鼠标移出去又马上移回来"不会让窗口
           先跳回把手条再重新滑出来。
        """
        if self.ball is None or self._ball_edge is None:
            return

        self._ball_anim_token += 1
        token = self._ball_anim_token

        start = self._ball_progress
        edge = self._ball_edge
        center = self._ball_center

        # 动画只有 160ms，屏幕基准一次算好就够了（每帧去问系统白费 syscall）
        area = work_area()
        monitor = monitor_rect()

        # 一开始就把网页切到**目标**形态。
        #
        # 旧版这里还会往页面注入一个「动画中」的 JS 钩子：动画期间强制卡片
        # 布局、内容按方向淡出 —— 那是为了遮「逐帧 resize 把内容挤扁」的丑。
        # 新版窗口永不 resize，内容从头到尾完整渲染，整套淡出机制都删了；
        # body 的 class 直接切到终点态，露出条上的箭头由 CSS 自己的
        # 短过渡淡入淡出。
        self._push_ball_layout()

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
                    x, y, w, h = dock.tween_slide(
                        edge, center, area, monitor, progress
                    )
                except Exception:
                    return

                # place_window 保持原样调用：窗口尺寸恒定，resize 等于没调，
                # 顺序规则自然不会触发（它的用武之地只剩概念上的兜底）。
                prev_x, prev_y, prev_w, prev_h = (
                    self._ball_x, self._ball_y, self._ball_w, self._ball_h)
                self._ball_progress = progress
                self._ball_w, self._ball_h = w, h
                self._ball_x, self._ball_y = x, y

                try:
                    place_window(self.ball, x, y, w, h,
                                 prev_x, prev_y, prev_w, prev_h,
                                 atomic=self._set_ball_rect)
                except Exception:
                    # 窗口没了（正在关闭）—— 安静退出，不要把异常抛进线程
                    return

                # 按**真实时间轴**对齐，而不是每帧固定睡 10ms。
                #
                # 每帧的 move 本身要花 1–3ms（跨进程调用，实测；旧版带 resize
                # 时也一样），固定睡 10ms 的话一帧实际是 11–13ms，16 帧下来
                # 总时长会从 160ms 涨到将近 200ms。
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

    def _set_ball_rect(self, x: int, y: int, w: int, h: int) -> bool:
        """
        悬浮窗的「一次性落位」。用**已经握在手里的 HWND**，不按标题去找。

        理由见 `winutil.set_window_rect`：按标题找是全机器的，两个实例并存时
        会把**别人的窗口**挪走（诊断脚本已经这么误伤过一次用户开着的 exe）。

        `self._ball_hwnd` 只在 `_polish` 里拿到过一次。拿不到（测试、窗口刚
        建出来）就返回 False，让 `place_window` 退回两次调用的做法 ——
        那条路完全不碰真实窗口，所以测试跑不到 Win32 上去。

        ## ⚠️ 这里必须做一次「逻辑 → 物理」的换算

        传进来的 x/y/w/h 是**逻辑像素**（`dock` 和 `self._ball_x` 那一整套都是），
        而 `SetWindowPos` 要的是**物理像素**（`run.py` 里声明了 DPI 感知）。
        两边约定不同，直接接上就会差一个缩放系数。

        漏掉这一步的症状特别绕，值得记：

            平时          位置整体向左偏 1/4 屏、贴不上边，尺寸还小一圈
            拖动的时候    突然跳到正确位置 —— 因为拖动走的是 pywebview 的
                          `move()`，那个是逻辑像素、内部自己换算
            松手          又回到偏的那个位置 —— 又走回这里了

        「有时对有时错」的几何 bug，先怀疑是不是**两条路径的单位不一致**。

        （README「单位约定」那节说 `ui_scale()` 只该在 `work_area()` 里用 ——
        那是针对「不要拿它去乘 pywebview 的值」说的。反过来，**要调 Win32
        就必须用它把逻辑换回物理**，这里是第二个合法用途。）
        """
        scale = ui_scale() or 1.0
        return winutil.set_window_rect(
            self._ball_hwnd,
            round(x * scale), round(y * scale),
            round(w * scale), round(h * scale),
        )

    def _push_ball_layout(self) -> None:
        """告诉网页该显示「展开的卡片」还是「收起态（露出条上带箭头）」"""
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
        # 就会看到一个停在展开位置的全尺寸窗口，配着「已收起」的状态，
        # 状态和实际位置对不上。
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
        拖动中：挪窗口，但**不让卡片跑出屏幕**。

        用户反馈「拖到右边之后还能继续往右拖」—— 拖出去就看不见了，
        只剩任务栏里一个按钮，很容易以为程序坏了。

        所以这里把目标位置夹住：**内容区（卡片本体）永远不越出工作区**，
        两侧的空白把手区允许悬出 SLIDE_W（见 dock.clamp_free 的说明 ——
        按整窗夹的话，从展开停靠位起拖会先跳一下）。
        贴边的时候内容正好停在边缘上（这也是我们想要的停靠位置），
        但再也推不出去了。
        """
        if self.ball is None:
            return {"ok": False}

        area = work_area()
        x, y = self._ball_x, self._ball_y

        nx, ny = dock.clamp_free(x + dx, y + dy, area)

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

        为什么？收起时屏幕上只剩一条 26px 宽的把手。
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
