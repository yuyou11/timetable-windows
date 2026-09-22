"""
本地存储。Android 版 Store.kt 的对应物，但实现完全不同。

## 手机版用 SharedPreferences，电脑版用 JSON 文件

这不是随意选择。Android 的 SharedPreferences 是平台提供的键值存储，
进程随时可能被系统杀掉、重启，所以必须用系统托管的东西。
而桌面程序的进程是我们自己管的，**一个普通文件就够了**，
而且用文件还有个额外好处：用户能直接打开看、能备份、能同步。

**同样是「存设置」，不同平台的最优解不一样 —— 别把一端的方案无脑搬过去。**

## 文件位置

    %APPDATA%\\Timetable\\data.json

放这里而不是程序目录，是因为：
  · 程序可能装在 Program Files（没有写权限）
  · 打包成单文件 exe 时，程序目录是临时的，重启就没了
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Optional

from . import builtin_data, dock, format_spec
from .day_type_policy import DayTypePolicy
from .format_spec import FormatError
from .models import Block, Course, DayType


def data_dir() -> Path:
    """数据目录。可以用环境变量覆盖，方便测试和绿色版"""
    override = os.environ.get("TIMETABLE_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Timetable"


class Store:
    """
    设置 + 课表 + 模板。

    整体读进内存、改完整体写回。数据量很小（几十 KB），
    每次改动就写一次文件，简单可靠，不用搞增量更新。
    """

    #: ⚠️ 这一行看着莫名其妙，但**删了程序就会崩**。
    #:
    #: pywebview 在创建窗口时会**递归遍历 js_api 对象上的所有公开属性**，
    #: 找出需要暴露给 JavaScript 的方法。遍历规则见 webview/util.py：
    #: 下划线开头的跳过；带 `_serializable = False` 的跳过；其余全部钻进去。
    #:
    #: 如果它钻进 Store（或任何持有原生对象的属性），轻则浪费时间，
    #: 重则——比如走到 pywebview 自己的 Window.native.AccessibilityObject——
    #: **无限递归把 C 栈撑爆，整个进程硬崩溃**，而且 Python 连异常都记不下来。
    #:
    #: 加这一行，就是告诉扫描器「别往里走」。
    _serializable = False

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (data_dir() / "data.json")
        self._data: dict[str, Any] = {}
        self.load()

    # ---------------- 读写 ----------------

    def load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(self._data, dict):
                    self._data = {}
            except (json.JSONDecodeError, OSError):
                # 文件坏了不能让程序起不来 —— 备份一份再从默认值开始，
                # 用户至少还有机会手工抢救原来的数据
                self._backup_broken_file()
                self._data = {}
        else:
            self._data = {}

    def _backup_broken_file(self) -> None:
        try:
            broken = self.path.with_suffix(".broken.json")
            self.path.replace(broken)
        except OSError:
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再替换：这样即使写到一半断电，也不会毁掉原文件。
        # 这个手法在任何「覆盖重要文件」的场景都适用。
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    # ---------------- 首次启动 ----------------

    @property
    def is_first_launch(self) -> bool:
        """
        判断要同时看两个条件：

          · first_launch_done 没写过 —— 说明没走过首次向导
          · courses 键不存在     —— 说明这份数据是全新的

        第二个条件是为了**老用户升级**：他们的数据文件里没这个键，
        但课表已经存在。只看第一个条件的话，每次升级都会弹一次「第一次使用」。
        """
        return not self._data.get("first_launch_done", False) and "courses" not in self._data

    def mark_launched(self) -> None:
        self._data["first_launch_done"] = True
        self.save()

    # ---------------- 开关 ----------------

    @property
    def enabled(self) -> bool:
        """悬浮窗总开关。关掉后程序不再主动刷新，也不发提醒"""
        return bool(self._data.get("enabled", False))

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._data["enabled"] = bool(value)
        self.save()

    @property
    def ball_enabled(self) -> bool:
        """悬浮窗是否显示。和 enabled 分开：可以只开悬浮窗不开启提醒"""
        return bool(self._data.get("ball_enabled", True))

    @ball_enabled.setter
    def ball_enabled(self, value: bool) -> None:
        self._data["ball_enabled"] = bool(value)
        self.save()

    # ---------------- 悬浮窗的位置和停靠 ----------------

    @property
    def ball_edge(self) -> str:
        """
        停靠在哪个边：''（自由浮动）/ left / right / top / bottom。

        存下来是因为：用户把卡片拖到屏幕右边缩起来，下次打开程序它**应该还在那**。
        每次都回到默认位置会让人反复调整，很烦。
        """
        edge = str(self._data.get("ball_edge", ""))
        return edge if edge in dock.EDGES else ""

    @ball_edge.setter
    def ball_edge(self, value: str) -> None:
        self._data["ball_edge"] = value if value in dock.EDGES else ""
        self.save()

    @property
    def ball_center(self) -> int:
        """
        贴边时那条要保住的中线的位置。

        贴左右边时是窗口的垂直中心；贴上下边时是水平中心。
        详见 dock.py 开头的说明。
        """
        return int(self._data.get("ball_center", -1))

    @ball_center.setter
    def ball_center(self, value: int) -> None:
        self._data["ball_center"] = int(value)
        self.save()

    @property
    def ball_pos(self) -> tuple[int, int]:
        """自由浮动时的位置。(-1, -1) 表示还没记录过"""
        raw = self._data.get("ball_pos")
        if isinstance(raw, list) and len(raw) == 2:
            try:
                return int(raw[0]), int(raw[1])
            except (TypeError, ValueError):
                pass
        return -1, -1

    @ball_pos.setter
    def ball_pos(self, value: tuple[int, int]) -> None:
        self._data["ball_pos"] = [int(value[0]), int(value[1])]
        self.save()

    # ---------------- 学期 ----------------

    @property
    def term_name(self) -> str:
        return str(self._data.get("term_name", builtin_data.TERM_NAME))

    @term_name.setter
    def term_name(self, value: str) -> None:
        self._data["term_name"] = value
        self.save()

    @property
    def term_start(self) -> date:
        raw = self._data.get("term_start")
        if raw:
            try:
                return date.fromisoformat(raw)
            except ValueError:
                pass
        return builtin_data.TERM_START

    @term_start.setter
    def term_start(self, value: date) -> None:
        self._data["term_start"] = value.isoformat()
        self.save()

    @property
    def total_weeks(self) -> int:
        return int(self._data.get("total_weeks", builtin_data.TOTAL_WEEKS))

    @total_weeks.setter
    def total_weeks(self, value: int) -> None:
        self._data["total_weeks"] = max(1, min(format_spec.MAX_WEEK_LIMIT, int(value)))
        self.save()

    @property
    def week_override(self) -> int:
        """手动指定周次。0 = 自动按日期算"""
        return int(self._data.get("week_override", 0))

    @week_override.setter
    def week_override(self, value: int) -> None:
        self._data["week_override"] = int(value)
        self.save()

    def week_of(self, d: date) -> int:
        forced = self.week_override
        if forced > 0:
            return forced
        from .engine import week_of as calc
        return calc(d, self.term_start)

    # ---------------- 课前提醒 ----------------

    @property
    def remind_lead(self) -> int:
        """提前几分钟提醒。0 = 关闭"""
        return int(self._data.get("remind_lead", 0))

    @remind_lead.setter
    def remind_lead(self, value: int) -> None:
        self._data["remind_lead"] = max(0, min(60, int(value)))
        self.save()

    @property
    def last_reminder_key(self) -> str:
        """
        上一条已发出的提醒的去重键：日期|开始时刻|课程名。

        为什么要存下来而不是只放内存里？
        因为提醒线程每分钟都可能检查一次，如果只靠内存变量去重，
        程序重启后同一节课会被再提醒一遍。
        存进文件，去重就跨进程生存期有效了。
        """
        return str(self._data.get("last_reminder", ""))

    @last_reminder_key.setter
    def last_reminder_key(self, value: str) -> None:
        self._data["last_reminder"] = value
        self.save()

    # ---------------- 课程 ----------------

    def courses(self) -> list[Course]:
        raw = self._data.get("courses")
        if raw is None:
            # 第一次运行：把内置课表写进去，之后就以存储里的为准。
            # 注意这是**惰性**的 —— 用户如果先点了「从空白开始」，
            # clear_courses() 会写入 "[]"，那就不会再走到这里。
            items = builtin_data.courses()
            self.save_courses(items)
            return items
        return [_course_from_dict(x, i) for i, x in enumerate(raw)]

    def save_courses(self, items: list[Course]) -> None:
        self._data["courses"] = [_course_to_dict(c) for c in items]
        self.save()

    def reset_courses(self) -> None:
        """恢复内置课表"""
        self._data.pop("courses", None)
        self.save()

    def clear_courses(self) -> None:
        """
        清空所有课程。

        ⚠️ 注意存的是 `[]` 而不是 pop 掉键。

        区别很关键：pop 之后 courses() 读到 None，会**重新填入内置课表** ——
        对「恢复内置」来说这是对的，但对「清空」来说完全是反效果：
        用户点了清空，19 门课又全回来了。

        **「没有数据」和「数据是空的」是两种状态，不能混用同一个表示。**
        """
        self._data["courses"] = []
        self.save()

    # ---------------- 作息模板 ----------------

    def templates(self) -> dict[DayType, list[Block]]:
        """
        当前生效的模板集合。

        存的是**用户改过的那部分**，不是展开后的完整六套。
        好处有两个：省空间；将来内置模板更新了，用户没覆盖过的那几种
        会自动跟着更新，而不是被旧数据钉死。
        """
        raw = self._data.get("templates", "")
        custom = format_spec.templates_from_json(raw) if raw else {}
        merged = builtin_data.templates()
        merged.update(custom)          # 用户改过的覆盖内置
        return merged

    def custom_templates(self) -> dict[DayType, list[Block]]:
        """只看用户自定义的部分（用于界面提示和导出判断）"""
        raw = self._data.get("templates", "")
        return format_spec.templates_from_json(raw) if raw else {}

    def save_templates(self, mapping: dict[DayType, list[Block]]) -> None:
        self._data["templates"] = format_spec.templates_to_json(mapping)
        self.save()

    def reset_templates(self) -> None:
        self._data.pop("templates", None)
        self.save()

    @property
    def has_custom_templates(self) -> bool:
        return bool(self.custom_templates())

    # ---------------- 日型策略（v3）----------------

    def day_type_policy(self) -> DayTypePolicy:
        """
        这份配置实际启用哪几种日型。

        没设置过就是 `DayTypePolicy.DEFAULT`（A + 没早八的 B + 周末）。
        """
        raw = self._data.get("day_types", "")
        if not raw:
            return DayTypePolicy.DEFAULT
        # day_types_from_json 自己会在损坏时返回 DEFAULT，所以这里不用再兜底
        return format_spec.day_types_from_json(raw)

    def save_day_type_policy(self, policy: DayTypePolicy) -> None:
        self._data["day_types"] = format_spec.day_types_to_json(policy)
        self.save()

    def reset_day_type_policy(self) -> None:
        self._data.pop("day_types", None)
        self.save()

    @property
    def has_custom_day_types(self) -> bool:
        """用户是否**显式设置过**日型策略（没设过就是默认那套）"""
        return bool(self._data.get("day_types", ""))

    def template_set(self) -> tuple[dict[DayType, list[Block]], DayTypePolicy]:
        """
        模板和策略**成对**返回。

        ## 为什么要成对

        手机版把这两样放进同一个 `TemplateSet` 对象，理由是
        **「策略和模板必须同源」**：如果模板来自 A 处方、策略来自 B 处，
        就可能出现「按策略这是 A 型日，但取到的却是 B 型的模板」这种错配。

        Python 没有对象把两者绑在一起，那就用**返回一个二元组**来达到同样效果 ——
        调用方一次就拿到配套的两样，想只取一样也得先解包，
        比「分别调两个方法」更难写错。

        （对应手机版的 `TemplateSet.policy` + `TemplateSet.custom`。）
        """
        return self.templates(), self.day_type_policy()

    @property
    def has_custom_schedule_config(self) -> bool:
        """
        「复制 JSON」按钮该不该带上作息配置。

        判据是**模板或日型任意一个被改过**。

        ⚠️ 手机版这里踩过坑：以前只判断 `hasCustomTemplates`，
        于是有一类配置会丢 —— 用户导入了一份**只改 dayTypes、没改模板**的文件，
        此时 `hasCustomTemplates` 是 false，复制出来的 JSON 不含 dayTypes，
        他那份日型设置就**静默消失了**。

        这类漏判特别隐蔽：功能没报错，只是一个字段没被带上。
        **判断「要不要带上某个东西」时，要把所有相关的来源都数一遍。**
        """
        return self.has_custom_templates or self.has_custom_day_types

    # ---------------- 导入导出 ----------------

    def export_json(self, include_schedule_config: bool = False) -> str:
        """
        导出。

        include_schedule_config=True 时输出**完整的作息配置**：
        展开后的六套模板 + 日型策略。这样导出的文件是一份能直接编辑的
        完整底稿 —— 想改起床时间，在那 100 多行里找到对应那一行改掉就行，
        不用从零写一套模板。

        ⚠️ **模板和日型必须一起导出。** 只带模板不带 `dayTypes` 的话，
        别人导入后拿到的日型和你的不一样（同一份模板，你只启用四种，
        他却六种全开）——**半份配置比没有配置更容易让人困惑。**

        （参数名沿用手机版的做法从 `include_templates` 改成
        `include_schedule_config`：它现在同时管模板和日型，
        还叫原名就是**名字在说谎**。）
        """
        return format_spec.serialize(
            self.term_name,
            self.term_start,
            self.total_weeks,
            self.courses(),
            self.templates() if include_schedule_config else None,
            self.day_type_policy() if include_schedule_config else None,
        )

    def apply_import(self, parsed: format_spec.Parsed, mode: str) -> str:
        """
        套用一份解析结果。

        mode: "replace" 或 "merge"

        三段各自独立：**「没写」永远表示「不要动」，而不是「清空」**。
        这条规则贯穿整个导入流程，是防止一次误操作毁掉用户数据的关键。

        返回一句给用户看的结果描述。
        """
        if parsed.term is not None:
            self._data["term_name"] = parsed.term.name
            self._data["term_start"] = parsed.term.start_date.isoformat()
            self._data["total_weeks"] = parsed.term.total_weeks

        if parsed.templates is not None:
            self._data["templates"] = format_spec.templates_to_json(parsed.templates)

        # 日型策略同理：没带这一段就一个字都不动。
        # 否则用户每次导入一份只改课表的文件，作息策略都会被打回默认值 ——
        # 那正是「没写 = 清空」这种误解造成的典型事故。
        if parsed.day_types is not None:
            self._data["day_types"] = format_spec.day_types_to_json(parsed.day_types)

        parts: list[str] = []

        if parsed.courses is None:
            parts.append("课表保持不变")
        elif mode == "replace":
            self.save_courses(parsed.courses)
            parts.append(f"已导入 {len(parsed.courses)} 门课（原课表已替换）")
        else:
            existing = self.courses()
            seen = {_dedup_key(c) for c in existing}
            added = 0
            for c in parsed.courses:
                if _dedup_key(c) not in seen:
                    seen.add(_dedup_key(c))
                    existing.append(c)
                    added += 1
            self.save_courses(existing)
            parts.append(f"合并完成：新增 {added} 门，跳过 {len(parsed.courses) - added} 门重复")

        if parsed.templates is not None:
            parts.append(f"作息模板 {len(parsed.templates)} 种")

        if parsed.day_types is not None:
            parts.append(f"启用日型 {len(parsed.day_types.enabled)} 种")

        self.save()
        return "，".join(parts)


def _dedup_key(c: Course) -> str:
    """
    判重键 = 课程名 + 星期 + 起始节次。

    为什么不只用课程名？因为同一门课一周可能上两次（正课和习题课在不同时段），
    只按名字去重会把第二次误判成重复。
    """
    return f"{c.name}|{c.day_of_week}|{c.start_node}"


def _course_to_dict(c: Course) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": c.name,
        "dayOfWeek": c.day_of_week,
        "nodes": [c.start_node, c.end_node],
        "weeks": format_spec.format_weeks(c.weeks),
    }
    if c.place:
        d["place"] = c.place
    if not c.enabled:
        d["enabled"] = False
    return d


def _course_from_dict(raw: Any, index: int) -> Course:
    """本地存储里的课程。坏了就当空课程，不让整个程序起不来"""
    if not isinstance(raw, dict):
        return Course(name="损坏的课程", day_of_week=1, start_node=1, end_node=2,
                      weeks=frozenset(), id=f"c{index + 1:03d}")
    nodes = raw.get("nodes") or [1, 1]
    try:
        weeks = format_spec.parse_weeks(str(raw.get("weeks", "*")), format_spec.MAX_WEEK_LIMIT)
    except FormatError:
        weeks = frozenset()
    return Course(
        name=str(raw.get("name", "未命名")),
        day_of_week=int(raw.get("dayOfWeek", 1)),
        start_node=int(nodes[0]),
        end_node=int(nodes[1]),
        weeks=weeks,
        place=str(raw.get("place", "")),
        enabled=bool(raw.get("enabled", True)),
        id=f"c{index + 1:03d}",
    )
