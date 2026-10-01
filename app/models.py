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

    ## v4：值是日型 id（"1"-"7"），不是语义名

    自定义日型上线后（格式 v4），日型在文件、存储、前端里的身份是
    **id 1-7**；成员名（A / B_TRAIN_A / …）只是代码里的语义别名，
    方便引擎和测试读。id 与规则的对应关系见 DEFAULT_DAY_TYPE_NAMES
    上方的表格，这是**格式标准的一部分**，改名也不能动：

        id 1  工作日有早八（第 1-2 节有课）
        id 2  无早八的普通工作日
        id 3  周二
        id 4  周四
        id 5  周六
        id 6  周日
        id 7  工作日全天无课（休息日，自动判定，不可禁用）

    v3 及更早的文件里写的是语义名（"A"、"B_TRAIN_A"…），
    解析层负责翻译成 id —— 见 format_spec 的双键解析。
    """

    A = "1"                    # 工作日有早八（原 v3 的 "A"）
    B_NORMAL = "2"             # 无早八的普通工作日（原 "B_NORMAL"）
    B_TRAIN_A = "3"            # 周二（原 "B_TRAIN_A"，训练日 · 力量A）
    B_TRAIN_B = "4"            # 周四（原 "B_TRAIN_B"，训练日 · 力量B）
    SATURDAY = "5"             # 周六（原 "SATURDAY"）
    SUNDAY = "6"               # 周日（原 "SUNDAY"）
    REST = "7"                 # 工作日全天无课（原 "REST"，休息日）

    # ⚠️ REST（id 7）**故意不进** DAY_TYPE_ORDER。它不是用户可启停的日型，
    # 而是引擎从课表推导出来的状态 ——「那天没课」是客观事实，不是偏好。
    # 不进 ORDER 的连锁效果全部是故意的：
    #   · 文件的 dayTypes 段（启用/回落）写不了它 —— 写 "7" 会报错
    #   · 设置页的日型勾选、回落下拉都不会出现它
    # 与 v3 的区别：它的**名字和模板**在 v4 里可以自定义、可以随文件导出
    # （templates 段允许键 "7"）—— 只是「哪天算休息日」这件事仍然由引擎定。

    def __str__(self) -> str:  # pragma: no cover - 调试便利，非行为路径
        return self.value


#: 七种日型的默认名字。**文案与 v3 的 DAY_TYPE_LABEL 逐字相同** ——
#: v4 只是让它们变成「可以改的默认值」，默认观感不变。
#:
#: ⚠️ A 的文案是「A 型日」，**不带「有早八」**。
#:
#: 原来写的是「A 型日 · 有早八」。加了日型策略之后，这句话会变成假话：
#: 用户可以把没早八的工作日全部回落到 A 型模板，于是周二（当天没课）
#: 也会被标成「A 型日 · 有早八」。
#:
#: 根子在于它把两件独立的事焊在了一起：
#:   · **用哪套模板** —— 由策略决定（DayTypePolicy.resolve）
#:   · **今天有没有早八** —— 由日历决定（engine.natural_day_type）
#:
#: 拆开之后，需要连起来显示的地方调 engine.day_type_display()。
#: **凡是「一个字段同时表达两件事」的地方，等其中一件事能独立变化时就会出问题。**
DEFAULT_DAY_TYPE_NAMES: dict[DayType, str] = {
    DayType.A: "A 型日",
    DayType.B_NORMAL: "B 型日",
    DayType.B_TRAIN_A: "B 型日 · 训练日 力量A",
    DayType.B_TRAIN_B: "B 型日 · 训练日 力量B",
    DayType.SATURDAY: "周六",
    DayType.SUNDAY: "周日",
    # REST（id 7）不在 DAY_TYPE_ORDER 里（理由见枚举上的注释），但显示文案要有 ——
    # 今天页、悬浮窗的日型描述都会用到。v4 起它也可以被用户改名。
    DayType.REST: "无课 · 休息日",
}


def day_type_name(t: DayType, custom: Optional[dict[DayType, str]] = None) -> str:
    """
    当前生效的日型名：**自定义优先，没改过用默认**。

    `custom` 是 store 里那份稀疏的名字表（只有用户改过的 id 才有键）。
    所有「界面要显示日型名」的地方都应该走这里，而不是直接查
    DEFAULT_DAY_TYPE_NAMES —— 否则用户的改名在某些页面会静默失效。
    """
    if custom:
        name = custom.get(t)
        if name:
            return name
    return DEFAULT_DAY_TYPE_NAMES[t]


#: 六种可启用日型的固定顺序（id 升序，REST/id 7 不在其中）。
#: 文件 dayTypes 段的输出顺序、设置页/编辑页的列表顺序都依赖它保持稳定。
#:
#: ⚠️ 与 v3 的顺序不同（v3 是 A、训A、训B、B、周六、周日的语义分组序）。
#: v4 文件按 id 升序输出；需要复刻 v3 字节级输出的地方（parity 守卫）
#: 用 format_spec 里私有的旧序常量，两者互不干扰。
DAY_TYPE_ORDER = [
    DayType.A, DayType.B_NORMAL, DayType.B_TRAIN_A,
    DayType.B_TRAIN_B, DayType.SATURDAY, DayType.SUNDAY,
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
