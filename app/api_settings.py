"""
设置页那几个开关、学期信息、界面主题和自定义外观。

（由 `tools/split_api.py` 从 `api.py` 原样切出；之后在这里加了 `set_theme`
和自定义外观那几个方法。）
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from . import builtin_data, engine, skin
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

    # ============================================================
    #  自定义外观（主色调 / 背景图 / 卡片透明度）
    # ============================================================
    #
    #  和 set_theme 的「重启后生效」不同，这一组都是**立即生效**：
    #  存完 → _notify_settings_changed() → main.py 的 _on_settings_changed
    #  会把新外观推给各窗口（main._push_skin）。
    #
    #  推给主窗口的是全套（主色调 + 半透明底色）；悬浮窗只推主色调 ——
    #  它的窗口底色改不了，半透明卡片会在圆角处露底（app/skin.py 开头
    #  有完整说明）。

    def set_accent(self, value: str) -> dict[str, Any]:
        """
        换主色调。`""` / None = 恢复默认。

        颜色格式不对就**如实报错**，不静默当成默认 —— 和 set_theme 同一条
        规矩：让用户以为选上了，比报错更糟。
        """
        text = "" if value is None else str(value).strip()
        picked = skin.normalize_hex(text)
        if text and picked is None:
            return {"ok": False,
                    "message": f"颜色格式不对：{text}（要 #2563eb 这样六位十六进制）"}
        self.store.accent = picked or ""
        self._notify_settings_changed()
        return self._settings_dict()

    def set_text_color(self, value: str) -> dict[str, Any]:
        """
        换背景图上的文字颜色。`""` / None = 恢复默认。

        校验口径和 set_accent 相同：格式不对**如实报错**，不静默当成默认。
        没开背景图时它不生效（前端会把控件禁用），但值照存 ——
        用户先调好文字、再选图，顺序不该有讲究。
        """
        text = "" if value is None else str(value).strip()
        picked = skin.normalize_hex(text)
        if text and picked is None:
            return {"ok": False,
                    "message": f"颜色格式不对：{text}（要 #2563eb 这样六位十六进制）"}
        self.store.text_color = picked or ""
        self._notify_settings_changed()
        return self._settings_dict()

    def set_card_opacity(self, value: Any) -> dict[str, Any]:
        """卡片不透明度 0–100。100 = 不透明。store 那层会钳范围。"""
        self.store.card_opacity = value
        self._notify_settings_changed()
        return self._settings_dict()

    def set_sidebar_opacity(self, value: Any) -> dict[str, Any]:
        """侧栏不透明度 0–100。100 = 不透明。和卡片不透明度互相独立。"""
        self.store.sidebar_opacity = value
        self._notify_settings_changed()
        return self._settings_dict()

    def set_bg_dim(self, value: Any) -> dict[str, Any]:
        """背景图暗化 0–100。没开背景图时调它也没副作用，存着备用。"""
        self.store.bg_dim = value
        self._notify_settings_changed()
        return self._settings_dict()

    def choose_background_image(self) -> dict[str, Any]:
        """
        选背景图：弹文件框 → 压缩存进数据目录 → 立即生效。

        ⚠️ 首次设图时会顺手把卡片不透明度从 100 调到 90：全不透明的卡片
        会把图挡得严严实实，用户第一眼「设置成功了但什么都看不见」，
        很难想到去调另一个开关。只是给个能看到效果的起点，随时能调回去。
        """
        path = self._ask_open_image()
        if not path:
            return {"ok": False, "cancelled": True}

        try:
            skin.save_background(path, self.store.path.parent)
        except ValueError as e:
            return {"ok": False, "message": str(e)}
        except OSError as e:
            return {"ok": False, "message": f"图片存不进去：{e}"}

        was_opaque = self.store.card_opacity == 100
        side_was_opaque = self.store.sidebar_opacity == 100
        # 一次操作连改最多 3 个设置：batch 收成一次落盘
        #（不等价于性能问题 —— 数据量小写盘很快；收拢是为了
        #  中途断电时别留下「图开了、不透明度还没跟上」的半套状态）
        with self.store.batch():
            self.store.bg_image = True
            if was_opaque:
                self.store.card_opacity = 90
            if side_was_opaque:
                self.store.sidebar_opacity = 90

        self._notify_settings_changed()
        msg = "背景图已设置"
        touched = [name for flag, name in
                   ((was_opaque, "卡片"), (side_was_opaque, "侧栏")) if flag]
        if touched:
            msg += "，" + "和".join(touched) + "不透明度顺手调到了 90%，可以在下面再调"
        return {"ok": True, "message": msg, "settings": self._settings_dict()}

    def remove_background_image(self) -> dict[str, Any]:
        """不用背景图了：删掉图片文件 + 关掉开关。文件本来就没有也照常成功。"""
        try:
            skin.background_path(self.store.path.parent).unlink(missing_ok=True)
        except OSError as e:
            return {"ok": False, "message": f"删不掉背景图文件：{e}"}
        self.store.bg_image = False
        self._notify_settings_changed()
        return {"ok": True, "message": "已恢复默认背景", "settings": self._settings_dict()}

    def get_background_image(self) -> dict[str, Any]:
        """
        背景图内容，data URL 形式。没有就回 None。

        为什么走桥传 base64，而不是给个 file:// 路径让页面自己去加载：
        页面跑在 pywebview 的内置 HTTP 服务上（http://127.0.0.1），从那里
        引 file:// 资源会被 WebView2 的同源策略拦掉 —— 而且拦得**悄无声息**，
        界面上就是没有背景图。data URL 没有跨域问题，一次拉下来就一直用。
        """
        url = None
        if self.store.bg_image:
            url = skin.background_data_url(
                skin.background_path(self.store.path.parent))
        return {"ok": True, "dataUrl": url}

    def _ask_open_image(self) -> Optional[str]:
        """弹一个「选图片」文件框，返回路径；取消 / 主窗口还没好返回 None。"""
        if self._window is None:
            return None
        import webview
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("图片 (*.jpg;*.jpeg;*.png;*.webp;*.bmp)", "所有文件 (*.*)"),
        )
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

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
