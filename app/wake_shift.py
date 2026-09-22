"""
改起床时间 —— 把「起床到第一个锚点」之间那几格整体平移。

## 为什么需要这个动作本身

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

## 为什么单独一个模块

这是一百多行纯算法：给一串 Block、给一个目标起床时刻，算出新的 Block 串，
或者算出一句「为什么改不了」。它不碰 store、不碰窗口，混在 `Api` 的四十多个
方法中间只会让人找不到它。

搬出来还有个额外好处：**能单测了**。以前只能通过 Api + Store + 内置数据绕一圈
来验它，像「周末模板里没有课表格子、要退而用晚上的睡眠当锚点」这种分支
根本覆盖不到。

⚠️ 报错文案里有三句是 `tests/smoke.py` 断言过的（「塞不下」「早读」「课表格子」），
它们的用处在文案里 —— **用户要能照着它下一步该干嘛**。改文案记得同步改测试。
"""

from __future__ import annotations

from typing import NamedTuple, Optional, Sequence

from . import engine, slots
from .models import Block, Kind


class Result(NamedTuple):
    """`apply` 的结果。三种情况靠 `unchanged` / `blocks` 区分，别去猜。"""

    ok: bool
    #: 给用户看的一句话。成功、没变化、失败都带。
    message: str
    #: 改好的格子。只在「成功且真的挪了」时有值。
    blocks: Optional[list[Block]] = None
    #: 起床时间本来就是这个点，什么都没改（此时 blocks 是 None）。
    unchanged: bool = False


def apply(blocks: Sequence[Block], target: int) -> Result:
    """
    把 `blocks` 的起床时间改成 `target`（当天第几分钟）。

    `blocks` 是某一个日型的完整一天（00:00 到 24:00 排满）。返回的
    `Result.blocks` 是改好的新的一天，顺序按开始时刻排好；调用方负责写回存储。
    """
    blocks = sorted(blocks, key=lambda b: b.start)

    # 找出「午前最后一个睡觉块」—— 和 engine.wake_minute() 用的是同一条规则，
    # 保证「界面显示的起床时间」和「实际改的那一格」永远是同一个
    sleep_idx = -1
    best_end = -1
    for i, b in enumerate(blocks):
        if b.kind == Kind.SLEEP and b.start < engine.NOON and b.end > best_end:
            best_end = b.end
            sleep_idx = i

    if sleep_idx < 0:
        return Result(False, "这套模板里没有上午的睡眠段，没法改起床时间")

    old = blocks[sleep_idx].end
    if target == old:
        return Result(True, "起床时间没有变化", unchanged=True)

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
        return Result(
            False,
            "这套模板里找不到「不能挪动的锚点」（课表格子或晚上的睡眠），没法安全地顺延。",
        )

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
        return Result(
            False,
            (
                f"起床到「{anchor.title}」之间塞不下。\n\n"
                f"「{anchor.title}」固定在 {slots.fmt(anchor.start)} 开始（{why}），\n"
                f"从 {slots.fmt(updated[sleep_idx].end)} 起床算起只有 {room} 分钟，\n"
                f"而中间这几格加起来要 {need} 分钟。\n\n"
                f"如果本意是「起晚一点、不早读了」，请先到列表里删掉早读那一格，再改起床时间。"
            ),
        )

    for i in range(sleep_idx, anchor_idx):
        if updated[i].start < 0:
            return Result(
                False, f"这样改会让「{updated[i].title}」跑到昨天去，换个时间试试。"
            )

    moved = anchor_idx - sleep_idx - 1
    direction = "推迟" if delta > 0 else "提前"
    return Result(
        True,
        f"起床时间已{direction}到 {slots.fmt(target)}"
        + (f"，后面 {moved} 格跟着挪" if moved else ""),
        blocks=updated,
    )
