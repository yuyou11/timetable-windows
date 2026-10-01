"""
作息模板与日型管理 —— 作息编辑页、设置页「星期怎么排」。

（由 `tools/split_api.py` 从 `api.py` 原样切出；v5 起日型可创建/删除、
星期几可映射、早八优先层可指定 —— 见 day_type_policy.py 的说明。）
"""

from __future__ import annotations

from typing import Any

from . import engine, format_spec, slots, wake_shift
from .day_type_policy import DayTypeConfig, MAX_DAY_TYPES
from .format_spec import WEEKDAY_CN
from .models import (
    DEFAULT_DAY_TYPE_NAMES,
    Block,
    DayType,
    Kind,
    day_type_name,
)
from .payloads import _block_dict, _parse_day_type, _valid_index


class TemplatesMixin:
    """作息模板与日型管理。"""

    # ============================================================
    #  作息模板编辑
    # ============================================================

    def get_templates(self) -> dict[str, Any]:
        """返回**现存**日型的格子（v5 起由 dayTypeConfig 决定有哪几个），
        并标出「哪些是用户改过的」"""
        custom = self.store.custom_templates()
        merged = self.store.templates()
        config = self.store.day_type_config()
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
                for t in config.types
            ],
        }

    def _managed_day_type(self, key: str) -> Any:
        """解析 key 并要求它**现存**。不现存返回 None（报错由调用方给）。

        模板编辑类接口都走这里而不是裸 _parse_day_type：
        已删除的日型不该再能被编辑 —— id 还在 1-7 里，但配置里没有它。
        """
        t = _parse_day_type(key)
        if t is None or not self.store.day_type_config().manages(t):
            return None
        return t

    def save_template_block(self, day_type: str, payload: dict[str, Any],
                            index: int = -1) -> dict[str, Any]:
        day_type_enum = self._managed_day_type(day_type)
        if day_type_enum is None:
            return {"ok": False, "message": f"未知的日型：{day_type}"}

        try:
            block = self._block_from_payload(payload)
        except ValueError as e:
            return {"ok": False, "message": str(e)}

        # 从「当前完整一套」出发做修改，再整份存成自定义模板。
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
        day_type_enum = self._managed_day_type(day_type)
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
        day_type_enum = self._managed_day_type(day_type)
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

    def save_template_blocks(self, day_type: str, blocks: list) -> dict[str, Any]:
        """
        整套替换某一日型的模板 —— 新一代作息编辑（时间轴 + 行内编辑）的落盘接口。

        前端的编辑会话在本地工作副本上自由改（拖拽、行内编辑、撤销），
        点「保存」时把整列一次性提交到这里。逐格的 save_template_block /
        delete_template_block 保留着（smoke 测试和旧路径还在用），
        但拖拽式编辑不能用它们 —— 每帧一次「落盘 + 通知提醒线程重排」
        既浪费也会让悬浮窗抽搐。

        校验与逐格版同一套规矩（排序、重叠报错、至少一格）；
        传入的顺序不重要，落盘前会按 start 排好。
        """
        day_type_enum = self._managed_day_type(day_type)
        if day_type_enum is None:
            return {"ok": False, "message": f"未知的日型：{day_type}"}
        if not isinstance(blocks, list):
            return {"ok": False, "message": "参数格式不对：blocks 要是一个数组"}

        try:
            new_blocks = [self._block_from_payload(b) for b in blocks]
        except ValueError as e:
            return {"ok": False, "message": str(e)}

        if not new_blocks:
            return {"ok": False, "message": "至少要留一格，否则这一天就没有作息了"}

        new_blocks.sort(key=lambda b: b.start)
        for prev, cur in zip(new_blocks, new_blocks[1:]):
            if cur.start < prev.end:
                return {
                    "ok": False,
                    "message": (
                        f"这两格时间重叠了：\n\n"
                        f"　{slots.fmt(prev.start)}–{slots.fmt(prev.end)}　{prev.title}\n"
                        f"　{slots.fmt(cur.start)}–{slots.fmt(cur.end)}　{cur.title}\n\n"
                        f"同一时刻只能有一件固定的事。"
                    ),
                }

        full = self.store.templates()
        full[day_type_enum] = new_blocks
        self.store.save_templates(full)
        return self._templates_ok(f"已保存「{self._type_label(day_type_enum)}」"
                                  f"（{len(new_blocks)} 格）")

    def copy_template(self, src: str, dst: str) -> dict[str, Any]:
        """
        整套复制：把 src 日型的模板原样复制到 dst（覆盖）。

        改作息的多数需求其实是「让某一天跟另一天差不多，再微调两格」——
        逐格重抄一遍既慢又容易抄错。复制是整体动作，所以放在后端做：
        一次落盘、一套校验，前端拿到的是和手改完全一致的结果。
        """
        src_enum = self._managed_day_type(src)
        dst_enum = self._managed_day_type(dst)
        if src_enum is None:
            return {"ok": False, "message": f"未知的日型：{src}"}
        if dst_enum is None:
            return {"ok": False, "message": f"未知的日型：{dst}"}
        if src_enum is dst_enum:
            return {"ok": False, "message": "源和目标是同一个日型 —— 不需要复制"}

        full = self.store.templates()
        full[dst_enum] = [b.copy() for b in full[src_enum]]
        self.store.save_templates(full)
        return self._templates_ok(
            f"已复制：{self._type_label(src_enum)} → {self._type_label(dst_enum)}"
            f"（{len(full[dst_enum])} 格）"
        )

    def _type_label(self, day_type: DayType) -> str:
        """当前生效的日型名（自定义名优先）—— 报错和成功消息都用它"""
        return day_type_name(day_type, self.store.day_type_names)

    def reset_templates(self) -> dict[str, Any]:
        self.store.reset_templates()
        return self._templates_ok("已恢复内置作息模板")

    # ============================================================
    #  日型管理（v5）：创建 / 删除 / 编辑 / 星期映射 / 早八优先层
    # ============================================================

    def get_day_types(self) -> dict[str, Any]:
        """
        日型系统的现状：现存哪些日型、各管星期几、谁是早八日型。

        给设置页「星期怎么排」和日型编辑卡片用。每行带**起床时间**
        （用户配置时得看得出代价）和 weekdays（它管哪几天，勾选框的
        现状）；rest 行 locked=True（自动判定层，删不掉、不参与映射）。
        """
        config = self.store.day_type_config()
        templates = self.store.templates()
        names = self.store.day_type_names

        types = []
        for t in config.types:
            w = engine.wake_minute(templates[t])
            types.append({
                "key": t.value,
                "label": day_type_name(t, names),
                # 默认名要单独给：编辑卡片的「清空 = 恢复默认名」
                # 需要显示恢复成什么，而 label 已经是改过的名字
                "defaultName": DEFAULT_DAY_TYPE_NAMES[t],
                "wake": slots.fmt(w) if w is not None else None,
                "locked": t is DayType.REST,
                "weekdays": config.weekdays_of(t),
                "isEarly": config.early_type is t,
                "isDefaultName": t not in names,
            })

        return {
            "types": types,
            "earlyType": config.early_type.value if config.early_type else "",
            # 还没被占用的 id —— 新建卡片用它判断「还能不能建」
            "freeIds": [str(i) for i in range(1, MAX_DAY_TYPES + 1)
                        if DayType(str(i)) not in config.types],
            "hasCustomDayTypeConfig": self.store.has_custom_day_type_config,
            "hasCustomDayTypeNames": self.store.has_custom_day_type_names,
        }

    def create_day_type(self, name: str) -> dict[str, Any]:
        """
        新建日型：分配最小的空闲 id，作息从**最早一支现存日型**整套复制
        当起点（空白的一格都排不出来，复制来的总比从零拖快）。

        星期映射**不自动指向新日型** —— 管哪几天由用户在卡片里勾，
        默认它谁都不管（不勾的话它只是躺在列表里的一套模板）。
        """
        config = self.store.day_type_config()

        text = str(name or "").strip()
        if not text:
            return {"ok": False, "message": "日型名不能为空"}
        if len(text) > format_spec.DAY_TYPE_NAME_MAX:
            return {"ok": False,
                    "message": f"日型名最多 {format_spec.DAY_TYPE_NAME_MAX} 个字"
                               f"（要显示在今天页和悬浮窗上）：「{text}」"}

        free = [i for i in range(1, MAX_DAY_TYPES + 1)
                if DayType(str(i)) not in config.types]
        if not free:
            return {"ok": False,
                    "message": f"日型已经满 {MAX_DAY_TYPES} 种了 —— 先删掉一个不用的，再建新的"}

        new_id = DayType(str(free[0]))
        # 名字进稀疏表：新建的日型没有「默认名」可言
        names = self.store.day_type_names
        names[new_id] = text

        # 作息起点：最早一支现存日型（types 按 id 升序，第一个就是）
        full = self.store.templates()
        full[new_id] = [b.copy() for b in full[config.types[0]]]

        new_config = DayTypeConfig(
            types=tuple(sorted(config.types + (new_id,), key=lambda t: int(t.value))),
            weekday_map=config.weekday_map,
            early_type=config.early_type,
        )
        self.store.save_day_type_config(new_config)
        self.store.day_type_names = names
        self.store.save_templates(full)
        self._notify_settings_changed()
        return {
            "ok": True,
            "message": f"已创建「{text}」（id {new_id.value}），"
                       f"作息复制自「{self._type_label(config.types[0])}」，"
                       "记得在下面勾选它管哪几天",
            "newId": new_id.value,
            "dayTypes": self.get_day_types(),
            "templates": self.get_templates(),
            "settings": self._settings_dict(),
        }

    def delete_day_type(self, key: str) -> dict[str, Any]:
        """
        删除日型。三道守卫，每一条的报错都告诉用户「该怎么办」：

        · 休息日（id 7）是引擎的自动判定层，删不掉；
        · 还管着星期几的不许删 —— 否则那几天就没有模板可用了；
        · 还被指定为早八日型的不许删 —— 早八优先层会悬空。
        """
        t = _parse_day_type(str(key))
        if t is None:
            return {"ok": False, "message": f"未知的日型：{key}"}

        config = self.store.day_type_config()
        if t is DayType.REST:
            return {"ok": False,
                    "message": "休息日（id 7）删不掉 —— 它由引擎自动判定"
                               "（工作日全天没课的那天），不是一份可以删的配置"}
        if not config.manages(t):
            return {"ok": False, "message": "这个日型不存在（可能已经被删了）"}

        label = self._type_label(t)
        days = config.weekdays_of(t)
        if days:
            day_names = "、".join(WEEKDAY_CN[d] for d in days)
            return {"ok": False,
                    "message": f"「{label}」还管着 {day_names} —— 删掉的话这几天"
                               f"就没有模板可用了。\n先在编辑卡片里把"
                               f"这些星期改派给别的日型，再来删它。"}
        if config.early_type is t:
            return {"ok": False,
                    "message": f"「{label}」还是「早八日型」—— 删掉的话有早八的"
                               f"日子就没有模板可用了。\n先在「星期怎么排」里把"
                               f"早八日型换成别的，再来删它。"}

        new_config = DayTypeConfig(
            types=tuple(x for x in config.types if x is not t),
            weekday_map=config.weekday_map,
            early_type=config.early_type,
        )
        self.store.save_day_type_config(new_config)
        # 它在存储里的自定义模板一并清掉 —— id 之后可能被新建复用，
        # 留着旧模板会冒出来冒充新日型的「起点」
        custom = self.store.custom_templates()
        if t in custom:
            del custom[t]
            self.store.save_templates(custom)
        self._notify_settings_changed()
        return {
            "ok": True,
            "message": f"已删除「{label}」",
            "dayTypes": self.get_day_types(),
            "templates": self.get_templates(),
            "settings": self._settings_dict(),
        }

    def update_day_type(self, key: str, props: dict) -> dict[str, Any]:
        """
        编辑现存日型的基础属性：名字（可空 = 保持不变）+ 管星期几。

        星期映射是**全局唯一**的：把周二勾给这个日型，原来管周二的那个
        就自动让出周二（每个星期几只能有一种模板）。卡片上会说明这一点。
        """
        t = _parse_day_type(str(key))
        if t is None:
            return {"ok": False, "message": f"未知的日型：{key}"}
        config = self.store.day_type_config()
        if not config.manages(t):
            return {"ok": False, "message": "这个日型不存在（可能已经被删了）"}
        if not isinstance(props, dict):
            return {"ok": False, "message": "参数格式不对"}

        new_config = config

        # ---- 名字（可选改；字段省略 = 不改）----
        #
        # 语义：非空串 = 改名；**空串 = 恢复默认名**（从稀疏表里删掉）。
        # 改名的校验（非空、长度）在这里，原来在 save_day_type_names ——
        # 那个桥接已删：UI 改名全部走这里，不留第二条路。
        if "name" in props:
            text = str(props.get("name") or "").strip()
            if text and len(text) > format_spec.DAY_TYPE_NAME_MAX:
                return {"ok": False,
                        "message": f"日型名最多 {format_spec.DAY_TYPE_NAME_MAX} 个字"
                                   f"（要显示在今天页和悬浮窗上）：「{text}」"}
            names = self.store.day_type_names
            if text:
                names[t] = text
            else:
                names.pop(t, None)          # 恢复默认名
            self.store.day_type_names = names

        # ---- 管星期几（可选改；休息日不参与映射，字段会被忽略）----
        if "weekdays" in props and t is not DayType.REST:
            raw_days = props.get("weekdays")
            if not isinstance(raw_days, list):
                return {"ok": False, "message": "weekdays 要是星期几的数组（1=周一 … 7=周日）"}
            days: list[int] = []
            for raw in raw_days:
                try:
                    dow = int(raw)
                except (TypeError, ValueError):
                    return {"ok": False, "message": f"星期几写的是「{raw}」，"
                                                    f"要是 1-7 的数字（1=周一）"}
                if not 1 <= dow <= 7:
                    return {"ok": False,
                            "message": f"星期几 {dow} 超出范围（1=周一 … 7=周日）"}
                if dow not in days:
                    days.append(dow)
            new_map = list(config.weekday_map)
            for dow in days:
                new_map[dow - 1] = t
            new_config = DayTypeConfig(types=config.types,
                                       weekday_map=tuple(new_map),
                                       early_type=config.early_type)

        if new_config is not config:
            self.store.save_day_type_config(new_config)
        self._notify_settings_changed()
        return {
            "ok": True,
            "message": f"已保存「{self._type_label(t)}」",
            "dayTypes": self.get_day_types(),
            "templates": self.get_templates(),
            "settings": self._settings_dict(),
        }

    def save_weekday_map(self, mapping: list) -> dict[str, Any]:
        """
        保存星期映射：7 项 id 字符串（周一..周日），每项必须是现存的
        非 REST 日型。「星期怎么排」卡片里那七个下拉框的落点。
        """
        config = self.store.day_type_config()
        if not isinstance(mapping, list) or len(mapping) != 7:
            return {"ok": False,
                    "message": "参数格式不对：映射要是恰好 7 项（从周一到周日）"}

        new_map: list[DayType] = []
        for i, raw in enumerate(mapping):
            t = _parse_day_type(str(raw))
            if t is None or not config.manages(t):
                return {"ok": False,
                        "message": f"{WEEKDAY_CN[i + 1]}映射到的日型「{raw}」不存在"}
            if t is DayType.REST:
                return {"ok": False,
                        "message": f"{WEEKDAY_CN[i + 1]}不能映射到休息日（id 7）—— "
                                   "它由引擎自动判定，不参与星期映射"}
            new_map.append(t)

        self.store.save_day_type_config(DayTypeConfig(
            types=config.types, weekday_map=tuple(new_map),
            early_type=config.early_type,
        ))
        # 必须通知：提醒线程要按新的映射重排 —— 哪天用哪套变了，
        # 「下一个切换时刻」就变了。
        self._notify_settings_changed()
        return self._day_types_ok("已保存星期映射")

    def save_early_type(self, key: str) -> dict[str, Any]:
        """
        指定「早八日型」：工作日第 1-2 节有课时优先用它。
        传空串 = 不区分早八（有早八的工作日也按星期映射走）。
        """
        config = self.store.day_type_config()
        text = str(key or "").strip()
        early: Any = None
        if text:
            t = _parse_day_type(text)
            if t is None or not config.manages(t):
                return {"ok": False, "message": f"未知的日型：{key}"}
            if t is DayType.REST:
                return {"ok": False,
                        "message": "休息日（id 7）当不了早八日型 —— 它本身由"
                                   "「工作日全天没课」触发，两个自动层不能叠在一起"}
            early = t

        self.store.save_day_type_config(DayTypeConfig(
            types=config.types, weekday_map=config.weekday_map, early_type=early,
        ))
        self._notify_settings_changed()
        msg = ("已取消早八区分 —— 有早八的工作日也按星期映射走"
               if early is None else
               f"已设置早八日型：「{self._type_label(early)}」")
        return self._day_types_ok(msg)

    def reset_day_types(self) -> dict[str, Any]:
        """恢复默认：日型列表/星期映射/早八层全部回到出厂，日型名也还原"""
        self.store.reset_day_type_config()
        self.store.reset_day_type_names()
        self._notify_settings_changed()
        return self._day_types_ok("已恢复默认日型")

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
