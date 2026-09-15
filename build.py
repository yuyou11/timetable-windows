"""
打包成独立的 exe。

    python build.py           打包成**文件夹**（默认，推荐）
    python build.py --onefile 打包成单文件 exe
    python build.py --console 带控制台的调试版（单文件）

## 为什么默认是文件夹版

一开始默认是单文件，图的是"一个 exe 直接发给同学"。但日常自用它有两个代价：

  1. **启动慢。** 单文件版每次启动都要把整个压缩包解压到临时目录
     （%TEMP%\_MEIxxxx），几十 MB 的东西每次开机都解一遍。
     文件夹版直接加载，没有这一步。
  2. **会同时出现两个进程。** 单文件是"引导进程 + 真正的应用进程"，
     所以任务管理器里有两个同名进程 —— 看起来像开重了，容易让人心里发毛，
     强杀的时候也容易只杀掉一个。文件夹版就一个进程。

文件夹版的代价是不能单独发 exe，得把整个文件夹一起给。
（要发给同学时就用 `--onefile`，或者把文件夹打成 zip。）

两种产物**可以并存** —— 见下面 main() 里关于清理范围的说明。

## 打包出来是什么样

  · **不需要装 Python** —— Python 解释器和用到的库都在里面了
  · 文件夹版约 40 MB（159 个文件），单文件版约 21 MB
  · 还依赖系统的 Edge WebView2 运行时（Win10 1803+ / Win11 自带，不用管）

## 为什么要写这个脚本，而不是直接敲 pyinstaller 命令

因为参数不少，而且有几个必须做对的地方：

  1. **--add-data 要把 web 目录带上**，否则打包后程序找不到界面文件
  2. **--add-data 还要把 assets 目录带上**，否则窗口图标会静默变回默认的
  3. **--icon 要指向 icon.ico**，否则 exe 在资源管理器里是个通用图标
  4. **--windowed 必须加**，否则运行时会弹出一个黑色控制台窗口

把这几条固化在脚本里，下次打包就不会忘。

⚠️ 第 2 条和第 3 条容易搞混，它们是**两件事**：

    --icon      编译进 exe 的 PE 头 → 资源管理器里那个图标
    --add-data  运行时资源          → 窗口标题栏左上角那个图标

两个都写才对。漏了第 2 条尤其隐蔽：程序照跑、界面照显示，
只是图标变回了默认的 —— 没有任何报错。

图标本身用 `python tools/gen_icon.py` 生成，别手改 app/assets 里的文件。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEP = ";"   # Windows 上 --add-data 的分隔符是分号


def main() -> int:
    # 默认文件夹版；要单文件就显式说 --onefile。
    #
    # 注意：`--onedir` 仍然认（老习惯/老文档里都是它），只是不再是唯一
    # 触发文件夹版的方式。留着它是为了不让以前记下的命令突然改变行为。
    onefile = "--onefile" in sys.argv
    onedir = not onefile

    # 调试用：打包成带控制台的版本，这样启动失败时能看到报错。
    # 正式发布不要用 —— 用户会看到一个黑色命令行窗口。
    console = "--console" in sys.argv
    name = "时间规划表-debug" if console else "时间规划表"

    # 清理。注意**只删自己这一次的产物**，不要把整个 dist 清空 ——
    # 单文件版和文件夹版是两个不同的产物，本来就应该能并存
    # （一个发给同学，一个自己天天开）。早先这里是无条件 rmtree("dist")，
    # 于是打完单文件再打文件夹版，单文件就被悄悄删掉了。
    out_dir = ROOT / "dist"
    # build/ 是 PyInstaller 的中间目录，可以整个删
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    # dist/ 只删这次要生成的那一个
    target = out_dir / (name if onedir else f"{name}.exe")
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    elif target.exists():
        target.unlink()

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", name,
        *(("--console",) if console else ("--windowed",)),   # --windowed 会藏掉控制台，也就藏掉了报错
        # 界面文件必须一起打包，否则打包后窗口一片空白、且没有任何报错。
        # dest 写 "web" 而不是 "app/web" —— 要跟 main.py 里 resource_path()
        # 打包后用的那个相对路径对上。对不上就是白屏，非常难查。
        "--add-data", f"{ROOT / 'app' / 'web'}{SEP}web",
        # 图标也要带上，理由同上：main.py 用 resource_path("assets", "icon.ico")
        # 取窗口图标，打包后找不到的话**不会报错**，只是静默地用回默认图标 ——
        # 又是一个「配置错了但没有任何提示」的坑。
        "--add-data", f"{ROOT / 'app' / 'assets'}{SEP}assets",
        # exe 文件本身的图标。这一项和上面的 --add-data 是**两件事**：
        #   --icon    改的是 exe 在资源管理器里显示的那个图标（编译进 PE 头）
        #   --add-data 是把 icon.ico 当资源带上，供运行时给窗口设置图标
        # 两个都写，资源管理器和窗口标题栏才都是这个图标。
        "--icon", str(ROOT / "app" / "assets" / "icon.ico"),
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "clr_loader",
        # 托盘图标。pystray 的后端是按平台动态选的，PyInstaller 静态分析看不到，
        # 不显式声明的话打出来的包一用托盘就报 ImportError（运行时才炸）。
        "--hidden-import", "pystray._win32",
        "--hidden-import", "PIL.Image",
        "--hidden-import", "PIL.ImageDraw",
        "--hidden-import", "PIL.ImageFont",
        # 排除确定用不到的大块头，能减小体积。
        #
        # ⚠️ 只排**叶子模块**（没人依赖它的那种）。
        #
        # 我一开始顺手把 http.server、email、xmlrpc 也排了，结果程序打包后
        # 一启动就崩：pywebview 内部的 wsgiref.simple_server 依赖 http.server。
        # 这类错误的特点是 —— **打包过程完全成功，没有任何警告**，
        # 只有运行到那一行 import 时才会暴露，而且是给用户的时候才暴露。
        #
        # tkinter / PIL / numpy / pytest 是安全的：这个程序确实不用它们，
        # 而且没有第三方库会去 import 它们。
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        "--exclude-module", "numpy",
        "--exclude-module", "pytest",
        # ⚠️ 注意：**不能排 PIL** —— 托盘图标的绘制要用它。
        # 这也说明了「排除模块」这件事必须谨慎：排掉一个看似无关的库，
        # 可能刚好是某个功能依赖的。而且它只在运行时才暴露。
        str(ROOT / "run.py"),
    ]
    if onefile:
        args.insert(4, "--onefile")

    print("打包中…（第一次会比较慢，一两分钟）\n")
    result = subprocess.run(args, cwd=str(ROOT))
    if result.returncode != 0:
        print("\n打包失败", file=sys.stderr)
        return result.returncode

    exe = ROOT / "dist" / (f"{name}/{name}.exe" if onedir else f"{name}.exe")
    if not exe.exists():
        print(f"\n打包命令成功，但没找到产物：{exe}", file=sys.stderr)
        return 1

    size = exe.stat().st_size / 1024 / 1024
    print(f"\n完成：{exe}")
    if onedir:
        # 文件夹版要连 _internal 一起用，报文件夹总大小才有意义
        whole = sum(f.stat().st_size
                    for f in exe.parent.rglob("*") if f.is_file())
        print(f"大小：{size:.1f} MB（exe 本体）／{whole / 1024 / 1024:.1f} MB（整个文件夹）")
        print("\n⚠️ 这是文件夹版：**exe 不能单独拿出来**，")
        print("   必须和同目录的 _internal 文件夹放在一起才能运行。")
        print("   要发给同学的话，把整个「时间规划表」文件夹打包成 zip 发过去。")
        print("   （或者用 python build.py --onefile 打一个单文件版。）")
    else:
        print(f"大小：{size:.1f} MB")
        print("\n把这个 exe 发给同学就能直接用，对方不需要装 Python。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
