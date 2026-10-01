"""
日型配置 —— 「哪几种日型存在、星期几用哪种、有早八优先用哪种」。

## v5：DayTypePolicy → DayTypeConfig

日型可创建/删除、星期可映射之后，旧的「启用集合 + 回落」模型
（DayTypePolicy）从运行时退役，换成三样东西：

    types        现存的日型 id（**必含休息日 "7"**，最多 7 个）
    weekday_map  周一..周日 各映射到哪种日型（恰好 7 项）
    early_type   工作日有早八时优先用的日型；None = 不区分早八

引擎判定顺序（engine.resolve_day_type）：

    ① 工作日全天无课              → 休息日（客观事实，最高优先）
    ② 工作日有早八且指定了早八型   → early_type
    ③ 其余                        → weekday_map[星期几]

休息日不参与 weekday_map 和 early_type —— 它只由「工作日全天没课」
这一条客观事实触发，跟星期映射是两个层面。

## DayTypePolicy 为什么还在

v3/v4 文件的 dayTypes 段和旧 data.json 的 day_types 键还在用它。
读进来之后由 config_from_policy() **确定性迁移**成 DayTypeConfig，
迁移结果与旧程序的默认行为逐日一致（见那边的对照表）。
它不再参与任何运行时判定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import DAY_TYPE_ORDER, DayType

#: 日型数量上限（id 空间 "1"-"7"，其中 "7" 恒为休息日）
MAX_DAY_TYPES = 7


@dataclass(frozen=True)
class DayTypeConfig:
    """
    一份配置里「日型系统」的全貌。

    frozen=True：和 DayTypePolicy 同一个理由 —— 它被当作**值**在
    存储序列化、引擎、界面之间传来传去，可变对象只会带来
    「谁把它改了」的追查成本。
    """

    #: 现存的日型 id 集合（按 id 升序）。**必须含 REST** —— 休息日
    #: 是引擎的自动判定层，不存在「删掉休息日」这个操作。
    types: tuple[DayType, ...]

    #: 周一..周日 各映射到哪种日型。恰好 7 项；每一项都必须 ∈ types
    #: 且不能是 REST（休息日不走映射）。
    weekday_map: tuple[DayType, ...]

    #: 工作日有早八（第 1-2 节有课）时优先用的日型。
    #: None = 不区分早八 —— 有早八的工作日也按星期映射走。
    early_type: Optional[DayType]

    def manages(self, day_type: DayType) -> bool:
        """这个 id 是否现存（删除守卫 / 编辑入口都用它）"""
        return day_type in self.types

    def weekdays_of(self, day_type: DayType) -> list[int]:
        """这个日型管哪些星期几（1=周一 … 7=周日），升序"""
        return [dow for dow, t in enumerate(self.weekday_map, start=1) if t is day_type]


#: 旧「自然规则」里每个星期几对应的槽位 —— config_from_policy 的迁移依据。
#: （v4 及更早：周二 → 训练日A、周四 → 训练日B，其余工作日 → 普通日。）
_LEGACY_NATURAL_SLOT = {
    1: DayType.B_NORMAL,
    2: DayType.B_TRAIN_A,
    3: DayType.B_NORMAL,
    4: DayType.B_TRAIN_B,
    5: DayType.B_NORMAL,
    6: DayType.SATURDAY,
    7: DayType.SUNDAY,
}


def config_from_policy(policy: "DayTypePolicy") -> DayTypeConfig:
    """
    把旧 DayTypePolicy **确定性迁移**成 DayTypeConfig。

    规则：每个星期几先落到旧自然槽位（周二→训练日A…），槽位在
    enabled 里就用槽位，不在就用 fallback —— 这正是旧 resolve()
    对那一天给出的答案，所以迁移前后**逐日等价**。
    早八优先层同理：A 启用则早八用 A，否则用 fallback。
    """
    def slot(dow: int) -> DayType:
        natural = _LEGACY_NATURAL_SLOT[dow]
        return natural if natural in policy.enabled else policy.fallback

    early = DayType.A if DayType.A in policy.enabled else policy.fallback
    return DayTypeConfig(
        types=tuple(DAY_TYPE_ORDER) + (DayType.REST,),
        weekday_map=tuple(slot(dow) for dow in range(1, 8)),
        early_type=early,
    )


# ============================================================
#  以下为 v3/v4 兼容构件 —— 不参与运行时判定
# ============================================================

@dataclass(frozen=True)
class DayTypePolicy:
    """
    【已退役】旧模型：「启用哪几种 + 没启用时回落到哪种」。

    还活着的原因只有一个：v3/v4 文件的 dayTypes 段和旧 data.json
    仍在用它表达配置，解析后立刻经 config_from_policy 迁移。
    新代码请用 DayTypeConfig。
    """

    #: 这份配置里**真正会被用到**的日型集合，不能为空
    enabled: frozenset[DayType]

    #: 算出来的日型不在 `enabled` 里时，改用哪一种
    #: （允许不在 enabled 里 —— 「用这套模板兜底」是独立指定）
    fallback: DayType

    def resolve(self, computed: DayType) -> DayType:
        """旧引擎的映射落点：启用就用原样，没启用退回 fallback"""
        return computed if computed in self.enabled else self.fallback

    def as_dict(self) -> dict[str, object]:
        """给前端 / 本地存储用的普通 dict（顺序固定，见 models.DAY_TYPE_ORDER）"""
        return {
            "enabled": [t.value for t in DAY_TYPE_ORDER if t in self.enabled],
            "fallback": self.fallback.value,
        }


#: 【已退役】不裁剪任何日型 —— 引擎算出什么就用什么。
#: 只剩一个用途：测「v3 旧规则本身」时当恒等映射。
ALL = DayTypePolicy(
    enabled=frozenset(DAY_TYPE_ORDER),
    fallback=DayType.B_NORMAL,
)

#: 【已退役】旧默认策略（A + 没早八的 B + 周末，训练日不启用）。
#: 新模型的默认值在 DayTypeConfig.DEFAULT；这个对象只服务旧文件解析。
DEFAULT = DayTypePolicy(
    enabled=frozenset({
        DayType.A,
        DayType.B_NORMAL,
        DayType.SATURDAY,
        DayType.SUNDAY,
    }),
    fallback=DayType.B_NORMAL,
)

# 挂到类上对齐手机版 Companion 的写法（历史约定，见 git 历史）
DayTypePolicy.ALL = ALL          # type: ignore[attr-defined]
DayTypePolicy.DEFAULT = DEFAULT  # type: ignore[attr-defined]


#: 新模型的默认配置 —— 与 DayTypePolicy.DEFAULT 的行为**逐日一致**：
#: 有早八 → A 型日；其余工作日 → B 型日（训练日默认不启用，效果上
#: 周二/周四也走 B 型日）；周六/周日各自的模板。
#: 对照：config_from_policy(DayTypePolicy.DEFAULT) 的结果就是这个。
def _default_config() -> DayTypeConfig:
    return DayTypeConfig(
        types=tuple(DAY_TYPE_ORDER) + (DayType.REST,),
        weekday_map=(
            DayType.B_NORMAL, DayType.B_NORMAL, DayType.B_NORMAL,
            DayType.B_NORMAL, DayType.B_NORMAL,
            DayType.SATURDAY, DayType.SUNDAY,
        ),
        early_type=DayType.A,
    )


DEFAULT_CONFIG = _default_config()
DayTypeConfig.DEFAULT = DEFAULT_CONFIG  # type: ignore[attr-defined]
