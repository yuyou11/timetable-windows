"""
课表网格：`api.get_week()` 返回的结构必须覆盖**整周七天**。

## 这个文件是为什么建的

用户导入新课表后反馈「周六周日的课都无法显示」。查下来是
`get_week` 里写死了 `range(1, 6)` —— 只生成周一到周五，
周六、周日的格子**根本没造出来**。

而引擎和提醒调度一直是支持周末的，所以那些课其实会上、也会提醒，
**只有课表页看不见**。用户看到一张"少了课"的表，
很难判断是没导入成功还是程序不显示。

这个 bug 藏了很久，因为**原来的课表恰好全在周一到周五** ——
直到导入了一份有周末课的表才暴露。所以这里必须有一条测试盯着。

## 为什么不用截图去测

截图测不出这种东西：它只能证明「我截的那一屏看着对」，
证明不了「七天都在」「每行格子数对」。而且一改样式截图就全失效。
结构化断言既准确又稳定。

## 隔离

会构造真实的 Store，所以必须把数据目录指到临时目录 ——
这个项目真的发生过「测试把用户课表覆盖成 28 字节」的事故。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import slots  # noqa: E402
from app.models import Course  # noqa: E402

#: 一周七天的列标签（周一=1 … 周日=7）
EXPECTED_LABELS = ["一", "二", "三", "四", "五", "六", "日"]


class WeekGridTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="timetable_weekgrid_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        saved = os.environ.get("TIMETABLE_DATA_DIR")
        os.environ["TIMETABLE_DATA_DIR"] = tmp
        self.addCleanup(self._restore_data_dir, saved)

        from app.api import Api
        from app.store import Store

        self.store = Store()
        # 隔离没生效就拒绝往下跑 —— 否则会覆盖用户的真实数据文件
        self.assertTrue(
            str(self.store.path).startswith(tmp),
            f"数据目录没隔离成功：{self.store.path}",
        )
        self.api = Api(store=self.store)

    @staticmethod
    def _restore_data_dir(saved):
        if saved is None:
            os.environ.pop("TIMETABLE_DATA_DIR", None)
        else:
            os.environ["TIMETABLE_DATA_DIR"] = saved

    def set_courses(self, courses):
        self.store.save_courses(courses)

    def week_with_weekend_courses(self):
        """
        一份**第 5 周周六、第 2 周周日**有课的课表 —— 就是用户遇到的那张。

        周一到周五全部空着，这样「周末有没有显示出来」一目了然。
        """
        self.set_courses([
            Course("周六的课", 6, 1, 2, frozenset({5}), "S-101"),
            Course("周日的课", 7, 3, 4, frozenset({2}), "U-202"),
        ])
        return self.api.get_week(0)


class TestSevenDayColumns(WeekGridTestCase):
    """七天都要有 —— 这是那个 bug 的直接回归测试"""

    def test_returns_seven_days(self):
        w = self.api.get_week(0)
        self.assertEqual(
            7, len(w["days"]),
            f"课表应当有 7 天（周一…周日），实际只有 {len(w['days'])} 天。\n"
            f"以前这里写死 range(1, 6)，结果周六周日的课完全不显示。",
        )

    def test_day_labels_are_monday_through_sunday(self):
        w = self.api.get_week(0)
        labels = [d["label"] for d in w["days"]]
        self.assertEqual(EXPECTED_LABELS, labels)

    def test_dow_is_one_through_seven(self):
        w = self.api.get_week(0)
        self.assertEqual([1, 2, 3, 4, 5, 6, 7], [d["dow"] for d in w["days"]])

    def test_weekend_has_its_own_day_type(self):
        """
        周六周日必须用各自的日型模板，而不是被当成普通工作日。

        这条保证「周末」不只是多了两列空格子 —— 它的作息、
        起床时间都该按 SATURDAY / SUNDAY 那两套走。
        """
        w = self.api.get_week(0)
        self.assertEqual("SATURDAY", w["days"][5]["dayType"])
        self.assertEqual("SUNDAY", w["days"][6]["dayType"])


class TestRowCellAlignment(WeekGridTestCase):
    """表头几列，每行就得几格 —— 否则表格会错位"""

    def test_every_row_has_one_cell_per_day(self):
        w = self.api.get_week(0)
        n_days = len(w["days"])
        for r in w["rows"]:
            self.assertEqual(
                n_days, len(r["cells"]),
                f"第 {r['startNode']}-{r['endNode']} 节有 {len(r['cells'])} 格，"
                f"但有 {n_days} 天 —— 表格会错位",
            )

    def test_cell_dow_matches_its_column(self):
        w = self.api.get_week(0)
        for r in w["rows"]:
            self.assertEqual(
                [1, 2, 3, 4, 5, 6, 7],
                [c["dow"] for c in r["cells"]],
                "格子里的 dow 必须和列位置一一对应（否则课会显示在错误的星期下）",
            )


class TestWeekendCoursesShowUp(WeekGridTestCase):
    """周末的课要真的出现在格子里 —— 用户报的就是这个"""

    def test_saturday_course_appears_in_the_saturday_column(self):
        self.set_courses([Course("周六的课", 6, 1, 2, frozenset({5}), "S-101")])

        # 翻到第 5 周
        this_week = self.store.week_of(date.today())
        w = self.api.get_week(5 - this_week)

        self.assertEqual(5, w["week"])
        # 第 1-2 节那一行、周六那一列
        row = next(r for r in w["rows"] if r["startNode"] == 1)
        sat = next(c for c in row["cells"] if c["dow"] == 6)
        self.assertEqual("周六的课", sat["name"],
                         "第 5 周周六 1-2 节有课，课表里却没显示")
        self.assertEqual("S-101", sat["place"])

    def test_sunday_course_appears_in_the_sunday_column(self):
        self.set_courses([Course("周日的课", 7, 3, 4, frozenset({2}), "U-202")])

        this_week = self.store.week_of(date.today())
        w = self.api.get_week(2 - this_week)

        row = next(r for r in w["rows"] if r["startNode"] == 3)
        sun = next(c for c in row["cells"] if c["dow"] == 7)
        self.assertEqual("周日的课", sun["name"],
                         "第 2 周周日 3-4 节有课，课表里却没显示")

    def test_course_in_the_wrong_week_does_not_show(self):
        """周次不对就不该出现 —— 免得「显示出来了」其实是因为没做周次过滤"""
        self.set_courses([Course("周六的课", 6, 1, 2, frozenset({5}), "S-101")])

        this_week = self.store.week_of(date.today())
        w = self.api.get_week(6 - this_week)          # 第 6 周，这门课不在

        row = next(r for r in w["rows"] if r["startNode"] == 1)
        sat = next(c for c in row["cells"] if c["dow"] == 6)
        self.assertIsNone(sat["name"], "第 6 周没有这门课，不该显示")

    def test_weekdays_still_work(self):
        """
        顺带确认没把周一到周五弄坏 —— 修「加两列」时最容易出这种错。
        """
        self.set_courses([Course("周三的课", 3, 5, 6, frozenset({5}), "W-303")])

        this_week = self.store.week_of(date.today())
        w = self.api.get_week(5 - this_week)

        row = next(r for r in w["rows"] if r["startNode"] == 5)
        wed = next(c for c in row["cells"] if c["dow"] == 3)
        self.assertEqual("周三的课", wed["name"])

    def test_range_text_covers_the_whole_week(self):
        """标题上的日期范围要从周一写到周日"""
        w = self.api.get_week(0)
        # 「9.14 – 9.20」这样的格式，两头分别是周一和周日
        start_text, end_text = [p.strip() for p in w["rangeText"].split("–")]
        monday = self.store.term_start + __import__("datetime").timedelta(
            days=(w["week"] - 1) * 7
        )
        sunday = monday + __import__("datetime").timedelta(days=6)
        self.assertEqual(f"{monday.month}.{monday.day}", start_text)
        self.assertEqual(f"{sunday.month}.{sunday.day}", end_text)


class TestGridRowsCoverTenNodes(WeekGridTestCase):
    """节次行本身也不能少 —— 和「七天」是同一类问题"""

    def test_ten_nodes_in_five_rows(self):
        w = self.api.get_week(0)
        self.assertEqual([(1, 2), (3, 4), (5, 6), (7, 8), (9, 10)],
                         [(r["startNode"], r["endNode"]) for r in w["rows"]])

    def test_row_times_match_the_slot_table(self):
        w = self.api.get_week(0)
        for r in w["rows"]:
            self.assertEqual(slots.fmt(slots.start(r["startNode"])), r["startTime"])
            self.assertEqual(slots.fmt(slots.end(r["endNode"])), r["endTime"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
