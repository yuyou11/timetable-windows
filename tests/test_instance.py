"""
单实例检测的测试。

    python -m unittest tests.test_instance -v

⚠️ 全部测试**只允许有一个**用例真的去调 `instance.acquire()`：
互斥体一旦被当前测试进程拿到，进程内后续任何 acquire 都会返回 False ——
拆成多个用例的话，用例之间的执行顺序（unittest 按字母序）会决定谁看到
「第一个」谁看到「第二个」，测试就从"验证行为"变成"验证运气"了。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import instance


class TestMutexSemantics(unittest.TestCase):
    def test_first_acquire_succeeds_and_second_is_refused(self):
        """
        同一进程内两次 acquire：第一次 True，第二次 False。

        这就是「程序已经在跑，再次启动被拦下」的全部机制 ——
        Windows 语义保证同名互斥体第二次 CreateMutexW 会拿到
        ERROR_ALREADY_EXISTS，和跨进程的情形完全一致。

        ⚠️ 必须传**独立的测试名字**：机器上正跑着真程序的话，全局互斥体
        已经被它拿了，这里第一次 acquire 就会 False —— 测试从「验证行为」
        变成「验证运气」。这条护栏在真机上真的触发过一次。
        """
        name = f"Local\\TimetableTest-{os.getpid()}"
        self.assertTrue(instance.acquire(name), "第一次 acquire 应该拿到互斥体")
        self.assertFalse(instance.acquire(name), "第二次 acquire 应该被拒（已有实例）")

    def test_handle_is_kept_alive(self):
        """句柄要存住：内核对象随进程存活，中途不该有「忘了自己拿过锁」这种状态"""
        self.assertIsNotNone(instance._held_handle)


class TestContract(unittest.TestCase):
    """跨文件契约：不碰内核，只看源码。"""

    RUN_PY = Path(__file__).resolve().parent.parent / "run.py"

    def test_run_py_wires_the_check_before_anything_heavy(self):
        """
        run.py 必须接线单实例检测，而且位置要在 `from app.main import` **之前**。

        拦截的全部意义就是「第二个实例什么都不做」——
        检测要是排在导入主模块之后，拦截就只剩仪式感了。
        """
        src = self.RUN_PY.read_text(encoding="utf-8")
        self.assertIn("from app.instance import", src,
                      "run.py 没有导入单实例检测")
        self.assertIn("if not acquire():", src, "run.py 没有调用单实例检测")
        self.assertIn("warn_already_running()", src, "被拦下时没有弹窗告知用户")
        self.assertLess(
            src.index("from app.instance import"),
            src.index("from app.main import"),
            "单实例检测必须排在导入 app.main 之前 —— 晚了就白拦了",
        )

    def test_mutex_name_is_session_scoped_and_version_free(self):
        """
        名字必须带 Local\\ 前缀（限定当前登录会话），并且**不含版本号**。

        带版本号的话，覆盖升级瞬间「旧版还在跑、新版刚启动」就拦不住了 ——
        两个版本各拿各的互斥体，同时跑起来，托盘里出现两个图标。
        """
        self.assertTrue(instance.MUTEX_NAME.startswith("Local\\"),
                        "互斥体名字要限定在登录会话内")
        self.assertNotIn("2.", instance.MUTEX_NAME, "名字里不许带版本号")


if __name__ == "__main__":
    unittest.main(verbosity=2)
