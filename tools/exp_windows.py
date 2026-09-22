"""
隔离实验：到底哪个窗口配置会让程序崩掉。

用法：
    python tools/exp_windows.py main            只开主窗口
    python tools/exp_windows.py ball            主窗口 + 悬浮窗（透明）
    python tools/exp_windows.py ball-solid      主窗口 + 悬浮窗（不透明）
    python tools/exp_windows.py toast           主窗口 + 提示条
    python tools/exp_windows.py all             三个都开

程序会在 60 秒后自动退出，然后打印「存活时间」。哪个配置活不过 60 秒，
问题就在哪个窗口上。

## 为什么要写这个脚本

「程序跑十几秒就没了」这种情况，光看代码是看不出来的 ——
必须一个一个变量地试。把变量做成命令行参数，比反复改代码再重启快得多。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webview  # noqa: E402

from app.api import Api  # noqa: E402
from app.main import resource_path, screen_size  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "main"
api = Api()
sw, sh = screen_size()
print(f"模式 = {mode}   屏幕工作区 = {sw}x{sh}")

main = webview.create_window(
    "诊断-主窗口", str(resource_path("web", "index.html")), js_api=api,
    width=1000, height=720,
)
api._window = main

if mode == "second":
    # 普通（有边框）的第二窗口 —— 用来区分「第二个窗口有问题」
    # 还是「无边框窗口有问题」
    webview.create_window(
        "诊断-第二窗口", str(resource_path("web", "toast.html")), js_api=api,
        width=330, height=108,
        x=max(0, sw - 348), y=max(0, sh - 208),
    )

if mode == "ball-hidden":
    # 无边框，但创建时先藏着、3 秒后再显示 ——
    # 试试能不能绕开创建时的渲染
    _ball = webview.create_window(
        "诊断-悬浮窗", str(resource_path("web", "ball.html")), js_api=api,
        width=250, height=268,
        x=max(0, sw - 262), y=max(0, sh - 280),
        frameless=True, on_top=True, hidden=True,
        background_color="#000000",
    )

    def _reveal() -> None:
        time.sleep(3)
        try:
            _ball.show()
            print("已 show() 悬浮窗")
        except Exception as e:                   # noqa: BLE001
            print(f"show 失败：{e}")

    threading.Thread(target=_reveal, daemon=True).start()

if mode in ("ball", "ball-solid", "all"):
    webview.create_window(
        "诊断-悬浮窗", str(resource_path("web", "ball.html")), js_api=api,
        width=250, height=268,
        x=max(0, sw - 262), y=max(0, sh - 280),
        frameless=True, on_top=True,
        transparent=(mode != "ball-solid"),      # ← 唯一被改变的变量
        easy_drag=False,
        background_color="#000000",
    )

if mode in ("toast", "all"):
    webview.create_window(
        "诊断-提示条", str(resource_path("web", "toast.html")), js_api=api,
        width=330, height=108,
        x=max(0, sw - 348), y=max(0, sh - 208),
        frameless=True, easy_drag=True, on_top=True, hidden=True,
        transparent=True, background_color="#000000",
    )

start = time.time()


def watchdog() -> None:
    while time.time() - start < 60:
        time.sleep(1)
    print(f"\n存活满 60 秒 —— 这个配置没问题")
    for w in webview.windows:
        try:
            w.destroy()
        except Exception:                # noqa: BLE001
            pass


threading.Thread(target=watchdog, daemon=True).start()

print("启动中…（60 秒后自动结束）")
webview.start()
print(f"webview.start() 返回了，存活 {time.time() - start:.1f} 秒")
