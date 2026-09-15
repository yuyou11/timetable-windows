"""
Do the new week-grid tests actually catch the "only 5 days" bug?

Puts `range(1, 6)` back into api.get_week and confirms the tests go red.
A test that passes on both the broken and the fixed code proves nothing.

    python tools/verify_grid_guard.py

Uses `python -B` + PYTHONDONTWRITEBYTECODE and clears __pycache__ before each
run: a previous mutation test in this project was fooled by a stale .pyc
because the mutated and restored files had the same size and the same mtime
second (see the project notes).

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TESTS = "tests.test_week_grid"

#: (path, fixed text, broken text, label)
MUTATIONS = [
    (
        ROOT / "app" / "api.py",
        "for i in range(1, WEEK_DAYS + 1):",
        "for i in range(1, 6):",
        "day columns back to Monday-Friday",
    ),
    (
        ROOT / "app" / "api.py",
        "for dow in range(1, WEEK_DAYS + 1):",
        "for dow in range(1, 6):",
        "row cells back to Monday-Friday",
    ),
]


def say(msg):
    print(msg, flush=True)


def clear_bytecode():
    for d in ROOT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def run():
    clear_bytecode()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TIMETABLE_DATA_DIR"] = tempfile.mkdtemp(prefix="tt_grid_")
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", TESTS],
                       cwd=str(ROOT), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    shutil.rmtree(env["TIMETABLE_DATA_DIR"], ignore_errors=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    api = ROOT / "app" / "api.py"
    original = api.read_text(encoding="utf-8")

    say("=== baseline (fixed) ===")
    code, out = run()
    say("  exit code = " + str(code) + "   (0 expected)")
    if code != 0:
        say("  BASELINE FAILS -- fix the code before mutation testing")
        for line in out.splitlines():
            if line.startswith(("FAIL:", "ERROR:")):
                say("    | " + line)
        return 2

    results = []
    try:
        for path, fixed_text, broken_text, label in MUTATIONS:
            say("")
            say("=== " + label + " ===")
            src = original
            if src.count(fixed_text) < 1:
                say("  SKIP: pattern not found")
                results.append((label, None))
                continue
            path.write_text(src.replace(fixed_text, broken_text), encoding="utf-8")
            code, out = run()
            caught = code != 0
            say("  exit code = " + str(code)
                + "   (" + ("caught it, good" if caught else "NOT CAUGHT") + ")")
            n_fail = sum(1 for line in out.splitlines()
                         if line.startswith(("FAIL:", "ERROR:")))
            say("  failing tests: " + str(n_fail))
            results.append((label, caught))
            path.write_text(src, encoding="utf-8")
    finally:
        api.write_text(original, encoding="utf-8")

    restored = api.read_text(encoding="utf-8") == original
    clear_bytecode()
    say("")
    say("app/api.py restored byte-identical: " + str(restored))
    if not restored:
        return 3

    say("")
    say("================ SUMMARY ================")
    ok = True
    for label, caught in results:
        say("  " + label + " : " + str(caught))
        if caught is not True:
            ok = False
    say("")
    if ok:
        say("RESULT: every mutation is caught -- the tests are a real guard")
        say("        against the 'weekend courses are invisible' bug.")
        return 0
    say("RESULT: at least one mutation slipped through.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
