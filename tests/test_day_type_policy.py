"""
日型策略的测试 —— 「这份配置实际用哪几种日型」。

这是手机版 `DayTypePolicyTest.kt` 的 Python 对应版本，用例逐条对齐。

## 背景

引擎原来固定算六种日型，其中 `B_TRAIN_A / B_TRAIN_B`（周二力量A、周四力量B）
来自某一份具体规划表里的训练安排，不是通用规律。
现在改成可配置：JSON 里写 `dayTypes.enabled` 决定用哪几种，
不在其中的一律落到 `dayTypes.fallback`。

## 这个文件守两件事

  ① **默认策略不能把「有早八 / 没早八」这档区分弄丢** ——
     那是这个程序存在的理由（它回答的就是「今天要不要早起」）
  ② 策略要真的穿透到 `engine.moments`，而不只是改个标签

## 为什么两边各有一份测试

`test_core.py` 测引擎（用 ALL 策略当恒等映射），这里测策略本身。
分开是因为**策略错了和引擎错了表现完全不同**：
引擎错是时间轴不对，策略错是「用错了哪套模板」——
后者更隐蔽（时间轴看起来完全正常，只是比该起的时间早/晚了半小时）。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import builtin_data, engine, format_spec, slots  # noqa: E402
from app.day_type_policy import DayTypePolicy  # noqa: E402
from app.models import DayType, Kind  # noqa: E402

TERM_START = date(2026, 9, 7)      # 教学第 1 周的周一


def d(day: int) -> date:
    return date(2026, 9, day)


class PolicyTestCase(unittest.TestCase):
    """第 3 周的几天 —— 周一 9/21、周二 9/22、周四 9/24、周五 9/25、周六 9/26、周日 9/27"""

    def setUp(self):
        self.courses = builtin_data.courses()
        self.templates = builtin_data.templates()
        self.mon, self.tue, self.thu = d(21), d(22), d(24)
        self.fri, self.sat, self.sun = d(25), d(26), d(27)

    def type_of(self, dt: date, policy: DayTypePolicy) -> DayType:
        return engine.day_type(dt, 3, self.courses, policy)

    def wake_of(self, dt: date, policy: DayTypePolicy):
        """这一天几点起（从它用的那套模板推出来）"""
        t = self.type_of(dt, policy)
        return engine.wake_minute(self.templates[t])

    def moments_of(self, dt: date, policy: DayTypePolicy):
        return engine.moments(dt, 3, self.courses, self.templates, policy)


# ============================================================
#  一、原始规则本身没有被改坏
# ============================================================

class TestNaturalRuleUntouched(PolicyTestCase):
    def test_natural_rule_still_yields_all_six(self):
        """
        策略层的改动**不能**动到日历规则本身。

        这条保证了「想让训练日生效」时，引擎还拿得出那个答案 ——
        如果这里直接把训练日那两行删掉，就再也没有回旋余地了。
        """
        self.assertEqual(DayType.A, engine.natural_day_type(self.mon, 3, self.courses))
        self.assertEqual(DayType.B_TRAIN_A, engine.natural_day_type(self.tue, 3, self.courses))
        self.assertEqual(DayType.B_TRAIN_B, engine.natural_day_type(self.thu, 3, self.courses))
        self.assertEqual(DayType.B_NORMAL, engine.natural_day_type(self.fri, 3, self.courses))
        self.assertEqual(DayType.SATURDAY, engine.natural_day_type(self.sat, 3, self.courses))
        self.assertEqual(DayType.SUNDAY, engine.natural_day_type(self.sun, 3, self.courses))

    def test_all_policy_is_identity(self):
        """ALL 策略不该改变任何日型"""
        for dt in (self.mon, self.tue, self.thu, self.fri, self.sat, self.sun):
            self.assertEqual(
                engine.natural_day_type(dt, 3, self.courses),
                self.type_of(dt, DayTypePolicy.ALL),
                f"{dt} 在 ALL 策略下被改动了",
            )


# ============================================================
#  二、默认策略：保住核心区分，去掉训练日细分
# ============================================================

class TestDefaultPolicy(PolicyTestCase):
    def test_training_days_are_gone_by_default(self):
        self.assertEqual(DayType.B_NORMAL, self.type_of(self.tue, DayTypePolicy.DEFAULT))
        self.assertEqual(DayType.B_NORMAL, self.type_of(self.thu, DayTypePolicy.DEFAULT))

    def test_early_class_still_maps_to_a(self):
        self.assertEqual(DayType.A, self.type_of(self.mon, DayTypePolicy.DEFAULT))

    def test_weekend_survives(self):
        self.assertEqual(DayType.SATURDAY, self.type_of(self.sat, DayTypePolicy.DEFAULT))
        self.assertEqual(DayType.SUNDAY, self.type_of(self.sun, DayTypePolicy.DEFAULT))

    def test_no_early_class_still_wakes_at_0725(self):
        """
        ★ 这条是默认值取舍的核心。

        「有早八 06:55 起 / 没早八 07:25 起」是这个程序存在的理由 ——
        它回答的就是「今天要不要早起」。默认策略削掉了训练日的细分，
        但**绝不能**顺手把这一档也弄丢：那样周二会被按 A 型排，
        没早八的日子也会 06:55 把人叫起来。
        """
        self.assertEqual(7 * 60 + 25, self.wake_of(self.tue, DayTypePolicy.DEFAULT))
        self.assertEqual(7 * 60 + 25, self.wake_of(self.thu, DayTypePolicy.DEFAULT))
        self.assertEqual(7 * 60 + 25, self.wake_of(self.fri, DayTypePolicy.DEFAULT))
        # 有早八的日子照旧 06:55
        self.assertEqual(6 * 60 + 55, self.wake_of(self.mon, DayTypePolicy.DEFAULT))

    def test_policy_reaches_the_timeline_not_just_the_label(self):
        """
        策略要真的穿透到时间轴，而不只是改了标签。

        日型不一致最典型的坏法：界面显示 B 型、实际按 A 型排。
        这条不看日型名字，**直接看排出来的时间轴** ——
        每套模板的第一格都是「00:00 到起床」，所以第一格的结束时刻
        就是那天的起床时间，一目了然。
        """
        tue_default = self.moments_of(self.tue, DayTypePolicy.DEFAULT)
        self.assertEqual(7 * 60 + 25, tue_default[0].end, "周二应当按 B 型日排（07:25 起）")

        tue_all = self.moments_of(self.tue, DayTypePolicy.ALL)
        self.assertEqual(7 * 60 + 25, tue_all[0].end,
                         "ALL 策略下周二走训练日模板，同样是 07:25 起")

        # 真正证明「策略生效了」的是：两种策略排出来的东西**不一样**。
        # 只断言起床时间相同是测不出来的 —— 两套模板碰巧都 07:25 起。
        self.assertNotEqual(
            [(m.start, m.end, m.title) for m in tue_default],
            [(m.start, m.end, m.title) for m in tue_all],
            "默认策略和 ALL 策略在周二应当排出不同的时间轴",
        )

        mon_default = self.moments_of(self.mon, DayTypePolicy.DEFAULT)
        self.assertEqual(6 * 60 + 55, mon_default[0].end, "周一有早八，仍是 06:55 起")


# ============================================================
#  三、自定义策略
# ============================================================

class TestCustomPolicy(PolicyTestCase):
    def test_only_a_and_weekend(self):
        """
        用户明确选择「我只要 A 型和周末」—— 这时周二就该按 A 型走，
        哪怕那意味着没早八的日子也 06:55 起床。
        **这是用户的选择，不是 bug。**
        """
        p = DayTypePolicy(
            enabled=frozenset({DayType.A, DayType.SATURDAY, DayType.SUNDAY}),
            fallback=DayType.A,
        )
        self.assertEqual(DayType.A, self.type_of(self.tue, p))
        self.assertEqual(DayType.A, self.type_of(self.thu, p))
        self.assertEqual(DayType.A, self.type_of(self.fri, p))
        self.assertEqual(DayType.SATURDAY, self.type_of(self.sat, p))
        self.assertEqual(6 * 60 + 55, self.wake_of(self.tue, p))

    def test_fallback_may_be_outside_enabled(self):
        """
        「只启用 A 和周末，但周中没早八时要回落成 B 型」是完全正当的用法。

        fallback 表达的是「拿哪套模板兜底」，和「启用了哪些日型」不是一回事。
        """
        p = DayTypePolicy(
            enabled=frozenset({DayType.A, DayType.SATURDAY}),
            fallback=DayType.B_NORMAL,
        )
        self.assertNotIn(DayType.B_NORMAL, p.enabled)
        self.assertEqual(DayType.B_NORMAL, self.type_of(self.fri, p))
        self.assertEqual(7 * 60 + 25, self.wake_of(self.fri, p))

    def test_only_training_day(self):
        p = DayTypePolicy(
            enabled=frozenset({DayType.B_TRAIN_A}),
            fallback=DayType.B_TRAIN_A,
        )
        self.assertEqual(DayType.B_TRAIN_A, self.type_of(self.mon, p))
        self.assertEqual(DayType.B_TRAIN_A, self.type_of(self.tue, p))
        self.assertEqual(DayType.B_TRAIN_A, self.type_of(self.sat, p))


# ============================================================
#  四、日型描述不能说假话
# ============================================================

class TestDayTypeDisplay(PolicyTestCase):
    def test_default_policy_says_has_early_class_on_monday(self):
        label = engine.day_type_display(self.mon, 3, self.courses, DayTypePolicy.DEFAULT)
        self.assertIn("有早八", label)

    def test_label_does_not_lie_under_custom_policy(self):
        """
        ★ 这是「A 的标签为什么要去掉『有早八』」的直接验证。

        用户把工作日全回落到 A 型之后，周二（当天**没有**早八）
        会被标成 A 型日 —— 如果标签里写死「有早八」，那就是一句假话。

        所以「有没有早八」去问日历，「用哪套模板」去问策略。
        """
        p = DayTypePolicy(
            enabled=frozenset({DayType.A, DayType.SATURDAY, DayType.SUNDAY}),
            fallback=DayType.A,
        )
        label = engine.day_type_display(self.tue, 3, self.courses, p)

        # 用的是 A 型模板
        self.assertIn("A 型日", label)
        # 但今天**没有**早八，所以不能说「有早八」
        self.assertNotIn("有早八", label,
                         f"周二没有早八，却显示成「{label}」—— 那是假话")

    def test_weekend_label(self):
        label = engine.day_type_display(self.sat, 3, self.courses, DayTypePolicy.DEFAULT)
        self.assertNotIn("有早八", label)
        self.assertIn("周六", label)


# ============================================================
#  五、JSON 解析
# ============================================================

class TestParseDayTypes(unittest.TestCase):
    @staticmethod
    def doc(day_types: str) -> str:
        return (
            '{"format":"timetable","version":3,"dayTypes":' + day_types
            + ',"courses":[]}'
        )

    def parsed(self, json_text: str) -> format_spec.Parsed:
        return format_spec.parse(json_text, 19)

    def failed(self, json_text: str) -> str:
        with self.assertRaises(format_spec.FormatError) as cm:
            format_spec.parse(json_text, 19)
        return str(cm.exception)

    def test_reads_a_complete_segment(self):
        p = self.parsed(
            self.doc('{"enabled":["A","SATURDAY","SUNDAY"],"fallback":"A"}')
        ).day_types
        self.assertEqual(
            frozenset({DayType.A, DayType.SATURDAY, DayType.SUNDAY}), p.enabled
        )
        self.assertEqual(DayType.A, p.fallback)

    def test_missing_segment_means_do_not_touch(self):
        """
        没写 dayTypes 段时是 None，表示「不动」。

        和 courses 一样的语义：如果当成「用默认值」，
        用户每导入一份只改课表的文件，辛苦配好的日型策略都会被打回默认。
        """
        json_text = (
            '{"format":"timetable","version":3,'
            '"courses":[{"name":"高数","dayOfWeek":1,"nodes":[1,2],"weeks":"1-5"}]}'
        )
        self.assertIsNone(self.parsed(json_text).day_types)

    def test_empty_enabled_is_an_error(self):
        msg = self.failed(self.doc('{"enabled":[],"fallback":"A"}'))
        self.assertIn("至少要启用一种", msg)

    def test_missing_enabled_is_an_error(self):
        msg = self.failed(self.doc('{"fallback":"A"}'))
        self.assertIn("enabled", msg)

    def test_names_are_case_insensitive(self):
        p = self.parsed(
            self.doc('{"enabled":["a","b_normal","saturday"],"fallback":"b_normal"}')
        ).day_types
        self.assertEqual(
            frozenset({DayType.A, DayType.B_NORMAL, DayType.SATURDAY}), p.enabled
        )
        self.assertEqual(DayType.B_NORMAL, p.fallback)

    def test_unknown_name_lists_the_available_ones(self):
        msg = self.failed(self.doc('{"enabled":["A","FOO"],"fallback":"A"}'))
        self.assertIn("第 2 项", msg)
        self.assertIn("B_TRAIN_A", msg)

    def test_fallback_defaults_to_first_enabled_in_standard_order(self):
        """
        省略 fallback 时，按**标准顺序**取第一个已启用项。

        用固定顺序而不是集合遍历顺序 —— 后者每次运行可能不同，
        会让同一份文件解析出不同结果，直接违反「输出是确定性的」那条约定。
        """
        p = self.parsed(self.doc('{"enabled":["SUNDAY","B_TRAIN_B"]}')).day_types
        # 标准顺序 A, B_TRAIN_A, B_TRAIN_B, B_NORMAL, SATURDAY, SUNDAY
        # 被启用的有 B_TRAIN_B 和 SUNDAY → 取更靠前的 B_TRAIN_B
        self.assertEqual(DayType.B_TRAIN_B, p.fallback)

    def test_only_day_types_is_still_valid_content(self):
        """一份只写 dayTypes 的文件是正当的（只想改日型，课表模板都别动）"""
        p = self.parsed(self.doc('{"enabled":["A"],"fallback":"A"}'))
        self.assertIsNone(p.courses)
        self.assertIsNone(p.templates)
        self.assertIsNotNone(p.day_types)

    def test_all_four_empty_is_an_error(self):
        msg = self.failed('{"format":"timetable","version":3}')
        for segment in ("term", "courses", "templates", "dayTypes"):
            self.assertIn(segment, msg, f"报错里应当提到 {segment} 这一段")

    def test_enabled_must_be_an_array(self):
        msg = self.failed(self.doc('{"enabled":"A","fallback":"A"}'))
        self.assertIn("必须是一个数组", msg)

    def test_v2_file_is_still_accepted(self):
        """v2 的文件（没有 dayTypes）照常能导入 —— 版本号只增不减的兼容性"""
        json_text = (
            '{"format":"timetable","version":2,'
            '"courses":[{"name":"高数","dayOfWeek":1,"nodes":[1,2],"weeks":"1-5"}]}'
        )
        p = self.parsed(json_text)
        self.assertEqual(1, len(p.courses))
        self.assertIsNone(p.day_types)

    def test_newer_version_is_refused(self):
        """遇到比自己新的版本要明确拒绝，而不是硬着头皮解析"""
        json_text = (
            '{"format":"timetable","version":99,'
            '"courses":[{"name":"高数","dayOfWeek":1,"nodes":[1,2],"weeks":"1-5"}]}'
        )
        msg = self.failed(json_text)
        self.assertIn("v99", msg)
        self.assertIn("请更新", msg)


# ============================================================
#  六、序列化与往返
# ============================================================

class TestSerializeDayTypes(unittest.TestCase):
    def test_roundtrip_through_a_full_file(self):
        policy = DayTypePolicy(
            enabled=frozenset({DayType.A, DayType.SATURDAY, DayType.SUNDAY}),
            fallback=DayType.B_NORMAL,
        )
        text = format_spec.serialize(
            "测试学期", TERM_START, 19, [],
            builtin_data.templates(), policy,
        )
        back = format_spec.parse(text, 19).day_types
        self.assertEqual(policy.enabled, back.enabled)
        self.assertEqual(policy.fallback, back.fallback)

    def test_output_order_is_fixed(self):
        """
        输入顺序不同、内容相同 → 输出必须一致。

        否则同一份数据每次导出都产生 diff，版本对比就没法看了。
        """
        a = DayTypePolicy(
            enabled=frozenset({DayType.SUNDAY, DayType.A, DayType.SATURDAY}),
            fallback=DayType.A,
        )
        b = DayTypePolicy(
            enabled=frozenset({DayType.A, DayType.SATURDAY, DayType.SUNDAY}),
            fallback=DayType.A,
        )
        self.assertEqual(format_spec.day_types_to_json(a),
                         format_spec.day_types_to_json(b))
        self.assertIn('["A", "SATURDAY", "SUNDAY"]',
                      format_spec.day_types_to_json(a))

    def test_day_types_come_after_term_and_before_courses(self):
        """
        段的位置是约定的一部分（和手机版一致）：

            format → version → term → dayTypes → courses → templates

        「输出是确定性的」要求两边逐字节相同，所以这个位置不是随便挑的。
        """
        text = format_spec.serialize(
            "学期", TERM_START, 19,
            [], builtin_data.templates(), DayTypePolicy.DEFAULT,
        )
        i_term = text.index('"term"')
        i_dt = text.index('"dayTypes"')
        i_courses = text.index('"courses"')
        i_templates = text.index('"templates"')
        self.assertLess(i_term, i_dt)
        self.assertLess(i_dt, i_courses)
        self.assertLess(i_courses, i_templates)

    def test_omitting_the_policy_writes_no_segment(self):
        """不给策略就不写这一段 —— 「没写 = 不要动」的前提是别乱写"""
        text = format_spec.serialize("学期", TERM_START, 19, [])
        self.assertNotIn("dayTypes", text)


# ============================================================
#  七、本地存储往返
# ============================================================

class TestLocalStorage(unittest.TestCase):
    def test_roundtrip(self):
        """
        这个坑 templates 那边踩过一次：写出去的形状和读回来的对不上，
        结果用户改的设置**静默消失**。所以每个存储格式都要有一条往返测试。
        """
        policy = DayTypePolicy(
            enabled=frozenset({DayType.A, DayType.B_TRAIN_A, DayType.SATURDAY}),
            fallback=DayType.B_TRAIN_A,
        )
        back = format_spec.day_types_from_json(format_spec.day_types_to_json(policy))
        self.assertEqual(policy.enabled, back.enabled)
        self.assertEqual(policy.fallback, back.fallback)

    def test_corrupt_storage_falls_back_to_default(self):
        """存储损坏不能变成「程序打不开」—— 大不了重新配一遍"""
        for bad in ["", "   ", "{坏掉的", "null", "[]", '{"enabled":[]}']:
            self.assertEqual(
                DayTypePolicy.DEFAULT,
                format_spec.day_types_from_json(bad),
                f"{bad!r} 应该回落到默认策略",
            )

    def test_default_policy_survives_a_roundtrip(self):
        back = format_spec.day_types_from_json(
            format_spec.day_types_to_json(DayTypePolicy.DEFAULT)
        )
        self.assertEqual(DayTypePolicy.DEFAULT, back)


# ============================================================
#  八、设置页用的接口（电脑版特有）
# ============================================================
#
# 手机版那边是 Android 界面 + SharedPreferences，接口形状不同，
# 所以这一组没有对应的 Kotlin 测试 —— 它是电脑版自己的。
#
# 但**守的东西是一样的**：加上界面之后，用户能改的东西变多了，
# 而「改了没生效」正是这种功能最典型的坏法。

class DayTypeApiTestCase(unittest.TestCase):
    """会构造真实的 Store，所以数据目录必须隔离"""

    def setUp(self):
        import os
        import shutil
        import tempfile

        tmp = tempfile.mkdtemp(prefix="timetable_daytype_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        saved = os.environ.get("TIMETABLE_DATA_DIR")
        os.environ["TIMETABLE_DATA_DIR"] = tmp

        def restore():
            if saved is None:
                os.environ.pop("TIMETABLE_DATA_DIR", None)
            else:
                os.environ["TIMETABLE_DATA_DIR"] = saved

        self.addCleanup(restore)

        from app.api import Api
        from app.store import Store

        self.store = Store()
        # 隔离没生效就拒绝往下跑 —— 这些方法会写盘
        self.assertTrue(
            str(self.store.path).startswith(tmp),
            f"数据目录没隔离成功：{self.store.path}",
        )
        self.api = Api(store=self.store)


class TestDayTypeApi(DayTypeApiTestCase):
    def test_get_returns_all_six_with_wake_times(self):
        """
        六个日型都要返回，每个都要带**起床时间**。

        起床时间是界面上的必需信息：用户勾选时得看得出代价 ——
        「把工作日全勾成 A 型」意味着没早八的日子也 06:55 起床。
        只给个名字的话，那个代价是看不见的。
        """
        d = self.api.get_day_types()
        self.assertEqual(6, len(d["types"]))
        for t in d["types"]:
            self.assertIn("key", t)
            self.assertIn("label", t)
            self.assertIn("wake", t, f"{t['key']} 没带起床时间")
            self.assertIsNotNone(t["wake"], f"{t['key']} 的起床时间算不出来")

    def test_get_marks_the_current_fallback(self):
        d = self.api.get_day_types()
        marked = [t["key"] for t in d["types"] if t["isFallback"]]
        self.assertEqual(["B_NORMAL"], marked, "默认策略的回落项是 B_NORMAL")

    def test_default_state_is_the_four_core_types(self):
        d = self.api.get_day_types()
        enabled = sorted(t["key"] for t in d["types"] if t["enabled"])
        self.assertEqual(["A", "B_NORMAL", "SATURDAY", "SUNDAY"], enabled)
        self.assertFalse(d["hasCustomDayTypes"], "没设置过就该是 false")

    def test_save_persists_and_reports(self):
        r = self.api.save_day_types(["A", "SATURDAY", "SUNDAY"], "A")
        self.assertTrue(r["ok"], r.get("message"))
        self.assertTrue(r["dayTypes"]["hasCustomDayTypes"], "存过之后应当标记为已自定义")

        # 重新读一遍，确认真的落盘了（而不是只改了内存）
        from app.store import Store
        again = Store().day_type_policy()
        self.assertEqual(frozenset({DayType.A, DayType.SATURDAY, DayType.SUNDAY}),
                         again.enabled)
        self.assertEqual(DayType.A, again.fallback)

    def test_save_actually_changes_behaviour(self):
        """
        ★ 最重要的一条：保存之后，**起床时间真的跟着变**。

        只验「存下来了」是不够的 —— 解析对了、存储对了、序列化也对了，
        但引擎忘了用它，用户改了设置却没有任何变化，而且不会报错。
        **「存下来」和「生效」是两件事，必须分别验。**

        ## ⚠️ 这条测试第一版是**假通过**的，所以写成现在这样

        第一版只断言了「周五的日型是 A」和「A 型模板 06:55 起床」——
        而**后者恒真**（A 型模板本来就 06:55），根本没比较改动前后。
        加了策略却完全不生效时它照样绿。

        现在改成：**先量一次、改、再量一次，断言两次不同**。
        这样只有「策略真的穿透到时间轴」才可能通过。
        """
        fri = date(2026, 9, 25)          # 第 3 周周五，没早八

        def friday_wake():
            """周五实际几点起 —— 走完整的「取模板 → 推起床时间」链路"""
            templates, policy = self.store.template_set()
            used = engine.day_type(fri, 3, self.store.courses(), policy)
            return used, engine.wake_minute(templates[used])

        # ---- 改之前：默认策略把周五算成 B_NORMAL，07:25 起 ----
        used_before, wake_before = friday_wake()
        self.assertEqual(DayType.B_NORMAL, used_before)
        self.assertEqual(7 * 60 + 25, wake_before)

        # ---- 改：fallback 设成 A，于是没早八的工作日也走 A 型 ----
        r = self.api.save_day_types(["A", "SATURDAY", "SUNDAY"], "A")
        self.assertTrue(r["ok"], r.get("message"))

        # ---- 改之后：同一个周五，应当变成 A 型、06:55 起 ----
        used_after, wake_after = friday_wake()
        self.assertEqual(DayType.A, used_after, "周五没有回落到 A 型 —— 策略没生效")
        self.assertEqual(6 * 60 + 55, wake_after, "起床时间没跟着变 —— 策略没穿透到时间轴")

        # 真正说明问题的是这一句：**两次结果不一样**
        self.assertNotEqual(
            (used_before, wake_before), (used_after, wake_after),
            "改前改后完全一样 —— 这个设置等于没有用",
        )

    def test_empty_selection_is_refused(self):
        """一种都不勾要拦下来并给出可操作的说法"""
        r = self.api.save_day_types([], "A")
        self.assertFalse(r["ok"])
        self.assertIn("至少", r["message"])

    def test_fallback_outside_enabled_is_refused_by_the_ui(self):
        """
        ⚠️ 界面比**文件格式**更严 —— 这个差异是刻意的，别去「统一」它。

            文件：fallback 允许不在 enabled 里（正当用法：
                  "只启用 A 和周末，但周中没早八时回落成 B 型"）
            界面：不允许，因为那是个下拉框 ——
                  让用户选一个没勾选的日型，等于给了个自相矛盾的控件

        测试在这里把差异钉住：以后有人看到两边不一致想去「对齐」，
        会先看到这段说明。
        """
        r = self.api.save_day_types(["A", "SATURDAY"], "B_NORMAL")
        self.assertFalse(r["ok"])
        self.assertIn("没有被勾选", r["message"])
        # 报错要指出还有别的路子，而不是只说「不行」
        self.assertIn("导入", r["message"])

        # 而文件格式那边**允许**同样内容 —— 证明差异确实存在
        text = ('{"format":"timetable","version":3,'
                '"dayTypes":{"enabled":["A","SATURDAY"],"fallback":"B_NORMAL"}}')
        parsed = format_spec.parse(text, 19)
        self.assertEqual(DayType.B_NORMAL, parsed.day_types.fallback)
        self.assertNotIn(DayType.B_NORMAL, parsed.day_types.enabled)

    def test_unknown_type_is_refused(self):
        r = self.api.save_day_types(["A", "FOO"], "A")
        self.assertFalse(r["ok"])
        self.assertIn("FOO", r["message"])

    def test_unknown_fallback_is_refused(self):
        r = self.api.save_day_types(["A"], "MONDAY")
        self.assertFalse(r["ok"])
        self.assertIn("MONDAY", r["message"])

    def test_duplicates_are_collapsed(self):
        r = self.api.save_day_types(["A", "A", "A"], "A")
        self.assertTrue(r["ok"], r.get("message"))
        self.assertEqual(1, len([t for t in r["dayTypes"]["types"] if t["enabled"]]))

    def test_reset_restores_the_default(self):
        self.api.save_day_types(["A", "B_TRAIN_A", "B_TRAIN_B"], "B_NORMAL")
        r = self.api.reset_day_types()
        self.assertTrue(r["ok"])
        self.assertFalse(r["dayTypes"]["hasCustomDayTypes"])
        enabled = sorted(t["key"] for t in r["dayTypes"]["types"] if t["enabled"])
        self.assertEqual(["A", "B_NORMAL", "SATURDAY", "SUNDAY"], enabled)

    def test_every_response_is_json_serializable(self):
        """
        桥接返回值必须能 json.dumps。

        这个项目真的栽过一次：`import_from_file` 返回里夹了个 dataclass，
        整个导入功能因此不可用（`tests/test_api_serializable.py` 有完整记录）。
        新接口一律在这里过一遍。
        """
        import json
        responses = [
            self.api.get_day_types(),
            self.api.save_day_types(["A", "SATURDAY"], "A"),
            self.api.save_day_types([], "A"),               # 失败路径也要能序列化
            self.api.reset_day_types(),
        ]
        for i, r in enumerate(responses):
            try:
                json.dumps(r)
            except TypeError as e:
                self.fail(f"第 {i + 1} 个返回值不能 JSON 序列化：{e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
