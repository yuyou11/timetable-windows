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
from app.dock import Area, SLIDE_W, WINDOW_H, WINDOW_W


#: 一块 1920×1040 的工作区（1080 的屏幕扣掉 40px 任务栏）
AREA = Area(0, 0, 1920, 1040)

#: 同一块屏幕的**显示器边界**（含任务栏占的那条）。
#: 收起滑出以它为准，展开落位以 AREA 为准 —— 两者在这组测试里故意不同，
#: 逼着实现把两个基准分清楚。
MONITOR = Area(0, 0, 1920, 1080)


class TestComputeDock(unittest.TestCase):
    def test_near_side_edges_docks(self):
        self.assertEqual("left", dock.compute_dock(0, 500, WINDOW_W, WINDOW_H, AREA))
        self.assertEqual(
            "right",
            dock.compute_dock(1920 - WINDOW_W, 500, WINDOW_W, WINDOW_H, AREA),
        )

    def test_top_bottom_no_longer_dock(self):
        """
        上下边已经取消停靠（dock.EDGES 只有左右）。

        「整窗滑出」的把手在窗口左右两侧，上下滑出是另一套几何；
        拖到上/下边缘松手就是自由浮动。
        """
        self.assertIsNone(dock.compute_dock(800, 0, WINDOW_W, WINDOW_H, AREA))
        self.assertIsNone(
            dock.compute_dock(800, 1040 - WINDOW_H, WINDOW_W, WINDOW_H, AREA)
        )

    def test_far_from_everything_is_free(self):
        self.assertIsNone(dock.compute_dock(800, 500, WINDOW_W, WINDOW_H, AREA))

    def test_just_inside_margin_docks(self):
        self.assertEqual(
            "left", dock.compute_dock(dock.DOCK_MARGIN, 500, WINDOW_W, WINDOW_H, AREA)
        )

    def test_just_outside_margin_is_free(self):
        self.assertIsNone(
            dock.compute_dock(dock.DOCK_MARGIN + 1, 500, WINDOW_W, WINDOW_H, AREA)
        )

    def test_picks_the_nearest_of_the_two_sides(self):
        # 离左边 20px、离右边一千多 → 左边
        self.assertEqual("left", dock.compute_dock(20, 5, WINDOW_W, WINDOW_H, AREA))
        # 反过来
        self.assertEqual(
            "right", dock.compute_dock(1920 - WINDOW_W - 20, 5, WINDOW_W, WINDOW_H, AREA)
        )

    def test_uses_work_area_edges_not_screen_edges(self):
        """
        判断贴边用的是**工作区**的边，不是屏幕的边。

        任务栏在左边时工作区从 x=80 开始。如果错用了屏幕的 x=0，
        卡片停靠后会被任务栏压住。
        """
        shifted = Area(80, 0, 1920, 1040)

        # 贴住工作区左缘 → 停靠
        self.assertEqual("left", dock.compute_dock(80, 500, WINDOW_W, WINDOW_H, shifted))
        # 离开工作区左缘一点点 → 停靠
        self.assertEqual("left", dock.compute_dock(100, 500, WINDOW_W, WINDOW_H, shifted))
        # 超出容差 → 不停靠
        self.assertIsNone(
            dock.compute_dock(120, 500, WINDOW_W, WINDOW_H, shifted)
        )

    def test_window_pushed_off_screen_still_docks(self):
        """
        窗口被拖到工作区外时，仍然算作贴着那个边。

        一开始我在这里断言「应该返回 None」，跑出来是 'left'。
        想了下：**代码是对的，我的预期错了。**
        用户把卡片推到工作区边上，正确的反应是按贴边处理（松手后滑出/收起），
        而不是让它就那么漂着。
        """
        shifted = Area(80, 0, 1920, 1040)
        self.assertEqual("left", dock.compute_dock(0, 500, WINDOW_W, WINDOW_H, shifted))
        self.assertEqual(
            "left", dock.compute_dock(-500, 500, WINDOW_W, WINDOW_H, shifted)
        )


class TestSlideGeometry(unittest.TestCase):
    """收起/展开两个静止端点的位置。尺寸必须**恒定**。"""

    def test_size_is_constant_in_both_states(self):
        for edge in dock.EDGES:
            for collapsed in (True, False):
                x, y, w, h = dock.slide_geometry(
                    edge, 500, AREA, MONITOR, collapsed
                )
                self.assertEqual((WINDOW_W, WINDOW_H), (w, h), f"{edge}/{collapsed}")

    def test_right_dock_collapsed_hides_all_but_the_handle(self):
        """
        贴右边收起：窗口滑出屏幕右缘，只留 SLIDE_W 宽的把手在屏幕内。

        把手滑出的基准是**显示器**边界，不是工作区 —— 任务栏停在右边时
        两者不同（见下面 side taskbar 的用例）。
        """
        x, y, w, h = dock.slide_geometry("right", 500, AREA, MONITOR, True)
        self.assertEqual(MONITOR.right - SLIDE_W, x)
        self.assertEqual(x + w, MONITOR.right + (WINDOW_W - SLIDE_W))  # 大部分在屏外
        self.assertEqual(500, y + h // 2)            # 中线保住

    def test_right_dock_expanded_shows_content_flush(self):
        """
        贴右边展开：滑到**内容区贴住工作区右缘**为止，不是整窗都进来。

        用户要求「不要全弹出，内容全露即可」—— 靠屏幕边的那条把手区
        留在屏外，整窗因此比工作区多探出去一个把手宽度；
        卡片本体（内容区）则完整落在工作区内、不压任务栏。
        """
        x, y, w, h = dock.slide_geometry("right", 500, AREA, MONITOR, False)
        self.assertEqual(AREA.right, x + w - SLIDE_W)   # 内容区右缘 = 工作区右缘
        self.assertEqual(AREA.right + SLIDE_W, x + w)   # 把手区悬出屏外
        self.assertGreaterEqual(x + SLIDE_W, AREA.left) # 内容区左缘也在屏内
        self.assertEqual(500, y + h // 2)               # 中线保住

    def test_left_dock_mirrors_right(self):
        cx, cy, cw, ch = dock.slide_geometry("left", 500, AREA, MONITOR, True)
        # 收起：往屏幕左缘外滑，只留页面最右 SLIDE_W 那条
        self.assertEqual(MONITOR.left - (WINDOW_W - SLIDE_W), cx)
        self.assertEqual(cw, MONITOR.left + SLIDE_W - cx)
        self.assertEqual(500, cy + ch // 2)

        x, y, w, h = dock.slide_geometry("left", 500, AREA, MONITOR, False)
        # 展开：内容区贴工作区左缘，靠边的把手区悬出屏外
        self.assertEqual(AREA.left - SLIDE_W, x)
        self.assertEqual(AREA.left, x + SLIDE_W)        # 内容区左缘 = 工作区左缘
        self.assertEqual(500, y + h // 2)

    def test_collapsed_actually_leaves_the_screen(self):
        """收起态必须真的出屏，否则「滑出」就是空谈。"""
        rx, _, rw, _ = dock.slide_geometry("right", 500, AREA, MONITOR, True)
        self.assertEqual(rx + SLIDE_W, MONITOR.right)                  # 把手右缘 = 屏幕右缘
        self.assertGreater(rx + rw - SLIDE_W, MONITOR.right)           # 卡片主体全在屏外

        lx, _, lw, _ = dock.slide_geometry("left", 500, AREA, MONITOR, True)
        self.assertEqual(lx + lw - SLIDE_W, MONITOR.left)              # 把手左缘 = 屏幕左缘
        self.assertLess(lx, MONITOR.left)                              # 窗口左缘出了屏

    def test_side_taskbar_slide_target_uses_monitor(self):
        """
        任务栏在右边时：工作区右缘 1880，屏幕右缘 1920。

        展开的内容区贴 1880（不压任务栏），收起滑到 1920 − SLIDE_W
        （卡片整个出**屏幕**，压在任务栏上的只剩把手那条）。
        用错基准的症状：整张卡片明晃晃地盖在任务栏上。
        """
        area = Area(0, 0, 1880, 1040)
        x, _, w, _ = dock.slide_geometry("right", 500, area, MONITOR, False)
        self.assertEqual(area.right, x + w - SLIDE_W)
        cx, _, _, _ = dock.slide_geometry("right", 500, area, MONITOR, True)
        self.assertEqual(MONITOR.right - SLIDE_W, cx)

    def test_center_near_top_keeps_window_inside_vertically(self):
        # 中线在 y=10 处，窗口高 104 → 不夹取的话会跑到屏幕上方
        x, y, w, h = dock.slide_geometry("right", 10, AREA, MONITOR, False)
        self.assertGreaterEqual(y, AREA.top)
        self.assertEqual(AREA.right, x + w - SLIDE_W)   # 贴边的那个方向不受影响

    def test_center_near_bottom_keeps_window_inside_vertically(self):
        x, y, w, h = dock.slide_geometry("right", 1035, AREA, MONITOR, False)
        self.assertLessEqual(y + h, AREA.bottom)

    def test_x_is_never_clamped(self):
        """
        x **不能**被夹回工作区 —— 收起态的 x 就是要出屏，这正是设计。

        旧版「任何部分都不许出屏」的 clamp 如果误用到这里，
        收起态会变成「整窗留在屏内」，滑出功能整个失效。
        就算 y 被夹取（中线贴近屏幕角），x 也必须照样出屏。
        """
        for edge in dock.EDGES:
            x, _, w, _ = dock.slide_geometry(edge, 10, AREA, MONITOR, True)
            if edge == "right":
                self.assertGreater(x + w, MONITOR.right)
            else:
                self.assertLess(x, MONITOR.left)

    def test_unknown_edge_raises(self):
        with self.assertRaises(ValueError):
            dock.slide_geometry("top", 500, AREA, MONITOR, True)
        with self.assertRaises(ValueError):
            dock.slide_geometry("middle", 500, AREA, MONITOR, False)


class TestTweenSlide(unittest.TestCase):
    """收起/展开动画的中间帧：两个 x 之间的纯线性插值。"""

    def test_endpoints_match_slide_geometry(self):
        for edge in dock.EDGES:
            got = dock.tween_slide(edge, 500, AREA, MONITOR, 0.0)
            want = dock.slide_geometry(edge, 500, AREA, MONITOR, False)
            self.assertEqual(want, got, f"{edge} 展开端点对不上")

            got = dock.tween_slide(edge, 500, AREA, MONITOR, 1.0)
            want = dock.slide_geometry(edge, 500, AREA, MONITOR, True)
            self.assertEqual(want, got, f"{edge} 收起端点对不上")

    def test_size_and_y_constant_across_the_whole_tween(self):
        """动画全程窗口尺寸和 y 都不许变 —— 变了就说明又在 resize 了。"""
        for edge in dock.EDGES:
            frames = [
                dock.tween_slide(edge, 500, AREA, MONITOR, i / 16)
                for i in range(17)
            ]
            self.assertEqual(1, len({(w, h) for _, _, w, h in frames}),
                             f"{edge} 动画中尺寸变了")
            self.assertEqual(1, len({y for _, y, _, _ in frames}),
                             f"{edge} 动画中 y 变了")

    def test_x_moves_monotonically_toward_the_target(self):
        # 贴右边：收起 x（monitor.right − SLIDE_W）比展开 x 大 → 一路变大
        x0, _, _, _ = dock.tween_slide("right", 500, AREA, MONITOR, 0.0)
        x1, _, _, _ = dock.tween_slide("right", 500, AREA, MONITOR, 1.0)
        self.assertGreater(x1, x0)
        xs = [dock.tween_slide("right", 500, AREA, MONITOR, i / 16)[0]
              for i in range(17)]
        self.assertEqual(xs, sorted(xs))

        # 贴左边镜像：收起方向一路变小
        x0, _, _, _ = dock.tween_slide("left", 500, AREA, MONITOR, 0.0)
        x1, _, _, _ = dock.tween_slide("left", 500, AREA, MONITOR, 1.0)
        self.assertLess(x1, x0)
        xs = [dock.tween_slide("left", 500, AREA, MONITOR, i / 16)[0]
              for i in range(17)]
        self.assertEqual(xs, sorted(xs, reverse=True))

    def test_out_of_range_progress_is_clamped(self):
        over = dock.tween_slide("right", 500, AREA, MONITOR, 1.8)
        at_one = dock.tween_slide("right", 500, AREA, MONITOR, 1.0)
        self.assertEqual(at_one, over)

        under = dock.tween_slide("right", 500, AREA, MONITOR, -0.5)
        at_zero = dock.tween_slide("right", 500, AREA, MONITOR, 0.0)
        self.assertEqual(at_zero, under)

    def test_unknown_edge_raises(self):
        with self.assertRaises(ValueError):
            dock.tween_slide("对角线", 500, AREA, MONITOR, 0.5)


class TestClamping(unittest.TestCase):
    """clamp_to_area 是纯矩形夹取的底层；悬浮窗走的是上面的 clamp_free。"""

    def test_clamp_never_exceeds_area(self):
        # 各种极端输入都不该把矩形挤出工作区
        for x, y in ((-500, -500), (0, 0), (10, 10), (500, 500),
                     (1899, 1039), (2000, 2000), (99999, 99999)):
            nx, ny, _, _ = dock.clamp_to_area(
                x, y, WINDOW_W, WINDOW_H, AREA
            )
            self.assertGreaterEqual(nx, AREA.left, f"{x},{y}")
            self.assertGreaterEqual(ny, AREA.top, f"{x},{y}")
            self.assertLessEqual(nx + WINDOW_W, AREA.right, f"{x},{y}")
            self.assertLessEqual(ny + WINDOW_H, AREA.bottom, f"{x},{y}")

    def test_clamp_keeps_size(self):
        nx, ny, w, h = dock.clamp_to_area(-50, -50, WINDOW_W, WINDOW_H, AREA)
        self.assertEqual((WINDOW_W, WINDOW_H), (w, h))


class TestClampFree(unittest.TestCase):
    """
    拖动 / 自由浮动的夹取按**内容区**语义来：内容不许出工作区，
    把手区允许悬出 SLIDE_W。

    这个语义是跟着「展开 = 内容贴边」来的 —— 按整窗夹的话，
    从展开停靠位起拖的第一下会被拽回 SLIDE_W，表现为拖动起手一跳。
    """

    def test_docked_expanded_position_passes_through_unchanged(self):
        # 展开停靠位必须被原样放行，否则拖动起手会跳
        x, y, _, _ = dock.slide_geometry("right", 500, AREA, MONITOR, False)
        nx, ny = dock.clamp_free(x, y, AREA)
        self.assertEqual((x, y), (nx, ny))

        x, y, _, _ = dock.slide_geometry("left", 500, AREA, MONITOR, False)
        nx, ny = dock.clamp_free(x, y, AREA)
        self.assertEqual((x, y), (nx, ny))

    def test_content_region_stays_inside(self):
        # 推到极限：内容区右缘贴住工作区右缘，整窗只悬出一个把手宽度
        nx, _ = dock.clamp_free(99999, 500, AREA)
        self.assertEqual(AREA.right + SLIDE_W, nx + WINDOW_W)
        self.assertEqual(AREA.right, nx + WINDOW_W - SLIDE_W)

        # 左边镜像
        nx, _ = dock.clamp_free(-99999, 500, AREA)
        self.assertEqual(AREA.left - SLIDE_W, nx)
        self.assertEqual(AREA.left, nx + SLIDE_W)

    def test_y_is_clamped_fully(self):
        # 垂直方向没有把手区，整窗高度都得留在工作区内
        self.assertEqual(AREA.top, dock.clamp_free(100, -9999, AREA)[1])
        self.assertEqual(
            AREA.bottom - WINDOW_H, dock.clamp_free(100, 99999, AREA)[1]
        )


class TestStability(unittest.TestCase):
    """
    反复展开收起不能「漂移」。

    这是最容易出错的地方：如果每次都用「当前矩形」去算下一个位置，
    夹取带来的偏移会一次一次累积，卡片会自己慢慢爬到屏幕角落去。
    所以设计上要求每次都由**固定的中线**重新算。
    """

    def test_repeated_toggle_does_not_drift(self):
        for edge in dock.EDGES:
            center = 500
            first = None
            for i in range(20):
                collapsed = (i % 2 == 0)
                geo = dock.slide_geometry(edge, center, AREA, MONITOR, collapsed)
                if first is None and collapsed:
                    first = geo
                elif collapsed and first is not None:
                    self.assertEqual(first, geo, f"{edge} 第 {i} 次收起位置变了")
        # 展开状态同样要稳定
        for edge in dock.EDGES:
            positions = {
                dock.slide_geometry(edge, 500, AREA, MONITOR, False)
                for _ in range(10)
            }
            self.assertEqual(1, len(positions), f"{edge} 展开位置不稳定：{positions}")

    def test_center_is_preserved_across_toggle(self):
        # 从收起状态出发，取出中线，再收起 —— 应该回到原位
        for edge in dock.EDGES:
            x, y, w, h = dock.slide_geometry(edge, 700, AREA, MONITOR, True)
            center = dock.center_of(edge, x, y, w, h)
            again = dock.slide_geometry(edge, center, AREA, MONITOR, True)
            self.assertEqual((x, y, w, h), again, f"{edge} 往返后位置变了")


class TestCenterOf(unittest.TestCase):
    def test_vertical_center_for_side_edges(self):
        self.assertEqual(552, dock.center_of("left", 0, 500, WINDOW_W, WINDOW_H))
        self.assertEqual(552, dock.center_of("right", 1600, 500, WINDOW_W, WINDOW_H))


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

    def test_tab_radius_fits_in_the_handle(self):
        """
        露出的把手只有 13px 宽，圆角半径不能超过它的一半。

        超过了并不会报错，只是圆角会被浏览器等比压扁
        （两边圆角加起来比宽度还大），和窗口/卡片的圆角对不上观感。
        """
        self.assertLess(dock.CORNER_RADIUS_TAB * 2, dock.SLIDE_W)


class TestBallPageContract(unittest.TestCase):
    """
    窗口几何和页面布局是**两份独立写下的数据**，靠这条测试对账。

    窗口固定 WINDOW_W × WINDOW_H；页面必须正好填满它，并且给左右把手
    留出 SLIDE_W —— 对不上的话，收起时露出来的就不是干净的空白区，
    而是切到一半的文字或一截说不清的空白。
    """

    CSS = Path(__file__).resolve().parent.parent / "app" / "web" / "ball.html"

    def test_panel_padding_reserves_the_handle_zones(self):
        """
        #panel 的左右 padding 必须 = SLIDE_W + 11。

        11 是内容列原本的留白；加上 SLIDE_W 之后，内容列宽和旧版
        244 宽的卡片完全一致 —— 「变宽」的只是把手区。
        """
        text = self.CSS.read_text(encoding="utf-8")
        found = re.findall(r"padding:\s*9px\s+(\d+)px;", text)
        self.assertTrue(found, "ball.html 里找不到 #panel 的 padding")
        self.assertEqual(1, len(found), "#panel 的 padding 定义了多次")
        self.assertEqual(dock.SLIDE_W + 11, int(found[0]),
                         "padding 左右值和 SLIDE_W + 11 对不上")

    def test_arrow_overlay_sits_on_the_exposed_side(self):
        """
        贴右收起露的是页面**左侧**那条（窗口往右滑出屏幕），
        贴左收起露的是右侧 —— 覆盖层必须放在对应的那一侧。

        放反了的症状：收起后箭头在屏幕外，露出来的是一条空白。
        """
        text = self.CSS.read_text(encoding="utf-8")
        self.assertIn("body.dock-right #tab { left: 1px; }", text)
        self.assertIn("body.dock-left  #tab { right: 1px; }", text)

    def test_handle_overlay_is_one_pixel_narrower_than_the_slide(self):
        """
        覆盖层宽度必须 = SLIDE_W − 1：那 1px 是给面板边框让位的，
        盖住边框的话 hover 高亮会把手柄处的边框线吃掉。
        """
        text = self.CSS.read_text(encoding="utf-8")
        m = re.search(r"#tab \{[^}]*width:\s*(\d+)px", text, re.S)
        self.assertIsNotNone(m, "ball.html 的 #tab 里找不到 width")
        self.assertEqual(
            dock.SLIDE_W - 1, int(m.group(1)),
            "#tab 覆盖层宽度和 SLIDE_W − 1 对不上",
        )

    def test_no_top_bottom_rules_left(self):
        """上下边停靠已取消，残留的 dock-top/dock-bottom 样式应该清干净。"""
        text = self.CSS.read_text(encoding="utf-8")
        for leftover in ("dock-top", "dock-bottom"):
            self.assertNotIn(leftover, text, f"ball.html 还残留 {leftover} 样式")

    def test_panel_is_always_visible(self):
        """
        #panel 不许再有 display:none。

        固定尺寸方案的前提是「内容永远完整渲染」；把面板藏起来，
        收起态露出的把手就没有底色了。
        """
        text = self.CSS.read_text(encoding="utf-8")
        self.assertNotIn("display: none", text.split("#tab {")[0].split("#panel {")[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
