"""
悬浮窗桥接 —— 前端 `ball.js` 调的那几个方法，以及首次启动向导的落点。

（由 `tools/split_api.py` 从 `api.py` 原样切出，**一字未改**。）
"""

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


class BallMixin:
    """悬浮窗桥接 —— 前端 `ball.js` 调的那几个方法，以及首次启动向导的落点。"""


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
