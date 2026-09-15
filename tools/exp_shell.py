"""
验证三件事：
  1. 托盘图标起来了
  2. 悬浮窗真的不在任务栏里（WS_EX_TOOLWINDOW 生效）
  3. 悬浮窗被隐藏 + 主窗口关闭时，**不会**触发那个栈溢出崩溃

第 3 条是用户实际踩到的崩溃（System.InsufficientExecutionStackException），
必须专门验一遍 —— 它只在「悬浮窗已关 + 关闭主窗口」这条路径上出现。

    python tools/exp_shell.py
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webview  # noqa: E402

from app import winutil  # noqa: E402
from app.main import App  # noqa: E402

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000


def ex_style(title: str) -> int:
    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    if not hwnd:
        return -1
    get_long = getattr(ctypes.windll.user32, "GetWindowLongPtrW",
                       ctypes.windll.user32.GetWindowLongW)
    return get_long(hwnd, GWL_EXSTYLE)


def run() -> None:
    app = App()

    time.sleep(1)
    app.build()

    def probe() -> None:
        time.sleep(6)
        print("\n--- 检测 ---")

        # 1. 托盘
        print(f"  托盘图标      : {'已启动' if app.tray is not None else '未启动（缺库？）'}")

        # 2. 任务栏
        for title, label in ((winutil.TITLE_MAIN, "主窗口"),
                             (winutil.TITLE_BALL, "悬浮窗"),
                             (winutil.TITLE_TOAST, "提示条")):
            style = ex_style(title)
            if style < 0:
                print(f"  {label:6}      : 找不到窗口")
                continue
            tool = bool(style & WS_EX_TOOLWINDOW)
            appwin = bool(style & WS_EX_APPWINDOW)
            where = "不进任务栏" if (tool and not appwin) else "★ 在任务栏里 ★"
            print(f"  {label:6}      : {where}   (exstyle=0x{style & 0xFFFFFFFF:08X})")

        # 3. 复现用户的崩溃路径
        print("\n--- 复现崩溃路径：先关悬浮窗，再关主窗口 ---")
        app.store.ball_enabled = False
        try:
            if app.ball is not None:
                app.ball.hide()
            print("  已隐藏悬浮窗")
        except Exception as e:                   # noqa: BLE001
            print(f"  隐藏失败：{e}")

        time.sleep(0.6)
        print("  现在关闭主窗口（这一步以前会栈溢出）…")
        try:
            app.main.destroy()
            print("  ✓ 主窗口关闭没崩")
        except Exception as e:                   # noqa: BLE001
            print(f"  ✗ 关闭时抛异常：{type(e).__name__}: {e}")

        time.sleep(2)
        print("\n如果上面没有出现「堆栈空间不足」的 Windows 错误框，说明修好了。")
        try:
            app._quit()
        except Exception:
            pass
        time.sleep(0.5)
        import os
        os._exit(0)          # 直接把进程干掉，免得残留窗口挡住实验

    threading.Thread(target=probe, daemon=True).start()
    webview.start()


if __name__ == "__main__":
    run()
