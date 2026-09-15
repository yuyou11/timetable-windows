"""
Is the swallowed first hover caused by the Python-side suppress flag, or does
the page never receive the mouseenter at all?

That distinction decides where the fix goes, so measure it instead of guessing:

  A) build a variant with _ball_suppress_expand forced off
     -> if the first hover NOW expands, the page DOES send the event and the
        Python guard is what eats it
     -> if it still does not expand, the page never saw the mouse, and the
        Python guard is innocent

Restores app/main.py from a byte-exact copy afterwards (finally block).

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "app" / "main.py"
EXE = ROOT / "dist" / "时间规划表.exe"
PROBE = ROOT / "tools" / "probe_ball_hover.py"

ORIGINAL = "if self._ball_suppress_expand:"
VARIANT = "if False:  # suppress temporarily disabled for this experiment"


def say(msg):
    print(msg, flush=True)


def build(log_name):
    with open(ROOT / "build-logs" / log_name, "w", encoding="utf-8",
              errors="replace") as fh:
        p = subprocess.run([sys.executable, "build.py"], cwd=str(ROOT),
                           stdout=fh, stderr=subprocess.STDOUT)
    say("  build exit code = " + str(p.returncode))
    return p.returncode == 0


def probe(log_name, tag):
    with open(ROOT / "build-logs" / log_name, "w", encoding="utf-8",
              errors="replace") as fh:
        p = subprocess.run([sys.executable, str(PROBE), str(EXE), tag],
                           cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)
    say("  probe exit code = " + str(p.returncode) + "   (1 = hover swallowed)")
    for line in (ROOT / "build-logs" / log_name).read_text(
            encoding="utf-8", errors="replace").splitlines():
        if "expanded on" in line or "REPRODUCED" in line or "NOT reproduced" in line:
            say("    | " + line)
    return p.returncode


def main():
    if not EXE.exists():
        say("FAIL: no exe: " + str(EXE))
        return 2

    source = TARGET.read_text(encoding="utf-8")
    if source.count(ORIGINAL) != 1:
        say("EXPECTED 1 occurrence of the suppress check, found "
            + str(source.count(ORIGINAL)))
        return 2

    fixed_exe = Path(tempfile.gettempdir()) / "timetable_fixed2.exe"
    shutil.copy2(EXE, fixed_exe)

    try:
        say("=== building a variant with the suppress flag disabled ===")
        TARGET.write_text(source.replace(ORIGINAL, VARIANT), encoding="utf-8")
        if not build("probe_suppressoff_build.log"):
            say("FAIL: build failed")
            return 2
        say("=== probing that variant ===")
        code_off = probe("probe_suppressoff_run.log", "suppressoff")
    finally:
        TARGET.write_text(source, encoding="utf-8")

    if TARGET.read_text(encoding="utf-8") != source:
        say("RESTORE FAILED -- app/main.py changed!")
        return 3
    say("app/main.py restored byte-identical")

    # put the real exe back and probe it for contrast
    shutil.copy2(fixed_exe, EXE)
    say("=== probing the normal build (suppress flag active) ===")
    code_on = probe("probe_suppresson_run.log", "suppresson")

    say("")
    say("with suppress DISABLED -> probe exit " + str(code_off)
        + "   (0 = first hover expanded)")
    say("with suppress ACTIVE   -> probe exit " + str(code_on)
        + "   (1 = first hover swallowed)")
    say("")
    if code_off == 0 and code_on == 1:
        say("CONCLUSION: the page DOES send mouseenter; the Python suppress flag")
        say("            is what swallows the first hover.")
        return 0
    if code_off == 1:
        say("CONCLUSION: even with suppress off the first hover does nothing,")
        say("            so the page never receives the mouse event.")
        say("            (the suppress flag is innocent)")
        return 0
    say("CONCLUSION: unexpected -- read the probe logs.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
