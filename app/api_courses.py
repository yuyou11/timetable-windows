"""
课程增删改 —— 课程编辑页。

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


class CoursesMixin:
    """课程增删改 —— 课程编辑页。"""


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
