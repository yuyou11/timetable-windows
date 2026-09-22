"""
一次性工具：把 `app/api.py` 的 `Api` 类按领域拆成几个 Mixin。

## 为什么用脚本而不是手工剪贴

要搬的是一千多行，里面全是中文注释和 f-string 报错文案。手工复制抄错一个字，
就变成「运行时不报错、只是用户看到一句错话」—— 本项目最怕的那种静默失效。
脚本按 `ast` 给的行号**原样切片**源码文本，搬过去的方法逐字节不变。

⚠️ 切片要**连方法前面的注释和空行一起切**。第一版只按 `lineno..end_lineno` 切，
结果方法之间的分节注释（`# ==== 课程编辑 ====`）和类文档串全部丢了，
方法也挤在一起。第二版改成「上一个兄弟节点的末尾 + 1」起切 —— 夹在中间的
注释天然是给**后面**那个方法写的，跟着它走才对。

## 为什么是 Mixin，不是「领域服务对象」

pywebview 建窗口时会**递归遍历 `js_api` 对象的所有公开属性**去找要暴露的
方法（README 坑 1：0xC0000409 硬崩就是这么来的）。所以 `Api` 组装完之后
必须仍然是**一个对象**、公开属性仍然只有方法。

    ✅  class Api(BallMixin, CoursesMixin, ...):   # dir() 上还是那些方法
    ❌  self.courses_svc = CourseService()          # 扫描器会钻进去，走回老路

Mixin 的私有方法（`_course_from_payload` 这种下划线开头的）扫描器会跳过，
和拆分前的情况一样。

## 怎么跑

    python tools/split_api.py

跑完**必须 review 一遍 `git diff`** 再提交 —— 脚本保证的是「搬运不失真」，
不保证「分组分得对」。跑完还要跑一次全量测试。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "app" / "api.py"

#: 分组：新文件名 -> (Mixin 类名, 这一组是干什么的, 方法名列表)
#:
#: 方法名必须和 api.py 里的一字不差；漏写的名字会留在 api.py 的核心类里
#: （不是错误，只是没拆干净 —— 跑完看打印出来的「留在核心类」清单就知道）。
GROUPS: dict[str, tuple[str, str, list[str]]] = {
    "api_ball.py": (
        "BallMixin",
        "悬浮窗桥接 —— 前端 `ball.js` 调的那几个方法，以及首次启动向导的落点。",
        [
            "ball_state", "move_ball", "ball_drag_start", "ball_drag_end",
            "ball_hover", "ball_slide_out", "show_main", "hide_ball",
            "mark_launched",
        ],
    ),
    "api_settings.py": (
        "SettingsMixin",
        "设置页那几个开关，和学期信息。",
        [
            "set_enabled", "set_ball_enabled", "set_remind_lead",
            "set_term", "set_week_override",
        ],
    ),
    "api_courses.py": (
        "CoursesMixin",
        "课程增删改 —— 课程编辑页。",
        [
            "get_courses", "save_course", "delete_course", "toggle_course",
            "clear_courses", "reset_courses", "_course_from_payload",
        ],
    ),
    "api_templates.py": (
        "TemplatesMixin",
        "作息模板与日型策略 —— 作息编辑页、设置页的日型勾选。",
        [
            "get_templates", "save_template_block", "delete_template_block",
            "shift_wake_time", "reset_templates",
            "get_day_types", "save_day_types", "reset_day_types", "_day_types_ok",
            "_block_from_payload",
        ],
    ),
    "api_transfer.py": (
        "TransferMixin",
        "导入导出、AI 提示词，以及文件选择框。",
        [
            "import_from_file", "confirm_import", "_preview_dict",
            "export_to_file", "copy_json", "get_ai_prompt",
            "_ask_open_file", "_ask_save_file",
        ],
    ),
}

#: 每个新文件开头统一放的 import。宁可多不可少 —— 多的后面用
#: 「未使用的 import」检查一个个删掉，少的会让产物直接跑不起来。
HEADER_IMPORTS = """\
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from . import builtin_data, engine, format_spec, slots, wake_shift
from .ai_prompt import AiPrompt
from .day_type_policy import DayTypePolicy
from .models import DAY_TYPE_LABEL, DAY_TYPE_ORDER, Block, Course, DayType, Kind
from .payloads import (  # noqa: F401
    _block_dict,
    _course_dict,
    _moment_dict,
    _parse_day_type,
    _short_day_type,
    _valid_index,
)
from .store import Store
"""

NOTE = "\n（由 `tools/split_api.py` 从 `api.py` 原样切出，**一字未改**。）\n"


def is_docstring(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def slice_methods(lines: list[str], cls: ast.ClassDef):
    """
    按原始顺序切出每个方法的源码。

    返回 (方法名 -> 源码, 类文档串源码)。每段源码**从上一个兄弟节点的下一行
    开始**，所以夹在两个方法之间的注释、分节标题、空行都会跟着后面那个方法走。
    """
    out: dict[str, str] = {}
    doc = ""
    prev_end = cls.lineno          # 「class Api:」那一行
    for node in cls.body:
        if is_docstring(node):
            doc = "".join(lines[node.lineno - 1: node.end_lineno])
            prev_end = node.end_lineno
            continue
        if not isinstance(node, ast.FunctionDef):
            prev_end = node.end_lineno
            continue
        text = "".join(lines[prev_end: node.end_lineno])   # 行号 1-based，切片 0-based
        out[node.name] = text.lstrip("\n")
        prev_end = node.end_lineno
    return out, doc


def main() -> int:
    src = API.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)

    api_cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    methods, class_doc = slice_methods(lines, api_cls)

    claimed: dict[str, str] = {}
    for fname, (_cls, _desc, names) in GROUPS.items():
        for name in names:
            if name not in methods:
                raise SystemExit(f"!! {fname} 要的 {name} 在 Api 里找不到（名字写错？）")
            if name in claimed:
                raise SystemExit(f"!! {name} 被分到了两组：{claimed[name]} 和 {fname}")
            claimed[name] = fname

    # ---- 写出各个 Mixin ----
    for fname, (cls, desc, names) in GROUPS.items():
        body = "\n\n\n".join(methods[n] for n in names)
        out = f'"""\n{desc}\n{NOTE}"""\n\n' + HEADER_IMPORTS + f"\n\nclass {cls}:\n"
        out += '    """' + desc + '"""\n\n\n' + body
        (ROOT / "app" / fname).write_text(out, encoding="utf-8")
        print(f"  写出 app/{fname:18} {cls:16} {len(names)} 个方法")

    # ---- 重写 api.py：只留核心方法 + 组装 ----
    header = "".join(lines[: api_cls.lineno - 1])
    kept = [n for n, _ in methods.items() if n not in claimed]
    body = "\n\n\n".join(methods[n] for n in kept)

    bases = ", ".join(cls for cls, _d, _n in GROUPS.values())
    imports = "\n".join(
        f"from .{Path(f).stem} import {cls}" for f, (cls, _d, _n) in GROUPS.items()
    )
    head = f"{header}\n{imports}\n\n\nclass Api({bases}):\n{class_doc}\n\n\n"
    API.write_text(head + body, encoding="utf-8")

    print(f"\n  重写 app/api.py              {len(kept)} 个方法留在核心类")
    print("  留在核心类的有：")
    for n in kept:
        print(f"      {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
