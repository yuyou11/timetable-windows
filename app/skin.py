"""
自定义外观：主色调、背景图、卡片透明度。

## 为什么颜色推导放在 Python，而不是让前端自己算

用户挑一个颜色，界面上的点缀色（按钮、进度条、当前时段高亮……）要跟着
变一整套：主色、浅底（--accent-soft）、深文字（--accent-text），而且
浅色档和深色档各要一套。这套推导**只该有一份**：

  · 写在 JS 里，主窗口能用，悬浮窗/提示条用不上（它们不加载 app.js）；
  · 写在 Python 里，三个窗口都能推（main.py 本来就在往页面推主题），
    而且 HSL 换算、对比度钳制这些数学**可以直接单测**。

所以：Python 算好最终色值 → 推给页面 → 页面只管把 CSS 变量设上。

## 为什么文字颜色要连带「翻卡片底色」

深色图配浅色文字时，如果卡片还是浅色半透明（浅色主题的默认），白字压在
白卡片上照样看不清 —— 用户会把这理解成「调了没用」。所以文字颜色一旦
生效，卡片底色**跟着文字的深浅配对**：浅文字配深色半透明卡片（深色档的
surface），深文字配浅色卡片。边框和警示色同理翻档 —— 浅色档的那两个值
压在深色卡片上，前者刺眼后者发暗。

推导照旧只在 Python 做（和主色调同一条规矩），页面只管设变量。背景图
铺满整个窗口，所以这组覆盖和底色一样设在根上：图到哪，文字跟到哪 ——
不然深色图铺到左侧导航，那边还是主题默认的深色字，照样看不清。

## 为什么悬浮窗只吃主色调、不吃透明度和背景图

小窗口（悬浮窗、提示条）的**窗口底色**只能在创建时定，运行期改不了，
它会在四个圆角处露出来（详见 screen._bg_color）。把卡片改成半透明，
露出来的就是窗口底色 —— 圆角处一圈实色。所以透明度和背景图只推主窗口
（主窗口是矩形 + 系统标题栏，页面铺满整个客户区，没有这个约束）。
主色只是普通的前景色，不碰窗口底色，三个窗口都能安全地跟。

## 为什么推导时要钳亮度

用户可能挑很浅的柠檬黄或很深的藏青。原样当主色：

  · 浅色档下白字压在浅黄按钮上 → 看不见字
  · 深色档下藏青当文字色 → 深底上糊成一片

所以按目标档把亮度钳进「能用」的范围（色相、饱和度原样保留）——
用户挑的是**色调**，我们保证它在哪个底上都还看得清。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Optional

# ============================================================
#  各主题档的底色（surface, surface-2, bg）
#
#  这三对值和 style.css 里的 CSS 变量是**两份独立写下的数据**：
#  style.css 那份被 tests/test_theme.py 钉着，这里这份由
#  tests/test_skin.py 和 screen._bg_color() 对账 —— 改颜色请两边一起改。
# ============================================================

SURFACES: dict[str, tuple[str, str, str]] = {
    "light": ("#ffffff", "#fafbfc", "#f5f6f8"),
    "dark": ("#181b21", "#1f232a", "#0f1115"),
}

#: 背景图统一压成长边不超过这个值的 JPEG —— 用户随手选一张 4K 原图，
#: 不压缩的话一个 data URL 能有十几 MB，过桥会明显卡顿。
BACKGROUND_MAX_EDGE = 2560

#: 背景图源文件的大小上限。超过它基本是没压缩的原图或相机直出，
#: 压缩耗时也长，直接请用户换小一点的。
BACKGROUND_MAX_BYTES = 50 * 1024 * 1024


# ============================================================
#  颜色工具
# ============================================================

def normalize_hex(value: Any) -> Optional[str]:
    """
    把用户输入规整成 `#rrggbb`（小写）。认不出来就返回 None。

    认这三类：`#2563eb`、`2563eb`、`#25e`（三位的简写，展开成六位）。
    `data.json` 是用户能手改的文件，`accent` 存的也是它 ——
    所以读进来必须过这一道，坏了就当「没设置」，不能让程序崩。
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6 or any(ch not in "0123456789abcdef" for ch in text):
        return None
    return "#" + text


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    text = hex_color.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, rgb[0])), max(0, min(255, rgb[1])), max(0, min(255, rgb[2]))
    )


def _rgb_to_hsl(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (v / 255.0 for v in rgb)
    hi, lo = max(r, g, b), min(r, g, b)
    light = (hi + lo) / 2.0
    if hi == lo:
        return 0.0, 0.0, light
    span = hi - lo
    sat = span / (2.0 - hi - lo) if light > 0.5 else span / (hi + lo)
    if hi == r:
        hue = (g - b) / span + (6.0 if g < b else 0.0)
    elif hi == g:
        hue = (b - r) / span + 2.0
    else:
        hue = (r - g) / span + 4.0
    return hue / 6.0, sat, light


def _hsl_to_rgb(hsl: tuple[float, float, float]) -> tuple[int, int, int]:
    hue, sat, light = hsl
    if sat == 0.0:
        v = round(light * 255.0)
        return v, v, v

    def channel(n: float) -> float:
        k = (n + hue * 12.0) % 12.0
        a = sat * min(light, 1.0 - light)
        return light - a * max(-1.0, min(k - 3.0, 9.0 - k, 1.0))

    return (round(channel(0) * 255.0), round(channel(8) * 255.0), round(channel(4) * 255.0))


def _clamp_lightness(hex_color: str, lo: float, hi: float) -> str:
    """保留色相和饱和度，把亮度钳进 [lo, hi]。"""
    h, s, l = _rgb_to_hsl(_hex_to_rgb(hex_color))
    return _rgb_to_hex(_hsl_to_rgb((h, s, min(hi, max(lo, l)))))


def _blend(fore: str, back: str, ratio: float) -> str:
    """fore 以 [ratio] 的不透明度盖在 back 上，返回混合后的实色。"""
    f, b = _hex_to_rgb(fore), _hex_to_rgb(back)
    return _rgb_to_hex(tuple(round(f[i] * ratio + b[i] * (1.0 - ratio)) for i in range(3)))


def _rgba(hex_color: str, alpha: float) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return f"rgba({r}, {g}, {b}, {round(alpha, 2)})"


# ============================================================
#  推导
# ============================================================

def accent_payload(accent: str, theme_pref: str) -> dict[str, Optional[str]]:
    """
    从用户挑的颜色推出当前档的一整套点缀色。

    返回的三个键对应 CSS 变量 --accent / --accent-soft / --accent-text。
    没设主色（accent 为空）时三个键都是 None —— 前端据此**摘掉**覆盖，
    回到样式表里的默认色。这很重要：「恢复默认」必须真的回到默认，
    而不是把上一个颜色留在那里。
    """
    from .screen import resolve_theme

    picked = normalize_hex(accent)
    if picked is None:
        return {"accent": None, "accentSoft": None, "accentText": None}

    if resolve_theme(theme_pref) == "dark":
        base = _clamp_lightness(picked, 0.55, 0.74)   # 深底上要够亮才看得见
        text = _clamp_lightness(picked, 0.62, 0.84)
        soft = _blend(base, SURFACES["dark"][0], 0.16)
    else:
        base = _clamp_lightness(picked, 0.32, 0.58)   # 浅底上白字要压得住
        text = _clamp_lightness(picked, 0.24, 0.46)
        soft = _blend(base, "#ffffff", 0.11)

    return {"accent": base, "accentSoft": soft, "accentText": text}


def _alpha_of(card_opacity: Any) -> float:
    """不透明度（0–100）→ 0..1。手改坏了按 100（不透明）处理。"""
    try:
        n = int(card_opacity)
    except (TypeError, ValueError):
        return 1.0
    return max(0, min(100, n)) / 100.0


# ============================================================
#  背景图上的文字颜色
# ============================================================

#: 文字配对翻成深色卡片时，边框和警示色也换深色档的那一对 ——
#: 浅色档的 #e4e7ec / #dc2626 压在深色半透明卡片上，前者刺眼后者发暗。
#: 这两个值和 style.css 深色档的 --border / --danger 是两份独立写下的
#: 数据，改颜色请两边一起改。
PAIRED_DARK_BORDER = "#2a2f38"
PAIRED_DARK_DANGER = "#f87171"


def text_pairing(text_color: str) -> Optional[dict[str, str]]:
    """
    从用户挑的文字颜色推一套内容区配色，并决定卡片底色跟哪一档。

    返回 dict 带 `text` / `textDim` / `textFaint` / `pairing` 四个键；
    颜色认不出来返回 None（和 normalize_hex 的口径一致：坏了当没设置）。

    ## 深浅的判定和钳制

    亮度 ≥ 0.5 算「浅文字」，配深色卡片；否则算「深文字」，配浅色卡片。
    钳制不是为了对比度 —— 对比度由配对保证 —— 而是把「浅」「深」钉死：
    挑了浅档却被钳回中间灰，配对就自相矛盾了。色相、饱和度原样保留，
    用户挑的仍是**色调**。

    ## dim / faint 怎么推

    直接拿文字色往配对档的底色上混（42% / 68%）。混出来的灰和主题
    自带的 --text-dim / --text-faint 几乎重合，还保住了用户挑的色相；
    比另写一组亮度公式少一份要维护的数据。
    """
    picked = normalize_hex(text_color)
    if picked is None:
        return None

    hue, sat, light = _rgb_to_hsl(_hex_to_rgb(picked))
    if light >= 0.5:
        # 只往浅侧推（不够浅的拉到 0.55），**不设上限** —— 越浅在深底上越
        # 清楚，挑了纯白就该还是纯白。深侧同理，没有下限。
        text = _rgb_to_hex(_hsl_to_rgb((hue, sat, max(0.55, light))))
        pairing = "dark"
    else:
        text = _rgb_to_hex(_hsl_to_rgb((hue, sat, min(0.45, light))))
        pairing = "light"

    base = SURFACES[pairing][0]
    return {
        "text": text,
        "textDim": _blend(text, base, 0.42),
        "textFaint": _blend(text, base, 0.68),
        "pairing": pairing,
    }


def main_payload(theme_pref: str, accent: str, card_opacity: Any,
                 has_bg: bool, text_color: str = "",
                 sidebar_opacity: Any = 100) -> dict[str, Any]:
    """
    主窗口的整套外观覆盖，一次性推给 `window.__applySkin`。

    surface 系（卡片）和 sidebar 系（左侧导航）是**两对**独立的半透明底色，
    透明度分别来自「卡片不透明度」和「侧栏不透明度」两个设置。

    ⚠️ 两对必须**同给同不给**：CSS 里 `--sidebar` 默认回退到 `var(--surface)`
    （引用的是**当前值**）。只给一边的话，另一边会顺着回退拿到被覆盖的
    半透明值 —— 比如「卡片不透明、侧栏半透明」时，卡片会意外跟着变透明。
    所以只要任何一边需要覆盖（开了背景图，或任一透明度不满），两对都显式
    给出，不需要时都给 None 一起回默认。

    文字那组（text / textDim / textFaint / border / danger）只在**开了
    背景图且设了文字颜色**时才给 —— 没图的时候这些字都落在实色卡片上，
    主题默认色就是对着它们调的，不该被覆盖。文字颜色生效时，两组底色的
    基色跟文字深浅配对（见 text_pairing），不再跟全局主题档走。
    """
    from .screen import resolve_theme

    payload = accent_payload(accent, theme_pref)
    alpha = _alpha_of(card_opacity)
    side_alpha = _alpha_of(sidebar_opacity)

    trio = text_pairing(text_color) if has_bg else None
    if trio is not None:
        payload["text"] = trio["text"]
        payload["textDim"] = trio["textDim"]
        payload["textFaint"] = trio["textFaint"]
        pairing = trio["pairing"]
        payload["border"] = PAIRED_DARK_BORDER if pairing == "dark" else None
        payload["danger"] = PAIRED_DARK_DANGER if pairing == "dark" else None
    else:
        payload["text"] = None
        payload["textDim"] = None
        payload["textFaint"] = None
        payload["border"] = None
        payload["danger"] = None
        pairing = resolve_theme(theme_pref)

    if has_bg or alpha < 0.999 or side_alpha < 0.999:
        payload["surface"] = _rgba(SURFACES[pairing][0], alpha)
        payload["surface2"] = _rgba(SURFACES[pairing][1], alpha)
        payload["sidebar"] = _rgba(SURFACES[pairing][0], side_alpha)
        payload["sidebar2"] = _rgba(SURFACES[pairing][1], side_alpha)
    else:
        payload["surface"] = None
        payload["surface2"] = None
        payload["sidebar"] = None
        payload["sidebar2"] = None
    return payload


# ============================================================
#  背景图
# ============================================================

def background_path(dest_dir: Path) -> Path:
    """背景图的落盘位置：数据目录下的固定文件名，新图直接覆盖旧图。"""
    return Path(dest_dir) / "background.jpg"


def save_background(src: str, dest_dir: Path) -> Path:
    """
    把用户选的图处理成数据目录里的 background.jpg。

    统一转成 JPEG（长边不超过 BACKGROUND_MAX_EDGE）再落盘，理由：

      · 用户可能选 PNG（截图）、WebP、甚至带透明通道的图 ——
        直接复制的话扩展名、体积、透明度三件事都不受控
      · JPEG 重编码 + 限边长之后，过桥的 data URL 才有稳定的体积
      · 带透明的图按白底合成（当壁纸用，黑底几乎总是错的）

    任何一步失败都抛 ValueError，message 是**能直接给用户看**的那句。
    """
    from PIL import Image, ImageOps

    path = Path(src)
    if not path.is_file():
        raise ValueError("选中的文件不存在")
    try:
        if path.stat().st_size > BACKGROUND_MAX_BYTES:
            raise ValueError("这张图超过 50MB 了，换一张小一点的")
    except OSError as e:
        raise ValueError(f"读不了这个文件：{e}") from e

    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                flat = Image.new("RGB", im.size, (255, 255, 255))
                flat.paste(im, mask=im.split()[-1])
                im = flat
            elif im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((BACKGROUND_MAX_EDGE, BACKGROUND_MAX_EDGE), Image.LANCZOS)
            dest = background_path(dest_dir)
            Path(dest_dir).mkdir(parents=True, exist_ok=True)
            im.save(dest, "JPEG", quality=85, optimize=True)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"这张图片读不出来（{type(e).__name__}），换一张 JPG 或 PNG 试试") from e
    return dest


def background_data_url(path: Path) -> Optional[str]:
    """
    背景图的 data URL。文件不在或读不出就返回 None —— 前端拿不到图
    就维持默认背景，不该为一张壁纸起不来。
    """
    path = Path(path)
    try:
        if not path.is_file():
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
