"""
边缘停靠的几何计算。

这是「鼠标一碰边缘就滑出来、移开就缩回去」这个功能的全部数学部分。

## 为什么单独放一个文件

因为这里的计算**最容易写错、又最难用眼睛发现**：差几个像素、判错了边、
夹取时算反了，表现都是「看起来差不多但就是别扭」。而这部分逻辑不碰
任何窗口 API，可以当成纯数学来测 —— 所以把它拎出来，配一组测试。

真正操作窗口（move / resize）的代码在 main.py 里，那部分没法单测，
只能靠跑起来看。

## 全程使用「逻辑像素」

**这个模块里所有坐标和尺寸都是逻辑像素**（100% 缩放下的像素），
不是物理像素。这一点非常关键，搞错了会让窗口跑到屏幕外面去。

为什么？因为 pywebview 的 Windows 后端**整个公开 API 都用逻辑像素**，
DPI 换算它自己在内部做（见 webview/platforms/winforms.py）：

    create_window(x=, y=, width=, height=)  收逻辑值，内部乘缩放系数
    window.x / .y / .width / .height        返回逻辑值，内部除缩放系数
    move() / resize()                       收逻辑值

所以调用方**只管用逻辑像素**，一个缩放系数都不要乘。
（我一开始就是多乘了一次 1.25，结果悬浮窗被推到屏幕外面 —— 详见 README 的踩坑记录。）

工作区也是：从 Win32 拿到的 `SPI_GETWORKAREA` 是**物理**像素，
必须先除一次缩放系数转成逻辑的，才能和 pywebview 的坐标对上。
这件事在 main.work_area() 里做。

## 三个概念

  · **工作区（Area）**：屏幕扣掉任务栏之后那块（逻辑像素）。
  · **窗口坐标**：窗口左上角在工作区里的位置。
  · **停靠中心（center）**：贴左边或右边时是窗口的**垂直**中心；
    贴上边或下边时是**水平**中心。缩进去的时候两个尺寸不一样，
    但这条中线要保住 —— 否则滑进滑出时会「跳」一下。
"""

from __future__ import annotations

from typing import NamedTuple, Optional

# ---- 尺寸 ----

#: 展开状态的卡片
CARD_W = 244
CARD_H = 104

#: 收起后露在外面的小方框。竖着贴左右边时用 SHORT×LONG，横着贴上下边时反过来
TAB_SHORT = 26
TAB_LONG = 76

#: 松手时离边缘多近才算「要停靠」
DOCK_MARGIN = 32

#: 圆角半径（逻辑像素）。**这两个数只给 CSS 用。**
#:
#: 窗口本身的圆角是 Windows 裁的（见 winutil.set_rounded_corners），
#: 半径由系统定、我们调不了；这两个数管的是页面里那圈 1px 边框的圆角。
#: 两边对不上的话，边角会看到边框被切掉一截，或者边框线画进了已经被裁掉、
#: 根本显示不出来的区域里。
#:
#: ⚠️ **必须和 app/web/ball.html 里的 --radius-card / --radius-tab 一致。**
#: 因为是两份独立写下的数据、没有任何机制保证它们相等，所以
#: tests/test_dock.py 里有一条测试直接读 ball.html 来核对 ——
#: 改一边忘了另一边，跑测试会当场报出来。
#:
#: 卡片那个 10 是照着 Windows 自己给窗口的圆角半径挑的（实测约 10 逻辑
#: 像素），这样卡片和系统其他窗口的圆角看起来是一套的。
CORNER_RADIUS_CARD = 10
CORNER_RADIUS_TAB = 8

EDGES = ("left", "right", "top", "bottom")


class Area(NamedTuple):
    """屏幕上的可用区域（扣掉任务栏）"""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def collapsed_size(edge: str) -> tuple[int, int]:
    """收起后小方框的宽高。贴左右边是竖条，贴上下边是横条。"""
    if edge in ("left", "right"):
        return TAB_SHORT, TAB_LONG
    return TAB_LONG, TAB_SHORT


def compute_dock(x: int, y: int, w: int, h: int, area: Area,
                 margin: int = DOCK_MARGIN) -> Optional[str]:
    """
    判断窗口松手后该停靠在哪个边。离得都远就返回 None（自由浮动）。

    四个边各算一个距离，取最近的；最近的那个还超过 margin 就不停靠。

    全部参数都是逻辑像素（见模块开头）。所以 margin 也是一个逻辑值 ——
    不需要按屏幕缩放调整，因为缩放已经由 pywebview 统一处理了。
    """
    distances = {
        "left": x - area.left,
        "right": area.right - (x + w),
        "top": y - area.top,
        "bottom": area.bottom - (y + h),
    }
    edge = min(distances, key=lambda k: distances[k])
    return edge if distances[edge] <= margin else None


def docked_size(edge: str, collapsed: bool) -> tuple[int, int]:
    """停靠状态下窗口的尺寸。collapsed=True 是那个收起的小方框。"""
    if collapsed:
        return collapsed_size(edge)
    return CARD_W, CARD_H


def placed_geometry(edge: str, center: int, area: Area,
                    w: int, h: int) -> tuple[int, int, int, int]:
    """
    已知窗口尺寸，算出贴边之后它该在哪。返回 (x, y, w, h)。

    这是 `docked_geometry` 的底层版本。为什么要拆出来：那边只能表达
    「收起」和「展开」两种尺寸，而**动画需要任意中间尺寸**。
    位置这件事只跟尺寸和停靠边有关，跟「算不算收起态」无关，所以能单独拎出来。
    """
    if edge == "left":
        nx, ny = area.left, center - h / 2
    elif edge == "right":
        nx, ny = area.right - w, center - h / 2
    elif edge == "top":
        nx, ny = center - w / 2, area.top
    elif edge == "bottom":
        nx, ny = center - w / 2, area.bottom - h
    else:
        raise ValueError(f"未知的停靠方向：{edge!r}")

    return clamp_to_area(nx, ny, w, h, area)


def docked_geometry(edge: str, center: int, area: Area,
                    collapsed: bool) -> tuple[int, int, int, int]:
    """
    算出停靠后窗口该在哪、多大。返回 (x, y, w, h)。

    [center] 见模块开头说明 —— 贴左右边时是垂直中心，贴上下边时是水平中心。
    **只传中心而不是传整个矩形**，是为了避免反复展开收起时坐标漂移：
    每次都由中心重新算，误差不会累积。
    """
    w, h = docked_size(edge, collapsed)
    return placed_geometry(edge, center, area, w, h)


def tween_docked_geometry(edge: str, center: int, area: Area,
                          progress: float) -> tuple[int, int, int, int]:
    """
    收起/展开动画的**中间帧**。返回 (x, y, w, h)。

    [progress] 0.0 = 完全展开的卡片，1.0 = 收起的小方框。
    超出 [0, 1] 会被夹回来 —— 缓动曲线算出 -0.001 这种值很正常，
    不该让它变成一个比卡片还大的窗口。

    ## 为什么插值的是尺寸，而不是位置

    因为位置是尺寸的**函数**：`placed_geometry` 里那四个公式说明了这一点
    （比如贴右边是 `nx = area.right - w`，让窗口的右边缘永远咬着屏幕边缘）。
    所以只要插值尺寸，位置每帧重算就自动是对的 —— 窗口会沿着屏幕边缘
    顺滑地收进去，不会中途跳一下。

    反过来说，**如果连位置一起插值就会出错**：起点和终点的位置各自算出来
    没问题，但中间的线性插值不等于「中间尺寸对应的正确位置」，
    因为那个关系不是线性的（它由 clamp 和中心线共同决定）。

    ## 为什么这里不做缓动

    缓动是「时间 → progress」那一步的事，由调用方决定。
    这个函数保持**纯线性**，几何计算就是几何计算 ——
    以后想换缓动曲线（ease-out、回弹……）都不用动这里。
    """
    w0, h0 = docked_size(edge, False)
    w1, h1 = docked_size(edge, True)

    p = max(0.0, min(1.0, float(progress)))
    w = round(w0 + (w1 - w0) * p)
    h = round(h0 + (h1 - h0) * p)
    return placed_geometry(edge, center, area, w, h)


def clamp_to_area(x: float, y: float, w: int, h: int,
                  area: Area) -> tuple[int, int, int, int]:
    """
    把窗口夹回工作区里，不让任何一部分跑到屏幕外面。

    注意先夹 x 再算 y 那种按顺序来的写法容易出错，这里两个方向独立夹取。
    """
    nx = max(area.left, min(area.right - w, x))
    ny = max(area.top, min(area.bottom - h, y))
    return int(round(nx)), int(round(ny)), int(w), int(h)


def center_of(edge: str, x: int, y: int, w: int, h: int) -> int:
    """从窗口矩形里取出那条需要保住的中线"""
    if edge in ("left", "right"):
        return int(round(y + h / 2))
    return int(round(x + w / 2))


def default_area(width: int = 1920, height: int = 1080) -> Area:
    """兜底的工作区（拿不到真实屏幕信息时用）"""
    return Area(0, 0, width, height)
