"""
Verify the new serializable-return tests actually catch the `_parsed` bug.

Puts the exact line that broke import back into api.py, runs the new tests,
and confirms they go red. Then restores from a byte-exact copy.

    python tools/verify_serializable_guard.py

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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "app" / "api.py"

FIXED = 'return {"ok": True, "preview": self._preview_dict(parsed)}'
BROKEN = ('return {"ok": True, "preview": self._preview_dict(parsed), '
          '"_parsed": parsed}')

TESTS = "tests.test_api_serializable"


def say(msg):
    print(msg, flush=True)


def clear_bytecode():
    for d in ROOT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def run(target):
    clear_bytecode()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    import tempfile
    env["TIMETABLE_DATA_DIR"] = tempfile.mkdtemp(prefix="tt_mut_")
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", target],
                       cwd=str(ROOT), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    shutil.rmtree(env["TIMETABLE_DATA_DIR"], ignore_errors=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    original = API.read_text(encoding="utf-8")

    if original.count(FIXED) != 1:
        say("EXPECTED exactly 1 occurrence of the fixed return line, found "
            + str(original.count(FIXED)))
        say("(if the code changed shape, update FIXED in this script)")
        return 2

    results = {}
    try:
        say("=== baseline (fixed code) ===")
        code, out = run(TESTS)
        results["baseline"] = code
        say("  exit code = " + str(code) + "   (0 expected)")

        say("")
        say("=== with `_parsed` put back (the bug that broke import) ===")
        API.write_text(original.replace(FIXED, BROKEN), encoding="utf-8")
        code, out = run(TESTS)
        results["mutated"] = code
        say("  exit code = " + str(code) + "   (non-zero = caught it, good)")
        # show which assertions fired
        for line in out.splitlines():
            if line.startswith("FAIL:") or line.startswith("ERROR:"):
                say("    | " + line)
            elif "TypeError" in line and "serializ" in line.lower():
                say("    | " + line.strip()[:120])
    finally:
        API.write_text(original, encoding="utf-8")

    if API.read_text(encoding="utf-8") != original:
        say("RESTORE FAILED -- app/api.py changed!")
        return 3
    clear_bytecode()
    say("")
    say("app/api.py restored byte-identical, __pycache__ cleared")

    say("")
    say("baseline = " + str(results["baseline"]) + ", mutated = " + str(results["mutated"]))
    if results["baseline"] == 0 and results["mutated"] != 0:
        say("RESULT: the tests catch the bug — red without the fix, green with it.")
        return 0
    say("RESULT: INCONCLUSIVE — the tests do not discriminate.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
