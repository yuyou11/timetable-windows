"""
自定义外观（主色调 / 背景图 / 文字颜色 / 卡片透明度）的契约。

## 覆盖什么

  · 颜色推导（app/skin.py）：色值规整、明暗两档的推导、对比度钳制，
    以及文字颜色的深浅配对（浅文字配深色卡片，反之亦然）
  · 「同源」对账：skin.SURFACES 里的底色必须和 screen._bg_color() 相等 ——
    半透明卡片是拿这对值算 rgba 的，对不上就是另一种形式的圆角露边
  · Store 的外观设置键：默认值、越界/坏值容错、落盘重载
  · Api 的桥接方法：校验、失败路径、选图 → 压缩 → data URL 全链路

## 为什么选图那条要走真文件

choose_background_image 和 import_from_file 一样属于「会弹对话框」的
方法 —— 单测里把 `_ask_open_image` 打桩换成真文件路径，让
「选图 → PIL 压缩 → 落盘 → 读回 data URL」整条链真的跑一遍。
只测逻辑不测边界，报错永远发生在用户手里（见 test_api_serializable
的教训）。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import skin  # noqa: E402


def _isolated_dir() -> Path:
    """数据目录隔离。和 test_frozen_contracts.IsolatedApiTestCase 同一套路。"""
    tmp = tempfile.mkdtemp(prefix="timetable_skin_")
    saved = os.environ.get("TIMETABLE_DATA_DIR")
    os.environ["TIMETABLE_DATA_DIR"] = tmp

    def restore():
        os.environ.pop("TIMETABLE_DATA_DIR", None)
        if saved is not None:
            os.environ["TIMETABLE_DATA_DIR"] = saved
        shutil.rmtree(tmp, ignore_errors=True)

    return Path(tmp), restore


class TestNormalizeHex(unittest.TestCase):
    def test_accepts_the_three_usable_spellings(self):
        for raw, want in [
            ("#2563eb", "#2563eb"),
            ("2563EB", "#2563eb"),          # 无 # 大写
            ("#25E", "#2255ee"),            # 三位简写展开
            ("  #0D9488  ", "#0d9488"),     # 带空白（界面来的值不该有，但手改的会有）
        ]:
            self.assertEqual(want, skin.normalize_hex(raw), raw)

    def test_garbage_returns_none(self):
        for bad in ("", "   ", "#", "#12345", "#1234567", "red", "#2563eg", 0x2563eb, None, ["#ffffff"]):
            self.assertIsNone(skin.normalize_hex(bad), repr(bad))


class TestAccentPayload(unittest.TestCase):
    def test_empty_accent_means_all_none(self):
        """None 是给前端的信号：摘掉覆盖、回到样式表默认。不能是某个具体颜色。"""
        for pref in ("light", "dark", "system"):
            self.assertEqual(
                {"accent": None, "accentSoft": None, "accentText": None},
                skin.accent_payload("", pref), pref,
            )

    def test_light_and_dark_derive_different_sets(self):
        light = skin.accent_payload("#2563eb", "light")
        dark = skin.accent_payload("#2563eb", "dark")
        self.assertNotEqual(light, dark, "同一颜色在两档推导出了同一套值，钳制没起作用")
        for key in ("accent", "accentSoft", "accentText"):
            self.assertRegex(light[key], r"^#[0-9a-f]{6}$", key)
            self.assertRegex(dark[key], r"^#[0-9a-f]{6}$", key)

    def test_too_light_is_clamped_on_light_theme(self):
        """白字要压得住：很浅的颜色当主色时，亮度必须被钳下来。
        （0.59 而不是 0.58：钳到边界后过一遍 8-bit 色值会有第三位小数的舍入。）"""
        for too_light in ("#e8e8e8", "#fdfdf5"):
            rgb = skin._hex_to_rgb(skin.accent_payload(too_light, "light")["accent"])
            _h, _s, lightness = skin._rgb_to_hsl(rgb)
            self.assertLessEqual(lightness, 0.59, f"{too_light} 没被钳住")

    def test_too_dark_is_clamped_on_dark_theme(self):
        """深底上要看得见：很深的颜色当主色时，亮度必须被抬起来。"""
        for too_dark in ("#0a0a14", "#101018"):
            rgb = skin._hex_to_rgb(skin.accent_payload(too_dark, "dark")["accent"])
            _h, _s, lightness = skin._rgb_to_hsl(rgb)
            self.assertGreaterEqual(lightness, 0.54, f"{too_dark} 没被钳住")

    def test_garbage_accent_is_treated_as_unset(self):
        self.assertEqual(
            {"accent": None, "accentSoft": None, "accentText": None},
            skin.accent_payload("红红色", "light"),
        )


class TestSurfacesAgreeWithWindowBg(unittest.TestCase):
    """
    skin.SURFACES ↔ screen._bg_color() 必须是同一对值。

    半透明卡片的 rgba 就是拿 SURFACES 里的 surface 算的。它和真实的
    窗口底色对不上时没有测试会红 —— 卡片是半透明的，圆角处露的
    是「算错的颜色」，看起来只是「颜色有点怪」，极难排查。所以钉在这儿。
    （style.css ↔ _bg_color 那一对由 tests/test_theme.py 负责。）
    """

    def test_surface_column_matches_bg_color(self):
        from app.screen import _bg_color

        for pref in ("light", "dark"):
            self.assertEqual(
                _bg_color(pref).lower(), skin.SURFACES[pref][0].lower(),
                f"{pref} 档的 surface 和窗口底色对不上 —— 改颜色请两边一起改"
                f"（app/screen.py 的 _bg_color 和 app/skin.py 的 SURFACES）",
            )

    def test_both_rows_are_complete_and_distinct(self):
        self.assertEqual(3, len(skin.SURFACES["light"]))
        self.assertEqual(3, len(skin.SURFACES["dark"]))
        self.assertNotEqual(skin.SURFACES["light"], skin.SURFACES["dark"])


class TestTextPairing(unittest.TestCase):
    """文字颜色的派生与深浅配对（浅文字 → 深色卡片，深文字 → 浅色卡片）。"""

    def test_unset_or_garbage_means_none(self):
        for bad in ("", "   ", "红红色", "#12345", 0x123456, None):
            self.assertIsNone(skin.text_pairing(bad), repr(bad))

    def test_light_text_pairs_with_dark_surfaces(self):
        got = skin.text_pairing("#ffffff")
        self.assertEqual("dark", got["pairing"])
        self.assertEqual("#ffffff", got["text"])
        for key in ("textDim", "textFaint"):
            self.assertRegex(got[key], r"^#[0-9a-f]{6}$", key)
        # 三个层级必须真的拉开了，不然 dim/faint 没了层次
        self.assertNotEqual(got["text"], got["textDim"])
        self.assertNotEqual(got["textDim"], got["textFaint"])

    def test_dark_text_pairs_with_light_surfaces(self):
        got = skin.text_pairing("#1a1d23")
        self.assertEqual("light", got["pairing"])
        self.assertEqual("#1a1d23", got["text"])

    def test_lightness_is_pinned_to_its_own_side(self):
        """配对靠「亮度 ≥ 0.5 算浅文字」这个二分 —— 钳制必须把颜色留在
        自己那一侧，不然挑了浅档却被推导成中间灰，配对就自相矛盾。
        （0.549 而不是 0.55：钳到边界后过一遍 8-bit 色值会有舍入。）"""
        for raw in ("#808080", "#cccccc", "#fde68a"):
            rgb = skin._hex_to_rgb(skin.text_pairing(raw)["text"])
            _h, _s, light = skin._rgb_to_hsl(rgb)
            self.assertGreaterEqual(light, 0.549, f"{raw} 没留在浅侧")
        for raw in ("#1a1d23", "#333333", "#0a0a14"):
            rgb = skin._hex_to_rgb(skin.text_pairing(raw)["text"])
            _h, _s, light = skin._rgb_to_hsl(rgb)
            self.assertLessEqual(light, 0.451, f"{raw} 没留在深侧")

    def test_extremes_are_left_alone(self):
        """上限不设：纯白就该还是纯白，越浅在深底上越清楚。"""
        self.assertEqual("#ffffff", skin.text_pairing("#ffffff")["text"])
        self.assertEqual("#0a0a14", skin.text_pairing("#0a0a14")["text"])

    def test_hue_survives_derivation(self):
        """用户挑的是色调 —— 钳亮度不许动色相。"""
        for raw in ("#fde68a", "#9ec5ff", "#264d33"):
            want = skin._rgb_to_hsl(skin._hex_to_rgb(raw))[0]
            got = skin._rgb_to_hsl(
                skin._hex_to_rgb(skin.text_pairing(raw)["text"]))[0]
            self.assertAlmostEqual(want, got, places=2, msg=raw)


class TestMainPayload(unittest.TestCase):
    def test_no_customization_means_all_none(self):
        payload = skin.main_payload("light", "", 100, False)
        self.assertEqual(
            {"accent": None, "accentSoft": None, "accentText": None,
             "surface": None, "surface2": None,
             "sidebar": None, "sidebar2": None,
             "text": None, "textDim": None, "textFaint": None,
             "border": None, "danger": None},
            payload,
        )

    def test_low_opacity_produces_rgba_surfaces(self):
        payload = skin.main_payload("light", "", 50, False)
        self.assertEqual("rgba(255, 255, 255, 0.5)", payload["surface"])
        self.assertEqual("rgba(250, 251, 252, 0.5)", payload["surface2"])

    def test_dark_theme_uses_dark_surfaces(self):
        payload = skin.main_payload("dark", "", 70, False)
        self.assertEqual("rgba(24, 27, 33, 0.7)", payload["surface"])

    def test_background_image_alone_also_makes_surfaces_translucent(self):
        """透明度 100 但开了背景图：surface 不透明的话图只在缝隙里可见，功能等于没做。"""
        payload = skin.main_payload("light", "", 100, True)
        self.assertIsNotNone(payload["surface"])

    def test_opacity_out_of_range_is_clamped(self):
        # 500 钳到 100（不透明）→ 不需要覆盖，surface 为 None
        self.assertEqual(1.0, skin._alpha_of(500))
        self.assertEqual(0.0, skin._alpha_of(-50))
        self.assertEqual(0.35, skin._alpha_of(35))
        self.assertIsNone(skin.main_payload("light", "", 500, False)["surface"])

    def test_payload_is_json_safe(self):
        payload = skin.main_payload("system", "#0d9488", 90, True, "#ffffff")
        json.dumps(payload)   # 抛 TypeError 就是不能过桥

    def test_light_text_flips_surfaces_to_dark_pairing(self):
        """浅文字必须配深色卡片 —— 白字压在浅色半透明卡片上等于没解决可读性。"""
        payload = skin.main_payload("light", "", 100, True, "#ffffff")
        self.assertEqual("#ffffff", payload["text"])
        self.assertEqual("rgba(24, 27, 33, 1.0)", payload["surface"])
        self.assertEqual("rgba(31, 35, 42, 1.0)", payload["surface2"])
        self.assertEqual(skin.PAIRED_DARK_BORDER, payload["border"])
        self.assertEqual(skin.PAIRED_DARK_DANGER, payload["danger"])

    def test_dark_text_keeps_light_surfaces(self):
        payload = skin.main_payload("light", "", 90, True, "#1a1d23")
        self.assertEqual("rgba(255, 255, 255, 0.9)", payload["surface"])
        self.assertIsNone(payload["border"])
        self.assertIsNone(payload["danger"])

    def test_text_color_needs_a_background_image(self):
        """没图时文字都落在实色卡片上，主题默认色就是对着它们调的 —— 不覆盖。
        值照存（重新选图会回来），payload 里则是干净的 None。"""
        payload = skin.main_payload("light", "", 90, False, "#ffffff")
        self.assertIsNone(payload["text"])
        self.assertEqual("rgba(255, 255, 255, 0.9)", payload["surface"])

    def test_garbage_text_color_is_treated_as_unset(self):
        payload = skin.main_payload("dark", "", 90, True, "红")
        self.assertIsNone(payload["text"])
        self.assertEqual("rgba(24, 27, 33, 0.9)", payload["surface"])

    def test_sidebar_opacity_is_independent_of_cards(self):
        """两个滑杆各管各的：一边调了不许连累另一边。"""
        payload = skin.main_payload("light", "", 100, True, "", 50)
        self.assertEqual("rgba(255, 255, 255, 1.0)", payload["surface"])
        self.assertEqual("rgba(255, 255, 255, 0.5)", payload["sidebar"])
        payload = skin.main_payload("light", "", 50, True, "", 100)
        self.assertEqual("rgba(255, 255, 255, 0.5)", payload["surface"])
        self.assertEqual("rgba(255, 255, 255, 1.0)", payload["sidebar"])

    def test_surfaces_and_sidebar_are_given_together(self):
        """CSS 里 --sidebar 回退引用的是 --surface 的**当前值** —— 只覆盖
        一边时，另一边会顺着回退拿到半透明值。所以两组必须同给同不给：
        侧栏半透明而卡片满不透明时，卡片也要显式给 rgba(…, 1.0)。"""
        payload = skin.main_payload("light", "", 100, False, "", 50)
        self.assertIsNotNone(payload["surface"])
        self.assertIsNotNone(payload["sidebar"])

    def test_sidebar_follows_text_pairing(self):
        """浅文字翻档时侧栏底色也用深色底 —— 白字压在白侧栏上同样看不清。"""
        payload = skin.main_payload("light", "", 90, True, "#ffffff", 90)
        self.assertEqual("rgba(24, 27, 33, 0.9)", payload["sidebar"])
        self.assertEqual("rgba(31, 35, 42, 0.9)", payload["sidebar2"])


class TestPairedDarkValuesAgreeWithCss(unittest.TestCase):
    """配对翻档用的边框/警示色必须就是 style.css 深色档的那两个值。

    不然「开了文字颜色」前后，边框和警示红会跳成另一种颜色 —— 和
    SURFACES ↔ _bg_color 那条同源对账是同一个道理：两份独立写下的数据，
    没有机制保证它们相等，只能钉在测试里。"""

    def test_paired_values_exist_in_style_css_dark_blocks(self):
        css = (Path(__file__).resolve().parent.parent
               / "app" / "web" / "style.css").read_text(encoding="utf-8")
        for value in (skin.PAIRED_DARK_BORDER, skin.PAIRED_DARK_DANGER):
            self.assertIn(value, css,
                          f"{value} 不在 style.css 里 —— 深色档改颜色请两边一起改")


class TestStoreKeys(unittest.TestCase):
    def setUp(self):
        self.tmp, self._restore = _isolated_dir()
        self.addCleanup(self._restore)
        from app.store import Store
        self.path = self.tmp / "data.json"
        self.store = Store(path=self.path)

    def _reloaded(self):
        from app.store import Store
        return Store(path=self.path)

    def test_defaults(self):
        self.assertEqual("", self.store.accent)
        self.assertEqual("", self.store.text_color)
        self.assertEqual(100, self.store.card_opacity)
        self.assertEqual(100, self.store.sidebar_opacity)
        self.assertFalse(self.store.bg_image)
        self.assertEqual(0, self.store.bg_dim)

    def test_round_trip(self):
        self.store.accent = "#0D9488"
        self.store.text_color = "#FDE68A"
        self.store.card_opacity = 85
        self.store.sidebar_opacity = 60
        self.store.bg_image = True
        self.store.bg_dim = 30

        other = self._reloaded()
        self.assertEqual("#0d9488", other.accent)   # 落盘时规整成小写
        self.assertEqual("#fde68a", other.text_color)
        self.assertEqual(85, other.card_opacity)
        self.assertEqual(60, other.sidebar_opacity)
        self.assertTrue(other.bg_image)
        self.assertEqual(30, other.bg_dim)

    def test_bad_values_fall_back_to_defaults(self):
        """data.json 是用户能手改的：坏值退默认，不能让程序起不来。"""
        self.store.accent = "#2563eb"
        self.store.text_color = "#ffffff"
        self.store.card_opacity = 50
        self.store.save()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw["accent"] = "red"
        raw["text_color"] = "白色"
        raw["card_opacity"] = "半透明"
        raw["bg_dim"] = None
        raw["bg_image"] = "yes"          # 不是 True 本体，当 False
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        other = self._reloaded()
        self.assertEqual("", other.accent)
        self.assertEqual("", other.text_color)
        self.assertEqual(100, other.card_opacity)
        self.assertEqual(0, other.bg_dim)
        self.assertFalse(other.bg_image)

    def test_setter_clamps_opacity_into_range(self):
        self.store.card_opacity = 500
        self.assertEqual(100, self.store.card_opacity)
        self.store.card_opacity = -5
        self.assertEqual(0, self.store.card_opacity)
        self.store.sidebar_opacity = 500
        self.assertEqual(100, self.store.sidebar_opacity)
        self.store.sidebar_opacity = -5
        self.assertEqual(0, self.store.sidebar_opacity)
        self.store.bg_dim = 99
        self.assertEqual(99, self.store.bg_dim)

    def test_setter_normalizes_bad_accent_to_empty(self):
        self.store.accent = "not-a-color"
        self.assertEqual("", self.store.accent)
        self.store.text_color = "not-a-color"
        self.assertEqual("", self.store.text_color)


class TestApiSkin(unittest.TestCase):
    def setUp(self):
        self.tmp, self._restore = _isolated_dir()
        self.addCleanup(self._restore)
        from app.api import Api
        from app.store import Store
        self.store = Store(path=self.tmp / "data.json")
        self.api = Api(store=self.store)

    def test_set_accent_round_trip(self):
        r = self.api.set_accent("#0d9488")
        self.assertEqual("#0d9488", self.store.accent)
        self.assertEqual("#0d9488", r["accent"])
        self.assertEqual(100, r["cardOpacity"])   # 快照带上外观字段，前端靠它同步

    def test_set_accent_rejects_garbage_loudly(self):
        r = self.api.set_accent(" crimson ")
        self.assertFalse(r["ok"])
        self.assertIn("颜色格式不对", r["message"])
        self.assertEqual("", self.store.accent)   # 原值不动

    def test_set_accent_empty_clears(self):
        self.api.set_accent("#2563eb")
        r = self.api.set_accent("")
        self.assertEqual("", r["accent"])

    def test_set_text_color_round_trip(self):
        r = self.api.set_text_color("#FDE68A")
        self.assertEqual("#fde68a", self.store.text_color)
        self.assertEqual("#fde68a", r["textColor"])   # 快照带上外观字段

    def test_set_text_color_rejects_garbage_loudly(self):
        r = self.api.set_text_color(" 纯白 ")
        self.assertFalse(r["ok"])
        self.assertIn("颜色格式不对", r["message"])
        self.assertEqual("", self.store.text_color)   # 原值不动

    def test_set_text_color_empty_clears(self):
        self.api.set_text_color("#ffffff")
        self.assertEqual("", self.api.set_text_color("")["textColor"])

    def test_set_card_opacity_and_bg_dim_clamp(self):
        self.assertEqual(85, self.api.set_card_opacity(85)["cardOpacity"])
        self.assertEqual(0, self.api.set_card_opacity(-3)["cardOpacity"])
        self.assertEqual(30, self.api.set_bg_dim(30)["bgDim"])
        self.assertEqual(100, self.api.set_card_opacity("abc")["cardOpacity"])

    def test_set_sidebar_opacity_independent(self):
        r = self.api.set_card_opacity(85)
        self.assertEqual(85, r["cardOpacity"])
        self.assertEqual(100, r["sidebarOpacity"])   # 不连累另一边
        r = self.api.set_sidebar_opacity(50)
        self.assertEqual(50, r["sidebarOpacity"])
        self.assertEqual(85, r["cardOpacity"])
        self.assertEqual(0, self.api.set_sidebar_opacity(-3)["sidebarOpacity"])
        self.assertEqual(100, self.api.set_sidebar_opacity("abc")["sidebarOpacity"])

    def test_get_background_image_when_unset(self):
        self.assertEqual({"ok": True, "dataUrl": None}, self.api.get_background_image())

    def test_choose_background_full_chain(self):
        """真图走一遍：选 → 压缩落盘 → 开关打开 → data URL 能读回来。"""
        from PIL import Image

        src = self.tmp / "wallpaper.png"
        Image.new("RGB", (3000, 2000), (120, 40, 200)).save(src, "PNG")
        self.api._ask_open_image = lambda: str(src)

        r = self.api.choose_background_image()
        self.assertTrue(r["ok"], r)

        dest = skin.background_path(self.tmp)
        self.assertTrue(dest.is_file(), "背景图没有落盘")
        with Image.open(dest) as im:
            self.assertLessEqual(max(im.size), skin.BACKGROUND_MAX_EDGE)
        self.assertTrue(self.store.bg_image)

        got = self.api.get_background_image()
        self.assertTrue(got["dataUrl"].startswith("data:image/jpeg;base64,"))

        # 首次设图：不透明度从 100 自动让到 90，让用户第一眼能看到效果
        self.assertEqual(90, self.store.card_opacity)
        self.assertIn("90%", r["message"])

    def test_choose_background_does_not_retouch_opacity_twice(self):
        from PIL import Image

        self.store.card_opacity = 80
        src = self.tmp / "wallpaper.png"
        Image.new("RGB", (100, 100), (0, 0, 0)).save(src, "PNG")
        self.api._ask_open_image = lambda: str(src)

        self.assertTrue(self.api.choose_background_image()["ok"])
        self.assertEqual(80, self.store.card_opacity, "用户调过的透明度不该被动")

    def test_choose_cancelled(self):
        self.api._ask_open_image = lambda: None
        self.assertEqual({"ok": False, "cancelled": True},
                         self.api.choose_background_image())
        self.assertFalse(self.store.bg_image)

    def test_choose_bad_image_fails_with_readable_message(self):
        bogus = self.tmp / "not-an-image.png"
        bogus.write_text("这根本不是图片", encoding="utf-8")
        self.api._ask_open_image = lambda: str(bogus)

        r = self.api.choose_background_image()
        self.assertFalse(r["ok"])
        self.assertTrue(r["message"])
        self.assertFalse(self.store.bg_image)

    def test_remove_background(self):
        from PIL import Image

        src = self.tmp / "wallpaper.png"
        Image.new("RGB", (50, 50), (0, 0, 0)).save(src, "PNG")
        self.api._ask_open_image = lambda: str(src)
        self.api.choose_background_image()

        r = self.api.remove_background_image()
        self.assertTrue(r["ok"])
        self.assertFalse(self.store.bg_image)
        self.assertFalse(skin.background_path(self.tmp).exists())
        self.assertIsNone(self.api.get_background_image()["dataUrl"])

    def test_remove_when_never_set_still_succeeds(self):
        r = self.api.remove_background_image()
        self.assertTrue(r["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
