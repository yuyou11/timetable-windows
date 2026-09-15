"""
冒烟测试：检查所有模块能不能导入、关键接口能不能跑通。

不依赖界面，所以在没有图形环境（或者不方便开窗口）的时候也能跑。
打包前跑一遍，能挡掉大部分「一启动就崩」的低级错误。

    python tests/smoke.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 用临时目录，别碰用户真实的存档
os.environ["TIMETABLE_DATA_DIR"] = tempfile.mkdtemp(prefix="timetable_smoke_")

FAILED = []


def _assert(cond, msg="断言失败"):
    if not cond:
        raise AssertionError(msg)


def check(name: str, fn) -> None:
    try:
        fn()
        print(f"  [ok]   {name}")
    except Exception as e:                     # noqa: BLE001
        print(f"  [FAIL] {name}: {type(e).__name__}: {e}")
        FAILED.append(name)


print("1. 模块导入")
check("app.models", lambda: __import__("app.models", fromlist=["x"]))
check("app.slots", lambda: __import__("app.slots", fromlist=["x"]))
check("app.engine", lambda: __import__("app.engine", fromlist=["x"]))
check("app.format_spec", lambda: __import__("app.format_spec", fromlist=["x"]))
check("app.builtin_data", lambda: __import__("app.builtin_data", fromlist=["x"]))
check("app.store", lambda: __import__("app.store", fromlist=["x"]))
check("app.ai_prompt", lambda: __import__("app.ai_prompt", fromlist=["x"]))
check("app.api", lambda: __import__("app.api", fromlist=["x"]))
# main 会 import webview，界面相关；单独试
check("app.main", lambda: __import__("app.main", fromlist=["x"]))

print("\n2. 内置数据")
from app import builtin_data, engine, format_spec
from app.models import DayType

check("内置课表 19 门", lambda: _assert(len(builtin_data.courses()) == 19))
check("内置模板 6 种", lambda: _assert(len(builtin_data.templates()) == 6))


def check_templates_wake():
    t = builtin_data.templates()
    assert engine.wake_minute(t[DayType.A]) == 6 * 60 + 55
    assert engine.wake_minute(t[DayType.SATURDAY]) == 9 * 60


check("起床时间推导", check_templates_wake)

print("\n3. Api 接口")


def make_api():
    from app.api import Api
    return Api()


check("构造 Api", make_api)


def check_bootstrap():
    api = make_api()
    d = api.bootstrap()
    assert d["term"]["totalWeeks"] == 19
    assert d["today"]["moments"]
    assert d["today"]["tomorrow"]["wake"]


check("bootstrap", check_bootstrap)


def check_today():
    api = make_api()
    t = api.get_today()
    assert t["week"] >= 1
    assert t["now"] is not None
    assert len(t["moments"]) > 5
    # 时间轴必须首尾相接
    ms = t["moments"]
    assert ms[0]["start"] == 0
    assert ms[-1]["end"] == 1440
    for a, b in zip(ms, ms[1:]):
        assert a["end"] == b["start"], "时间轴有空洞"


check("get_today 时间轴连续", check_today)


def check_week():
    api = make_api()
    w = api.get_week(0)
    # 节次 5 行（1-2 / 3-4 / 5-6 / 7-8 / 9-10），星期 7 列（周一…周日）
    assert len(w["rows"]) == 5
    assert all(len(r["cells"]) == 7 for r in w["rows"]), "每行应有 7 格"
    assert len(w["days"]) == 7, "课表要显示整周七天，含周六日"


check("get_week 网格形状", check_week)


def check_courses_crud():
    api = make_api()
    n = len(api.get_courses())
    r = api.save_course({"name": "冒烟测试课", "dayOfWeek": 3, "startNode": 5,
                         "endNode": 6, "weeks": "1-5", "place": "T-101"})
    assert r["ok"], r.get("message")
    assert len(api.get_courses()) == n + 1

    # 故意写错，看看会不会被拦下并且给出可读的提示
    bad = api.save_course({"name": "坏课", "dayOfWeek": 9, "startNode": 1,
                           "endNode": 2, "weeks": "1"})
    assert not bad["ok"], "越界的星期应该被拦下"
    assert "1–7" in bad["message"], f"报错文案不够具体：{bad['message']}"

    bad2 = api.save_course({"name": "坏课", "dayOfWeek": 1, "startNode": 1,
                            "endNode": 2, "weeks": "5-2"})
    assert not bad2["ok"]
    assert "起点比终点大" in bad2["message"]


check("课程增删改 + 校验", check_courses_crud)


def check_ball_state():
    api = make_api()
    s = api.ball_state()
    assert s["ok"]
    assert s["now"] is not None
    assert "remain" in s["now"] and "progress" in s["now"]


check("ball_state", check_ball_state)


def check_template_overlap_guard():
    """
    重叠必须被拦下。

    引擎遇到重叠会按「先到先得」把后一格静默截断 —— 用户写的某一格就这么没了，
    界面上还看不出来。这种「不报错的错」比直接失败糟糕得多。
    """
    api = make_api()
    api.reset_templates()

    r = api.save_template_block("A", {
        "start": "07:00", "end": "08:00", "title": "冲突", "kind": "STUDY",
    })
    assert not r["ok"], "重叠应该被拦下"
    assert "重叠" in r["message"], r["message"]
    # 报错要能指导用户怎么改，而不是只说「错了」
    assert "两格都要改" in r["message"], f"报错不够有用：{r['message']}"


check("模板重叠拦截 + 报错可操作", check_template_overlap_guard)


def check_wake_time_one_click():
    """
    改起床时间必须能一步到位。

    这一条是**发现死锁之后补的**：

        模板里「睡觉」00:00–06:55 和「起床、洗漱」06:55–07:10 是相邻两格。
        想把起床时间推到 07:30，一格一格改是走不通的 ——
            改「睡觉」→ 报重叠
            改「起床、洗漱」→ 也报重叠
        每一步都被拦下，每句报错听起来都对，但用户就是改不动。

    所以专门做了一个 shift_wake_time，一次改两格。
    """
    api = make_api()
    api.reset_templates()

    # 先证明死锁确实存在（这是这个接口存在的理由）
    blocked = api.save_template_block("A", {
        "start": "00:00", "end": "07:30", "title": "睡觉", "kind": "SLEEP",
    }, index=0)
    assert not blocked["ok"], "单独改「睡觉」应该被重叠检查拦下（这正说明需要专门接口）"

    # ---- 提早起床：整段顺延，应该成功 ----
    r = api.shift_wake_time("A", "06:30")
    assert r["ok"], r.get("message")

    a_type = next(x for x in api.get_templates()["types"] if x["key"] == "A")
    assert a_type["wake"] == "06:30", f"起床时间没跟着变：{a_type['wake']}"

    blocks = {b["start"]: b for b in a_type["blocks"]}
    assert blocks["00:00"]["end"] == "06:30", "睡觉格没改"
    assert "06:30" in blocks, f"后面那一格没跟着挪：{sorted(blocks)}"
    # 时长要保住：起床洗漱原来是 15 分钟
    assert blocks["06:30"]["end"] == "06:45", f"时长变了：{blocks['06:30']}"

    # ---- 推迟起床：起床到早八之间塞不下，必须如实报错而不是硬塞 ----
    bad = api.shift_wake_time("A", "07:30")
    assert not bad["ok"], "推迟到 07:30 会挤到早八，应该被拦下"
    assert "塞不下" in bad["message"], bad["message"]
    # 报错要指出怎么办，而不是只说「不行」
    assert "早读" in bad["message"], f"报错不够可操作：{bad['message']}"
    assert "课表格子" in bad["message"], "要说明为什么锚点不能挪"

    api.reset_templates()
    back = next(x for x in api.get_templates()["types"] if x["key"] == "A")
    assert back["wake"] == "06:55", "恢复内置后应该回到 06:55"


check("改起床时间一步到位（含死锁验证）", check_wake_time_one_click)


def check_export_import_roundtrip():
    api = make_api()
    # 前面的用例往数据里加过课，先归零，否则这里的门数对不上。
    # 这也是为什么每个 check 都应该自己准备好前置状态 ——
    # 依赖上一个用例留下的数据，是测试之间最常见的隐形耦合。
    api.reset_courses()

    text = api.store.export_json(True)
    parsed = format_spec.parse(text)
    assert len(parsed.courses) == 19, f"内置应该是 19 门，实际 {len(parsed.courses)}"
    assert len(parsed.templates) == 6

    api.clear_courses()
    assert len(api.get_courses()) == 0, "清空后应该是空的"
    api.store.apply_import(parsed, "replace")
    assert len(api.get_courses()) == 19, "导入后应恢复 19 门课"


check("导出 → 清空 → 导入 往返", check_export_import_roundtrip)


def check_prompts():
    api = make_api()
    for kind in ("course", "template", "fix"):
        r = api.get_ai_prompt(kind)
        assert r["ok"] and len(r["text"]) > 200, f"{kind} 提示词太短"
        assert "${" not in r["text"], f"{kind} 提示词里有未展开的占位符"
    # 提示词里必须带上真实的学期信息，否则 AI 会瞎编日期
    r = api.get_ai_prompt("course")
    assert "2026-09-07" in r["text"], "提示词里没带上学期起始日"
    assert "19" in r["text"]


check("AI 提示词", check_prompts)


def check_term_change():
    api = make_api()
    r = api.set_term("测试学期", "2026-09-09", 18)   # 故意给一个周三
    assert r["ok"]
    assert r["term"]["start"] == "2026-09-07", f"没自动吸附到周一：{r['term']['start']}"
    assert "自动对齐" in r["message"]
    assert r["term"]["totalWeeks"] == 18


check("改学期自动吸附周一", check_term_change)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：{', '.join(FAILED)}")
    raise SystemExit(1)
print("全部通过")
