"""
时间轴引擎 —— 整个程序的心脏。Android 版 `TimelineEngine.kt` 的移植。

输入：一个日期 + 第几教学周 + 全部课程
输出：从 00:00 到 24:00 一条**首尾相接、互不重叠**的日程表

## 两层叠加

    第一层  作息模板（A 型 / B 型 / 周六 / 周日）—— 铺满一整天
    第二层  当天的课                        —— 盖在第一层上面

为什么是这个顺序？模板回答「正常情况下这个点该干嘛」，
课表回答「学校规定这个点必须在哪」。后者是硬约束，优先级更高。
而且课表是稀疏的（一天最多 5 段），拿它去覆盖密集的模板最省事。

反过来先铺课表再填模板，就会遇到「两个空闲段之间怎么填」的问题，边界情况反而更多。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Optional, Sequence

from . import slots
from .day_type_policy import DayTypeConfig
from .models import (
    Block,
    Course,
    DayType,
    Kind,
    Moment,
    day_type_name,
)

#: 午前 / 午后的分界线，用于判断「上午的睡眠段」
NOON = 12 * 60


# ============================================================
#  教学周次
# ============================================================

def week_of(d: date, term_start: date) -> int:
    """
    教学第几周。

    注意这里用整除（Python 的 `//` 本身就是向下取整），而不是 C/Java 那种朝零取整。
    日期早于学期开始时差值是负数：
        -3 // 7 == -1   → 周次 0（学期还没开始）  ✓
        -3 / 7  == -0.43 → 截断成 0 → 周次 1     ✗ 错的

    Kotlin 那边要专门写 Math.floorDiv 才能得到同样的结果，
    Python 的 // 天生就是对的 —— 但**别依赖这个巧合**，注释写清楚为什么。
    """
    return (d - term_start).days // 7 + 1


def monday_of(d: date) -> date:
    """
    把任意日期吸附到它所在那一周的周一。

    为什么需要它：整个时间轴是按「第 N 周 = 起始日 + (N-1)×7 天」推出来的，
    起始日只要不是周一，每一周的边界就全错位。

    两个地方会用到，但处理方式不同 —— 这个对比很值得记：

        JSON 导入  → 报错拒绝。用户不在场，没法确认，猜错了就是整张表错位。
        界面选日期  → 自动吸附。用户就在屏幕前，顺手改对比甩个错误更好。

    **同一个问题在批处理场景和交互场景下，应该给不同的答案。**
    """
    return d - timedelta(days=d.isoweekday() - 1)


# ============================================================
#  第一步：今天属于哪种日型
# ============================================================

def courses_on(d: date, week: int, courses: Iterable[Course]) -> list[Course]:
    """今天实际要上的课（已按周次过滤）"""
    dow = d.isoweekday()
    return [c for c in courses if c.enabled and c.day_of_week == dow and week in c.weeks]


def has_early_class(d: date, week: int, courses: Iterable[Course]) -> bool:
    """
    「有早八」的日历定义：今天第 1-2 节有课。

    这是**日历事实**，不受任何配置影响 —— 界面上的「· 有早八」
    后缀永远按它说话（day_type_display）。
    """
    return any(c.start_node <= 2 for c in courses_on(d, week, courses))


def resolve_day_type(
    d: date,
    week: int,
    courses: Iterable[Course],
    config: DayTypeConfig,
) -> DayType:
    """
    今天实际用哪套模板 —— 引擎判定的唯一入口。按顺序三条：

      ① 工作日全天无课 → 休息日（REST）。「课表里这天一门课都没有」
         是数据里写得明明白白的客观事实 —— 假期、停课、课表没排到的
         周次都落到这里。它**不受星期映射影响**：休息日不是一种
         「偏好」，再怎么映射也不能把没课的日子变成上学日。
      ② 工作日有早八且配置指定了早八日型 → early_type。
         「今天要不要早起」是这个程序存在的理由，这一层优先于星期映射。
      ③ 其余 → weekday_map[星期几]。

    ## ⚠️ `config` 故意不给默认值

    忘了传配置的话，程序会静悄悄按默认映射跑 —— 界面按 A 型显示、
    通知却按 B 型排，这类不一致查起来非常费劲。不给默认值，
    每个漏改的调用点都会当场 TypeError。Python 没有编译期检查，
    这个报错就是我们的「编译器」。

    ## 周末为什么不做「无课 → 休息日」

    现状语义：周末不管有没有课都按周六/周日（映射目标）的模板过 ——
    周末的作息本来就不由课表驱动。只有**工作日**的无课才判休息日
    （假期、停课、调课都发生在工作日）。
    """
    dow = d.isoweekday()          # 1=周一 … 7=周日
    if dow >= 6:
        return config.weekday_map[dow - 1]
    if not courses_on(d, week, courses):
        return DayType.REST       # 全天没课 → 按休息日过
    if config.early_type is not None and has_early_class(d, week, courses):
        return config.early_type
    return config.weekday_map[dow - 1]


def day_type(
    d: date,
    week: int,
    courses: Iterable[Course],
    config: DayTypeConfig,
) -> DayType:
    """
    **实际要用的日型** —— resolve_day_type 的对外名字。

    ## ⚠️ `config` 故意不给默认值

    理由见 resolve_day_type：漏传配置必须当场报错，不能静默按默认跑。
    """
    return resolve_day_type(d, week, courses, config)


def day_type_display(
    d: date,
    week: int,
    courses: Iterable[Course],
    config: DayTypeConfig,
    names: Optional[dict[DayType, str]] = None,
) -> str:
    """
    界面上显示的日型描述 —— 会区分「用哪套模板」和「今天有没有早八」。

    [names] 是 store 里的自定义日型名（v4）；不传或传空就用默认名。
    引擎本身不碰 store，所以名字只能由调用方带进来。

    ## 为什么需要单独一个函数

    默认名只说明「这套模板叫什么」。但用户看到今天标着
    「A 型日」时，自然会想知道今天要不要早起 —— 而这**不能从标签推出来**：

        默认配置下    A 型日 ⟺ 当天第 1-2 节有课（有早八）
        自定义配置下  用户可以让工作日全部落到 A 型模板，
                     于是「A 型日」不再意味着「有早八」

    实测过一次：把工作日全落到 A 之后，周二（当天没课）的界面
    依然显示「A 型日 · 有早八」—— **那是一句假话**。

    所以「有没有早八」去问**日历**（has_early_class），
    「用哪套模板」去问**配置**（day_type），两者拼起来才是完整的描述。
    """
    used = day_type(d, week, courses, config)
    label = day_type_name(used, names)
    early = d.isoweekday() <= 5 and has_early_class(d, week, courses)
    return f"{label} · 有早八" if early else label


def wake_minute(template: Sequence[Block]) -> Optional[int]:
    """
    从模板推出「今天几点起床」。

    做法是找**午前最后一个睡觉块的结束时刻** —— 那一格结束就意味着醒了。

    为什么不写死 06:55？因为模板可以被用户导入替换，起床时间不再是个常量。
    而且这样一来，模板一改，悬浮窗、次日预告、界面三处会**同时**跟着变，
    没有哪个地方需要单独改。

    返回 None 表示这套模板里没有上午的睡眠段（比如整夜工作的排法）——
    界面要能接受这种情况，那一行直接不显示，而不是显示一个错的时间。
    """
    morning_sleep = [b for b in template if b.kind == Kind.SLEEP and b.start < NOON]
    if not morning_sleep:
        return None
    return max(b.end for b in morning_sleep)


# ============================================================
#  第二步：合成时间轴
# ============================================================

def moments(
    d: date,
    week: int,
    courses: Sequence[Course],
    templates: dict[DayType, Sequence[Block]],
    config: DayTypeConfig = DayTypeConfig.DEFAULT,
) -> list[Moment]:
    """
    把模板和当天的课叠成一条完整的时间轴。

    `config` 有默认值（和 `templates` 一样），因为**老的调用点保持原样就能继续工作** ——
    这是给参数设默认值的好处。但要注意：这里给默认值**不同于** `day_type()` 里
    故意不给 —— 那里不给是为了逼出「忘了传策略」的错误，而这里给，
    是因为 `moments` 是引擎入口，它的调用点（api / main / 测试）
    本来就可能只想用默认作息跑一遍。

    策略和模板必须**同源**：如果模板来自 A 处方、策略来自 B 处，
    就可能出现「按策略这是 A 型日，但取到的却是 B 型的模板」这种错配。
    手机版把两者放进同一个 `TemplateSet` 对象里就是为解决这个。
    这里由 `store.template_set()` 负责成对返回，见那边的说明。
    """
    base = templates[day_type(d, week, courses, config)]
    today_courses = courses_on(d, week, courses)

    course_blocks = [
        Block(
            start=slots.start(c.start_node),
            end=slots.end(c.end_node),        # 连堂自动合并成一整块
            title=c.name,
            note="",
            kind=Kind.CLASS,
            nodes=(c.start_node, c.end_node),
            place=c.place,
            is_course=True,
        )
        for c in today_courses
    ]

    out: list[Block] = []
    claimed: set[int] = set()

    for blk in base:
        if blk.nodes is not None:
            # 占位格：有课就整格替换，没课就标注「无课」
            want = slots.start(blk.nodes[0])
            idx = next((i for i, cb in enumerate(course_blocks) if cb.start == want), -1)
            if idx >= 0:
                out.append(course_blocks[idx])
                claimed.add(idx)
            else:
                out.append(blk.copy(title=f"第 {blk.nodes[0]}-{blk.nodes[1]} 节 · 无课"))
        else:
            # 固定日程：和课撞上就**整块让位** —— 阈值是原块的 100%。
            #
            # 这里曾经是「被课压掉多少就保留多少」，结果出现过
            # 「训练 100 分钟」被两节课夹成 5 分钟缝还照显的自相矛盾条目
            # （手机版 v1.11.1 用户报障，2026-10）。残块再短也留不得：
            # 标题、备注都是按完整时长写的，砍剩一截之后它们全变成假话。
            # 空档交给 _assemble 的「空档 · 机动」兜底。
            if not any(_overlap(cb, blk) for cb in course_blocks):
                out.append(blk)

    # 没有任何占位格认领的课（周末加课、临时调课等）直接插进来；
    # 和它撞上的非课程块按同一条 100% 规则整块让位（无课占位格同理）
    for i, cb in enumerate(course_blocks):
        if i in claimed:
            continue
        out = [b for b in out if b.is_course or not _overlap(b, cb)]
        out.append(cb)

    return _assemble(out)


def _overlap(a: Block, b: Block) -> bool:
    """两块时间是否有重叠。首尾相接不算 —— 10:05 下课、10:05 开始的块互不侵犯"""
    return a.start < b.end and b.start < a.end


def _assemble(blocks: Sequence[Block]) -> list[Moment]:
    """
    把一堆可能重叠、可能有空洞的块，整理成一条连续的线。

    这个函数是「结果一定合法」的保证：不管上面的叠加逻辑多乱，
    经过它之后出来的东西一定满足三条 —— 有序、不重叠、首尾相接。

    这种「最后一道收口」的写法很值得学：把正确性压力集中到一处，
    上游就可以写得随意一些，不用每一步都小心翼翼。
    """
    ordered = sorted((b for b in blocks if b.end > b.start), key=lambda b: b.start)

    raw: list[Moment] = []
    cursor = 0

    for b in ordered:
        start = max(b.start, cursor)
        if start >= b.end:
            continue                                  # 完全被前面的块吃掉了
        if start > cursor:
            raw.append(_gap(cursor, start))
        raw.append(Moment(start, b.end, b.title, b.note, b.place, b.kind, b.is_course))
        cursor = b.end

    if cursor < slots.MINUTES_PER_DAY:
        raw.append(_gap(cursor, slots.MINUTES_PER_DAY))

    return _merge_adjacent(raw)


def _gap(from_minute: int, to_minute: int) -> Moment:
    return Moment(
        from_minute, to_minute,
        "空档 · 机动", "临时会议、社团活动、突发任务", "", Kind.FREE,
    )


def _merge_adjacent(items: Sequence[Moment]) -> list[Moment]:
    """
    相邻两格如果标题、备注、地点、性质完全一样，就合成一格。

    没有这一步的话，模板里被课切成两半的「晚自习」会显示成两条，
    看起来像是重复了。
    """
    merged: list[Moment] = []
    for m in items:
        last = merged[-1] if merged else None
        if (last is not None
                and last.end == m.start
                and last.title == m.title
                and last.note == m.note
                and last.place == m.place
                and last.kind == m.kind):
            merged[-1] = Moment(
                last.start, m.end, last.title, last.note, last.place, last.kind, last.is_course
            )
        else:
            merged.append(m)
    return merged


# ============================================================
#  第三步：查询
# ============================================================

def current_at(items: Sequence[Moment], minute: int) -> Optional[Moment]:
    """此刻在哪一格"""
    found = None
    for m in items:
        if m.start <= minute:
            found = m
        else:
            break
    if found is None and items:
        return items[0]
    return found


def next_after(items: Sequence[Moment], minute: int) -> Optional[Moment]:
    """下一格是什么"""
    for m in items:
        if m.start > minute:
            return m
    return None
