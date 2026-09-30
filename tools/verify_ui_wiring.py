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

    methods = ["get_day_types", "save_day_types", "reset_day_types"]

    print("1. 后端定义了这三个方法")
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
    for elem in ["dayTypeList", "dayTypeFallback", "dayTypeHint",
                 "saveDayTypes", "resetDayTypes"]:
        if ('id="' + elem + '"') in html:
            ok('#' + elem)
        else:
            bad('index.html 里没有 id="' + elem + '"（JS 会拿到 null）')
        # 而且 JS 确实在用它
        if ("'" + elem + "'") not in js:
            bad("前端 JS 里没有引用 #" + elem)

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

        # ---- 挑试验日（必须动态挑，理由见下） ----
        #
        # 假期自动识别（53780bf）上线后，没课的工作日会被判成 REST，
        # 而 REST **刻意**不参与策略映射（休息日不该被改回上学日）——
        # 在 REST 日上改策略「理应无效」。所以试验日不能写死周五：
        # 要挑一个「有课、且默认策略下不用 A 型模板」的工作日
        # （比如周二的 B 训练日），改策略强制回落到 A，起床时间才会变。
        mon = date(2026, 9, 21)          # 内置学期里的某一周的周一
        week = store.week_of(mon)
        probe, before = None, None
        for i in range(5):
            d = mon + timedelta(days=i)
            courses = store.courses()
            if engine.natural_day_type(d, week, courses) is DayType.REST:
                continue                 # 休息日改策略本来就无效，当不了试验日
            tpl, pol = store.template_set()
            used = engine.day_type(d, week, courses, pol)
            if used is DayType.A:
                continue                 # 本来就用 A，改成「只留 A」不会变
            probe = d
            before = (used, engine.wake_minute(tpl[used]))
            break

        if probe is None:
            bad("内置课表里挑不出试验日（需要有课且默认不用 A 型的工作日）——"
                "内置数据变了？这一步的行为检查整个跳不过去")
        else:
            r = api.save_day_types(["A", "SATURDAY", "SUNDAY"], "A")
            if not r.get("ok"):
                bad("save_day_types 失败：" + str(r.get("message")))
            else:
                ok("save_day_types 保存成功")

            def probe_wake():
                tpl, pol = store.template_set()
                used = engine.day_type(probe, week, store.courses(), pol)
                return used, engine.wake_minute(tpl[used])

            after = probe_wake()
            if before != after:
                ok(f"行为真的变了：{probe.strftime('%a')} "
                   f"{before[0].value} {before[1] // 60}:{before[1] % 60:02d}"
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
