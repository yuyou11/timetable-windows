"""
Verify the slide-animation tests actually fail when the behaviour is broken.

Mutations, each reverted immediately:
  M1  ball.html: shrink the panel padding     -> the handle-zone check must fail
  M2  ball.html: flip the arrow overlay side  -> the overlay-side check must fail
  M3  ball.html: hide the panel again         -> the always-visible check must fail
  M4  ball.js:   re-add window.ballAnim       -> the removal check must fail
  M5  ballwin.py: re-add the ballAnim inject -> the removal check must fail
  M6  dock.py:   re-widen EDGES with top      -> the store whitelist check must fail
  M7  dock.py:   make the tween snap          -> the monotonic-tween check must fail

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "app" / "web" / "ball.html"
JS = ROOT / "app" / "web" / "ball.js"
BALLWIN = ROOT / "app" / "ballwin.py"
DOCK = ROOT / "app" / "dock.py"

T = "tests.test_ball_ui."
D = "tests.test_dock."
S = "tests.test_store.TestClampingAndNormalisation."

MUTATIONS = [
    ("M1 handle padding removed", HTML, "padding: 9px 24px;", "padding: 9px 11px;",
     D + "TestBallPageContract.test_panel_padding_reserves_the_handle_zones"),
    ("M2 arrow overlay side flipped", HTML,
     "body.dock-right #tab { left: 1px; }",
     "body.dock-right #tab { right: 1px; }",
     D + "TestBallPageContract.test_arrow_overlay_sits_on_the_exposed_side"),
    ("M3 panel hidden again", HTML,
     "padding: 9px 24px;", "padding: 9px 24px;\n      display: none;",
     D + "TestBallPageContract.test_panel_is_always_visible"),
    ("M4 ballAnim re-added to JS", JS,
     "window.applyDock = function (edge, collapsed) {",
     "window.ballAnim = function (edge, collapsing) {};\n\n"
     "window.applyDock = function (edge, collapsed) {",
     T + "TestSlideContract.test_ball_anim_is_gone_on_both_sides"),
    ("M5 ballAnim inject re-added to ballwin", BALLWIN,
     "# 短过渡淡入淡出。\n        self._push_ball_layout()",
     "# 短过渡淡入淡出。\n        self._eval_js(\n"
     "            self.ball, \"window.ballAnim && window.ballAnim('right', false)\")",
     T + "TestSlideContract.test_ball_anim_is_gone_on_both_sides"),
    ("M6 top/bottom edges re-allowed", DOCK,
     'EDGES = ("left", "right")',
     'EDGES = ("left", "right", "top", "bottom")',
     S + "test_ball_edge_setter_accepts_both_side_edges"),
    ("M7 tween snaps instead of ramping", DOCK,
     "return int(round(x0 + (x1 - x0) * p)), y0, w, h",
     "return x1, y0, w, h",
     D + "TestTweenSlide.test_x_moves_monotonically_toward_the_target"),
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
    files = (HTML, JS, BALLWIN, DOCK)
    originals = {p: p.read_text(encoding="utf-8") for p in files}

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
    say("all " + str(len(files)) + " files restored byte-identical: " + str(restored))
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
