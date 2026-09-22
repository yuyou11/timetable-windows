"""
数据结构定义。

这份文件是 Android 版 `Models.kt` 的 Python 移植。两边**必须严格对齐** ——
因为它们读写的是同一套 JSON 格式，电脑上导出的文件要能导进手机，反之亦然。

## 时间为什么用「整数分钟」

`start` / `end` 都不是时间对象，而是**距当天 00:00 的分钟数**：
08:30 -> 8*60+30 = 510，23:10 -> 1390。

理由：后面要做三件事 —— 比大小、算时长、跨格裁剪。
这三件事在整数上都是一行代码，用 datetime.time 反而要来回转换，
而且 time 对象还不支持直接相减。

**能用整数表达的量，就别用复杂对象。** 这是很常见的一个取舍。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional


class Kind(str, Enum):
    """时段性质，决定界面配色。继承 str 是为了能直接和 JSON 里的字符串比较。"""

    SLEEP = "SLEEP"      # 睡觉
    MEAL = "MEAL"        # 吃饭
    CLASS = "CLASS"      # 上课
    STUDY = "STUDY"      # 自习
    TRAIN = "TRAIN"      # 运动
    FREE = "FREE"        # 自由时间
    CHORE = "CHORE"      # 洗漱等杂事（默认值）
    TRANSIT = "TRANSIT"  # 路上


class DayType(str, Enum):
    """
    一天属于哪种模板。

    注意：**这个类型不是按星期几写死的，而是每天现算的**。
    判断依据只有一条 —— 今天第 1-2 节有没有课。见 engine.day_type()。
    """

    A = "A"                    # 有早八
    B_TRAIN_A = "B_TRAIN_A"    # 周二 · 力量 A
    B_TRAIN_B = "B_TRAIN_B"    # 周四 · 力量 B
    B_NORMAL = "B_NORMAL"      # 无早八的普通工作日
    SATURDAY = "SATURDAY"
    SUNDAY = "SUNDAY"


DAY_TYPE_LABEL = {
    # ⚠️ A 的文案是「A 型日」，**不带「有早八」**。
    #
    # 原来写的是「A 型日 · 有早八」。加了日型策略之后，这句话会变成假话：
    # 用户可以把没早八的工作日全部回落到 A 型模板，于是周二（当天没课）
    # 也会被标成「A 型日 · 有早八」。
    #
    # 根子在于它把两件独立的事焊在了一起：
    #   · **用哪套模板** —— 由策略决定（DayTypePolicy.resolve）
    #   · **今天有没有早八** —— 由日历决定（engine.natural_day_type）
    #
    # 拆开之后，需要连起来显示的地方调 engine.day_type_display()。
    # **凡是「一个字段同时表达两件事」的地方，等其中一件事能独立变化时就会出问题。**
    DayType.A: "A 型日",
    DayType.B_TRAIN_A: "B 型日 · 训练日 力量A",
    DayType.B_TRAIN_B: "B 型日 · 训练日 力量B",
    DayType.B_NORMAL: "B 型日",
    DayType.SATURDAY: "周六",
    DayType.SUNDAY: "周日",
}

#: 六种日型的固定顺序。导出、界面展示都依赖它保持稳定
DAY_TYPE_ORDER = [
    DayType.A, DayType.B_TRAIN_A, DayType.B_TRAIN_B,
    DayType.B_NORMAL, DayType.SATURDAY, DayType.SUNDAY,
]


@dataclass
class Block:
    """
    模板里的一格。

    nodes 不为 None 时，说明这一格是**课表格子**（值是节次范围，如 (1, 2)），
    当天有课时会被整格替换；为 None 则是固定日程，任何课都盖不住它。

    用 tuple 而不是 range 表示节次范围：range 是惰性对象，比较和判断都别扭，
    这里只需要「起点和终点」两个数，tuple 更直白。
    """

    start: int
    end: int
    title: str
    note: str = ""
    kind: Kind = Kind.CHORE
    nodes: Optional[tuple[int, int]] = None
    place: str = ""
    is_course: bool = False

    @property
    def duration(self) -> int:
        return self.end - self.start

    def copy(self, **changes) -> "Block":
        """对应 Kotlin 的 data class .copy()；Python 用 dataclasses.replace 实现"""
        return replace(self, **changes)


@dataclass
class Course:
    """一门课。weeks 是「这门课在第几教学周会上」，用它过滤单双周、起止周。"""

    name: str
    day_of_week: int          # 1 = 周一 … 7 = 周日
    start_node: int
    end_node: int
    weeks: frozenset[int]     # 用 frozenset：不可变，可哈希，能放进 set 比较
    place: str = ""
    enabled: bool = True
    id: str = ""


@dataclass
class Moment:
    """时间轴上的最终结果。界面和悬浮窗都只认这个。"""

    start: int
    end: int
    title: str
    note: str = ""
    place: str = ""
    kind: Kind = Kind.CHORE
    is_course: bool = False

    @property
    def duration(self) -> int:
        return self.end - self.start

    def contains(self, minute: int) -> bool:
        return self.start <= minute < self.end
