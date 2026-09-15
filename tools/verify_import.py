"""
Verify the generated schedule file actually imports cleanly -- through the
program's OWN import path, in an ISOLATED data directory.

    python tools/verify_import.py 课表-2026-09-14.json

Why this exists: format_spec.parse() passing is necessary but not sufficient.
The real flow is parse -> _preview_dict -> confirm_import -> store.apply_import,
and the preview is what the user actually sees before saying yes. If the
preview is wrong (or the file replaces the wrong section), the parse passing
tells you nothing.

## Isolation is mandatory

This constructs a real App/Store. A past incident (see the project notes) had
a test write an almost-empty data file over the user's real schedule because
it used the default data directory. So:
  - TIMETABLE_DATA_DIR points at a temp dir
  - an assertion confirms the isolation actually took effect before going on
  - the caller's real data.json hash is compared before and after

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def real_data_hash():
    p = Path(os.environ["APPDATA"]) / "Timetable" / "data.json"
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_check(schedule: Path, tmp: str) -> int:
    """真正的验证逻辑。清理和真实数据核对交给 main() 做。

    为什么不把 return 放在 finally 里：**在 finally 里 return 会把异常吞掉**
    （Python 会发 SyntaxWarning）。那样一旦中间出异常，脚本会静默返回一个
    成功码 —— 一个"检查没跑完却报告通过"的验证脚本比没有更糟。
    """
    from app import format_spec
    from app.api import Api
    from app.store import Store

    store = Store()
    # isolation self-check -- refuse to run against the real file
    if not str(store.path).startswith(tmp):
        print(f"ISOLATION FAILED: store path is {store.path}", file=sys.stderr)
        return 3
    print(f"isolated data dir          : {tmp}")
    print(f"seeded courses             : {len(store.courses())}")
    print(f"seeded total_weeks         : {store.total_weeks}")
    print(f"seeded templates in file   : {'templates' in store._data}")

    api = Api(store=store)

    # ---- step 1: 走真正的 import_from_file，只把文件对话框换成假的 ----
    #
    # ⚠️ 第一版这里**绕过了 import_from_file**，直接调
    # `format_spec.parse()` + `_preview_dict()`（理由是那个方法要弹文件框，
    # 自动化不了）。结果它验了逻辑却**没走到序列化边界** ——
    # 而后来真出事的正是那一步：import_from_file 的返回值里夹了一个
    # dataclass 实例，pywebview 做 json.dumps 时抛异常，
    # 整个导入功能不可用，而这份脚本当时报的是 PASS。
    #
    # 教训：**绕开某个入口做验证，就等于那条入口没验证过。**
    # 现在只替换 _ask_open_file 这一个函数 —— 它本来就是"让用户选文件"
    # 这个动作的边界，替换掉它比跳过整个方法诚实得多。
    api._ask_open_file = lambda: str(schedule)

    result = api.import_from_file()
    if not result.get("ok"):
        print(f"import_from_file 失败：{result.get('message')}", file=sys.stderr)
        return 1

    # 这一步是重点：模拟 pywebview 对返回值的处理。
    # 它会 json.dumps 之后才把结果发给前端（webview/util.py 的 _call），
    # 序列化失败会被它捕获成 {isError: true}，用户看到一句含糊的报错。
    try:
        json.dumps(result)
    except TypeError as e:
        print(f"返回值无法 JSON 序列化：{e}", file=sys.stderr)
        print("（pywebview 会在这一步失败，界面上会显示「导入失败」）",
              file=sys.stderr)
        return 1

    preview = result["preview"]
    print()
    print("=== 预览（用户在界面上会看到的东西）===")
    for k, v in preview.items():
        if k == "templateLines":
            continue
        print(f"  {k:<20} {v}")
    print("  返回值可序列化       True")

    # ---- step 2: the user clicks "替换现有课表" ----
    result = api.confirm_import("replace")
    print()
    print("=== 确认导入（replace）===")
    print(f"  ok      : {result.get('ok')}")
    print(f"  message : {result.get('message')}")

    # ---- step 3: read it back from disk ----
    store2 = Store()
    courses = store2.courses()
    print()
    print("=== 从磁盘读回 ===")
    print(f"  courses            : {len(courses)}")
    print(f"  total_weeks        : {store2.total_weeks}")
    print(f"  term_name          : {store2.term_name}")
    print(f"  term_start         : {store2.term_start}")
    print(f"  templates in file  : {'templates' in store2._data}")
    print(f"  ball_enabled       : {store2.ball_enabled}")

    # ---- assertions ----
    problems = []
    if len(courses) != 24:
        problems.append(f"expected 24 courses, got {len(courses)}")
    if "templates" in store2._data:
        problems.append("templates got written -- the file should not touch them")
    if store2.term_start.isoformat() != "2026-09-07":
        problems.append(f"term_start changed: {store2.term_start}")
    if store2.total_weeks != 19:
        problems.append(f"total_weeks changed: {store2.total_weeks}")

    # the kept field must survive
    pe = [c for c in courses if "体育" in c.name]
    if not pe or pe[0].place != "操场 / 体育馆":
        problems.append(f"PE place not preserved: {pe[0].place if pe else 'missing'}")

    # spot-check a few weeks expansions
    def find(name, dow, node):
        for c in courses:
            if c.name == name and c.day_of_week == dow and c.start_node == node:
                return c
        return None

    ai = find("人工智能概论", 4, 7)
    if not ai or sorted(ai.weeks) != [3, 5, 7, 9, 11, 13, 15, 17]:
        problems.append(f"人工智能概论 weeks wrong: {sorted(ai.weeks) if ai else None}")

    sat = find("高等数学A(Ⅰ)", 6, 1)
    if not sat or sorted(sat.weeks) != [5]:
        problems.append(f"周六高数 weeks wrong: {sorted(sat.weeks) if sat else None}")

    print()
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print("  x " + p)
        return 1
    print("PASS: file imports cleanly, 24 courses, templates and settings untouched")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python tools/verify_import.py <file.json>", file=sys.stderr)
        return 2
    schedule = Path(sys.argv[1])
    if not schedule.exists():
        print(f"not found: {schedule}", file=sys.stderr)
        return 2

    before_hash = real_data_hash()
    print(f"real data.json hash before : {before_hash}")

    tmp = tempfile.mkdtemp(prefix="tt_verify_import_")
    os.environ["TIMETABLE_DATA_DIR"] = tmp
    code = 0
    try:
        code = run_check(schedule, tmp)
    finally:
        os.environ.pop("TIMETABLE_DATA_DIR", None)
        shutil.rmtree(tmp, ignore_errors=True)

    after_hash = real_data_hash()
    print()
    print(f"real data.json hash after  : {after_hash}")
    if after_hash != before_hash:
        print("!!! REAL DATA CHANGED -- restore it from your backup !!!",
              file=sys.stderr)
        return 4
    print("real data.json untouched")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
