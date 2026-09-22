"""
悬浮窗上那几个按钮的行为。

## 为什么要单独测这个

因为这些逻辑**没法靠看界面发现问题**，而错了又很难往回追：

  · 「关闭」按钮点过一次会禁用自己（防连点）。而悬浮窗是**长期存在**的
    窗口，关掉只是 hide()，页面没被销毁 —— 所以再打开时那个禁用状态
    还在，表现是「悬浮窗关不掉了」，而且**没有任何报错，只是点了没反应**。
  · 恢复按钮的代码要放在**每一处**会让悬浮窗重新出现的地方。漏掉一个，
    就会留下「走某条路径打开后按钮是死的」这种间歇性问题。

所以这里盯的是「调用点有没有漏」，而不是按钮长什么样。

## 怎么测的

`main.App` 一构造就会去碰屏幕、窗口、托盘 —— 单测里不能真起一个 GUI。
但这里要验的又不是窗口本身，而是**「App 在某个时刻有没有让前端恢复按钮」**。

所以用一个假的窗口对象替换掉真的：它只记下 evaluate_js 被调了什么，
别的什么都不做。这样能把真实的那几段流程跑一遍，又不碰任何系统资源。
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeWindow:
    """假窗口：记下所有 evaluate_js 调用，其它一律不响应。

    ## ⚠️ move / resize 不是空函数，这是故意的

    真实的 pywebview 里，`move()` 和 `resize()` 内部都是 SetWindowPos，
    而传进去的 flag 带 **SWP_SHOWWINDOW** —— 也就是说
    **调一次几何就等于把窗口显示出来，哪怕之前 hide() 过。**

    （见 `webview/platforms/winforms.py` 的 `BrowserView.move` / `resize`，
    resize 那处甚至把裸数字 64 = 0x40 直接当 flag 传。）

    这个假窗口以前把 move/resize 写成 `pass`，于是它比真窗口「乖」——
    207 个测试全绿，却漏掉了「点关闭之后窗口自己冒回来」这个真实 bug。
    **假对象不模拟真实副作用，测试就只是在自我确认。**
    所以这里把副作用演出来：调了 move/resize，就当作窗口可见。
    """

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.js: list[str] = []
        self.shown = False
        self.hidden = False
        self.visible = False

    def evaluate_js(self, script: str):
        self.js.append(script)

    def show(self):
        self.shown = True
        self.visible = True

    def hide(self):
        self.hidden = True
        self.visible = False

    def move(self, *a):
        # 真实行为：SWP_SHOWWINDOW，会显示窗口
        self.visible = True

    def resize(self, *a):
        # 真实行为：flag 64 = SWP_SHOWWINDOW，会显示窗口
        self.visible = True

    def scripts(self) -> str:
        """所有执行过的 JS 拼在一起，方便做子串断言"""
        return "\n".join(self.js)


class BallButtonTestCase(unittest.TestCase):
    """
    共用的搭建/拆卸：造一个 App，但把窗口和托盘换成假的。

    注意 **不调 App.build()** —— 那才会真的创建窗口。
    这里手工塞一个假 ball 进去，直接测那几个「窗口已经存在之后」的流程。
    """

    def setUp(self):
        # ⚠️ 第一件事：把数据目录隔离到临时目录。**这一步不能省、也不能挪到后面。**
        #
        # 下面 make_app() 只桩掉了 `Store.load`，**没有桩 `Store.save`** ——
        # 而测试会调 `_on_settings_changed()` / `_toggle_ball()`，它们经由
        # store 的 setter 触发 `save()`，把内存里那份几乎空的 `_data`
        # **写进真实的 `%APPDATA%\Timetable\data.json`**。
        #
        # 这个坑真的踩过：跑完几轮测试之后，数据文件只剩下 28 字节的
        # `{"ball_enabled": true}` —— 课程、作息模板、学期设置全没了，
        # 而且**测试是绿的、没有任何提示**。这是最糟的一类 bug：
        # 它不破坏功能，它破坏数据。
        #
        # `Store` 是在**构造时**才去读 `TIMETABLE_DATA_DIR` 的
        # （见 store.py 的 data_dir()），所以在这里设就来得及。
        tmp = tempfile.mkdtemp(prefix="timetable_ballui_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        saved_data_dir = os.environ.get("TIMETABLE_DATA_DIR")
        os.environ["TIMETABLE_DATA_DIR"] = tmp

        def restore_data_dir():
            if saved_data_dir is None:
                os.environ.pop("TIMETABLE_DATA_DIR", None)
            else:
                os.environ["TIMETABLE_DATA_DIR"] = saved_data_dir

        self.addCleanup(restore_data_dir)

        # 这几个模块一导入就会牵连 pywebview（要初始化 WebView2 运行时），
        # 单测里没必要。用一个空的壳子顶替掉，App 只用到类本身。
        fake_webview = mock.MagicMock()
        patcher = mock.patch.dict(sys.modules, {"webview": fake_webview})
        patcher.start()
        self.addCleanup(patcher.stop)

        # 保证 app.main 是带着假 webview 重新导入的
        sys.modules.pop("app.main", None)

        from app import main as main_mod
        self.main_mod = main_mod

        # 自检：隔离**确认生效**了才继续。
        #
        # 宁可让测试在这里失败，也不能在隔离失效的情况下往下跑 ——
        # 那样它会安安静静地把用户的数据文件覆盖掉。
        # 以后如果有人改了 data_dir() 的取值方式、或者动了上面的顺序，
        # 会在这里当场发现。
        store_path = self.main_mod.Store().path
        self.assertTrue(
            str(store_path).startswith(tmp),
            f"数据目录没隔离成功：{store_path}\n"
            f"拒绝继续 —— 再往下跑会覆盖用户的真实数据文件。",
        )

    def make_app(self):
        """造一个没建过窗口的 App，再手工挂上假窗口"""
        # `save` 也一并桩掉：这些测试验的是「按钮恢复逻辑」，
        # 跟持久化无关。不桩的话每次改设置都会真写一次磁盘 ——
        # 平时只是慢，隔离失效时就是覆盖用户数据（见 setUp 的说明）。
        with mock.patch.object(self.main_mod.Store, "load", lambda self: None), \
             mock.patch.object(self.main_mod.Store, "save", lambda self: None), \
             mock.patch.object(self.main_mod, "Tray", return_value=None):
            app = self.main_mod.App()

        # 让「窗口是不是真的还在」这个判断返回 True。
        #
        # 测试里的 app.ball 是个假窗口，代表「窗口已经存在」。而
        # `_ball_alive()` 是去问**系统**要真实矩形的（winutil.window_rect）——
        # 单测里根本没有真窗口，不桩的话它一定返回 None，于是
        # `_ensure_ball()` 会判定「窗口已失效」并试图重建，把假窗口换掉，
        # 后面所有断言就都对着一个 MagicMock 了。
        #
        # 需要「窗口不存在」的场景，用例里自己再套一层 patch 覆盖掉。
        rect = mock.patch.object(
            self.main_mod.winutil, "window_rect", lambda title: (0, 0, 244, 104)
        )
        rect.start()
        self.addCleanup(rect.stop)

        app.ball = FakeWindow("ball")
        app.main = FakeWindow("main")
        app.toast = FakeWindow("toast")
        # 停靠状态：自由浮动，免得几何计算牵扯到真实屏幕
        app._ball_edge = None
        app._ball_x, app._ball_y = 100, 100
        app._ball_w, app._ball_h = self.main_mod.dock.CARD_W, self.main_mod.dock.CARD_H
        return app

    @staticmethod
    def fake_create_ball(app, calls):
        """
        造一个假的 `_create_ball`，但它**保留真实现的第一句 guard**。

        ⚠️ 为什么要复制那句 `if self.ball is not None: return`：

        假函数如果不模拟它，就会比真的「乖」—— 无条件重建 —— 于是
        「`_ensure_ball` 忘了先把失效引用清掉」这个 bug **测不出来**。

        这不是假设。第一版的假函数就是直接赋值，结果
        `tools/verify_external_close_guard.py` 的 M3 变异（把那行
        `self.ball = None` 删掉）**改坏了代码，测试却仍然是绿的**。

        和 `FakeWindow.move/resize` 模拟 `SWP_SHOWWINDOW` 是同一个道理：
        **假对象不模拟真实副作用，测试就只是在自我确认。**
        """
        def create(sw, sh):
            if app.ball is not None:
                return
            calls.append((sw, sh))
            app.ball = FakeWindow("rebuilt")

        return create


class TestCloseButtonReset(BallButtonTestCase):
    """「关闭」按钮的恢复逻辑"""

    def test_reset_asks_frontend_to_reenable(self):
        app = self.make_app()
        app._reset_ball_close_button()
        self.assertIn("ballShown", app.ball.scripts(),
                      "恢复按钮应该调用前端的 window.ballShown()")

    def test_settings_toggle_reset_the_button(self):
        """
        在设置里把悬浮窗关掉再打开，按钮必须是可用的。

        这是用户最可能走的那条路径：点了「关闭」之后想再要回来，
        就得到设置里去开 —— 如果那条路不恢复按钮，用户会觉得
        「这功能坏了，打开了也关不掉」。
        """
        app = self.make_app()
        app.store._data["ball_enabled"] = True

        app._on_settings_changed()

        self.assertTrue(app.ball.shown, "设置改成开启后应该显示悬浮窗")
        self.assertIn("ballShown", app.ball.scripts(),
                      "设置里重新打开悬浮窗之后，没有恢复「关闭」按钮")

    def test_tray_toggle_reset_the_button(self):
        """托盘菜单里切换显示，同样要恢复按钮（另一条会 show 的路径）"""
        app = self.make_app()
        app.store._data["ball_enabled"] = False      # 先关着，toggle 会打开它

        app._toggle_ball()

        self.assertTrue(app.ball.shown)
        self.assertIn("ballShown", app.ball.scripts(),
                      "托盘菜单重新打开悬浮窗之后，没有恢复「关闭」按钮")

    def test_reset_is_harmless_when_ball_is_gone(self):
        """
        悬浮窗被销毁（或还没建）时调它不能抛异常。

        这些恢复调用散落在几个「窗口可能不存在」的流程里，
        一旦抛异常就会被外面的 except 吞掉 —— 然后**后面所有代码都不执行了**，
        表现又变成「莫名其妙不生效」。所以它自己必须是安全的。
        """
        app = self.make_app()
        app.ball = None
        app._reset_ball_close_button()          # 不该抛

    def test_reset_survives_frontend_error(self):
        """前端 evaluate_js 失败也不能往外抛，理由同上"""
        app = self.make_app()
        app.ball.evaluate_js = mock.Mock(side_effect=RuntimeError("窗口没了"))
        app._reset_ball_close_button()          # 不该抛


class TestCloseButtonWiring(BallButtonTestCase):
    """前端那半边：按钮 id 和 window.ballShown 必须真的存在"""

    WEB = Path(__file__).resolve().parent.parent / "app" / "web"

    def test_ball_js_defines_ball_shown(self):
        """
        Python 会调 `window.ballShown()`，JS 里必须真有这个函数。

        这种跨语言调用**没有任何编译期检查**：名字打错一个字母，
        Python 照样调用成功，前端静默什么都不做。
        所以两边都对一遍。
        """
        js = (self.WEB / "ball.js").read_text(encoding="utf-8")
        self.assertIn("window.ballShown", js)

    def test_close_button_exists_in_html(self):
        """Python/JS 都在找 #btnClose，HTML 里必须有这个元素"""
        html = (self.WEB / "ball.html").read_text(encoding="utf-8")
        self.assertIn('id="btnClose"', html)

    def test_hide_button_is_gone(self):
        """
        旧的「隐藏」按钮应该已经全部换成「关闭」。

        留着它的话，两个按钮一个隐藏一个关闭，用户分不清区别 ——
        而它们的实际行为差别很大（隐藏不改设置，关闭会改设置）。
        """
        html = (self.WEB / "ball.html").read_text(encoding="utf-8")
        js = (self.WEB / "ball.js").read_text(encoding="utf-8")
        self.assertNotIn('id="btnHide"', html)
        self.assertNotIn("btnHide", js)


class TestBallStaysClosed(BallButtonTestCase):
    """点「关闭」之后，悬浮窗必须真的消失，而不是被几何更新又叫回来。

    ## 这条测试是怎么来的

    用户报的现象：点悬浮窗的「关闭」→ 窗口闪了一下没关掉 → 再点就没反应了；
    重启程序后悬浮窗又变成关闭状态。

    根因是 pywebview 的 `move()` / `resize()` 带 SWP_SHOWWINDOW——
    **调一次几何就等于把窗口显示出来**。而前端那条
    `mouseleave` → `ball_hover(false)` 的回调，恰好会在 hide() 之后跑一遍几何
    （窗口在光标底下消失，WebView2 会补发一次 mouseleave，380ms 后回调），
    于是窗口又冒出来了 —— 形态还变成了「收起的小方框」，
    那上面根本没有「关闭」按钮，所以用户「再点就没反应了」。
    """

    def test_close_then_mouse_leave_does_not_revive_the_ball(self):
        app = self.make_app()
        app.store._data["ball_enabled"] = True

        # 悬浮窗正在显示，并且是「贴右边停靠 + 卡片展开」——
        # 用户能点到「关闭」按钮，就说明当时卡片是展开的
        app.ball.show()
        app._ball_edge = "right"
        app._ball_center = 400
        app._ball_collapsed = False

        # 用户点了「关闭」（前端调 pywebview.api.hide_ball）
        app.api.hide_ball()
        self.assertFalse(app.ball.visible, "点了「关闭」之后窗口应该是藏起来的")

        # 窗口在光标底下消失 → WebView2 补发 mouseleave → 380ms 后前端回调。
        # 这一步是真实会发生的，它不能让窗口复活。
        app._ball_hover(False)

        self.assertFalse(
            app.ball.visible,
            "悬浮窗被「关闭」之后又自己显示出来了 —— "
            "几何更新必须避开已关闭的悬浮窗（pywebview 的 move/resize 会显示窗口）",
        )

    def test_drag_end_does_not_revive_the_ball_either(self):
        """另一条会改几何的路径：拖完松手。关掉之后同样不许把窗口弄回来。"""
        app = self.make_app()
        app.store._data["ball_enabled"] = False
        app._ball_edge = "right"
        app._ball_center = 400
        app.store._data["ball_pos"] = [100, 100]

        app._ball_drag_end()

        self.assertFalse(app.ball.visible, "已关闭的悬浮窗不该被拖动路径重新显示")

    def test_hover_does_not_touch_state_while_closed(self):
        """关掉之后，鼠标进出也不该改动停靠状态。

        只挡几何不挡状态的话，`_ball_collapsed` 会偷偷翻成 True 而尺寸没变 ——
        等用户在设置里重新打开悬浮窗，就会看到一个 244×104 的窗口里
        画着那张「收起的小方框」，状态和实际尺寸对不上。
        """
        app = self.make_app()
        app.store._data["ball_enabled"] = False
        app._ball_edge = "right"
        app._ball_center = 400
        app._ball_collapsed = False

        # ⚠️ 两次调用之间必须各断言一次。
        #
        # 这里踩过一个坑：最初写成「先 hover(False) 再 hover(True)，最后断言一次」，
        # 结果**恒真** —— 因为 True 分支里有 `if self._ball_collapsed:` 会把它
        # 翻回 False，两次调用正好互相抵消，断言不管有没有 bug 都通过。
        # 这种「因为错误的原因而通过」的测试比没有测试更糟：它给的是假的信心。
        # 是 tools/verify_ball_guard.py 把它照出来的。
        app._ball_hover(False)
        self.assertFalse(
            app._ball_collapsed,
            "鼠标离开时，已关闭的悬浮窗不该被改成「收起」状态",
        )

        app._ball_hover(True)
        self.assertFalse(
            app._ball_collapsed,
            "鼠标进入时，已关闭的悬浮窗也不该被改动收起状态",
        )


class TestSuppressExpand(BallButtonTestCase):
    """`_ball_suppress_expand` —— 压住「自动弹出」的那个标志。

    ## 这个标志为什么容易写错

    它只在**一处**被解除：`_ball_hover` 的「鼠标离开」分支。
    所以任何**无条件**把它设成 True 的写法都会造成永久压制 ——
    因为启动时光标通常离悬浮窗很远，页面既不发 mouseenter 也不发
    mouseleave，那个 True 就永远挂在那儿。

    用户报的现象正是这个：鼠标移上去没反应，点一下才弹出来
    （点击会走 `_ball_drag_start`，那条路会清掉标志）。

    所以这里的测试盯的是**「什么时候该压、什么时候不该压」**：
    判断依据是光标的实际位置，不是「刚启动」这个时间点。
    """

    def make_docked_app(self):
        """造一个「贴边停靠 + 已收起」的 App —— 就是启动时悬浮窗的样子"""
        app = self.make_app()
        app.store._data["ball_enabled"] = True
        app.ball.show()
        app._ball_edge = "right"
        app._ball_center = 600
        app._ball_collapsed = True
        return app

    def test_hover_expands_when_not_suppressed(self):
        """没被压制时，鼠标进来就应该展开成卡片（正常该有的行为）"""
        app = self.make_docked_app()
        app._ball_suppress_expand = False

        app._ball_hover(True)

        self.assertFalse(app._ball_collapsed, "鼠标移进来应该展开")
        self.assertEqual(
            (self.main_mod.dock.CARD_W, self.main_mod.dock.CARD_H),
            (app._ball_w, app._ball_h),
            "展开后尺寸应该是卡片尺寸",
        )

    def test_hover_is_swallowed_when_suppressed(self):
        """被压制时（光标本来就在窗口上），鼠标进来不该展开 —— 避免自弹"""
        app = self.make_docked_app()
        app._ball_suppress_expand = True

        app._ball_hover(True)

        self.assertTrue(app._ball_collapsed, "压制期间不该自己弹出来")

    def test_leaving_clears_the_suppress_flag(self):
        """
        鼠标离开是**唯一**的解除点。

        这条测试是为了说明「为什么不能无条件设 True」：
        解除依赖 mouseleave，而光标一直在窗口里或一直在外面时，
        mouseleave 都不会发生 —— 标志就卡住了。
        """
        app = self.make_docked_app()
        app._ball_suppress_expand = True

        app._ball_hover(False)

        self.assertFalse(app._ball_suppress_expand, "鼠标离开后应该解除压制")

    def test_drag_end_suppresses_only_when_cursor_is_over_the_ball(self):
        """
        拖动停靠之后**才**该压制 —— 而且要看光标的实际位置。

        两种子情况都要覆盖，因为「光标在不在窗口上」决定了是
        「刚缩进去就弹回来」（该压）还是「用户第一次移上去没反应」（不该压）。
        """
        # --- 光标压在悬浮窗上：应该压住 ---
        app = self.make_docked_app()
        app._ball_collapsed = False
        # 钉住「拖到了边缘」这个前提：真正算停靠与否的是 dock.compute_dock，
        # 那套几何在 test_dock.py 里有 23 条测试，这里不重复验它
        with mock.patch.object(self.main_mod.dock, "compute_dock",
                               return_value="right"), \
             mock.patch.object(app, "_cursor_over_ball", return_value=True):
            app._ball_drag_end()
        self.assertTrue(app._ball_suppress_expand, "光标在窗口上，应该压住自动弹出")

        # --- 光标在别处：不应该压 ---
        app = self.make_docked_app()
        app._ball_collapsed = False
        with mock.patch.object(self.main_mod.dock, "compute_dock",
                               return_value="right"), \
             mock.patch.object(app, "_cursor_over_ball", return_value=False):
            app._ball_drag_end()
        self.assertFalse(
            app._ball_suppress_expand,
            "光标不在窗口上时压住的话，用户的第一次悬停就白移了",
        )

    def test_create_ball_does_not_suppress_unconditionally(self):
        """
        反向护栏：`_create_ball` 里**不能**再出现无条件压制。

        这正是原来的 bug。改成「启动时一律不压、由 _polish 按光标位置决定」
        之后，这条断言把它钉住 —— 免得以后有人看到「刚启动先别弹」
        那句注释又把它加回来。
        """
        src = (Path(__file__).resolve().parent.parent / "app" / "main.py") \
            .read_text(encoding="utf-8")
        # 只看 _create_ball 那一小段
        start = src.index("def _create_ball(")
        end = src.index("def _apply_ball_geometry(")
        body = src[start:end]
        self.assertNotIn(
            "self._ball_suppress_expand = True", body,
            "_create_ball 里出现了无条件压制 —— 会让用户的第一次悬停失效",
        )


class TestCursorInWindow(unittest.TestCase):
    """`winutil.cursor_in_window` 的矩形判断。

    这个函数决定「压不压住自动弹出」，而它的输入是**物理像素**
    （GetWindowRect 和 GetCursorPos 都是），两边口径一致才能直接比。
    这里把两个来源都换成假的，测纯比较逻辑 —— 包括边界。
    """

    def setUp(self):
        from app import winutil
        self.winutil = winutil
        self.rect = (2400, 600, 2560, 800)

    def _check(self, cursor):
        with mock.patch.object(self.winutil, "window_rect", return_value=self.rect), \
             mock.patch.object(self.winutil, "cursor_pos", return_value=cursor):
            return self.winutil.cursor_in_window("whatever")

    def test_inside(self):
        self.assertTrue(self._check((2500, 700)))

    def test_outside_left_and_above(self):
        self.assertFalse(self._check((2399, 700)))
        self.assertFalse(self._check((2500, 599)))

    def test_outside_right_and_below(self):
        self.assertFalse(self._check((2560, 700)), "右边界是开区间")
        self.assertFalse(self._check((2500, 800)), "下边界是开区间")

    def test_top_left_corner_is_inside(self):
        self.assertTrue(self._check((2400, 600)), "左上角属于窗口内")

    def test_missing_information_means_do_not_suppress(self):
        """
        拿不到矩形或光标位置时返回 False（= 不压制）。

        这个方向的失败是安全的：最坏结果是多弹一次。
        反过来（拿不到就当压在窗口上）会让用户的第一次悬停永久失效，
        而这个项目已经栽过一次这种「静默压制」了。
        """
        with mock.patch.object(self.winutil, "window_rect", return_value=None), \
             mock.patch.object(self.winutil, "cursor_pos", return_value=(2500, 700)):
            self.assertFalse(self.winutil.cursor_in_window("x"))

        with mock.patch.object(self.winutil, "window_rect", return_value=self.rect), \
             mock.patch.object(self.winutil, "cursor_pos", return_value=None):
            self.assertFalse(self.winutil.cursor_in_window("x"))


class TestDockTween(unittest.TestCase):
    """收起 / 展开动画的中间帧几何（`dock.tween_docked_geometry`）。

    这是纯数学，所以在这里测；真正操作窗口的那部分（main._animate_ball）
    只能靠跑起来看 —— 那正是 tools/probe_ball_hover.py 之类的脚本干的事。
    """

    def setUp(self):
        from app import dock
        self.dock = dock
        self.area = dock.Area(0, 0, 1920, 1080)

    def test_progress_zero_equals_expanded(self):
        """progress=0 必须和「展开态」逐像素相同 —— 否则动画起点会跳一下"""
        got = self.dock.tween_docked_geometry("right", 500, self.area, 0.0)
        want = self.dock.docked_geometry("right", 500, self.area, False)
        self.assertEqual(got, want)

    def test_progress_one_equals_collapsed(self):
        """progress=1 必须和「收起态」逐像素相同 —— 否则收尾会跳一下"""
        got = self.dock.tween_docked_geometry("right", 500, self.area, 1.0)
        want = self.dock.docked_geometry("right", 500, self.area, True)
        self.assertEqual(got, want)

    def test_size_is_monotonic_for_every_edge(self):
        """四条边上，尺寸都要**一路减小**，不能中途反弹"""
        for edge in self.dock.EDGES:
            with self.subTest(edge=edge):
                widths, heights = [], []
                for i in range(11):
                    x, y, w, h = self.dock.tween_docked_geometry(
                        edge, 500, self.area, i / 10
                    )
                    widths.append(w)
                    heights.append(h)
                self.assertEqual(widths, sorted(widths, reverse=True),
                                 f"{edge}: 宽度不是单调递减的")
                self.assertEqual(heights, sorted(heights, reverse=True),
                                 f"{edge}: 高度不是单调递减的")

    def test_moving_edge_hugs_the_screen_edge(self):
        """
        动画过程中，**贴着屏幕那一侧不能动**。

        这是"看起来是收进边缘"而不是"原地缩小"的关键。
        比如贴右边，窗口右边缘要始终咬着工作区右边界。
        """
        for edge in self.dock.EDGES:
            with self.subTest(edge=edge):
                for i in range(11):
                    x, y, w, h = self.dock.tween_docked_geometry(
                        edge, 500, self.area, i / 10
                    )
                    if edge == "right":
                        self.assertEqual(x + w, self.area.right)
                    elif edge == "left":
                        self.assertEqual(x, self.area.left)
                    elif edge == "top":
                        self.assertEqual(y, self.area.top)
                    elif edge == "bottom":
                        self.assertEqual(y + h, self.area.bottom)

    def test_center_line_is_kept(self):
        """那条中线（贴左右边时的垂直中心）全程都要保住，否则会跳一下"""
        for edge in ("left", "right"):
            with self.subTest(edge=edge):
                for i in range(11):
                    x, y, w, h = self.dock.tween_docked_geometry(
                        edge, 500, self.area, i / 10
                    )
                    self.assertAlmostEqual(y + h / 2, 500, delta=1.0)

    def test_out_of_range_progress_is_clamped(self):
        """
        超出 [0,1] 的值要夹回来。

        缓动曲线算出 -0.0000001 或者 1.0000001 是很正常的事，
        不夹的话会出现"比卡片还大的窗口"或"负宽度"。
        """
        over = self.dock.tween_docked_geometry("right", 500, self.area, 1.8)
        at_one = self.dock.tween_docked_geometry("right", 500, self.area, 1.0)
        self.assertEqual(over, at_one)

        under = self.dock.tween_docked_geometry("right", 500, self.area, -0.5)
        at_zero = self.dock.tween_docked_geometry("right", 500, self.area, 0.0)
        self.assertEqual(under, at_zero)

    def test_midpoint_is_between_the_two_ends(self):
        """中间帧的尺寸要真的落在两端之间（防止插值写反）"""
        x, y, w, h = self.dock.tween_docked_geometry("right", 500, self.area, 0.5)
        cw, ch = self.dock.CARD_W, self.dock.CARD_H
        tw, th = self.dock.collapsed_size("right")
        self.assertTrue(tw < w < cw, f"宽度 {w} 不在 {tw} 和 {cw} 之间")
        self.assertTrue(th < h < ch, f"高度 {h} 不在 {th} 和 {ch} 之间")

    def test_unknown_edge_still_raises(self):
        """方向写错要当场报错，别静默算出一个乱位置"""
        with self.assertRaises(ValueError):
            self.dock.tween_docked_geometry("对角线", 500, self.area, 0.5)


class TestEaseOut(unittest.TestCase):
    """缓动曲线（`main._ease_out`）"""

    def setUp(self):
        from app import main
        self.ease = main._ease_out

    def test_endpoints(self):
        self.assertAlmostEqual(self.ease(0.0), 0.0)
        self.assertAlmostEqual(self.ease(1.0), 1.0)

    def test_is_monotonic(self):
        vals = [self.ease(i / 20) for i in range(21)]
        self.assertEqual(vals, sorted(vals))

    def test_is_front_loaded(self):
        """
        曲线上凸 = 开始快、结尾慢。

        这是 ease-out 的定义，也是选它的理由：匀速（=直线）收尾会显得"顿"，
        开头快、结尾贴上去才像被边缘吸进去。
        """
        self.assertGreater(self.ease(0.5), 0.5, "中点应该已经走完一大半")
        self.assertGreater(self.ease(0.25), 0.25)


class TestAnimTimingMatchesCss(BallButtonTestCase):
    """动画时长的**跨文件一致性**

    这是一个由两个进程、两套机制共同演出的动画：
    Python 逐帧改窗口尺寸，CSS 过渡淡出内容。两边各写了一份 160ms，
    而**没有任何机制保证它们相等** —— 改了一边忘了另一边，
    就会出现"窗口还在动、内容已经淡完了"或者反过来的错位。

    这个项目对同类问题（--radius-card）的做法是让测试直接读文件核对，
    这里沿用同一套办法。
    """

    WEB = Path(__file__).resolve().parent.parent / "app" / "web"

    def test_css_duration_equals_python_constant(self):
        html = (self.WEB / "ball.html").read_text(encoding="utf-8")
        m = re.search(r"--ball-anim-ms:\s*(\d+)ms", html)
        self.assertIsNotNone(m, "ball.html 里找不到 --ball-anim-ms")
        css_ms = int(m.group(1))
        self.assertEqual(
            css_ms, self.main_mod.BALL_ANIM_MS,
            f"ball.html 的 --ball-anim-ms 是 {css_ms}ms，"
            f"而 main.py 的 BALL_ANIM_MS 是 {self.main_mod.BALL_ANIM_MS}ms —— "
            f"窗口动画和内容淡出会对不上",
        )

    def test_animation_uses_that_variable(self):
        """
        过渡必须真的用这个变量，而不是另写一个数字。

        ⚠️ 这里只断言「用到了这个变量」，**不钉死乘数**（0.6 / 0.45）。
        乘数是拿抓帧截图调出来的，随时可能再调 —— 把它写进测试的话，
        每次微调观感都要改测试，测试就变成绊脚石了。
        要钉的是「和 --ball-anim-ms 挂钩」这件事本身。
        """
        html = (self.WEB / "ball.html").read_text(encoding="utf-8")
        start = html.index("#panel > * {")
        block = html[start:html.index("}", start)]
        self.assertIn("var(--ball-anim-ms)", block,
                      "内容淡出没有和 --ball-anim-ms 挂钩 —— "
                      "改动画时长时它会和窗口脱节")

    def test_ball_js_defines_ball_anim(self):
        """
        Python 会调 `window.ballAnim(edge, collapsing)`，JS 里必须真有它。

        跨语言调用没有任何编译期检查 —— 名字打错一个字母，
        Python 照样调用成功、前端静默什么都不做（动画就只剩窗口在动、
        内容不淡出）。所以两边都对一遍。
        """
        js = (self.WEB / "ball.js").read_text(encoding="utf-8")
        # ⚠️ 必须锚定成**定义**，不能只做子串匹配。
        #
        # 最初写的是 `assertIn("window.ballAnim", js)`，结果被
        # `window.ballAnimRenamed` 蒙混过关 —— 后者包含前者这个子串，
        # 而 Python 那边依然会去调一个不存在的名字。
        # （这是变异测试 tools/verify_anim_guards.py 照出来的，
        #  和项目里 `assertNotIn("from .screen import *")` 被注释误伤
        #  是同一类错误：**子串匹配描述不了"真正的语句"。**）
        self.assertRegex(
            js, r"window\.ballAnim\s*=\s*function",
            "ball.js 里没有定义 window.ballAnim（Python 会调它）",
        )

    def test_buttons_do_not_wrap(self):
        """
        按钮文字必须单行、溢出裁掉。

        这条是抓帧截图抓出来的问题（build-logs/shots/anim_collapse_440ms.png）：
        窗口收窄时按钮跟着变窄，文字被挤得竖着折行 ——
        「打开主窗口」变成「打开 / 主窗 / 口」，比不做动画还难看。
        """
        html = (self.WEB / "ball.html").read_text(encoding="utf-8")
        start = html.index(".row3 button {")
        block = html[start:html.index("}", start)]
        self.assertIn("white-space: nowrap", block,
                      "按钮没禁用换行 —— 窗口收窄时文字会竖着折行")
        self.assertIn("overflow: hidden", block,
                      "按钮没裁掉溢出 —— 会看到半截字挤在边框外")

    def test_collapse_fade_is_faster_than_expand_fade(self):
        """
        收起时的淡出必须**比展开时的淡入快**。

        为什么两边不能对称：窗口尺寸是三次方 ease-out（前段冲得快），
        而 CSS 的 ease-out 更平缓 —— 同一个时刻窗口已经走了 76%、
        内容才淡到 40%，于是文字被挤折行（截图见过）。
        所以收起方向要覆盖掉延迟并压缩时长。

        这条测试盯的是**那个覆盖还在不在**：如果哪天有人把
        `transition-duration` 那行删了（看着像冗余），
        折行问题会立刻回来，而且只有抓到那一帧才看得见。
        """
        html = (self.WEB / "ball.html").read_text(encoding="utf-8")
        start = html.index("body.ball-collapsing #panel > * {")
        block = html[start:html.index("}", start)]
        self.assertIn("transition-duration", block,
                      "收起方向没有压缩淡出时长 —— 文字会在窗口收窄时折行")
        self.assertIn("transition-delay: 0ms", block,
                      "收起方向没有清掉延迟 —— 会延迟才开始淡出，来不及")


class TestAnimationDriver(BallButtonTestCase):
    """`_animate_ball` 的调度逻辑（不真的动窗口 —— 假窗口会记下调用）"""

    def make_docked_app(self, collapsed=True):
        app = self.make_app()
        app.store._data["ball_enabled"] = True
        app.ball.show()
        app._ball_edge = "right"
        app._ball_center = 400
        app._ball_collapsed = collapsed
        app._ball_progress = 1.0 if collapsed else 0.0
        return app

    def test_starts_a_thread_and_tells_the_page(self):
        """启动时应该通知网页进入动画态（否则内容不会淡出）"""
        app = self.make_docked_app(collapsed=False)
        app._ball_collapsed = True
        with mock.patch.object(self.main_mod.dock, "tween_docked_geometry",
                               return_value=(100, 100, 244, 104)):
            app._animate_ball(1.0)
        # 动画在后台线程里，给它一点时间发出第一帧的通知
        for _ in range(50):
            if "ballAnim" in app.ball.scripts():
                break
            time.sleep(0.02)
        self.assertIn("ballAnim", app.ball.scripts(),
                      "没有通知网页进入动画态")

    def test_token_invalidates_an_in_flight_animation(self):
        """
        新动画一开，旧动画必须立刻作废。

        否则两个线程同时改同一个窗口，窗口会在两组坐标之间抽搐。
        这里用"令牌号变了"来验证 —— 旧线程下次检查时会发现号过期。
        """
        app = self.make_docked_app(collapsed=False)
        before = app._ball_anim_token
        app._animate_ball(1.0)
        self.assertGreater(app._ball_anim_token, before,
                           "起动画时应该把令牌 +1，让旧动画作废")

    def test_free_floating_is_not_animated(self):
        """
        自由浮动时没有"收起"这回事，不该动画 —— 应该立刻落位。

        这条防的是"动画逻辑漏判了 _ball_edge is None"：那样会在
        没有停靠边的情况下调 tween_docked_geometry，而那个函数收到
        edge=None 会抛 ValueError（线程里会静默死掉）。
        """
        app = self.make_app()
        app.store._data["ball_enabled"] = True
        app.ball.show()
        app._ball_edge = None            # 自由浮动
        app._ball_x, app._ball_y = 50, 60
        app._ball_collapsed = False

        app._apply_ball_geometry(animate=True)   # 不该抛、也不该起动画

        self.assertFalse(app._ball_collapsed,
                         "自由浮动时不该被改成收起态")

    def test_closed_ball_is_never_animated(self):
        """
        悬浮窗关着的时候，动画一步都不能走。

        这条是第一个 bug 的回归护栏：pywebview 的 resize()/move()
        **会把窗口显示出来**，所以一个还在跑的动画线程足以把刚隐藏的
        悬浮窗又弄回来。
        """
        app = self.make_docked_app(collapsed=False)
        app.store._data["ball_enabled"] = False
        app.ball.hide()

        app._apply_ball_geometry(animate=True)
        time.sleep(0.25)          # 足够动画跑完前几帧

        self.assertFalse(app.ball.visible,
                         "悬浮窗已关闭，动画却把窗口又显示出来了")


# ============================================================
#  悬浮窗出现在任务栏上 / 关掉之后再也唤不回来
# ============================================================

class TestWaitForWindow(unittest.TestCase):
    """
    等窗口出现，而不是睡一个固定时长。

    这是「悬浮窗出现在任务栏上」那个 bug 的修复本身。
    """

    def test_returns_as_soon_as_the_window_appears(self):
        """窗口一出现就返回，不用等满超时"""
        from app import winutil
        state = {"calls": 0}

        def fake_find(title):
            state["calls"] += 1
            return 12345 if state["calls"] >= 3 else 0

        with mock.patch.object(winutil, "_find", fake_find):
            hwnd = winutil.wait_for_window("随便什么标题", timeout=5.0,
                                           interval=0.001)

        self.assertEqual(12345, hwnd)
        self.assertEqual(3, state["calls"], "应该一找到就收手")

    def test_returns_zero_after_timeout(self):
        """一直找不到就等到超时再返回 0 —— 让调用方有机会把它报出来"""
        from app import winutil
        with mock.patch.object(winutil, "_find", lambda title: 0):
            start = time.monotonic()
            hwnd = winutil.wait_for_window("找不到的窗口", timeout=0.05,
                                           interval=0.01)
            elapsed = time.monotonic() - start

        self.assertEqual(0, hwnd)
        self.assertGreaterEqual(elapsed, 0.05, "超时之前不该提前返回")


class TestBallExternalClose(BallButtonTestCase):
    """
    悬浮窗被「外部」要求关闭时会发生什么（任务栏右键 → 关闭、Alt+F4）。

    ## 这个 bug 的两半

    **第一半：任务栏上本来不该有按钮。**
    窗口天生带着 WS_EX_APPWINDOW（实测 EX=0x00050008），要靠
    `make_tool_window` 把它摘掉。而那个调用原来排在「睡 0.9 秒之后」，
    窗口约 **0.80 秒**才出现 —— 余量只有 0.1 秒，冷启动时抢不过就
    **静默失败**，于是那个按钮永久留在任务栏上。
    修法是换成 `wait_for_window` 轮询（见 TestWaitForWindow）。

    **第二半：窗口一旦被销毁，就再也唤不回来。**
    因为悬浮窗窗口没有订阅 `closing` —— pywebview 的 `Event.set()` 在
    没有订阅者时返回 False，于是 `args.Cancel` 保持 False，**窗口被真的
    关掉了**。而 `App.ball` 这个引用还指着已销毁的窗口（**不是 None**），
    所以「`ball is None` 才重建」那条路永远走不到；每个入口都去调
    `show()`，在已 Dispose 的 Form 上抛异常，又被 `except: pass` 吞掉。
    用户看到的就是「点了没反应」，重启前无解。
    """

    def test_external_close_is_cancelled_and_becomes_hide(self):
        """
        外部关闭要被拦下来，并按程序的设计当成「关闭」（= 隐藏 + 关设置）。

        返回值的含义在 pywebview 里很容易看反：

            True  → 允许关闭
            False → 取消关闭
        """
        app = self.make_app()
        app.store._data["ball_enabled"] = True

        allowed = app._on_ball_closing()

        self.assertFalse(allowed,
                         "外部关闭应该被取消 —— 按设计只该隐藏，窗口要留着")
        self.assertTrue(app.ball.hidden, "应该把窗口藏起来")
        self.assertFalse(app.store.ball_enabled,
                         "「关闭」的语义是连设置一起关掉，否则重启它又冒出来了")

    def test_shutdown_is_still_allowed_to_close_it(self):
        """
        ⚠️ 退出流程必须放行 —— 否则程序永远退不掉。

        pywebview 的 `destroy_window()` 实现就是 `i.Close()`，**会再触发
        一次 FormClosing**。这里要是也无条件取消，`_shutdown()` 就收不了尾。
        """
        app = self.make_app()
        app.store._data["ball_enabled"] = True
        app._shutting_down = True

        self.assertTrue(app._on_ball_closing(),
                        "正在退出时必须允许关闭，否则 destroy() 会被自己拦下")

    def test_create_ball_subscribes_to_closing(self):
        """
        反向护栏：`_create_ball` 里必须订阅 closing。

        用锚定正则而不是子串 —— 子串会被 `events.closing_foo` 这类改写
        蒙混过关（项目里踩过：`assertIn("window.ballAnim", js)` 被
        `window.ballAnimRenamed` 骗过去了）。
        """
        src = Path(self.main_mod.__file__).read_text(encoding="utf-8")
        self.assertRegex(
            src,
            r"self\.ball\.events\.closing \+= self\._on_ball_closing",
            "悬浮窗没有订阅 closing —— 外部（任务栏/Alt+F4）能把它真的销毁掉",
        )

    def test_polish_waits_for_the_window_instead_of_sleeping(self):
        """
        反向护栏：`_polish` 里**不能**再出现「睡一个固定时长再去找窗口」。

        那正是任务栏按钮这个 bug 的根因：睡 0.9 秒，而窗口约 0.80 秒才
        出现 —— 余量只有 0.1 秒。冷启动时抢不过，按标题找窗口返回 0，
        `make_tool_window` 静默失败，WS_EX_APPWINDOW 就摘不掉了。

        钉住它，是因为「睡一会儿再试」这个写法**看起来特别合理**，
        很容易被下一个人顺手加回来。而它失败的时候**不报错**，
        只在用户的机器上偶尔发作 —— 开发机上几乎测不出来。
        """
        src = Path(self.main_mod.__file__).read_text(encoding="utf-8")
        start = src.index("def _polish(")
        end = src.index("def _apply_ball_geometry(")
        body = src[start:end]

        # ⚠️ 必须用**行锚定**的正则，不能写成 assertNotIn("time.sleep(0.9)")。
        #
        # 这条测试的第一版就是 assertNotIn，结果**在 baseline 上直接变红** ——
        # 因为上面那段注释里提到了「原来是 time.sleep(0.9) 硬等」，
        # 子串断言分不清「注释里提到」和「真的在调」。
        #
        # 这和 `window.ballAnimRenamed` 蒙混过 `assertIn("window.ballAnim")`
        # 是同一个坑：**「存在/不存在」型的子串断言，描述不了
        # 「它到底是不是一句可执行的语句」** —— 有 tests/test_ball_ui.py 里
        # 那条已经有言在先的教训，这里又栽了一次。
        self.assertNotRegex(
            body, r"(?m)^\s*time\.sleep\(0\.9\)\s*$",
            "_polish 又改回「睡固定时长」了 —— 窗口出现得比它晚时会静默失败",
        )
        self.assertRegex(
            body, r"(?m)^\s*ball_ready = bool\(winutil\.wait_for_window\(",
            "_polish 应该用 wait_for_window 等窗口真的出现，而不是睡一会儿再试",
        )

    def test_alive_asks_the_system_not_our_own_memory(self):
        """窗口在不在，要问系统，不能只看 self.ball is not None"""
        app = self.make_app()

        with mock.patch.object(self.main_mod.winutil, "window_rect",
                               return_value=None):
            self.assertFalse(app._ball_alive(),
                             "系统里找不到窗口时，就该认为它已经没了")

        with mock.patch.object(self.main_mod.winutil, "window_rect",
                               return_value=(0, 0, 244, 104)):
            self.assertTrue(app._ball_alive())

    def test_ensure_does_not_rebuild_while_the_window_is_alive(self):
        """窗口还在就不要重建 —— 重建会丢掉它的位置和停靠状态"""
        app = self.make_app()
        built = []
        app._create_ball = self.fake_create_ball(app, built)

        self.assertTrue(app._ensure_ball())
        self.assertEqual([], built, "窗口还在的时候不该重建")
        self.assertEqual("ball", app.ball.name, "原来的窗口对象要保持不变")

    def test_ensure_rebuilds_when_the_window_was_destroyed(self):
        """窗口没了要能重建，而不是对着一个死引用反复调 show()"""
        app = self.make_app()
        built = []
        app._create_ball = self.fake_create_ball(app, built)

        with mock.patch.object(self.main_mod.winutil, "window_rect",
                               return_value=None):
            ok = app._ensure_ball()

        self.assertTrue(ok)
        self.assertEqual(1, len(built), "窗口失效时必须重建一次")
        self.assertEqual("rebuilt", app.ball.name)

    def test_toggle_recovers_a_destroyed_window(self):
        """
        用户报的核心场景：窗口被外部销毁之后，还能不能重新叫回来。

        修复前：`self.ball` 不是 None → 不重建 → `self.ball.show()`
        抛异常 → 被 `except: pass` 吞掉 → 点了没反应，重启前无解。
        """
        app = self.make_app()
        app.store._data["ball_enabled"] = False    # 先关着，toggle 会打开它

        rebuilt = []
        app._create_ball = self.fake_create_ball(app, rebuilt)

        with mock.patch.object(self.main_mod.winutil, "window_rect",
                               return_value=None):   # 系统里已经没有这个窗口了
            app._toggle_ball()

        self.assertEqual(1, len(rebuilt),
                         "窗口已销毁时，切换开关应该把它重建出来")
        self.assertTrue(app.store.ball_enabled)
        self.assertTrue(app.ball.shown, "重建之后要真的显示出来")

    def test_settings_change_recovers_a_destroyed_window(self):
        """同一件事的另一条入口：设置页里打开悬浮窗"""
        app = self.make_app()
        app.store._data["ball_enabled"] = True

        rebuilt = []
        app._create_ball = self.fake_create_ball(app, rebuilt)

        with mock.patch.object(self.main_mod.winutil, "window_rect",
                               return_value=None):
            app._on_settings_changed()

        self.assertEqual(1, len(rebuilt), "设置里打开悬浮窗时也该把它重建出来")
        self.assertTrue(app.ball.shown)


class TestKindColorMatchesAcrossPages(unittest.TestCase):
    """
    KIND_COLOR 在主窗口和悬浮窗里各写了一份，值必须一模一样。

    ## 为什么要有这条测试

    前端没有模块系统（两个页面各自独立加载脚本），拿不到同一个常量 ——
    只能复制。而**复制的东西迟早漂移**，漂移了还看不出来：实测就漂过一次
    （CLASS 一边 #3b82f6、一边 #2563eb），于是同一门课在主窗口和悬浮窗上
    显示成两种颜色，一直没人发现。

    这条照着 `test_dock.py` 里核对圆角半径那条写：
    **两份独立写下的数据，就得配一条读文件逐项比对的测试接着。**
    """

    WEB = Path(__file__).resolve().parent.parent / "app" / "web"
    BLOCK_RE = re.compile(r"const KIND_COLOR = \{(.*?)\};", re.DOTALL)
    PAIR_RE = re.compile(r"([A-Z_]+):\s*'(#[0-9a-fA-F]{6})'")

    def _colors(self, filename: str) -> dict:
        text = (self.WEB / filename).read_text(encoding="utf-8")
        m = self.BLOCK_RE.search(text)
        self.assertIsNotNone(m, f"{filename} 里找不到 KIND_COLOR 定义")
        colors = dict(self.PAIR_RE.findall(m.group(1)))
        self.assertEqual(8, len(colors),
                         f"{filename} 的 KIND_COLOR 应该有 8 项，实际 {len(colors)} 项")
        return colors

    def test_app_js_and_ball_js_agree(self):
        app_colors = self._colors("app.js")
        ball_colors = self._colors("ball.js")
        self.assertEqual(
            app_colors, ball_colors,
            "app.js 和 ball.js 的 KIND_COLOR 漂移了 —— 同一个 kind 会在"
            "主窗口和悬浮窗上显示成两种颜色。改一边必须同步改另一边。",
        )

    def test_covers_every_kind(self):
        """8 种 kind 一个都不能漏 —— 漏了前端会 fallback 成灰色，很难发现"""
        from app.models import Kind
        self.assertEqual({k.value for k in Kind}, set(self._colors("app.js")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
