"""
Cross-platform format parity: can the desktop REPRODUCE the phone's export
byte for byte?

    python tools/verify_format_parity.py

The standard (7.3) says: "导出同一份规划，永远得到逐字节相同的文件."
Field order is fixed as format -> version -> term -> dayTypes -> courses ->
templates, and templates follow the canonical A/B_TRAIN_A/.../SUNDAY order.

The phone's docs/example-full.json is a REAL mobile export. So the strongest
available check is:

    parse it  ->  re-serialize it  ->  compare with the original file

If that comes out identical, the two implementations agree on every detail a
human would notice: field order, indentation, spacing, array layout, and the
week-string compaction. If it differs, the diff tells you exactly what to fix.

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: 手机版导出的真实文件。两个项目是**同级目录**，所以从仓库往上找一层 ——
#: 不写死绝对路径（公开仓库里留着本机路径既没用、也暴露目录结构）。
PHONE = ROOT.parent / "06-android-timetable" / "docs" / "example-full.json"


def main() -> int:
    if not PHONE.exists():
        print("phone export not found: " + str(PHONE), file=sys.stderr)
        return 2

    from app import format_spec

    original = PHONE.read_text(encoding="utf-8")

    parsed = format_spec.parse(original, fallback_total_weeks=19)
    if parsed.term is None:
        print("FAIL: no term segment parsed", file=sys.stderr)
        return 1

    rebuilt = format_spec.serialize(
        term_name=parsed.term.name,
        start_date=parsed.term.start_date,
        total_weeks=parsed.term.total_weeks,
        courses=parsed.courses,
        templates=parsed.templates,
        day_types=parsed.day_types,
    )

    # normalise line endings only -- CRLF vs LF is a checkout artefact,
    # not a format difference
    a = original.replace("\r\n", "\n").rstrip("\n").splitlines()
    b = rebuilt.replace("\r\n", "\n").rstrip("\n").splitlines()

    print("phone file lines   : " + str(len(a)))
    print("desktop rebuilt    : " + str(len(b)))
    print()

    if a == b:
        print("PARITY: the desktop reproduces the phone's export byte for byte.")
        print("        (field order, indentation, spacing, weeks compaction -- all agree)")
        return 0

    print("DIFFERS. First differences:")
    diff = list(difflib.unified_diff(a, b, "phone", "desktop", lineterm="", n=1))
    for line in diff[:60]:
        print("  " + line)
    if len(diff) > 60:
        print("  ... (" + str(len(diff) - 60) + " more diff lines)")
    print()
    print("This is the list of things to align. See the standard section 7.3.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
