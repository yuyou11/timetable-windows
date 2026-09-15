"""
桥接层的「名字契约」测试：JS 和 Python 两边的方法名必须对得上。

## 为什么需要这个文件

前端调后端只有一条路 —— `pywebview.api.xxx()`。这条路上**没有任何检查**：
方法名写错，运行时既不报错也不崩溃，只是那一次调用**静默失败**，
表现是「点了没反应」或者一句含糊的提示。

这个项目已经因此产生过一个真缺陷：`app.js` 一直在调 `call('mark_launched')`，
而 `Api` 上从来没有这个方法（它只在 `Store` 上，而 `Store` 带着
`_serializable = False`，被 pywebview 挡在桥外）。后果是首次启动走完向导
会弹一个「出错了」，而且那个标记永远写不进去。

所以这里把契约钉在**测试期**：改前端或改 Api 时，名字对不上会当场报出来。

## 两个方向都要查

  · JS → Python：`app.js` / `ball.js` 里的 `call('x')` 和 `pywebview.api.x(...)`
  · Python → JS：`main.py` 里 `evaluate_js("window.x()")` 调的那些全局函数

反方向同样会静默失效 —— 把 `showToast` 改名成 `showTip`，
主程序照样跑，只是提醒弹不出来。

## 这个文件只依赖 app.api，不导入 app.main

`tests/test_ball_ui.py` 会操作 `sys.modules`（弹出 `app.main` 再重新导入）。
保持这个文件只碰 `app.api`，就不会和它互相干扰。
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api import Api  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "app" / "web"

#: JS 里通过 call() 包装调后端的地方：call('method_name', ...)
CALL_RE = re.compile(r"call\(\s*'([A-Za-z_]\w*)'")

#: JS 里直接调后端的地方：pywebview.api.method_name(...)
#:
#: 注意 call() 包装内部是 `pywebview.api[method](...)`（动态取属性），
#: 那里没有字符串字面量，所以匹配不到 —— 这是**故意的**：动态调用在测试期
#: 无法检查，能检查的是 call('...') 的实参，也就是上面那条正则。
DIRECT_RE = re.compile(r"pywebview\.api\.([A-Za-z_]\w*)")

#: main.py 用 evaluate_js 调用的 JS 全局函数（反方向的契约）。
#:
#: 这既是测试数据，也是「Python 到底会调前端哪些函数」的一份文档。
#: 加新的记得补在这里；删掉不用的会有下面那条测试提醒。
JS_GLOBALS_CALLED_FROM_PY = (
    "applyDock",     # ball.js：告诉页面显示展开的卡片还是收起的小方框
    "ballAnim",      # ball.js：收起/展开动画开始，内容按方向淡出或淡入
    "ballShown",     # ball.js：恢复「关闭」按钮（长期存在的窗口，禁用状态会留着）
    "refreshAll",    # app.js：主窗口整体重画
    "pulse",         # ball.js：提醒到了，让悬浮窗闪一下
    "showToast",     # toast.html：弹出提醒提示条
)


def _frontend_sources() -> dict[str, str]:
    """
    所有可能调后端的前端文件。

    `.html` 也要扫 —— toast.html 的脚本是**内联**在页面里的，
    只扫 `.js` 会漏掉它。
    """
    paths = sorted(WEB.glob("*.js")) + sorted(WEB.glob("*.html"))
    return {p.name: p.read_text(encoding="utf-8") for p in paths}


def _js_called_methods() -> set[str]:
    """前端源码里出现的所有后端方法名"""
    names: set[str] = set()
    for text in _frontend_sources().values():
        names |= set(CALL_RE.findall(text))
        names |= set(DIRECT_RE.findall(text))
    return names


class TestJsCallsExistOnApi(unittest.TestCase):
    """JS 调用的每个后端方法，Api 上都必须真的有"""

    def test_every_js_call_resolves_to_an_api_method(self):
        called = _js_called_methods()

        # 先确认正则没坏。匹配不到东西的话下面那条断言会「因为空集而通过」，
        # 那种假绿比测试失败更危险 —— 它会让整个文件形同虚设。
        self.assertGreater(
            len(called), 20,
            f"只从前端扫到 {len(called)} 个方法调用，正则或文件位置可能不对",
        )

        missing = sorted(m for m in called if not callable(getattr(Api, m, None)))
        self.assertEqual(
            [], missing,
            "下列方法前端在调，但 Api 上没有对应的公开方法："
            f"{missing}\n"
            "要么在 api.py 里补上，要么改掉前端里的名字 —— 两边必须一致。",
        )

    def test_bridged_methods_are_not_underscore_prefixed(self):
        """
        桥接方法不能以下划线开头。

        pywebview 只暴露**公开**方法。如果哪天把 `get_today` 改名成
        `_get_today`，上面那条测试仍然会通过（方法确实存在），
        但 JS 永远调不到它 —— 因为扫描器会跳过下划线开头的名字。

        所以这里单独挡一道。
        """
        private = sorted(m for m in _js_called_methods() if m.startswith("_"))
        self.assertEqual(
            [], private,
            f"这些被 JS 调用的名字是下划线开头的，桥不过去：{private}",
        )


class TestPythonCallsJsGlobals(unittest.TestCase):
    """反方向：main.py 用 evaluate_js 调的前端函数，前端必须真的定义了"""

    def test_globals_called_from_python_are_defined_in_the_frontend(self):
        """
        检查方式是在前端源码里找这个名字的**文本**。

        这是个**弱检查** —— 名字出现在注释里也会通过。但对「改名忘了同步」
        这类事故足够了，而且不会因为定义写法的差异而误报：
        `window.x = function(){}` 和顶层的 `function x(){}`
        在普通脚本里都会挂到 window 上，而后者源码里根本没有 "window.x"。
        """
        source = "\n".join(_frontend_sources().values())
        missing = [n for n in JS_GLOBALS_CALLED_FROM_PY if n not in source]
        self.assertEqual(
            [], missing,
            "main.py 用 evaluate_js 调这些前端函数，但前端源码里找不到："
            f"{missing}\n"
            "改名之后忘了同步，表现是「功能静默失效」（比如提醒条弹不出来）。",
        )

    def test_no_stale_entries_in_the_list(self):
        """
        上面那份清单不能过期。

        列了但 main.py 里根本不出现的，说明前端已经不调它了 ——
        留着会让人以为还有这条调用路径。
        """
        main_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        stale = [n for n in JS_GLOBALS_CALLED_FROM_PY if n not in main_src]
        self.assertEqual(
            [], stale,
            f"这些前端函数 main.py 已经不用了，从清单里删掉：{stale}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
