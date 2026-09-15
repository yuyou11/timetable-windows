"""
日型策略 —— 决定「哪几种日型真的会被用到」。

这是 Android 版 `data/DayTypePolicy.kt` 的 Python 移植。
**两边的行为必须完全一致**，否则同一份文件在手机和电脑上会得到不同的时间轴。

## 为什么需要它

引擎能算出**六种**日型：

    A          有早八的日子
    B_TRAIN_A  周二 · 力量A     ← 这两条来自原文档里的训练安排
    B_TRAIN_B  周四 · 力量B     ← 只对排了力量训练的人有意义
    B_NORMAL   没早八的日子
    SATURDAY / SUNDAY

但「周二练力量A」是**某一份具体规划表**里的安排，不是通用规律。
别人的课表里可能根本没有训练日，那两套模板就纯属噪声 ——
更要紧的是，它会让「周二到底该几点起」这件事变得难以回答。

所以把「引擎能算出什么」和「这套配置实际用哪些」拆成两层：

    natural_day_type()   纯日历规则 —— 今天第 1-2 节有没有课
    DayTypePolicy.resolve()   映射到这份配置真正启用的日型上

**这是很通用的一招：把「算得出什么」和「要用什么」拆开，
中间那层策略就成了可配置的。** 直接改计算逻辑做不到这点 ——
一旦写死就没有回旋余地了。

## 默认值为什么是这四个

`enabled = {A, B_NORMAL, SATURDAY, SUNDAY}`、`fallback = B_NORMAL`

效果是：

    有早八的日子     → A 型日，06:55 起床
    没早八的工作日   → B 型日，07:25 起床
    周末             → 周六 / 周日模板

也就是说 —— **「有早八 / 没早八」这个最核心的区分完整保留了**，
被裁掉的只有「力量A / 力量B」这个细分。

取舍的理由：「今天要不要早起」是这个程序存在的理由，不能动；
而训练日的细分只对少数人有意义，让它默认不出现、需要时再显式打开。

**默认值应该保留「所有人都需要的行为」，把「只有部分人需要的行为」变成可选。**
反过来做的话，大多数人会觉得这个软件在自顾自地替他们安排事情。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import DAY_TYPE_ORDER, DayType


@dataclass(frozen=True)
class DayTypePolicy:
    """
    哪几种日型真会被用到。

    frozen=True 是有意的：这个对象会被当作**值**传递（存进配置、序列化、
    在几处之间共享），可变对象在这里只会带来「谁把它改了」的追查成本。
    """

    #: 这份配置里**真正会被用到**的日型集合，不能为空
    enabled: frozenset[DayType]

    #: 算出来的日型不在 `enabled` 里时，改用哪一种
    fallback: DayType

    def resolve(self, computed: DayType) -> DayType:
        """
        把「日历算出来的日型」映射成「这次实际要用的日型」。

        只有一个分支，但它是整个策略的落点：
        启用就用原样的，没启用就退回 `fallback`。

        注意 `fallback` **不必**在 `enabled` 里 —— 它表示的是
        「用这套模板来兜底」，是一个独立的指定，不是「启用了一种日型」。
        手机版那边的注释把这条写成了设计决定之一，这里保持一致。
        """
        return computed if computed in self.enabled else self.fallback

    def as_dict(self) -> dict[str, object]:
        """给前端 / 本地存储用的普通 dict（顺序固定，见 models.DAY_TYPE_ORDER）"""
        return {
            "enabled": [t.value for t in DAY_TYPE_ORDER if t in self.enabled],
            "fallback": self.fallback.value,
        }


#: 不裁剪任何日型 —— 引擎算出什么就用什么。
#:
#: 测「原始日历规则」时用这个：它让 resolve() 变成恒等映射，
#: 于是测的就是 natural_day_type() 那条规则本身。
#: （手机版里叫 ALL，是同一个用途。）
ALL = DayTypePolicy(
    enabled=frozenset(DAY_TYPE_ORDER),
    fallback=DayType.B_NORMAL,
)

#: 默认策略（也是文件里没写 `dayTypes` 段时的回落值）。
#:
#: 只保留「有早八 / 没早八」这档最核心的区分，去掉训练日的细分。
#: 详见模块开头对默认值取舍的说明。
DEFAULT = DayTypePolicy(
    enabled=frozenset({
        DayType.A,
        DayType.B_NORMAL,
        DayType.SATURDAY,
        DayType.SUNDAY,
    }),
    fallback=DayType.B_NORMAL,
)

# ⚠️ 这两个也挂到类上（`DayTypePolicy.DEFAULT`），对齐手机版的
# `DayTypePolicy.Companion.DEFAULT` —— 那边它们就住在 companion object 里。
#
# 为什么不只留模块级？因为**调用方写起来更像「它属于这个类型」**：
# `DayTypePolicy.DEFAULT` 一眼看出是「这种策略的默认值」，
# 而裸的 `DEFAULT` 得回头翻 import 才知道是谁的默认值。
# 两种写法都留着，是因为模块级的那个在 `day_type_policy.ALL` 这种
# 明确限定的场合更短，而项目里两种用法都会有。
#
# 写法上不能在类体里直接构造自己（类还没建完），所以只能建完之后挂上去。
DayTypePolicy.ALL = ALL          # type: ignore[attr-defined]
DayTypePolicy.DEFAULT = DEFAULT  # type: ignore[attr-defined]
