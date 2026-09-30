"""
单实例检测：程序已经在跑的时候，再次启动就弹窗拦下。

## 为什么用命名互斥体（named mutex）

判定「程序是不是已经在跑」有几种常见做法，各有坑：

    · 找窗口标题        主窗口可能正收在托盘里（隐藏着），找不到 ≠ 没在跑
    · 锁文件            程序崩溃后锁文件残留，第二次启动永远起不来 —— 得自己处理陈旧锁
    · 枚举进程名        开发时叫 python.exe、打包后叫 时间规划表.exe，两套名字两套逻辑

命名互斥体是 Windows 为这件事准备的原语：内核对象随**进程**存活，
正常退出、崩溃、被任务管理器强杀，互斥体都自动消失 —— 没有任何残留要清理。
`Local\\` 前缀把名字限定在当前登录会话内：一台机器多个人同时登录
（快速用户切换 / 多用户远程桌面）时互不干扰，各自都能跑一份
（数据本来也是按用户存在 %APPDATA% 的，这正是想要的粒度）。

名字里**故意不带版本号**：覆盖升级的瞬间（旧版还在跑、新版刚双开）也必须
能正确判定「已在运行」，带了版本号就拦不住这个场景了。

## 用法（见 run.py）

    if not instance.acquire():
        instance.warn_already_running()
        return 0          # 第二个实例到此为止
"""

from __future__ import annotations

import ctypes

#: 互斥体名字。Local\\ 限定当前登录会话（见模块 docstring）
MUTEX_NAME = "Local\\TimetableSingleInstance"

#: CreateMutexW 打开了一个**已存在**的同名互斥体时，GetLastError() 返回这个
ERROR_ALREADY_EXISTS = 183

#: 拿到的句柄。内核对象只要进程不退出就一直有效，故意不 CloseHandle ——
#: 存进模块级变量只是防止有人误以为它需要手动管理；真正释放靠进程退出。
_held_handle = None


def acquire(name: str = MUTEX_NAME) -> bool:
    """
    尝试获取单实例互斥体。

    返回 True = 拿到了（本进程就是那个唯一实例，可以继续启动）；
    返回 False = 已经有实例在跑（调用方应该弹窗告知，然后退出）。

    [name] 默认用全局互斥体；测试传独立名字，免得机器上正跑着真程序时
    测试被误判成「第二个实例」。

    ⚠️ 创建互斥体本身失败（理论上几乎不可能）时**返回 True 放行**：
    拦死会让程序永远起不来，而放行的最坏结果只是出现两个实例 ——
    两害相权，永远选能用的那个。
    """
    global _held_handle
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True
    _held_handle = handle
    return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def warn_already_running() -> None:
    """
    弹一个原生消息框，告诉用户程序已经在跑了。

    ## 为什么用 MessageBoxW 而不是起个 pywebview 小窗口

    调用时机在一切 GUI 栈初始化**之前**（这也是拦截的意义 —— 别让重复的
    实例白干任何活）。这时 webview 还没起来，为一句提示起一套 WebView2
    太重了；系统自带的 MessageBoxW 零依赖、模态、样式也和系统一致。

    MB_TOPMOST + MB_SETFOREGROUND：弹出时必须被人看见 —— 这个框存在的
    全部意义就是「解释为什么什么都没发生」。双击了 exe 却毫无反应，
    用户会以为程序坏了；框被压在别的窗口底下的话，等于没弹。
    """
    MB_OK = 0x00000000
    MB_ICONINFORMATION = 0x00000040
    MB_SETFOREGROUND = 0x00010000
    MB_TOPMOST = 0x00040000
    ctypes.windll.user32.MessageBoxW(
        None,
        "时间规划表已经在运行了。\n\n"
        "如果桌面上没看到窗口，请看屏幕右下角的托盘图标 ——\n"
        "程序可能只是收在了那里。",
        "时间规划表",
        MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST,
    )
