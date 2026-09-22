"""
载荷转换 —— 内部数据结构 → 前端要的 dict。

## 为什么单独一个模块

这七个是**纯函数**：吃一个 Moment / Course / Block，吐一个 dict，不碰 store、
不碰窗口、不碰任何外部状态。原先混在 `api.py` 里，和「桥接编排」（哪些回调
要接、出错怎么回给前端）挤在一起，结果 api.py 长到 1300 行 —— 要找一个字段
叫什么名字，得先在四十多个方法里翻。

纯函数搬出来的代价是零：没有状态要跟着走，签名一字没改，
`api.py` 里 import 回去照常用（下面那些调用点一处没动）。

⚠️ **字段名是前端契约**，改这里要同步改 `app/web/` 那边的取值。
目前盯这件事的测试不够：`tests/test_api_serializable.py` 只保证返回值能
`json.dumps`，`tests/test_store.py` 的 TestFrozenJsonKeys 只盯本地存储的键名，
**前端取的字段名没有测试盯着** —— 所以改名要自己去 `app/web/` 里 grep 一遍。
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from . import format_spec, slots
from .models import Block, Course, DayType, Moment


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


def _short_day_type(t: DayType) -> str:
    return {
        DayType.A: "早八",
        DayType.B_TRAIN_A: "训A",
        DayType.B_TRAIN_B: "训B",
        DayType.B_NORMAL: "无早八",
        DayType.SATURDAY: "周六",
        DayType.SUNDAY: "周日",
    }[t]
