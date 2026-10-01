"""
Is the day-type UI actually wired to anything?

Two failure modes this checks, both silent in production:

  M1  save_weekday_map() writes the mapping but the engine ignores it
      -> the user changes the weekday mapping and nothing happens.
      Simulated by making the save a no-op.

  M2  the "mapping target must be a managed day type" guard is removed
      -> the UI would accept a mapping to a nonexistent day type.

Both mutations must be CAUGHT for the tests to be worth anything.

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 日型那几个桥接方法原来住在 api.py，split_api 重构后搬进了
# api_templates.py —— 守卫的锚点跟着代码走（2026-09 修）。
API = ROOT / "app" / "api_templates.py"

TESTS = "tests.test_day_type_policy.TestDayTypeManageApi"

SAVE_BODY = "        self.store.save_day_type_config(DayTypeConfig(\n            types=config.types, weekday_map=tuple(new_map),\n            early_type=config.early_type,\n        ))"
SAVE_NOOP = "        pass  # MUTATION: mapping is silently dropped"

GUARD = "            if t is None or not config.manages(t):\n                return {\"ok\": False,\n                        \"message\": f\"{WEEKDAY_CN[i + 1]}映射到的日型「{raw}」不存在\"}"
GUARD_OFF = "            if False:  # MUTATION: guard disabled\n                pass"


def say(msg):
    print(msg, flush=True)


def clear_bytecode():
    for d in ROOT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def run(test_id):
    clear_bytecode()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TIMETABLE_DATA_DIR"] = tempfile.mkdtemp(prefix="tt_dt_")
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", test_id],
                       cwd=str(ROOT), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    shutil.rmtree(env["TIMETABLE_DATA_DIR"], ignore_errors=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    original = API.read_text(encoding="utf-8")

    say("=== baseline ===")
    code, out = run(TESTS)
    say("  exit code = " + str(code) + "   (0 expected)")
    if code != 0:
        say("  BASELINE FAILS")
        for line in out.splitlines():
            if line.startswith(("FAIL:", "ERROR:")):
                say("    | " + line)
        return 2

    results = []
    try:
        for label, fixed, broken, test_id in [
            ("M1 save is a no-op (mapping silently dropped)",
             SAVE_BODY, SAVE_NOOP,
             TESTS + ".test_save_weekday_map_roundtrip"),
            ("M2 mapping-target guard removed",
             GUARD, GUARD_OFF,
             TESTS + ".test_save_weekday_map_validation"),
        ]:
            say("")
            say("=== " + label + " ===")
            if fixed not in original:
                say("  SKIP: pattern not found")
                results.append((label, None))
                continue
            API.write_text(original.replace(fixed, broken), encoding="utf-8")
            code, out = run(test_id)
            caught = code != 0
            say("  exit code = " + str(code)
                + "   (" + ("caught it, good" if caught else "NOT CAUGHT") + ")")
            n = sum(1 for line in out.splitlines() if line.startswith(("FAIL:", "ERROR:")))
            say("  failing tests: " + str(n))
            results.append((label, caught))
    finally:
        API.write_text(original, encoding="utf-8")

    restored = API.read_text(encoding="utf-8") == original
    clear_bytecode()
    say("")
    say("app/api_templates.py restored byte-identical: " + str(restored))
    if not restored:
        return 3

    say("")
    ok = all(c is True for _, c in results)
    for label, caught in results:
        say("  " + label + " : " + str(caught))
    say("")
    if ok:
        say("RESULT: both mutations caught -- the day-type UI is really wired up.")
        return 0
    say("RESULT: a mutation slipped through.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
