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
from pathlib import Path
from typing import Any, Optional, Sequence

from . import builtin_data, engine, format_spec, slots
from .ai_prompt import AiPrompt
from .day_type_policy import DayTypePolicy
from .models import DAY_TYPE_LABEL, DAY_TYPE_ORDER, Block, Course, DayType, Kind, Moment
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


def _fmt_duration(minutes: int) -> str:
    """45 -> '45 分'；95 -> '1 时 35 分'；120 -> '2 时'"""
    if minutes <= 0:
        return ""
    if minutes < 60:
        return f"{minutes} 分"
    h, m = divmod(minutes, 60)
    return f"{h} 时" if m == 0 else f"{h} 时 {m} 分"


def _moment_dict(m: Moment, now_minute: Optional[int] = None) -> dict[str, Any]:
    d = {
        "start": m.start,
        "end": m.end,
        "timeStart": slots.fmt(m.start),
        "timeEnd": slots.fmt(m.end),
        "title": m.title,
        "note": m.note,
        "place": m.place,
        "kind": m.kind.value,
        "duration": m.duration,
        "durationText": _fmt_duration(m.duration),
        "isCourse": m.is_course,
    }
    if now_minute is not None:
        d["isNow"] = m.contains(now_minute)
        d["remain"] = max(0, m.end - now_minute)
        d["progress"] = (
            round((now_minute - m.start) * 100 / m.duration) if m.duration > 0 else 0
        )
        d["progress"] = max(0, min(100, d["progress"]))
    return d


def _course_dict(c: Course, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "name": c.name,
        "dayOfWeek": c.day_of_week,
        "weekday": format_spec.WEEKDAY_CN[c.day_of_week],
        "startNode": c.start_node,
        "endNode": c.end_node,
        "nodesText": f"第 {c.start_node}-{c.end_node} 节",
        "weeks": sorted(c.weeks),
        "weeksText": format_spec.format_weeks(c.weeks) + " 周",
        "place": c.place,
        "enabled": c.enabled,
    }


def _block_dict(b: Block) -> dict[str, Any]:
    return {
        "start": slots.fmt(b.start),
        "end": slots.fmt(b.end),
        "startMin": b.start,
        "endMin": b.end,
        "title": b.title,
        "note": b.note,
        "kind": b.kind.value,
        "nodes": list(b.nodes) if b.nodes else None,
        "durationText": _fmt_duration(b.duration),
    }


def _valid_index(index: int, items: Sequence[Any]) -> bool:
    """
    下标是不是还在范围内。

    抽出来是因为「列表已经在别处刷新过了」在界面上很常见（用户开着两个窗口、
    或者刚点了删除又点了编辑），而 `0 <= i < len(x)` 这个表达式写五遍，
    就有五个地方要各自判断边界是开还是闭。写一遍，只有一处要判对。
    """
    return 0 <= index < len(items)


def _parse_day_type(key: str) -> Optional[DayType]:
    """
    前端传来的日型字符串 → 枚举。不认识就返回 None。

    返回 None 而不是抛异常，是因为报错文案由调用方决定 ——
    三个调用点都要在文案里带上用户传进来的那个值。
    """
    try:
        return DayType(key)
    except ValueError:
        return None


class Api:
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
            "version": "1.0",
            "isFirstLaunch": self.store.is_first_launch,
            "term": self._term_dict(),
            "settings": self._settings_dict(),
            "today": self.get_today(),
            "dayTypes": [{"key": t.value, "label": DAY_TYPE_LABEL[t]} for t in DAY_TYPE_ORDER],
            "kinds": [k.value for k in Kind],
            "maxNode": slots.MAX_NODE,
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
            "hasCustomTemplates": self.store.has_custom_templates,
            "hasCustomDayTypes": self.store.has_custom_day_types,
            # 「要不要把作息配置一起导出」的判据 —— 模板**或**日型被改过都算。
            # 前端不要自己用 hasCustomTemplates 去判断：
            # 那样在「只改了日型、没改模板」时会把日型设置漏掉（手机版踩过）。
            "hasCustomScheduleConfig": self.store.has_custom_schedule_config,
            "dayTypes": self.store.day_type_policy().as_dict(),
        }

    # ============================================================
    #  今天
    # ============================================================

    def get_today(self) -> dict[str, Any]:
        today = date.today()
        week = self.store.week_of(today)
        courses = self.store.courses()
        # 模板和策略**成对**取 —— 理由见 store.template_set 的说明
        # （「策略和模板必须同源」，否则会出现按 A 的策略取了 B 的模板）
        templates, policy = self.store.template_set()
        items = engine.moments(today, week, courses, templates, policy)

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
            "dayType": engine.day_type(today, week, courses, policy).value,
            # 用 day_type_display 而不是 DAY_TYPE_LABEL：后者只说「用哪套模板」，
            # 而「今天有没有早八」是日历决定的另一件事 —— 自定义策略下
            # 两者会分家（把工作日全落到 A 型时，「A 型日·有早八」就是假话）。
            "dayTypeLabel": engine.day_type_display(today, week, courses, policy),
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
        templates, policy = self.store.template_set()
        day_type = engine.day_type(tomorrow, week, courses, policy)
        template = templates[day_type]
        items = engine.moments(tomorrow, week, courses, templates, policy)

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
            "dayTypeLabel": engine.day_type_display(tomorrow, week, courses, policy),
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
        _, policy = self.store.template_set()

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
        for i in range(1, WEEK_DAYS + 1):
            dt = monday + timedelta(days=i - 1)
            day_type = engine.day_type(dt, week, courses, policy)
            days.append({
                "dow": i,
                "label": format_spec.WEEKDAY_CN[i].replace("周", ""),
                "dateText": f"{dt.month}/{dt.day}",
                "dayType": day_type.value,
                "shortType": _short_day_type(day_type),
            })

        rows = []
        for start_node, end_node in [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10)]:
            cells = []
            for dow in range(1, WEEK_DAYS + 1):
                hit = next(
                    (
                        c for c in courses
                        if c.enabled and c.day_of_week == dow
                        and c.start_node == start_node and week in c.weeks
                    ),
                    None,
                )
                cells.append(
                    {
                        "dow": dow,
                        "name": hit.name if hit else None,
                        "place": hit.place if hit else None,
                        "weeksText": format_spec.format_weeks(hit.weeks) + " 周" if hit else None,
                        "nodesText": f"第 {start_node}-{end_node} 节" if hit else None,
                    }
                )
            rows.append({
                "startNode": start_node,
                "endNode": end_node,
                "label": f"第 {start_node}-{end_node} 节",
                "startTime": slots.fmt(slots.start(start_node)),
                "endTime": slots.fmt(slots.end(end_node)),
                "cells": cells,
            })

        return {
            "week": week,
            "isCurrent": week == this_week,
            "rangeText": f"{monday.month}.{monday.day} – {sunday.month}.{sunday.day}",
            "days": days,
            "rows": rows,
        }

    # ============================================================
    #  悬浮窗
    # ============================================================

    def ball_state(self) -> dict[str, Any]:
        """
        悬浮窗需要的全部数据。

        **刻意做成一个方法返回所有东西**，而不是让前端连着调好几个 ——
        悬浮窗刷新很频繁（每分钟至少一次），往返次数越少越好。
        而且这样能保证「此刻」「接着」「进度」是同一时刻算出来的，
        不会出现「标题已经是下一节课了，但倒计时还是上一节的」这种撕裂。
        """
        today = date.today()
        week = self.store.week_of(today)
        courses = self.store.courses()
        templates, policy = self.store.template_set()
        items = engine.moments(today, week, courses, templates, policy)

        now = datetime.now()
        minute = now.hour * 60 + now.minute
        current = engine.current_at(items, minute)
        nxt = engine.next_after(items, minute)

        return {
            "ok": True,
            "week": week,
            "dayTypeLabel": engine.day_type_display(today, week, courses, policy),
            "now": _moment_dict(current, minute) if current else None,
            "next": {"time": slots.fmt(nxt.start), "title": nxt.title} if nxt else None,
        }

    def move_ball(self, dx: float, dy: float) -> dict[str, Any]:
        """
        把悬浮窗挪动 (dx, dy) 像素。

        为什么不是「设置窗口位置 (x, y)」？
        因为前端只能拿到鼠标的相对位移（screenX 的差值），拿不到窗口的绝对位置。
        让 Python 累加位移，比前端去猜窗口在哪可靠得多。

        实际的移动和边界限制交给 main.py —— 那边才有窗口尺寸和屏幕信息。
        """
        if self._on_ball_move is not None:
            return self._on_ball_move(dx, dy)

        if self._ball_window is None:
            return {"ok": False}
        try:
            x, y = self._ball_window.x, self._ball_window.y
            self._ball_window.move(int(x + dx), int(y + dy))
        except Exception:
            # 移动失败不该让程序崩 —— 顶多是拖不动
            return {"ok": False}
        return {"ok": True}

    def ball_drag_start(self) -> dict[str, Any]:
        """
        开始拖动了。

        存在的意义只有一个：如果悬浮窗正贴边收起，**先让它弹出来再拖**。
        否则用户拖的是一条 26 像素宽的细缝，很容易以为「拖没了」。
        """
        if self._on_ball_drag_start:
            self._on_ball_drag_start()
        return {"ok": True}

    def ball_drag_end(self) -> dict[str, Any]:
        """
        拖完了。由 main.py 判断要不要吸附到边缘。

        为什么判断放在 Python 而不是 JS？
        因为几何计算是纯数学，放在 Python 里能写单元测试（见 app/dock.py）。
        放在 JS 里就只能靠肉眼看了。
        """
        if self._on_ball_drag_end:
            return self._on_ball_drag_end()
        return {"ok": False}

    def ball_hover(self, entered: bool) -> dict[str, Any]:
        """
        鼠标进入 / 离开悬浮窗。

        网页只能知道「鼠标离开了我的可视区域」，但它不知道这意味着什么 ——
        该不该缩回去，取决于窗口是不是停靠在边上。所以交给 Python 决定。
        """
        if self._on_ball_hover:
            return self._on_ball_hover(bool(entered))
        return {"ok": True}

    def ball_slide_out(self) -> dict[str, Any]:
        """手动把收起的悬浮窗拉出来（不依赖鼠标悬停）"""
        if self._on_ball_slide_out:
            return self._on_ball_slide_out()
        return {"ok": True}

    def show_main(self) -> dict[str, Any]:
        if self._on_show_main:
            self._on_show_main()
        return {"ok": True}

    def hide_ball(self) -> dict[str, Any]:
        """从悬浮窗上直接关掉它。同时把设置也改掉，否则重启又冒出来了。"""
        self.store.ball_enabled = False
        self._notify_settings_changed()
        return {"ok": True, "settings": self._settings_dict()}

    def mark_launched(self) -> dict[str, Any]:
        """
        首次启动向导走完了，写进数据文件，下次不再问。

        ⚠️ 这个方法曾经**不存在** —— app.js 一直在调 `call('mark_launched')`，
        但 `Api` 上从来没有它（只在 `Store` 上，而 `Store` 带着
        `_serializable = False`，被 pywebview 挡在桥外）。
        后果是首次启动走完向导会弹一个「出错了」，而且这个标记永远写不进去。

        这类缺陷运行时只会表现为「点了没反应」或一句含糊的提示，
        所以现在由 tests/test_api_contract.py 在测试期把名字契约钉住。
        """
        self.store.mark_launched()
        return {"ok": True}

    # ============================================================
    #  设置
    # ============================================================

    def set_enabled(self, value: bool) -> dict[str, Any]:
        return self._apply_setting("enabled", bool(value))

    def set_ball_enabled(self, value: bool) -> dict[str, Any]:
        return self._apply_setting("ball_enabled", bool(value))

    def set_remind_lead(self, minutes: int) -> dict[str, Any]:
        return self._apply_setting("remind_lead", int(minutes))

    def set_term(self, name: str, start_iso: str, total_weeks: int) -> dict[str, Any]:
        """
        改学期设置。

        和手机版一样，起始日**自动吸附到周一** —— 整个时间轴是按
        「第 N 周 = 起始日 + (N-1)×7 天」推的，起始日不是周一就全错位。
        JSON 导入时这种情况会报错（用户不在场），
        但界面里用户就在屏幕前，顺手改对比甩个错误更好。
        """
        try:
            picked = date.fromisoformat(start_iso)
        except ValueError:
            return {"ok": False, "message": "日期格式不对，应该是 2026-09-07 这样"}

        monday = engine.monday_of(picked)
        self.store.term_name = name or builtin_data.TERM_NAME
        self.store.term_start = monday
        self.store.total_weeks = int(total_weeks)
        # 改了起始日，之前手动钉死的周次就没意义了
        self.store.week_override = 0
        self._notify_settings_changed()

        msg = "已保存"
        if monday != picked:
            msg = f"已自动对齐到那一周的周一：{monday}"
        return {"ok": True, "message": msg, "term": self._term_dict()}

    def set_week_override(self, value: int) -> dict[str, Any]:
        self.store.week_override = max(0, int(value))
        self._notify_settings_changed()
        return self._term_dict()

    # ============================================================
    #  课程编辑（图形化改 JSON 的核心）
    # ============================================================

    def get_courses(self) -> list[dict[str, Any]]:
        return [_course_dict(c, i) for i, c in enumerate(self.store.courses())]

    def save_course(self, payload: dict[str, Any], index: int = -1) -> dict[str, Any]:
        """
        新增或修改一门课。

        index = -1 表示新增，否则是替换第 index 条。

        这里对输入做了完整校验，**报错文案和 JSON 导入那边保持一致** ——
        用户在图形界面里被拦下，和在 JSON 里被拦下，看到的应该是同一套规则。
        两套规则会让人迷惑，也会让「图形界面比手写宽松」变成 bug 的温床。
        """
        try:
            course = self._course_from_payload(payload)
        except ValueError as e:
            return {"ok": False, "message": str(e)}

        courses = self.store.courses()
        if index < 0:
            courses.append(course)
            msg = f"已添加「{course.name}」"
        else:
            if not _valid_index(index, courses):
                return {"ok": False, "message": "这条课程已经不存在了，列表可能已经刷新"}
            courses[index] = course
            msg = f"已修改「{course.name}」"

        self.store.save_courses(courses)
        return self._courses_ok(msg)

    def delete_course(self, index: int) -> dict[str, Any]:
        courses = self.store.courses()
        if not _valid_index(index, courses):
            return {"ok": False, "message": "这条课程已经不存在了"}
        removed = courses.pop(index)
        self.store.save_courses(courses)
        return self._courses_ok(f"已删除「{removed.name}」")

    def toggle_course(self, index: int, enabled: bool) -> dict[str, Any]:
        courses = self.store.courses()
        if not _valid_index(index, courses):
            return {"ok": False, "message": "这条课程已经不存在了"}
        c = courses[index]
        courses[index] = Course(c.name, c.day_of_week, c.start_node, c.end_node,
                                c.weeks, c.place, bool(enabled), c.id)
        self.store.save_courses(courses)
        return self._courses_ok("")

    def clear_courses(self) -> dict[str, Any]:
        self.store.clear_courses()
        # 清空之后 get_courses() 必然是 []：Store.clear_courses 存的是 "[]"
        # 而不是把键删掉，所以不会触发「懒加载内置课表」那条路
        # （见 store.py 里 clear_courses 的注释）
        return self._courses_ok("已清空课表")

    def reset_courses(self) -> dict[str, Any]:
        self.store.reset_courses()
        return self._courses_ok("已恢复内置课表")

    def _course_from_payload(self, p: dict[str, Any]) -> Course:
        name = str(p.get("name", "")).strip()
        if not name:
            raise ValueError("课程名不能为空")

        try:
            dow = int(p.get("dayOfWeek", 1))
        except (TypeError, ValueError):
            raise ValueError("星期必须是 1–7 的数字") from None
        if not 1 <= dow <= 7:
            raise ValueError(f"星期必须在 1–7 之间（周一=1，周日=7），现在是 {dow}")

        try:
            start_node = int(p.get("startNode", 1))
            end_node = int(p.get("endNode", 1))
        except (TypeError, ValueError):
            raise ValueError("节次必须是数字") from None
        if not (1 <= start_node <= slots.MAX_NODE and 1 <= end_node <= slots.MAX_NODE):
            raise ValueError(f"节次必须在 1–{slots.MAX_NODE} 之间")
        if start_node > end_node:
            raise ValueError("起始节次不能比结束节次大")

        weeks_text = str(p.get("weeks", "")).strip()
        if not weeks_text:
            raise ValueError('周次不能为空，写法如 "2-4,6-17"，或 "*" 表示全学期')
        try:
            weeks = format_spec.parse_weeks(weeks_text, self.store.total_weeks)
        except format_spec.FormatError as e:
            raise ValueError(str(e)) from None

        return Course(
            name=name,
            day_of_week=dow,
            start_node=start_node,
            end_node=end_node,
            weeks=weeks,
            place=str(p.get("place", "")).strip(),
            enabled=bool(p.get("enabled", True)),
        )

    # ============================================================
    #  作息模板编辑
    # ============================================================

    def get_templates(self) -> dict[str, Any]:
        """返回六种日型的格子，并把「哪些是用户改过的」标出来"""
        custom = self.store.custom_templates()
        merged = self.store.templates()
        return {
            "customized": [t.value for t in custom],
            "types": [
                {
                    "key": t.value,
                    "label": DAY_TYPE_LABEL[t],
                    "isCustom": t in custom,
                    "wake": (
                        slots.fmt(w) if (w := engine.wake_minute(merged[t])) is not None else None
                    ),
                    "blocks": [_block_dict(b) for b in merged[t]],
                }
                for t in DAY_TYPE_ORDER
            ],
        }

    def save_template_block(self, day_type: str, payload: dict[str, Any],
                            index: int = -1) -> dict[str, Any]:
        day_type_enum = _parse_day_type(day_type)
        if day_type_enum is None:
            return {"ok": False, "message": f"未知的日型：{day_type}"}

        try:
            block = self._block_from_payload(payload)
        except ValueError as e:
            return {"ok": False, "message": str(e)}

        # 从「当前的完整六套」出发做修改，再整份存成自定义模板。
        # 这样即使用户只改了一格，存下来的也是完整的一套 ——
        # 避免「改了一格，其余几格悄悄回落到内置」这种难以察觉的行为。
        full = self.store.templates()
        blocks = list(full[day_type_enum])

        if index < 0:
            blocks.append(block)
        elif _valid_index(index, blocks):
            blocks[index] = block
        else:
            return {"ok": False, "message": "这一格已经不存在了，列表可能已经刷新"}

        blocks.sort(key=lambda b: b.start)

        # 重叠必须拦下来：引擎遇到重叠会「先到先得」把后一格静默截断，
        # 用户写的某一格就这么没了，界面上还看不出来。
        for prev, cur in zip(blocks, blocks[1:]):
            if cur.start < prev.end:
                return {
                    "ok": False,
                    "message": (
                        f"这两格时间重叠了：\n\n"
                        f"　{slots.fmt(prev.start)}–{slots.fmt(prev.end)}　{prev.title}\n"
                        f"　{slots.fmt(cur.start)}–{slots.fmt(cur.end)}　{cur.title}\n\n"
                        f"同一时刻只能有一件固定的事。\n"
                        f"如果你的本意是改起床时间，注意「睡觉」和后面的「起床、洗漱」"
                        f"是两格，两格都要改。"
                    ),
                }

        full[day_type_enum] = blocks
        self.store.save_templates(full)
        return self._templates_ok("已保存")

    def delete_template_block(self, day_type: str, index: int) -> dict[str, Any]:
        day_type_enum = _parse_day_type(day_type)
        if day_type_enum is None:
            return {"ok": False, "message": f"未知的日型：{day_type}"}

        full = self.store.templates()
        blocks = list(full[day_type_enum])
        if not _valid_index(index, blocks):
            return {"ok": False, "message": "这一格已经不存在了"}
        if len(blocks) <= 1:
            return {"ok": False, "message": "至少要留一格，否则这一天就没有作息了"}

        removed = blocks.pop(index)
        full[day_type_enum] = blocks
        self.store.save_templates(full)
        return self._templates_ok(f"已删除「{removed.title}」")

    def shift_wake_time(self, day_type: str, new_time: str) -> dict[str, Any]:
        """
        改起床时间 —— 一个动作改两格。

        ## 为什么需要这个专门的方法

        模板里「睡觉」和「起床、洗漱」是**两格**：
            睡觉        00:00–06:55
            起床、洗漱   06:55–07:10

        想把起床时间推到 07:30，就得同时改两格。但一格一格改是走不通的：

            改「睡觉」→ 报错：和「起床、洗漱」重叠
            改「起床、洗漱」→ 报错：和「睡觉」重叠

        **用户会陷入死锁** —— 每一步都被拦下，而每一句报错听起来都很合理。

        手机版的解法是「文档里写一句：两处都要改」。那在只有 JSON 的时代勉强能用，
        但既然做了图形界面，就该让用户点一下搞定，而不是记住一条规则。

        这也是做界面时值得记住的一点：
        **凡是「用户必须按特定顺序做几件事」的地方，都值得做成一个动作。**
        """
        day_type_enum = _parse_day_type(day_type)
        if day_type_enum is None:
            return {"ok": False, "message": f"未知的日型：{day_type}"}

        target = slots.parse_hhmm(new_time.strip(), is_end=False)
        if target is None:
            return {"ok": False, "message": f'时间 "{new_time}" 格式不对，应该是 "07:30" 这样'}

        full = self.store.templates()
        blocks = sorted(full[day_type_enum], key=lambda b: b.start)

        # 找出「午前最后一个睡觉块」—— 和 engine.wake_minute() 用的是同一条规则，
        # 保证「界面显示的起床时间」和「实际改的那一格」永远是同一个
        sleep_idx = -1
        best_end = -1
        for i, b in enumerate(blocks):
            if b.kind == Kind.SLEEP and b.start < engine.NOON and b.end > best_end:
                best_end = b.end
                sleep_idx = i

        if sleep_idx < 0:
            return {"ok": False, "message": "这套模板里没有上午的睡眠段，没法改起床时间"}

        old = blocks[sleep_idx].end
        if target == old:
            return {"ok": True, "message": "起床时间没有变化",
                    "templates": self.get_templates()}

        delta = target - old

        # ------------------------------------------------------------
        #  只挪「起床 → 第一个锚点」之间那几格。
        #
        #  ## 为什么不能整段顺延
        #
        #  一开始我写的是「起床之后全部往后推」。跑测试才发现行不通：
        #  模板是**一整天的完整分区**（00:00 到 24:00 排得满满的），
        #  往后推必然把最后那格挤出 24:00。
        #  往前推虽然不溢出，但末尾会留一截空白。
        #
        #  ## 锚点是什么
        #
        #  锚点 = **课表格子**（带 nodes 的那种）。它的时间是由学校课表决定的，
        #  08:30 上课就是 08:30，不能因为你起晚了就延后。
        #  这是整个作息里唯一真正「钉死」的东西。
        #
        #  所以正确的做法是：起床到第一个课表格子之间的事情（洗漱、早餐、早读）
        #  跟着起床时间挪，撞到锚点就说明塞不下，如实报错。
        #
        #  周末模板里没有课表格子，就退而用「晚上最后一觉」当锚点。
        # ------------------------------------------------------------
        anchor_idx = None
        for i in range(sleep_idx + 1, len(blocks)):
            if blocks[i].nodes is not None:
                anchor_idx = i
                break
        if anchor_idx is None:
            for i in range(sleep_idx + 1, len(blocks)):
                if blocks[i].kind == Kind.SLEEP:
                    anchor_idx = i
                    break
        if anchor_idx is None:
            return {
                "ok": False,
                "message": "这套模板里找不到「不能挪动的锚点」（课表格子或晚上的睡眠），没法安全地顺延。",
            }

        updated = list(blocks)
        updated[sleep_idx] = updated[sleep_idx].copy(end=target)

        for i in range(sleep_idx + 1, anchor_idx):
            b = updated[i]
            updated[i] = b.copy(start=b.start + delta, end=b.end + delta)

        # ---- 校验 ----
        #
        #  注意检查的**顺序**：先报「塞不下锚点」，再报「跑到昨天」。
        #
        #  因为整段是**等量平移**的，段内各格的相对关系没变，
        #  真正会出问题的只有两件事：撞上锚点、或者挪到 00:00 之前。
        #  而「撞上锚点」是用户最可能遇到、也最需要解释清楚的那种 ——
        #  先报它，用户才知道该删早读还是该改时间。
        #  反过来的话，用户会先看到一句「格子重叠了」，一头雾水。
        anchor = updated[anchor_idx]

        if anchor_idx > sleep_idx + 1 and updated[anchor_idx - 1].end > anchor.start:
            room = anchor.start - updated[sleep_idx].end
            need = sum(updated[i].duration for i in range(sleep_idx + 1, anchor_idx))
            why = "它是课表格子，跟着课表走，不能挪" if anchor.nodes else "它是固定的睡眠时段"
            return {
                "ok": False,
                "message": (
                    f"起床到「{anchor.title}」之间塞不下。\n\n"
                    f"「{anchor.title}」固定在 {slots.fmt(anchor.start)} 开始（{why}），\n"
                    f"从 {slots.fmt(updated[sleep_idx].end)} 起床算起只有 {room} 分钟，\n"
                    f"而中间这几格加起来要 {need} 分钟。\n\n"
                    f"如果本意是「起晚一点、不早读了」，请先到列表里删掉早读那一格，再改起床时间。"
                ),
            }

        for i in range(sleep_idx, anchor_idx):
            if updated[i].start < 0:
                return {
                    "ok": False,
                    "message": f"这样改会让「{updated[i].title}」跑到昨天去，换个时间试试。",
                }

        full[day_type_enum] = updated
        self.store.save_templates(full)
        self._notify_settings_changed()

        moved = anchor_idx - sleep_idx - 1
        direction = "推迟" if delta > 0 else "提前"
        return self._templates_ok(
            f"起床时间已{direction}到 {slots.fmt(target)}"
            + (f"，后面 {moved} 格跟着挪" if moved else "")
        )

    def reset_templates(self) -> dict[str, Any]:
        self.store.reset_templates()
        return self._templates_ok("已恢复内置作息模板")

    # ============================================================
    #  日型策略（v3）
    # ============================================================

    def get_day_types(self) -> dict[str, Any]:
        """
        日型策略的现状，给设置页那几个复选框用。

        返回里带上**每个日型的起床时间** —— 界面要在选项旁边显示它。
        没有这个信息的话，用户勾选时看不出代价：
        「把工作日全勾成 A 型」意味着没早八的日子也 06:55 起床，
        而那正是这个程序最该帮人避免的事。
        """
        policy = self.store.day_type_policy()
        templates = self.store.templates()

        types = []
        for t in DAY_TYPE_ORDER:
            w = engine.wake_minute(templates[t])
            types.append({
                "key": t.value,
                "label": DAY_TYPE_LABEL[t],
                "wake": slots.fmt(w) if w is not None else None,
                "enabled": t in policy.enabled,
                "isFallback": t is policy.fallback,
            })

        return {
            "types": types,
            "fallback": policy.fallback.value,
            "hasCustomDayTypes": self.store.has_custom_day_types,
        }

    def save_day_types(self, enabled: list, fallback: str) -> dict[str, Any]:
        """
        保存勾选结果。

        ## 两道校验，都是为了挡住「看不出来但很糟糕」的配置

        **① `enabled` 不能为空。** 那样每天都会落到 fallback 上，
        等于把整套日型系统废掉 —— 几乎不可能是本意，多半是手滑全取消了。
        格式标准里这一条也是**报错**而不是静默接受。

        **② `fallback` 必须在 `enabled` 里。**

        ⚠️ 这一条**和格式标准相反**，是刻意的：

            文件里      fallback 允许不在 enabled 里（那是正当用法，
                        "只启用 A 和周末，但周中没早八时回落成 B 型"）
            界面上      这里只能从勾选项里选，因为这是一个下拉框 ——
                        让用户在下拉里选一个**没勾选**的日型，
                        等于给了一个自相矛盾的控件

        界面的约束比文件的约束**更紧**：文件要容纳所有合法写法，
        而界面应该只呈现能自洽的组合。用户真需要那种配置，
        导入一份文件即可，那个入口一直开着。
        """
        # 先做校验，再把 key 转成枚举
        chosen: list[DayType] = []
        for raw in enabled or []:
            t = _parse_day_type(str(raw))
            if t is None:
                return {"ok": False, "message": f"未知的日型：{raw}"}
            if t not in chosen:
                chosen.append(t)

        if not chosen:
            return {
                "ok": False,
                "message": "至少要启用一种日型。\n"
                           "全都取消的话，每一天都会落到「回落到」那一种上，"
                           "等于这个设置没有意义。",
            }

        fb = _parse_day_type(str(fallback))
        if fb is None:
            return {"ok": False, "message": f"未知的回落日型：{fallback}"}
        if fb not in chosen:
            return {
                "ok": False,
                "message": f"「回落到」选的是 {DAY_TYPE_LABEL[fb]}，但它没有被勾选。\n"
                           "回落的含义是「算出来的日型没启用时，改用哪一种」——"
                           "所以它自己得是启用的。\n"
                           "（如果你确实需要「启用 A 和周末、却回落到 B 型」这种配置，"
                           "可以导入一份带 dayTypes 段的文件，文件里允许这样写。）",
            }

        self.store.save_day_type_policy(
            DayTypePolicy(enabled=frozenset(chosen), fallback=fb)
        )
        # 必须通知：提醒线程要按新的日型重排 —— 起床时间一改，
        # 「下一个切换时刻」就变了。漏掉这一步不会报错，只是闹钟停在旧时间上。
        self._notify_settings_changed()
        return self._day_types_ok(f"已启用 {len(chosen)} 种日型")

    def reset_day_types(self) -> dict[str, Any]:
        """恢复默认策略（A + 没早八的 B + 周末）"""
        self.store.reset_day_type_policy()
        self._notify_settings_changed()
        return self._day_types_ok("已恢复默认日型")

    def _day_types_ok(self, message: str) -> dict[str, Any]:
        """
        保存成功后的统一回包。

        和 `_templates_ok` 一样，**先通知再取数据** —— 反过来的话
        回给前端的可能不是通知之后的状态。
        """
        return {
            "ok": True,
            "message": message,
            "dayTypes": self.get_day_types(),
            "settings": self._settings_dict(),
        }

    def _block_from_payload(self, p: dict[str, Any]) -> Block:
        title = str(p.get("title", "")).strip()
        if not title:
            raise ValueError("这一格要显示什么？不能为空")

        start = slots.parse_hhmm(str(p.get("start", "")).strip(), is_end=False)
        if start is None:
            raise ValueError('开始时刻格式不对，应该是 "06:55" 这样')

        end = slots.parse_hhmm(str(p.get("end", "")).strip(), is_end=True)
        if end is None:
            raise ValueError('结束时刻格式不对，应该是 "07:10" 这样（一天最后一段可以写 "24:00"）')

        if end <= start:
            raise ValueError("结束时刻必须晚于开始时刻")

        try:
            kind = Kind(str(p.get("kind", "CHORE")).upper())
        except ValueError:
            raise ValueError(f"时段性质无法识别：{p.get('kind')}") from None

        nodes = None
        raw_nodes = p.get("nodes")
        if raw_nodes:
            try:
                a, b = int(raw_nodes[0]), int(raw_nodes[1])
            except (TypeError, ValueError, IndexError):
                raise ValueError("课表占位格必须写成一到十之间的两个节次") from None
            if not (1 <= a <= slots.MAX_NODE and 1 <= b <= slots.MAX_NODE):
                raise ValueError(f"节次必须在 1–{slots.MAX_NODE} 之间")
            if a > b:
                raise ValueError("起始节次不能比结束节次大")
            nodes = (a, b)

        return Block(start=start, end=end, title=title,
                     note=str(p.get("note", "")).strip(), kind=kind, nodes=nodes)

    # ============================================================
    #  导入导出
    # ============================================================

    def import_from_file(self) -> dict[str, Any]:
        """
        选文件 → 解析 → 返回预览。

        注意这里**只解析不套用** —— 真正的写入在 confirm_import 里。
        导入是要覆盖用户数据的，必须先让他看见将要发生什么。
        """
        path = self._ask_open_file()
        if not path:
            return {"ok": False, "cancelled": True}

        try:
            text = Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = Path(path).read_text(encoding="gbk")
            except (UnicodeDecodeError, OSError) as e:
                return {"ok": False, "message": f"读不出文件内容：{e}"}
        except OSError as e:
            return {"ok": False, "message": f"打不开文件：{e}"}

        try:
            parsed = format_spec.parse(text, self.store.total_weeks)
        except format_spec.FormatError as e:
            return {"ok": False, "message": str(e)}

        # ⚠️ 这里**只能**返回能被 JSON 序列化的东西。
        #
        # 曾经这里多带了一个 `"_parsed": parsed`（Parsed 是 dataclass 实例）。
        # 后果是**导入功能完全不能用**：pywebview 会把 js_api 的返回值
        # json.dumps 之后才发给前端（见 webview/util.py 里 _call 函数），
        # 序列化失败会被它捕获成 `{isError: true}`，用户看到的是
        # 「导入失败 TypeError: Object of type Parsed is not JSON serializable」。
        #
        # 这个键还是完全多余的：解析结果已经存在 self._pending_import 里
        # （由 _preview_dict 顺手存下），前端不需要拿到它 —— 那正是
        # _pending_import 存在的理由（见它的注释：别让前端来回搬数据）。
        #
        # **规矩：桥接方法的返回值必须能 json.dumps。** 传对象图过去是行不通的，
        # 要么转成 dict，要么像这里一样根本别传。
        # tests/test_api_serializable.py 把这条钉住了。
        return {"ok": True, "preview": self._preview_dict(parsed)}

    def confirm_import(self, mode: str) -> dict[str, Any]:
        """套用上一次解析的结果。mode: 'replace' | 'merge'"""
        parsed = self._pending_import
        if parsed is None:
            return {"ok": False, "message": "没有待导入的数据，请重新选择文件"}
        self._pending_import = None

        message = self.store.apply_import(parsed, mode)
        self._notify_settings_changed()
        return {"ok": True, "message": message}

    def _preview_dict(self, parsed: format_spec.Parsed) -> dict[str, Any]:
        self._pending_import = parsed

        term = parsed.term
        template_lines = []
        if parsed.templates:
            for t in DAY_TYPE_ORDER:
                if t in parsed.templates:
                    wake = engine.wake_minute(parsed.templates[t])
                    template_lines.append({
                        "key": t.value,
                        "wake": slots.fmt(wake) if wake is not None else None,
                    })

        # 启用了哪些日型（v3）。标题里带上起床时间 —— 这是用户最想核对的
        # 那一件事（「我说好 7 点起，到底给我排的几点」）。
        day_type_lines = []
        if parsed.day_types is not None:
            for t in DAY_TYPE_ORDER:
                if t in parsed.day_types.enabled:
                    # 起床时间要从**这一份文件里的模板**推，没有就用内置的
                    blocks = (parsed.templates or {}).get(t)
                    if blocks is None:
                        blocks = self.store.templates().get(t, [])
                    wake = engine.wake_minute(blocks)
                    day_type_lines.append({
                        "key": t.value,
                        "wake": slots.fmt(wake) if wake is not None else None,
                    })

        return {
            "hasTerm": term is not None,
            "termName": term.name if term else None,
            "termStart": term.start_date.isoformat() if term else None,
            "totalWeeks": term.total_weeks if term else None,
            "courseCount": len(parsed.courses) if parsed.courses is not None else None,
            "coursesUnchanged": parsed.courses is None,
            "templateCount": len(parsed.templates) if parsed.templates else 0,
            "templatesUnchanged": parsed.templates is None,
            "templateLines": template_lines,
            "dayTypesUnchanged": parsed.day_types is None,
            "dayTypeEnabled": (
                [
                    {"key": t.value, "fallback": parsed.day_types.fallback is t}
                    for t in DAY_TYPE_ORDER
                    if t in parsed.day_types.enabled
                ]
                if parsed.day_types is not None
                else []
            ),
            "dayTypeFallback": (
                parsed.day_types.fallback.value
                if parsed.day_types is not None
                else None
            ),
            "dayTypeLines": day_type_lines,
            "warnings": parsed.warnings,
        }

    def export_to_file(self, include_schedule_config: bool) -> dict[str, Any]:
        """
        ⚠️ 参数名跟着手机版从 `include_templates` 改成了 `include_schedule_config`。

        它现在同时管**模板和日型策略**两样东西，还叫原名就是「名字在说谎」——
        而一个名字和实际行为不符的参数，早晚会有人按名字去理解它。
        （前端 app.js 里的调用点要同步改，否则会 TypeError。见
        tests/test_api_contract.py：它扫前端所有 call('x') 的实参个数。）
        """
        text = self.store.export_json(bool(include_schedule_config))
        default_name = "课表.json" if not include_schedule_config else "完整备份.json"
        path = self._ask_save_file(default_name)
        if not path:
            return {"ok": False, "cancelled": True}
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as e:
            return {"ok": False, "message": f"写文件失败：{e}"}

        count = len(self.store.courses())
        extra = " + 作息配置（模板与启用日型）" if include_schedule_config else ""
        return {"ok": True, "message": f"已导出 {count} 门课{extra}", "path": str(path)}

    def copy_json(self, include_schedule_config: bool) -> dict[str, Any]:
        text = self.store.export_json(bool(include_schedule_config))
        return {"ok": True, "text": text, "message": f"已复制（{len(text)} 字）"}

    def get_ai_prompt(self, kind: str) -> dict[str, Any]:
        """
        AI 提示词。

        提示词里最要紧的三个值是学期名称、第 1 周周一日期、总周数 ——
        它们决定整张表的时间对不对。让 AI 去猜的话它大概率会编一个，
        而错一天的后果是每一周的课都错位。
        所以这里从 Store 读出真实值填进去，用户复制到的天然是正确的。
        """
        if kind == "template":
            text = AiPrompt.for_templates(self.store.term_name)
        elif kind == "fix":
            text = AiPrompt.for_fix(
                "（把程序导入时弹出的报错原文粘在这里）",
                "（把 AI 上次生成的 JSON 粘在这里）",
            )
        else:
            text = AiPrompt.for_courses(
                self.store.term_name,
                self.store.term_start.isoformat(),
                self.store.total_weeks,
            )
        return {"ok": True, "text": text}

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

    def _ask_open_file(self) -> Optional[str]:
        if self._window is None:
            return None
        import webview
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("课表 JSON (*.json)", "所有文件 (*.*)"),
        )
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    def _ask_save_file(self, default_name: str) -> Optional[str]:
        if self._window is None:
            return None
        import webview
        result = self._window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=default_name,
            file_types=("JSON 文件 (*.json)",),
        )
        if not result:
            return None
        return result if isinstance(result, str) else result[0]


def _short_day_type(t: DayType) -> str:
    return {
        DayType.A: "早八",
        DayType.B_TRAIN_A: "训A",
        DayType.B_TRAIN_B: "训B",
        DayType.B_NORMAL: "无早八",
        DayType.SATURDAY: "周六",
        DayType.SUNDAY: "周日",
    }[t]
