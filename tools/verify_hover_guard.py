"""
Verify the new suppress-flag tests actually fail on the buggy code.

Two mutations, each reverted immediately:
  M1  _create_ball: restore the old unconditional `_ball_suppress_expand = True`
      -> expect test_create_ball_does_not_suppress_unconditionally to FAIL
  M2  _ball_drag_end: suppress unconditionally again
      -> expect test_drag_end_suppresses_only_when_cursor_is_over_the_ball FAIL

app/main.py is restored from a byte-exact copy in a finally block.

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "app" / "main.py"

# Where the fix lives now
DRAG_FIXED = "self._ball_suppress_expand = self._cursor_over_ball()"
DRAG_OLD = "self._ball_suppress_expand = True"

CREATE_ANCHOR = "self._ball_edge = None\n            self._ball_collapsed = False\n"
CREATE_MUTATION = (
    "self._ball_edge = None\n            self._ball_collapsed = False\n"
    "            self._ball_suppress_expand = True  # MUTATION\n"
)

TESTS = [
    "tests.test_ball_ui.TestSuppressExpand.test_create_ball_does_not_suppress_unconditionally",
    "tests.test_ball_ui.TestSuppressExpand.test_drag_end_suppresses_only_when_cursor_is_over_the_ball",
]


def say(msg):
    print(msg, flush=True)


def run(test_id):
    p = subprocess.run([sys.executable, "-m", "unittest", test_id],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode


def main():
    original = TARGET.read_text(encoding="utf-8")

    if original.count(DRAG_FIXED) != 2:
        say("EXPECTED 2 occurrences of the cursor-based suppress assignment, found "
            + str(original.count(DRAG_FIXED)))
        return 2

    results = {}
    try:
        # ---- M1: restore the old unconditional set in _create_ball ----
        say("=== M1: _create_ball sets suppress unconditionally (the old bug) ===")
        mutated = original.replace(CREATE_ANCHOR, CREATE_MUTATION, 1)
        if mutated == original:
            say("  FAIL: could not apply M1 (anchor not found)")
            return 2
        TARGET.write_text(mutated, encoding="utf-8")
        results["M1 guard test"] = run(TESTS[0])
        say("  exit code = " + str(results["M1 guard test"]) + "   (1 = caught it, good)")

        TARGET.write_text(original, encoding="utf-8")

        # ---- M2: drag_end suppresses unconditionally again ----
        say("=== M2: _ball_drag_end suppresses unconditionally ===")
        TARGET.write_text(original.replace(DRAG_FIXED, DRAG_OLD), encoding="utf-8")
        results["M2 behaviour test"] = run(TESTS[1])
        say("  exit code = " + str(results["M2 behaviour test"]) + "   (1 = caught it, good)")
    finally:
        TARGET.write_text(original, encoding="utf-8")

    if TARGET.read_text(encoding="utf-8") != original:
        say("RESTORE FAILED -- app/main.py changed!")
        return 3
    say("app/main.py restored byte-identical")

    # ---- and the real thing must be green ----
    say("=== the real code ===")
    ok = True
    for tid in TESTS:
        code = run(tid)
        ok = ok and code == 0
        say("  " + tid.split(".")[-1] + " -> exit " + str(code))

    say("")
    caught = all(v != 0 for v in results.values())
    if caught and ok:
        say("RESULT: both mutations are caught, real code is green.")
        return 0
    say("RESULT: INCONCLUSIVE -- a mutation was NOT caught, or real code fails.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
