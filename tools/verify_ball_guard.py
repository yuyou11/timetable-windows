"""
Verify the new regression tests actually guard the fix.

A test that passes both before and after a fix proves nothing. So this script:

  1. backs up app/main.py
  2. neutralizes the `if not self.store.ball_enabled` guards (simulating the
     buggy version), runs the two new tests, and records the result
  3. restores app/main.py from the backup -- in a finally block, so it is put
     back even if step 2 explodes
  4. runs the same tests on the real file and records the result

Expected:
  guards removed -> tests FAIL  (the bug is caught)
  guards present -> tests PASS

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "app" / "main.py"

GUARD = "if not self.store.ball_enabled:"
NEUTERED = "if False:  # guard neutralized on purpose"

TESTS = "tests.test_ball_ui.TestBallStaysClosed"


def run_tests():
    env = dict(os.environ)
    env["TIMETABLE_DATA_DIR"] = tempfile.mkdtemp(prefix="tt_verify_")
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", TESTS, "-v"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def summarize(output):
    """Pull out just the per-test verdicts and the final line."""
    lines = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.endswith("... ok") or stripped.endswith("... FAIL") or stripped.endswith("... ERROR"):
            name = stripped.split(" ")[0]
            lines.append("    " + name + "  ->  " + stripped.split("...")[-1].strip())
        elif stripped.startswith("Ran ") or stripped == "OK" or stripped.startswith("FAILED"):
            lines.append("    " + stripped)
    return "\n".join(lines) if lines else "    (no output parsed)"


def main():
    script = TARGET.read_text(encoding="utf-8")
    count = script.count(GUARD)
    print("guards found in app/main.py: " + str(count))
    if count != 2:
        print("EXPECTED 2 GUARDS -- refusing to continue")
        return 2

    backup = None
    try:
        # ---- phase 1: buggy version ----
        TARGET.write_text(script.replace(GUARD, NEUTERED), encoding="utf-8")
        code, out = run_tests()
        print("")
        print("=== WITHOUT the guard (simulating the reported bug) ===")
        print("exit code = " + str(code) + "   (non-zero means the tests caught it)")
        print(summarize(out))
        without_ok = code != 0
        without_out = out
    finally:
        # always put the real file back
        TARGET.write_text(script, encoding="utf-8")
        backup = TARGET.read_text(encoding="utf-8")

    if backup != script:
        print("")
        print("RESTORE FAILED -- app/main.py does not match the original!")
        return 3
    print("")
    print("app/main.py restored and verified byte-identical")

    # ---- phase 2: fixed version ----
    code, out = run_tests()
    print("")
    print("=== WITH the guard (the actual committed code) ===")
    print("exit code = " + str(code) + "   (0 means all green)")
    print(summarize(out))

    print("")
    if without_ok and code == 0:
        print("RESULT: the tests are a real guard -- they fail without the fix, pass with it.")
        return 0
    print("RESULT: INCONCLUSIVE -- the tests do not discriminate the two versions.")
    if not without_ok:
        print("        (they passed even without the guard)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
