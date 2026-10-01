"""
日型配置的测试 —— 「哪些日型存在、星期几用哪种、早八优先层」（v5 模型）。

## 模型变了，测试意图没变

v4 及更早是 DayTypePolicy（启用集合 + 回落），v5 换成 DayTypeConfig
（types / weekday_map / early_type）：日型可创建删除、星期可映射、
早八优先层可指定。这个文件的用例整体重写，但守的还是同两件事：

  ① **配置不能把「有早八 / 没早八」这档区分弄丢** ——
     那是这个程序存在的理由（它回答的就是「今天要不要早起」）
  ② 配置要真的穿透到 `engine.moments`，而不只是改个标签

## 为什么两边各有一份测试

`test_core.py` 测引擎（用恒等配置当基线），这里测配置本身。
分开是因为**配置错了和引擎错了表现完全不同**：
引擎错是时间轴不对，配置错是「用错了哪套模板」——
后者更隐蔽（时间轴看起来完全正常，只是比该起的时间早/晚了半小时）。

## 旧模型的测试意图怎么搬的

  · 「策略映射行为」   → 星期映射 + 早八优先层（TestWeekdayMap 等）
  · 「REST 不参与映射」→ TestRestDay 原样保留
  · 「标签不说谎」     → TestDayTypeDisplay 原样保留
  · v3/v4 文件解析     → TestLegacyDayTypesMigration（旧段 → 迁移成映射）
  · 「界面比文件严」   → 消失：新模型界面没有 fallback 这个控件了，
    文件侧的宽容（fallback 可在 enabled 外）归入迁移测试。
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import builtin_data, engine, format_spec, slots  # noqa: E402
from app.day_type_policy import (  # noqa: E402
    DEFAULT_CONFIG,
    MAX_DAY_TYPES,
    DayTypeConfig,
    DayTypePolicy,
    config_from_policy,
)
from app.models import DayType, Kind  # noqa: E402

TERM_START = date(2026, 9, 7)      # 教学第 1 周的周一


def d(day: int) -> date:
    return date(2026, 9, day)


#: 「恒等配置」——星期映射到旧自然槽位（周二→训练A、周四→训练B…）、
#: 早八优先层 = A。它让 resolve_day_type 的行为与 v4 的
#: natural_day_type + DayTypePolicy.ALL **逐日一致**：
#: 测「引擎原始行为」（训练日模板还在、周二晚上真有训练）用它。
IDENTITY = config_from_policy(DayTypePolicy.ALL)


# ============================================================
#  一、引擎判定：默认配置与恒等配置
# ============================================================

class ConfigTestCase(unittest.TestCase):
    """公共搭建：第 2 周（9/14–9/20）和第 3 周（9/21–9/27）的内置数据"""

    def setUp(self):
        self.courses = builtin_data.courses()
        self.templates = builtin_data.templates()

    def type_of(self, dt: date, config: DayTypeConfig) -> DayType:
        week = (dt - TERM_START).days // 7 + 1
        return engine.day_type(dt, week, self.courses, config)

    def wake_of(self, dt: date, config: DayTypeConfig):
        week = (dt - TERM_START).days // 7 + 1
        used = engine.day_type(dt, week, self.courses, config)
        return engine.wake_minute(self.templates[used])


class TestEngineRules(ConfigTestCase):
    """三条判定顺序（resolve_day_type）—— ①休息日 ②早八层 ③星期映射"""

    def test_early_class_takes_priority_on_weekday(self):
        # 第 3 周周一有英语（1-2 节）→ 走早八层 → A 型
        self.assertEqual(DayType.A, self.type_of(d(21), DEFAULT_CONFIG))
        self.assertTrue(engine.has_early_class(d(21), 3, self.courses))

    def test_weekday_without_early_class_follows_map(self):
        # 第 2 周周五（9/18）有课但没早八 → 星期映射 → B 型（默认映射工作日全是 2）
        self.assertEqual(DayType.B_NORMAL, self.type_of(date(2026, 9, 18), DEFAULT_CONFIG))

    def test_class_free_weekday_is_rest(self):
        # 第 1 周周一（英语 2-4 周才开）→ 全天没课 → 休息日，
        # 不是按星期映射把人按 07:25 的节奏排满
        self.assertEqual(DayType.REST, self.type_of(date(2026, 9, 7), DEFAULT_CONFIG))
        # 第 8 周周三同理
        self.assertEqual(DayType.REST, self.type_of(date(2026, 10, 28), DEFAULT_CONFIG))

    def test_weekend_follows_map_even_without_classes(self):
        # 周末不判休息日（周末作息本来就不由课表驱动）
        self.assertEqual(DayType.SATURDAY, self.type_of(d(26), DEFAULT_CONFIG))
        self.assertEqual(DayType.SUNDAY, self.type_of(d(27), DEFAULT_CONFIG))

    def test_early_layer_can_be_switched_off(self):
        """early_type=None = 不区分早八：有早八的日子也按星期映射走"""
        no_early = DayTypeConfig(DEFAULT_CONFIG.types, DEFAULT_CONFIG.weekday_map, None)
        self.assertEqual(DayType.B_NORMAL, self.type_of(d(21), no_early),
                         "取消早八区分后，周一（有早八）该按映射走 B 型")


class TestIdentityConfigGolden(ConfigTestCase):
    """
    ★ 迁移的金测试：恒等配置（config_from_policy(ALL)）必须与
    v4 的「自然规则 + 恒等映射」**逐日一致** ——
    这是「v5 没把老行为改坏」的最强证据。
    """

    #: 旧自然规则的对照表（日期 → v4 的 natural_day_type + policy=ALL 的答案）
    GOLDEN = [
        (d(21), 3, DayType.A),          # 周一有早八
        (d(22), 3, DayType.B_TRAIN_A),  # 周二晚训练（无早八）
        (d(24), 3, DayType.B_TRAIN_B),  # 周四力量B
        (date(2026, 9, 18), 2, DayType.B_NORMAL),   # 周五有课无早八
        (d(26), 3, DayType.SATURDAY),
        (d(27), 3, DayType.SUNDAY),
        (date(2026, 9, 7), 1, DayType.REST),     # 开学前周一无课
        (date(2026, 10, 28), 8, DayType.REST),   # 第 8 周周三无课
    ]

    def test_identity_reproduces_the_v4_natural_table(self):
        for dt, week, want in self.GOLDEN:
            self.assertEqual(want, self.day(dt, week),
                             f"{dt} 与 v4 自然规则不一致")

    def day(self, dt: date, week: int) -> DayType:
        return engine.day_type(dt, week, self.courses, IDENTITY)

    def test_identity_map_is_the_legacy_slots(self):
        self.assertEqual(
            [DayType.B_NORMAL, DayType.B_TRAIN_A, DayType.B_NORMAL,
             DayType.B_TRAIN_B, DayType.B_NORMAL, DayType.SATURDAY,
             DayType.SUNDAY],
            list(IDENTITY.weekday_map),
        )
        self.assertEqual(DayType.A, IDENTITY.early_type)

    def test_default_config_is_the_migrated_legacy_default(self):
        """新默认 == 旧默认策略迁移的结果（结构性金测试）"""
        self.assertEqual(config_from_policy(DayTypePolicy.DEFAULT), DEFAULT_CONFIG)

    def test_training_days_are_gone_by_default(self):
        """默认配置下周二晚上不该出现训练块 —— 「训练日」不是通用规律"""
        week = 3
        dt = d(22)
        ms = engine.moments(dt, week, self.courses, self.templates, DEFAULT_CONFIG)
        m = engine.current_at(ms, 19 * 60 + 30)
        self.assertNotIn("训练", m.title)
        self.assertNotEqual(Kind.TRAIN, m.kind)

    def test_training_reappears_under_identity(self):
        """对照：恒等配置（周二映射到训练日）下训练块真的在"""
        ms = engine.moments(d(22), 3, self.courses, self.templates, IDENTITY)
        m = engine.current_at(ms, 19 * 60 + 30)
        self.assertIn("训练", m.title)
        self.assertEqual(Kind.TRAIN, m.kind)


class TestWeekdayMap(ConfigTestCase):
    """星期映射 + 早八层的组合行为"""

    def make_config(self, map_over, early=DayType.A):
        m = list(DEFAULT_CONFIG.weekday_map)
        for dow, t in map_over.items():
            m[dow - 1] = t
        return DayTypeConfig(DEFAULT_CONFIG.types, tuple(m), early)

    def test_map_decides_the_weekday(self):
        cfg = self.make_config({2: DayType.B_TRAIN_A})   # 周二 → 训练日A
        self.assertEqual(DayType.B_TRAIN_A, self.type_of(d(22), cfg))
        self.assertEqual(DayType.B_NORMAL, self.type_of(date(2026, 9, 18), cfg),
                         "没被改派的星期仍走默认映射")

    def test_map_change_reaches_the_timeline_not_just_the_label(self):
        """配置要真的穿透到时间轴 —— 只改个标签不算数"""
        week = 3
        ms_default = engine.moments(d(22), week, self.courses,
                                    self.templates, DEFAULT_CONFIG)
        ms_train = engine.moments(d(22), week, self.courses,
                                  self.templates,
                                  self.make_config({2: DayType.B_TRAIN_A}))
        sig_default = [(m.start, m.title) for m in ms_default]
        sig_train = [(m.start, m.title) for m in ms_train]
        self.assertNotEqual(sig_default, sig_train,
                            "映射改了，时间轴却一模一样 —— 配置没穿透到引擎")

    def test_wake_time_follows_the_mapped_template(self):
        """改映射后起床时间跟着变 —— 「今天几点起」是这个程序的主问题"""
        cfg_train = self.make_config({2: DayType.B_TRAIN_A})
        # 训练日模板 07:25 起、B 型也是 07:25 —— 换成 A 型（06:55）看得清
        cfg_a = self.make_config({2: DayType.A})
        self.assertEqual(7 * 60 + 25, self.wake_of(d(22), cfg_train))
        self.assertEqual(6 * 60 + 55, self.wake_of(d(22), cfg_a))

    def test_early_layer_overrides_map_on_early_days(self):
        """周一有早八：早八层优先于星期映射"""
        cfg = self.make_config({1: DayType.B_NORMAL}, early=DayType.A)
        self.assertEqual(DayType.A, self.type_of(d(21), cfg),
                         "有早八的日子没走早八层")

    def test_only_a_and_weekend(self):
        """「只留 A 型 + 周末」的自定义配置：工作日全落 B，早八走 A"""
        cfg = self.make_config({1: DayType.B_NORMAL, 2: DayType.B_NORMAL,
                                3: DayType.B_NORMAL, 4: DayType.B_NORMAL,
                                5: DayType.B_NORMAL})
        self.assertEqual(DayType.A, self.type_of(d(21), cfg))
        self.assertEqual(DayType.B_NORMAL, self.type_of(d(22), cfg))
        self.assertEqual(DayType.SATURDAY, self.type_of(d(26), cfg))

    def test_no_early_class_still_wakes_at_0725(self):
        """没早八的工作日按 B 型 07:25 起 —— 核心区分还在"""
        self.assertEqual(7 * 60 + 25,
                         self.wake_of(date(2026, 9, 18), DEFAULT_CONFIG))

    def test_monday_with_early_class_wakes_at_0655(self):
        self.assertEqual(6 * 60 + 55, self.wake_of(d(21), DEFAULT_CONFIG))


# ============================================================
#  二、REST：自动判定层
# ============================================================

class TestRestDay(ConfigTestCase):
    def test_rest_survives_every_map(self):
        """无课工作日永远是休息日 —— 星期映射管不到它"""
        for map_override in ({1: DayType.A}, {1: DayType.B_TRAIN_A},
                             {1: DayType.SATURDAY}):
            m = list(DEFAULT_CONFIG.weekday_map)
            m[0] = map_override[1]
            cfg = DayTypeConfig(DEFAULT_CONFIG.types, tuple(m), None)
            self.assertEqual(DayType.REST,
                             self.type_of(date(2026, 9, 7), cfg))

    def test_rest_day_label(self):
        week = 1
        label = engine.day_type_display(date(2026, 9, 7), week, self.courses,
                                        DEFAULT_CONFIG)
        self.assertEqual("无课 · 休息日", label)


# ============================================================
#  三、显示：标签不能说谎
# ============================================================

class TestDayTypeDisplay(ConfigTestCase):
    def test_early_day_shows_the_fact(self):
        label = engine.day_type_display(d(21), 3, self.courses, DEFAULT_CONFIG)
        self.assertIn("A 型日", label)
        self.assertIn("有早八", label)

    def test_label_does_not_lie_under_custom_map(self):
        """
        「A 型日 · 有早八」是句假话的情形：
        把工作日全映射到 A、又关掉早八层之后，周二（无早八）也会用 A 模板 ——
        但那天没有早八，标签**不能**带「· 有早八」。
        """
        cfg = DayTypeConfig(DEFAULT_CONFIG.types,
                            tuple([DayType.A] * 7), None)
        label = engine.day_type_display(d(22), 3, self.courses, cfg)
        self.assertEqual("A 型日", label)

    def test_custom_name_is_used(self):
        names = {DayType.A: "早八日"}
        label = engine.day_type_display(d(21), 3, self.courses, DEFAULT_CONFIG,
                                        names)
        self.assertIn("早八日", label)

    def test_weekend_labels(self):
        self.assertEqual("周六",
                         engine.day_type_display(d(26), 3, self.courses, DEFAULT_CONFIG))
        self.assertEqual("周日",
                         engine.day_type_display(d(27), 3, self.courses, DEFAULT_CONFIG))


# ============================================================
#  四、dayTypeConfig 段：解析与校验
# ============================================================

class TestParseDayTypeConfig(unittest.TestCase):
    @staticmethod
    def doc(segment: str) -> str:
        return ('{"format":"timetable","version":5,"dayTypeConfig":'
                + segment + ',"courses":[]}')

    def parsed(self, json_text: str):
        return format_spec.parse(json_text, 19)

    def failed(self, json_text: str) -> str:
        with self.assertRaises(format_spec.FormatError) as cm:
            format_spec.parse(json_text, 19)
        return str(cm.exception)

    def test_reads_a_complete_segment(self):
        p = self.parsed(self.doc(
            '{"types":["1","2","5","6","7"],'
            '"weekdayMap":["2","2","2","2","2","5","6"],'
            '"earlyType":"1"}')).day_type_config
        self.assertEqual((DayType.A, DayType.B_NORMAL, DayType.SATURDAY,
                          DayType.SUNDAY, DayType.REST), p.types)
        self.assertEqual(DayType.A, p.early_type)
        self.assertEqual([1, 2, 3, 4, 5], p.weekdays_of(DayType.B_NORMAL))

    def test_rest_is_added_back_automatically(self):
        """types 里漏了休息日不报错 —— 它是自动判定层，删不掉"""
        p = self.parsed(self.doc(
            '{"types":["1","2"],"weekdayMap":["1","1","1","1","1","1","1"]}')).day_type_config
        self.assertIn(DayType.REST, p.types)

    def test_types_unknown_id_is_an_error(self):
        msg = self.failed(self.doc('{"types":["1","9"],"weekdayMap":["1","1","1","1","1","1","1"]}'))
        self.assertIn("9", msg)

    def test_weekday_map_must_have_exactly_seven(self):
        msg = self.failed(self.doc('{"types":["1","2"],"weekdayMap":["1","1","1"]}'))
        self.assertIn("7", msg)

    def test_weekday_map_target_must_be_managed(self):
        """映射目标必须在 types 里 —— 映射到不存在的日型会找不到模板"""
        msg = self.failed(self.doc(
            '{"types":["1","2"],"weekdayMap":["1","1","3","1","1","1","1"]}'))
        self.assertIn("3", msg)

    def test_weekday_map_refuses_rest(self):
        msg = self.failed(self.doc(
            '{"types":["1","2","7"],"weekdayMap":["1","1","7","1","1","1","1"]}'))
        self.assertIn("休息日", msg)

    def test_early_type_validation(self):
        msg = self.failed(self.doc(
            '{"types":["1","2"],"weekdayMap":["1","1","1","1","1","1","1"],'
            '"earlyType":"9"}'))
        self.assertIn("9", msg)

        msg = self.failed(self.doc(
            '{"types":["1","2","7"],"weekdayMap":["1","1","1","1","1","1","1"],'
            '"earlyType":"7"}'))
        self.assertIn("休息日", msg)

    def test_early_type_may_be_omitted(self):
        """省略 / null / 空串 = 不区分早八"""
        for seg in ('{"types":["1","2"],"weekdayMap":["1","1","1","1","1","1","1"]}',
                    '{"types":["1","2"],"weekdayMap":["1","1","1","1","1","1","1"],'
                    '"earlyType":null}',
                    '{"types":["1","2"],"weekdayMap":["1","1","1","1","1","1","1"],'
                    '"earlyType":""}'):
            p = self.parsed(self.doc(seg)).day_type_config
            self.assertIsNone(p.early_type)

    def test_only_config_is_valid_content(self):
        p = self.parsed(self.doc(
            '{"types":["1","2"],"weekdayMap":["1","1","1","1","1","1","1"]}'))
        self.assertIsNone(p.courses)
        self.assertIsNone(p.templates)
        self.assertIsNotNone(p.day_type_config)

    def test_newer_version_is_refused(self):
        json_text = ('{"format":"timetable","version":99,'
                     '"courses":[{"name":"高数","dayOfWeek":1,"nodes":[1,2],"weeks":"1-5"}]}')
        msg = self.failed(json_text)
        self.assertIn("v99", msg)
        self.assertIn("请更新", msg)


# ============================================================
#  五、v3/v4 旧 dayTypes 段 → 迁移
# ============================================================

class TestLegacyDayTypesMigration(unittest.TestCase):
    """
    旧文件（v3/v4 带 dayTypes 段）照常导入：「启用/回落」确定性迁移成
    星期映射 —— 迁移结果与旧程序的判定**逐日一致**（对照表见
    config_from_policy 的 _LEGACY_NATURAL_SLOT）。
    """

    def parsed(self, day_types: str, version: int = 4):
        raw = ('{"format":"timetable","version":%d,"dayTypes":%s,'
               '"courses":[]}' % (version, day_types))
        return format_spec.parse(raw, 19)

    def test_default_policy_migrates_to_default_config(self):
        p = self.parsed('{"enabled":["A","B_NORMAL","SATURDAY","SUNDAY"],'
                        '"fallback":"B_NORMAL"}')
        self.assertEqual(DEFAULT_CONFIG, p.day_type_config)

    def test_identity_policy_migrates_to_identity_config(self):
        p = self.parsed('{"enabled":["A","B_NORMAL","B_TRAIN_A","B_TRAIN_B",'
                        '"SATURDAY","SUNDAY"],"fallback":"B_NORMAL"}')
        self.assertEqual(IDENTITY, p.day_type_config)

    def test_fallback_decides_disabled_slots(self):
        """
        enabled 里没有的槽位落到 fallback —— 这正是旧 resolve() 的答案。
        例：只启用 A + 周末、回落 B 型 → 周二（自然槽位训练日A，未启用）
        落 B 型。
        """
        p = self.parsed('{"enabled":["A","SATURDAY","SUNDAY"],"fallback":"B_NORMAL"}')
        cfg = p.day_type_config
        self.assertEqual(DayType.B_NORMAL, cfg.weekday_map[1], "周二应落到回落项")

    def test_fallback_may_be_outside_enabled(self):
        """文件格式允许 fallback 不在 enabled 里 —— 迁移照常处理"""
        p = self.parsed('{"enabled":["A","SATURDAY"],"fallback":"B_NORMAL"}')
        self.assertEqual(DayType.B_NORMAL, p.day_type_config.weekday_map[0])

    def test_fallback_determines_early_layer_when_a_disabled(self):
        """A 没启用 → 早八层落到 fallback"""
        p = self.parsed('{"enabled":["B_NORMAL","SATURDAY","SUNDAY"],'
                        '"fallback":"B_NORMAL"}')
        self.assertEqual(DayType.B_NORMAL, p.day_type_config.early_type)

    def test_names_are_case_insensitive(self):
        p = self.parsed('{"enabled":["a","b_normal","saturday"],"fallback":"b_normal"}')
        self.assertIn(DayType.A, p.day_types.enabled)
        self.assertIsNotNone(p.day_type_config)

    def test_v3_file_is_still_accepted(self):
        p = self.parsed('{"enabled":["A"],"fallback":"A"}', version=3)
        self.assertIsNotNone(p.day_type_config)

    def test_empty_enabled_is_an_error(self):
        with self.assertRaises(format_spec.FormatError) as cm:
            self.parsed('{"enabled":[],"fallback":"A"}')
        self.assertIn("至少要启用一种", str(cm.exception))

    def test_rest_is_refused_in_the_legacy_segment(self):
        """休息日在旧段里也写不了 —— 这条口径两端一致"""
        with self.assertRaises(format_spec.FormatError) as cm:
            self.parsed('{"enabled":["A","7"],"fallback":"A"}')
        self.assertIn("休息日", str(cm.exception))

    def test_config_wins_over_legacy_segment(self):
        """两个段都写时以 dayTypeConfig 为准（新格式是唯一权威）"""
        raw = ('{"format":"timetable","version":5,'
               '"dayTypes":{"enabled":["A"],"fallback":"A"},'
               '"dayTypeConfig":{"types":["2","5","7"],'
               '"weekdayMap":["2","2","2","2","2","5","5"],'
               '"earlyType":null},'
               '"courses":[]}')
        p = format_spec.parse(raw, 19)
        self.assertEqual((DayType.B_NORMAL, DayType.SATURDAY, DayType.REST),
                         p.day_type_config.types)
        self.assertIsNone(p.day_type_config.early_type)


# ============================================================
#  六、序列化与往返
# ============================================================

class TestSerializeDayTypeConfig(unittest.TestCase):
    def test_roundtrip_through_a_full_file(self):
        config = DayTypeConfig(
            types=(DayType.A, DayType.B_NORMAL, DayType.SATURDAY, DayType.REST),
            weekday_map=(DayType.A, DayType.B_NORMAL, DayType.A,
                         DayType.B_NORMAL, DayType.A, DayType.SATURDAY,
                         DayType.SATURDAY),
            early_type=None,
        )
        text = format_spec.serialize(
            "测试学期", TERM_START, 19, [], builtin_data.templates(),
            day_type_config=config,
        )
        self.assertIn('"version": 5', text)
        back = format_spec.parse(text, 19).day_type_config
        self.assertEqual(config, back)

    def test_segment_position_is_fixed(self):
        """dayTypeConfig 在 dayTypeNames 之后、courses 之前 —— 段序是字节契约"""
        text = format_spec.serialize(
            "学期", TERM_START, 19, [], builtin_data.templates(),
            day_type_names={DayType.A: "早八日"},
            day_type_config=DEFAULT_CONFIG,
        )
        i_dtn = text.index('"dayTypeNames"')
        i_dtc = text.index('"dayTypeConfig"')
        i_courses = text.index('"courses"')
        self.assertLess(i_dtn, i_dtc)
        self.assertLess(i_dtc, i_courses)

    def test_omitting_the_config_writes_no_segment(self):
        text = format_spec.serialize("学期", TERM_START, 19, [])
        self.assertNotIn("dayTypeConfig", text)

    def test_v3_compat_output_keeps_the_old_shape(self):
        """version=3 兼容输出写旧 dayTypes 段、不写 dayTypeConfig —— parity 依赖它"""
        text = format_spec.serialize(
            "学期", TERM_START, 19, [], builtin_data.templates(),
            day_types=DayTypePolicy.DEFAULT,
            day_type_config=DEFAULT_CONFIG,
            version=3,
        )
        self.assertIn('"version": 3', text)
        self.assertIn('"dayTypes"', text)
        self.assertNotIn("dayTypeConfig", text)

    def test_local_storage_json_roundtrip(self):
        raw = format_spec.day_type_config_to_json(DEFAULT_CONFIG)
        self.assertEqual(DEFAULT_CONFIG,
                         format_spec.day_type_config_from_json(raw))

    def test_corrupt_local_storage_returns_default(self):
        for bad in ("", "{", "[]", "null", '{"types":[]}'):
            self.assertEqual(DEFAULT_CONFIG,
                             format_spec.day_type_config_from_json(bad))


# ============================================================
#  七、本地存储（store 层）
# ============================================================

class TestLocalStorage(unittest.TestCase):
    def setUp(self):
        import os
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix="timetable_cfg_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        saved = os.environ.get("TIMETABLE_DATA_DIR")
        os.environ["TIMETABLE_DATA_DIR"] = tmp

        def restore():
            if saved is None:
                os.environ.pop("TIMETABLE_DATA_DIR", None)
            else:
                os.environ["TIMETABLE_DATA_DIR"] = saved

        self.addCleanup(restore)
        from app.store import Store
        self.store = Store()
        self.assertTrue(str(self.store.path).startswith(tmp))

    def test_roundtrip(self):
        config = DayTypeConfig(DEFAULT_CONFIG.types,
                               DEFAULT_CONFIG.weekday_map, None)
        self.store.save_day_type_config(config)
        self.assertEqual(config, self.store.day_type_config())
        self.assertTrue(self.store.has_custom_day_type_config)

    def test_default_when_never_configured(self):
        self.assertEqual(DEFAULT_CONFIG, self.store.day_type_config())
        self.assertFalse(self.store.has_custom_day_type_config)

    def test_legacy_day_types_key_is_lazily_migrated(self):
        """旧 data.json 只有 day_types 键 → 读出来的是迁移后的配置"""
        from app.store import Store
        self.store._data["day_types"] = format_spec.day_types_to_json(
            DayTypePolicy.DEFAULT)
        self.store.save()
        again = Store()
        self.assertEqual(DEFAULT_CONFIG, again.day_type_config())
        # 新键没写过 → 判据还是「有旧配置」（复制 JSON 要带上）
        self.assertFalse(again.has_custom_day_type_config)
        self.assertTrue(again.has_custom_day_types)

    def test_reset_clears_both_keys(self):
        self.store._data["day_types"] = "{}"
        self.store.save_day_type_config(DEFAULT_CONFIG)
        self.store.reset_day_type_config()
        self.assertEqual(DEFAULT_CONFIG, self.store.day_type_config())
        self.assertFalse(self.store.has_custom_day_type_config)
        self.assertFalse(self.store.has_custom_day_types)


# ============================================================
#  八、管理 API：创建 / 删除 / 编辑 / 星期映射 / 早八层
# ============================================================

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


class TestDayTypeManageApi(DayTypeApiTestCase):
    def test_get_shape_with_wake_times(self):
        d = self.api.get_day_types()
        self.assertEqual(7, len(d["types"]))
        self.assertEqual([], d["freeIds"], "默认满 7 种")
        self.assertEqual("1", d["earlyType"])
        for t in d["types"]:
            self.assertIn("key", t)
            self.assertIn("label", t)
            self.assertIn("wake", t, f"{t['key']} 没带起床时间")
            self.assertIn("weekdays", t)
            self.assertIn("locked", t)

    def test_default_weekday_mapping(self):
        d = self.api.get_day_types()
        by_key = {t["key"]: t for t in d["types"]}
        self.assertEqual([1, 2, 3, 4, 5], by_key["2"]["weekdays"])
        self.assertEqual([6], by_key["5"]["weekdays"])
        self.assertEqual([7], by_key["6"]["weekdays"])
        self.assertTrue(by_key["1"]["isEarly"])

    def test_rest_row_is_locked(self):
        d = self.api.get_day_types()
        rest = next(t for t in d["types"] if t["key"] == "7")
        self.assertTrue(rest["locked"])
        self.assertEqual([], rest["weekdays"])
        self.assertFalse(rest["isEarly"])

    def test_create_refuses_when_full(self):
        r = self.api.create_day_type("考试日")
        self.assertFalse(r["ok"])
        self.assertIn("满", r["message"])

    def test_delete_then_create_reuses_the_id(self):
        self.assertTrue(self.api.delete_day_type("3")["ok"])
        r = self.api.create_day_type("考试日")
        self.assertTrue(r["ok"], r.get("message"))
        self.assertEqual("3", r["newId"])
        # 作息起点：复制自第一支现存日型（id 1 = A 型）
        blocks = self.store.templates()[DayType.B_TRAIN_A]
        self.assertEqual(len(blocks), len(self.store.templates()[DayType.A]))

    def test_create_refuses_empty_name(self):
        self.api.delete_day_type("3")
        r = self.api.create_day_type("   ")
        self.assertFalse(r["ok"])
        self.assertIn("不能为空", r["message"])

    def test_delete_guards(self):
        # ① 休息日删不掉
        r = self.api.delete_day_type("7")
        self.assertFalse(r["ok"])
        self.assertIn("休息日", r["message"])

        # ② 管着星期几的删不掉（默认里 "2" 管周一~周五）
        r = self.api.delete_day_type("2")
        self.assertFalse(r["ok"])
        self.assertIn("周一", r["message"])

        # ③ 早八日型删不掉（默认 earlyType = "1"）
        r = self.api.delete_day_type("1")
        self.assertFalse(r["ok"])
        self.assertIn("早八", r["message"])

        # 谁都不管的（"3"）可以删
        self.assertTrue(self.api.delete_day_type("3")["ok"])

    def test_update_weekdays_moves_the_map(self):
        """把周二勾给训练日：原来管周二的自动让出（映射全局唯一）"""
        r = self.api.update_day_type("3", {"weekdays": [2]})
        self.assertTrue(r["ok"], r.get("message"))
        by_key = {t["key"]: t for t in r["dayTypes"]["types"]}
        self.assertEqual([2], by_key["3"]["weekdays"])
        self.assertEqual([1, 3, 4, 5], by_key["2"]["weekdays"])

    def test_update_name_persists(self):
        r = self.api.update_day_type("3", {"name": "力量日"})
        self.assertTrue(r["ok"], r.get("message"))
        by_key = {t["key"]: t for t in r["dayTypes"]["types"]}
        self.assertEqual("力量日", by_key["3"]["label"])
        self.assertFalse(by_key["3"]["isDefaultName"])

    def test_update_validates_weekdays(self):
        r = self.api.update_day_type("3", {"weekdays": [0]})
        self.assertFalse(r["ok"])
        self.assertIn("超出范围", r["message"])
        r = self.api.update_day_type("3", {"weekdays": ["x"]})
        self.assertFalse(r["ok"])

    def test_update_refuses_unknown(self):
        r = self.api.update_day_type("MONDAY", {"weekdays": [1]})
        self.assertFalse(r["ok"])

    def test_save_weekday_map_roundtrip(self):
        r = self.api.save_weekday_map(["3", "3", "2", "4", "2", "5", "6"])
        self.assertTrue(r["ok"], r.get("message"))
        by_key = {t["key"]: t for t in r["dayTypes"]["types"]}
        self.assertEqual([1, 2], by_key["3"]["weekdays"])
        self.assertEqual([4], by_key["4"]["weekdays"])

        # 落盘：重新构造 Store 还能读回来
        from app.store import Store
        cfg = Store().day_type_config()
        self.assertEqual(DayType.B_TRAIN_A, cfg.weekday_map[0])
        self.assertEqual(DayType.B_TRAIN_B, cfg.weekday_map[3])

    def test_save_weekday_map_validation(self):
        r = self.api.save_weekday_map(["3", "3"])
        self.assertFalse(r["ok"])
        self.assertIn("7", r["message"])

        r = self.api.save_weekday_map(["3", "3", "3", "3", "3", "5", "7"])
        self.assertFalse(r["ok"])
        self.assertIn("休息日", r["message"])

        r = self.api.save_weekday_map(["MONDAY", "3", "3", "3", "3", "5", "6"])
        self.assertFalse(r["ok"])
        self.assertIn("MONDAY", r["message"])

    def test_save_early_type(self):
        r = self.api.save_early_type("")
        self.assertTrue(r["ok"], r.get("message"))
        self.assertEqual("", r["dayTypes"]["earlyType"])

        r = self.api.save_early_type("3")
        self.assertTrue(r["ok"], r.get("message"))
        self.assertEqual("3", r["dayTypes"]["earlyType"])

        r = self.api.save_early_type("7")
        self.assertFalse(r["ok"])
        self.assertIn("休息日", r["message"])

        r = self.api.save_early_type("MONDAY")
        self.assertFalse(r["ok"])

    def test_reset_restores_the_default(self):
        self.api.update_day_type("3", {"weekdays": [2], "name": "力量日"})
        self.api.save_early_type("")
        r = self.api.reset_day_types()
        self.assertTrue(r["ok"])
        self.assertEqual(DEFAULT_CONFIG, self.store.day_type_config())
        self.assertFalse(r["dayTypes"]["hasCustomDayTypeConfig"])

    def test_every_response_is_json_serializable(self):
        import json
        responses = [
            self.api.get_day_types(),
            self.api.create_day_type("考试日"),          # 满员拒绝路径
            self.api.delete_day_type("7"),               # 休息日拒绝路径
            self.api.delete_day_type("2"),               # 映射守护拒绝路径
            self.api.update_day_type("3", {"weekdays": [2]}),
            self.api.save_weekday_map(["2", "2", "2", "2", "2", "5", "6"]),
            self.api.save_weekday_map(["2", "2"]),       # 失败路径
            self.api.save_early_type(""),
            self.api.save_early_type("7"),               # 失败路径
            self.api.reset_day_types(),
        ]
        for i, r in enumerate(responses):
            try:
                json.dumps(r)
            except TypeError as e:
                self.fail(f"第 {i + 1} 个返回值不能 JSON 序列化：{e}")


class TestDayTypeNameApi(DayTypeApiTestCase):
    """自定义日型名 —— v5 起改名统一走 update_day_type（空名 = 恢复默认）"""

    def test_update_name_persists_and_labels_follow(self):
        self.assertTrue(self.api.update_day_type("1", {"name": "早八日"})["ok"])
        r = self.api.update_day_type("3", {"name": "力量日"})
        self.assertTrue(r["ok"], r.get("message"))
        labels = {t["key"]: t["label"] for t in r["dayTypes"]["types"]}
        self.assertEqual("早八日", labels["1"])
        self.assertEqual("力量日", labels["3"])
        self.assertEqual("B 型日", labels["2"], "没改过的 id 用默认名")

        from app.store import Store
        again = Store().day_type_names
        self.assertEqual("早八日", again[DayType.A])

    def test_empty_name_restores_the_default(self):
        """清空名字 = 恢复默认名（从稀疏表删掉）"""
        self.api.update_day_type("1", {"name": "早八日"})
        r = self.api.update_day_type("1", {"name": ""})
        self.assertTrue(r["ok"], r.get("message"))
        labels = {t["key"]: t["label"] for t in r["dayTypes"]["types"]}
        self.assertEqual("A 型日", labels["1"])

    def test_too_long_name_is_refused(self):
        r = self.api.update_day_type("1", {"name": "x" * 21})
        self.assertFalse(r["ok"])
        self.assertIn("20", r["message"])

    def test_unknown_id_is_refused(self):
        r = self.api.update_day_type("9", {"name": "第九种"})
        self.assertFalse(r["ok"])
        self.assertIn("9", r["message"])

    def test_reset_day_types_clears_names_too(self):
        self.api.update_day_type("1", {"name": "早八日"})
        self.api.reset_day_types()
        self.assertFalse(self.api.get_day_types()["hasCustomDayTypeNames"])


class TestTemplateBulkApi(DayTypeApiTestCase):
    """整套替换（save_template_blocks）与整套复制（copy_template）——
    新一代作息编辑（时间轴 + 行内编辑）的落盘接口"""

    def test_bulk_save_replaces_and_sorts(self):
        """整列提交：顺序无关、落盘前按 start 排好"""
        r = self.api.save_template_blocks("1", [
            {"start": "12:00", "end": "13:00", "title": "午餐", "kind": "MEAL"},
            {"start": "00:00", "end": "06:55", "title": "睡觉", "kind": "SLEEP"},
            {"start": "06:55", "end": "07:30", "title": "起床、洗漱"},
        ])
        self.assertTrue(r["ok"], r.get("message"))

        blocks = self.store.templates()[DayType.A]
        self.assertEqual([b.start for b in blocks],
                         sorted(b.start for b in blocks))
        self.assertEqual(3, len(blocks))
        self.assertEqual("睡觉", blocks[0].title)

    def test_bulk_save_refuses_overlap(self):
        r = self.api.save_template_blocks("1", [
            {"start": "00:00", "end": "08:00", "title": "睡觉", "kind": "SLEEP"},
            {"start": "07:00", "end": "24:00", "title": "早起"},
        ])
        self.assertFalse(r["ok"])
        self.assertIn("重叠", r["message"])

    def test_bulk_save_refuses_empty_and_bad_payload(self):
        r = self.api.save_template_blocks("1", [])
        self.assertFalse(r["ok"])
        self.assertIn("至少", r["message"])

        r = self.api.save_template_blocks("1", [
            {"start": "08:00", "end": "09:00", "title": ""},
        ])
        self.assertFalse(r["ok"])
        self.assertIn("不能为空", r["message"])

        r = self.api.save_template_blocks("1", [
            {"start": "10:00", "end": "09:00", "title": "倒流的一格"},
        ])
        self.assertFalse(r["ok"])

    def test_bulk_save_allows_rest_day(self):
        """休息日（id 7）是可编辑日型，整列保存对它照常工作"""
        r = self.api.save_template_blocks("7", [
            {"start": "00:00", "end": "24:00", "title": "完全躺平"},
        ])
        self.assertTrue(r["ok"], r.get("message"))
        self.assertEqual("完全躺平", self.store.templates()[DayType.REST][0].title)

    def test_bulk_save_refuses_deleted_day_type(self):
        """已删除的日型不能再编辑（id 还在 1-7，但配置里没有它）"""
        self.api.delete_day_type("3")
        r = self.api.save_template_blocks("3", [
            {"start": "00:00", "end": "24:00", "title": "x"},
        ])
        self.assertFalse(r["ok"])

    def test_bulk_save_unknown_day_type_refused(self):
        r = self.api.save_template_blocks("MONDAY", [])
        self.assertFalse(r["ok"])
        self.assertIn("MONDAY", r["message"])

    def test_copy_template_roundtrip(self):
        r = self.api.copy_template("1", "6")
        self.assertTrue(r["ok"], r.get("message"))
        self.assertIn("→", r["message"])
        dst = self.store.templates()[DayType.SUNDAY]
        self.assertEqual(len(self.store.templates()[DayType.A]), len(dst))
        back = self.api.get_templates()
        label_dst = next(t for t in back["types"] if t["key"] == "6")
        self.assertTrue(label_dst["isCustom"], "复制后目标应标记为已自定义")

    def test_copy_template_refuses_same_and_unknown(self):
        r = self.api.copy_template("5", "5")
        self.assertFalse(r["ok"])
        self.assertIn("同一个", r["message"])

        r = self.api.copy_template("MONDAY", "5")
        self.assertFalse(r["ok"])
        self.assertIn("MONDAY", r["message"])

    def test_every_bulk_response_is_json_serializable(self):
        import json
        responses = [
            self.api.save_template_blocks("1", [
                {"start": "00:00", "end": "24:00", "title": "一天"},
            ]),
            self.api.save_template_blocks("1", []),        # 失败路径
            self.api.copy_template("1", "5"),
            self.api.copy_template("5", "5"),              # 失败路径
        ]
        for i, r in enumerate(responses):
            try:
                json.dumps(r)
            except TypeError as e:
                self.fail(f"第 {i + 1} 个返回值不能 JSON 序列化：{e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
