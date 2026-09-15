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
from .day_type_policy import DayTypePolicy
from .models import DAY_TYPE_LABEL, Block, Course, DayType, Kind, Moment

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

def natural_day_type(d: date, week: int, courses: Iterable[Course]) -> DayType:
    """
    **日历上是哪种日** —— 判断依据只有一条：今天第 1-2 节有没有课。

    这是整个设计里最值得注意的一处。文档里写死了「周一、周三 = A 型」，
    但如果照抄成 `if dow in (1, 3)`，那么第 8 周停课、国庆调课、中秋放假时，
    程序还会按 A 型让你 06:55 起床 —— 错的。

    改成「现算」之后，这几种情况**自动就对**了，一行特判都不用写。

    ## 它和 day_type() 的分工

    这个函数回答「**日历上是哪种日**」，`day_type()` 回答
    「**这次实际要用哪套模板**」。两者的差别来自 `DayTypePolicy`：
    这份配置可能根本没启用训练日，那么算出来的 `B_TRAIN_A` 就要被映射成别的。

    拆成两层的好处是策略可配置 —— 如果这里直接把训练日那两行删掉，
    就再也没有「想让训练日生效」的余地了。

    **要测「日历规则」本身（比如「周二算不算训练日」）就调这个。**

    （手机版里叫 naturalDayType，同一个东西。）
    """
    dow = d.isoweekday()          # 1=周一 … 7=周日
    if dow == 6:
        return DayType.SATURDAY
    if dow == 7:
        return DayType.SUNDAY

    has_early_class = any(
        c.enabled and c.day_of_week == dow and c.start_node <= 2 and week in c.weeks
        for c in courses
    )
    if has_early_class:
        return DayType.A

    if dow == 2:
        return DayType.B_TRAIN_A
    if dow == 4:
        return DayType.B_TRAIN_B
    return DayType.B_NORMAL


def day_type(
    d: date,
    week: int,
    courses: Iterable[Course],
    policy: DayTypePolicy,
) -> DayType:
    """
    **实际要用的日型** —— 原始日历规则再经过 `policy` 映射。

    ## ⚠️ `policy` 故意不给默认值

    如果给它一个默认值，那么调用方「忘了传策略」时不会有任何提示，
    程序会静悄悄地按默认策略跑 —— 而界面按 A 型显示、通知却按 B 型排，
    这类不一致查起来非常费劲。

    不给默认值的话，**每个漏改的调用点都会当场 TypeError**，一个都跑不掉。
    Python 没有编译期的参数检查，这个报错就是我们的「编译器」。
    （手机版那边是 Kotlin 编译期报错，效果一样，理由写在 DayTypePolicy 的注释里。）
    """
    return policy.resolve(natural_day_type(d, week, courses))


def day_type_display(
    d: date,
    week: int,
    courses: Iterable[Course],
    policy: DayTypePolicy,
) -> str:
    """
    界面上显示的日型描述 —— 会区分「用哪套模板」和「今天有没有早八」。

    ## 为什么需要单独一个函数

    `DAY_TYPE_LABEL` 只说明「这套模板叫什么」。但用户看到今天标着
    「A 型日」时，自然会想知道今天要不要早起 —— 而这**不能从标签推出来**：

        默认配置下    A 型日 ⟺ 当天第 1-2 节有课（有早八）
        自定义配置下  用户可以让工作日全部回落到 A 型模板，
                     于是「A 型日」不再意味着「有早八」

    实测过一次：把工作日全落到 A 之后，周二（当天没课）的界面
    依然显示「A 型日 · 有早八」—— **那是一句假话**。

    所以「有没有早八」去问**日历**（natural_day_type），
    「用哪套模板」去问**策略**（day_type），两者拼起来才是完整的描述。
    """
    natural = natural_day_type(d, week, courses)
    used = policy.resolve(natural)
    label = DAY_TYPE_LABEL[used]
    return f"{label} · 有早八" if natural is DayType.A else label


def courses_on(d: date, week: int, courses: Iterable[Course]) -> list[Course]:
    """今天实际要上的课（已按周次过滤）"""
    dow = d.isoweekday()
    return [c for c in courses if c.enabled and c.day_of_week == dow and week in c.weeks]


def wake_minute(template: Sequence[Block]) -> Optional[int]:
    """
    从模板推出「今天几点起床」。

    做法是找**午前最后一个睡觉块的结束时刻** —— 那一格结束就意味着醒了。

    为什么不写死 06:55？因为模板可以被用户导入替换，起床时间不再是个常量。
    而且这样一来，模板一改，悬浮球、次日预告、界面三处会**同时**跟着变，
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
    policy: DayTypePolicy = DayTypePolicy.DEFAULT,
) -> list[Moment]:
    """
    把模板和当天的课叠成一条完整的时间轴。

    `policy` 有默认值（和 `templates` 一样），因为**老的调用点保持原样就能继续工作** ——
    这是给参数设默认值的好处。但要注意：这里给默认值**不同于** `day_type()` 里
    故意不给 —— 那里不给是为了逼出「忘了传策略」的错误，而这里给，
    是因为 `moments` 是引擎入口，它的调用点（api / main / 测试）
    本来就可能只想用默认作息跑一遍。

    策略和模板必须**同源**：如果模板来自 A 处方、策略来自 B 处，
    就可能出现「按策略这是 A 型日，但取到的却是 B 型的模板」这种错配。
    手机版把两者放进同一个 `TemplateSet` 对象里就是为解决这个。
    这里由 `store.template_set()` 负责成对返回，见那边的说明。
    """
    base = templates[day_type(d, week, courses, policy)]
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
            # 固定日程：被课「压」掉多少就保留多少
            segments = [blk]
            for cb in course_blocks:
                segments = [piece for seg in segments for piece in _clip(seg, cb)]
            out.extend(segments)

    # 没有任何占位格认领的课（周末加课、临时调课等），直接插进来并裁掉冲突
    for i, cb in enumerate(course_blocks):
        if i in claimed:
            continue
        kept = [b for b in out if b.is_course]
        kept += [piece for b in out if not b.is_course for piece in _clip(b, cb)]
        out = kept + [cb]

    return _assemble(out)


def _clip(b: Block, c: Block) -> list[Block]:
    """把一块 b 挖掉与 c 重叠的部分，返回剩下的碎片（0、1 或 2 块）"""
    if b.end <= c.start or b.start >= c.end:
        return [b]                      # 不重叠，原样返回
    parts: list[Block] = []
    if b.start < c.start:
        parts.append(b.copy(end=c.start))    # 前一段
    if b.end > c.end:
        parts.append(b.copy(start=c.end))    # 后一段
    return parts


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
