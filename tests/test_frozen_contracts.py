"""
几条「违反了会静默炸掉」的契约。

这里测的都不是业务逻辑，而是**代码组织方式和平台约定**层面的约束。
它们的共同特点是：违反了几乎不会有像样的报错 ——
要么硬崩且不留日志，要么看起来还在跑但某个功能永远失效。
所以只能用测试钉住。

这三条都是这个项目实际踩过的坑，不是想当然加的。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import winutil  # noqa: E402
from app.api import Api  # noqa: E402
from app.store import Store  # noqa: E402


class IsolatedApiTestCase(unittest.TestCase):
    """
    构造 `Api` 会连带构造 `Store`，而 `Store` 默认会读真实数据文件
    （`%APPDATA%\\Timetable\\data.json`）。测试绝不允许碰用户真数据，
    所以这里把数据目录指到临时目录，用完清掉。

    `store.data_dir()` 认 `TIMETABLE_DATA_DIR` 这个环境变量（见 store.py），
    而且它是在 `Store()` **构造时**才读的 —— 所以只要在构造之前设好就行，
    不需要折腾模块导入顺序。
    """

    def setUp(self) -> None:
        tmp = tempfile.mkdtemp(prefix="timetable_contract_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self._saved = os.environ.get("TIMETABLE_DATA_DIR")
        os.environ["TIMETABLE_DATA_DIR"] = tmp
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        if self._saved is None:
            os.environ.pop("TIMETABLE_DATA_DIR", None)
        else:
            os.environ["TIMETABLE_DATA_DIR"] = self._saved


class TestStoreIsInvisibleToTheBridge(unittest.TestCase):
    """`Store._serializable = False` 删了程序就会崩"""

    def test_serializable_flag_is_false(self):
        """
        pywebview 建窗口时会**递归遍历 js_api 对象上的所有公开属性**，
        找出要暴露给 JavaScript 的方法（见 webview/util.py）：
        下划线开头的跳过；带 `_serializable = False` 的跳过；其余全部钻进去。

        `Api.store` 是个**公开**属性，所以全靠这个开关让扫描器绕开它 ——
        否则它会钻进 Store 内部的引用链，可能撑爆 C 栈：
        进程硬崩 0xC0000409，日志里一个字都没有。
        """
        self.assertIs(
            Store._serializable, False,
            "Store._serializable 必须显式为 False，否则 pywebview 会遍历它",
        )


class TestWindowTitlesAreDistinct(unittest.TestCase):
    """三个窗口的标题必须互不相同"""

    def test_titles_are_pairwise_distinct(self):
        """
        拿窗口句柄走的是 `FindWindowW(None, 标题)`（见 winutil.py 开头的说明，
        因为访问 `window.native` 有无限递归的风险）。

        代价就是标题成了**唯一标识**：两个窗口重名，就会操作错窗口 ——
        而且是静默的，看起来只是「圆角没生效」或者「任务栏里多了一个按钮」。
        """
        titles = {
            "TITLE_MAIN": winutil.TITLE_MAIN,
            "TITLE_BALL": winutil.TITLE_BALL,
            "TITLE_TOAST": winutil.TITLE_TOAST,
        }
        self.assertEqual(
            len(titles), len(set(titles.values())),
            f"窗口标题重复了，FindWindowW 会找错窗口：{titles}",
        )
        for name, title in titles.items():
            self.assertTrue(title.strip(), f"{name} 是空的")


class TestApiExposesOnlyMethods(IsolatedApiTestCase):
    """
    `Api` 上的公开属性只能是「可调用的方法」「None」，或者**明确声明了
    `_serializable = False`** 的对象。

    ## 为什么

    pywebview 会把 `js_api` 对象的公开属性全部遍历一遍。所以**任何**公开的
    对象属性都有被它钻进去的风险。存一个 `Window` 进去的后果最严重：

        window.native.AccessibilityObject.Handle.Zero.Zero.Zero.Zero...

    这是一条**无限的属性链**（只在无边框窗口上无限），递归深度爆掉、
    C 栈撑破，进程以 0xC0000409 硬崩，**Python 连异常都来不及记**。

    而且症状极具误导性：「主窗口好好的，一加上悬浮球就崩」——
    因为普通窗口的 AccessibilityObject 链条会正常终止。

    ## 所以规矩是

      · 要桥给 JS 的东西 → 写成**方法**（方法不会被钻进去）
      · 框架对象、窗口引用 → **下划线开头**（扫描器跳过）
      · 万不得已的公开对象属性 → 必须带 `_serializable = False`
    """

    def test_no_public_attribute_holds_an_untraversable_object(self):
        api = Api()
        offenders: dict[str, str] = {}

        for name in dir(api):
            if name.startswith("_"):
                continue
            value = getattr(api, name)
            if callable(value) or value is None:
                continue
            # 明确声明了「别遍历我」的对象是允许的（Store 就是这么办的）
            if getattr(value, "_serializable", True) is False:
                continue
            offenders[name] = type(value).__name__

        self.assertEqual(
            {}, offenders,
            "Api 上有公开的属性存着对象，pywebview 会递归遍历它："
            f"{offenders}\n"
            "改成下划线开头，或者给那个类加 `_serializable = False`。",
        )

    def test_window_and_callback_slots_are_private(self):
        """
        窗口引用和回调槽必须以下划线开头，名字也不能改 ——
        它们是 `main.py` 注入时按名字找的（见 main.py 里那段接线代码）。
        """
        api = Api()
        expected = (
            "_window",
            "_ball_window",
            "_on_settings_changed",
            "_on_quit",
            "_on_show_main",
            "_on_ball_drag_end",
            "_on_ball_drag_start",
            "_on_ball_move",
            "_on_ball_hover",
            "_on_ball_slide_out",
        )
        for name in expected:
            self.assertTrue(hasattr(api, name), f"Api 少了 {name}")

        # 同时确认没有对应的公开版本冒出来（那会被 pywebview 扫到）
        for name in expected:
            public = name.lstrip("_")
            self.assertFalse(
                hasattr(Api, public),
                f"Api 上出现了公开的 {public}，它会被 pywebview 遍历",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
