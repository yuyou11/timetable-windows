"""
导入导出、AI 提示词，以及文件选择框。

（由 `tools/split_api.py` 从 `api.py` 原样切出，**一字未改**。）
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from . import builtin_data, engine, format_spec, slots, wake_shift
from .ai_prompt import AiPrompt
from .day_type_policy import DayTypePolicy
from .models import DAY_TYPE_LABEL, DAY_TYPE_ORDER, Block, Course, DayType, Kind
from .payloads import (  # noqa: F401
    _block_dict,
    _course_dict,
    _moment_dict,
    _parse_day_type,
    _short_day_type,
    _valid_index,
)
from .store import Store


class TransferMixin:
    """导入导出、AI 提示词，以及文件选择框。"""


    # ============================================================
    #  导入导出
    # ============================================================

    def import_from_file(self) -> dict[str, Any]:
        """
        选文件 → 解析 → 返回预览。

        注意这里**只解析不套用** —— 真正的写入在 confirm_import 里。
        导入是要覆盖用户数据的，必须先让他看见将要发生什么。
        """
        path = self._ask_open_file()
        if not path:
            return {"ok": False, "cancelled": True}

        try:
            text = Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = Path(path).read_text(encoding="gbk")
            except (UnicodeDecodeError, OSError) as e:
                return {"ok": False, "message": f"读不出文件内容：{e}"}
        except OSError as e:
            return {"ok": False, "message": f"打不开文件：{e}"}

        try:
            parsed = format_spec.parse(text, self.store.total_weeks)
        except format_spec.FormatError as e:
            return {"ok": False, "message": str(e)}

        # ⚠️ 这里**只能**返回能被 JSON 序列化的东西。
        #
        # 曾经这里多带了一个 `"_parsed": parsed`（Parsed 是 dataclass 实例）。
        # 后果是**导入功能完全不能用**：pywebview 会把 js_api 的返回值
        # json.dumps 之后才发给前端（见 webview/util.py 里 _call 函数），
        # 序列化失败会被它捕获成 `{isError: true}`，用户看到的是
        # 「导入失败 TypeError: Object of type Parsed is not JSON serializable」。
        #
        # 这个键还是完全多余的：解析结果已经存在 self._pending_import 里
        # （由 _preview_dict 顺手存下），前端不需要拿到它 —— 那正是
        # _pending_import 存在的理由（见它的注释：别让前端来回搬数据）。
        #
        # **规矩：桥接方法的返回值必须能 json.dumps。** 传对象图过去是行不通的，
        # 要么转成 dict，要么像这里一样根本别传。
        # tests/test_api_serializable.py 把这条钉住了。
        return {"ok": True, "preview": self._preview_dict(parsed)}



    def confirm_import(self, mode: str) -> dict[str, Any]:
        """套用上一次解析的结果。mode: 'replace' | 'merge'"""
        parsed = self._pending_import
        if parsed is None:
            return {"ok": False, "message": "没有待导入的数据，请重新选择文件"}
        self._pending_import = None

        message = self.store.apply_import(parsed, mode)
        self._notify_settings_changed()
        return {"ok": True, "message": message}



    def _preview_dict(self, parsed: format_spec.Parsed) -> dict[str, Any]:
        self._pending_import = parsed

        term = parsed.term
        template_lines = []
        if parsed.templates:
            for t in DAY_TYPE_ORDER:
                if t in parsed.templates:
                    wake = engine.wake_minute(parsed.templates[t])
                    template_lines.append({
                        "key": t.value,
                        "wake": slots.fmt(wake) if wake is not None else None,
                    })

        # 启用了哪些日型（v3）。标题里带上起床时间 —— 这是用户最想核对的
        # 那一件事（「我说好 7 点起，到底给我排的几点」）。
        day_type_lines = []
        if parsed.day_types is not None:
            for t in DAY_TYPE_ORDER:
                if t in parsed.day_types.enabled:
                    # 起床时间要从**这一份文件里的模板**推，没有就用内置的
                    blocks = (parsed.templates or {}).get(t)
                    if blocks is None:
                        blocks = self.store.templates().get(t, [])
                    wake = engine.wake_minute(blocks)
                    day_type_lines.append({
                        "key": t.value,
                        "wake": slots.fmt(wake) if wake is not None else None,
                    })

        return {
            "hasTerm": term is not None,
            "termName": term.name if term else None,
            "termStart": term.start_date.isoformat() if term else None,
            "totalWeeks": term.total_weeks if term else None,
            "courseCount": len(parsed.courses) if parsed.courses is not None else None,
            "coursesUnchanged": parsed.courses is None,
            "templateCount": len(parsed.templates) if parsed.templates else 0,
            "templatesUnchanged": parsed.templates is None,
            "templateLines": template_lines,
            "dayTypesUnchanged": parsed.day_types is None,
            "dayTypeEnabled": (
                [
                    {"key": t.value, "fallback": parsed.day_types.fallback is t}
                    for t in DAY_TYPE_ORDER
                    if t in parsed.day_types.enabled
                ]
                if parsed.day_types is not None
                else []
            ),
            "dayTypeFallback": (
                parsed.day_types.fallback.value
                if parsed.day_types is not None
                else None
            ),
            "dayTypeLines": day_type_lines,
            "warnings": parsed.warnings,
        }



    def export_to_file(self, include_schedule_config: bool) -> dict[str, Any]:
        """
        ⚠️ 参数名跟着手机版从 `include_templates` 改成了 `include_schedule_config`。

        它现在同时管**模板和日型策略**两样东西，还叫原名就是「名字在说谎」——
        而一个名字和实际行为不符的参数，早晚会有人按名字去理解它。
        （前端 app.js 里的调用点要同步改，否则会 TypeError。见
        tests/test_api_contract.py：它扫前端所有 call('x') 的实参个数。）
        """
        text = self.store.export_json(bool(include_schedule_config))
        default_name = "课表.json" if not include_schedule_config else "完整备份.json"
        path = self._ask_save_file(default_name)
        if not path:
            return {"ok": False, "cancelled": True}
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as e:
            return {"ok": False, "message": f"写文件失败：{e}"}

        count = len(self.store.courses())
        extra = " + 作息配置（模板与启用日型）" if include_schedule_config else ""
        return {"ok": True, "message": f"已导出 {count} 门课{extra}", "path": str(path)}



    def copy_json(self, include_schedule_config: bool) -> dict[str, Any]:
        text = self.store.export_json(bool(include_schedule_config))
        return {"ok": True, "text": text, "message": f"已复制（{len(text)} 字）"}



    def get_ai_prompt(self, kind: str) -> dict[str, Any]:
        """
        AI 提示词。

        提示词里最要紧的三个值是学期名称、第 1 周周一日期、总周数 ——
        它们决定整张表的时间对不对。让 AI 去猜的话它大概率会编一个，
        而错一天的后果是每一周的课都错位。
        所以这里从 Store 读出真实值填进去，用户复制到的天然是正确的。
        """
        if kind == "template":
            text = AiPrompt.for_templates(self.store.term_name)
        elif kind == "fix":
            text = AiPrompt.for_fix(
                "（把程序导入时弹出的报错原文粘在这里）",
                "（把 AI 上次生成的 JSON 粘在这里）",
            )
        else:
            text = AiPrompt.for_courses(
                self.store.term_name,
                self.store.term_start.isoformat(),
                self.store.total_weeks,
            )
        return {"ok": True, "text": text}



    def _ask_open_file(self) -> Optional[str]:
        if self._window is None:
            return None
        import webview
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("课表 JSON (*.json)", "所有文件 (*.*)"),
        )
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result



    def _ask_save_file(self, default_name: str) -> Optional[str]:
        if self._window is None:
            return None
        import webview
        result = self._window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=default_name,
            file_types=("JSON 文件 (*.json)",),
        )
        if not result:
            return None
        return result if isinstance(result, str) else result[0]
