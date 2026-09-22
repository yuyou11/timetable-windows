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

    def test_growing_while_moving_left_on_another_axis_still_moves_first(self):
        """
        横向「长大 + 左移」（不安全）与纵向「缩小 + 上移」（安全）混在一起 ——
        只要有一个方向不安全，就不能先 resize。
        """
        self.assertEqual(["move", "resize"],
                         order(100, 0, 300, 50, 200, 0, 200, 100))


if __name__ == "__main__":
    unittest.main()
