"""
Windows 特有的窗口微调。

这里做的事，pywebview 都没提供现成的口子 —— 只能直接跟 Win32 API 打交道。

**这些调用全部包在 try 里，失败就当没做。** 它们都是「有更好、没有也能用」的
修饰性功能（不进任务栏、没有阴影），不该因为它们让整个程序起不来。

## 为什么要按标题找窗口

pywebview 的 Window 对象能拿到 HWND 的路径是 `window.native.Handle`，
但访问 `.native` 有风险 —— pywebview 在建窗口时会递归遍历对象图，
碰到它自己的原生对象就可能无限递归（详见 api.py 里那段注释）。

所以这里绕开它，用 `FindWindowW(None, 标题)` 按标题找。
代价是**窗口标题必须唯一** —— 三个窗口的标题不能重名，否则会找错。
"""

from __future__ import annotations

import ctypes
import time

# ---- Win32 常量 ----

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080      # 工具窗口：不进任务栏、不进 Alt+Tab
WS_EX_APPWINDOW = 0x00040000       # 强制进任务栏（跟上面相反，要清掉）

SW_HIDE = 0
SW_SHOW = 5

DWMWA_NCRENDERING_POLICY = 2       # DWM 非客户区渲染策略
DWMNCRP_DISABLED = 1               # 关掉它 = 关掉窗口的系统阴影

DWMWA_WINDOW_CORNER_PREFERENCE = 33    # 窗口圆角偏好（Win11 22000+ 才有）
DWMWCP_DONOTROUND = 1                  # 直角
DWMWCP_ROUND = 2                       # 系统默认圆角

# 三个窗口的标题。**必须互不相同**，否则 FindWindowW 会找错。
TITLE_MAIN = "时间规划表"
TITLE_BALL = "时间规划表悬浮窗"
TITLE_TOAST = "时间规划表提醒"


def _find(title: str) -> int:
    """按标题找窗口句柄。找不到返回 0。"""
    try:
        return ctypes.windll.user32.FindWindowW(None, title)
    except Exception:
        return 0


def wait_for_window(title: str, timeout: float = 10.0,
                    interval: float = 0.02) -> int:
    """
    等一个窗口**真正出现**，返回它的 HWND；超时返回 0。

    ## 为什么需要它

    窗口是**异步创建**的：`create_window()` 只是登记一下，真正的 WinForms
    Form 是在 `webview.start()` 的消息循环里才建出来的。所以「创建完马上按
    标题去找」本身就是个竞态 —— 有时找得到，有时找不到。

    原来的代码用 `time.sleep(0.9)` 绕开这件事。实测（见下面那段注释）
    窗口大约 **0.80 秒**才出现，固定睡 0.9 秒的余量**只有 0.1 秒**。
    冷启动时（WebView2 第一次初始化、杀毒软件扫一遍）轻易就超过它，
    于是 `make_tool_window` 找不到窗口 → **静默返回 False** →
    `WS_EX_APPWINDOW` 摘不掉 → 悬浮窗**永久地出现在任务栏上**。

    这是用户实际报上来的 bug。它的恶劣之处在于**只在慢的时候发生**：
    开发机上跑十次都是好的，用户那边偶尔中一次。

    ## 换成轮询为什么就好了

    轮询没有"窗口期"：窗口一出现就立刻返回（正常情况下还是 0.8 秒左右），
    慢机器上多等一会儿也不会失败。**该等条件成立，就不要睡一个固定时长** ——
    睡固定时长本质上是在赌「这段时间够不够」，而赌输的代价是静默失效。
    """
    deadline = time.monotonic() + timeout
    while True:
        hwnd = _find(title)
        if hwnd:
            return hwnd
        if time.monotonic() >= deadline:
            return 0
        time.sleep(interval)


def make_tool_window(title: str) -> bool:
    """
    把窗口变成「工具窗口」：

      · **不出现在任务栏**（这正是它该有的样子 —— 它是悬浮提示，不是应用窗口）
      · 不出现在 Alt+Tab 里

    做法是给窗口加上 WS_EX_TOOLWINDOW 并清掉 WS_EX_APPWINDOW。

    ## ⚠️ 最后那两行 ShowWindow 必须判断原来的可见状态

    只改扩展样式的话，任务栏上那个按钮会一直留着，直到窗口重建 ——
    所以要「隐藏再显示」刷一次。

    但**不能无脑地 hide 完就 show**。第一版就是这么写的，结果把
    `hidden=True` 创建的**提醒提示条也显示出来了** —— 一开机就有一个
    提示条杵在屏幕上，特别突兀。

    正确做法：先记下它原本可不可见，刷完恢复成原来那样。
    """
    hwnd = _find(title)
    if not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)

        style = get_long(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        set_long(hwnd, GWL_EXSTYLE, style)

        # 刷新任务栏，但要恢复成原来那个可见状态
        was_visible = bool(user32.IsWindowVisible(hwnd))
        user32.ShowWindow(hwnd, SW_HIDE)
        if was_visible:
            user32.ShowWindow(hwnd, SW_SHOW)
        return True
    except Exception:
        return False


SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

WS_CAPTION = 0x00C00000       # 标题栏（WS_BORDER | WS_DLGFRAME）
WS_THICKFRAME = 0x00040000    # 可调边框 —— DWM 的系统阴影就挂在这个样式上


def set_window_rect(hwnd: int, x: int, y: int, w: int, h: int) -> bool:
    """
    ⚠️ 这里收的是**物理像素** —— 和 `window_rect()` / `cursor_pos()` 的返回值
    同一套单位。调用方如果手上是逻辑像素（`dock`、`App._ball_x` 那一整套），
    必须先乘 `screen.ui_scale()` 换过来，否则整体偏一个缩放系数。
    """
    """
    **一次** `SetWindowPos` 同时改位置和尺寸。

    ## 为什么必须合成一次

    pywebview 的 `move()` 和 `resize()` 是两次独立的跨线程调用，中间窗口会
    经历一个「位置和尺寸不配套」的中间态。悬浮窗贴边展开是**长大的同时朝
    反方向挪**（贴右边 = 向左长），于是那一瞬间窗口还是一条细条、停在最终
    位置的左端 —— 它右边到屏幕边缘那一大片是**从来没被窗口覆盖过的桌面**。

    用户看到的现象：浅色模式下弹出过程有一条「黑色拖尾」（深色模式下卡片
    和桌面都是深的，看不出来）。抓帧证实那块是 `#1b2127` —— **是桌面壁纸**，
    不是我们任何一个变量的颜色。

    合成一次之后窗口立刻占满整块矩形，露出来的就是**窗口自己的底色**
    （浅色下是 `#ffffff`），和卡片一色，什么都看不出来。

    ⚠️ 这里刻意**不带** `SWP_SHOWWINDOW`（0x40）。pywebview 的 move/resize
    那两处是带的 —— 那会让一个已隐藏的窗口被「动一下」就重新显示出来
    （`tests/test_ball_ui.py` 有几条护栏专门盯这个）。这里不带，等于顺手
    消掉了那类风险。

    ## 为什么收 HWND 而不是收标题

    按标题找是**全机器**的（`FindWindowW`）—— 同时开两个实例时，它会返回
    **先找到的那一个**，也就是别人的窗口。这个坑真实发生过：一个诊断脚本
    按标题找悬浮窗，结果驱到了用户自己开着的那个 exe 上。

    所以这里只认调用方**已经握在手里的 HWND**，一次查找都不做。
    传 0（还没拿到句柄）直接返回 False，让调用方退回两次调用的做法。
    """
    if not hwnd:
        return False
    try:
        ok = ctypes.windll.user32.SetWindowPos(
            hwnd, 0, int(x), int(y), int(w), int(h),
            SWP_NOZORDER | SWP_NOACTIVATE,
        )
        return bool(ok)
    except Exception:
        return False


def disable_shadow(title: str) -> bool:
    """
    关掉窗口的系统阴影。

    用户反馈「阴影太深」——那不是我画的（CSS 里没写 box-shadow），
    是 **Windows 给无边框窗口自动加的系统阴影**，又硬又重。

    这个阴影改不了浓淡，只能开或关。关掉之后靠那圈 1px 边框给卡片定形，
    看起来干净得多。

    ## ⚠️ 它会顺手把圆角也关掉

    `DWMWA_NCRENDERING_POLICY = DWMNCRP_DISABLED` 关掉的是**整个非客户区
    渲染**，而 Win11 的自动圆角正属于非客户区渲染。所以调完这个之后，
    窗口会从圆角变回直角。

    实测（`tools/exp_corner.py` 的对照矩阵，做法是给窗口铺满纯红、
    再逐像素读四角，看哪些像素被裁掉了）：

        什么都不做              → 圆角（Win11 默认就有）
        只关阴影                → 直角   ← 就是这个把它干掉的
        关阴影 + 显式要求圆角   → 圆角，且阴影仍然是关的
        只要求直角              → 直角

    所以这两个调用必须**成对出现**：`disable_shadow()` 之后要补一次
    `set_rounded_corners(..., True)`。这也是「圆角明明写了 CSS 却没有」
    的真正原因 —— CSS 的 border-radius 只是画在页面里的一个圆角线框，
    真正把窗口裁成圆角的是 DWM。
    """
    hwnd = _find(title)
    if not hwnd:
        return False
    try:
        value = ctypes.c_int(DWMNCRP_DISABLED)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_NCRENDERING_POLICY, ctypes.byref(value), ctypes.sizeof(value)
        )

        # ⚠️ 只调上面那个**不够**：Win11 的系统阴影是挂在 `WS_THICKFRAME` /
        # `WS_CAPTION` 这两个窗口样式上的，样式还在，阴影就还在。
        # 用户实测反馈「阴影没消掉」，就是这一层没摘。
        # 两者一起摘才彻底：DWM 那边关掉非客户区渲染，Win32 这边摘掉
        # 「有边框/有标题栏」这个前提。
        user32 = ctypes.windll.user32
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        GWL_STYLE = -16
        style = get_long(hwnd, GWL_STYLE)
        set_long(hwnd, GWL_STYLE, style & ~WS_CAPTION & ~WS_THICKFRAME)

        return result == 0
    except Exception:
        return False


DWMWA_USE_IMMERSIVE_DARK_MODE = 20    # 标题栏深/浅二选一（Win10 1809+）
DWMWA_CAPTION_COLOR = 35              # 标题栏任意底色（Win11 22621+）
DWMWA_TEXT_COLOR = 36                 # 标题栏文字色（同上）


def _colorref(hex_color: str) -> int:
    """`'#rrggbb'` → COLORREF（`0x00bbggrr`，**低位是红**，别按 RGBA 记）。"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


def set_titlebar_theme(hwnd: int, bg_hex: str, text_hex: str, dark: bool) -> str:
    """
    让**系统标题栏**跟着主题走。主窗口是唯一用得上的（另两个无边框）。

    页面换了色、标题栏还停在系统默认的样子，看着就是上下两截 —— 所以这个
    不是可有可无的美化，是主题这件事的**收尾**。

    ## 两层做法，新的优先

      1. `DWMWA_CAPTION_COLOR` / `DWMWA_TEXT_COLOR`（Win11 22621+）——
         可以设成**和卡片一模一样**的颜色（`#ffffff` / `#181b21`）。
      2. 再往前只有 `DWMWA_USE_IMMERSIVE_DARK_MODE`（Win10 1809+），
         只能在「深 / 浅」之间切，颜色由系统定。

    两个都拿不到（老系统、或者 DWM 不认）就返回 False，调用方不用管 ——
    这属于「有更好、没有也能用」的修饰，不该因为它让程序起不来。
    注意这一层调的是**加了 try 的**：DwmSetWindowAttribute 在不支持的属性上
    会返回失败码，而有些环境会直接抛。

    ## ⚠️ 只收 HWND，不按标题去找

    理由见 `set_window_rect`（按标题找是全机器的，会串到别人的窗口）。
    另外**调用方必须先等窗口真正建出来**（`wait_for_window`）——
    `create_window()` 只是登记一下，真正的窗口要等 `webview.start()` 才出现。
    不等就调，这里拿不到句柄、**静默返回 False，表现就是「标题栏死活不变」**。

    ## 返回值是一段**可读的结果**，不是布尔

    这个函数踩过一次：返回 True 于是日志写「已设为 …」，可屏幕上纹丝不动。
    原因是那次的主题恰好和系统一致（都是深色），**设置成功也看不出变化** ——
    我把「调用成功」当成了「视觉上变了」。

    所以现在把**走的是哪一层**和**从 DWM 读回来的实际值**一起返回。
    「写进去」和「生效」是两件事，只有读回来才作数 —— `set_rounded_corners`
    早就是这么做的，我当时没照做。

    传 0 返回空串。
    """
    if not hwnd:
        return ""

    def _read(attr) -> int:
        got = ctypes.c_uint32(0xFFFFFFFF)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, attr, ctypes.byref(got), ctypes.sizeof(got))
        return got.value

    try:
        dwm = ctypes.windll.dwmapi

        # 1) 精确配色。**两个都要成功才算这条路走通** —— 只换底色不换文字色，
        # 深色底配深色字就是看不见字，比不换还糟。
        want_cap = _colorref(bg_hex)
        want_txt = _colorref(text_hex)
        first = ""
        cap = ctypes.c_uint32(want_cap)
        txt = ctypes.c_uint32(want_txt)
        if (dwm.DwmSetWindowAttribute(hwnd, DWMWA_CAPTION_COLOR,
                                      ctypes.byref(cap), ctypes.sizeof(cap)) == 0
                and dwm.DwmSetWindowAttribute(hwnd, DWMWA_TEXT_COLOR,
                                              ctypes.byref(txt), ctypes.sizeof(txt)) == 0):
            got_cap = _read(DWMWA_CAPTION_COLOR)
            if got_cap == want_cap:
                return f"精确配色(生效) #{bg_hex[1:]}/{text_hex[1:]}"

            # ⚠️ **返回 0 不等于生效。** 实测：Win11 22621 之前的系统上
            # DwmSetWindowAttribute 对这两个属性照样返回 0（S_OK），但读回来
            # 是 0xFFFFFFFF —— 那是「恢复系统默认」的哨兵值，等于什么都没写。
            #
            # 第一版看到返回 0 就当成成功、还提前 return 了，于是永远走不到
            # 下面那层真正会生效的 USE_IMMERSIVE_DARK_MODE。用户看到的就是
            # 「标题栏死活不变」。**「调用没报错」和「效果真的生效」是两件事**
            # —— set_rounded_corners 早就有读回校验，当时没照做。
            # 所以这里读回对不上，就老老实实往下走。
            first = f"精确配色未生效(读回#{got_cap:06x}) → "

        # 2) 退路：深 / 浅二选一
        flag = ctypes.c_int(1 if dark else 0)
        if dwm.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(flag), ctypes.sizeof(flag)) == 0:
            got_dark = _read(DWMWA_USE_IMMERSIVE_DARK_MODE)
            state = "生效" if bool(got_dark) == dark else "未生效"
            return f"{first}深浅二选一({state}) dark={dark}"

        return first + "两层都不支持"
    except Exception as e:
        return f"抛异常：{type(e).__name__}: {e}"


def window_rect(title: str):
    """
    窗口的**物理像素**矩形 (left, top, right, bottom)，找不到返回 None。

    ## 为什么用 GetWindowRect 而不是用我们自己记的逻辑坐标

    悬浮窗的位置和尺寸我们自己是记着一份的（`App._ball_x/_ball_w` 那一套）。
    但那份和窗口的实际矩形**不保证相等** —— `_polish` 里就专门有一句
    「create_window 传的尺寸不可靠，实测传 305×130 读回来是 290×92」，
    所以之后又强制 resize 了一次。

    拿这份记忆值去做「光标在不在窗口里」的判断，就会在窗口边缘附近判断错。
    这个问题要的就是边界处的准确性，所以直接问系统要真实矩形。
    """
    hwnd = _find(title)
    if not hwnd:
        return None
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        r = RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


def cursor_pos():
    """光标的**物理像素**坐标 (x, y)，拿不到返回 None。

    ⚠️ 返回的是物理像素，不是逻辑像素。

    `run.py` 里声明了进程是 DPI 感知的，所以 GetCursorPos 给出的是物理值；
    而 pywebview 的坐标系统是逻辑值。**两边不能直接比** ——
    在 125% 的屏上会差 1.25 倍，屏幕右上角那一片会整体判断错。

    （同理 GetWindowRect 也是物理值，所以它和这里可以直接比。
    详见 screen.ui_scale 的说明 —— 那个换算只在 work_area 里做。）
    """
    try:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        p = POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(p)):
            return None
        return (p.x, p.y)
    except Exception:
        return None


def cursor_in_window(title: str) -> bool:
    """
    光标此刻是不是在这个窗口的矩形范围内。

    两个都是物理像素，直接比即可（见上面两个函数的说明）。
    拿不到任何一边就返回 False —— 这个函数的用途是「压住自动弹出」，
    返回 False 表示「不压」，最坏结果是多弹一次，无害。
    """
    rect = window_rect(title)
    pos = cursor_pos()
    if rect is None or pos is None:
        return False
    left, top, right, bottom = rect
    x, y = pos
    return left <= x < right and top <= y < bottom


def set_rounded_corners(title: str, rounded: bool) -> bool:
    """
    让窗口本身变成圆角（或变回直角）。

    ## 为什么需要它

    因为 `disable_shadow()` 会连圆角一起关掉（见那边的注释）。
    这里显式再表态一次，让「关阴影」和「要圆角」两件事能同时成立。

    ## 为什么不用 SetWindowRgn 自己裁一个圆角区域

    那种做法（`CreateRoundRectRgn` + `SetWindowRgn`）也能做出圆角，
    而且半径想多大就多大，但有两个真实的代价：

      · **没有抗锯齿** —— 区域是硬裁的，圆角边缘一圈锯齿，一眼看得出
      · **尺寸一变就得重画** —— 区域记的是当时那个大小。而悬浮窗在悬停时
        会在 244×104 和 26×76 之间来回变，忘了跟着重设，圆角就会错位

    交给 DWM 则两者都没有：它自己跟着窗口尺寸走，边缘也是平滑的。

    （这条路真走过一次：为了让贴边那一侧保持直角，用区域拼过一个
    「左圆右方」的形状。后来发现没必要 —— 四个角都圆就行，
    于是整套区域代码都拆了。留着这段注释是想说明**为什么当初会走弯路**：
    区域唯一的优势是能逐角控制，一旦不需要逐角控制，它就只剩缺点了。）

    ## Win10 上会怎样

    `DWMWA_WINDOW_CORNER_PREFERENCE` 是 Win11 才有的属性。Win10 上
    `DwmSetWindowAttribute` 会返回一个失败码，这里就返回 False。
    调用方不需要因此做任何特别处理 —— 页面底色和卡片同色，
    圆角没有也只是一张直角卡片，和现在一样。
    """
    hwnd = _find(title)
    if not hwnd:
        return False
    try:
        value = ctypes.c_int(DWMWCP_ROUND if rounded else DWMWCP_DONOTROUND)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(value), ctypes.sizeof(value),
        )
        # 返回 0 只代表「这个属性 Windows 认识」，不代表像素真的变了。
        # 所以再读回来确认一次 —— 排查这类问题时，「调用没报错」
        # 和「效果真的生效」是两件事，不能混为一谈。
        got = ctypes.c_int(-1)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(got), ctypes.sizeof(got),
        )
        return result == 0 and got.value == value.value
    except Exception:
        return False
