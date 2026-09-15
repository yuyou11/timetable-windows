"""
Is the day-type UI actually wired to anything?

Two failure modes this checks, both silent in production:

  M1  save_day_types() writes the setting but does NOT notify / the engine
      ignores it  -> the user changes the setting and nothing happens.
      Simulated by making save_day_types a no-op.

  M2  the fallback not-in-enabled guard is removed
      -> the UI would accept a self-contradictory combination.

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
API = ROOT / "app" / "api.py"

TESTS = "tests.test_day_type_policy.TestDayTypeApi"

SAVE_BODY = "        self.store.save_day_type_policy(\n            DayTypePolicy(enabled=frozenset(chosen), fallback=fb)\n        )"
SAVE_NOOP = "        pass  # MUTATION: setting is silently dropped"

GUARD = "        if fb not in chosen:"
GUARD_OFF = "        if False:  # MUTATION: guard disabled"


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
            ("M1 save is a no-op (setting silently dropped)",
             SAVE_BODY, SAVE_NOOP,
             TESTS + ".test_save_actually_changes_behaviour"),
            ("M2 fallback-not-in-enabled guard removed",
             GUARD, GUARD_OFF,
             TESTS + ".test_fallback_outside_enabled_is_refused_by_the_ui"),
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
    say("app/api.py restored byte-identical: " + str(restored))
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
