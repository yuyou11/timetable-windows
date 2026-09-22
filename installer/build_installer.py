"""
把打包好的文件夹版做成安装程序（Inno Setup）。

    python installer/build_installer.py

## 为什么要有个脚本，而不是直接双击 .iss

和 `build.py` 一样的理由：有几件必须做对的事，固化成检查比"记得"可靠。

  1. **先确认打包产物在。** 不然 Inno 会报一堆"找不到文件"，
     而真正的原因（还没跑 build.py）反而看不出来。
  2. **拒绝把个人课表打进安装包。** 见下面 `check_no_personal_data`。
  3. **找到 ISCC.exe。** Inno Setup 可能装在 3 个不同位置，
     而且它**不在 PATH 里**。
  4. **顺便把 .iss 的编码摆正。** 见下面 `ensure_utf8_bom`。

## 前置条件

  · 已经跑过 `python build.py`（生成 dist\时间规划表\）
  · 装了 Inno Setup 6：`winget install JRSoftware.InnoSetup`
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent

DIST_APP = ROOT / "dist" / "时间规划表"
ISS = HERE / "timetable.iss"
ISL = HERE / "ChineseSimplified.isl"
OUT_DIR = ROOT / "dist" / "installer"

APP_NAME = "时间规划表"

#: ISCC.exe 可能在这些地方。按"先用户级、后机器级"排 ——
#: winget 默认装的是用户级那一份。
ISCC_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def find_iscc() -> Path | None:
    for p in ISCC_CANDIDATES:
        if p.exists():
            return p
    found = shutil.which("ISCC")
    return Path(found) if found else None


def check_no_personal_data() -> list[str]:
    """
    确认产物里没有夹带**个人数据**。

    ## 为什么专门查这个

    `.gitignore` 里特意写了一条：`课表*.json` / `*课表*.json` 不进版本库，
    理由是「里面是个人课表（学校、年级专业、课程名、教室号）」。

    安装包是要发给同学的 —— 一旦带上，等于把自己的课表随程序一起散出去了，
    而且对方装完只看得到自己的界面，**根本不会发现包里还藏着一份别人的课表**。

    所以这里按扩展名和文件名两路查，宁可误报也不能漏。
    程序正常运行时数据写在 %APPDATA%\\Timetable，本来就不该出现在 dist 里。
    """
    if not DIST_APP.is_dir():
        return []

    suspicious: list[str] = []
    for p in DIST_APP.rglob("*"):
        if not p.is_file():
            continue
        name = p.name
        if name == "data.json" or name.endswith(".broken.json"):
            suspicious.append(str(p.relative_to(DIST_APP)))
        elif "课表" in name and name.endswith(".json"):
            suspicious.append(str(p.relative_to(DIST_APP)))
    return suspicious


def ensure_utf8_bom(path: Path) -> bool:
    """
    给含中文的 Inno 脚本补上 UTF-8 BOM。

    ## 为什么非做不可

    Inno Setup 6 读 .iss / .isl 时：**有 BOM 就按 UTF-8 读，没有就按系统
    代码页（这台机器是 GBK/936）读。**

    我们的 .iss 是 UTF-8 无 BOM 的 —— 那样中文会被当成 GBK 解码，
    变成一串乱码显示在安装界面上。而且**编译不会报错**，只有人眼看得见。

    这和这个项目里反复出现的那类问题同源：**不报错的错**。
    （`ChineseSimplified.isl` 是从官方翻译仓库下的，它本来就带 BOM，
    所以只需要照顾我们自己写的 .iss。）

    返回是否做了修改。
    """
    if not path.exists():
        return False
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        return False
    path.write_bytes(b"\xef\xbb\xbf" + data)
    return True


def main() -> int:
    print("=" * 60)
    print(f"打包「{APP_NAME}」安装程序")
    print("=" * 60)

    # ---- 1. 产物在不在 ----
    exe = DIST_APP / f"{APP_NAME}.exe"
    if not exe.exists():
        print()
        print(f"找不到打包产物：{exe}")
        print()
        print("先跑一次：")
        print("    python build.py")
        return 1
    files = sum(1 for p in DIST_APP.rglob("*") if p.is_file())
    size_mb = sum(p.stat().st_size for p in DIST_APP.rglob("*") if p.is_file()) / 1024 / 1024
    print(f"\n产物：{DIST_APP}")
    print(f"      {files} 个文件，{size_mb:.1f} MB")

    # ---- 2. 不能夹带个人数据 ----
    suspicious = check_no_personal_data()
    if suspicious:
        print()
        print("⚠️ 产物里疑似夹带了个人数据，拒绝继续：")
        for s in suspicious:
            print("   " + s)
        print()
        print("安装包是要发给别人的，个人课表不能跟着一起出去。")
        print("确认这些文件确实该在里面之后，再从 dist 里删掉重打。")
        return 1
    print("检查：产物里没有个人数据 ✓")

    # ---- 3. 语言文件 ----
    if not ISL.exists():
        print()
        print(f"缺少中文语言文件：{ISL}")
        print("它是 Inno Setup 的简体中文翻译，见 installer/README 的说明。")
        return 1

    # ---- 4. 修 .iss 编码 ----
    if ensure_utf8_bom(ISS):
        print("检查：给 timetable.iss 补上了 UTF-8 BOM（否则中文界面会乱码）")
    else:
        print("检查：timetable.iss 的 BOM 正常 ✓")

    # ---- 5. 找编译器 ----
    iscc = find_iscc()
    if iscc is None:
        print()
        print("找不到 Inno Setup 的编译器 ISCC.exe。")
        print()
        print("装一下：")
        print("    winget install JRSoftware.InnoSetup")
        print()
        print("找过这些位置：")
        for p in ISCC_CANDIDATES:
            print("    " + str(p))
        return 1
    print(f"编译器：{iscc}")

    # ---- 6. 编译 ----
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print()
    print("编译中…（40 MB 的内容要压一会儿，通常半分钟到两分钟）")
    print()
    result = subprocess.run([str(iscc), str(ISS)], cwd=str(HERE))

    if result.returncode != 0:
        print()
        print("编译失败，看上面的报错。")
        return result.returncode

    # ---- 7. 报产物 ----
    produced = sorted(
        OUT_DIR.glob("*.exe"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not produced:
        print()
        print(f"编译命令成功，但 {OUT_DIR} 里没有 exe", file=sys.stderr)
        return 1

    out = produced[0]
    print()
    print("=" * 60)
    print(f"完成：{out}")
    print(f"大小：{out.stat().st_size / 1024 / 1024:.1f} MB")
    print("=" * 60)
    print()
    print("这个安装程序：")
    print("  · 装给当前用户，不需要管理员权限（不弹 UAC）")
    print("  · 默认装到 %LOCALAPPDATA%\\Programs\\" + APP_NAME)
    print("  · 带开始菜单快捷方式，桌面图标可选")
    print("  · 控制面板「应用和功能」里能卸载")
    print("  · 卸载**不会**删掉课表数据（在 %APPDATA%\\Timetable）")
    print()
    print("发给同学就发这一个 exe，对方不需要装 Python。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
