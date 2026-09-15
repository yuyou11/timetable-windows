"""
Does the SHIPPED exe actually contain the fixed api.py?

    python tools/verify_frozen_api.py [path-to-exe]

Why this is worth doing: "I ran build.py from the fixed source tree" is an
assumption, not evidence. The build could pick up a stale __pycache__, or I
could have rebuilt before saving. The only proof is to open the artifact and
read the code that is inside it.

Method: app modules live in the PYZ archive embedded in the exe, and PyInstaller
zlib-compresses them -- so a plain byte search of the exe finds NOTHING (I tried
that first; the positive control came back False, which is how I knew the search
was meaningless). So unpack it properly with PyInstaller's own readers.

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "dist" / "时间规划表" / "时间规划表.exe"


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not exe.exists():
        print("not found: " + str(exe), file=sys.stderr)
        return 2
    print("exe: " + str(exe))

    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    archive = CArchiveReader(str(exe))

    # find the PYZ entry
    pyz_name = None
    for name in archive.toc:
        if name.lower().endswith(".pyz"):
            pyz_name = name
            break
    if not pyz_name:
        print("no PYZ entry found; toc follows:", file=sys.stderr)
        for name in list(archive.toc)[:40]:
            print("   " + name, file=sys.stderr)
        return 2
    print("pyz entry: " + pyz_name)

    data = archive.extract(pyz_name)
    if data is None:
        print("could not extract the PYZ", file=sys.stderr)
        return 2
    if isinstance(data, tuple):
        data = data[1]
    if isinstance(data, io.BytesIO):
        data = data.read()
    if not isinstance(data, (bytes, bytearray)):
        print(f"unexpected PYZ payload type: {type(data)}", file=sys.stderr)
        return 2

    # ZlibArchiveReader wants a real path -- it parses an optional "?offset"
    # suffix off the filename, so a BytesIO blows up inside it.
    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix="tt_frozen_"))
    pyz_path = tmpdir / "PYZ.pyz"
    pyz_path.write_bytes(bytes(data))
    print("extracted PYZ to " + str(pyz_path)
          + "  (" + str(len(data)) + " bytes)")

    pyz = ZlibArchiveReader(str(pyz_path))
    names = [n for n in pyz.toc if n.startswith("app.")]
    print("app modules inside the exe: " + ", ".join(sorted(names)))

    if "app.api" not in pyz.toc:
        print("app.api not found in the PYZ", file=sys.stderr)
        return 2

    code = pyz.extract("app.api")
    if code is None:
        print("could not extract app.api", file=sys.stderr)
        return 2
    # code may come back as a code object or as marshalled bytes
    if isinstance(code, (bytes, bytearray)):
        import marshal
        code = marshal.loads(bytes(code))
    if hasattr(code, "co_consts") and not hasattr(code, "co_names"):
        import marshal
        code = marshal.loads(code.co_consts[0])

    # Walk the code object graph collecting every string constant and name.
    seen: set[str] = set()

    def walk(co):
        if not hasattr(co, "co_consts"):
            return
        for const in co.co_consts:
            if isinstance(const, str):
                seen.add(const)
            elif hasattr(const, "co_consts"):
                walk(const)
        for name in getattr(co, "co_names", ()):
            seen.add(name)
        for name in getattr(co, "co_varnames", ()):
            seen.add(name)

    walk(code)

    control = "没有待导入的数据，请重新选择文件"
    print()
    print("positive control (a string that IS in api.py):")
    print("  found = " + str(control in seen) + "   (must be True, else the")
    print("  extraction failed and this check proves nothing)")

    print()
    print("bug marker '_parsed' (the key that broke import):")
    found_bug = "_parsed" in seen
    print("  found = " + str(found_bug) + "   (must be False)")

    if control not in seen:
        print()
        print("INCONCLUSIVE: could not read the code, so nothing is proven.")
        return 3

    print()
    if found_bug:
        print("FAIL: the shipped exe STILL contains the buggy '_parsed' key.")
        print("      The rebuild did not pick up the fix.")
        return 1
    print("PASS: the shipped exe does NOT contain '_parsed' -- the fix is in.")

    # ---- v3 / 周末修复是否也在里面 ----
    #
    # 光看「重新打包成功了」不算数：得证明产物里装的是**新版代码**。
    # WEEK_DAYS 是这次修「周六周日不显示」时抽出来的常量，
    # 它在 api 模块的名字表里出现，就说明打包用的是修复后的源码。
    print()
    print("--- v3 / weekend fix ---")
    has_week_days = "WEEK_DAYS" in seen
    print("  symbol 'WEEK_DAYS' present : " + str(has_week_days))
    print("  module app.day_type_policy : " + str("app.day_type_policy" in names))

    if not has_week_days:
        print()
        print("FAIL: the exe has no WEEK_DAYS symbol -- it was built before the")
        print("      'weekend courses are invisible' fix.")
        return 1
    if "app.day_type_policy" not in names:
        print()
        print("FAIL: the exe has no app.day_type_policy module -- it was built")
        print("      before the v3 (dayTypes) alignment.")
        return 1

    print()
    print("PASS: the exe is the v3 build with the weekend fix.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
