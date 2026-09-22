"""
设置页那几个开关、学期信息、界面主题。

（由 `tools/split_api.py` 从 `api.py` 原样切出；之后在这里加了 `set_theme`。）
"""

from __future__ import annotations

from datetime import date
from typing import Any

from . import builtin_data, engine
from .store import THEMES

#: 主题三档的中文名，给「已切换到…」那句提示用。
THEME_LABEL = {"system": "跟随系统", "light": "始终浅色", "dark": "始终深色"}


class SettingsMixin:
    """设置页那几个开关，和学期信息。"""

    # ============================================================
    #  设置
    # ============================================================

    def set_enabled(self, value: bool) -> dict[str, Any]:
        return self._apply_setting("enabled", bool(value))

    def set_ball_enabled(self, value: bool) -> dict[str, Any]:
        return self._apply_setting("ball_enabled", bool(value))

    def set_theme(self, value: str) -> dict[str, Any]:
        """
        换界面主题：`system`（跟随系统）/ `light` / `dark`。

        ⚠️ **返回的 message 里必须写明「重启后生效」** —— 点了没反应会让人
        以为坏了。为什么不能立刻生效：小窗口的窗口底色只能在创建时定，
        运行期改不了，它会在四个圆角处露出来（见 `store.theme` 的说明）。

        不认识的取值**如实报错**，不要静默当成某一档 —— 那会让用户以为
        自己选的生效了。
        """
        text = str(value)
        if text not in THEMES:
            return {"ok": False,
                    "message": f"不认识的主题：{text}（只支持 system / light / dark）"}

        self.store.theme = text
        self._notify_settings_changed()
        return {
            "ok": True,
            "message": f"已切换到「{THEME_LABEL[text]}」，重启后生效",
            "theme": text,
        }

    def set_remind_lead(self, minutes: int) -> dict[str, Any]:
        return self._apply_setting("remind_lead", int(minutes))

    def set_term(self, name: str, start_iso: str, total_weeks: int) -> dict[str, Any]:
        """
        改学期设置。

        和手机版一样，起始日**自动吸附到周一** —— 整个时间轴是按
        「第 N 周 = 起始日 + (N-1)×7 天」推的，起始日不是周一就全错位。
        JSON 导入时这种情况会报错（用户不在场），
        但界面里用户就在屏幕前，顺手改对比甩个错误更好。
        """
        try:
            picked = date.fromisoformat(start_iso)
        except ValueError:
            return {"ok": False, "message": "日期格式不对，应该是 2026-09-07 这样"}

        monday = engine.monday_of(picked)
        self.store.term_name = name or builtin_data.TERM_NAME
        self.store.term_start = monday
        self.store.total_weeks = int(total_weeks)
        # 改了起始日，之前手动钉死的周次就没意义了
        self.store.week_override = 0
        self._notify_settings_changed()

        msg = "已保存"
        if monday != picked:
            msg = f"已自动对齐到那一周的周一：{monday}"
        return {"ok": True, "message": msg, "term": self._term_dict()}

    def set_week_override(self, value: int) -> dict[str, Any]:
        self.store.week_override = max(0, int(value))
        self._notify_settings_changed()
        return self._term_dict()
