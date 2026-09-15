"""
边缘停靠的几何测试。

这部分逻辑最容易「看起来对、用起来别扭」：差几个像素、判错边、
夹取算反，表现都是「说不清哪里不对」。所以拿纯函数的形式测一遍。

    python -m unittest tests.test_dock -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import dock
from app.dock import Area, CARD_H, CARD_W, TAB_LONG, TAB_SHORT


#: 一块 1920×1040 的工作区（1080 的屏幕扣掉 40px 任务栏）
AREA = Area(0, 0, 1920, 1040)


class TestCollapsedSize(unittest.TestCase):
    def test_vertical_tab_for_side_edges(self):
        self.assertEqual((TAB_SHORT, TAB_LONG), dock.collapsed_size("left"))
        self.assertEqual((TAB_SHORT, TAB_LONG), dock.collapsed_size("right"))

    def test_horizontal_tab_for_top_bottom(self):
        self.assertEqual((TAB_LONG, TAB_SHORT), dock.collapsed_size("top"))
        self.assertEqual((TAB_LONG, TAB_SHORT), dock.collapsed_size("bottom"))


class TestComputeDock(unittest.TestCase):
    def test_near_each_edge(self):
        self.assertEqual("left", dock.compute_dock(0, 500, CARD_W, CARD_H, AREA))
        self.assertEqual("right", dock.compute_dock(1920 - CARD_W, 500, CARD_W, CARD_H, AREA))
        self.assertEqual("top", dock.compute_dock(800, 0, CARD_W, CARD_H, AREA))
        self.assertEqual("bottom", dock.compute_dock(800, 1040 - CARD_H, CARD_W, CARD_H, AREA))

    def test_far_from_everything_is_free(self):
        self.assertIsNone(dock.compute_dock(800, 500, CARD_W, CARD_H, AREA))

    def test_just_inside_margin_docks(self):
        self.assertEqual("left", dock.compute_dock(dock.DOCK_MARGIN, 500, CARD_W, CARD_H, AREA))

    def test_just_outside_margin_is_free(self):
        self.assertIsNone(
            dock.compute_dock(dock.DOCK_MARGIN + 1, 500, CARD_W, CARD_H, AREA)
        )

    def test_picks_the_nearest_edge(self):
        # 贴着左上角：离上边 5px、离左边 60px → 应该选上边
        self.assertEqual("top", dock.compute_dock(60, 5, CARD_W, CARD_H, AREA))
        # 反过来
        self.assertEqual("left", dock.compute_dock(5, 60, CARD_W, CARD_H, AREA))

    def test_uses_work_area_edges_not_screen_edges(self):
        """
        判断贴边用的是**工作区**的边，不是屏幕的边。

        任务栏在左边时工作区从 x=80 开始。如果错用了屏幕的 x=0，
        球停靠后会被任务栏压住。
        """
        shifted = Area(80, 0, 1920, 1040)

        # 贴住工作区左缘 → 停靠
        self.assertEqual("left", dock.compute_dock(80, 500, CARD_W, CARD_H, shifted))
        # 离开工作区左缘一点点 → 停靠
        self.assertEqual("left", dock.compute_dock(100, 500, CARD_W, CARD_H, shifted))
        # 超出容差 → 不停靠
        self.assertIsNone(dock.compute_dock(120, 500, CARD_W, CARD_H, shifted))

    def test_window_pushed_off_screen_still_docks(self):
        """
        窗口被拖到屏幕外面时，仍然算作贴着那个边 —— 然后被夹回工作区。

        一开始我在这里断言「应该返回 None」，跑出来是 'left'。
        想了下：**代码是对的，我的预期错了。**
        用户把卡片推到任务栏上甚至推到屏幕外，正确的反应是把它拉回来，
        而不是让它就那么漂在外面（那样就再也点不到了）。
        """
        shifted = Area(80, 0, 1920, 1040)
        self.assertEqual("left", dock.compute_dock(0, 500, CARD_W, CARD_H, shifted))
        self.assertEqual("left", dock.compute_dock(-500, 500, CARD_W, CARD_H, shifted))

        # 而且真能拉回来
        x, y, w, h = dock.docked_geometry("left", 500, shifted, collapsed=False)
        self.assertEqual(80, x)
        self.assertGreaterEqual(y, shifted.top)


class TestDockedGeometry(unittest.TestCase):
    def test_right_dock_collapsed_hugs_right_edge(self):
        x, y, w, h = dock.docked_geometry("right", 500, AREA, collapsed=True)
        self.assertEqual((TAB_SHORT, TAB_LONG), (w, h))
        self.assertEqual(AREA.right, x + w)          # 右缘贴住
        self.assertEqual(500, y + h // 2)            # 中线保住

    def test_right_dock_expanded_grows_leftward(self):
        x, y, w, h = dock.docked_geometry("right", 500, AREA, collapsed=False)
        self.assertEqual((CARD_W, CARD_H), (w, h))
        self.assertEqual(AREA.right, x + w)
        self.assertEqual(500, y + h // 2)
        # 展开后左缘应该在收起状态左缘的更左边（也就是向左长出来）
        cx, _, cw, _ = dock.docked_geometry("right", 500, AREA, collapsed=True)
        self.assertLess(x, cx)

    def test_left_dock_hugs_left_edge(self):
        for collapsed in (True, False):
            x, y, w, h = dock.docked_geometry("left", 500, AREA, collapsed=collapsed)
            self.assertEqual(AREA.left, x)
            self.assertEqual(500, y + h // 2)

    def test_bottom_dock_hugs_bottom_edge(self):
        for collapsed in (True, False):
            x, y, w, h = dock.docked_geometry("bottom", 900, AREA, collapsed=collapsed)
            self.assertEqual(AREA.bottom, y + h)
            self.assertEqual(900, x + w // 2)

    def test_top_dock_hugs_top_edge(self):
        for collapsed in (True, False):
            x, y, w, h = dock.docked_geometry("top", 900, AREA, collapsed=collapsed)
            self.assertEqual(AREA.top, y)
            self.assertEqual(900, x + w // 2)

    def test_unknown_edge_raises(self):
        with self.assertRaises(ValueError):
            dock.docked_geometry("middle", 500, AREA, collapsed=True)


class TestClamping(unittest.TestCase):
    def test_center_near_top_keeps_window_inside(self):
        # 中线在 y=10 处，卡片高 104 → 不夹取的话会跑到屏幕上方
        x, y, w, h = dock.docked_geometry("right", 10, AREA, collapsed=False)
        self.assertGreaterEqual(y, AREA.top)
        self.assertEqual(AREA.right, x + w)          # 贴边的那个方向不受影响

    def test_center_near_bottom_keeps_window_inside(self):
        x, y, w, h = dock.docked_geometry("right", 1035, AREA, collapsed=False)
        self.assertLessEqual(y + h, AREA.bottom)

    def test_top_dock_near_right_corner(self):
        x, y, w, h = dock.docked_geometry("top", 1910, AREA, collapsed=False)
        self.assertLessEqual(x + w, AREA.right)
        self.assertEqual(AREA.top, y)

    def test_clamp_never_exceeds_area(self):
        # 各种极端输入都不该把窗口挤出工作区
        for edge in dock.EDGES:
            for center in (-500, 0, 10, 500, 1039, 2000, 99999):
                for collapsed in (True, False):
                    x, y, w, h = dock.docked_geometry(edge, center, AREA, collapsed)
                    self.assertGreaterEqual(x, AREA.left, f"{edge}/{center}")
                    self.assertGreaterEqual(y, AREA.top, f"{edge}/{center}")
                    self.assertLessEqual(x + w, AREA.right, f"{edge}/{center}")
                    self.assertLessEqual(y + h, AREA.bottom, f"{edge}/{center}")


class TestStability(unittest.TestCase):
    """
    反复展开收起不能「漂移」。

    这是最容易出错的地方：如果每次都用「当前矩形」去算下一个位置，
    夹取带来的偏移会一次一次累积，球会自己慢慢爬到屏幕角落去。
    所以设计上要求每次都由**固定的中线**重新算。
    """

    def test_repeated_toggle_does_not_drift(self):
        for edge in dock.EDGES:
            center = 500
            first = None
            for i in range(20):
                collapsed = (i % 2 == 0)
                geo = dock.docked_geometry(edge, center, AREA, collapsed)
                if first is None and collapsed:
                    first = geo
                elif collapsed and first is not None:
                    self.assertEqual(first, geo, f"{edge} 第 {i} 次收起位置变了")
        # 展开状态同样要稳定
        for edge in dock.EDGES:
            positions = {dock.docked_geometry(edge, 500, AREA, False) for _ in range(10)}
            self.assertEqual(1, len(positions), f"{edge} 展开位置不稳定：{positions}")

    def test_center_is_preserved_across_toggle(self):
        # 从整理后的收起状态出发，取出中线，再展开、再收起 —— 应该回到原位
        for edge in dock.EDGES:
            x, y, w, h = dock.docked_geometry(edge, 700, AREA, collapsed=True)
            center = dock.center_of(edge, x, y, w, h)
            again = dock.docked_geometry(edge, center, AREA, collapsed=True)
            self.assertEqual((x, y, w, h), again, f"{edge} 往返后位置变了")


class TestCenterOf(unittest.TestCase):
    def test_vertical_center_for_side_edges(self):
        self.assertEqual(552, dock.center_of("left", 0, 500, CARD_W, CARD_H))
        self.assertEqual(552, dock.center_of("right", 1600, 500, CARD_W, CARD_H))

    def test_horizontal_center_for_horizontal_edges(self):
        self.assertEqual(922, dock.center_of("top", 800, 0, CARD_W, CARD_H))
        self.assertEqual(922, dock.center_of("bottom", 800, 936, CARD_W, CARD_H))


class TestCornerRadiusMatchesCss(unittest.TestCase):
    """
    圆角半径在 Python 和 CSS 里各写了一份，**必须相等**。

    ## 为什么不在两边都写死就算了

    因为这两份数据要配合使用、却没有任何机制保证它们一致：

        Python（dock.CORNER_RADIUS_*）→ Windows 按它裁窗口的角
        CSS（ball.html 的 --radius-*）→ 浏览器按它画那圈 1px 边框的角

    半径不一样的话，边角处会看到边框线被切掉一截，或者边框画进了
    已经被裁掉、根本显示不出来的区域里。而**这种错不会报任何错**，
    只是看起来「有点怪」，极难往回追。

    所以干脆让测试去读 CSS 文件核对 —— 改了一边忘了另一边，
    跑测试就会当场报出来。这也正是当初写下 CORNER_RADIUS_* 时
    在注释里承诺过的那条测试。
    """

    CSS = Path(__file__).resolve().parent.parent / "app" / "web" / "ball.html"

    def _css_var(self, name: str) -> int:
        """从 ball.html 里取出一个 --name: NNpx 的值"""
        text = self.CSS.read_text(encoding="utf-8")
        found = re.findall(rf"--{name}:\s*(\d+)px", text)
        self.assertTrue(found, f"ball.html 里找不到 --{name}")
        # 同名变量应该只有一处定义，多出来说明有重复，容易改漏
        self.assertEqual(1, len(found), f"--{name} 定义了 {len(found)} 次")
        return int(found[0])

    def test_card_radius_matches(self):
        self.assertEqual(
            dock.CORNER_RADIUS_CARD, self._css_var("radius-card"),
            "dock.CORNER_RADIUS_CARD 和 ball.html 的 --radius-card 不一致",
        )

    def test_tab_radius_matches(self):
        self.assertEqual(
            dock.CORNER_RADIUS_TAB, self._css_var("radius-tab"),
            "dock.CORNER_RADIUS_TAB 和 ball.html 的 --radius-tab 不一致",
        )

    def test_tab_radius_fits_in_the_tab(self):
        """
        收起的小方框只有 26px 宽，圆角半径不能超过它的一半。

        超过了并不会报错，只是圆角会被浏览器压扁成一个奇怪的形状
        （两边圆角加起来比宽度还大），看起来像坏掉了。
        """
        self.assertLess(dock.CORNER_RADIUS_TAB * 2, dock.TAB_SHORT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
