"""
程序化地驱动一遍「停靠 → 展开 → 收起」，验证机制真的能跑。

## 为什么需要它

我看不见屏幕。这个功能全是窗口位置和尺寸的变化 —— 眼睛一看就知道对不对，
但对我来说就是一片空白。所以换个办法：**让程序自己去操作，然后把每一步的
窗口矩形读回来核对**。

    python tools/exp_dock.py

跑完会打印每一阶段的窗口位置和尺寸，对不对一眼能看出来。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webview  # noqa: E402

from app.api import Api  # noqa: E402
from app.main import _bg_color, resource_path, ui_scale, work_area  # noqa: E402
from app import dock  # noqa: E402

# 真实运行时会先声明 DPI 感知（见 run.py）。这个实验脚本绕过了 run.py，
# 所以自己声明一次 —— 否则量出来的坐标和实际运行时的对不上。
try:
    import ctypes as _ct
    _ct.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

#: 给 compute_dock 装个探针，把**真实传入的参数**打出来。
#: 我自己的 probe 算出 'right'、程序内部却算出 None ——
#: 说明两边读到的坐标不是同一个值，只有看到真实入参才能确定。
_orig_compute = dock.compute_dock


def _spy(x, y, w, h, area, margin=dock.DOCK_MARGIN, scale=1.0):
    result = _orig_compute(x, y, w, h, area, margin, scale)
    print(f"    [spy] 传入 area = ({area.left},{area.top},{area.right},{area.bottom})"
          f"  {area.width}×{area.height}")
    print(f"    [spy] compute_dock(x={x}, y={y}, w={w}, h={h}) → {result!r}")
    return result


dock.compute_dock = _spy

SCALE = ui_scale()
CW, CH = round(dock.CARD_W * SCALE), round(dock.CARD_H * SCALE)

area = work_area()
print(f"DPI 缩放：{SCALE}   工作区：{area.left},{area.top} – {area.right},{area.bottom}"
      f"  ({area.width}×{area.height})")
print(f"卡片尺寸：{CW}×{CH}    收起尺寸："
      f"{dock.collapsed_size('right', SCALE)}\n")

api = Api()
win = webview.create_window(
    "停靠实验", str(resource_path("web", "ball.html")), js_api=api,
    width=CW, height=CH,
    x=area.right - CW - 40, y=area.top + 300,
    min_size=(round(dock.TAB_SHORT * SCALE), round(dock.TAB_SHORT * SCALE)),
    frameless=True, on_top=True, background_color=_bg_color(),
)
api._ball_window = win


class FakeApp:
    """借用 App 里的停靠方法，但不启动整个程序"""

    def __init__(self) -> None:
        self.ball = win
        self.store = api.store
        self._ball_edge = None
        self._ball_center = 0
        self._ball_collapsed = False
        self._ball_w = dock.CARD_W
        self._ball_h = dock.CARD_H
        self._ball_suppress_expand = False

    # 从 App 里拷过来的三个方法（保持逻辑一致）
    from app.main import App
    _apply_ball_geometry = App._apply_ball_geometry
    _push_ball_layout = App._push_ball_layout
    _ball_drag_end = App._ball_drag_end
    _ball_hover = App._ball_hover
    _ball_slide_out = App._ball_slide_out


app = FakeApp()


def rect() -> str:
    try:
        return f"x={win.x:5} y={win.y:5}  w={win.width:4} h={win.height:4}"
    except Exception as e:                       # noqa: BLE001
        return f"读取失败 {e}"


def step(label: str) -> None:
    time.sleep(1.0)
    print(f"  {label:26} {rect()}")


def probe(label: str) -> None:
    """
    直接把停靠判断的中间值打出来。

    「为什么没停靠」这种事，光看结果猜不出来 —— 得看到四个边的距离
    分别是多少、最近的那个有没有超容差。
    """
    x, y = int(win.x), int(win.y)
    w, h = app._ball_w, app._ball_h
    d = {
        "left": x - area.left,
        "right": area.right - (x + w),
        "top": y - area.top,
        "bottom": area.bottom - (y + h),
    }
    edge = dock.compute_dock(x, y, w, h, area, scale=SCALE)
    print(f"    [probe {label}] 读到 x={x} y={y} w={w} h={h}")
    print(f"                    四边距离 {d} → 判定 {edge!r}")


def run() -> None:
    time.sleep(2.5)
    print("开始\n")

    # ---- 1. 初始：自由浮动 ----
    step("初始（自由浮动）")

    # ---- 2. 模拟拖到右边松手 ----
    win.move(area.right - dock.CARD_W - 4, 300)
    time.sleep(0.8)
    print(f"\n  拖到右边后松手前           {rect()}")
    probe("松手前")
    app._ball_drag_end()
    print(f"    [after] edge={app._ball_edge!r} collapsed={app._ball_collapsed} "
          f"追踪尺寸={app._ball_w}×{app._ball_h} center={app._ball_center}")
    step("松手 → 应该缩成竖条")

    # ---- 3. 鼠标移上去 → 弹出来 ----
    app._ball_suppress_expand = False      # 正常流程里这一步是 mouseleave 做的
    app._ball_hover(True)
    step("鼠标进入 → 应该弹开")

    # ---- 4. 鼠标移开 → 缩回去 ----
    app._ball_hover(False)
    step("鼠标离开 → 应该缩回")

    # ---- 5. 再来一次，确认不漂移 ----
    for i in range(3):
        app._ball_hover(True)
        time.sleep(0.35)
        expanded = (win.x, win.y, win.width, win.height)
        app._ball_hover(False)
        time.sleep(0.35)
        collapsed = (win.x, win.y, win.width, win.height)
    print(f"\n  反复 3 次后：展开={expanded}  收起={collapsed}")

    # ---- 6. 拖到上边 ----
    win.move(700, area.top + 2)
    time.sleep(0.8)
    probe("拖到上边")
    app._ball_drag_end()
    step("拖到上边 → 应该缩成横条")
    app._ball_suppress_expand = False
    app._ball_hover(True)
    step("鼠标进入 → 应该弹开")

    # ---- 7. 拖到空白处 → 不再停靠 ----
    app._ball_hover(False)
    time.sleep(0.4)
    win.move(600, 500)
    time.sleep(0.5)
    app._ball_drag_end()
    step("拖到中间 → 自由浮动")
    print(f"\n  停靠状态 = {app._ball_edge!r}（应为 None）")

    print("\n完成")
    time.sleep(1)
    win.destroy()


threading.Thread(target=run, daemon=True).start()
webview.start()
