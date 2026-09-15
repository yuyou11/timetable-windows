"""
桥接层的「返回值契约」：**js_api 方法的返回值必须能 JSON 序列化**。

## 为什么需要这个文件

前端调后端这条路上，返回值不是原样过去的 —— pywebview 会先

    json.dumps(result)

再塞进一段 JS 里（见 `webview/util.py` 的 `_call`）。序列化一旦失败，
它**会捕获异常并伪装成一次成功的调用**，把 `{isError: true, value: {...}}`
回给前端。用户看到的就是一句莫名其妙的报错。

这个项目真的发生过（2026-09-14）：

    api.import_from_file() 返回了 {"ok": True, "preview": ..., "_parsed": parsed}
    而 parsed 是 format_spec.Parsed —— 一个 dataclass 实例，json 不认识它
    → 界面上弹「导入失败 TypeError: Object of type Parsed is not JSON serializable」

**整个导入功能因此完全不可用**，而 Python 侧日志里一个字都没有
（异常在 pywebview 内部被吞了）。用户是点了导入才知道的。

## 为什么上一次验证没抓住它

写这个测试的时候我意识到之前的 `tools/verify_import.py` 有个盲区：
它为了自动化，直接调了 `format_spec.parse()` + `_preview_dict()` +
`confirm_import()`，**跳过了 `import_from_file`**（那个要弹文件选择框）。
于是逻辑全对、序列化边界一次都没走到 —— 报了个 PASS。

教训和踩坑清单里那条一样：**端到端验证必须先确认「路径真的走到了」。**
所以这个测试**显式把文件对话框换成假的**，让 `import_from_file` 真正跑完，
再对它的返回值做 json.dumps —— 走的正是用户那条路。

## 覆盖方式

逐个调用**前端真的会调**的那些方法（方法名从 app.js / ball.js 里扫出来，
和 test_api_contract.py 用同一套正则），对每个返回值 json.dumps。

带参数的方法、以及会启动外部程序的方法会跳过 —— 跳过的都记在
`SKIP` 里并写明理由，而不是默默略过。**静默跳过等于没测。**
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "app" / "web"

CALL_RE = re.compile(r"call\(\s*'([A-Za-z_]\w*)'")
DIRECT_RE = re.compile(r"pywebview\.api\.([A-Za-z_]\w*)")

#: 不调用这些方法，以及为什么。**必须写明理由** —— 否则以后没人知道
#: 是真不用测，还是当初懒得测。
SKIP = {
    # 会启动外部程序 / 弹系统对话框。单测里跑它们会真的弹出资源管理器窗口，
    # 或者卡在对话框上等一个永远不会来的点击。
    "open_data_folder": "会打开资源管理器",
    "open_release_page": "会打开浏览器",
    "quit_app": "会真的退出程序",
    "show_main": "需要真实的窗口对象",
    # 需要参数。它们的返回形状由下面 TestImportRoundTrip 之外的
    # 功能测试覆盖（那里是带着真实参数调的）。
    "add_course": "需要参数",
    "update_course": "需要参数",
    "delete_course": "需要参数",
    "move_course": "需要参数",
    "add_template_block": "需要参数",
    "update_template_block": "需要参数",
    "delete_template_block": "需要参数",
    "set_template_block_time": "需要参数",
    "set_wake_time": "需要参数",
    "set_today_type": "需要参数",
    # 需要参数。它的返回结构由 tests/test_day_type_policy.py 里的
    # TestDayTypeApi 覆盖（那边带着真实参数调，并逐个 json.dumps 过）。
    "save_day_types": "需要参数",
    "confirm_import": "需要参数（且要先用 import_from_file 预备状态）",
    "set_enabled": "需要参数",
    "set_ball_enabled": "需要参数",
    "set_remind_lead": "需要参数",
    "set_term": "需要参数",
    "set_week_override": "需要参数",
    "set_theme": "需要参数",
    "log_error": "需要参数",
    "move_ball": "需要参数",
    "ball_hover": "需要参数",
    "ball_drag_end": "需要参数",
    "ball_drag_start": "需要参数",
    "ball_slide_out": "需要参数",
    "remove_recent": "需要参数",
}


def _frontend_called_methods() -> set[str]:
    names: set[str] = set()
    for p in sorted(WEB.glob("*.js")) + sorted(WEB.glob("*.html")):
        text = p.read_text(encoding="utf-8")
        names |= set(CALL_RE.findall(text))
        names |= set(DIRECT_RE.findall(text))
    return names


class ApiSerializableTestCase(unittest.TestCase):
    """造一个真 Api，但把数据目录隔离到临时目录、把系统对话框换成假的。"""

    def setUp(self):
        # ⚠️ 先隔离数据目录。**这一步不能省、也不能往后挪。**
        # 下面会构造真实的 Store 并调用会写数据的接口
        # （导入、改设置……），不隔离就会覆盖用户的真实 data.json。
        # 这个项目已经因此丢过一次课表，详见 test_ball_ui.py 里的说明。
        tmp = tempfile.mkdtemp(prefix="timetable_serial_")
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

        # 自检：隔离真的生效了才继续。宁可这里失败，
        # 也不能在隔离失效的情况下把用户的课表写掉。
        self.assertTrue(
            str(self.store.path).startswith(tmp),
            f"数据目录没隔离成功：{self.store.path}\n"
            f"拒绝继续 —— 再往下跑会覆盖用户的真实数据文件。",
        )

        self.api = Api(store=self.store)
        # 文件对话框一律返回 None（= 用户取消），
        # 需要真文件的测试自己再覆盖一次。
        self.api._ask_open_file = lambda: None
        self.api._ask_save_file = lambda: None

    def serializable(self, value, method: str):
        """返回 json.dumps 的结果，失败时给出**能照着改**的报错。"""
        try:
            return json.dumps(value)
        except TypeError as e:
            self.fail(
                f"api.{method}() 的返回值不能被 JSON 序列化：{e}\n\n"
                f"pywebview 会把返回值 json.dumps 之后才发给前端，"
                f"序列化失败会变成界面上一句「{type(e).__name__}: {e}」，"
                f"而 Python 侧不会有任何回报。\n"
                f"修法：把对象转成 dict / 基本类型，或者干脆不要把它放进返回值。"
            )


class TestEveryCalledMethodReturnsJson(ApiSerializableTestCase):
    """前端会调的每个方法，返回值都要能过 json.dumps"""

    def test_all_methods_are_serializable(self):
        called = _frontend_called_methods()
        # 正则坏掉的话下面会因为"没有方法要测"而空过 —— 那种假绿比失败更危险
        self.assertGreater(len(called), 20,
                           f"只从前端扫到 {len(called)} 个方法，正则或文件位置可能不对")

        checked, skipped = [], []
        for name in sorted(called):
            if name in SKIP:
                skipped.append(name)
                continue
            fn = getattr(self.api, name, None)
            if not callable(fn):
                continue          # 名字不存在由 test_api_contract 负责报

            try:
                result = fn()
            except TypeError:
                # 需要参数但不是必错 —— 由上面 SKIP 的说明负责解释
                skipped.append(name)
                continue
            except Exception:
                # 接口自己抛错（比如没有待导入的数据）也是合理行为，
                # 这条测试只关心**成功返回时**能不能序列化
                skipped.append(name)
                continue

            self.serializable(result, name)
            checked.append(name)

        # 断言真的测到了一批。全被跳过的话这条测试等于没跑。
        self.assertGreater(
            len(checked), 10,
            f"只实际检查了 {len(checked)} 个方法，覆盖太少：{checked}\n"
            f"跳过了：{sorted(skipped)}",
        )


class TestImportFromFileIsSerializable(ApiSerializableTestCase):
    """专门盯 `import_from_file` —— 它就是踩过坑的那一个。

    上面那条通用测试也会覆盖它，但这里把**真实的文件**喂进去，
    走完整的 读文件 → parse → 预览 这条链，
    确保将来任何一步往回加对象都会被抓住。
    """

    def _make_schedule(self) -> Path:
        """写一份最小但合法的 v2 课表文件"""
        text = (
            "{\n"
            '  "format": "timetable",\n'
            '  "version": 2,\n'
            '  "term": {"name": "测试学期", "startDate": "2026-09-07", "totalWeeks": 19},\n'
            '  "courses": [\n'
            '    {"name": "测试课", "dayOfWeek": 6, "nodes": [1, 2], "weeks": "5",\n'
            '     "place": "A-101"}\n'
            "  ]\n"
            "}\n"
        )
        path = Path(self.store.path).parent / "schedule.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_preview_result_is_serializable(self):
        path = self._make_schedule()
        self.api._ask_open_file = lambda: str(path)

        result = self.api.import_from_file()

        self.assertTrue(result.get("ok"), f"导入预览应该成功，实际：{result}")
        # 就是这一句曾经失败过
        self.serializable(result, "import_from_file")
        self.assertEqual(1, result["preview"]["courseCount"])

    def test_no_raw_objects_are_smuggled_into_the_return(self):
        """
        反向护栏：返回值里不该夹带任何"内部对象"。

        `_parsed` 那个键当年就是这么混进去的 —— 它看起来像是"顺手多回传一点
        信息，反正前端用不上也不会坏"。但它直接让整个功能崩了。

        这里把所有值过一遍：只允许 dict / list / str / int / float / bool / None。
        """
        path = self._make_schedule()
        self.api._ask_open_file = lambda: str(path)
        result = self.api.import_from_file()

        allowed = (dict, list, str, int, float, bool, type(None))

        def walk(node, trail="result"):
            self.assertIsInstance(
                node, allowed,
                f"{trail} 是 {type(node).__name__}，不是能过 JSON 的基本类型。\n"
                f"桥接返回值里只能有 dict / list / str / 数字 / 布尔 / None —— "
                f"传对象过来会让 pywebview 的 json.dumps 抛异常，"
                f"而用户只会看到一句含糊的「导入失败」。",
            )
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{trail}[{k!r}]")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{trail}[{i}]")

        walk(result)

    def test_import_still_works_end_to_end(self):
        """
        修完序列化之后，导入这件事本身还得好使。

        只测"能序列化"是不够的 —— 也可能顺手把返回结构改坏了、
        或者 _pending_import 没存上，导致后面 confirm_import 拿不到东西。
        """
        path = self._make_schedule()
        self.api._ask_open_file = lambda: str(path)

        self.api.import_from_file()
        result = self.api.confirm_import("replace")
        self.serializable(result, "confirm_import")
        self.assertTrue(result.get("ok"), f"确认导入应该成功，实际：{result}")

        names = [c.name for c in self.store.courses()]
        self.assertEqual(["测试课"], names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
