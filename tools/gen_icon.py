"""
从原始素材生成应用图标。

    python tools/gen_icon.py

读  tools/icon-source.png          （原始素材，**不要手改**）
写  app/assets/icon.png            （给托盘用，正方形、居中、已清理透明边）
写  app/assets/icon.ico            （给窗口图标和 exe 用，多尺寸）

三个用途：

    托盘图标    pystray 要一个 PIL Image     → icon.png
    窗口图标    pywebview 要一个 .ico 路径   → icon.ico
    exe 图标    PyInstaller 的 --icon        → icon.ico

## 为什么不直接把素材当图标用

`clean()` 会修两件事，都得做掉才能当图标：

  1. **不居中** —— 裁掉四周空白后重新居中。
     最早那版素材确实歪：圆环落在画布 (13,7)-(199,192)，左边空 13px、
     右边空 0px，整个图案往右偏了约 6px，放进标题栏肉眼能看出是歪的。
  2. **透明背景不干净** —— 把接近透明的像素压成完全透明（`ALPHA_FLOOR`）。
     最早那版素材四个角的 alpha 是 3 而不是 0，缩放时这层"若有若无"的底
     会被放大成脏边。

> ⚠️ **当前的 `icon-source.png` 这两条都已经不成立了**（实测：内容
> bbox 就是整张 200×200，四角 alpha 全为 0）。所以现在跑这个脚本，
> `clean()` 实质上是空操作。
>
> 保留它是因为**素材随时可能被换掉**，而这两个问题一旦出现都是"看起来
> 只是有点脏"、很难归因的类型。多跑两步裁剪和阈值处理，换来的是
> 「换了任何素材都不会糊」。

另外 .ico 必须是**多尺寸**的：Windows 会在不同场合挑不同大小
（任务栏 32、Alt+Tab 48、大图标视图 256...），只塞一张图的话，
系统只能自己缩，效果比我们预先缩好要差。

## 为什么是「缩放」而不是「重画」

这个图案是几何图形（两个圆 + 时钟指针），理论上可以按测出来的颜色和比例
重新画一遍，那样每个尺寸都是清晰的。但代价是**可能和用户给的设计不像** ——
重画就是重新解释一遍，比例、颜色、圆角都可能跑偏。

缩放则原样保留用户的图，只在尺寸上做文章。代价是 256 那张要放大
（素材有效内容 187px），会略软一点 —— 但它是纯色图形，实际看不出。

**保留设计 > 追求极限清晰。** 何况这个图标真正被看到的尺寸是 16-48px，
全都在缩小的方向上。
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:                                       # noqa: BLE001
    print("需要 Pillow：pip install pillow", file=sys.stderr)
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "icon-source.png"
OUT_DIR = ROOT / "app" / "assets"

#: .ico 里要包含的尺寸。16/24/32/48 是最常用的四档，
#: 64/128 给大图标视图，256 给现代高 DPI 下的超大显示。
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]

#: 边长，托盘和窗口都用这个尺寸的 PNG
PNG_SIZE = 256

#: 图案四周留白占边长的比例。
#: 原素材是 6.5%（187/200），这里取 4% —— 略紧一点，
#: 小尺寸下（16px）图案才够大、看得清是什么。
MARGIN = 0.04

#: alpha 低于这个值就当作完全透明。
#: 原素材四角是 alpha=3（"几乎透明"而不是"透明"），
#: 留着它缩放之后会在边缘留下脏边。
ALPHA_FLOOR = 8


def clean(img):
    """
    把接近透明的像素压成完全透明，然后裁掉四周的空白。

    顺序很重要：**先清理再裁剪**。反过来的话，那圈 alpha=3 的残留
    会被当成"有内容"，裁出来的框就是整张画布，等于没裁。
    """
    img = img.convert("RGBA")

    # alpha 低于阈值的，整个像素清零（连 RGB 一起清，免得缩放时颜色渗出来）
    alpha = img.getchannel("A").point(lambda v: 255 if v >= ALPHA_FLOOR else 0)
    img.putalpha(alpha)

    bbox = alpha.getbbox()
    if bbox is None:
        raise SystemExit(f"素材看起来是完全透明的，检查一下 {SRC}")
    return img.crop(bbox)


def centered_square(img, size):
    """
    把裁好的图案居中放进一个正方形画布，四周留出固定比例的空白。

    居中这件事必须显式做：素材本身偏右 6px，直接缩放的话
    这个偏移会一起被带进图标里。
    """
    inner = max(1, int(round(size * (1 - 2 * MARGIN))))
    scaled = img.resize((inner, inner), Image.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(scaled, ((size - inner) // 2, (size - inner) // 2), scaled)
    return canvas


def main():
    if not SRC.exists():
        print(f"找不到素材：{SRC}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    src = Image.open(SRC)
    print(f"素材 {SRC.name}  {src.size[0]}x{src.size[1]}  模式 {src.mode}")

    cleaned = clean(src)
    print(f"清理后内容尺寸 {cleaned.size[0]}x{cleaned.size[1]}")

    # ---- icon.png：给托盘 ----
    big = centered_square(cleaned, PNG_SIZE)
    png = OUT_DIR / "icon.png"
    big.save(png)
    print(f"写出 {png.relative_to(ROOT)}  {PNG_SIZE}x{PNG_SIZE}  "
          f"{png.stat().st_size / 1024:.1f} KB")

    # ---- icon.ico：给窗口和 exe ----
    #
    # Pillow 的 save(format="ICO", sizes=[...]) 会自己把图缩成各个尺寸。
    # 但它**直接缩原图**，不会逐尺寸重新居中 —— 所以先给它一张已经
    # 规范化好的大图（PNG_SIZE >= 最大的 ICO 尺寸），这样每一档都是从
    # 同一张干净的图缩下来的，不会有二次缩放。
    if PNG_SIZE < max(ICO_SIZES):
        raise SystemExit("PNG_SIZE 必须 >= 最大的 ICO 尺寸，否则大图标会被放大")

    ico = OUT_DIR / "icon.ico"
    big.save(ico, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    print(f"写出 {ico.relative_to(ROOT)}  含尺寸 {ICO_SIZES}  "
          f"{ico.stat().st_size / 1024:.1f} KB")

    # 回读核对：存进去的每一档都要真的在，而且都是正方形。
    # 只检查"文件写成功了"是不够的 —— 尺寸漏掉一档照样能写成功，
    # 但 Windows 用到大图标时就会露馅（显示成模糊的放大版）。
    with Image.open(ico) as check:
        got = sorted(check.ico.sizes())
    want = sorted((s, s) for s in ICO_SIZES)
    if got != want:
        print(f"x .ico 里的尺寸对不上：期望 {want}，实际 {got}", file=sys.stderr)
        return 1
    print(f"核对通过：.ico 里确实有 {len(got)} 档尺寸")

    print("\n完成。改这个图标请改 tools/icon-source.png 后重跑本脚本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
