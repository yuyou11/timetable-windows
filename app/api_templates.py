"""
作息模板与日型策略 —— 作息编辑页、设置页的日型勾选/改名。

（由 `tools/split_api.py` 从 `api.py` 原样切出；v4 起日型带 id 1-7、
名字可自定义 —— 见 models.py 的说明。）
"""

from __future__ import annotations

from typing import Any

from . import engine, format_spec, slots, wake_shift
from .day_type_policy import DayTypePolicy
from .models import (
    DAY_TYPE_ORDER,
    Block,
    DayType,
    Kind,
    day_type_name,
)
from .payloads import _block_dict, _parse_day_type, _valid_index

#: 界面上出现的全部日型：六个可启用的（id 升序）+ 休息日（id 7）。
#: 作息编辑页的标签、设置页的列表都按这个顺序排 —— 休息日排在最后，
#: 因为它是「自动判定」的那一个，和前六个「用户勾选启用」的不是一类。
TEMPLATE_TYPES = DAY_TYPE_ORDER + [DayType.REST]


class TemplatesMixin:
    """作息模板与日型策略 —— 作息编辑页、设置页的日型勾选/改名。"""

    # ============================================================
    #  作息模板编辑
    # ============================================================

    def get_templates(self) -> dict[str, Any]:
        """返回七种日型的格子（v4 起含休息日），并标出「哪些是用户改过的」"""
        custom = self.store.custom_templates()
        merged = self.store.templates()
        names = self.store.day_type_names
        return {
            "customized": [t.value for t in custom],
            "types": [
                {
                    "key": t.value,
                    "label": day_type_name(t, names),
                    "isCustom": t in custom,
                    "wake": (
                        slots.fmt(w) if (w := engine.wake_minute(merged[t])) is not None else None
                    ),
                    "blocks": [_block_dict(b) for b in merged[t]],
                }
                for t in TEMPLATE_TYPES
            ],
        }

    def save_template_block(self, day_type: str, payload: dict[str, Any],
                            index: int = -1) -> dict[str, Any]:
        day_type_enum = _parse_day_type(day_type)
        if day_type_enum is None:
            return {"ok": False, "message": f"未知的日型：{day_type}"}

        try:
            block = self._block_from_payload(payload)
        except ValueError as e:
            return {"ok": False, "message": str(e)}

        # 从「当前的完整六套」出发做修改，再整份存成自定义模板。
        # 这样即使用户只改了一格，存下来的也是完整的一套 ——
        # 避免「改了一格，其余几格悄悄回落到内置」这种难以察觉的行为。
        full = self.store.templates()
        blocks = list(full[day_type_enum])

        if index < 0:
            blocks.append(block)
        elif _valid_index(index, blocks):
            blocks[index] = block
        else:
            return {"ok": False, "message": "这一格已经不存在了，列表可能已经刷新"}

        blocks.sort(key=lambda b: b.start)

        # 重叠必须拦下来：引擎遇到重叠会「先到先得」把后一格静默截断，
        # 用户写的某一格就这么没了，界面上还看不出来。
        for prev, cur in zip(blocks, blocks[1:]):
            if cur.start < prev.end:
                return {
                    "ok": False,
                    "message": (
                        f"这两格时间重叠了：\n\n"
                        f"　{slots.fmt(prev.start)}–{slots.fmt(prev.end)}　{prev.title}\n"
                        f"　{slots.fmt(cur.start)}–{slots.fmt(cur.end)}　{cur.title}\n\n"
                        f"同一时刻只能有一件固定的事。\n"
                        f"如果你的本意是改起床时间，注意「睡觉」和后面的「起床、洗漱」"
                        f"是两格，两格都要改。"
                    ),
                }

        full[day_type_enum] = blocks
        self.store.save_templates(full)
        return self._templates_ok("已保存")

    def delete_template_block(self, day_type: str, index: int) -> dict[str, Any]:
        day_type_enum = _parse_day_type(day_type)
        if day_type_enum is None:
            return {"ok": False, "message": f"未知的日型：{day_type}"}

        full = self.store.templates()
        blocks = list(full[day_type_enum])
        if not _valid_index(index, blocks):
            return {"ok": False, "message": "这一格已经不存在了"}
        if len(blocks) <= 1:
            return {"ok": False, "message": "至少要留一格，否则这一天就没有作息了"}

        removed = blocks.pop(index)
        full[day_type_enum] = blocks
        self.store.save_templates(full)
        return self._templates_ok(f"已删除「{removed.title}」")

    def shift_wake_time(self, day_type: str, new_time: str) -> dict[str, Any]:
        """
        改起床时间 —— 一个动作改两格。算法和「为什么是这样」在 `wake_shift.py`。

        这里只负责桥接：把前端参数翻译成 `wake_shift.apply` 要的入参，
        把结果翻译成前端要的 dict，并管好写盘和通知。
        """
        day_type_enum = _parse_day_type(day_type)
        if day_type_enum is None:
            return {"ok": False, "message": f"未知的日型：{day_type}"}

        target = slots.parse_hhmm(new_time.strip(), is_end=False)
        if target is None:
            return {"ok": False, "message": f'时间 "{new_time}" 格式不对，应该是 "07:30" 这样'}

        full = self.store.templates()
        result = wake_shift.apply(full[day_type_enum], target)

        if not result.ok:
            return {"ok": False, "message": result.message}

        if result.unchanged:
            # 什么都没改，所以**不写盘、也不通知** —— 和改动前的行为一致。
            # （这里刻意不用 _templates_ok：它会顺手通知一次。）
            return {"ok": True, "message": result.message,
                    "templates": self.get_templates()}

        full[day_type_enum] = result.blocks
        self.store.save_templates(full)
        # 通知只由 _templates_ok 发一次。原来这里还会先通知一次（一次操作
        # 两轮「存盘 + 推送 + 刷新」），是历史遗留，已经收敛 ——
        # 别的模板方法从来都是只通知一次的。
        return self._templates_ok(result.message)

    def reset_templates(self) -> dict[str, Any]:
        self.store.reset_templates()
        return self._templates_ok("已恢复内置作息模板")

    # ============================================================
    #  日型策略（v3）+ 自定义日型名（v4）
    # ============================================================

    def get_day_types(self) -> dict[str, Any]:
        """
        日型策略的现状，给设置页那几个复选框用。

        返回里带上**每个日型的起床时间** —— 界面要在选项旁边显示它。
        没有这个信息的话，用户勾选时看不出代价：
        「把工作日全勾成 A 型」意味着没早八的日子也 06:55 起床，
        而那正是这个程序最该帮人避免的事。

        v4 的两处扩充：
        · 列表含第 7 行（休息日），带 locked=True —— 前端渲染成
          「自动生效、没有勾选框」的一行，改名入口照常可用；
        · 每行带 isDefaultName，前端据此显示「已改名」的状态。
        """
        policy = self.store.day_type_policy()
        templates = self.store.templates()
        names = self.store.day_type_names

        types = []
        for t in TEMPLATE_TYPES:
            w = engine.wake_minute(templates[t])
            types.append({
                "key": t.value,
                "label": day_type_name(t, names),
                "wake": slots.fmt(w) if w is not None else None,
                "enabled": (t in policy.enabled) or t is DayType.REST,
                "isFallback": t is policy.fallback,
                "isDefaultName": t not in names,
                "locked": t is DayType.REST,
            })

        return {
            "types": types,
            "fallback": policy.fallback.value,
            "hasCustomDayTypes": self.store.has_custom_day_types,
            "hasCustomDayTypeNames": self.store.has_custom_day_type_names,
        }

    def save_day_types(self, enabled: list, fallback: str) -> dict[str, Any]:
        """
        保存勾选结果。

        ## 两道校验，都是为了挡住「看不出来但很糟糕」的配置

        **① `enabled` 不能为空。** 那样每天都会落到 fallback 上，
        等于把整套日型系统废掉 —— 几乎不可能是本意，多半是手滑全取消了。
        格式标准里这一条也是**报错**而不是静默接受。

        **② `fallback` 必须在 `enabled` 里。**

        ⚠️ 这一条**和格式标准相反**，是刻意的：

            文件里      fallback 允许不在 enabled 里（那是正当用法，
                        "只启用 A 和周末，但周中没早八时回落成 B 型"）
            界面上      这里只能从勾选项里选，因为这是一个下拉框 ——
                        让用户在下拉里选一个**没勾选**的日型，
                        等于给了一个自相矛盾的控件

        界面的约束比文件的约束**更紧**：文件要容纳所有合法写法，
        而界面应该只呈现能自洽的组合。用户真需要那种配置，
        导入一份文件即可，那个入口一直开着。
        """
        # 先做校验，再把 key 转成枚举
        chosen: list[DayType] = []
        for raw in enabled or []:
            t = _parse_day_type(str(raw))
            if t is None:
                return {"ok": False, "message": f"未知的日型：{raw}"}
            if t is DayType.REST:
                return {"ok": False, "message": "休息日（id 7）由引擎自动判定、始终生效，"
                                                "不需要也不可以在这里勾选"}
            if t not in chosen:
                chosen.append(t)

        if not chosen:
            return {
                "ok": False,
                "message": "至少要启用一种日型。\n"
                           "全都取消的话，每一天都会落到「回落到」那一种上，"
                           "等于这个设置没有意义。",
            }

        fb = _parse_day_type(str(fallback))
        if fb is None:
            return {"ok": False, "message": f"未知的回落日型：{fallback}"}
        if fb is DayType.REST:
            return {"ok": False, "message": "休息日（id 7）由引擎自动判定，永远兜不了底 —— "
                                            "回落目标请从启用的日型里选"}
        if fb not in chosen:
            return {
                "ok": False,
                "message": f"「回落到」选的是 {day_type_name(fb, self.store.day_type_names)}，"
                           "但它没有被勾选。\n"
                           "回落的含义是「算出来的日型没启用时，改用哪一种」——"
                           "所以它自己得是启用的。\n"
                           "（如果你确实需要「启用 A 型和周末、却回落到 B 型」这种配置，"
                           "可以导入一份带 dayTypes 段的文件，文件里允许这样写。）",
            }

        self.store.save_day_type_policy(
            DayTypePolicy(enabled=frozenset(chosen), fallback=fb)
        )
        # 必须通知：提醒线程要按新的日型重排 —— 起床时间一改，
        # 「下一个切换时刻」就变了。漏掉这一步不会报错，只是闹钟停在旧时间上。
        self._notify_settings_changed()
        return self._day_types_ok(f"已启用 {len(chosen)} 种日型")

    def save_day_type_names(self, names: dict) -> dict[str, Any]:
        """
        保存自定义日型名（v4）。`names` 形如 {"1": "早八日", "3": "力量日"}。

        · 键必须是 "1"-"7"；值去掉首尾空白后非空、不超过 20 字；
        · 和默认名相同的值会被 store 的 setter 剔除（稀疏存储只记改过的）；
        · 传空对象 = 全部恢复默认名。

        改名是**纯展示层**的事 —— 引擎按 id 路由，名字变了不影响
        哪天用哪套模板，所以不用单独 kick 提醒线程；但今天页/悬浮窗
        显示的标签要跟着变，通知一次让各窗口刷新。
        """
        if not isinstance(names, dict):
            return {"ok": False, "message": "参数格式不对：要的是一个 {日型id: 名字} 对象"}

        clean: dict[DayType, str] = {}
        for key, value in names.items():
            t = _parse_day_type(str(key))
            if t is None:
                return {"ok": False, "message": f"未知的日型 id：{key}"}
            if not isinstance(value, str):
                return {"ok": False, "message": f'日型 "{key}" 的名字必须是字符串'}
            text = value.strip()
            if not text:
                return {"ok": False,
                        "message": f'日型 "{key}" 的名字不能为空 —— 想恢复默认名就把这个键删掉'}
            if len(text) > format_spec.DAY_TYPE_NAME_MAX:
                return {"ok": False,
                        "message": f"日型名最多 {format_spec.DAY_TYPE_NAME_MAX} 个字"
                                   f"（要显示在今天页和悬浮窗上）：「{text}」"}
            clean[t] = text

        self.store.day_type_names = clean
        self._notify_settings_changed()
        count = len(self.store.day_type_names)
        return self._day_types_ok(
            "已恢复全部默认名" if count == 0 else f"已保存 {count} 个自定义日型名"
        )

    def reset_day_types(self) -> dict[str, Any]:
        """恢复默认：策略回落到 A + 没早八的 B + 周末，日型名也全部还原"""
        self.store.reset_day_type_policy()
        self.store.reset_day_type_names()
        self._notify_settings_changed()
        return self._day_types_ok("已恢复默认日型（策略和名字）")

    def _day_types_ok(self, message: str) -> dict[str, Any]:
        """
        保存成功后的统一回包。

        和 `_templates_ok` 一样，**先通知再取数据** —— 反过来的话
        回给前端的可能不是通知之后的状态。
        """
        return {
            "ok": True,
            "message": message,
            "dayTypes": self.get_day_types(),
            "settings": self._settings_dict(),
        }

    def _block_from_payload(self, p: dict[str, Any]) -> Block:
        title = str(p.get("title", "")).strip()
        if not title:
            raise ValueError("这一格要显示什么？不能为空")

        start = slots.parse_hhmm(str(p.get("start", "")).strip(), is_end=False)
        if start is None:
            raise ValueError('开始时刻格式不对，应该是 "06:55" 这样')

        end = slots.parse_hhmm(str(p.get("end", "")).strip(), is_end=True)
        if end is None:
            raise ValueError('结束时刻格式不对，应该是 "07:10" 这样（一天最后一段可以写 "24:00"）')

        if end <= start:
            raise ValueError("结束时刻必须晚于开始时刻")

        try:
            kind = Kind(str(p.get("kind", "CHORE")).upper())
        except ValueError:
            raise ValueError(f"时段性质无法识别：{p.get('kind')}") from None

        nodes = None
        raw_nodes = p.get("nodes")
        if raw_nodes:
            limit = self.store.node_count
            try:
                a, b = int(raw_nodes[0]), int(raw_nodes[1])
            except (TypeError, ValueError, IndexError):
                raise ValueError(f"课表占位格必须写成 1 到 {limit} 之间的两个节次") from None
            if not (1 <= a <= limit and 1 <= b <= limit):
                raise ValueError(f"节次必须在 1–{limit} 之间（这所学校一天 {limit} 节）")
            if a > b:
                raise ValueError("起始节次不能比结束节次大")
            nodes = (a, b)

        return Block(start=start, end=end, title=title,
                     note=str(p.get("note", "")).strip(), kind=kind, nodes=nodes)
