"""
节次时间表 —— 来自《时间规划表》第二节。Android 版 `Slots.kt` 的移植。

## 为什么用「从 1 开始」的数组

START[0] 是占位，永远不用。这样写 START[3] 时，3 就是课表上的「第 3 节」，
不用到处写 3-1。代价是浪费一个元素，换来的是**少一个 off-by-one 的机会** ——
这类错误往往要等到课表和实际对不上才会被发现。

## ⚠️ 连堂

第 1-2 节中间只有 5 分钟换场，所以实际占用的是一个完整的 08:30–10:05 时间块，
而不是「08:30–09:15 + 09:20–10:05」两个独立的块。
"""

from typing import Optional

#: 下标即节次，下标 0 占位不用
START: tuple[int, ...] = (
    0,
    8 * 60 + 30,    # 1   08:30
    9 * 60 + 20,    # 2   09:20
    10 * 60 + 25,   # 3   10:25
    11 * 60 + 15,   # 4   11:15
    14 * 60,        # 5   14:00
    14 * 60 + 50,   # 6   14:50
    15 * 60 + 55,   # 7   15:55
    16 * 60 + 45,   # 8   16:45
    19 * 60,        # 9   19:00
    19 * 60 + 50,   # 10  19:50
)

END: tuple[int, ...] = (
    0,
    9 * 60 + 15,    # 1   09:15
    10 * 60 + 5,    # 2   10:05
    11 * 60 + 10,   # 3   11:10
    12 * 60,        # 4   12:00
    14 * 60 + 45,   # 5   14:45
    15 * 60 + 35,   # 6   15:35
    16 * 60 + 40,   # 7   16:40
    17 * 60 + 30,   # 8   17:30
    19 * 60 + 45,   # 9   19:45
    20 * 60 + 35,   # 10  20:35
)

MAX_NODE = 10

#: 一天的分钟数。写死成常量，比到处散落 1440 好
MINUTES_PER_DAY = 24 * 60


def start(node: int) -> int:
    return START[node]


def end(node: int) -> int:
    return END[node]


def fmt(minute: int) -> str:
    """510 -> '08:30'；1440 -> '24:00'（一天的最后时刻，界面上要显示成 24:00 而不是 00:00）"""
    if minute >= MINUTES_PER_DAY:
        return "24:00"
    return f"{minute // 60:02d}:{minute % 60:02d}"


def parse_hhmm(text: str, is_end: bool = False) -> Optional[int]:
    """
    '06:55' -> 415；'24:00' -> 1440（只允许出现在 end 位置）。

    解析失败返回 None 而不是抛异常 —— 因为调用方（格式解析器）需要
    根据是哪种失败给出不同的错误文案，抛异常会把上下文丢掉。
    """
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= minute <= 59):
        return None
    if hour == 24:
        return MINUTES_PER_DAY if (is_end and minute == 0) else None
    if not (0 <= hour <= 23):
        return None
    return hour * 60 + minute
