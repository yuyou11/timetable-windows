"""
《时间规划表》数据格式标准 v2 —— 解析与生成。

这份文件是 Android 版 `ScheduleFormat.kt` 的 Python 移植。
**两边的行为必须完全一致**，否则手机上导出的文件到电脑上打不开（或者更糟：
打得开但读错了），那就失去意义了。

## 设计这条标准的四条原则（和 Kotlin 那边一字不差）

1. **给人写的部分要宽容，给机器读的部分要严格。**
   用户手写 `"weeks": "2-4，6-17"`（中文逗号）应该能work，
   但 `"dayOfWeek": 9` 必须报错而不是猜。

2. **报错要能直接照着改。**
   startDate 不是周一的时候，把**正确的日期**算出来告诉用户。
   报错的价值不在于「告诉用户错了」，而在于「告诉用户改成什么」。

3. **未知字段一律忽略。**
   这样将来标准加字段，老版本程序拿到新文件不会崩，只会少读一个字段。
   这是让格式能演进的唯一办法。

4. **版本号只增不减。**
   碰到比自己新的版本要**明确拒绝**，而不是硬着头皮解析 ——
   硬解析的后果是数据被静默截断，用户以为导入成功了，其实丢了一半。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from . import slots
from .day_type_policy import DayTypePolicy
from .models import DAY_TYPE_ORDER, Block, Course, DayType, Kind

FORMAT_ID = "timetable"

#: 当前支持的格式版本。
#:
#: v1 → v2 新增了可选的 `templates` 段。
#: v2 → v3 新增了可选的 `dayTypes` 段（启用哪些日型）。
#:
#: 两次都是**只增不改**，没有动过任何已有字段，所以老文件照常能用 ——
#: 这就是「只增不改」原则的价值：升级格式不需要任何数据迁移。
#:
#: ## 既然只是「新增一段」，为什么还要升版本号
#:
#: 因为**老版本程序会误解新文件，而且不会报错**。
#:
#: v2 的程序不认识 `dayTypes`，它会照旧用六种日型跑 ——
#: 用户明明写了「只启用 A 和周末」，装回老版本却会看到训练日又冒出来。
#: 这种「悄悄用错」正是版本号要防的东西。
#:
#: 升到 v3 之后，老版本会明确拒绝：
#: 「文件是 v3 格式，这个程序只认到 v2，请更新后再导入」——
#: **拒绝比误解好**，这一条在任何数据交换的场合都成立。
VERSION = 3

MAX_NODE = slots.MAX_NODE
MAX_WEEK_LIMIT = 30
DEFAULT_TOTAL_WEEKS = 19

WEEKDAY_CN = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}

_DAY_NAMES = {
    "周一": 1, "星期一": 1, "礼拜一": 1,
    "周二": 2, "星期二": 2,
    "周三": 3, "星期三": 3,
    "周四": 4, "星期四": 4,
    "周五": 5, "星期五": 5,
    "周六": 6, "星期六": 6,
    "周日": 7, "周天": 7, "星期日": 7, "星期天": 7,
}

#: 日型的 JSON 键名 -> 枚举。导出和导入都靠它，保证两边一致。
_DAY_TYPE_NAMES = {t.value: t for t in DAY_TYPE_ORDER}

#: 时段性质的名字 -> 枚举。大小写不敏感（解析时统一转大写再查）。
_KIND_NAMES = {k.value: k for k in Kind}


class FormatError(Exception):
    """格式错误。message 会原样显示给用户，所以必须写得能照着改。"""


# ============================================================
#  一、周次写法
# ============================================================

def parse_weeks(raw: str, total_weeks: int) -> frozenset[int]:
    """
    把周次字符串解析成集合。

    支持的写法：
        "3"          第 3 周
        "2-4"        第 2 到 4 周
        "1-17/2"     1、3、5…17 周（单周）
        "2-16/2"     2、4、6…16 周（双周）
        "2-4,6-17"   分段
        "*"          全部周次

    容错：中文逗号「，」、全角减号、波浪号、破折号都会先规范化掉。
    用户手写时几乎一定会打出这些，不该成为导入失败的理由。
    """
    s = _normalize_weeks(raw)
    if not s:
        raise FormatError("weeks 不能为空")

    if s == "*":
        return frozenset(range(1, total_weeks + 1))

    out: set[int] = set()

    for segment in s.split(","):
        if not segment:
            raise FormatError(f'weeks 里有多余的逗号（"{raw}"）')

        range_part, sep, step_part = segment.partition("/")

        if sep:
            if not step_part:
                raise FormatError('weeks 的 "/" 后面缺少步长，正确写法如 "1-17/2"')
            if not step_part.isdigit():
                raise FormatError(f'weeks 的步长 "{step_part}" 不是数字，正确写法如 "1-17/2"')
            step = int(step_part)
        else:
            step = 1

        if step < 1:
            raise FormatError(f"weeks 的步长必须大于 0，现在是 {step}")

        dash = range_part.find("-")
        if dash < 0:
            if not range_part.isdigit():
                raise FormatError(f'weeks 里的 "{range_part}" 既不是周次也不是区间')
            first = last = int(range_part)
        else:
            a, b = range_part[:dash], range_part[dash + 1:]
            if not a.isdigit():
                raise FormatError(f'weeks 区间的起点 "{a}" 不是数字')
            if not b.isdigit():
                raise FormatError(f'weeks 区间的终点 "{b}" 不是数字')
            first, last = int(a), int(b)

        if first > last:
            raise FormatError(f'weeks 区间 "{range_part}" 起点比终点大')
        if first < 1 or last > total_weeks:
            raise FormatError(
                f'weeks 的周次必须在 1–{total_weeks} 之间，"{range_part}" 超出范围'
            )

        out.update(range(first, last + 1, step))

    if not out:
        raise FormatError("weeks 没有解析出任何周次")
    return frozenset(out)


def _normalize_weeks(raw: str) -> str:
    """把各种中文标点统一成 ASCII，再去掉所有空白"""
    table = {
        "，": ",", "～": "-", "~": "-",
        "—": "-", "–": "-", "－": "-",
    }
    out = raw
    for src, dst in table.items():
        out = out.replace(src, dst)
    return re.sub(r"\s+", "", out)


def format_weeks(weeks: frozenset[int] | set[int]) -> str:
    """
    反向：把集合压成最短的字符串。

        {3,5,7,9,11,13,15,17} -> "3-17/2"   认出等差数列，优先用步长写法
        {2,3,4,6,...,17}      -> "2-4,6-17" 不是等差，退回分段

    优先输出步长写法，是因为「单周 / 双周」在课表语境下比一长串数字更容易核对 ——
    人一眼就能看出对不对。
    """
    if not weeks:
        return ""

    s = sorted(weeks)
    if len(s) == 1:
        return str(s[0])

    # 等差数列且步长大于 1 -> 压成 "起点-终点/步长"
    if len(s) >= 3:
        step = s[1] - s[0]
        if step > 1 and all(b - a == step for a, b in zip(s, s[1:])):
            return f"{s[0]}-{s[-1]}/{step}"

    # 否则合并连续区间
    parts: list[str] = []
    first = prev = s[0]
    for w in s[1:]:
        if w == prev + 1:
            prev = w
        else:
            parts.append(str(first) if first == prev else f"{first}-{prev}")
            first = prev = w
    parts.append(str(first) if first == prev else f"{first}-{prev}")
    return ",".join(parts)


# ============================================================
#  二、解析
# ============================================================

@dataclass
class Term:
    name: str
    start_date: date
    total_weeks: int


@dataclass
class Parsed:
    term: Optional[Term]
    #: None 表示「不要动现有课程」，区别于空列表「清空课程」
    courses: Optional[list[Course]]
    #: None 表示「不要动现有模板」
    templates: Optional[dict[DayType, list[Block]]]
    #: None 表示「不要动现有日型策略」（v3 新增）
    #:
    #: 和 courses 一样用「None = 不动」而不是「None = 用默认」——
    #: 否则用户每次导入一份只改课表的文件，作息策略都会被打回默认值。
    day_types: Optional[DayTypePolicy] = None
    warnings: list[str] = field(default_factory=list)


def parse(text: str, fallback_total_weeks: int = DEFAULT_TOTAL_WEEKS) -> Parsed:
    """
    解析一份文件。失败时抛 FormatError，message 直接给用户看。

    不用「返回 None」而是抛异常：调用方必须显式处理失败分支，
    编译器（这里是阅读代码的人）能一眼看出哪里可能出错。
    """
    # 容错：用户从聊天软件复制的文本里常混进 BOM 或不换行空格
    cleaned = text.lstrip("﻿").replace(" ", " ").strip()
    # 容错：AI 经常用 ```json 包起来，用户直接整段粘进文件
    cleaned = _strip_code_fence(cleaned)

    try:
        root = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise FormatError(f"不是合法的 JSON：第 {e.lineno} 行第 {e.colno} 列 —— {e.msg}") from None

    if not isinstance(root, dict):
        raise FormatError("文件的顶层必须是一个 JSON 对象（以 { 开头、以 } 结尾）")

    fmt = str(root.get("format", ""))
    if fmt != FORMAT_ID:
        raise FormatError(
            f'这不是本程序的课表文件。\nformat 字段应该是 "{FORMAT_ID}"，实际读到的是 "{fmt}"。'
        )

    version = root.get("version", 0)
    if not isinstance(version, int) or version < 1:
        raise FormatError("缺少 version 字段，或者它不是整数。无法确认文件格式版本。")
    if version > VERSION:
        raise FormatError(
            f"文件是 v{version} 格式，这个程序只认到 v{VERSION}。\n"
            f"请更新程序后再导入 —— 强行导入会丢掉新格式里多出来的内容。"
        )

    # ---- term 段 ----
    total_weeks = fallback_total_weeks
    term: Optional[Term] = None
    term_obj = root.get("term")
    if isinstance(term_obj, dict):
        term, total_weeks = _parse_term(term_obj)

    # ---- templates 段 ----
    warnings: list[str] = []
    templates: Optional[dict[DayType, list[Block]]] = None
    tmpl_obj = root.get("templates")
    if isinstance(tmpl_obj, dict):
        templates = _parse_templates(tmpl_obj, warnings)
    elif tmpl_obj is not None:
        raise FormatError("templates 必须是一个对象（用 { } 包起来）")

    # ---- dayTypes 段（可选，v3）----
    day_types: Optional[DayTypePolicy] = None
    dt_obj = root.get("dayTypes")
    if isinstance(dt_obj, dict):
        day_types = _parse_day_types(dt_obj)
    elif dt_obj is not None:
        raise FormatError("dayTypes 必须是一个对象（用 { } 包起来）")

    # ---- courses 段 ----
    # 允许缺失或为空，结果是 None，含义是「不要动现有课程」。
    # 为什么允许？因为「我只想改作息，课表别动」是完全正当的需求。
    courses: Optional[list[Course]] = None
    raw_courses = root.get("courses")
    if raw_courses is not None and not isinstance(raw_courses, list):
        raise FormatError("courses 必须是一个数组（用 [ ] 包起来）")
    if raw_courses:
        parsed: list[Course] = []
        for i, item in enumerate(raw_courses):
            if not isinstance(item, dict):
                raise FormatError(f"courses 第 {i + 1} 项不是对象。")
            parsed.append(_parse_course(item, i, total_weeks))
        courses = parsed
        warnings.extend(_detect_conflicts(parsed))

    # ---- 兜底：这份文件至少得说点什么 ----
    #
    # 四段全空的文件导入它没有任何意义，而且多半意味着用户选错了文件、
    # 或者 AI 输出的东西是坏的 —— 明确报错比「导入成功但什么都没变」好。
    if term is None and courses is None and templates is None and day_types is None:
        raise FormatError(
            "这份文件里 term、courses、templates、dayTypes 四段都没有内容，"
            "没有可导入的东西。\n\n"
            "如果你是想导入课表，检查一下 courses 数组是不是空的。"
        )

    return Parsed(term, courses, templates, day_types, warnings)


def _strip_code_fence(text: str) -> str:
    """
    去掉 AI 输出的 ```json ... ``` 包裹。

    这不是「纵容不规范」—— 而是**用户的真实工作流里必然会出现的东西**。
    提示词里已经写了「不要用代码块」，但 AI 十次里有两次还是会加。
    与其让用户去手工删那两行，不如这里顺手处理掉。
    """
    m = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n?```\s*$", text, re.DOTALL)
    return m.group(1).strip() if m else text


def _parse_term(obj: dict[str, Any]) -> tuple[Term, int]:
    total_weeks = obj.get("totalWeeks", DEFAULT_TOTAL_WEEKS)
    if not isinstance(total_weeks, int) or not (1 <= total_weeks <= MAX_WEEK_LIMIT):
        raise FormatError(
            f"term.totalWeeks 必须在 1–{MAX_WEEK_LIMIT} 之间，现在是 {total_weeks}。"
        )

    start_raw = str(obj.get("startDate", "")).strip()
    if not start_raw:
        raise FormatError('term.startDate 不能为空，正确写法如 "2026-09-07"。')
    try:
        start = date.fromisoformat(start_raw)
    except ValueError:
        raise FormatError(
            f'term.startDate "{start_raw}" 不是合法日期，正确写法如 "2026-09-07"。'
        ) from None

    if start.isoweekday() != 1:
        from .engine import monday_of
        raise FormatError(
            "term.startDate 必须是周一。\n"
            f"{start_raw} 是{WEEKDAY_CN[start.isoweekday()]}，"
            f"应该填 {monday_of(start)}。"
        )

    name = str(obj.get("name", "")).strip() or "未命名学期"
    return Term(name, start, total_weeks), total_weeks


def _parse_course(obj: dict[str, Any], index: int, total_weeks: int) -> Course:
    """报错时带上课程名 —— 否则用户面对 19 条数据不知道是哪一条错了"""
    raw_name = str(obj.get("name", "")).strip()

    def err(msg: str) -> None:
        who = f"第 {index + 1} 门课（{raw_name}）" if raw_name else f"第 {index + 1} 门课"
        raise FormatError(f"{who}：{msg}")

    if not raw_name:
        err("缺少 name（课程名）")

    dow = _parse_day_of_week(obj.get("dayOfWeek"), err)

    nodes = obj.get("nodes")
    if not isinstance(nodes, list):
        err("缺少 nodes。写法是 [起始节次, 结束节次]，例如 [1, 2]。")
    if len(nodes) != 2:
        err("nodes 必须是两个数字，例如 [1, 2]")
    try:
        start_node, end_node = int(nodes[0]), int(nodes[1])
    except (TypeError, ValueError):
        err("nodes 里必须都是数字")
    if not (1 <= start_node <= MAX_NODE and 1 <= end_node <= MAX_NODE):
        err(f"nodes 的节次必须在 1–{MAX_NODE} 之间，现在是 [{start_node}, {end_node}]")
    if start_node > end_node:
        err(f"nodes 的起始节次比结束节次大：[{start_node}, {end_node}]")

    if "weeks" not in obj:
        err("缺少 weeks")
    weeks = _parse_weeks_field(obj["weeks"], total_weeks, err)

    return Course(
        name=raw_name,
        day_of_week=dow,
        start_node=start_node,
        end_node=end_node,
        weeks=weeks,
        place=str(obj.get("place", "")).strip(),
        enabled=bool(obj.get("enabled", True)),
    )


def _parse_day_of_week(value: Any, err) -> int:
    if isinstance(value, bool):          # 注意 bool 是 int 的子类，必须先挡掉
        err("dayOfWeek 必须是 1–7 的整数")
    if isinstance(value, int):
        if not 1 <= value <= 7:
            err(f"dayOfWeek 必须在 1–7 之间（周一=1，周日=7），现在是 {value}")
        return value
    if isinstance(value, str):
        t = value.strip()
        if t.isdigit():
            n = int(t)
            if not 1 <= n <= 7:
                err(f"dayOfWeek 必须在 1–7 之间（周一=1，周日=7），现在是 {n}")
            return n
        if t in _DAY_NAMES:
            return _DAY_NAMES[t]
        err(f'dayOfWeek 写的是 "{t}"，只接受 1–7 或「周一」这类写法')
    err("缺少 dayOfWeek（1–7，周一=1）")


def _parse_weeks_field(value: Any, total_weeks: int, err) -> frozenset[int]:
    """weeks 同时支持两种写法：字符串（给人手写）和数组（给程序生成）"""
    if isinstance(value, str):
        try:
            return parse_weeks(value, total_weeks)
        except FormatError as e:
            err(str(e))
    if isinstance(value, list):
        if not value:
            err("weeks 数组是空的")
        out: set[int] = set()
        for w in value:
            if isinstance(w, bool) or not isinstance(w, int):
                err(f"weeks 数组里出现了非整数：{w!r}")
            if not 1 <= w <= total_weeks:
                err(f"weeks 数组里的 {w} 超出 1–{total_weeks} 的范围")
            out.add(w)
        return frozenset(out)
    err('weeks 必须是字符串（如 "2-4,6-17"）或数组（如 [2,3,4,6,7]）')


def _detect_conflicts(courses: list[Course]) -> list[str]:
    """
    找出「同一时段有两门课，且**周次有重叠**」的情况。

    注意必须比较周次是否重叠，不能只比时段。
    真实课表里就有正当的重叠例子：周四 7-8 节，
    人工智能概论占单周、程序设计基础B 占第 2 周 —— 时段相同但周次不相交，这是合法的。

    如果只比时段，这里就会误报。**误报多了用户就不看警告了，那警告等于没有。**
    """
    warnings: list[str] = []
    for i in range(len(courses)):
        for j in range(i + 1, len(courses)):
            a, b = courses[i], courses[j]
            if a.day_of_week != b.day_of_week:
                continue
            if a.start_node != b.start_node or a.end_node != b.end_node:
                continue
            overlap = a.weeks & b.weeks
            if overlap:
                weeks_text = "、".join(str(w) for w in sorted(overlap))
                warnings.append(
                    f"{WEEKDAY_CN[a.day_of_week]}第 {a.start_node}-{a.end_node} 节："
                    f"「{a.name}」和「{b.name}」在第 {weeks_text} 周冲突"
                )
    return warnings


# ============================================================
#  三、作息模板（v2）
# ============================================================

def _day_type_names_text() -> str:
    """报错里那份「可用的值是：A、B_TRAIN_A、…」，顺序固定"""
    return "、".join(t.value for t in DAY_TYPE_ORDER)


def _day_type_by_name(name: str) -> Optional[DayType]:
    """
    按名字查日型，**大小写不敏感**。

    大小写不敏感是刻意的：用户手写 `"a"` 或 `"B_normal"` 都不该导入失败 ——
    和别处解析 kind / dayOfWeek 的宽容度保持一致。
    见下面 _parse_day_types 的第 ③ 条说明。
    """
    wanted = name.strip().upper()
    for t in DAY_TYPE_ORDER:
        if t.value.upper() == wanted:
            return t
    return None


def _parse_day_types(obj: dict[str, Any]) -> DayTypePolicy:
    """
    解析 `dayTypes` 段 —— 「这份配置实际启用哪几种日型」。

    ```json
    "dayTypes": {
      "enabled": ["A", "B_NORMAL", "SATURDAY", "SUNDAY"],
      "fallback": "B_NORMAL"
    }
    ```

    ## 三个刻意的设计决定（和手机版一字不差）

    **① `enabled` 为空数组要报错，而不是当成「什么都不启用」。**
    那样的话每天都会落到 fallback 上，等于把整套日型系统废掉 ——
    几乎不可能是用户的本意，多半是写错了。报错比静默接受好。

    **② `fallback` 允许不在 `enabled` 里。**
    它表达的是「用哪套模板兜底」，跟「启用了哪些日型」不是一回事。
    比如只启用 A 和周末、却希望周中没早八时回落到 B 型，
    就写成 `enabled: [A, SATURDAY, SUNDAY]` + `fallback: B_NORMAL`。
    这是完全正当的用法，所以不拦。

    **③ 名字大小写不敏感。**
    用户手写时写成 `"a"` 或 `"B_normal"` 都不该导入失败。
    """
    raw = obj.get("enabled")
    if raw is None:
        raise FormatError(
            "dayTypes 段缺少 enabled 字段。\n"
            '它要列出启用的日型，比如 "enabled": ["A", "B_NORMAL", "SATURDAY", "SUNDAY"]'
        )

    if not isinstance(raw, list):
        # 报出实际读到的类型，用户才知道自己写成了什么形状
        raise FormatError(
            f"dayTypes.enabled 必须是一个数组，现在读到的是 {type(raw).__name__}。"
        )

    if not raw:
        raise FormatError(
            "dayTypes.enabled 是空数组 —— 至少要启用一种日型。\n"
            '如果想让「有早八 / 没早八」都能区分，用 '
            '["A", "B_NORMAL", "SATURDAY", "SUNDAY"]。'
        )

    enabled: set[DayType] = set()
    for i, item in enumerate(raw):
        text = str(item).strip()
        t = _day_type_by_name(text)
        if t is None:
            raise FormatError(
                f'dayTypes.enabled 第 {i + 1} 项 "{text}" 不是可识别的日型。\n'
                f"可用的值是：{_day_type_names_text()}"
            )
        enabled.add(t)

    # fallback 可以省略；省略时取 enabled 里在标准顺序中最靠前的那种。
    #
    # 用固定顺序（DAY_TYPE_ORDER）而不是集合的遍历顺序 —— 后者每次运行
    # 可能不一样（同一条 Python 规则，set 的迭代顺序不保证稳定），
    # 会导致同一份文件解析出不同结果，直接违反「输出是确定性的」那条约定。
    fallback_raw = str(obj.get("fallback", "")).strip()
    if not fallback_raw:
        fallback = next(t for t in DAY_TYPE_ORDER if t in enabled)
    else:
        fb = _day_type_by_name(fallback_raw)
        if fb is None:
            raise FormatError(
                f'dayTypes.fallback "{fallback_raw}" 不是可识别的日型。\n'
                f"可用的值是：{_day_type_names_text()}"
            )
        fallback = fb

    return DayTypePolicy(enabled=frozenset(enabled), fallback=fallback)


def _parse_templates(obj: dict[str, Any], warnings: list[str]) -> dict[DayType, list[Block]]:
    """
    只要求用户提供**想改的那几种日型**，没提供的会回落到内置模板。
    这样改一个起床时间只要写十来行，而不是把六套模板整套抄一遍。
    """
    out: dict[DayType, list[Block]] = {}

    for key, value in obj.items():
        day_type = _DAY_TYPE_NAMES.get(key)
        if day_type is None:
            available = "、".join(t.value for t in DAY_TYPE_ORDER)
            raise FormatError(
                f'templates 里有无法识别的日型 "{key}"。\n可用的值是：{available}'
            )

        if not isinstance(value, list):
            raise FormatError(f"templates.{key} 必须是一个数组。")
        if not value:
            raise FormatError(
                f"templates.{key} 是空数组 —— 与其不写，不如整个删掉这一段让它用内置模板。"
            )

        blocks: list[Block] = []
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                raise FormatError(f"templates.{key} 第 {i + 1} 项不是对象。")
            try:
                blocks.append(_parse_block(item))
            except FormatError as e:
                raise FormatError(f"templates.{key} 第 {i + 1} 项：{e}") from None

        _check_template(blocks, key, warnings)
        out[day_type] = sorted(blocks, key=lambda b: b.start)

    if not out:
        raise FormatError("templates 段是空的，删掉它或者填至少一种日型。")
    return out


def _parse_block(obj: dict[str, Any]) -> Block:
    start_raw = str(obj.get("start", "")).strip()
    end_raw = str(obj.get("end", "")).strip()
    if not start_raw:
        raise FormatError('缺少 start（如 "06:55"）')
    if not end_raw:
        raise FormatError('缺少 end（如 "07:10"）')

    start = slots.parse_hhmm(start_raw, is_end=False)
    if start is None:
        raise FormatError(f'start "{start_raw}" 不是合法时刻，写法如 "06:55"')
    end = slots.parse_hhmm(end_raw, is_end=True)
    if end is None:
        raise FormatError(
            f'end "{end_raw}" 不是合法时刻，写法如 "07:10"；一天的最后一段可以写 "24:00"'
        )
    if end <= start:
        raise FormatError(f"end ({end_raw}) 必须晚于 start ({start_raw})")

    title = str(obj.get("title", "")).strip()
    if not title:
        raise FormatError("缺少 title（这一格显示什么）")

    kind_raw = str(obj.get("kind", "CHORE")).strip().upper()
    kind = _KIND_NAMES.get(kind_raw)
    if kind is None:
        available = "、".join(k.value for k in Kind)
        raise FormatError(f'kind "{kind_raw}" 无法识别。可用值：{available}')

    nodes_field = obj.get("nodes")
    nodes: Optional[tuple[int, int]] = None
    if nodes_field is not None:
        if not isinstance(nodes_field, list) or len(nodes_field) != 2:
            raise FormatError("nodes 必须是两个数字，例如 [1, 2]")
        try:
            a, b = int(nodes_field[0]), int(nodes_field[1])
        except (TypeError, ValueError):
            raise FormatError("nodes 里必须都是数字") from None
        if not (1 <= a <= MAX_NODE and 1 <= b <= MAX_NODE):
            raise FormatError(f"nodes 的节次必须在 1–{MAX_NODE} 之间，现在是 [{a}, {b}]")
        if a > b:
            raise FormatError(f"nodes 的起始节次比结束节次大：[{a}, {b}]")
        nodes = (a, b)

    return Block(
        start=start,
        end=end,
        title=title,
        note=str(obj.get("note", "")).strip(),
        kind=kind,
        nodes=nodes,
    )


def _check_template(blocks: list[Block], key: str, warnings: list[str]) -> None:
    """
    **重叠必须报错**，不能只警告。

    原因是引擎在合成时间轴时，遇到两个重叠的固定日程会按「先到先得」
    把后面的截断 —— 结果是用户写的某一格被静默吃掉，界面上看不出来，
    只有对着表才发现少了东西。这种「不报错的错」比直接失败糟糕得多。
    """
    ordered = sorted(blocks, key=lambda b: b.start)
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.start < prev.end:
            raise FormatError(
                f"templates.{key} 里有两格时间重叠：\n"
                f"  {slots.fmt(prev.start)}–{slots.fmt(prev.end)}　{prev.title}\n"
                f"  {slots.fmt(cur.start)}–{slots.fmt(cur.end)}　{cur.title}\n"
                f"同一时刻只能有一件固定的事。如果是想上两门课，那属于课表，不在这里写。"
            )

    # 没铺满一整天是可以接受的：引擎会自动填成「空档 · 机动」。
    # 但提醒一句，因为漏写往往是手滑而不是本意。
    gaps: list[str] = []
    cursor = 0
    for b in ordered:
        if b.start > cursor:
            gaps.append(f"{slots.fmt(cursor)}–{slots.fmt(b.start)}")
        cursor = b.end
    if cursor < slots.MINUTES_PER_DAY:
        gaps.append(f"{slots.fmt(cursor)}–24:00")
    if gaps:
        shown = "、".join(gaps[:3]) + ("…" if len(gaps) > 3 else "")
        warnings.append(
            f"templates.{key} 有 {len(gaps)} 段没排到（{shown}），会被自动填成「空档 · 机动」"
        )


# ============================================================
#  四、生成
# ============================================================

def serialize(
    term_name: str,
    start_date: date,
    total_weeks: int,
    courses: Optional[list[Course]],
    templates: Optional[dict[DayType, list[Block]]] = None,
    day_types: Optional[DayTypePolicy] = None,
) -> str:
    """
    生成 JSON 文本。

    ## 为什么手写序列化，而不用 json.dumps(indent=2)

    同样的理由在 Kotlin 那边也踩过：第三方序列化器的**字段顺序不受你控制**
    （Android 的 org.json 用 LinkedHashMap 保序，标准 JDK 版用 HashMap 随机），
    结果是同一个函数在不同平台上产出的文件长得不一样。

    对一份「给人看、给人改」的文件来说这是致命的：用户照着手机导出的文件
    学会了格式，再去看文档里的例子，会发现两者不一样。

    所以这里手写，把输出完全握在自己手里：字段顺序固定、数组写一行、缩进统一。

    **只要输出是给人看的产物，就不要把它交给第三方库的默认行为。**
    """
    lines: list[str] = []
    lines.append("{")
    lines.append(f'  "format": "{FORMAT_ID}",')
    lines.append(f'  "version": {VERSION},')

    lines.append('  "term": {')
    lines.append(f'    "name": "{_escape(term_name)}",')
    lines.append(f'    "startDate": "{start_date.isoformat()}",')
    lines.append(f'    "totalWeeks": {total_weeks}')
    lines.append("  },")

    # dayTypes 段：只有调用方明确给了策略才写。
    #
    # 放在 term 之后、courses 之前，是因为它描述的是「整份配置怎么跑」，
    # 属于全局设置；课表和模板都是它的下游。
    # 给人改的文件，**顺序本身就是一种说明**。
    #
    # 顺序和手机版一致（ScheduleFormat.serialize 里也是这个位置）——
    # 「输出是确定性的」那条约定要求两边逐字节相同，
    # 所以这个位置不是随便挑的。
    if day_types is not None:
        enabled_text = ", ".join(
            f'"{t.value}"' for t in DAY_TYPE_ORDER if t in day_types.enabled
        )
        lines.append('  "dayTypes": {')
        lines.append(f'    "enabled": [{enabled_text}],')
        lines.append(f'    "fallback": "{day_types.fallback.value}"')
        lines.append("  },")

    lines.append('  "courses": [')
    course_list = courses or []
    for i, c in enumerate(course_list):
        lines.append("    {")
        lines.append(f'      "name": "{_escape(c.name)}",')
        lines.append(f'      "dayOfWeek": {c.day_of_week},')
        lines.append(f'      "nodes": [{c.start_node}, {c.end_node}],')
        weeks_text = format_weeks(c.weeks)
        line = f'      "weeks": "{weeks_text}"'
        if c.place:
            line += f',\n      "place": "{_escape(c.place)}"'
        # 默认值不写出来，文件更干净；解析时缺省即 True
        if not c.enabled:
            line += ',\n      "enabled": false'
        lines.append(line)
        lines.append("    }" + ("," if i < len(course_list) - 1 else ""))

    # ⚠️ 逗号必须跟在 `]` 那一行上，不能自己占一行。
    #
    # 原来的写法是 `lines.append("]")` 然后 `if templates: lines.append(",")`，
    # 结果生成的是
    #       ]
    #     ,
    # 这种**孤立逗号行**。JSON 还能解析（逗号在词法上是分隔符，位置无所谓），
    # 所以谁都没发现 —— 直到用 `tools/verify_format_parity.py` 把
    # 电脑版重建的文件和手机版真实导出的文件**逐字节**比了一次。
    #
    # 它违反的是标准 7.3 那条「同一份规划导出必须逐字节相同」：
    # 手机版写 `],`，电脑版写 `]\n,`，同一份数据在两个平台上 diff 满天飞，
    # 版本对比和「是不是真的一样」这类判断就都没法做了。
    #
    # 教训：**「能被解析」不等于「格式对」。** 结构断言看不出这种差异，
    # 只有和权威产物比字节才看得出来。
    has_trailing_segments = bool(templates)
    lines.append("  ]" + ("," if has_trailing_segments else ""))

    if templates:
        lines.extend(_serialize_templates(templates))

    lines.append("}")
    return "\n".join(lines) + "\n"


def _serialize_templates(templates: dict[DayType, list[Block]], with_key: bool = True) -> list[str]:
    """
    按 DAY_TYPE_ORDER 的固定顺序输出，保证同一份数据每次导出结果完全一致。

    ## with_key 这个参数是踩坑之后加的

    同一个函数有两个用途，而它们需要的**外层形状不一样**：

        with_key=True   写进整份课表文件里，要写成   "templates": { ... }
        with_key=False  存进本地存储，自己就是一份完整 JSON   { ... }

    一开始我两种都用了 True，结果存进本地存储的是 `"templates": { ... }` ——
    这**不是合法的 JSON**（一个裸的键值对，没有外层大括号）。
    读回来时 json.loads 直接失败，然后被「坏了就当没有」的兜底逻辑吞掉，
    于是用户改的模板**静默消失**，一点报错都没有。

    这类「不报错的错」最危险，因为它看起来一切正常。
    """
    out: list[str] = ['  "templates": {'] if with_key else ["{"]
    indent = "    " if with_key else "  "
    inner = "      " if with_key else "    "
    types = [t for t in DAY_TYPE_ORDER if t in templates]

    for ti, day_type in enumerate(types):
        blocks = templates[day_type]
        out.append(f'{indent}"{day_type.value}": [')
        for bi, b in enumerate(blocks):
            parts = [
                f'"start": "{slots.fmt(b.start)}"',
                f'"end": "{slots.fmt(b.end)}"',
                f'"title": "{_escape(b.title)}"',
            ]
            if b.note:
                parts.append(f'"note": "{_escape(b.note)}"')
            # CHORE 是解析时的默认值，写出来是噪音
            if b.kind != Kind.CHORE:
                parts.append(f'"kind": "{b.kind.value}"')
            if b.nodes is not None:
                parts.append(f'"nodes": [{b.nodes[0]}, {b.nodes[1]}]')
            out.append(f"{inner}{{ " + ", ".join(parts) + " }" + ("," if bi < len(blocks) - 1 else ""))
        out.append(f"{indent}]" + ("," if ti < len(types) - 1 else ""))
    out.append("  }" if with_key else "}")
    return out


def _escape(s: str) -> str:
    """
    JSON 字符串转义。

    少了这一步，课程名里出现一个引号就能生成一份坏文件 ——
    而且症状是「导出看起来成功了，导入却报 JSON 错误」，非常难查。
    """
    out: list[str] = []
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def templates_to_json(templates: dict[DayType, list[Block]]) -> str:
    """
    给本地存储用。

    写成**一份完整的 JSON 对象**（不带 "templates" 键名）——
    因为存进去之后要能被独立解析回来。详见 _serialize_templates 的注释。
    """
    if not templates:
        return ""
    return "\n".join(_serialize_templates(templates, with_key=False))


def day_types_to_json(policy: DayTypePolicy) -> str:
    """
    把日型策略序列化成一小段 JSON，给本地存储用。

    写法和 `templates_to_json` 一致：**一份完整的 JSON 对象**，
    存进去之后能被独立解析回来。
    （那一边踩过坑：写成 `"templates": { ... }` 不是合法 JSON，
    读回来失败被「坏了就当没有」吞掉，用户改的东西静默消失。）
    """
    enabled_text = ", ".join(
        f'"{t.value}"' for t in DAY_TYPE_ORDER if t in policy.enabled
    )
    return "{\n" f'  "enabled": [{enabled_text}],\n' f'  "fallback": "{policy.fallback.value}"\n' "}"


def day_types_from_json(text: str) -> DayTypePolicy:
    """
    读回本地存储里的日型策略。

    坏了就返回 `DayTypePolicy.DEFAULT`，**绝不让程序起不来** ——
    大不了重新配一遍，比起不了程序好得多。
    （和 `templates_from_json` 一个路子：那边坏了返回空表。）

    ⚠️ 返回的是 DEFAULT 而**不是 None**，这一点和手机版一致。
    None 在别处有专门的含义 ——「文件里没写这一段，不要动现有策略」。
    如果这里也用 None 表示「坏了」，调用方就没法区分
    「没配过」和「配过但存储坏了」，两者该做的事不一样。
    """
    if not text.strip():
        return DayTypePolicy.DEFAULT
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return DayTypePolicy.DEFAULT
    if not isinstance(obj, dict):
        return DayTypePolicy.DEFAULT
    try:
        return _parse_day_types(obj)
    except (FormatError, TypeError, AttributeError, ValueError):
        return DayTypePolicy.DEFAULT


def templates_from_json(text: str) -> dict[DayType, list[Block]]:
    """
    读回本地存储里的模板。

    坏了就当作「没有自定义模板」，让程序照常起来 ——
    **存储损坏不能变成「程序打不开」**，否则用户连进去修的入口都没有。
    只是他得重新改一遍模板，比起不了程序好得多。
    """
    if not text.strip():
        return {}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(obj, dict):
        # 比如存的是 "null" / "[]" / "123" —— json.loads 能过，但不是我们想要的形状。
        # 少了这一步，None.items() 会让程序在启动阶段直接崩掉。
        return {}
    try:
        return _parse_templates(obj, [])
    except (FormatError, TypeError, AttributeError, ValueError):
        return {}
