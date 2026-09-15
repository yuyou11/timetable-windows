"""
本地存储（app/store.py）的直接单元测试。

## 为什么这个文件必须存在

`app/store.py` 是**磁盘 JSON 词汇表的唯一定义者** —— 所有键名
（`ball_edge`、`total_weeks`、`term_start`……）只有在这里被真正写出去。
别的地方（api / main / 前端）都只是读者。

这带来一种很隐蔽的风险：**改个键名，程序不会报任何错**。
旧存档里的数据还在，只是再也没人读它了 —— 用户升级之后发现
「我的学期设置怎么没了」，而代码看起来毫无问题。
这类事故只能靠「把键名字面量钉死在测试里」来预防：改名会让测试当场变红。

## 这个文件测什么、不测什么

测的是**存储契约**：键名、值的形状、往返一致性、边界夹取、
损坏文件的兜底行为。不测业务逻辑（那在 test_core.py），
也不碰任何真实用户数据 —— 每个用例都用自己的一次性临时目录。

## 两条来自真实事故的规矩

1. **存进去 → 重新构造 Store → 读出来必须相等。**
   项目被「写得进、读不出」坑过两次（模板那次的详情见
   format_spec._serialize_templates 的注释）。只断言 setter 写完的
   内存值是不够的 —— 那只能证明「我以为写对了」。
2. **损坏的文件不能让程序起不来。**
   用户连修的入口都没有的话，数据就真没了。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import format_spec, store as store_mod
from app.models import Block, Course, DayType, Kind
from app.store import Store

# 通过 store 模块间接取用，避免再多一条 import（这两个模块本来就被它依赖）
BS = store_mod.builtin_data
EDGES = store_mod.dock.EDGES

#: 用来区分「这个参数没传」和「传了 None」——apply_import 的分支全靠它
_UNSET = object()


def _signature(c: Course):
    """课程的比较签名。故意不含 id：id 是按数组下标重新生成的"""
    return (c.name, c.day_of_week, c.start_node, c.end_node,
            sorted(c.weeks), c.place, c.enabled)


def _blocks(blocks):
    """模板格子的比较签名，用来做「存进去 == 读出来」的逐格对比"""
    return [(b.start, b.end, b.title, b.note, b.kind.value, b.nodes) for b in blocks]


class StoreTestCase(unittest.TestCase):
    """
    公共搭建：**每个用例一个全新的临时目录**。

    绝不使用默认路径 —— `Store()` 不带参数时指向 `%APPDATA%\\Timetable`，
    那是用户真实的数据。测试把它覆盖掉是最不可原谅的事故。

    也不用 `TIMETABLE_DATA_DIR` 环境变量（smoke.py 那种做法）：
    环境变量是进程级的，会泄漏到别的测试里，造成「单独跑是绿的、
    一起跑是红的」这种最难查的相互污染。显式传 path 干净得多。
    """

    def setUp(self):
        d = tempfile.mkdtemp(prefix="timetable_store_test_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.dir = Path(d)
        self.data_path = self.dir / "data.json"
        self.store = Store(path=self.data_path)

    # ---- 小工具 ----

    def reopen(self) -> Store:
        """重新从磁盘构造一个 Store —— 往返测试的核心动作"""
        return Store(path=self.data_path)

    def raw(self) -> dict:
        """直接读原始文件，绕过 Store 的所有封装"""
        return json.loads(self.data_path.read_text(encoding="utf-8"))

    def raw_text(self) -> str:
        return self.data_path.read_text(encoding="utf-8")

    def write_raw(self, text: str) -> None:
        self.data_path.write_text(text, encoding="utf-8")

    def write_raw_obj(self, obj) -> None:
        self.data_path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def parsed(self, *, courses=_UNSET, term=_UNSET, templates=_UNSET):
        """
        造一份「用户导入的文件」并经正式解析器解析。

        走 format_spec.parse 而不是手搓 Parsed 对象，是因为真实的导入
        路径就是它 —— 手搓对象会把解析器的行为绕过去，测不出整合问题。
        """
        doc: dict = {"format": "timetable", "version": 2}
        if courses is not _UNSET:
            doc["courses"] = courses
        if term is not _UNSET:
            doc["term"] = term
        if templates is not _UNSET:
            doc["templates"] = templates
        return format_spec.parse(json.dumps(doc, ensure_ascii=False))


# ============================================================
#  一、文件本身的行为
# ============================================================

class TestStoreBasics(StoreTestCase):
    """
    存储最底层的几条约定。

    守的不是某个字段，而是「文件什么时候出现、写完之后现场干不干净」。
    这类问题平时完全看不出来，一旦出问题就是「用户数据丢了」或者
    「程序目录里莫名其妙多出一堆文件」。
    """

    def test_explicit_path_is_honoured(self):
        """
        传进来的 path 必须被真的用上。

        这条是**整个文件的安全带**：它一旦失效，其余用例就会去读写
        用户真实的 %APPDATA% 存档。所以放在最前面，写得最直白。
        """
        self.assertEqual(self.data_path, self.store.path)

    def test_loading_missing_file_does_not_create_it(self):
        """
        只是打开程序，不该凭空造出一个文件。

        读一个不存在的文件就把空文件写下去，会让「有没有数据」这个判断
        失真，也会在用户目录里留下一堆没人要的空 JSON。
        """
        self.assertEqual({}, self.store._data)
        self.assertFalse(self.data_path.exists(), "load() 不该写文件")

    def test_save_creates_missing_parent_directories(self):
        """
        数据目录第一次使用时并不存在，save() 必须自己把它建出来。

        漏掉 mkdir 的症状是：全新安装后**任何一次设置修改都静默失败**
        （异常被上层吞掉），用户以为改了其实没改。
        """
        deep = self.dir / "sub" / "deep" / "data.json"
        s = Store(path=deep)
        s.enabled = True
        self.assertTrue(deep.exists())
        self.assertTrue(json.loads(deep.read_text(encoding="utf-8"))["enabled"])

    def test_save_leaves_no_leftover_tmp_file(self):
        """
        先写 .tmp 再 replace 是防断电的正确手法（见 save() 注释），
        但临时文件必须被 replace 掉，不能留在用户目录里。
        """
        self.store.enabled = True
        self.assertFalse((self.dir / "data.tmp").exists(), "临时文件没被清掉")
        self.assertEqual(["data.json"], sorted(p.name for p in self.dir.iterdir()))

    def test_saved_file_is_readable_chinese(self):
        """
        落盘必须是「用户能直接打开看」的。

        导出的文件是给人看、给人改、给 AI 读的，所以 json.dumps 要带
        ensure_ascii=False —— 否则中文全变成 \\uXXXX，所谓「能打开看」
        就成了一句空话。这条直接查文件字节里有没有中文。
        """
        self.store.term_name = "大一上 · 示例专业"
        self.assertIn("大一上", self.raw_text())
        self.assertEqual("大一上 · 示例专业", self.raw()["term_name"])

    def test_serializable_flag_stays_false(self):
        """
        `_serializable = False` 看着莫名其妙，但删了程序会硬崩。

        pywebview 在建窗口时会递归遍历 js_api 上所有公开属性；
        钻进持有原生对象的属性时可能无限递归、撑爆 C 栈，
        连异常都记不下来（详见 store.py 里那段注释）。
        所以这条不是「测个常量」，而是给一个崩溃开关上锁。
        """
        self.assertIs(False, Store._serializable)


# ============================================================
#  二、冻结的 JSON 键名
# ============================================================

class TestFrozenJsonKeys(StoreTestCase):
    """
    **本文件最重要的一组**：把磁盘上的键名字面量钉死。

    每个用例都 set 一个属性，然后直接读原始文件，用**整个字典相等**
    来断言 —— 不只是「这个键在」，还包括「没有多出别的键」。
    这样将来任何人重命名一个键（或者顺手加了个新键），
    测试会立刻变红，而不是等到用户升级后数据静默对不上。

    注意 `last_reminder_key` ↔ JSON 键 `last_reminder` 是**故意的错配**，
    单独有一条用例把它钉住 —— 它看起来像手滑，其实是历史遗留的
    磁盘兼容约定，改掉会让老用户的去重状态失效。
    """

    def test_enabled_key(self):
        self.store.enabled = True
        self.assertEqual({"enabled": True}, self.raw())

    def test_ball_enabled_key_is_a_json_bool(self):
        self.store.ball_enabled = False
        raw = self.raw()
        self.assertEqual({"ball_enabled": False}, raw)
        self.assertIsInstance(raw["ball_enabled"], bool, "不能写成 0/1")

    def test_ball_edge_key(self):
        self.store.ball_edge = "left"
        self.assertEqual({"ball_edge": "left"}, self.raw())

    def test_ball_center_key(self):
        self.store.ball_center = 640
        self.assertEqual({"ball_center": 640}, self.raw())

    def test_ball_pos_key_is_a_list_of_two_ints(self):
        """位置上存成 JSON 数组而不是字符串，读回来是元组"""
        self.store.ball_pos = (120, 260)
        self.assertEqual({"ball_pos": [120, 260]}, self.raw())

    def test_term_name_key(self):
        self.store.term_name = "示例大学 2026 级 · 大一上"
        self.assertEqual({"term_name": "示例大学 2026 级 · 大一上"}, self.raw())

    def test_term_start_key_is_iso_date_string(self):
        self.store.term_start = date(2026, 9, 14)
        self.assertEqual({"term_start": "2026-09-14"}, self.raw())

    def test_total_weeks_key(self):
        self.store.total_weeks = 18
        self.assertEqual({"total_weeks": 18}, self.raw())

    def test_week_override_key(self):
        self.store.week_override = 7
        self.assertEqual({"week_override": 7}, self.raw())

    def test_remind_lead_key(self):
        self.store.remind_lead = 15
        self.assertEqual({"remind_lead": 15}, self.raw())

    def test_last_reminder_key_writes_underscored_free_name(self):
        """
        故意的错配：属性叫 last_reminder_key，磁盘键叫 last_reminder。

        访问器名字是后来统一加的 `_key` 后缀，磁盘键为了兼容老存档
        没跟着改。**两者不一致是刻意的**，所以这里正着反着都断言一遍：
        属性写出去必须落在 `last_reminder`，而 `last_reminder_key`
        这个键永远不该出现在文件里。
        """
        self.store.last_reminder_key = "2026-09-14|08:30|高等数学A(Ⅰ)"
        raw = self.raw()
        self.assertEqual({"last_reminder": "2026-09-14|08:30|高等数学A(Ⅰ)"}, raw)
        self.assertNotIn("last_reminder_key", raw, "改了键名会让老用户的去重状态失效")

    def test_first_launch_done_key(self):
        self.store.mark_launched()
        self.assertEqual({"first_launch_done": True}, self.raw())

    def test_course_dict_exact_shape_with_place(self):
        """
        课程的字段名和可选字段的省略规则，一次全钉住。

        `nodes` 是两元素数组、`weeks` 是字符串、`dayOfWeek` 是驼峰 ——
        这些只要有一个字母变了，手机版的导出文件就对不上了。

        另外：`place` 为空 / `enabled` 为 True 时**不写出这两个键**
        （省得文件里全是噪音），所以这里用的是整字典相等，
        顺带把「该省的要省掉」也一起钉住了。
        """
        self.store.save_courses([
            Course("高等数学", 1, 3, 4, frozenset({2, 3, 4}), "教一-203"),
        ])
        self.assertEqual(
            {"courses": [{
                "name": "高等数学",
                "dayOfWeek": 1,
                "nodes": [3, 4],
                "weeks": "2-4",
                "place": "教一-203",
            }]},
            self.raw(),
        )

    def test_course_dict_omits_place_and_keeps_false_enabled(self):
        """没教室就不写 place；停用的课必须写出 enabled: false"""
        self.store.save_courses([
            Course("自习", 5, 7, 8, frozenset({5}), "", False),
        ])
        self.assertEqual(
            {"courses": [{
                "name": "自习",
                "dayOfWeek": 5,
                "nodes": [7, 8],
                "weeks": "5",
                "enabled": False,
            }]},
            self.raw(),
        )

    def test_templates_are_stored_as_a_json_string(self):
        """
        模板在文件里是**一个字符串**，不是嵌套对象 —— 这个表示法很反常，
        所以必须显式钉住，否则下一个人重构时几乎一定会「顺手改正」。

        为什么是字符串？因为磁盘上要存的是「用户改过的那部分」，
        直接放对象会和导出格式里的 templates 段混淆（还出过一次
        「存进去不是合法 JSON、读回来被静默吞掉」的事故，
        详见 format_spec._serialize_templates）。
        """
        custom = {DayType.A: [
            Block(0, 7 * 60 + 30, "睡觉", kind=Kind.SLEEP),
            Block(7 * 60 + 30, 1440, "自定义的一天", kind=Kind.STUDY),
        ]}
        self.store.save_templates(custom)

        raw = self.raw()
        self.assertIsInstance(raw["templates"], str, "模板必须是字符串，不是对象")
        inner = json.loads(raw["templates"])
        self.assertEqual(["A"], list(inner))
        self.assertEqual("00:00", inner["A"][0]["start"])
        self.assertEqual("07:30", inner["A"][0]["end"])
        self.assertEqual("24:00", inner["A"][1]["end"])
        self.assertEqual("SLEEP", inner["A"][0]["kind"])


# ============================================================
#  三、往返：set -> 新 Store -> 读出来
# ============================================================

class TestRoundTrip(StoreTestCase):
    """
    「写得进、读不出」是这个项目的惯犯。

    setter 写完通常都会顺手断言一下内存里的值，但那只能证明
    「我以为写对了」；真正的契约是**重启程序之后还在**。
    所以每个属性都走一遍：赋值 → 重新构造 Store → 断言相等。

    只用磁盘上真实存在的数据，不碰任何内存缓存。
    """

    def test_all_scalar_settings_survive_reload(self):
        s = self.store
        s.enabled = True
        s.ball_enabled = False
        s.ball_edge = "right"
        s.ball_center = 540
        s.ball_pos = (120, 260)
        s.term_name = "2026 秋"
        s.term_start = date(2026, 9, 14)
        s.total_weeks = 20
        s.week_override = 7
        s.remind_lead = 15
        s.last_reminder_key = "2026-09-14|08:30|高等数学A(Ⅰ)"
        s.mark_launched()

        back = self.reopen()
        self.assertIs(True, back.enabled)
        self.assertIs(False, back.ball_enabled)
        self.assertEqual("right", back.ball_edge)
        self.assertEqual(540, back.ball_center)
        self.assertEqual((120, 260), back.ball_pos)
        self.assertEqual("2026 秋", back.term_name)
        self.assertEqual(date(2026, 9, 14), back.term_start)
        self.assertEqual(20, back.total_weeks)
        self.assertEqual(7, back.week_override)
        self.assertEqual(15, back.remind_lead)
        self.assertEqual("2026-09-14|08:30|高等数学A(Ⅰ)", back.last_reminder_key)
        self.assertFalse(back.is_first_launch)

    def test_courses_survive_reload(self):
        items = [
            Course("高等数学", 1, 3, 4, frozenset({2, 3, 4}), "教一-203"),
            Course("程序设计基础B", 4, 7, 8, frozenset({2, 4, 6}), "", False),
        ]
        self.store.save_courses(items)

        got = self.reopen().courses()
        self.assertEqual(2, len(got))
        self.assertEqual([_signature(c) for c in items], [_signature(c) for c in got])

    def test_course_ids_are_regenerated_from_position(self):
        """
        id 不存盘，是按数组下标现算的（c001 / c002 …）。

        这不是缺陷，但要钉住：前端拿 id 来定位课程，如果将来改成
        存 id，下标和 id 万一不一致就会「删错课」。
        """
        self.store.save_courses([
            Course("甲", 1, 1, 2, frozenset({1})),
            Course("乙", 2, 3, 4, frozenset({1})),
        ])
        got = self.reopen().courses()
        self.assertEqual(["c001", "c002"], [c.id for c in got])

    def test_empty_week_set_survives_reload(self):
        """weeks 为空集合时会写成空字符串，读回来仍是空集合（不能变成全天）"""
        self.store.save_courses([Course("没周次的课", 1, 1, 2, frozenset())])
        self.assertEqual(frozenset(), self.reopen().courses()[0].weeks)

    def test_templates_survive_reload(self):
        custom = {DayType.A: [
            Block(0, 7 * 60 + 30, "睡觉", kind=Kind.SLEEP),
            Block(7 * 60 + 30, 1440, "自定义的一天", kind=Kind.STUDY),
        ]}
        self.store.save_templates(custom)

        back = self.reopen()
        got = back.custom_templates()
        self.assertEqual([DayType.A], list(got))
        self.assertEqual(_blocks(custom[DayType.A]), _blocks(got[DayType.A]))

    def test_merged_templates_survive_reload(self):
        """
        合并后的模板也要能读回来：改过的用自定义，没改过的回落到内置。

        注意 merged 里没被覆盖的那几种**不写进文件** —— 这是特意的，
        将来内置模板更新时它们会自动跟着更新，而不是被旧数据钉死。
        """
        custom = {DayType.SATURDAY: [
            Block(0, 9 * 60, "睡觉", kind=Kind.SLEEP),
            Block(9 * 60, 1440, "周六", kind=Kind.FREE),
        ]}
        self.store.save_templates(custom)

        merged = self.reopen().templates()
        self.assertEqual(6, len(merged), "六种日型都要在（没覆盖的来自内置）")
        self.assertEqual(9 * 60, merged[DayType.SATURDAY][0].end)
        self.assertEqual(_blocks(BS.templates()[DayType.A]), _blocks(merged[DayType.A]))

    def test_week_of_uses_override_then_term_start(self):
        """
        week_of 的两条路径都要能用：手动指定优先，否则按学期起始日算。

        它把存储里的两个字段（week_override / term_start）接进了引擎，
        接错线的症状是「整周的课全错一天」，而且界面上看着很正常。
        """
        # 默认 = 自动，按内置学期起始日（2026-09-07）算
        self.assertEqual(1, self.store.week_of(date(2026, 9, 7)))

        # 手动指定优先：不管日期是哪天，都返回指定的那一周
        self.store._data["week_override"] = 7
        self.assertEqual(7, self.store.week_of(date(2026, 9, 7)))

        # 0 = 回到自动；换了学期起始日之后周次必须跟着挪（9/21 变成第 2 周）
        self.store._data["week_override"] = 0
        self.store.term_start = date(2026, 9, 14)
        self.assertEqual(1, self.store.week_of(date(2026, 9, 14)))
        self.assertEqual(2, self.store.week_of(date(2026, 9, 21)))


# ============================================================
#  四、边界夹取与规范化
# ============================================================

class TestClampingAndNormalisation(StoreTestCase):
    """
    越界值怎么处理：**写进去的时候夹取，读出来的时候放行**。

    这组测试一半是在描述设计，一半是在记录脆弱点：

      · total_weeks / remind_lead 的 setter 会夹到合法区间，
        但 getter 不夹 —— 因为「文件被人手改过」不是程序能控制的，
        读取路径不做二次修正，避免把用户的原始数据悄悄改掉。
      · ball_edge 是唯一的例外：**四边白名单在 getter 和 setter 里
        各有一份**。重复是有意的（换只手改文件也不能让悬浮窗停到
        「对角线」上去），代价是两边都得测，少测一边将来就可能漂移。
    """

    # ---- total_weeks ----

    def test_total_weeks_setter_clamps_low(self):
        for bad in (0, -5, -999):
            self.store.total_weeks = bad
            self.assertEqual(1, self.store.total_weeks, f"{bad} 应该被夹到 1")

    def test_total_weeks_setter_clamps_high(self):
        for bad in (31, 999):
            self.store.total_weeks = bad
            self.assertEqual(
                format_spec.MAX_WEEK_LIMIT, self.store.total_weeks,
                f"{bad} 应该被夹到 {format_spec.MAX_WEEK_LIMIT}",
            )

    def test_total_weeks_setter_keeps_in_range_value(self):
        for good in (1, 19, 30):
            self.store.total_weeks = good
            self.assertEqual(good, self.store.total_weeks)

    def test_total_weeks_setter_accepts_numeric_string_and_float(self):
        """表单传过来的常常是字符串/浮点数，能转就该转，不该报错"""
        self.store.total_weeks = "18"
        self.assertEqual(18, self.store.total_weeks)
        self.store.total_weeks = 19.9
        self.assertEqual(19, self.store.total_weeks)

    def test_total_weeks_setter_rejects_garbage(self):
        with self.assertRaises(ValueError):
            self.store.total_weeks = "十九周"

    def test_total_weeks_getter_does_not_clamp(self):
        """
        读取路径不夹取 —— 文件里写多少就返回多少。

        记录这个不对称：夹取只发生在**用户通过界面设置**的时候，
        读了别人给的文件之后我们不去偷偷修正它。
        """
        self.write_raw_obj({"total_weeks": 999})
        self.assertEqual(999, self.reopen().total_weeks)

    def test_total_weeks_getter_raises_on_garbage(self):
        """
        脆弱点（不是好行为，是现状）：文件里是合法 JSON 但字段类型不对时，
        getter 会直接抛 ValueError。

        load() 只挡 JSONDecodeError / OSError —— 「能解析但内容不对」
        这一类兜不住。手工改坏存档的用户会看到程序起不来。
        把现状钉住，是为了让将来决定「要不要兜」的人有意识地改这里。
        """
        self.write_raw_obj({"total_weeks": "十九"})
        with self.assertRaises(ValueError):
            _ = self.reopen().total_weeks

    # ---- remind_lead ----

    def test_remind_lead_setter_clamps_negative_to_zero(self):
        for bad in (-1, -60, -999):
            self.store.remind_lead = bad
            self.assertEqual(0, self.store.remind_lead, f"{bad} 应该被夹到 0")

    def test_remind_lead_setter_clamps_over_an_hour(self):
        """提前量超过 60 分钟没有意义，夹到 60"""
        for bad in (61, 999):
            self.store.remind_lead = bad
            self.assertEqual(60, self.store.remind_lead)

    def test_remind_lead_setter_keeps_valid_values(self):
        for good in (0, 5, 15, 60):
            self.store.remind_lead = good
            self.assertEqual(good, self.store.remind_lead)

    def test_remind_lead_setter_rejects_garbage(self):
        with self.assertRaises(ValueError):
            self.store.remind_lead = "一会儿"

    def test_remind_lead_getter_does_not_clamp(self):
        """同上：读取路径放行，界面路径才夹取"""
        self.write_raw_obj({"remind_lead": 600})
        self.assertEqual(600, self.reopen().remind_lead)

    # ---- ball_edge：getter 与 setter 各有一份白名单 ----

    def test_ball_edge_setter_accepts_all_four_edges(self):
        self.assertEqual(("left", "right", "top", "bottom"), EDGES)
        for edge in EDGES:
            self.store.ball_edge = edge
            self.assertEqual(edge, self.store.ball_edge)
            self.assertEqual({"ball_edge": edge}, self.raw())

    def test_ball_edge_setter_normalises_garbage_to_free(self):
        """
        非白名单的值一律存成空串（= 自由浮动），不报错。

        这里特意用 `"diagonal"` —— 一个「看起来像边但其实不是」的值，
        最容易骗过只检查「非空」的写法。
        """
        for bad in ("diagonal", "LEFT", " north ", "左右", ""):
            self.store.ball_edge = bad
            self.assertEqual("", self.store.ball_edge, f"{bad!r} 不该被存下来")
            self.assertEqual({"ball_edge": ""}, self.raw())

    def test_ball_edge_getter_normalises_garbage_to_free(self):
        """
        getter 的白名单是独立的一份 —— 绕过 setter 直接往 _data 里塞脏值，
        模拟「存档被手工改过 / 旧版本写进了新值」。
        """
        for edge in EDGES:
            self.store._data["ball_edge"] = edge
            self.assertEqual(edge, self.store.ball_edge)
        for bad in ("diagonal", "LEFT", None, 123):
            self.store._data["ball_edge"] = bad
            self.assertEqual("", self.store.ball_edge, f"{bad!r} 应该被规范成空串")

    # ---- ball_pos / ball_center 的形状检查 ----

    def test_ball_pos_defaults_to_minus_one(self):
        """(-1, -1) = 还没记录过位置，界面上据此决定用默认位置"""
        self.assertEqual((-1, -1), self.store.ball_pos)

    def test_ball_pos_getter_rejects_wrong_shapes(self):
        """
        位置只认「恰好两个能转成整数的元素」。

        数组长度不对、不是数组、元素不是数字，一律退回 (-1, -1)
        （= 还没记录过），而不是抛异常 —— 悬浮窗的位置坏掉不该
        让整个程序起不来，大不了回到默认位置。
        """
        for bad in ([1], [1, 2, 3], "100,200", {"x": 1}, None, ["a", "b"]):
            self.store._data["ball_pos"] = bad
            self.assertEqual((-1, -1), self.store.ball_pos, f"{bad!r} 应该退回 (-1, -1)")

    def test_ball_center_getter_raises_on_garbage(self):
        """脆弱点，同 total_weeks：类型不对就抛，读取路径没有兜底"""
        self.store._data["ball_center"] = "中间"
        with self.assertRaises(ValueError):
            _ = self.store.ball_center


# ============================================================
#  五、首次启动判定
# ============================================================

class TestIsFirstLaunch(StoreTestCase):
    """
    `is_first_launch` 是**两个条件的与**，缺一不可：

        first_launch_done 没写过   ← 没走过向导
        courses 键不存在            ← 这份数据是全新的

    第二个条件是给**老用户升级**用的：他们的存档里没有 first_launch_done，
    但课表已经在了。只看第一个条件的话，每次升级都会弹一次「第一次使用」
    向导 —— 用户会觉得「软件又把我东西弄丢了」。

    反过来，两个条件都满足才算新用户，所以这里把真值表铺满。
    """

    def test_brand_new_store_is_first_launch(self):
        self.assertTrue(self.store.is_first_launch)

    def test_empty_json_object_is_first_launch(self):
        self.write_raw("{}")
        self.assertTrue(self.reopen().is_first_launch)

    def test_explicit_false_flag_is_first_launch(self):
        self.write_raw_obj({"first_launch_done": False})
        self.assertTrue(self.reopen().is_first_launch)

    def test_false_after_mark_launched(self):
        self.store.mark_launched()
        self.assertFalse(self.store.is_first_launch)
        self.assertFalse(self.reopen().is_first_launch)

    def test_false_after_courses_saved(self):
        self.store.save_courses([Course("甲", 1, 1, 2, frozenset({1}))])
        self.assertFalse(self.store.is_first_launch)

    def test_false_for_upgrading_old_user(self):
        """
        老用户的存档：有 courses，没有 first_launch_done。

        这条是整个判定存在的理由 —— 它必须返回 False，
        否则每次版本升级都会给老用户弹一次向导。
        """
        self.write_raw_obj({"courses": [
            {"name": "旧课", "dayOfWeek": 1, "nodes": [1, 2], "weeks": "1"},
        ]})
        self.assertFalse(self.reopen().is_first_launch)

    def test_false_when_courses_key_is_empty_array(self):
        """
        课表键存在但为空（用户点过「从空白开始」）也算「不是新用户」。

        「没有数据」和「数据是空的」是两种状态 —— 这条与
        clear_courses / reset_courses 的区别是同一条原则。
        """
        self.write_raw_obj({"courses": []})
        self.assertFalse(self.reopen().is_first_launch)

    def test_clear_courses_also_clears_first_launch(self):
        self.store.clear_courses()
        self.assertFalse(self.store.is_first_launch)

    def test_mark_launched_survives_reset_courses(self):
        """走过向导这件事记在 first_launch_done 上，恢复内置课表不该把它抹掉"""
        self.store.mark_launched()
        self.store.reset_courses()
        self.assertFalse(self.store.is_first_launch)

    def test_calling_courses_alone_consumes_the_new_user_signal(self):
        """
        一个容易忽略的副作用：**只要调过一次 courses()，新用户标记就没了。**

        因为 courses() 在键不存在时会惰性写入内置课表（见它的注释），
        而「courses 键存在」正是判定的第二个条件。
        这是有意的 —— 课表都已经生成了，向导再弹出来才是错的。
        """
        self.assertTrue(self.store.is_first_launch)
        self.assertEqual(len(BS.courses()), len(self.store.courses()))
        self.assertFalse(self.store.is_first_launch)


# ============================================================
#  六、清空 vs 恢复内置
# ============================================================

class TestClearVersusResetCourses(StoreTestCase):
    """
    `clear_courses()` 和 `reset_courses()` 在界面上是相邻的两个按钮，
    但**磁盘上的表示完全不同**，而这个差别是功能正确性的前提：

        clear  → 写入 "courses": []   键在，值是空数组
        reset  → 把键整个删掉         键不在

    为什么不能都用「删键」表示清空？因为 courses() 读到 None 时会
    **重新填入内置课表** —— 用户点「清空」，19 门课又全回来了。

    **「没有数据」和「数据是空的」必须用两种表示。**
    """

    def test_clear_writes_empty_array_keeping_the_key(self):
        self.store.clear_courses()
        raw = self.raw()
        self.assertIn("courses", raw, "清空必须保留键，否则会被当成『没数据』")
        self.assertEqual([], raw["courses"])

    def test_clear_then_courses_stays_empty(self):
        """
        清空之后再读，必须还是空的。

        这是这条设计的价值所在：如果课程是「删键」表示的，
        这里会悄悄回填 19 门内置课 —— 用户会觉得「清空按钮坏了」。
        """
        self.store.clear_courses()
        self.assertEqual([], self.store.courses())

    def test_empty_courses_survives_reload(self):
        self.store.clear_courses()
        self.assertEqual([], self.reopen().courses())

    def test_reset_removes_the_key_entirely(self):
        self.store.save_courses([Course("甲", 1, 1, 2, frozenset({1}))])
        self.store.reset_courses()
        self.assertNotIn("courses", self.raw(), "恢复内置靠的就是『键不存在』")

    def test_reset_then_courses_refills_builtin(self):
        self.store.clear_courses()
        self.assertEqual([], self.store.courses())

        self.store.reset_courses()
        got = self.store.courses()
        self.assertEqual(len(BS.courses()), len(got))
        self.assertEqual(
            [_signature(c) for c in BS.courses()],
            [_signature(c) for c in got],
        )
        self.assertIn("courses", self.raw(), "读回来之后内置课表要落盘")

    def test_clear_after_reset_ends_empty(self):
        """两个操作互相不串味：先恢复再清空，结果必须是空"""
        self.store.reset_courses()
        self.store.clear_courses()
        self.assertEqual([], self.store.courses())


# ============================================================
#  七、模板的「部分覆盖」语义
# ============================================================

class TestTemplatesPersistence(StoreTestCase):
    """
    模板存的是**用户改过的那部分**，不是展开后的完整六套。

    这个决定带来两个必须测的行为：
      · templates()   = 内置 打底 + 用户覆盖（界面看到的）
      · custom_templates() = 只有用户改过的（导出判断用的）

    好处是省空间、且内置模板将来更新时没被覆盖的日型会自动跟上。
    坏处是「存在文件里的」和「读出来看到的」不是同一个东西 ——
    这正是最容易写出 bug 的地方。
    """

    CUSTOM_A = {DayType.A: [
        Block(0, 7 * 60, "睡觉", kind=Kind.SLEEP),
        Block(7 * 60, 1440, "自定义的一天", kind=Kind.STUDY),
    ]}

    def test_stored_value_is_reparseable_json(self):
        """
        存进去的字符串必须能被 json.loads 独立解析。

        这一条直接来自那次事故：曾经存的是 `"templates": { ... }`
        （带键名的片段，不是合法 JSON），json.loads 失败后被
        「坏了就当没有」吞掉 → 用户改的模板静默消失。
        """
        self.store.save_templates(self.CUSTOM_A)
        json.loads(self.raw()["templates"])

    def test_custom_templates_returns_only_overridden(self):
        self.store.save_templates(self.CUSTOM_A)
        custom = self.reopen().custom_templates()
        self.assertEqual([DayType.A], list(custom))
        self.assertTrue(self.store.has_custom_templates)

    def test_merged_templates_keep_builtin_for_untouched(self):
        self.store.save_templates(self.CUSTOM_A)
        merged = self.reopen().templates()

        self.assertEqual(6, len(merged), "六种日型都必须可用")
        self.assertEqual(7 * 60, merged[DayType.A][0].end, "A 型该用自定义的")
        self.assertEqual(
            _blocks(BS.templates()[DayType.B_NORMAL]),
            _blocks(merged[DayType.B_NORMAL]),
            "没被覆盖的日型必须回落到内置",
        )

    def test_no_custom_templates_by_default(self):
        self.assertEqual({}, self.store.custom_templates())
        self.assertFalse(self.store.has_custom_templates)
        self.assertEqual(6, len(self.store.templates()))

    def test_reset_templates_removes_the_key(self):
        self.store.save_templates(self.CUSTOM_A)
        self.store.reset_templates()

        self.assertNotIn("templates", self.raw())
        self.assertFalse(self.store.has_custom_templates)
        self.assertEqual({}, self.store.custom_templates())
        self.assertEqual(
            _blocks(BS.templates()[DayType.B_NORMAL]),
            _blocks(self.reopen().templates()[DayType.B_NORMAL]),
            "删掉自定义之后必须完全回到内置",
        )

    def test_saving_empty_mapping_writes_empty_string_not_missing_key(self):
        """
        save_templates({}) 的结果是「键在、值为空字符串」，和 reset 不一样。

        这不是漂亮的设计，但必须钉住：两条路径读出来都是「没有自定义模板」，
        但磁盘表示不同。将来谁要统一它们，这条测试会提醒他先想清楚。
        """
        self.store.save_templates({})
        raw = self.raw()
        self.assertIn("templates", raw)
        self.assertEqual("", raw["templates"])
        self.assertEqual({}, self.reopen().custom_templates())
        self.assertFalse(self.reopen().has_custom_templates)


# ============================================================
#  八、文件损坏
# ============================================================

class TestCorruptDataFile(StoreTestCase):
    """
    存档坏了，程序必须还能起来。

    这条优先级很高：**「读不出数据」比「读不到数据」好得多**。
    如果损坏直接让程序起不来，用户连进去修/导出的入口都没有，
    数据就真没了。

    所以 load() 的策略是「坏文件改名备份 + 从默认值开始」，
    用户至少还能手工抢救原来的内容。这一组就是在验证那个备份真的发生。
    """

    def test_broken_json_is_backed_up_and_store_starts_empty(self):
        broken = '{"enabled": tru'          # 截断的 JSON
        self.write_raw(broken)

        s = self.reopen()

        self.assertEqual({}, s._data, "坏了就该从空数据开始，而不是崩掉")
        backup = self.dir / "data.broken.json"
        self.assertTrue(backup.exists(), "坏文件必须留一份备份，别直接删")
        self.assertEqual(broken, backup.read_text(encoding="utf-8"))
        self.assertFalse(self.data_path.exists(), "坏文件被 replace 走了")

    def test_backup_name_is_dot_broken_dot_json(self):
        """
        备份的命名固定为 data.broken.json（Path.with_suffix 的结果）。

        钉住它是因为这个文件名会出现在用户眼前 —— 支持/文档里
        会告诉用户「去 %APPDATA%\\Timetable 找 data.broken.json」。
        """
        self.write_raw("{ 不是 json")
        self.reopen()
        self.assertEqual("data.broken.json", (self.dir / "data.broken.json").name)

    def test_store_is_usable_after_corruption(self):
        """坏文件之后写的必须是好文件 —— 不然下次启动又坏一次"""
        self.write_raw("{ 坏的")
        s = self.reopen()
        s.enabled = True
        self.assertTrue(self.reopen().enabled)
        self.assertEqual({"enabled": True}, self.raw())

    def test_non_dict_json_is_discarded_without_backup(self):
        """
        脆弱点/现状：合法 JSON 但形状不对（比如是个数组）时，
        代码是**静默丢弃、不备份**的（见 load() 里的 isinstance 分支）。

        跟真正损坏的 JSON 待遇不同。把现状写下来，
        将来要统一成「也备份」时，会看到这条测试变红。
        """
        self.write_raw("[]")
        s = self.reopen()
        self.assertEqual({}, s._data)
        self.assertFalse((self.dir / "data.broken.json").exists())
        self.assertEqual("[]", self.raw_text(), "文件本身没被动过")

    def test_non_dict_entry_becomes_placeholder_course(self):
        """
        课程数组里混进一个非对象（用户手改出来的），不能让整份课表读不出来。

        策略是「坏的当空课程，好的照读」—— 部分可用胜过全部不可用。
        """
        self.write_raw_obj({"courses": [
            "我不是对象",
            {"name": "好课", "dayOfWeek": 3, "nodes": [3, 4], "weeks": "1-5"},
        ]})
        got = self.reopen().courses()

        self.assertEqual(2, len(got))
        self.assertEqual("损坏的课程", got[0].name)
        self.assertEqual("c001", got[0].id)
        self.assertEqual(frozenset(), got[0].weeks)
        self.assertEqual("好课", got[1].name)
        self.assertEqual({1, 2, 3, 4, 5}, set(got[1].weeks))

    def test_unparseable_weeks_falls_back_to_empty(self):
        """周次写法坏掉（"5-2"）时当空集合，而不是抛出去炸掉启动"""
        self.write_raw_obj({"courses": [
            {"name": "坏周次", "dayOfWeek": 3, "nodes": [3, 4], "weeks": "5-2"},
        ]})
        got = self.reopen().courses()
        self.assertEqual(frozenset(), got[0].weeks)
        self.assertEqual("坏周次", got[0].name)

    def test_missing_weeks_defaults_to_every_week_up_to_the_limit(self):
        """
        现状：课缺少 weeks 字段时按 "*" 处理，展开成 1..MAX_WEEK_LIMIT（30）。

        注意用的是**格式上限 30**，不是当前学期的周数 —— 存储层在读课表时
        并不知道学期有多少周（那是 term 段的事）。所以一门缺字段的课
        会「每周都上」。钉住它，免得将来有人以为这里是 19。
        """
        self.write_raw_obj({"courses": [
            {"name": "没写周次", "dayOfWeek": 1, "nodes": [1, 2]},
        ]})
        got = self.reopen().courses()[0]
        self.assertEqual(set(range(1, format_spec.MAX_WEEK_LIMIT + 1)), set(got.weeks))

    def test_broken_templates_string_is_ignored(self):
        """模板那段坏了只影响模板，不能让程序和课表一起读不出来"""
        self.write_raw_obj({
            "templates": "这不是 json",
            "courses": [{"name": "好课", "dayOfWeek": 1, "nodes": [1, 2], "weeks": "1"}],
        })
        s = self.reopen()
        self.assertEqual({}, s.custom_templates())
        self.assertFalse(s.has_custom_templates)
        self.assertEqual(6, len(s.templates()), "坏模板要回落到内置六套")
        self.assertEqual(1, len(s.courses()), "课表不该受模板损坏影响")

    def test_json_null_is_not_treated_as_missing(self):
        """
        现状（脆弱点）：磁盘上是 `null` 时，`_data.get(key, default)`
        返回的是 None 而不是默认值，于是字符串属性变成字面量 "None"。

        正常写入路径永远不会产出 null，所以这只有手改存档才会碰到。
        这里不是断言「这是好行为」，而是把现状钉住：将来谁决定把 null
        当成缺省值处理，会看到这条失败，从而有意识地去改。
        """
        self.write_raw_obj({"term_name": None, "last_reminder": None})
        s = self.reopen()
        self.assertEqual("None", s.term_name)
        self.assertEqual("None", s.last_reminder_key)


# ============================================================
#  九、apply_import
# ============================================================

class TestApplyImport(StoreTestCase):
    """
    导入的三段（term / courses / templates）各自独立，规则是同一条：

        **「没写」永远表示「不要动」，而不是「清空」。**

    这条规则是防止一次误操作毁掉用户数据的关键，也是
    format_spec 里把「缺省」解析成 None（区别于空列表）的原因。
    所以两个分支（replace / merge）和三种「没写」都要走一遍，
    外加判重键的语义。
    """

    def test_replace_mode_swaps_the_whole_schedule(self):
        self.store.save_courses([Course("旧课", 1, 1, 2, frozenset({1}))])
        p = self.parsed(courses=[
            {"name": "新课一", "dayOfWeek": 2, "nodes": [3, 4], "weeks": "1-5"},
            {"name": "新课二", "dayOfWeek": 3, "nodes": [5, 6], "weeks": "2", "place": "A-101"},
        ])

        msg = self.store.apply_import(p, "replace")

        self.assertEqual("已导入 2 门课（原课表已替换）", msg)
        got = self.reopen().courses()
        self.assertEqual(["新课一", "新课二"], [c.name for c in got])
        self.assertEqual("A-101", got[1].place)

    def test_merge_mode_adds_new_and_skips_duplicates(self):
        self.store.save_courses([
            Course("高等数学", 1, 3, 4, frozenset({1, 2, 3}), "教一-203"),
        ])
        p = self.parsed(courses=[
            # 与已有的同名、同天、同起始节 → 重复
            {"name": "高等数学", "dayOfWeek": 1, "nodes": [3, 4], "weeks": "1-5"},
            # 新的
            {"name": "大学英语", "dayOfWeek": 1, "nodes": [1, 2], "weeks": "2-4"},
        ])

        msg = self.store.apply_import(p, "merge")

        self.assertEqual("合并完成：新增 1 门，跳过 1 门重复", msg)
        got = self.reopen().courses()
        self.assertEqual(2, len(got))
        self.assertEqual("教一-203", got[0].place, "被判定为重复的旧课不能被覆盖")

    def test_merge_counts_duplicate_by_name_day_and_node(self):
        """
        判重键 = 课程名 + 星期 + 起始节次，**不是只按名字**。

        同一门课一周上两次（正课 / 习题课在不同时段）是真实存在的，
        只按名字去重会把第二次误判成重复，用户会莫名其妙少一门课。
        """
        self.store.save_courses([Course("体育", 5, 7, 8, frozenset({2}))])
        p = self.parsed(courses=[
            {"name": "体育", "dayOfWeek": 5, "nodes": [7, 8], "weeks": "2"},   # 重复
            {"name": "体育", "dayOfWeek": 6, "nodes": [7, 8], "weeks": "2"},   # 换了一天 → 新的
            {"name": "体育", "dayOfWeek": 5, "nodes": [9, 10], "weeks": "2"},  # 换了时段 → 新的
        ])

        msg = self.store.apply_import(p, "merge")

        self.assertEqual("合并完成：新增 2 门，跳过 1 门重复", msg)
        self.assertEqual(3, len(self.reopen().courses()))

    def test_absent_courses_section_keeps_the_schedule(self):
        """
        `courses` 缺失或为空时解析成 None，含义是「不要动」。

        这正是「我只想改作息，课表别动」那条正当需求的支持点 ——
        如果这里退化成「清空」，用户导一次 AI 生成的模板就丢光课表。
        """
        self.store.save_courses([Course("要保留的课", 1, 1, 2, frozenset({1}))])
        p = self.parsed(
            term={"name": "新学期", "startDate": "2026-09-14", "totalWeeks": 18},
            courses=[],
        )
        self.assertIsNone(p.courses)

        msg = self.store.apply_import(p, "replace")

        self.assertEqual("课表保持不变", msg)
        self.assertEqual(["要保留的课"], [c.name for c in self.reopen().courses()])

    def test_absent_courses_also_keeps_in_merge_mode(self):
        """合并模式同样适用：没写课表就什么都不做，不能因为模式不同就变脸"""
        self.store.save_courses([Course("要保留的课", 1, 1, 2, frozenset({1}))])
        p = self.parsed(templates={"A": [
            {"start": "00:00", "end": "08:00", "title": "睡觉", "kind": "SLEEP"},
            {"start": "08:00", "end": "24:00", "title": "白天"},
        ]})

        msg = self.store.apply_import(p, "merge")

        self.assertIn("课表保持不变", msg)
        self.assertEqual(["要保留的课"], [c.name for c in self.reopen().courses()])

    def test_absent_term_section_keeps_term_settings(self):
        """文件里没有 term 段 → 学期设置原封不动（不是「重置成内置」）"""
        self.store.term_name = "我的学期"
        self.store.term_start = date(2026, 9, 14)
        self.store.total_weeks = 18

        p = self.parsed(courses=[
            {"name": "课", "dayOfWeek": 1, "nodes": [1, 2], "weeks": "1"},
        ])
        self.assertIsNone(p.term)
        self.store.apply_import(p, "replace")

        back = self.reopen()
        self.assertEqual("我的学期", back.term_name)
        self.assertEqual(date(2026, 9, 14), back.term_start)
        self.assertEqual(18, back.total_weeks)

    def test_term_section_is_applied_and_persisted(self):
        p = self.parsed(
            term={"name": "大一上", "startDate": "2026-09-14", "totalWeeks": 18},
            courses=[],
        )
        self.store.apply_import(p, "replace")

        back = self.reopen()
        self.assertEqual("大一上", back.term_name)
        self.assertEqual(date(2026, 9, 14), back.term_start)
        self.assertEqual(18, back.total_weeks)
        self.assertEqual(
            {"term_name": "大一上", "term_start": "2026-09-14", "total_weeks": 18},
            {k: self.raw()[k] for k in ("term_name", "term_start", "total_weeks")},
        )

    def test_templates_section_is_applied_and_reported(self):
        p = self.parsed(templates={"A": [
            {"start": "00:00", "end": "07:30", "title": "睡觉", "kind": "SLEEP"},
            {"start": "07:30", "end": "24:00", "title": "自定义的一天", "kind": "STUDY"},
        ]})

        msg = self.store.apply_import(p, "replace")

        self.assertEqual("课表保持不变，作息模板 1 种", msg)
        back = self.reopen()
        self.assertEqual(7 * 60 + 30, back.custom_templates()[DayType.A][0].end)
        self.assertEqual(7 * 60 + 30, back.templates()[DayType.A][0].end)
        self.assertEqual(
            _blocks(BS.templates()[DayType.SATURDAY]),
            _blocks(back.templates()[DayType.SATURDAY]),
        )

    def test_absent_templates_section_keeps_user_templates(self):
        """文件里没有 templates 段 → 用户改过的模板不能被冲掉"""
        custom = {DayType.A: [
            Block(0, 7 * 60, "睡觉", kind=Kind.SLEEP),
            Block(7 * 60, 1440, "自定义的一天", kind=Kind.STUDY),
        ]}
        self.store.save_templates(custom)

        p = self.parsed(courses=[
            {"name": "课", "dayOfWeek": 1, "nodes": [1, 2], "weeks": "1"},
        ])
        self.assertIsNone(p.templates)
        self.store.apply_import(p, "replace")

        self.assertEqual(7 * 60, self.reopen().custom_templates()[DayType.A][0].end)

    def test_apply_import_returns_a_readable_sentence(self):
        """
        返回值是直接显示给用户的，所以既不能是空串，也不能是异常信息。

        三段都有内容时应该用「，」串起来，让用户一眼看出这次动了什么。
        """
        p = self.parsed(
            term={"name": "大一上", "startDate": "2026-09-14", "totalWeeks": 18},
            courses=[{"name": "课", "dayOfWeek": 1, "nodes": [1, 2], "weeks": "1"}],
            templates={"A": [
                {"start": "00:00", "end": "08:00", "title": "睡觉", "kind": "SLEEP"},
                {"start": "08:00", "end": "24:00", "title": "白天"},
            ]},
        )

        msg = self.store.apply_import(p, "replace")

        self.assertEqual("已导入 1 门课（原课表已替换），作息模板 1 种", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
