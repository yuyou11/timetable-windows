"""
悬浮窗落位的顺序规则 —— 先挪还是先缩。

## 为什么这么小的一件事值得一条测试

`move()` 和 `resize()` 是两次独立的跨线程调用，中间窗口会经过一个
「位置和尺寸不配套」的中间态。这个中间态一旦**超出新旧两个矩形的并集**，
多出来的那块就是一片没画过的窗口区域 —— 它的颜色和卡片没关系：

    深色主题   那块和卡片同色，看不出来
    浅色主题   卡片边上一条黑边（用户报的「弹出过程中有黑色拖尾」）

判据是：**挑那个中间态落在「新旧矩形的并集」里的顺序。**

    先 resize 再 move  出问题 ⟺ 某轴「在长大，而位置在朝负方向挪」
    先 move 再 resize  出问题 ⟺ 某轴「在缩小，而位置在朝正方向挪」

⚠️ 这条规则的坏味道在于：**写反了只在一半的情况下看得见。**
固定成「先 resize 再 move」的话，展开正常、收起漏黑边；反过来则相反。
所以钉在这儿。
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.main import place_window


class FakeWin:
    """只记下调用顺序的假窗口。"""

    def __init__(self):
        self.calls: list[str] = []

    def move(self, x, y):
        self.calls.append("move")

    def resize(self, w, h):
        self.calls.append("resize")


def order(x, y, w, h, px, py, pw, ph):
    win = FakeWin()
    place_window(win, x, y, w, h, px, py, pw, ph)
    return win.calls


class TestPlaceWindowOrder(unittest.TestCase):
    def test_expand_docked_right_moves_first(self):
        """
        贴右边展开：长大的同时向**左**挪。

        这是用户报的那个 bug 的形状 —— 先 resize 的话窗口还在旧 x 上，
        右边缘会临时多伸出一截没画过的区域（截图里黑的正好在右边）。
        """
        self.assertEqual(["move", "resize"],
                         order(100, 180, 244, 104, 300, 200, 26, 76))

    def test_expand_docked_left_moves_first(self):
        """贴左边展开：横向不动、纵向在长大且向上挪。同样要先挪。"""
        self.assertEqual(["move", "resize"],
                         order(0, 180, 244, 104, 0, 200, 26, 76))

    def test_collapse_docked_right_resizes_first(self):
        """
        贴右边收起：缩小的同时向**右**挪。

        和展开正好相反 —— 先 move 的话窗口会临时比原来还大。
        这就是「只把顺序调换一下」为什么不行：那样只是把黑边从展开挪到收起。
        """
        self.assertEqual(["resize", "move"],
                         order(300, 200, 26, 76, 100, 180, 244, 104))

    def test_pure_move_resizes_first(self):
        """拖动（尺寸不变）：两种顺序等价，行为稳定走「先缩」那条。"""
        self.assertEqual(["resize", "move"],
                         order(200, 200, 244, 104, 0, 0, 244, 104))

    def test_one_axis_grows_the_other_shrinks(self):
        """
        一轴变大、一轴变小：**不能**用「整体是长大还是缩小」来判断（那会判错），
        得逐轴看。这里横向在长大但没有左移，纵向在缩小但没有下移，
        所以「先缩」是安全的。
        """
        self.assertEqual(["resize", "move"],
                         order(0, 0, 300, 50, 0, 0, 200, 100))

    def test_atomic_setter_is_preferred_and_skips_both_calls(self):
        """
        传了 `atomic` 就该一次搞定，一次 move/resize 都不许再调。

        这才是治「弹出时露出桌面」的那一刀（见 winutil.set_window_rect）——
        下面那套顺序规则只是退路，它治不了「新长出来的区域本来就没画过」。
        """
        win = FakeWin()
        calls = []

        def atomic(x, y, w, h):
            calls.append((x, y, w, h))
            return True

        place_window(win, 100, 180, 244, 104, 300, 200, 26, 76, atomic=atomic)
        self.assertEqual([(100, 180, 244, 104)], calls)
        self.assertEqual([], win.calls, "atomic 成功之后不该再动 move/resize")

    def test_falls_back_when_atomic_reports_failure(self):
        """atomic 拿不到 HWND 返回 False 时，退回两次调用 + 顺序规则。"""
        win = FakeWin()
        place_window(win, 100, 180, 244, 104, 300, 200, 26, 76,
                     atomic=lambda *a: False)
        self.assertEqual(["move", "resize"], win.calls)

    def test_growing_while_moving_left_on_another_axis_still_moves_first(self):
        """
        横向「长大 + 左移」（不安全）与纵向「缩小 + 上移」（安全）混在一起 ——
        只要有一个方向不安全，就不能先 resize。
        """
        self.assertEqual(["move", "resize"],
                         order(100, 0, 300, 50, 200, 0, 200, 100))


class TestSetBallRectUnits(unittest.TestCase):
    """
    落位必须做「逻辑 → 物理」的换算。

    ## 为什么这条测试必须存在

    `dock` / `App._ball_x` 那一整套是**逻辑像素**，`SetWindowPos` 要的是
    **物理像素**（`run.py` 里声明了 DPI 感知）。两边直接接上就差一个缩放系数。

    这个 bug 的症状特别绕，靠肉眼几乎定位不到：

        平时          位置偏 1/4 屏、贴不上边，尺寸还小一圈
        拖动的时候    突然跳到正确位置（那条路走 pywebview，单位是对的）
        松手          又回到偏的位置

    「有时对有时错」的几何 bug，先怀疑**两条路径单位不一致**。
    """

    def test_converts_logical_to_physical(self):
        from app import main as main_mod

        captured = {}

        def fake_set_rect(hwnd, x, y, w, h):
            captured.update(hwnd=hwnd, x=x, y=y, w=w, h=h)
            return True

        app = main_mod.App.__new__(main_mod.App)   # 不跑 __init__，够用了
        app._ball_hwnd = 4242

        with mock.patch.object(main_mod.winutil, "set_window_rect", fake_set_rect), \
                mock.patch.object(main_mod, "ui_scale", lambda: 1.25):
            ok = app._set_ball_rect(100, 200, 244, 104)

        self.assertTrue(ok)
        self.assertEqual({"hwnd": 4242, "x": 125, "y": 250, "w": 305, "h": 130},
                         captured,
                         "逻辑像素没换算成物理像素就直接喂给 SetWindowPos 了")

    def test_no_hwnd_means_no_win32_call(self):
        """
        拿不到 HWND 时一次 Win32 都不许碰（测试绝不能动真实窗口）。

        ⚠️ 这里**不打桩** `set_window_rect` —— 第一版打了桩，然后指望
        `_set_ball_rect` 返回 False。可它只是把结果透传，桩子返回 True 它就
        返回 True。**打桩掉被测对象，等于什么都没测**，那条测试当时是假的。

        真正要盯的是 `winutil.set_window_rect` 里那道 `if not hwnd: return`
        守卫：它必须在碰任何 Win32 API 之前就收手。一旦它去找窗口/动窗口，
        同时开着的另一个实例就会被误伤（这个坑真实发生过）。
        """
        from app import main as main_mod
        from app import winutil

        self.assertFalse(winutil.set_window_rect(0, 1, 2, 3, 4),
                         "hwnd=0 时必须直接返回 False，不许调 SetWindowPos")

        app = main_mod.App.__new__(main_mod.App)
        app._ball_hwnd = 0
        self.assertFalse(app._set_ball_rect(1, 2, 3, 4))


if __name__ == "__main__":
    unittest.main()
