"""
界面主题（跟随系统 / 始终浅色 / 始终深色）的契约。

## 最要紧的是那条「同源」测试

小窗口（悬浮窗、提示条）的**窗口底色**只能在创建时定，而 Windows 裁完圆角
之后露出来的就是它。页面里卡片的 `--surface` 和它对不上，四个圆角就会
看到一圈**反色的边**。

这两处是两份独立写下的数据：

    app/screen.py 的 _bg_color()      →  "#ffffff" / "#181b21"
    三个页面 CSS 里的 --surface        →  同样的两个值，各写三遍

没有任何机制保证它们相等 —— 而且这种 bug **只在某一个主题下看得见**，
换个主题就正常了，几乎没法自查。所以钉在这儿。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "app" / "web"
ROOT = Path(__file__).resolve().parent.parent


class TestWindowBackgroundMatchesSurface(unittest.TestCase):
    """Python 的窗口底色 ↔ 页面的 --surface，必须是同一对颜色。"""

    def test_bg_color_matches_surface_in_every_page(self):
        from app.screen import _bg_color

        light = _bg_color("light").lower()
        dark = _bg_color("dark").lower()
        self.assertNotEqual(light, dark, "两个主题返回了同一个底色，测试失去意义")

        for name in ("style.css", "ball.html", "toast.html"):
            text = (WEB / name).read_text(encoding="utf-8")
            found = {m.lower()
                     for m in re.findall(r"--surface:\s*(#[0-9a-fA-F]{6})", text)}
            self.assertEqual(
                {light, dark}, found,
                f"{name} 的 --surface 取值是 {sorted(found)}，"
                f"而 screen._bg_color 是 {light} / {dark}。\n"
                f"对不上就会在窗口的四个圆角处露出一圈反色的边 —— "
                f"改颜色请两边一起改（app/screen.py 的 _bg_color，和这里的 --surface）。",
            )

    def test_titlebar_bg_is_the_same_source_as_window_bg(self):
        """
        标题栏底色必须**等于** `_bg_color()`，也就是页面的 `--surface`。

        三处是三份独立写下的数据（`_bg_color`、`_titlebar_colors`、三个页面
        的 CSS），对不上就是「页面换了色、标题栏还是原来那个」——上下两截。
        """
        from app.screen import _bg_color, _titlebar_colors

        for pref in ("system", "light", "dark"):
            bg, fg = _titlebar_colors(pref)
            self.assertEqual(_bg_color(pref), bg.lower(),
                             f"主题 {pref!r} 下标题栏底色和窗口底色对不上")
            self.assertNotEqual(bg.lower(), fg.lower(),
                                f"主题 {pref!r} 下标题栏底色和文字色撞色了")

    def test_colorref_packs_bgr_not_rgb(self):
        """COLORREF 是 0x00bbggrr（低位是红），按 RGBA 记就会变成另一种颜色。"""
        from app.winutil import _colorref

        self.assertEqual(0x211b18, _colorref("#181b21"))   # r=18 g=1b b=21
        self.assertEqual(0xffffff, _colorref("#ffffff"))

    def test_titlebar_needs_a_real_hwnd(self):
        """
        拿不到句柄就返回 False，一次 DWM 都不许碰。

        ⚠️ 这条盯的是那个「静默不变」的坑：`create_window()` 只是登记，
        真正的窗口要等 `webview.start()`。不等就调，这里拿不到句柄，
        表现是**标题栏死活不变、而且没有任何报错**。
        """
        from app.winutil import set_titlebar_theme

        self.assertEqual("", set_titlebar_theme(0, "#ffffff", "#1a1d23", False))

    def test_every_page_can_accept_a_forced_theme(self):
        """
        三个窗口都得能接 Python 推过来的强制档，缺一个就是**只在那一个窗口**
        上主题不对 —— 悬浮窗深色、主窗口浅色这种，看起来像程序坏了。
        """
        for name in ("index.html", "ball.html", "toast.html"):
            text = (WEB / name).read_text(encoding="utf-8")
            self.assertIn("window.__applyTheme", text,
                          f"{name} 里没有 window.__applyTheme —— "
                          f"设置里选的「始终浅色/深色」对这个窗口不生效")

    def test_forced_theme_only_sets_known_values(self):
        """__applyTheme 只认 light / dark，别的（含 'system'）一概不动。"""
        for name in ("index.html", "ball.html", "toast.html"):
            text = (WEB / name).read_text(encoding="utf-8")
            self.assertRegex(
                text, r"t === 'light' \|\| t === 'dark'",
                f"{name} 的 __applyTheme 没有做取值过滤 —— "
                f"收到 'system' 会把 data-theme 设成 'system'，CSS 里没有这个档，"
                f"结果是浅色被硬钉住。",
            )


class TestThemeResolution(unittest.TestCase):
    def test_forced_modes_win_over_system(self):
        from app.screen import resolve_theme

        self.assertEqual("light", resolve_theme("light"))
        self.assertEqual("dark", resolve_theme("dark"))

    def test_system_falls_back_to_registry(self):
        from app.screen import is_dark_mode, resolve_theme

        self.assertEqual("dark" if is_dark_mode() else "light", resolve_theme("system"))

    def test_unknown_value_is_treated_as_system(self):
        """
        data.json 是用户能用记事本手改的，不能假设它写得对。
        拼错一个字符串不该让程序起不来，也不该静默当成某一档。
        """
        from app.store import _theme_or_system

        for bad in ("bogus", "", None, "Light", "深色"):
            self.assertEqual("system", _theme_or_system(bad), f"{bad!r} 没被拦下")
        for good in ("system", "light", "dark"):
            self.assertEqual(good, _theme_or_system(good))


class TestTitlebarStaysWithAppTheme(unittest.TestCase):
    """
    标题栏必须**始终**跟应用主题，不能被 pywebview 抢走。

    ## 这条测试防的是什么

    pywebview 的 WinForms 后端在窗口构造时和**每次 UserPreferenceChanged**
    （壁纸、IME、任务栏……任何系统设置变动都触发）都会按 **系统** 主题改写
    标题栏深浅（winforms.py 的 update_title_bar_theme）。系统深色 + 应用浅色
    时，只设一次的写法会被它随后盖掉 —— 用户看到的正是
    「浅色主题下标题栏一直是黑的」。

    对策（见 main._pin_titlebar_to_app_theme）：改写它的判定函数
    `is_dark_theme`，让它按**应用**主题回答 —— 它每次改写都替我们写，
    时序（构造时写、事件里重写）就无所谓了。事后补救是赢不了它的：
    构造函数里就直接调了一次。
    """

    def test_patcher_is_installed_in_build_before_any_window(self):
        src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("_pin_titlebar_to_app_theme", src,
                      "main.py 没有改写 pywebview 的标题栏判定")
        self.assertIn("_APP_TITLEBAR_DARK", src,
                      "缺少「应用主题的标题栏深浅」这个决定值")
        # 改写必须发生在 build() 里（webview.start 创建窗口之前）——
        # 构造函数里第一次就用上，事件重写更是全程用上
        self.assertLess(
            src.index("def build("),
            src.index("_pin_titlebar_to_app_theme()", src.index("def build(")),
            "build() 里必须调用 _pin_titlebar_to_app_theme()",
        )
        # 决定值必须先于改写安装定好，否则判定函数会走回退路径
        self.assertLess(
            src.index("_APP_TITLEBAR_DARK = resolve_theme("),
            src.index("detail = _pin_titlebar_to_app_theme()"),
            "要先定好应用主题的深浅，再安装判定改写",
        )

    def test_patch_has_a_fallback_when_pywebview_changes(self):
        """
        monkeypatch 第三方内部类，pywebview 升级改名就会失效 ——
        必须有 hasattr 守卫和原判定保存，失效时退回「跟系统」而不是崩。
        （目标是嵌套类 BrowserView.BrowserForm —— 窗体方法全在那层，
        第一版 patch 到 BrowserView 本体上，hasattr 悄悄是 False，
        整个改写静默没生效。）
        """
        src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('getattr(view, "BrowserForm", None)', src,
                      "patch 没有落到嵌套类 BrowserForm 上")
        self.assertIn('hasattr(form, "is_dark_theme")', src,
                      "patch 没有守卫 —— pywebview 改名后会直接崩")
        self.assertIn("_original_is_dark_theme", src,
                      "原判定没有存档 —— 回退路径无从谈起")


if __name__ == "__main__":
    unittest.main()
