"""
Run tools/verify_close_button.py against BOTH versions of the app.

A standalone PASS proves nothing -- maybe the test never reaches the buggy
path at all. (That is not hypothetical: the first version of the e2e script
tested a free-floating ball, where main._ball_hover() returns early, so the
trigger was never reached and it "passed" for no reason.)

So this driver:
  1. backs up the already-built fixed exe
  2. neutralizes the `if not self.store.ball_enabled` guards in app/main.py
  3. rebuilds -> a buggy exe
  4. runs the e2e against the buggy exe   (the bug should reproduce -> FAIL)
  5. restores app/main.py  (finally block)
  6. restores the fixed exe from the backup
  7. runs the e2e against the fixed exe   (should PASS)

Expected: buggy FAILS, fixed PASSES. Anything else means the harness is
vacuous and the root-cause story is incomplete.

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "app" / "main.py"
EXE = ROOT / "dist" / "时间规划表.exe"
E2E = ROOT / "tools" / "verify_close_button.py"

GUARD = "if not self.store.ball_enabled:"
NEUTERED = "if False:  # guard neutralized on purpose"


def say(msg):
    print(msg, flush=True)


def build(tag):
    log_file = ROOT / "build-logs" / ("e2e_" + tag + "_build.log")
    with open(log_file, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.run(
            [sys.executable, "build.py"],
            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
        )
    say("  build(" + tag + ") exit code = " + str(proc.returncode)
        + "   log: " + log_file.name)
    return proc.returncode == 0


def run_e2e(tag):
    log_file = ROOT / "build-logs" / ("e2e_" + tag + "_run.log")
    with open(log_file, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.run(
            [sys.executable, str(E2E), str(EXE)],
            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
        )
    say("  e2e(" + tag + ") exit code = " + str(proc.returncode)
        + "   log: " + log_file.name)
    for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if any(k in line for k in ("PASS", "FAIL", "INCONCLUSIVE", "visible after",
                                   "ball_enabled after", "rect after", "expanded",
                                   "definitely landed")):
            say("    | " + line)
    return proc.returncode


def main():
    if not EXE.exists():
        say("FAIL: no exe to test: " + str(EXE))
        return 2

    source = TARGET.read_text(encoding="utf-8")
    if source.count(GUARD) != 2:
        say("EXPECTED 2 GUARDS in app/main.py, found " + str(source.count(GUARD)))
        return 2

    backup_exe = Path(tempfile.gettempdir()) / "timetable_fixed_backup.exe"
    shutil.copy2(EXE, backup_exe)
    say("backed up the fixed exe -> " + str(backup_exe))

    try:
        # ---------------- buggy build ----------------
        say("")
        say("=== 1. building the BUGGY version (guards removed) ===")
        TARGET.write_text(source.replace(GUARD, NEUTERED), encoding="utf-8")
        if not build("buggy"):
            say("FAIL: buggy build failed")
            return 2
        say("=== 2. running the e2e against the BUGGY version ===")
        code_buggy = run_e2e("buggy")
    finally:
        TARGET.write_text(source, encoding="utf-8")

    if TARGET.read_text(encoding="utf-8") != source:
        say("RESTORE FAILED -- app/main.py does not match the original!")
        return 3
    say("")
    say("app/main.py restored and verified byte-identical")

    # ---------------- fixed build ----------------
    shutil.copy2(backup_exe, EXE)
    say("=== 3. running the e2e against the FIXED version (exe restored) ===")
    code_fixed = run_e2e("fixed")

    say("")
    say("buggy exit code = " + str(code_buggy) + "   (1 = bug reproduced, good)")
    say("fixed exit code = " + str(code_fixed) + "   (0 = clean, good)")
    say("")
    if code_buggy == 1 and code_fixed == 0:
        say("RESULT: the e2e harness discriminates. Root cause confirmed end to end.")
        return 0
    if code_buggy == 1 and code_fixed == 1:
        say("RESULT: the bug reproduces even with the guards -- the fix is INCOMPLETE.")
        return 1
    if code_buggy == 3:
        say("RESULT: INCONCLUSIVE -- the buggy run never reached the button.")
        say("        The harness cannot prove anything about this bug yet.")
        return 3
    say("RESULT: unexpected -- investigate the logs.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
