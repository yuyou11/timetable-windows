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
这件事在 screen.work_area() 里做；显示器边界同理，在 screen.monitor_rect() 里做。

## 三个概念

  · **工作区（area）**：屏幕扣掉任务栏之后那块（逻辑像素）。
    展开**落位**以它为准 —— 卡片不能压在任务栏上。
  · **显示器边界（monitor）**：整块屏幕的边界，**含任务栏占的那条**。
    收起**滑出**以它为准 —— 「屏幕之外」指的是显示器之外；
    任务栏停在左/右时它和工作区差一条任务栏的宽度（见 slide_geometry）。
  · **停靠中心（center）**：贴左边或右边时窗口的**垂直**中心。
    滑进滑出时 y 每次由它重算，误差不会累积 —— 否则会「跳」一下。

## 为什么是「窗口固定大小、整窗平移」，而不是「窗口变大变小」

旧版收起 = 把窗口逐帧 resize 成 26×76 的小方框。每帧新暴露出来的像素
要等 WebView2 异步重绘，重绘之前露的就是窗口底色/根本没画过的区域 ——
用户看到的就是弹出中途边上那条灰（见 main.place_window 注释里记的
「黑色拖尾」，同一类问题，换了台机器换了个主题就换个颜色出现）。

新版窗口**固定 WINDOW_W × WINDOW_H、永不 resize**：卡片左右各留
SLIDE_W 的空白把手区，收起 = 整窗往屏幕外平移、只留把手那条；
展开 = 平移到**内容区贴住屏幕边缘**为止（靠边的把手区留在屏外）。
**平移不产生任何"新暴露的未绘制区域"** —— 窗口里每一像素从创建起
就画好了，挪到哪都是画好的内容，灰边从根上消失。
"""

from __future__ import annotations

from typing import NamedTuple, Optional

# ---- 尺寸 ----

#: 悬浮窗窗口的固定尺寸。**收起和展开都是它，任何代码都不许 resize 这个窗口。**
#:
#: 宽 = 卡片内容区 244 + 左右各 13 的把手区。把手区就是收起时露在屏幕
#: 边缘的那条；展开时**靠屏幕边的那条留在屏外**（只滑出到内容贴边为止，
#: 见 slide_geometry），另一条则成为卡片内侧的一段留白。
#: 页面那边的对账关系（有测试核对）：#panel 的 padding 是 9px 24px，
#: 24 = SLIDE_W + 11，于是内容列宽 = 270 − 48 = 222，和旧版 244 − 22 一致 ——
#: 内容一个像素都没挪。
WINDOW_W = 270
WINDOW_H = 104

#: 收起时露在屏幕外面的把手条宽度（= 窗口两侧空白区的宽度）
SLIDE_W = 13

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
#:
#: TAB 那个 6 受把手宽度约束：把手只有 SLIDE_W = 13 宽（覆盖层再窄 1px），
#: 圆角超过宽度一半会被浏览器等比压扁 —— 半径跟着把手一起减半了。
CORNER_RADIUS_CARD = 10
CORNER_RADIUS_TAB = 6

#: 只支持左右贴边。
#:
#: 为什么没有上下边：「整窗滑出」的把手在窗口左右两侧，上下滑出需要
#: 另一套上下留白和纵向几何，而屏幕上/下缘恰恰是最常被任务栏占着的地方，
#: 收益很小。拖到上/下边缘松手就是自由浮动，不再吸附。
EDGES = ("left", "right")


class Area(NamedTuple):
    """屏幕上的一块矩形区域（逻辑像素）。工作区和显示器边界都用它表示。"""

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


def compute_dock(x: int, y: int, w: int, h: int, area: Area,
                 margin: int = DOCK_MARGIN) -> Optional[str]:
    """
    判断窗口松手后该停靠在哪个边。离得都远就返回 None（自由浮动）。

    只算左右两条边（见 EDGES 的说明）：拖到上/下边缘附近松手就是自由浮动。

    [x, y, w, h] 传**窗口**矩形。收起/展开状态下窗口视觉上就是整张卡片
    （两侧空白区是卡片自己的留白），所以按窗口边算吸附距离和用户手感一致。

    全部参数都是逻辑像素（见模块开头）。所以 margin 也是一个逻辑值 ——
    不需要按屏幕缩放调整，因为缩放已经由 pywebview 统一处理了。
    """
    distances = {
        "left": x - area.left,
        "right": area.right - (x + w),
    }
    edge = min(distances, key=lambda k: distances[k])
    return edge if distances[edge] <= margin else None


def slide_geometry(edge: str, center: int, area: Area, monitor: Area,
                   collapsed: bool) -> tuple[int, int, int, int]:
    """
    停靠状态下窗口该在哪。返回 (x, y, w, h)，尺寸恒为 WINDOW_W × WINDOW_H。

    [collapsed] True = 收起（只有把手露在屏幕边缘，其余在屏幕外），
                False = 展开（内容区贴着停靠边，完整可见；靠边的把手区留在屏外）。

    ## x 的两个基准（这是本函数最要紧的一处）

      · **展开**滑到「**内容区**贴住工作区边缘」为止 —— 不把整窗都搬进来。
        用户的要求：不要全弹出，内容全露就行。于是靠屏幕边的那条把手区
        留在屏外，滑出距离正好是内容区的宽度；内容区不压任务栏（工作区）。
      · **收起**滑向**显示器边界**：要把卡片整个送出**屏幕**，
        而不只是送出工作区。任务栏停在左/右时两者差一条任务栏的宽度 ——
        用工作区当滑出终点，整张卡片会明晃晃地压在任务栏上
        （这个窗口是 on_top 的，压上去就是压上去）。
        用显示器边界，压在任务栏上的只剩 SLIDE_W 宽的把手，可以接受。

    ## 只夹 y，绝不夹 x

    y 夹回工作区，防的是「中线被拖到屏幕角上，滑出之后把手看不见」。
    x **不能**夹 —— 收起态的 x 就是要出屏，这正是设计本身。
    旧版那个「任何部分都不许出屏」的 clamp 在这里是反作用，
    它只该用在拖动和自由浮动上（见 clamp_free）。

    [center] 是垂直中心。每次由中心重算而不是增量挪，误差不累积。
    """
    w, h = WINDOW_W, WINDOW_H
    if edge == "right":
        nx = (monitor.right - SLIDE_W) if collapsed else (area.right - WINDOW_W + SLIDE_W)
    elif edge == "left":
        nx = (monitor.left - (w - SLIDE_W)) if collapsed else (area.left - SLIDE_W)
    else:
        raise ValueError(f"未知的停靠方向：{edge!r}（只支持左右贴边）")

    ny = max(area.top, min(area.bottom - h, center - h / 2))
    return int(round(nx)), int(round(ny)), w, h


def tween_slide(edge: str, center: int, area: Area, monitor: Area,
                progress: float) -> tuple[int, int, int, int]:
    """
    收起/展开动画的**中间帧**。返回 (x, y, w, h)。

    [progress] 0.0 = 完全展开，1.0 = 完全收起。
    超出 [0, 1] 会被夹回来 —— 缓动曲线算出 -0.001 这种值很正常，
    不该让它变成一个超出两端点的位置。

    ## 为什么只插值 x，而且不用每帧重算几何

    窗口尺寸恒定，两个端点的 y 又必然相等（同一个 center、同一套夹取），
    所以整条动画就是两个 x 之间的一条水平线段 —— 纯线性插值即可。
    旧版位置是尺寸的函数、必须每帧由尺寸重算；新版没有这层耦合，
    这是「固定尺寸」换来的第二个便宜（第一个是灰边消失）。

    ## 为什么这里不做缓动

    缓动是「时间 → progress」那一步的事，由调用方决定。
    这个函数保持**纯线性**，几何计算就是几何计算 ——
    以后想换缓动曲线（ease-out、回弹……）都不用动这里。
    """
    x0, y0, w, h = slide_geometry(edge, center, area, monitor, False)
    x1, _, _, _ = slide_geometry(edge, center, area, monitor, True)
    p = max(0.0, min(1.0, float(progress)))
    return int(round(x0 + (x1 - x0) * p)), y0, w, h


def clamp_to_area(x: float, y: float, w: int, h: int,
                  area: Area) -> tuple[int, int, int, int]:
    """
    把矩形夹回工作区里，不让任何一部分跑到屏幕外面。

    一般代码不要直接用 —— 悬浮窗的拖动/自由浮动走 `clamp_free`，
    它才是按「内容区」语义夹取的那层。这个函数只作为纯矩形夹取的底层。

    注意先夹 x 再算 y 那种按顺序来的写法容易出错，这里两个方向独立夹取。
    """
    nx = max(area.left, min(area.right - w, x))
    ny = max(area.top, min(area.bottom - h, y))
    return int(round(nx)), int(round(ny)), int(w), int(h)


def clamp_free(x: float, y: float, area: Area) -> tuple[int, int]:
    """
    自由浮动 / 拖动时的夹取：**内容区**不许越出工作区，把手区允许悬出 SLIDE_W。

    为什么按内容区夹、而不是按整窗夹：展开停靠位本身就让靠边的把手区
    留在屏外（见 slide_geometry）。用户从那个位置按下开始拖、或拖完松手
    停在边上，整窗夹取都会立刻把窗口拽回 13px —— 表现为拖动起手一跳。
    内容区（WINDOW_W − 2·SLIDE_W 宽）才是用户眼里的「卡片本体」，
    它不出屏，「拖不出去」的承诺就依然成立；悬出的只是空白把手区，
    看起来就是卡片贴着边，和停靠态一个观感。
    """
    content_w = WINDOW_W - 2 * SLIDE_W
    nx, ny, _, _ = clamp_to_area(x + SLIDE_W, y, content_w, WINDOW_H, area)
    return nx - SLIDE_W, ny


def center_of(edge: str, x: int, y: int, w: int, h: int) -> int:
    """从窗口矩形里取出那条需要保住的中线（只支持左右贴边 = 垂直中心）"""
    return int(round(y + h / 2))


def default_area(width: int = 1920, height: int = 1080) -> Area:
    """兜底的工作区（拿不到真实屏幕信息时用）"""
    return Area(0, 0, width, height)
