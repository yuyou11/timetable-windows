"""
前后端桥接层。

## 这个文件是干什么的

界面用 HTML/CSS/JS 写，但数据和计算都在 Python 这边。
两边通过 pywebview 的 `js_api` 通信：

    JS 侧：  await pywebview.api.get_today()
    Python： Api.get_today(self)   ← 就是这里的方法

方法名要一一对应，返回值必须是 **JSON 可序列化**的（dict / list / str / int / bool / None）。
返回 datetime、dataclass 之类的东西，前端会收到 undefined —— 这类错误不会报错，
只会让界面莫名其妙地空着，很难查。所以下面统一用 `_to_dict` 系列函数转换。

## 为什么计算放在 Python 而不是全用 JS 做

因为手机版已经有一套验证过的引擎（111 个测试）。把它移植到 Python，
两边共用同一套逻辑和同一套测试用例，比在 JS 里重写一遍再测一遍要可靠得多。
**能复用验证过的逻辑，就不要重写。**
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from . import __version__, engine, format_spec, skin, slots
from .models import (
    Course,
    DayType,
    Kind,
    day_type_name,
)
from .store import Store

#: 一周七天（周一=1 … 周日=7）。
#:
#: 课表页要显示**整周**，包括周六日 —— 学校的课确实会排到周末
#: （周六补课、单双周错开的实验课等）。
#:
#: 写成常量是因为这个值在 get_week 里要出现两次（表头的天、每行的格子），
#: 两处都用同一个名字才不会被改偏 —— 只改一处会得到一张
#: 「表头 7 列、每行只有 5 格」的错位表格。
WEEK_DAYS = 7


# 纯函数（内部结构 → 前端 dict）搬去 `payloads.py` 了，各 Mixin 按需从那儿 import。
# （`_fmt_duration` 不对外：它只被 payloads 内部那两个转换函数用到。）
from .payloads import _moment_dict, _short_day_type


from .api_ball import BallMixin
from .api_settings import SettingsMixin
from .api_courses import CoursesMixin
from .api_templates import TemplatesMixin
from .api_transfer import TransferMixin


class Api(BallMixin, SettingsMixin, CoursesMixin, TemplatesMixin, TransferMixin):
    """暴露给 JS 的全部方法。改这里要同步改网页那边的调用。"""

    def __init__(self, store: Optional[Store] = None) -> None:
        #: 允许外部把 Store 传进来（main.py 就是这么做的）。
        #:
        #: 以前这里写死 `Store()`，而 main.py 紧接着又自己建一个、再赋值
        #: 覆盖回来 —— 于是启动时白白多构造一个 Store，连带多读一次数据文件，
        #: 建完立刻被丢弃。传进来之后就只有一份了。
        #:
        #: 不传也能用（`Api()`），这时自己建一个 —— 冒烟测试和几个诊断工具
        #: 就是这么调的。
        self.store = store if store is not None else Store()

        # ------------------------------------------------------------
        #  ⚠️ 下面这几个属性**必须**以下划线开头，不能改成公开名字。
        #
        #  pywebview 创建窗口时会递归遍历这个对象的所有公开属性，
        #  找出要暴露给 JavaScript 的方法（见 webview/util.py）。
        #  下划线开头的会被跳过。
        #
        #  如果把 Window 对象存成公开属性，扫描器会钻进去：
        #
        #      window.native.AccessibilityObject.Handle.Zero.Zero.Zero.Zero...
        #
        #  这是一条**无限的属性链**，最终把 C 栈撑爆 ——
        #  进程以 0xC0000409 硬崩溃，Python 连异常都来不及记，
        #  日志里什么都没有。排查它会非常痛苦（我为此花了很久）。
        #
        #  而且它只在**无边框窗口**上触发：普通窗口的 AccessibilityObject
        #  链条会正常终止，无边框的不会。所以你看到的症状会是
        #  「主窗口好好的，一加上悬浮窗就崩」。
        # ------------------------------------------------------------
        self._window = None              # 主窗口，由 main.py 注入
        self._ball_window = None         # 悬浮窗窗口
        self._on_settings_changed = None # 回调：让 main.py 重排定时器 / 开关悬浮窗
        self._on_quit = None             # 回调：真正退出程序（需要停掉后台线程）
        self._on_show_main = None        # 回调：显示主窗口
        self._on_ball_drag_end = None    # 回调：拖完了，判断要不要吸附到边缘
        self._on_ball_drag_start = None  # 回调：开始拖，收起状态要先弹出来
        self._on_ball_move = None        # 回调：拖动中（含边界限制）
        self._on_ball_hover = None       # 回调：鼠标进出悬浮窗
        self._on_ball_slide_out = None   # 回调：手动拉出收起的悬浮窗

        #: 导入分两步：先解析出预览给用户看，确认后才写入。
        #: 暂存在这里，而不是让前端把整份数据再传回来 ——
        #: 让前端来回搬运大块数据，等于多一次「传丢了/传错了」的机会。
        self._pending_import: Optional[format_spec.Parsed] = None

    # ============================================================
    #  启动数据
    # ============================================================

    def bootstrap(self) -> dict[str, Any]:
        """界面首次加载时调一次，拿全部初始数据"""
        return {
            # 版本号只在 app/__init__.py 里写一次，这里引过来 ——
            # 再抄一份字面量的话，改版本号时就多一处会漏的地方。
            "version": __version__,
            "isFirstLaunch": self.store.is_first_launch,
            "term": self._term_dict(),
            "settings": self._settings_dict(),
            # 自定义外观的成品（算好的色值）。Python 推一次（main._push_skin）
            # 可能赶在页面加载完成之前，boot() 拿这份再兜一次 —— 同源的值，
            # 重复应用无害（和 theme 兜底是同一个思路）。
            "skin": self._skin_dict(),
            "today": self.get_today(),
            # 现存的日型全给（v5 起由 dayTypeConfig 决定有哪几个）：
            # 前端哪一页要显示日型名，都以这里的 label 为准 ——
            # 名字是用户数据，不能让前端再抄一份默认值。
            "dayTypes": [
                {"key": t.value, "label": day_type_name(t, self.store.day_type_names)}
                for t in self.store.day_type_config().types
            ],
            "kinds": [k.value for k in Kind],
            "maxNode": self.store.node_count,
            "maxWeeks": format_spec.MAX_WEEK_LIMIT,
        }

    def _term_dict(self) -> dict[str, Any]:
        start = self.store.term_start
        today = date.today()
        return {
            "name": self.store.term_name,
            "start": start.isoformat(),
            "startWeekday": format_spec.WEEKDAY_CN[start.isoweekday()],
            "totalWeeks": self.store.total_weeks,
            "weekOverride": self.store.week_override,
            "todayWeek": self.store.week_of(today),
        }

    def _settings_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.store.enabled,
            "ballEnabled": self.store.ball_enabled,
            "remindLead": self.store.remind_lead,
            "theme": self.store.theme,
            # 字体大小（整体缩放倍率）。前端对主窗口文档设 CSS zoom，
            # 悬浮窗/提醒条不缩放（窗口尺寸是按内容定的）。
            "fontScale": self.store.font_scale,
            # 自定义外观的原始值（色值/百分比）。成品色值走 _skin_dict ——
            # 前端只负责把算好的东西设上去，不再实现一遍颜色推导。
            "accent": self.store.accent,
            "textColor": self.store.text_color,
            "cardOpacity": self.store.card_opacity,
            "sidebarOpacity": self.store.sidebar_opacity,
            "bgImage": self.store.bg_image,
            "bgDim": self.store.bg_dim,
            "hasCustomTemplates": self.store.has_custom_templates,
            "hasCustomDayTypes": self.store.has_custom_day_types,
            # 「要不要把作息配置一起导出」的判据 —— 模板**或**日型被改过都算。
            # 前端不要自己用 hasCustomTemplates 去判断：
            # 那样在「只改了日型、没改模板」时会把日型设置漏掉（手机版踩过）。
            "hasCustomScheduleConfig": self.store.has_custom_schedule_config,
        }

    def _skin_dict(self) -> dict[str, Any]:
        """自定义外观的**成品**：按当前深浅档算好的 CSS 变量值。"""
        return skin.main_payload(
            self.store.theme,
            self.store.accent,
            self.store.card_opacity,
            self.store.bg_image,
            self.store.text_color,
            self.store.sidebar_opacity,
        )

    # ============================================================
    #  今天
    # ============================================================

    def get_today(self) -> dict[str, Any]:
        today = date.today()
        week = self.store.week_of(today)
        courses = self.store.courses()
        # 模板和策略**成对**取 —— 理由见 store.template_set 的说明
        # （「策略和模板必须同源」，否则会出现按 A 的策略取了 B 的模板）
        templates, config = self.store.template_set()
        items = engine.moments(today, week, courses, templates, config)

        now = datetime.now()
        now_minute = now.hour * 60 + now.minute

        current = engine.current_at(items, now_minute)
        nxt = engine.next_after(items, now_minute)

        return {
            "date": today.isoformat(),
            "dateText": f"{today.year} 年 {today.month} 月 {today.day} 日",
            "weekday": format_spec.WEEKDAY_CN[today.isoweekday()],
            "week": week,
            "isBeforeTerm": week < 1,
            "isAfterTerm": week > self.store.total_weeks,
            "dayType": engine.day_type(today, week, courses, config).value,
            # 用 day_type_display 而不是 DAY_TYPE_LABEL：后者只说「用哪套模板」，
            # 而「今天有没有早八」是日历决定的另一件事 —— 自定义策略下
            # 两者会分家（把工作日全落到 A 型时，「A 型日·有早八」就是假话）。
            "dayTypeLabel": engine.day_type_display(
                today, week, courses, config, self.store.day_type_names),
            "now": _moment_dict(current, now_minute) if current else None,
            "next": (
                {"time": slots.fmt(nxt.start), "title": nxt.title} if nxt else None
            ),
            "moments": [_moment_dict(m, now_minute) for m in items],
            "tomorrow": self._tomorrow_dict(courses),
            "clock": now.strftime("%H:%M"),
        }

    def _tomorrow_dict(self, courses: list[Course]) -> dict[str, Any]:
        """明天的预告：几点起、第一件事、有几节课、最后一项"""
        tomorrow = date.today() + timedelta(days=1)
        week = self.store.week_of(tomorrow)
        templates, config = self.store.template_set()
        day_type = engine.day_type(tomorrow, week, courses, config)
        template = templates[day_type]
        items = engine.moments(tomorrow, week, courses, templates, config)

        wake = engine.wake_minute(template)
        first = next((m for m in items if m.kind != Kind.SLEEP and m.start > 0), None)
        course_names = [m.title for m in items if m.is_course]
        last = next(
            (m for m in reversed(items)
             if m.kind != Kind.SLEEP and m.end < slots.MINUTES_PER_DAY),
            None,
        )

        return {
            "weekday": format_spec.WEEKDAY_CN[tomorrow.isoweekday()],
            "week": week,
            "dayTypeLabel": engine.day_type_display(
                tomorrow, week, courses, config, self.store.day_type_names),
            "wake": slots.fmt(wake) if wake is not None else None,
            "wakeMinutes": wake,
            "first": (
                {"time": slots.fmt(first.start), "title": first.title} if first else None
            ),
            "courseCount": len(course_names),
            "courseNames": course_names,
            "last": (
                {
                    "time": slots.fmt(last.start),
                    "end": slots.fmt(last.end),
                    "title": last.title,
                }
                if last
                else None
            ),
        }

    # ============================================================
    #  课表
    # ============================================================

    def get_week(self, offset: int = 0) -> dict[str, Any]:
        this_week = self.store.week_of(date.today())
        week = max(1, min(self.store.total_weeks, this_week + int(offset)))
        courses = self.store.courses()
        _, config = self.store.template_set()

        monday = self.store.term_start + timedelta(days=(week - 1) * 7)
        sunday = monday + timedelta(days=6)

        # ⚠️ 这里必须遍历**七天**，不能只到周五。
        #
        # 原来写的是 range(1, 6)，也就是周一到周五 —— 于是周六、周日的课
        # **在课表页上完全不显示**。而引擎（engine.day_type）和提醒调度
        # 一直是支持周末的，所以那些课确实会在「今天」页和提醒里出现，
        # 只有课表页看不见。用户看到的是一张"少了课"的表，
        # 很难判断是没导入成功还是程序不显示。
        #
        # 这个 bug 是导入新课表之后才暴露的：新表里有 5 门周末的课
        # （第 5 周的高数 / 形势与政策 / 大模型，第 2 周的程序设计 / 思想道德），
        # 而旧的 19 门课恰好全在周一到周五，所以一直没被发现。
        days = []
        today = date.today()
        for i in range(1, WEEK_DAYS + 1):
            dt = monday + timedelta(days=i - 1)
            day_type = engine.day_type(dt, week, courses, config)
            days.append({
                "dow": i,
                "label": format_spec.WEEKDAY_CN[i].replace("周", ""),
                "dateText": f"{dt.month}/{dt.day}",
                "dayType": day_type.value,
                "shortType": _short_day_type(day_type),
                # 课表页高亮「当天」这一列；翻到别的周时七天全是 False
                "isToday": dt == today,
            })

        # 行结构来自「节数信息」（store.periods，默认 "442"）：一天的小节数
        # = 各位之和，不再写死 10 —— "442" 就是内置的 10 小节，"445" 是 13 小节
        # （第 11-13 节的时间按晚课节奏外推，见 slots.py）。每节单独成行，
        # 有的学校课不成对，按大课分行会整行漏课；同一门课横跨几行时，
        # 由前端用 rowspan 把相邻的同课格子合并回去。
        #
        # ⚠️ 行标签直接用小节号：学校数法和内部编号在这里是一一对应的
        # （每节 = 一个 45 分钟小节）。
        rows = []
        node_limit = self.store.node_count
        for start in range(1, node_limit + 1):
            rows.append({
                "startNode": start,
                "endNode": start,
                "label": f"第 {start} 节",
                "startTime": slots.fmt(slots.start(start)),
                "endTime": slots.fmt(slots.end(start)),
                "cells": [],
            })

        for row in rows:
            cells = []
            for dow in range(1, WEEK_DAYS + 1):
                # ⚠️ 匹配必须按「节点区间有重叠」，不能像以前那样要求
                # start_node 恰好等于行首 —— 以前行是写死的五对大课，
                # 别的学校单节的课、跨在 2-3 节上的课，一行都对不上，
                # 在课表页上**完全不显示**（连「有这行」的机会都没有）。
                hit = next(
                    (
                        c for c in courses
                        if c.enabled and c.day_of_week == dow
                        and c.start_node <= row["endNode"]
                        and c.end_node >= row["startNode"]
                        and week in c.weeks
                    ),
                    None,
                )
                nodes_text = None
                if hit is not None:
                    nodes_text = (
                        f"第 {hit.start_node}-{hit.end_node} 节"
                        if hit.end_node > hit.start_node
                        else f"第 {hit.start_node} 节"
                    )
                cells.append(
                    {
                        "dow": dow,
                        "name": hit.name if hit else None,
                        "place": hit.place if hit else None,
                        "weeksText": format_spec.format_weeks(hit.weeks) + " 周" if hit else None,
                        "nodesText": nodes_text,
                    }
                )
            row["cells"] = cells

        return {
            "week": week,
            "isCurrent": week == this_week,
            "rangeText": f"{monday.month}.{monday.day} – {sunday.month}.{sunday.day}",
            "days": days,
            "rows": rows,
        }

    def get_week_overview(self) -> dict[str, Any]:
        """课表页「选周浮层」的底料：每一周有没有课、日期范围、点阵形状。

        浮层下三分之一是各周的**点阵预览图**（对齐手机版）：每张图 7 列
        小圆点 = 一周七天，每行是两小节合成的「大节」（442 → 10 小节
        → 5 行），亮色 = 那个时段有课，灰色 = 没课 —— 一眼看出这周的
        「形状」。pattern[日][行] 用 "0"/"1" 字符串存，前端直接铺格子。

        「有没有课」必须和 get_week 取格子是**同一条判据**
        （c.enabled 且 week in c.weeks）—— 口径一旦分家，就会出现
        「点阵说这周有课、翻过去却是张空表」的怪现象。点阵的点亮判据
        是它的逐格版：节点区间重叠（和 get_week 的 hit 条件一致）。

        todayWeek 是未钳位的今天周次（开学前会是 0 或负数），前端拿它
        换算 weekOffset（get_week 的 offset 相对它偏移）；
        currentWeek 是钳到 1..总周数 的「本周」，浮层的「回到本周」按钮
        按它判禁用 —— 必须钳位，否则开学前它是 0，预览图里根本没有第 0 周。
        """
        courses = self.store.courses()
        this_week = self.store.week_of(date.today())
        total = self.store.total_weeks
        start = self.store.term_start
        node_limit = self.store.node_count
        pair_rows = (node_limit + 1) // 2   # 两小节并作一点：10 节 → 5 行

        weeks = []
        for w in range(1, total + 1):
            monday = start + timedelta(days=(w - 1) * 7)
            sunday = monday + timedelta(days=6)

            pattern = []
            for dow in range(1, WEEK_DAYS + 1):
                row = ""
                for p in range(pair_rows):
                    lo, hi = 2 * p + 1, min(2 * p + 2, node_limit)
                    lit = any(
                        c.enabled and c.day_of_week == dow and w in c.weeks
                        and c.start_node <= hi and c.end_node >= lo
                        for c in courses
                    )
                    row += "1" if lit else "0"
                pattern.append(row)

            weeks.append({
                "week": w,
                "hasCourse": any(c.enabled and w in c.weeks for c in courses),
                "rangeText": f"{monday.month}.{monday.day} – {sunday.month}.{sunday.day}",
                "pattern": pattern,
            })

        return {
            "todayWeek": this_week,
            "currentWeek": max(1, min(total, this_week)),
            "totalWeeks": total,
            "weeks": weeks,
        }

    # ============================================================
    #  杂项
    # ============================================================

    def open_data_folder(self) -> dict[str, Any]:
        import subprocess
        folder = self.store.path.parent
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(folder)])
        return {"ok": True, "message": str(folder)}

    def log_error(self, message: str) -> dict[str, Any]:
        """
        前端把 JS 报错转发到这里，写进日志文件。

        为什么需要它：前端异常默认只出现在开发者工具里，
        而用户不会去开开发者工具。于是「点了没反应」这种问题
        既看不见、也查不到。写进文件，下次就能对着日志找。

        日志超过 64 KB 就截断重来 —— 这是防它无限长大的最简单办法，
        比搞日志轮转省事得多。
        """
        try:
            log = self.store.path.parent / "error.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            if log.exists() and log.stat().st_size > 64 * 1024:
                log.unlink()
            with log.open("a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
        except OSError:
            pass
        return {"ok": True}

    def quit_app(self) -> dict[str, Any]:
        if self._on_quit:
            self._on_quit()
        return {"ok": True}

    # ============================================================
    #  内部工具
    # ============================================================

    def _notify_settings_changed(self) -> None:
        """设置变了 → 通知 main.py 重排定时器、开关悬浮窗"""
        if self._on_settings_changed:
            self._on_settings_changed()

    def _apply_setting(self, name: str, value: Any) -> dict[str, Any]:
        """
        写一个设置项 → 通知主窗口重排 → 回传最新的设置快照。

        三个 `set_xxx` 原本是逐字相同的三段代码，只有属性名不同。
        抽出来是为了让**「通知」只有一处实现** —— 漏掉一次通知，定时器和
        悬浮窗开关就会停在旧状态，而且不会有任何报错。
        """
        setattr(self.store, name, value)
        self._notify_settings_changed()
        return self._settings_dict()

    def _courses_ok(self, message: str) -> dict[str, Any]:
        """
        课表改完之后的统一收尾。

        每个改课表的方法都以同样的三件事结束：存盘、通知主窗口、
        把**刷新后的列表**回给前端。抽出来是为了让「通知」不会漏 ——
        漏一次，界面就停在旧数据上，不报错、不崩溃，只是「看着不对」。

        ⚠️ 顺序有意义：先通知，再取数据。反过来的话回给前端的可能不是
        通知之后的状态。
        """
        self._notify_settings_changed()
        return {"ok": True, "message": message, "courses": self.get_courses()}

    def _templates_ok(self, message: str) -> dict[str, Any]:
        """
        作息模板改完之后的统一收尾。理由同 `_courses_ok`。

        注意回给前端的永远是 `get_templates()` —— **合并了内置模板之后的
        完整六套**，而不是用户改过的那部分。界面上要显示的就是完整六套。
        """
        self._notify_settings_changed()
        return {"ok": True, "message": message, "templates": self.get_templates()}
