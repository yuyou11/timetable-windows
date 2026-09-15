"""
诊断脚本：从 Python 侧检查网页到底加载成什么样了。

## 为什么需要它

前端如果**语法错误**，整个文件都不会执行 —— 包括你自己写的
`window.onerror` 处理器。于是「窗口开着但什么都不做，也没有任何报错」，
完全无从下手。

这个脚本绕开前端，直接从 Python 调 `evaluate_js()` 进页面里问：
标题是什么、body 里有多少内容、pywebview.api 在不在、脚本有没有跑起来。

    python tools/diag_web.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webview  # noqa: E402

from app.api import Api  # noqa: E402
from app.main import resource_path  # noqa: E402

PROBES = [
    ("document.title", "页面标题"),
    ("document.body ? document.body.innerHTML.length : -1", "body 内容长度"),
    ("typeof window.pywebview", "pywebview 对象类型"),
    ("(window.pywebview && window.pywebview.api) ? 'yes' : 'no'", "api 是否就绪"),
    ("typeof boot", "app.js 的 boot 函数是否存在"),
    ("typeof $", "app.js 的 $ 是否存在"),
    ("typeof booted === 'undefined' ? 'undefined' : String(booted)", "booted 变量的值"),
    ("document.querySelectorAll('script').length", "页面里的 script 标签数"),
    ("document.querySelector('link[rel=stylesheet]') ? 'yes' : 'no'", "样式表是否加载"),
    ("document.getElementById('page-today') ? 'yes' : 'no'", "今天页的 DOM 是否存在"),
]


def main() -> int:
    api = Api()
    html = resource_path("web", "index.html")
    print(f"加载：{html}  (存在={html.exists()})")

    window = webview.create_window("诊断", str(html), js_api=api)

    def probe() -> None:
        time.sleep(4)
        print("\n--- 探测结果 ---")
        for expr, label in PROBES:
            try:
                value = window.evaluate_js(expr)
            except Exception as e:                       # noqa: BLE001
                value = f"<求值失败: {type(e).__name__}: {e}>"
            print(f"  {label:26} = {value}")

        # 试着主动调一次 boot，看会不会抛出具体错误
        print("\n--- 主动调用 boot() ---")
        try:
            window.evaluate_js("boot()")
            time.sleep(2)
            print("  调用成功")
        except Exception as e:                            # noqa: BLE001
            print(f"  调用失败：{type(e).__name__}: {e}")

        print("\n--- 再探一次 ---")
        try:
            print("  body 内容长度 =", window.evaluate_js("document.body.innerHTML.length"))
            print("  今天页标题    =", window.evaluate_js(
                "(document.getElementById('todayDate')||{}).textContent"))
        except Exception as e:                            # noqa: BLE001
            print(f"  失败：{e}")

        time.sleep(1)
        window.destroy()

    threading.Thread(target=probe, daemon=True).start()
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
