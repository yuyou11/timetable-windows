"""
核心逻辑测试 —— 用标准库 unittest，不依赖 pytest（少一个依赖就少一份麻烦）。

运行：
    python -m unittest discover -s tests -v

## 这些测试是从 Android 版原样移植过来的

移过来不是为了好看，而是因为**两边必须行为一致**。电脑版和手机版读写同一套
JSON 格式，如果引擎算出来的时间轴不一样，那导来导去就乱套了。

所以测试用例本身也是「规格」：Kotlin 那边验证过的东西，Python 这边也要过一遍。
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import builtin_data, engine, format_spec, slots
from app.day_type_policy import DayTypePolicy
from app.format_spec import FormatError
from app.models import Block, Course, DayType, Kind


TERM_START = date(2026, 9, 7)


def d(y: int, m: int, day: int) -> date:
    return date(y, m, day)


class TestWeekOf(unittest.TestCase):
    def test_first_week_starts_sep_7(self):
        self.assertEqual(1, engine.week_of(d(2026, 9, 7), TERM_START))    # 周一
        self.assertEqual(1, engine.week_of(d(2026, 9, 13), TERM_START))   # 周日，仍算第 1 周

    def test_second_week(self):
        self.assertEqual(2, engine.week_of(d(2026, 9, 14), TERM_START))
        self.assertEqual(2, engine.week_of(d(2026, 9, 20), TERM_START))

    def test_seventeenth_week(self):
        self.assertEqual(17, engine.week_of(d(2026, 12, 28), TERM_START))

    def test_before_term_start_gives_zero_or_negative(self):
        """
        学期开始之前应该是 0 或负数，不能是 1。

        这一条专门守 Python 的整除行为：-3 // 7 == -1（向下取整），
        而 C/Java 的 -3 / 7 == 0（向零取整）。后者会让开学前一天
        显示成「第 1 周」，看似正常实则错位。

        写法上要小心「差几天」和「往前几周」是两回事：

            9/07 是第 1 周
            9/06（差 1 天）  → 仍在第 1 周的前一天 → 第 0 周
            8/31（差 7 天）  → 正好退一整周     → 第 0 周
            8/30（差 8 天）  → 退了 1 周零 1 天  → 第 -1 周

        我第一版把 8/31 写成了「第 -1 周」，被这个测试自己抓出来了 ——
        **算周次时数错一格是最容易犯的错，所以每条断言都要能自己算出理由。**
        """
        self.assertEqual(0, engine.week_of(d(2026, 9, 6), TERM_START))    # 前一天，仍在第 0 周
        self.assertEqual(0, engine.week_of(d(2026, 8, 31), TERM_START))   # 差 7 天，退满一整周
        self.assertEqual(-1, engine.week_of(d(2026, 8, 30), TERM_START))  # 差 8 天，再往上一周
        self.assertLess(engine.week_of(d(2026, 8, 1), TERM_START), 0)


class TestMondayOf(unittest.TestCase):
    def test_snaps_to_monday(self):
        for offset in range(21):
            any_day = d(2026, 9, 7) + timedelta(days=offset)
            monday = engine.monday_of(any_day)
            self.assertEqual(1, monday.isoweekday(), f"{any_day} 吸附后应落在周一")
            self.assertLessEqual(monday, any_day, f"{any_day} 吸附后不该跑到未来")
            self.assertEqual(
                engine.week_of(monday, TERM_START),
                engine.week_of(any_day, TERM_START),
                "同一周内的任何一天，吸附后算出的周次必须相同",
            )

    def test_specific_days(self):
        self.assertEqual(d(2026, 9, 7), engine.monday_of(d(2026, 9, 7)))    # 周一
        self.assertEqual(d(2026, 9, 7), engine.monday_of(d(2026, 9, 13)))   # 周日 → 往回 6 天
        self.assertEqual(d(2026, 9, 14), engine.monday_of(d(2026, 9, 14)))  # 下周一


class TestDayType(unittest.TestCase):
    """
    日型推导是全程序最核心的一条规则：
    只看「今天第 1-2 节有没有课」，不按星期几写死。

    ## ⚠️ 这里测的是 natural_day_type（纯日历规则）

    加了日型策略之后，「日历上是哪种日」和「实际用哪套模板」是两件事
    （见 DayTypePolicy 的说明）。这一组测的是**前者**，所以调
    `natural_day_type`，策略不参与 —— 否则「周二算不算训练日」这种问题
    会被默认策略盖住，测不出规则本身。

    策略的映射行为单独在 TestDayTypePolicy 里测。
    """

    def setUp(self):
        self.courses = builtin_data.courses()

    def test_monday_with_early_class_is_a(self):
        self.assertEqual(
            DayType.A, engine.natural_day_type(d(2026, 9, 21), 3, self.courses)   # 第 3 周周一有英语
        )

    def test_monday_before_courses_start_is_rest(self):
        # 第 1 周大学英语还没开（它是 2-4、6-17 周）→ 全天没课
        # 无课日按休息日过（REST），而不是按 B 型把人按 07:25 的节奏排满
        self.assertEqual(
            DayType.REST, engine.natural_day_type(d(2026, 9, 7), 1, self.courses)
        )

    def test_friday_no_early_class_is_b(self):
        # 第 2 周周五有课（体育、思德等），但没有早八 → B 型
        # 这条和上面那条合起来才是完整规则：有课没早八 = B 型，没课 = REST
        self.assertEqual(
            DayType.B_NORMAL, engine.natural_day_type(d(2026, 9, 11), 2, self.courses)
        )

    def test_week8_wednesday_no_courses_is_rest(self):
        # 高数周三 1-2 节是「2-7、9-17 周」，第 8 周正好不上；
        # 周三其它的课也都排不到第 8 周 → 全天没课 → 休息日
        self.assertEqual(
            DayType.REST, engine.natural_day_type(d(2026, 10, 28), 8, self.courses)
        )

    def test_training_days(self):
        self.assertEqual(DayType.B_TRAIN_A, engine.natural_day_type(d(2026, 9, 22), 3, self.courses))
        self.assertEqual(DayType.B_TRAIN_B, engine.natural_day_type(d(2026, 9, 24), 3, self.courses))

    def test_weekend(self):
        self.assertEqual(DayType.SATURDAY, engine.natural_day_type(d(2026, 9, 26), 3, self.courses))
        self.assertEqual(DayType.SUNDAY, engine.natural_day_type(d(2026, 9, 27), 3, self.courses))


class TestWakeMinute(unittest.TestCase):
    def test_builtin_wake_times(self):
        t = builtin_data.templates()
        self.assertEqual(6 * 60 + 55, engine.wake_minute(t[DayType.A]))
        self.assertEqual(7 * 60 + 25, engine.wake_minute(t[DayType.B_NORMAL]))
        self.assertEqual(7 * 60 + 25, engine.wake_minute(t[DayType.B_TRAIN_A]))
        self.assertEqual(9 * 60, engine.wake_minute(t[DayType.SATURDAY]))
        self.assertEqual(8 * 60 + 30, engine.wake_minute(t[DayType.SUNDAY]))
        # 无课休息日睡到自然醒 —— 和周六一个点
        self.assertEqual(9 * 60, engine.wake_minute(t[DayType.REST]))

    def test_all_types_have_a_morning_wake_time(self):
        # 防回归：将来有人改了模板里某个 kind，导致找不到上午睡眠段，
        # 界面上那一行会凭空消失。这个测试会立刻发现。
        templates = builtin_data.templates()
        for day_type in DayType:
            wake = engine.wake_minute(templates[day_type])
            self.assertIsNotNone(wake, f"{day_type} 推不出起床时间")
            self.assertTrue(5 * 60 <= wake <= 11 * 60, f"{day_type} 的起床时间 {wake} 不像早晨")

    def test_no_morning_sleep_returns_none(self):
        self.assertIsNone(engine.wake_minute([Block(0, 1440, "连续工作", kind=Kind.STUDY)]))


class TestTimeline(unittest.TestCase):
    def setUp(self):
        self.courses = builtin_data.courses()
        self.templates = builtin_data.templates()

    def moments(self, dt, week, policy=None):
        """
        ⚠️ 这里的**默认策略是 ALL（六种日型全开）**，不是产品的默认策略。

        理由：这一组测的是**引擎本身**（模板叠加、连堂合并、收口），
        不是「默认配置长什么样」。用 ALL 当作恒等映射，
        测的就是 engery 的原始行为 —— 和加日型策略之前完全一致，
        所以原有断言一条都不用改。

        要测「默认策略会筛掉什么」，就显式传 DayTypePolicy.DEFAULT
        （见 test_default_policy_hides_training）。
        """
        return engine.moments(
            dt, week, self.courses, self.templates,
            DayTypePolicy.ALL if policy is None else policy,
        )

    def at(self, dt, week, hhmm, policy=None):
        h, m = (int(x) for x in hhmm.split(":"))
        return engine.current_at(self.moments(dt, week, policy), h * 60 + m)

    # ---------- 课表覆盖模板 ----------

    def test_monday_early_class_shows_course(self):
        m = self.at(d(2026, 9, 21), 3, "09:00")
        self.assertEqual("大学英语", m.title)
        self.assertEqual("教一-101", m.place)
        self.assertTrue(m.is_course)

    def test_consecutive_periods_merge_into_one_block(self):
        m = self.at(d(2026, 9, 21), 3, "09:00")
        self.assertEqual(slots.start(1), m.start)   # 08:30
        self.assertEqual(slots.end(2), m.end)       # 10:05，中间那 5 分钟算在里面

    def test_empty_slot_says_no_class(self):
        # 周一第 6 周有课（英语/高数/军理），但 5-6 节的近现代史要 15 周才开始
        # —— 这个格子空着，应显示「无课」。
        # ⚠️ 别用第 1 周周一：那天全天无课，整个走 REST 休息日，没有占位格。
        m = self.at(d(2026, 10, 12), 6, "15:00")
        self.assertIn("无课", m.title)

    def test_course_only_in_week_4(self):
        # 周二 5-6 节「大学物理实验」只排在第 4 周
        self.assertEqual("大学物理实验", self.at(d(2026, 9, 29), 4, "15:00").title)
        self.assertFalse(self.at(d(2026, 10, 6), 5, "15:00").title.startswith("大学物理实验"))

    def test_odd_even_week_switch_on_thursday(self):
        """
        周四 7-8 节：第 2 周是程序设计基础，第 3 周起（单周）换成数据结构。

        这是「同一格子、不同周次排不同课」的真实排法，
        也是格式标准里专门说明过的那种合法重叠（时段相同、周次不相交）。
        """
        self.assertEqual("程序设计基础", self.at(d(2026, 9, 17), 2, "16:00").title)
        # 第 3 周（单周）起是数据结构
        self.assertEqual("数据结构", self.at(d(2026, 9, 24), 3, "16:00").title)
        # 第 6 周是双周，数据结构不上（大物有课，所以这天不是 REST，
        # 7-8 节这个格子显示「无课」）
        self.assertIn("无课", self.at(d(2026, 10, 15), 6, "16:00").title)

    def test_tuesday_evening_is_training(self):
        """
        ⚠️ 这条要显式用 `DayTypePolicy.ALL`。

        默认策略**关掉了训练日**（周二 / 周四都回落到 B_NORMAL），
        所以「周二晚上有训练」这件事只在**六种日型全开**时才成立。
        这正是日型策略存在的意义 —— 见下面 test_default_policy_hides_training。
        """
        m = self.at(d(2026, 9, 22), 3, "19:30", policy=DayTypePolicy.ALL)
        self.assertIn("训练", m.title)
        self.assertEqual(Kind.TRAIN, m.kind)

    def test_thursday_training_is_strength_b(self):
        m = self.at(d(2026, 9, 24), 3, "19:30", policy=DayTypePolicy.ALL)
        self.assertIn("力量 B", m.note)

    def test_default_policy_hides_training(self):
        """
        默认策略下，周二晚上**不该**出现训练块。

        「周二练力量A」来自某一份具体规划表，不是通用规律 ——
        没有训练安排的人，那两套模板纯属噪声，还会让
        「周二到底该几点起」变得难以回答。所以默认关掉。

        这条和上面两条是一对：同一个时刻、同一份模板，
        只有策略不同，结果就应该不同。**只测一边说明不了策略真的生效了。**
        """
        m = self.at(d(2026, 9, 22), 3, "19:30", policy=DayTypePolicy.DEFAULT)
        self.assertNotIn("训练", m.title)
        self.assertNotEqual(Kind.TRAIN, m.kind)

    # ---------- 结构性不变量 ----------

    def test_whole_semester_timeline_is_contiguous(self):
        """
        遍历整学期 19 周 × 7 天 = 133 天，每天的时间轴必须：
        从 00:00 开始、到 24:00 结束、首尾相接、无空洞、无重叠。

        这条比上面所有单点断言都值钱：
        单点断言只能证明「我想到的情况是对的」，
        遍历才能证明「不存在我没想到的坏情况」。
        """
        for week in range(1, 20):
            for dow in range(7):
                dt = TERM_START + timedelta(days=(week - 1) * 7 + dow)
                ms = self.moments(dt, week)

                self.assertTrue(ms, f"{dt} 时间轴为空")
                self.assertEqual(0, ms[0].start, f"{dt} 没有从 00:00 开始")
                self.assertEqual(1440, ms[-1].end, f"{dt} 没有到 24:00 结束")
                for i in range(1, len(ms)):
                    self.assertEqual(
                        ms[i - 1].end, ms[i].start,
                        f"{dt} 第 {i} 格与上一格之间有空洞或重叠",
                    )

    def test_class_blocks_never_coexist_with_placeholder(self):
        ms = self.moments(d(2026, 9, 21), 3)
        class_blocks = [m for m in ms if m.kind == Kind.CLASS]
        self.assertEqual(5, len(class_blocks), "课表格子的数量应恒等于模板里的 5 个节次段")
        titles = [m.title for m in class_blocks]
        self.assertIn("大学英语", titles)
        self.assertTrue(any("无课" in t for t in titles))

    def test_weird_course_spanning_lunch_does_not_break_timeline(self):
        # 人为造一门 3-8 节连上的怪课，专门压测收口逻辑。
        # 现实里不会这么排，但边界情况正是 bug 的藏身处。
        weird = [Course("占位测试课", 2, 3, 8, frozenset(range(1, 20)), "X-101")]
        ms = engine.moments(d(2026, 9, 22), 3, weird, self.templates)

        self.assertEqual(0, ms[0].start)
        self.assertEqual(1440, ms[-1].end)
        for i in range(1, len(ms)):
            self.assertEqual(ms[i - 1].end, ms[i].start)
        self.assertTrue(any(m.title == "占位测试课" for m in ms))
        self.assertFalse(any(m.title == "午餐" for m in ms), "被盖住的午餐应该消失")

    def test_disabling_early_class_falls_back_to_b_type(self):
        """
        停掉早八课之后，整个上午会自动退化成 B 型。

        这条最能说明「规则要推导、不要写死」：
        没有任何一行代码在描述「停课之后要切模板」，
        它是 day_type() 那条判断自然产生的结果。
        """
        disabled = [c for c in self.courses if c.name != "大学英语"]
        dt = d(2026, 9, 21)
        self.assertEqual(
            DayType.B_NORMAL,
            engine.day_type(dt, 3, disabled, DayTypePolicy.ALL),
        )
        ms = engine.moments(dt, 3, disabled, self.templates, DayTypePolicy.ALL)
        self.assertIn("黄金自习块", engine.current_at(ms, 9 * 60).title)

    def test_disabling_other_course_only_affects_that_slot(self):
        disabled = [c for c in self.courses if c.name != "军事理论"]
        dt = d(2026, 9, 21)
        ms = engine.moments(dt, 3, disabled, self.templates)
        self.assertEqual("大学英语", engine.current_at(ms, 9 * 60).title)
        self.assertIn("无课", engine.current_at(ms, 16 * 60).title)

    # ---------- 自定义模板 ----------

    def test_custom_template_changes_wake_time(self):
        custom = {
            DayType.A: [
                Block(0, 8 * 60, "睡觉", kind=Kind.SLEEP),
                Block(8 * 60, 1440, "自定义的一天", kind=Kind.STUDY),
            ]
        }
        merged = dict(self.templates)
        merged.update(custom)

        self.assertEqual(8 * 60, engine.wake_minute(merged[DayType.A]))
        # 没被覆盖的日型继续用内置
        self.assertEqual(7 * 60 + 25, engine.wake_minute(merged[DayType.B_NORMAL]))

    def test_course_always_beats_custom_template(self):
        """
        课程永远优先于模板 —— 即使自定义模板里没留课表格子。

        这个行为是刻意的：模板回答「我打算怎么过」，课表回答「学校规定我必须在哪」。
        后者是硬约束。**如果改模板能让课凭空消失，那是灾难。**
        """
        custom = {
            DayType.A: [
                Block(0, 8 * 60 + 30, "睡觉", kind=Kind.SLEEP),
                Block(8 * 60 + 30, 1440, "我安排的一整天", kind=Kind.STUDY),
            ]
        }
        merged = dict(self.templates)
        merged.update(custom)
        ms = engine.moments(d(2026, 9, 21), 3, self.courses, merged)

        self.assertEqual("大学英语", engine.current_at(ms, 9 * 60).title)
        self.assertEqual("我安排的一整天", engine.current_at(ms, 12 * 60 + 30).title)
        for i in range(1, len(ms)):
            self.assertEqual(ms[i - 1].end, ms[i].start)


class TestWeeksParsing(unittest.TestCase):
    def test_basic_forms(self):
        self.assertEqual({3}, set(format_spec.parse_weeks("3", 19)))
        self.assertEqual({2, 3, 4}, set(format_spec.parse_weeks("2-4", 19)))
        self.assertEqual(
            {3, 5, 7, 9, 11, 13, 15, 17}, set(format_spec.parse_weeks("3-17/2", 19))
        )
        self.assertEqual(
            {2, 4, 6, 8, 10, 12, 14, 16}, set(format_spec.parse_weeks("2-16/2", 19))
        )
        self.assertEqual(set(range(1, 20)), set(format_spec.parse_weeks("*", 19)))

    def test_chinese_punctuation_is_tolerated(self):
        # 用户手写时几乎一定会打出这些，不该成为导入失败的理由
        expected = {2, 3, 4} | set(range(6, 18))
        self.assertEqual(expected, set(format_spec.parse_weeks("2～4，6－17", 19)))
        self.assertEqual(expected, set(format_spec.parse_weeks("2 - 4 , 6 - 17", 19)))

    def test_error_paths(self):
        for bad, needle in [
            ("1-20", "1–19"),
            ("5-2", "起点比终点大"),
            ("2,,4", "多余的逗号"),
            ("1-17/x", "步长"),
            ("", "不能为空"),
            ("   ", "不能为空"),
        ]:
            with self.assertRaises(FormatError, msg=f"{bad!r} 应该报错") as cm:
                format_spec.parse_weeks(bad, 19)
            self.assertIn(needle, str(cm.exception))

    def test_format_prefers_step_notation(self):
        self.assertEqual("3-17/2", format_spec.format_weeks({3, 5, 7, 9, 11, 13, 15, 17}))
        self.assertEqual("5", format_spec.format_weeks({5}))
        self.assertEqual("5-6", format_spec.format_weeks({5, 6}))
        self.assertEqual("2-4,6-17", format_spec.format_weeks({2, 3, 4} | set(range(6, 18))))
        self.assertEqual("2,5-12", format_spec.format_weeks({2} | set(range(5, 13))))

    def test_format_then_parse_roundtrip(self):
        samples = [
            {3},
            {2, 3, 4},
            {3, 5, 7, 9, 11, 13, 15, 17},
            {2} | set(range(5, 13)),
            set(range(1, 20)),
        ]
        for s in samples:
            text = format_spec.format_weeks(s)
            self.assertEqual(s, set(format_spec.parse_weeks(text, 19)), f"{s} 往返失败")


class TestDocumentParsing(unittest.TestCase):
    ONE = '{"name":"大学英语","dayOfWeek":1,"nodes":[1,2],"weeks":"2-4","place":"教一-101"}'

    def doc(self, courses: str, term: str = "", version: int = 2, fmt: str = "timetable") -> str:
        term_part = f'"term":{term},' if term else ""
        return f'{{"format":"{fmt}","version":{version},{term_part}"courses":[{courses}]}}'

    def test_minimal_document(self):
        p = format_spec.parse(self.doc(self.ONE))
        self.assertEqual(1, len(p.courses))
        c = p.courses[0]
        self.assertEqual("大学英语", c.name)
        self.assertEqual(1, c.day_of_week)
        self.assertEqual({2, 3, 4}, set(c.weeks))
        self.assertEqual("教一-101", c.place)
        self.assertIsNone(p.term)

    def test_term_section(self):
        p = format_spec.parse(self.doc(
            self.ONE, '{"name":"大一上","startDate":"2026-09-07","totalWeeks":19}'
        ))
        self.assertEqual("大一上", p.term.name)
        self.assertEqual(date(2026, 9, 7), p.term.start_date)
        self.assertEqual(19, p.term.total_weeks)

    def test_non_monday_start_date_reports_the_correct_date(self):
        # 报错的价值在于「告诉用户改成什么」，所以连正确日期都要算好
        with self.assertRaises(FormatError) as cm:
            format_spec.parse(self.doc(self.ONE, '{"startDate":"2026-09-09","totalWeeks":19}'))
        msg = str(cm.exception)
        self.assertIn("必须是周一", msg)
        self.assertIn("2026-09-07", msg)

    def test_day_of_week_accepts_chinese(self):
        p = format_spec.parse(self.doc('{"name":"高数","dayOfWeek":"周三","nodes":[3,4],"weeks":"1-5"}'))
        self.assertEqual(3, p.courses[0].day_of_week)

    def test_weeks_as_array(self):
        p = format_spec.parse(self.doc('{"name":"高数","dayOfWeek":3,"nodes":[3,4],"weeks":[2,3,4,6,7]}'))
        self.assertEqual({2, 3, 4, 6, 7}, set(p.courses[0].weeks))

    def test_rejects_foreign_file(self):
        with self.assertRaises(FormatError) as cm:
            format_spec.parse(self.doc(self.ONE, fmt="something-else"))
        self.assertIn("timetable", str(cm.exception))

    def test_rejects_newer_version(self):
        with self.assertRaises(FormatError) as cm:
            format_spec.parse(self.doc(self.ONE, version=99))
        self.assertIn("v99", str(cm.exception))
        self.assertIn("更新程序", str(cm.exception))

    def test_error_mentions_which_course(self):
        broken = '{"name":"高数","dayOfWeek":3,"nodes":[3,4],"weeks":"5-2"}'
        with self.assertRaises(FormatError) as cm:
            format_spec.parse(self.doc(self.ONE + "," + broken))
        msg = str(cm.exception)
        self.assertIn("第 2 门课", msg)
        self.assertIn("高数", msg)

    def test_all_empty_rejected(self):
        with self.assertRaises(FormatError) as cm:
            format_spec.parse('{"format":"timetable","version":2,"courses":[]}')
        self.assertIn("没有可导入的东西", str(cm.exception))

    def test_tolerates_code_fence_from_ai(self):
        """
        AI 十次里有两次会用 ```json 包起来，尽管提示词里写了不要。
        与其让用户手工删那两行，不如解析时顺手处理掉。
        """
        raw = "```json\n" + self.doc(self.ONE) + "\n```"
        p = format_spec.parse(raw)
        self.assertEqual(1, len(p.courses))

    # ---------- 只改模板不动课表 ----------

    TEMPLATE_ONLY = (
        '"templates":{"A":[{"start":"00:00","end":"06:55","title":"睡觉","kind":"SLEEP"},'
        '{"start":"06:55","end":"24:00","title":"白天"}]}'
    )

    def test_templates_only_file_is_valid(self):
        # 「我只想改起床时间，课表别动」是完全正当的需求
        raw = f'{{"format":"timetable","version":2,{self.TEMPLATE_ONLY},"courses":[]}}'
        p = format_spec.parse(raw)
        self.assertIsNone(p.courses, "courses 为空时应解析成 None，表示「不要动」")
        self.assertIsNotNone(p.templates)

    def test_null_courses_differs_from_empty_list(self):
        with_courses = format_spec.parse(self.doc(self.ONE))
        self.assertIsNotNone(with_courses.courses)

        templates_only = format_spec.parse(
            f'{{"format":"timetable","version":2,{self.TEMPLATE_ONLY},"courses":[]}}'
        )
        self.assertIsNone(templates_only.courses)

    # ---------- 模板校验 ----------

    def test_template_overlap_is_an_error(self):
        # 引擎遇到重叠会「先到先得」把后一格静默截断，用户写的某格就这么没了。
        # 这种「不报错的错」比直接失败糟糕得多，所以必须报错。
        tmpl = (
            '"templates":{"A":[{"start":"00:00","end":"08:00","title":"睡觉"},'
            '{"start":"07:00","end":"24:00","title":"早起"}]}'
        )
        raw = f'{{"format":"timetable","version":2,{tmpl},"courses":[]}}'
        with self.assertRaises(FormatError) as cm:
            format_spec.parse(raw)
        msg = str(cm.exception)
        self.assertIn("重叠", msg)
        self.assertIn("睡觉", msg)

    def test_template_gap_only_warns(self):
        tmpl = '"templates":{"A":[{"start":"08:00","end":"12:00","title":"上午"}]}'
        raw = f'{{"format":"timetable","version":2,{tmpl},"courses":[]}}'
        p = format_spec.parse(raw)
        self.assertTrue(any("空档" in w for w in p.warnings))

    def test_unknown_day_type_lists_valid_values(self):
        tmpl = '"templates":{"MONDAY":[{"start":"00:00","end":"24:00","title":"x"}]}'
        raw = f'{{"format":"timetable","version":2,{tmpl},"courses":[]}}'
        with self.assertRaises(FormatError) as cm:
            format_spec.parse(raw)
        msg = str(cm.exception)
        self.assertIn("MONDAY", msg)
        self.assertIn("SATURDAY", msg)

    def test_end_can_be_24_but_start_cannot(self):
        ok = '"templates":{"A":[{"start":"23:00","end":"24:00","title":"睡前"}]}'
        p = format_spec.parse(f'{{"format":"timetable","version":2,{ok},"courses":[]}}')
        self.assertEqual(1440, p.templates[DayType.A][0].end)

        bad = '"templates":{"A":[{"start":"24:00","end":"24:00","title":"x"}]}'
        with self.assertRaises(FormatError) as cm:
            format_spec.parse(f'{{"format":"timetable","version":2,{bad},"courses":[]}}')
        self.assertIn("start", str(cm.exception))

    def test_kind_is_case_insensitive(self):
        tmpl = '"templates":{"A":[{"start":"00:00","end":"24:00","title":"x","kind":"sleep"}]}'
        p = format_spec.parse(f'{{"format":"timetable","version":2,{tmpl},"courses":[]}}')
        self.assertEqual(Kind.SLEEP, p.templates[DayType.A][0].kind)

    def test_unknown_field_is_ignored(self):
        """
        未知字段一律忽略 —— 这是让格式能演进的唯一办法。
        将来 v3 加了 teacher 字段，v2 的程序拿到 v3 文件只会少读一个字段，
        而不是整个导入失败。
        """
        raw = self.doc(
            '{"name":"高数","dayOfWeek":3,"nodes":[3,4],"weeks":"1-5","teacher":"张老师"}'
        )
        p = format_spec.parse(raw)
        self.assertEqual(1, len(p.courses))


class TestRoundTrip(unittest.TestCase):
    """
    往返一致性 —— 用户「导出 → 改 → 导入」的整个工作流都建立在这上面。
    """

    def test_builtin_courses_survive_roundtrip(self):
        courses = builtin_data.courses()
        text = format_spec.serialize("测试", TERM_START, 19, courses)
        back = format_spec.parse(text).courses

        self.assertEqual(len(courses), len(back))
        for a, b in zip(courses, back):
            self.assertEqual(_sig_course(a), _sig_course(b))

    def test_builtin_templates_survive_roundtrip(self):
        templates = builtin_data.templates()
        text = format_spec.serialize("测试", TERM_START, 19, builtin_data.courses(), templates)
        back = format_spec.parse(text).templates

        # REST 故意不进文件（不在 DAY_TYPE_ORDER 里，理由见 models 的注释），
        # 往返后剩下的是六套标准日型；REST 的模板永远来自内置，不依赖往返。
        self.assertNotIn(DayType.REST, back)
        self.assertEqual(6, len(back))
        for day_type in back:
            self.assertEqual(
                _sig_blocks(templates[day_type]),
                _sig_blocks(back[day_type]),
                f"{day_type} 往返不一致",
            )

    def test_exported_templates_reproduce_identical_timeline(self):
        """
        端到端：导出 → 解析 → 拿回来算时间轴，必须和内置模板算出来的逐格一致。

        这条比单纯的「字段相等」更强 —— 它证明导出的模板**真的能用**。
        """
        templates = builtin_data.templates()
        text = format_spec.serialize("测试", TERM_START, 19, builtin_data.courses(), templates)
        back = format_spec.parse(text).templates
        # REST 不进文件（见 test_builtin_templates_survive_roundtrip），
        # 从内置补回 —— 生产环境里 store.templates() 也是这么合并的。
        back[DayType.REST] = templates[DayType.REST]

        courses = builtin_data.courses()
        for week, dow in [(3, 0), (3, 1), (3, 2), (3, 3), (3, 4), (8, 2), (1, 0)]:
            dt = TERM_START + timedelta(days=(week - 1) * 7 + dow)
            original = engine.moments(dt, week, courses, templates)
            restored = engine.moments(dt, week, courses, back)
            self.assertEqual(
                [(m.start, m.end, m.title, m.kind) for m in original],
                [(m.start, m.end, m.title, m.kind) for m in restored],
                f"{dt} 的时间轴在往返后变了",
            )

    def test_second_export_is_byte_identical(self):
        """导出必须是确定性的：同样的数据导出两次，逐字节相同"""
        courses = builtin_data.courses()
        templates = builtin_data.templates()
        a = format_spec.serialize("x", TERM_START, 19, courses, templates)
        b = format_spec.serialize("x", TERM_START, 19, format_spec.parse(a).courses,
                                  format_spec.parse(a).templates)
        self.assertEqual(a, b)

    def test_field_order_is_fixed(self):
        """
        输出字段顺序固定 —— 这条是踩坑之后才加的。

        最初用 json.dumps 生成，但第三方序列化器的字段顺序不受你控制
        （Android 的 org.json 用 LinkedHashMap 保序，标准 JDK 版用 HashMap 随机），
        结果同一个函数在不同平台上产出的文件长得不一样。

        对一份「给人看、给人改」的文件来说这是致命的：
        用户照着手机导出的文件学格式，再去看文档里的例子，会发现两者不一样。
        """
        text = format_spec.serialize("x", TERM_START, 19, builtin_data.courses(),
                                     builtin_data.templates())
        lines = text.splitlines()
        self.assertEqual('  "format": "timetable",', lines[1])
        self.assertTrue(lines[2].startswith('  "version"'))
        self.assertTrue(lines[3].startswith('  "term"'))
        self.assertLess(text.index('"courses"'), text.index('"templates"'))

    def test_template_line_field_order(self):
        text = format_spec.serialize("x", TERM_START, 19, [], builtin_data.templates())
        line = next(ln for ln in text.splitlines() if '"kind": "SLEEP"' in ln)
        self.assertLess(line.index('"start"'), line.index('"end"'))
        self.assertLess(line.index('"end"'), line.index('"title"'))
        self.assertLess(line.index('"title"'), line.index('"kind"'))

    def test_quotes_in_names_are_escaped(self):
        # 少了转义，课程名里一个引号就能生成一份坏文件，
        # 而且症状是「导出看起来成功了，导入却报 JSON 错误」—— 非常难查
        nasty = '他说 "你好" 和 \\反斜杠\\'
        text = format_spec.serialize("x", TERM_START, 19,
                                     [Course(nasty, 1, 1, 2, frozenset({1}))])
        self.assertEqual(nasty, format_spec.parse(text).courses[0].name)

    def test_disabled_flag_roundtrips(self):
        courses = [c.copy() if hasattr(c, "copy") else c for c in builtin_data.courses()]
        courses = [
            Course(c.name, c.day_of_week, c.start_node, c.end_node, c.weeks, c.place, False, c.id)
            if c.name == "军事理论" else c
            for c in courses
        ]
        text = format_spec.serialize("x", TERM_START, 19, courses)
        back = format_spec.parse(text).courses
        self.assertEqual(1, sum(1 for c in back if not c.enabled))
        self.assertEqual("军事理论", next(c.name for c in back if not c.enabled))

    def test_no_templates_section_when_empty(self):
        text = format_spec.serialize("x", TERM_START, 19, builtin_data.courses())
        self.assertNotIn("templates", text)
        self.assertIsNone(format_spec.parse(text).templates)


class TestTemplatePersistence(unittest.TestCase):
    """
    模板存进本地存储、再读回来的往返。

    ## 这一组是「测试抓到一个真 bug」的直接产物

    存模板用的是 `templates_to_json`，读回来用的是 `templates_from_json`。
    两个函数一开始共用同一段写出逻辑，那段逻辑会输出 `"templates": { ... }` ——
    也就是**带键名的片段**。

    问题是：`"templates": {...}` **不是合法的 JSON**（一个裸的键值对，
    没有外层大括号）。存进去之后 `json.loads` 直接失败，
    然后被「坏了就当没有」的兜底逻辑吞掉。

    后果：**用户改的模板静默消失，一点报错都没有。**
    导入时预览看着好好的（预览用的是内存里的对象），关掉再打开就打回原形。

    这类「不报错的错」最危险，因为一切看起来都正常 ——
    只有「存进去再读出来」这种往返测试才能抓住它。

    手机版当时也有同样的 bug，是靠这组测试发现的。
    """

    def test_single_day_type_roundtrip(self):
        original = {DayType.A: builtin_data.templates()[DayType.A]}
        text = format_spec.templates_to_json(original)

        # 先说清楚它必须是合法 JSON —— 这是 bug 的根源
        json.loads(text)

        back = format_spec.templates_from_json(text)
        self.assertEqual(1, len(back))
        self.assertIn(DayType.A, back)
        # 不写死格数：模板内容以后可能变，写死会让测试变成「改数据就要改测试」
        self.assertEqual(len(original[DayType.A]), len(back[DayType.A]))
        self.assertEqual("00:00", slots.fmt(back[DayType.A][0].start))
        self.assertEqual("06:55", slots.fmt(back[DayType.A][0].end))

    def test_all_six_roundtrip(self):
        original = builtin_data.templates()
        back = format_spec.templates_from_json(format_spec.templates_to_json(original))

        # REST 存不进去是**故意的**（它不在 DAY_TYPE_ORDER 里，见 models）——
        # 本地存储和文件一样只装六套标准日型，REST 永远从内置来。
        self.assertNotIn(DayType.REST, back)
        self.assertEqual(6, len(back))
        for day_type in back:
            self.assertEqual(
                _sig_blocks(original[day_type]),
                _sig_blocks(back[day_type]),
                f"{day_type} 存取往返不一致",
            )

    def test_modified_value_survives(self):
        """改过的值必须原样存下来，而不是悄悄回落到内置"""
        templates = dict(builtin_data.templates())
        templates[DayType.A] = [
            Block(0, 7 * 60 + 30, "睡觉", kind=Kind.SLEEP),
            Block(7 * 60 + 30, 1440, "自定义", kind=Kind.STUDY),
        ]
        back = format_spec.templates_from_json(format_spec.templates_to_json(templates))

        self.assertEqual(7 * 60 + 30, engine.wake_minute(back[DayType.A]))
        self.assertEqual(7 * 60 + 30, engine.wake_minute(templates[DayType.A]))
        # 没改过的日型也要还在
        self.assertIn(DayType.SATURDAY, back)

    def test_garbage_returns_empty_not_crash(self):
        """存储坏了要让程序能起来，而不是崩在启动阶段"""
        for bad in ["", "   ", "不是 json", '{"A": []}', '{"ZZZ": []}', "null", "[]"]:
            self.assertEqual({}, format_spec.templates_from_json(bad), f"{bad!r} 应该返回空")


class TestRealPhoneExport(unittest.TestCase):
    """
    最重要的一组：**电脑版能不能正确读手机版导出的文件。**

    这是「两边互通」这个承诺的唯一证明。用的是手机版真实导出的
    example-full.json，不是构造的测试数据。
    """

    #: 两个项目是**同级目录**，所以从仓库往上找一层。
    #: 找不到就自动跳过（是 skipUnless，不是失败）——
    #: 别人 clone 下去只克隆这一个仓库时，这几条测试跳过即可，
    #: 不该因为「没有隔壁那个项目」而报红。
    _SIBLING = Path(__file__).resolve().parent.parent.parent / "06-android-timetable"
    PHONE_EXPORT = _SIBLING / "docs" / "example-full.json"
    PHONE_SIMPLE = _SIBLING / "docs" / "example-schedule.json"

    @unittest.skipUnless(PHONE_EXPORT.exists(), "找不到手机版导出的示例文件")
    def test_reads_phone_export(self):
        """
        手机版导出的 example-full.json 必须能被完整读出来。

        ⚠️ 这里断言的是**通用示例数据**（示例大学 / 大学英语 / 教一-101），
        不是用户本人的课表。手机版把 docs/ 下这两份示例改成了通用样例，
        所以期望值跟着改。

        （电脑版的 `builtin_data.py` 才是用户本人的课表，两份数据
        现在**故意不一样** —— 见下面那条说明为什么。）
        """
        text = self.PHONE_EXPORT.read_text(encoding="utf-8")
        p = format_spec.parse(text)

        self.assertEqual("示例大学 2026 级 · 大一上", p.term.name)
        self.assertEqual(date(2026, 9, 7), p.term.start_date)
        self.assertEqual(19, p.term.total_weeks)
        self.assertEqual(19, len(p.courses))
        self.assertEqual(6, len(p.templates))
        self.assertEqual([], p.warnings, f"示例数据不该有冲突：{p.warnings}")

    @unittest.skipUnless(PHONE_EXPORT.exists(), "找不到手机版导出的示例文件")
    def test_phone_export_is_v3_with_day_types(self):
        """
        手机版导出的文件是 **v3**，带 `dayTypes` 段 —— 电脑版必须能读。

        这一条是「格式标准对齐」的直接证据：光升版本号没用，
        得真能解析出那段内容才算数。
        """
        p = format_spec.parse(self.PHONE_EXPORT.read_text(encoding="utf-8"))

        self.assertIsNotNone(
            p.day_types,
            "手机版的文件带了 dayTypes 段，电脑版却解析成 None —— 格式没对齐",
        )
        # 手机版默认策略：A + 没早八的 B + 周末，训练日不启用
        self.assertIn(DayType.A, p.day_types.enabled)
        self.assertIn(DayType.B_NORMAL, p.day_types.enabled)
        self.assertIn(DayType.SATURDAY, p.day_types.enabled)
        self.assertIn(DayType.SUNDAY, p.day_types.enabled)
        self.assertNotIn(DayType.B_TRAIN_A, p.day_types.enabled)
        self.assertNotIn(DayType.B_TRAIN_B, p.day_types.enabled)
        self.assertEqual(DayType.B_NORMAL, p.day_types.fallback)

    @unittest.skipUnless(PHONE_EXPORT.exists(), "找不到手机版导出的示例文件")
    def test_timeline_from_phone_export_is_sound(self):
        """
        用手机导出的文件跑出来的时间轴必须是**正确**的。

        ## 这条测试改过（原来是和内置数据逐条比对）

        原来的写法是「手机导出的时间轴 == 电脑版内置数据的时间轴」。
        那个前提建立在「两份数据内容相同」上 —— 而手机版已经把
        docs/ 里的示例换成了通用样例（示例大学 / 大学英语），
        电脑版的 builtin_data 仍是用户本人的课表，两者**故意不同**了。
        所以那个比对不再是「验证」，只是「比较两份不同的数据」。

        换成一个不依赖两边数据相同的检查：**结构正确性**
        （首尾相接、无重叠、有课的时段真的出现那门课）。
        这样它仍然能证明「v3 文件解析没读错 + 引擎没走样」，
        而不会因为示例数据换了一版就失效 ——
        **测试不该依赖「另一个项目里的示例文件永远不变」。**
        """
        p = format_spec.parse(self.PHONE_EXPORT.read_text(encoding="utf-8"))
        policy = p.day_types or DayTypePolicy.DEFAULT

        # 手机版示例里周一 1-2 节是「大学英语」
        dt = TERM_START + timedelta(days=2 * 7)          # 第 3 周周一
        ms = engine.moments(dt, 3, p.courses, p.templates, policy)

        self.assertEqual(0, ms[0].start, "时间轴必须从 00:00 开始")
        self.assertEqual(1440, ms[-1].end, "时间轴必须到 24:00 结束")
        for i in range(1, len(ms)):
            self.assertEqual(ms[i - 1].end, ms[i].start,
                             f"{ms[i - 1].title} 和 {ms[i].title} 之间有缝或重叠")

        english = next((m for m in ms if m.is_course and "大学英语" in m.title), None)
        self.assertIsNotNone(english, "第 3 周周一应该有大学英语（文件里是 2-4 周）")
        self.assertEqual(slots.start(1), english.start)
        self.assertEqual("教一-101", english.place)

    @unittest.skipUnless(PHONE_EXPORT.exists(), "找不到手机版导出的示例文件")
    def test_phone_export_day_types_actually_change_the_timeline(self):
        """
        `dayTypes` 段必须**真的影响结果**，而不只是被解析出来放着。

        这是最容易做错的一步：解析、存储、序列化都对了，
        但引擎忘了用它 —— 用户改了启用日型却没有任何变化，
        而且不会报错。**只测「解析出来了」是不够的。**
        """
        p = format_spec.parse(self.PHONE_EXPORT.read_text(encoding="utf-8"))
        dt = TERM_START + timedelta(days=2 * 7 + 1)      # 第 3 周周二
        week = 3

        with_default = engine.moments(dt, week, p.courses, p.templates, DayTypePolicy.DEFAULT)
        with_all = engine.moments(dt, week, p.courses, p.templates, DayTypePolicy.ALL)

        # 周二晚上：全开时有训练，默认策略下没有
        def at(ms, hhmm):
            h, m = (int(x) for x in hhmm.split(":"))
            return engine.current_at(ms, h * 60 + m)

        self.assertEqual(Kind.TRAIN, at(with_all, "19:30").kind,
                         "六种全开时，周二晚上应该是训练")
        self.assertNotEqual(Kind.TRAIN, at(with_default, "19:30").kind,
                            "默认策略关掉了训练日，周二晚上不该再是训练")

        # 「用哪套模板」也要跟着变
        self.assertEqual(DayType.B_TRAIN_A,
                         engine.day_type(dt, week, p.courses, DayTypePolicy.ALL))
        self.assertEqual(DayType.B_NORMAL,
                         engine.day_type(dt, week, p.courses, DayTypePolicy.DEFAULT))

    @unittest.skipUnless(PHONE_EXPORT.exists(), "找不到手机版导出的示例文件")
    def test_serialize_reproduces_the_phone_export_byte_for_byte(self):
        """
        ★ 格式对齐最强的一条证据。

        标准 7.3 要求「导出同一份规划，永远得到逐字节相同的文件」。
        手机版的 docs/example-full.json 是**真实导出物**，所以：

            解析它 → 用电脑版重新序列化 → 和原文件逐字节比

        如果完全相同，说明两边在所有**人能看出来的细节**上都一致：
        字段顺序、缩进、空格、数组排版、周次压缩写法。

        ## 这条测试抓到过一个真 bug

        第一次跑的时候差了 1 行：电脑版把 courses 数组的逗号写成了**单独一行**

            ]
          ,

        而手机版是 `],`。JSON 照样能解析（逗号在词法上是分隔符，
        位置无所谓），所以结构断言完全看不出来 ——
        但同一份数据在两个平台上 diff 满天飞，
        「是不是真的一样」这类判断就没法做了。

        **「能被解析」不等于「格式对」。** 结构断言看不出这种差异，
        只有和权威产物比字节才看得出来。
        """
        original = self.PHONE_EXPORT.read_text(encoding="utf-8")
        p = format_spec.parse(original, fallback_total_weeks=19)

        rebuilt = format_spec.serialize(
            term_name=p.term.name,
            start_date=p.term.start_date,
            total_weeks=p.term.total_weeks,
            courses=p.courses,
            templates=p.templates,
            day_types=p.day_types,
        )

        # 只归一化换行符 —— CRLF/LF 是检出产物，不是格式差异
        self.assertEqual(
            original.replace("\r\n", "\n").rstrip("\n"),
            rebuilt.replace("\r\n", "\n").rstrip("\n"),
            "电脑版重新序列化的结果和手机版导出的文件不一致 ——\n"
            "两个平台的导出必须逐字节相同（标准 7.3）。\n"
            "用 tools/verify_format_parity.py 可以看到具体差在哪一行。",
        )

    @unittest.skipUnless(PHONE_SIMPLE.exists(), "找不到手机版导出的示例文件")
    def test_reads_courses_only_export(self):
        p = format_spec.parse(self.PHONE_SIMPLE.read_text(encoding="utf-8"))
        self.assertEqual(19, len(p.courses))
        self.assertIsNone(p.templates, "只导课表的文件不该带模板")


def _sig_course(c: Course):
    return (c.name, c.day_of_week, c.start_node, c.end_node, sorted(c.weeks), c.place, c.enabled)


def _sig_blocks(blocks):
    return [
        (b.start, b.end, b.title, b.note, b.kind.value,
         (b.nodes[0] if b.nodes else None), (b.nodes[1] if b.nodes else None))
        for b in blocks
    ]


if __name__ == "__main__":
    unittest.main(verbosity=2)
