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
        return result == 0
    except Exception:
        return False


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
