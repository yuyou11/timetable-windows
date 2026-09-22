"""
课前提醒的调度线程。

## 为什么必须是一个独立的线程

`webview.start()` 是**阻塞的主循环**，主线程进了它就出不来了（见 `main.py`
的 `run()`）。所以得有人在后台一直盯着时间 —— 就是这个线程。

## 为什么不「算到下一个提醒时刻，一觉睡到那时候」

听起来更省。但用户随时可能改设置、改课表：一觉睡到明天早上的话，
今天下午刚调的提醒时间要到明天才生效。所以这里**最多睡 60 秒**就重算一次，
`kick()` 则是设置一变立刻叫醒，不用等睡满。

代价几乎为零：空闲时这个线程只是停在 `Event.wait()` 上，
没有循环、没有计算、没有 DOM 操作。实测每分钟醒来做的三件事
（`store.load()` + `template_set()` + `engine.moments()`）合计约 1 毫秒。

## 为什么要单独一个模块

它不碰窗口、不碰 webview，只依赖 store 和 engine —— 是个纯粹的后台调度器。
原来住在 `main.py` 里，和窗口生命周期、悬浮窗几何混在一起，
结果那 90 行「纯逻辑」被埋在一千行窗口代码中间。

搬出来还有个好处：**能单测了**。以前要验它就得把整个 App 和窗口都造出来。
"""

from __future__ import annotations

import threading
from datetime import datetime

from . import engine
from .store import Store


class ReminderScheduler(threading.Thread):
    """
    课前提醒。

    设计要点和手机版一致：
      · 最多睡 60 秒就重算一次 —— **不**精确睡到提醒时刻，
        因为用户随时可能改设置，睡太久会让改动延迟生效（理由见模块文档）
      · 去重键（日期|开始时刻|课程名）持久化，避免同一节课提醒两次
      · 只对「课」提醒，不对吃饭睡觉提醒（那些不需要提前准备）
    """

    def __init__(self, store: Store, on_fire) -> None:
        super().__init__(daemon=True, name="reminder")
        self.store = store
        self.on_fire = on_fire
        self._stop = threading.Event()
        self._wake = threading.Event()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def kick(self) -> None:
        """设置变了 → 立刻重算，不用等睡满"""
        self._wake.set()

    def run(self) -> None:
        while not self._stop.is_set():
            delay = self._next_delay()
            # wait 会在超时或 kick() 时返回，两种情况下都重新算一遍
            self._wake.wait(timeout=delay)
            self._wake.clear()
            if self._stop.is_set():
                return
            self._check_and_fire()

    def _next_delay(self) -> float:
        """
        距离下一次该检查还有多少秒。

        不精确计算到「提醒时刻」，而是**最多睡 60 秒**——
        因为用户随时可能改设置、改课表，睡太久会让改动延迟生效。
        60 秒一次的空转几乎不耗电，同时保证了响应性。

        真正省资源的地方在于：**这个线程什么都不做就只是在等待**，
        没有循环、没有计算、没有 DOM 操作。
        """
        return 60.0

    def _check_and_fire(self) -> None:
        try:
            store = self.store
            store.load()               # 重新读盘，拿到别的窗口刚写进去的改动

            lead = store.remind_lead
            if lead <= 0:
                return

            now = datetime.now()
            today = now.date()
            minute = now.hour * 60 + now.minute

            # 模板和策略成对取 —— 提醒排的时刻必须和界面显示的一致，
            # 否则会出现「界面说今天 06:55 起，闹钟却按 07:25 响」。
            templates, policy = store.template_set()
            items = engine.moments(
                today, store.week_of(today), store.courses(), templates, policy
            )

            # 窗口取 ±1 分钟：60 秒的检查间隔下，正好能覆盖到
            for m in items:
                if not m.is_course:
                    continue
                if abs(m.start - lead - minute) > 1:
                    continue

                key = f"{today.isoformat()}|{m.start}|{m.title}"
                if store.last_reminder_key == key:
                    continue
                store.last_reminder_key = key

                left = m.start - minute
                when = "现在就开始" if left <= 0 else f"{left} 分钟后"
                self.on_fire(m, when)
                return
        except Exception:
            # 提醒失败不能把线程搞死 —— 那样之后所有提醒都没了，而且没人知道
            pass
