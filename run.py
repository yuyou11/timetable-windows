"""
启动入口。

单独放一个文件（而不是直接用 app/main.py）是为了让 PyInstaller 有个明确的起点，
同时保证开发时双击这个文件、和打包后双击 exe，走的是同一条路。

## 这个文件里有两段看着多余、其实非有不可的代码

### 1. 接管 sys.stdout / sys.stderr

PyInstaller 用 `--windowed` 打包后，程序**没有控制台**，
于是 `sys.stdout` 和 `sys.stderr` 都是 `None`。

问题是：只要有**任何一行代码**（含第三方库内部）执行了 `print()`，
就会抛 `AttributeError: 'NoneType' object has no attribute 'write'`，
程序当场退出 —— 而因为没有控制台，**你什么都看不到**。

这个坑很难查：同一份代码 `--console` 打包就没事，`--windowed` 就静默死亡。
所以这里在程序最开始就把它们接住，让 print 变成无害的空操作。

### 2. 把崩溃写进文件

窗口模式下异常不会显示在任何地方。包一层 try/except，
把 traceback 写到数据目录的 crash.log —— 出了问题至少有个线索。
"""

import ctypes
import os
import sys
import traceback
from pathlib import Path


def _silence_streams() -> None:
    """窗口模式下没有控制台，把 stdout/stderr 换成无害的容器"""
    devnull = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = devnull
    if sys.stderr is None:
        sys.stderr = devnull


def _declare_dpi_aware() -> None:
    """
    声明本进程是 DPI 感知的。

    ## 这一句非有不可，而且必须在任何窗口创建之前

    默认情况下，Windows 会把非 DPI 感知的进程「虚拟化」——
    系统 API 返回的是**按缩放比例缩小的逻辑值**。

    问题在于：**WebView2 一初始化就会把进程变成 DPI 感知**。
    于是同一台机器上，同一个 `SystemParametersInfoW(SPI_GETWORKAREA)`：

        程序启动时      → 2048×1232   （125% 缩放下的虚拟值）
        webview 起来之后 → 2560×1540   （真实物理像素）

    **而窗口坐标一直是物理像素。** 用早期那个 2048 当右边界去算「贴右边了吗」，
    窗口明明贴着屏幕右缘，算出来却离了 560 像素。

    症状极具迷惑性：**贴着上边能停靠，贴着右边不能** ——
    因为上边缘在两种单位下都是 0，碰巧是对的；
    而右边缘一个是 2048、一个是 2560，差了一整块。

    （顺带解释另一个怪现象：`window.width` 报 229，而实际设的是 244 ——
    同一套虚拟化造成的。）

    解决就是开头这一句：**从进程启动就声明 DPI 感知**，
    这样所有 API 从始至终都在同一个坐标系（物理像素）里。

    代价是窗口尺寸也要按缩放系数放大，否则在高 DPI 屏上会显得偏小 ——
    这件事交给 main.ui_scale() 处理。
    """
    try:
        # Windows 8.1+ ：2 = PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            # 更老的备用 API
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            # 设不了就算了 —— 大不了停靠位置偏一点，不该因此起不来
            pass


def _crash_log_path() -> Path:
    base = os.environ.get("TIMETABLE_DATA_DIR") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Timetable" / "crash.log"


def main() -> int:
    # ---- 开发时直接用这个文件运行 ----
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parent))

    _silence_streams()
    # 必须在导入 webview / 创建任何窗口之前
    _declare_dpi_aware()

    try:
        from app.main import main as app_main
        return app_main()
    except BaseException:                      # noqa: BLE001
        # 连 KeyboardInterrupt 都要抓 —— 窗口模式下任何未处理异常
        # 都等于「双击了没反应」，那种反馈对用户毫无帮助
        try:
            log = _crash_log_path()
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write("\n" + "=" * 60 + "\n")
                traceback.print_exc(file=f)
        except Exception:                      # noqa: BLE001
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
