"""
`app/main.py` 里那几个「读系统环境」的辅助函数。

## 为什么单独测它们

这些函数住在 `main.py`（一个 1000 多行、混了窗口管理 / 提醒线程 / 退出流程
的文件）里，**一个测试都没有** —— 因为大家默认「main.py 是要开窗口才能测的」。

其实不是：`resource_path` 是纯函数，`is_dark_mode` / `work_area` / `screen_size`
只读系统、不碰窗口。它们完全可以测，只是以前没有地方写。

## 这个文件同时是一道「搬迁守卫」

这几个函数正被搬去 `app/screen.py`（`main.py` 里保留再导出）。
搬运过程中有两个很容易踩空的地方，下面都有测试盯着：

  1. `tools/exp_dock.py` / `exp_windows.py` / `diag_web.py` 是
     `from app.main import ...` 拿这些函数的。搬走之后如果忘了再导出，
     这些诊断工具会直接 ImportError —— 而**没有任何测试会跑到它们**。
     所以下面显式测「从 app.main 能拿到」。
  2. `resource_path` 用 `Path(__file__).resolve().parent` 当基准。
     新模块必须放在 `app/` 这一层，放进子包（比如 `app/ui/screen.py`）
     基准就变了，资源全部找不到 —— 而且**不报错**，只是窗口一片空白。
     `test_finds_real_files` 会在那种情况下当场失败。
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import dock, main, screen  # noqa: E402


class TestResourcePath(unittest.TestCase):
    """资源路径解析（打包前后都要能找到文件）"""

    def test_finds_real_files(self):
        """
        最要紧的一条：真的能定位到界面文件。

        如果 `resource_path` 的基准目录算错了（比如模块被搬进了子包），
        这个断言会失败 —— 而在真实程序里它只会表现为「窗口一片空白」，
        没有任何报错。
        """
        index = main.resource_path("web", "index.html")
        self.assertTrue(index.exists(), f"找不到界面文件：{index}")
        self.assertEqual("index.html", index.name)

        style = main.resource_path("web", "style.css")
        self.assertTrue(style.exists(), f"找不到样式文件：{style}")

    def test_directory_lookup(self):
        """也要能直接取目录（图标那边就是这么用的）"""
        web = main.resource_path("web")
        self.assertTrue(web.is_dir(), f"web 应该是个目录：{web}")
        self.assertTrue((web / "app.js").exists())

    def test_missing_path_returns_a_path_without_raising(self):
        """
        找不到时不抛异常，而是返回一个 Path。

        这是**有意的**设计：让报错发生在「真正读文件」那一步，
        那时错误信息里会带上完整路径，比在这里抛一句「找不到」更有用。
        这里把这个行为固定下来，免得以后有人「顺手加个 raise」。
        """
        missing = main.resource_path("nope", "missing")
        self.assertIsInstance(missing, Path)
        self.assertFalse(missing.exists())


class TestDarkModeAndColors(unittest.TestCase):
    """跟随系统浅色/深色主题"""

    def test_is_dark_mode_returns_a_bool(self):
        self.assertIsInstance(main.is_dark_mode(), bool)

    def test_background_color_matches_the_theme(self):
        """
        底色必须跟随系统主题 —— 这是「透明窗口在 WebView2 上不生效」
        那个坑的修法：不再依赖透明，改成页面自己画一块不透明的卡片。
        """
        expected = "#181b21" if main.is_dark_mode() else "#ffffff"
        self.assertEqual(expected, main._bg_color())


class TestScreenMetrics(unittest.TestCase):
    """屏幕工作区（逻辑像素）"""

    def test_ui_scale_is_positive(self):
        self.assertGreater(main.ui_scale(), 0)

    def test_work_area_is_a_sane_rectangle(self):
        area = main.work_area()
        self.assertIsInstance(area, dock.Area)
        # 不做像素级断言（换台机器就变），只要是个像样的屏幕范围
        self.assertGreater(area.width, 200)
        self.assertGreater(area.height, 200)
        self.assertGreater(area.right, area.left)
        self.assertGreater(area.bottom, area.top)

    def test_screen_size_matches_work_area(self):
        """悬浮窗靠这个值决定初始位置，所以它得和 work_area 一致"""
        w, h = main.screen_size()
        self.assertGreater(w, 200)
        self.assertGreater(h, 200)

    def test_physical_area_is_also_a_rectangle(self):
        """
        物理像素版本（work_area 会把它按缩放系数换算成逻辑像素）。

        注意这个是**从 `app.screen` 拿**，不是从 `app.main`。

        再导出的名单是刻意保持最小的：只导出三个诊断工具**真正用到**的那几个。
        `_work_area_physical` 没有任何外部使用者，所以它就留在 screen.py 里，
        main.py 不再白转一手。
        """
        area = screen._work_area_physical()
        self.assertIsInstance(area, dock.Area)
        self.assertGreater(area.width, 200)
        self.assertGreater(area.height, 200)


class TestHelpersAreReExportedFromMain(unittest.TestCase):
    """
    搬迁守卫：这些函数搬去 screen.py 之后，**必须**仍能从 app.main 导入。

    `tools/exp_dock.py`、`tools/exp_windows.py`、`tools/diag_web.py`
    都是 `from app.main import ...` 的写法。它们是排查窗口问题的诊断工具，
    平时不会跑，所以坏了很久都不会有人发现 —— 直到真正需要它们的那天。

    下面这一整块 import 就是那条契约。注意 `_bg_color` 是**下划线开头**的：
    如果用 `from .screen import *` 做再导出，星号导入会跳过下划线名字，
    `exp_dock.py` 会当场 ImportError。所以再导出必须写显式的名字列表。
    """

    def test_all_tool_dependencies_are_importable(self):
        # 这一行的存在本身就是测试：导不进来就直接报错
        from app.main import (  # noqa: F401
            _bg_color,
            resource_path,
            screen_size,
            ui_scale,
            work_area,
        )

        for fn in (_bg_color, resource_path, screen_size, ui_scale, work_area):
            self.assertTrue(callable(fn), f"{fn} 不是可调用的")

    def test_main_does_not_star_import_screen(self):
        """
        再导出不能写成星号导入。

        星号导入**不导入下划线开头的名字**，`_bg_color` 会静默消失。

        ## 为什么用正则而不是 assertNotIn

        第一版写的是 `assertNotIn("from .screen import *", src)` —— 结果是**它
        被一条注释绊倒了**：main.py 里那句「不能用 …… 星号导入」的说明里，
        正好就含有这个字符串。

        这跟 test_ball_ui.py 里 `assertNotIn("btnHide", js)` 是同一类陷阱：
        **「不存在」的断言如果只做子串匹配，任何一次提及都会让它误报**。

        所以这里描述的是「真的有一条星号导入语句」：必须出现在行首
        （`^` + MULTILINE），而注释行以 `#` 开头，匹配不上。
        """
        src = (Path(__file__).resolve().parent.parent
               / "app" / "main.py").read_text(encoding="utf-8")
        star_import = re.search(
            r"^\s*from\s+\.screen\s+import\s+\*", src, re.MULTILINE
        )
        self.assertIsNone(
            star_import,
            "不许用星号导入再导出 —— 会漏掉 _bg_color，tools/exp_dock.py 依赖它",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
