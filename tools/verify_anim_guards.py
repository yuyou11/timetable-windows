"""
Verify the animation tests actually fail when the animation is broken.

Mutations, each reverted immediately:
  M1  ball.html: drop --ball-anim-ms            -> the cross-file check must fail
  M2  ball.html: remove the collapse fast-fade  -> the asymmetry check must fail
  M3  ball.html: let buttons wrap again         -> the nowrap check must fail
  M4  ball.js:   rename window.ballAnim         -> the bridge check must fail
  M5  main.py:   make BALL_ANIM_MS inconsistent -> the cross-file check must fail

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "app" / "web" / "ball.html"
JS = ROOT / "app" / "web" / "ball.js"
MAIN = ROOT / "app" / "main.py"

T = "tests.test_ball_ui."

MUTATIONS = [
    ("M1 CSS var removed", HTML, "--ball-anim-ms: 160ms;", "--wrong-name: 160ms;",
     T + "TestAnimTimingMatchesCss.test_css_duration_equals_python_constant"),
    ("M2 fast collapse fade removed", HTML,
     "      transition-duration: calc(var(--ball-anim-ms) * 0.45);\n"
     "      transition-delay: 0ms;\n", "",
     T + "TestAnimTimingMatchesCss.test_collapse_fade_is_faster_than_expand_fade"),
    ("M3 button nowrap removed", HTML,
     "      white-space: nowrap;\n      overflow: hidden;\n"
     "      transition: background .15s, border-color .15s;",
     "      transition: background .15s, border-color .15s;",
     T + "TestAnimTimingMatchesCss.test_buttons_do_not_wrap"),
    ("M4 ballAnim renamed", JS, "window.ballAnim = function",
     "window.ballAnimRenamed = function",
     T + "TestAnimTimingMatchesCss.test_ball_js_defines_ball_anim"),
    ("M5 python duration drifted", MAIN, "BALL_ANIM_MS = 160",
     "BALL_ANIM_MS = 250",
     T + "TestAnimTimingMatchesCss.test_css_duration_equals_python_constant"),
]


def say(msg):
    print(msg, flush=True)


def clear_bytecode():
    """
    删掉 __pycache__。

    ⚠️ 这一步不能省，我在这里栽过一次：

    变异 M5 把 `BALL_ANIM_MS = 160` 改成 `250`，两个字符串**长度相同**，
    而改写和还原发生在**同一秒内**。Python 的 .pyc 校验只比对
    (源文件修改时间, 文件大小) —— 两个条件都撞上了，
    于是它认为缓存的字节码还有效，**继续用那份 250 的**。

    结果：还原明明成功（源码逐字节核对过），测试却报 250，
    我一度以为 main.py 没还回去。真相是**缓存撒了谎**。

    同类教训在这个项目里已经不是第一次：工具本身会给出
    「错误但看起来很像结论」的答案（见踩坑 24、27）。
    """
    import shutil as _shutil
    for d in ROOT.rglob("__pycache__"):
        _shutil.rmtree(d, ignore_errors=True)


def run(test_id):
    clear_bytecode()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", test_id],
                       cwd=str(ROOT), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode


def main():
    originals = {p: p.read_text(encoding="utf-8") for p in (HTML, JS, MAIN)}

    # sanity: the unmutated code must be green first
    say("=== baseline (unmutated) ===")
    for _, _, _, _, tid in MUTATIONS:
        code = run(tid)
        say("  " + tid.split(".")[-1] + " -> exit " + str(code))
        if code != 0:
            say("  BASELINE FAILS -- fix the code before mutation testing.")
            return 2

    results = []
    try:
        for name, path, old, new, tid in MUTATIONS:
            say("")
            say("=== " + name + " ===")
            src = originals[path]
            if old not in src:
                say("  SKIP: pattern not found")
                results.append((name, None))
                continue
            path.write_text(src.replace(old, new, 1), encoding="utf-8")
            code = run(tid)
            caught = code != 0
            say("  exit code = " + str(code) + "   ("
                + ("caught it, good" if caught else "NOT CAUGHT") + ")")
            results.append((name, caught))
            path.write_text(src, encoding="utf-8")
    finally:
        for path, src in originals.items():
            path.write_text(src, encoding="utf-8")

    restored = all(p.read_text(encoding="utf-8") == s for p, s in originals.items())
    say("")
    say("all three files restored byte-identical: " + str(restored))
    if not restored:
        return 3

    say("")
    say("================ SUMMARY ================")
    all_caught = True
    for name, caught in results:
        say("  " + name + " : " + str(caught))
        if caught is None or caught is False:
            all_caught = False
    say("")
    if all_caught:
        say("RESULT: every mutation is caught by the test suite.")
        return 0
    say("RESULT: at least one mutation slipped through -- the tests are weaker")
    say("        than they look.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
