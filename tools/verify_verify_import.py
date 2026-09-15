"""
Does tools/verify_import.py itself catch the `_parsed` bug?

I fixed a blind spot in verify_import.py (it used to bypass import_from_file).
A tool that reports PASS on the buggy code is worse than no tool -- so prove it
goes red BEFORE trusting its green.

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "app" / "api.py"
SCHEDULE = ROOT / "课表-2026-09-14.json"

FIXED = 'return {"ok": True, "preview": self._preview_dict(parsed)}'
BROKEN = ('return {"ok": True, "preview": self._preview_dict(parsed), '
          '"_parsed": parsed}')


def say(msg):
    print(msg, flush=True)


def clear_bytecode():
    for d in ROOT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def run_verify():
    clear_bytecode()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TIMETABLE_DATA_DIR"] = tempfile.mkdtemp(prefix="tt_vv_")
    p = subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools" / "verify_import.py"),
         str(SCHEDULE)],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    shutil.rmtree(env["TIMETABLE_DATA_DIR"], ignore_errors=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    if not SCHEDULE.exists():
        say("missing: " + str(SCHEDULE))
        return 2

    original = API.read_text(encoding="utf-8")
    if original.count(FIXED) != 1:
        say("EXPECTED 1 occurrence of the fixed return line, found "
            + str(original.count(FIXED)))
        return 2

    try:
        say("=== verify_import.py against the FIXED code ===")
        code_ok, out_ok = run_verify()
        say("  exit code = " + str(code_ok) + "   (0 expected)")

        say("")
        say("=== verify_import.py against the BUGGY code ===")
        API.write_text(original.replace(FIXED, BROKEN), encoding="utf-8")
        code_bad, out_bad = run_verify()
        say("  exit code = " + str(code_bad) + "   (non-zero = caught it, good)")
        for line in out_bad.splitlines():
            if "序列化" in line or "import_from_file" in line:
                say("    | " + line.strip())
    finally:
        API.write_text(original, encoding="utf-8")

    if API.read_text(encoding="utf-8") != original:
        say("RESTORE FAILED")
        return 3
    clear_bytecode()
    say("")
    say("app/api.py restored byte-identical")

    say("")
    if code_ok == 0 and code_bad != 0:
        say("RESULT: verify_import.py now discriminates -- it would have")
        say("        caught this bug. The blind spot is closed.")
        return 0
    say("RESULT: INCONCLUSIVE (fixed=" + str(code_ok)
        + ", buggy=" + str(code_bad) + ")")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
