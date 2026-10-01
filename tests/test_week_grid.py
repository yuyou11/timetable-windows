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
        起床时间都该按周六 / 周日那两套走（v4 里对应 id "5" / "6"）。
        """
        w = self.api.get_week(0)
        self.assertEqual("5", w["days"][5]["dayType"])
        self.assertEqual("6", w["days"][6]["dayType"])


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


class TestGridRowsFollowPeriods(WeekGridTestCase):
    """节次行由「节数信息」（store.periods）决定 —— 不再写死五对大课，
    也不再写死 10 节：一天的小节数 = 各位之和。

    默认 "442" = 上午 4 节、下午 4 节、晚上 2 节，和程序的 10 个内置
    小节一一对应：每一小节单独成行。"445"（13 节）这类超过 10 的结构，
    第 11 节起的时间按晚课节奏外推（见 slots.py）。"""

    def test_default_periods_give_ten_individual_rows(self):
        w = self.api.get_week(0)
        self.assertEqual([(n, n) for n in range(1, 11)],
                         [(r["startNode"], r["endNode"]) for r in w["rows"]])
        self.assertEqual([f"第 {n} 节" for n in range(1, 11)],
                         [r["label"] for r in w["rows"]])

    def test_rows_cover_exactly_the_declared_nodes(self):
        """不管结构怎么写，声明的每一节都得有行 —— 漏一行就是漏一类课。
        "441"（和 9）= 一天 9 节，没有第 10 节"""
        expected = {"441": 9, "442": 10, "22222": 10, "55": 10, "445": 13}
        for periods, n in expected.items():
            self.store.periods = periods
            self.assertEqual(n, self.store.node_count, periods)
            w = self.api.get_week(0)
            self.assertEqual([(k, k) for k in range(1, n + 1)],
                             [(r["startNode"], r["endNode"]) for r in w["rows"]],
                             f"periods={periods}")

    def test_periods_beyond_ten_get_extrapolated_times(self):
        """第 11 节起按晚课节奏外推：45 分钟一节、间歇 5 分钟、20:40 起始"""
        self.store.periods = "445"          # 4+4+5 = 13 节
        w = self.api.get_week(0)
        self.assertEqual(13, len(w["rows"]))
        row11 = next(r for r in w["rows"] if r["startNode"] == 11)
        self.assertEqual("20:40", row11["startTime"])
        self.assertEqual("21:25", row11["endTime"])
        row13 = next(r for r in w["rows"] if r["startNode"] == 13)
        self.assertEqual("22:20", row13["startTime"])
        self.assertEqual("23:05", row13["endTime"])

    def test_course_on_extrapolated_rows_shows_up(self):
        """第 12-13 节的课：内置时间表之外的节，以前连合法都算不上"""
        self.store.periods = "445"
        self.set_courses([Course("晚加课", 1, 12, 13, frozenset({5}), "S-100")])
        this_week = self.store.week_of(date.today())
        w = self.api.get_week(5 - this_week)
        shown = [c for r in w["rows"] for c in r["cells"] if c["name"] == "晚加课"]
        self.assertEqual(2, len(shown))
        self.assertEqual(["第 12-13 节", "第 12-13 节"],
                         [c["nodesText"] for c in shown])

    def test_invalid_periods_fall_back_to_default(self):
        """data.json 是能手改的：坏值退回默认结构，不能让程序起不来"""
        self.store._data["periods"] = "440"    # 有 0 —— 一位至少 1 节
        self.assertEqual("442", self.store.periods)
        w = self.api.get_week(0)
        self.assertEqual(10, len(w["rows"]))

    def test_sum_over_day_capacity_is_rejected(self):
        """和超过 14 = 45 分钟的节排不进一天 —— 退回默认而不是硬着头皮渲染"""
        self.store._data["periods"] = "456"    # 4+5+6 = 15 > 14
        self.assertEqual("442", self.store.periods)

    def test_pair_blocks_split_into_individual_rows(self):
        """"22222"（五段、每段 2 节）= 十节全部逐节分行 ——
        旧行结构的「成对显示」由前端合并相邻同课实现，不再是行的单位"""
        self.store.periods = "22222"
        w = self.api.get_week(0)
        self.assertEqual([(n, n) for n in range(1, 11)],
                         [(r["startNode"], r["endNode"]) for r in w["rows"]])

    def test_row_times_match_the_slot_table(self):
        w = self.api.get_week(0)
        for r in w["rows"]:
            self.assertEqual(slots.fmt(slots.start(r["startNode"])), r["startTime"])
            self.assertEqual(slots.fmt(slots.end(r["endNode"])), r["endTime"])


class TestCoursesOutsidePairsStillShow(WeekGridTestCase):
    """别的学校课不成对 —— 这些课以前在网格里**整门消失**，必须都能显示"""

    def week_of_course(self, course):
        self.set_courses([course])
        this_week = self.store.week_of(date.today())
        return self.api.get_week(5 - this_week if course.weeks == frozenset({5}) else 0)

    def test_single_period_course_shows_up(self):
        """第 4 节单节的课：旧行结构里没有任何一行的行首是 4，整门课看不见"""
        w = self.week_of_course(Course("单节课", 1, 4, 4, frozenset({5}), "S-101"))
        row = next(r for r in w["rows"] if r["startNode"] == 4)
        cell = next(c for c in row["cells"] if c["dow"] == 1)
        self.assertEqual("单节课", cell["name"])
        self.assertEqual("第 4 节", cell["nodesText"])

    def test_course_crossing_a_pair_boundary_shows_up(self):
        """跨在 2-3 节上的课：不属于任何一对大课，以前同样整门消失"""
        w = self.week_of_course(Course("跨节裸", 2, 2, 3, frozenset({5}), "S-102"))
        shown = [c for r in w["rows"] for c in r["cells"] if c["name"] == "跨节裸"]
        self.assertEqual(2, len(shown), "应出现在第 2 节和第 3 节两行里，供前端合并")

    def test_pair_course_fills_both_of_its_rows(self):
        """成对的课在逐节行里出现在两行，两行的身份信息完全一致 ——
        前端按这个一致性做 rowspan 合并，所以后端必须原样给出"""
        w = self.week_of_course(Course("成对裸", 3, 1, 2, frozenset({5}), "S-103"))
        rows = [r for r in w["rows"] if r["startNode"] in (1, 2)]
        cells = [next(c for c in r["cells"] if c["dow"] == 3) for r in rows]
        self.assertEqual(["成对裸", "成对裸"], [c["name"] for c in cells])
        self.assertEqual(1, len({(c["name"], c["place"], c["weeksText"]) for c in cells}),
                         "同一门课在两行的身份必须一致，否则前端合并不了")

    def test_evening_course_shows_school_numbering(self):
        """晚课的「第几节」跟学校数法走：442 下跨 9-10 两行，报「第 9-10 节」"""
        w = self.week_of_course(Course("晚课", 4, 9, 10, frozenset({5}), "S-104"))
        shown = [c for r in w["rows"] for c in r["cells"] if c["name"] == "晚课"]
        self.assertEqual(2, len(shown))
        self.assertEqual(["第 9-10 节", "第 9-10 节"], [c["nodesText"] for c in shown])

    def test_evening_course_under_441_reports_one_period(self):
        """441（一天 9 节）下，第 9 节的课报「第 9 节」"""
        self.store.periods = "441"
        w = self.week_of_course(Course("晚课", 4, 9, 9, frozenset({5}), "S-104"))
        row = next(r for r in w["rows"] if r["startNode"] == 9)
        cell = next(c for c in row["cells"] if c["dow"] == 4)
        self.assertEqual("晚课", cell["name"])
        self.assertEqual("第 9 节", cell["nodesText"])
        # 手改数据里的 [9,10] 超出了声明的小节数：不崩，宽容地落在第 9 节
        w = self.week_of_course(Course("旧晚课", 4, 9, 10, frozenset({5}), "S-104"))
        row = next(r for r in w["rows"] if r["startNode"] == 9)
        cell = next(c for c in row["cells"] if c["dow"] == 4)
        self.assertEqual("旧晚课", cell["name"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
