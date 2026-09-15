"""
验证 pywebview 能不能在运行时改窗口尺寸和位置。

「边缘自动隐藏」这个功能完全建立在这两个能力上，所以先单独验证一遍。
地基不牢的话，上面的东西都是白搭。

    python tools/exp_resize.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webview  # noqa: E402

win = webview.create_window(
    "resize 实验",
    "data:text/html,<body style='margin:0;background:#2563eb;color:#fff;"
    "font:14px sans-serif;display:grid;place-items:center'>"
    "<div id=info>初始</div></body>",
    width=244, height=104,
    x=200, y=200,
    # ⚠️ 关键：pywebview 的 min_size 默认是 (200, 100)。
    # 不放开的话，resize() 永远缩不到 200×100 以下 ——
    # 「缩成一条边」根本做不到。实测就是这么发现的。
    min_size=(20, 20),
    frameless=True, on_top=True,
)


def report(label: str) -> None:
    try:
        print(f"  {label:22} x={win.x:5} y={win.y:5} w={win.width:4} h={win.height:4}")
    except Exception as e:                       # noqa: BLE001
        print(f"  {label:22} 读取失败：{type(e).__name__}: {e}")


def experiment() -> None:
    time.sleep(2)
    print("开始实验\n")
    report("初始")

    steps = [
        ("缩小成竖条", lambda: (win.resize(24, 72), win.move(1000, 300))),
        ("挪到右边", lambda: win.move(1200, 300)),
        ("放大回卡片", lambda: (win.resize(244, 104), win.move(1000, 300))),
        ("挪到顶部", lambda: win.move(500, 0)),
        ("缩小成横条", lambda: (win.resize(72, 24), win.move(500, 0))),
    ]
    for label, action in steps:
        try:
            action()
        except Exception as e:                   # noqa: BLE001
            print(f"  {label} 失败：{type(e).__name__}: {e}")
            continue
        time.sleep(1.0)
        report(label)

    print("\n结论：resize 和 move 都能用")
    time.sleep(1)
    win.destroy()


threading.Thread(target=experiment, daemon=True).start()
webview.start()
