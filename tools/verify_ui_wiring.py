"""
Is the day-type editing UI actually wired end to end?

Pure static + behavioural check, no screenshots:

  1. api.py defines the three bridged methods
  2. app.js CALLS them (via call('...')) -- a backend method nobody calls
     is dead code; a frontend call to a missing method fails silently
  3. index.html has the elements app.js binds to (by id)
  4. style.css defines the classes those elements use
  5. the API actually round-trips: save -> read back -> behaviour changes

Steps 1-4 catch the classic "renamed one side, forgot the other" bug, which
in this project has already caused a real silent failure (mark_launched).
Step 5 catches "wired up but does nothing".

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import os
import re
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WEB = ROOT / "app" / "web"

problems = []


def ok(msg):
    print("  [ok]   " + msg)


def bad(msg):
    print("  [FAIL] " + msg)
    problems.append(msg)


def main() -> int:
    py = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "style.css").read_text(encoding="utf-8")

    methods = ["get_day_types", "save_day_types", "reset_day_types"]

    print("1. 后端定义了这三个方法")
    for m in methods:
        if re.search(r"def " + m + r"\(", py):
            ok("api." + m)
        else:
            bad("api.py 里找不到 def " + m)

    print("2. 前端调用了它们")
    for m in methods:
        if re.search(r"call\(\s*'" + m + r"'", js):
            ok("app.js 调用了 " + m)
        else:
            bad("app.js 没有调用 " + m + "（后端方法成了死代码）")

    print("3. 前端绑定的元素都在 HTML 里")
    # app.js 里 $('xxx') 用到的、和日型相关的那几个 id
    for elem in ["dayTypeList", "dayTypeFallback", "dayTypeHint",
                 "saveDayTypes", "resetDayTypes"]:
        if ('id="' + elem + '"') in html:
            ok('#' + elem)
        else:
            bad('index.html 里没有 id="' + elem + '"（app.js 会拿到 null）')
        # 而且 app.js 确实在用它
        if ("'" + elem + "'") not in js:
            bad("app.js 里没有引用 #" + elem)

    print("4. 用到的 CSS 类都有定义")
    for cls in ["daytype-list", "daytype-item", "daytype-wake", "daytype-badge"]:
        if ("." + cls) in css:
            ok("." + cls)
        else:
            bad("style.css 里没有 ." + cls)

    print("5. 接口真的能跑通（保存 -> 读回 -> 行为改变）")
    tmp = tempfile.mkdtemp(prefix="tt_uiwire_")
    saved = os.environ.get("TIMETABLE_DATA_DIR")
    os.environ["TIMETABLE_DATA_DIR"] = tmp
    try:
        from app import engine
        from app.api import Api
        from app.models import DayType
        from app.store import Store

        store = Store()
        if not str(store.path).startswith(tmp):
            bad("隔离失败，拒绝继续：" + str(store.path))
            return 2
        api = Api(store=store)

        d0 = api.get_day_types()
        if len(d0.get("types", [])) == 6:
            ok("get_day_types 返回六种日型")
        else:
            bad("get_day_types 返回的日型数不对：" + str(len(d0.get("types", []))))

        if all(t.get("wake") for t in d0["types"]):
            ok("每种日型都带了起床时间（界面要显示它）")
        else:
            bad("有日型没带起床时间，界面上会显示空白")

        fri = date(2026, 9, 25)

        def friday_wake():
            tpl, pol = store.template_set()
            used = engine.day_type(fri, 3, store.courses(), pol)
            return used, engine.wake_minute(tpl[used])

        before = friday_wake()
        r = api.save_day_types(["A", "SATURDAY", "SUNDAY"], "A")
        if not r.get("ok"):
            bad("save_day_types 失败：" + str(r.get("message")))
        else:
            ok("save_day_types 保存成功")

        after = friday_wake()
        if before != after:
            ok(f"行为真的变了：周五 {before[0].value} {before[1] // 60}:{before[1] % 60:02d}"
               f" -> {after[0].value} {after[1] // 60}:{after[1] % 60:02d}")
        else:
            bad("保存前后行为完全一样 —— 设置没生效（这正是最难发现的那种坏法）")

        # 失败路径也要能用（界面靠它弹提示）
        r_bad = api.save_day_types([], "A")
        if not r_bad.get("ok") and r_bad.get("message"):
            ok("空选择被拦下，并且给了可读的提示")
        else:
            bad("一种日型都不勾却没有被拦下")

        r_reset = api.reset_day_types()
        if r_reset.get("ok") and not r_reset["dayTypes"]["hasCustomDayTypes"]:
            ok("reset_day_types 恢复成功")
        else:
            bad("reset_day_types 没有恢复到默认状态")
    finally:
        os.environ.pop("TIMETABLE_DATA_DIR", None)
        if saved is not None:
            os.environ["TIMETABLE_DATA_DIR"] = saved
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if problems:
        print("FAILED (" + str(len(problems)) + " problems):")
        for p in problems:
            print("  x " + p)
        return 1
    print("PASS: the day-type UI is wired end to end.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
