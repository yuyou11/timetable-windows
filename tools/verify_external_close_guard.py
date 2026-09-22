"""
变异验证：证明「悬浮窗出现在任务栏 / 关掉之后再也唤不回来」这两条修复
**真的被测试盯着**。

## 为什么需要它

写完测试要问一句：**它在修复前会不会失败？**

不问的话，很可能写出一条恒真的断言 —— 修复前是绿的、修复后也是绿的，
看起来一切正常，实际上什么都没锁住。这个项目真的栽过一次：
`test_save_actually_changes_behaviour` 的第一版断言恒真，被
`tools/verify_daytype_guard.py` 照出来的。

所以这里的做法是：把修复**逐个改坏**，看测试是不是真的会变红。

    改坏 → 跑测试 → 断言它必须红 → 还原 → 逐字节核对还原成功

## ⚠️ 两件必须做对的事

1. **清 .pyc。**
   `BALL_ANIM_MS = 160` 改成 `250` 这种「等长 + 同一秒内还原」的变异，
   .pyc 只校验 (mtime, size) —— 两个条件都撞上，Python 会继续用旧字节码，
   于是**源码明明还原了，测试却还是红的**，给出假结论。
   所以：`python -B` + `PYTHONDONTWRITEBYTECODE=1` + 跑前清 `__pycache__`。

2. **锚点数量必须核对。**
   每个变异都先数一遍「要改的那段出现几次」，不是恰好 1 次就拒绝继续 ——
   否则锚点失效之后，脚本会**一个字节都没改却报告成功**。

跑法：

    python -B tools/verify_external_close_guard.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "app" / "main.py"
APP_JS = ROOT / "app" / "web" / "app.js"

TEST_TARGET = "tests.test_ball_ui"

#: (文件, 正确写法, 改坏的写法, 说明)
MUTATIONS = [
    (MAIN,
     "# 取消这次关闭：按设计它只该「隐藏」，窗口本身要留着。\n        return False",
     "# 取消这次关闭：按设计它只该「隐藏」，窗口本身要留着。\n        return True",
     "M1 不再拦截外部关闭（窗口会被真的销毁）"),

    (MAIN,
     "if self._shutting_down:\n            return True\n\n        try:\n"
     "            self.api.hide_ball()",
     "if False:\n            return True\n\n        try:\n"
     "            self.api.hide_ball()",
     "M2 退出流程也被拦下（程序将永远退不掉）"),

    (MAIN,
     "self.ball = None\n        try:\n            sw, sh = screen_size()\n"
     "            self._create_ball(sw, sh)",
     "try:\n            sw, sh = screen_size()\n"
     "            self._create_ball(sw, sh)",
     "M3 重建前不清掉失效引用（_create_ball 会直接返回）"),

    (MAIN,
     "return winutil.window_rect(winutil.TITLE_BALL) is not None",
     "return True",
     "M4 只看记忆里的引用，不去问系统"),

    (MAIN,
     "ball_ready = bool(winutil.wait_for_window(winutil.TITLE_BALL, HWND_TIMEOUT))",
     "time.sleep(0.9)\n"
     "            ball_ready = bool(winutil._find(winutil.TITLE_BALL))",
     "M5 _polish 改回固定睡眠（任务栏按钮那个原 bug）"),

    (MAIN,
     "self.ball.events.closing += self._on_ball_closing",
     "pass  # MUTATION: 没订阅 closing",
     "M6 不订阅 closing 事件"),

    (APP_JS,
     "CLASS: '#2563eb'",
     "CLASS: '#3b82f6'",
     "M7 KIND_COLOR 在两个页面之间漂移"),
]


def clear_bytecode() -> None:
    """.pyc 会让「还原了却还是红的」这种假结论出现，每次跑前必须清"""
    for p in ROOT.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)


def run_tests() -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TIMETABLE_DATA_DIR"] = tempfile.mkdtemp(prefix="tt_mut_")
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", TEST_TARGET],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def summary_line(out: str) -> str:
    for line in reversed(out.strip().splitlines()):
        if line.strip():
            return line.strip()
    return "(没有输出)"


def main() -> int:
    watched = [MAIN, APP_JS]
    originals = {p: p.read_text(encoding="utf-8") for p in watched}

    print("=" * 62)
    print("baseline：改坏之前必须是绿的，否则本脚本什么也证明不了")
    print("=" * 62)
    clear_bytecode()
    code, out = run_tests()
    print("  " + summary_line(out))
    if code != 0:
        print()
        print("baseline 就不是绿的 —— 先把它修绿再跑这个脚本。")
        print(out)
        return 2

    print()
    print("=" * 62)
    print("逐个把修复改坏，看测试会不会变红")
    print("=" * 62)

    results: list[tuple[str, bool | None]] = []

    try:
        for path, fixed, broken, label in MUTATIONS:
            original = originals[path]
            hits = original.count(fixed)
            if hits != 1:
                print(f"  SKIP  {label}：锚点出现 {hits} 次（期望 1 次），拒绝瞎改")
                results.append((label, None))
                continue

            path.write_text(original.replace(fixed, broken), encoding="utf-8")
            clear_bytecode()
            code, out = run_tests()
            caught = code != 0
            results.append((label, caught))

            mark = "抓到  " if caught else "没抓到"
            print(f"  {mark} {label}")
            if not caught:
                print(f"         ← 改坏之后测试仍然是绿的，这条修复没被锁住")

            path.write_text(original, encoding="utf-8")
    finally:
        # 无论中途出什么事（异常、Ctrl+C）都要把源码放回去
        for path, text in originals.items():
            try:
                if path.read_text(encoding="utf-8") != text:
                    path.write_text(text, encoding="utf-8")
            except OSError:
                pass

    print()
    print("=" * 62)
    print("还原核对（逐字节）")
    print("=" * 62)
    broken_files = []
    for path, text in originals.items():
        current = path.read_text(encoding="utf-8")
        if current != text:
            broken_files.append(path)
            print(f"  ✗ {path.relative_to(ROOT)} 没有还原干净！")
        else:
            print(f"  ✓ {path.relative_to(ROOT)}")

    caught_n = sum(1 for _, c in results if c is True)
    skipped_n = sum(1 for _, c in results if c is None)
    missed = [lbl for lbl, c in results if c is False]

    print()
    print(f"抓到 {caught_n} / {len(MUTATIONS)}" +
          (f"，跳过 {skipped_n}" if skipped_n else ""))

    if broken_files:
        print()
        print("还原失败 —— 请手工检查上面标 ✗ 的文件（可以用 git diff 看）")
        return 3

    if missed:
        print()
        print("下面这些变异没有被测试抓到，说明对应的修复没有被锁住：")
        for lbl in missed:
            print(f"  · {lbl}")
        return 1

    print()
    print("PASS：每一条修复都被测试盯着 —— 改坏它们，测试就会变红。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
