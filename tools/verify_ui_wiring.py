"""
Is the day-type editing UI actually wired end to end?

Pure static + behavioural check, no screenshots:

  1. the backend mixins (app/api_*.py) define the three bridged methods
  2. the main-window JS (app.js + page-*.js) CALLS them (via call('...')) --
     a backend method nobody calls is dead code; a frontend call to a
     missing method fails silently
  3. index.html has the elements the JS binds to (by id)
  4. style.css defines the classes those elements use
  5. the API actually round-trips: save -> read back -> behaviour changes

Steps 1-4 catch the classic "renamed one side, forgot the other" bug, which
in this project has already caused a real silent failure (mark_launched).
Step 5 catches "wired up but does nothing".

Scanning notes (2026-09): both sides of the contract were mechanically split
(split_api.py -> api_*.py mixins; split_app_js.py -> page-*.js), so this
script scans whole groups instead of single files -- the next file split
must not blind the guard again. Step 5 picks its probe day dynamically:
since the holiday auto-detection landed (53780bf), class-free weekdays are
REST days and deliberately ignore the day-type policy, so a hardcoded
"Friday must change" test-day would false-alarm.

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import os
import re
import shutil
import sys
import tempfile
from datetime import date, timedelta
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
    # 契约两端的代码在两次机械拆分后分散在多个文件里。守卫盯的是
    # 「两端是否对上」，不是「代码住在哪个文件」，所以扫整组文件：
    # 下次再拆文件，守卫不会又一次被甩下。
    py = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((ROOT / "app").glob("api*.py"))
    )
    js_files = [WEB / "app.js"] + sorted(WEB.glob("page-*.js"))
    js = "\n".join(p.read_text(encoding="utf-8") for p in js_files)
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "style.css").read_text(encoding="utf-8")

    methods = ["get_day_types", "create_day_type", "delete_day_type",
               "update_day_type", "save_weekday_map", "save_early_type",
               "reset_day_types"]

    print("1. 后端定义了这几个方法")
    for m in methods:
        if re.search(r"def " + m + r"\(", py):
            ok("api_*." + m)
        else:
            bad("app/api_*.py 里找不到 def " + m)

    print("2. 前端调用了它们")
    for m in methods:
        if re.search(r"call\(\s*'" + m + r"'", js):
            ok("前端 JS 调用了 " + m)
        else:
            bad("前端 JS 没有调用 " + m + "（后端方法成了死代码）")

    print("3. 前端绑定的元素都在 HTML 里")
    # JS 里 $('xxx') 用到的、和日型相关的那几个 id
    for elem in ["dayTypeList", "earlyTypeSel", "weekdayMap",
                 "addDayType", "dayTypeHint", "resetDayTypes"]:
        if ('id="' + elem + '"') in html:
            ok('#' + elem)
        else:
            bad('index.html 里没有 id="' + elem + '"（JS 会拿到 null）')
        # 而且 JS 确实在用它
        if ("'" + elem + "'") not in js:
            bad("前端 JS 里没有引用 #" + elem)

    print("4. 用到的 CSS 类都有定义")
    for cls in ["daytype-list", "daytype-item", "daytype-wake", "daytype-badge",
                "weekday-map", "wd-check"]:
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
        if len(d0.get("types", [])) == 7:
            ok("get_day_types 返回七种日型（默认满 7 种）")
        else:
            bad("get_day_types 返回的日型数不对：" + str(len(d0.get("types", []))))

        rest = next((t for t in d0["types"] if t.get("key") == "7"), None)
        if rest and rest.get("locked") and not rest.get("weekdays"):
            ok("休息日行 locked 且不参与映射（界面渲染成自动生效）")
        else:
            bad("休息日（id 7）的 locked/映射状态不对")

        if d0.get("earlyType") == "1":
            ok("默认早八日型是 id 1（A 型日）")
        else:
            bad("默认早八日型不对：" + str(d0.get("earlyType")))

        # 新建 → 映射 → 删除：三个桥接真的能走通
        self_del = api.delete_day_type("3")
        if self_del.get("ok"):
            ok("删除「谁都不管」的日型成功（腾出 id 3）")
        else:
            bad("删除日型失败：" + str(self_del.get("message")))

        r_new = api.create_day_type("考试日")
        if r_new.get("ok") and r_new.get("newId") == "3":
            ok("create_day_type 分配了最小空闲 id")
        else:
            bad("create_day_type 没走通：" + str(r_new.get("message")))

        r_map = api.update_day_type("3", {"weekdays": [2]})
        if r_map.get("ok"):
            ok("update_day_type 改派星期成功")
        else:
            bad("update_day_type 没走通：" + str(r_map.get("message")))

        # 行为检查：周二改派后，起床时间跟着新模板走
        tue = date(2026, 9, 15)          # 第 2 周周二（有课、无早八）
        week = store.week_of(tue)

        def tue_wake():
            tpl, cfg = store.template_set()
            used = engine.day_type(tue, week, store.courses(), cfg)
            return used, engine.wake_minute(tpl[used])

        after_map = tue_wake()
        if after_map[0].value == "3":
            ok(f"行为真的变了：周二 -> 日型 3（起床 "
               f"{after_map[1] // 60}:{after_map[1] % 60:02d}）")
        else:
            bad("星期映射没生效 —— 周二没走到改派的日型（最难发现的那种坏法）")

        # 映射到不存在的日型必须被拦下（M2 变异守的就是这道）
        r_bad = api.save_weekday_map(["3", "MONDAY", "3", "3", "3", "5", "6"])
        if not r_bad.get("ok") and r_bad.get("message"):
            ok("映射到不存在的日型被拦下，并且给了可读的提示")
        else:
            bad("映射到不存在的日型却没有被拦下")

        r_names = api.update_day_type("1", {"name": "早八日"})
        if r_names.get("ok") and next(
                t for t in r_names["dayTypes"]["types"] if t["key"] == "1"
        )["label"] == "早八日":
            ok("update_day_type 改名后 label 跟着变")
        else:
            bad("update_day_type 改名没有生效：" + str(r_names.get("message")))

        r_reset = api.reset_day_types()
        if r_reset.get("ok") and not r_reset["dayTypes"]["hasCustomDayTypeConfig"]:
            ok("reset_day_types 恢复成功（配置和名字一起还原）")
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
