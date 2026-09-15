"""
对比"现有课表"和"新生成的课表"，列出增删改。

    python tools/diff_schedule.py 新文件.json

为什么要专门做这个：用户说"计划有变"，但**变在哪几条**只有对比才看得出来。
直接拿新表覆盖旧表，用户就没机会发现"哦这条怎么变了" ——
而课表错一条的后果是某天该上的课不提醒。

比较用 (星期, 节次) 作为配对键。同一个时段只能有一门课，
所以按它配对是唯一合理的做法；再比课程名、周次、教室。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import format_spec  # noqa: E402

WD = format_spec.WEEKDAY_CN


def load_current() -> list[dict]:
    """读程序当前数据文件里的课表。只读，不改。"""
    import os
    base = os.environ.get("TIMETABLE_DATA_DIR")
    if base:
        path = Path(base) / "data.json"
    else:
        path = Path(os.environ["APPDATA"]) / "Timetable" / "data.json"

    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw.get("courses", [])


def key(c: dict) -> tuple:
    n = c["nodes"]
    return (c["dayOfWeek"], n[0], n[1])


def weeks_text(c: dict, total: int) -> str:
    w = c["weeks"]
    if isinstance(w, str):
        try:
            return format_spec.format_weeks(format_spec.parse_weeks(w, total))
        except format_spec.FormatError:
            return w
    return format_spec.format_weeks(frozenset(w))


def describe(c: dict, total: int) -> str:
    place = c.get("place", "") or "（无教室）"
    return f"{c['name']}｜第 {weeks_text(c, total)} 周｜{place}"


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python tools/diff_schedule.py 新文件.json", file=sys.stderr)
        return 2

    new_raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    total = (new_raw.get("term") or {}).get("totalWeeks", 19)
    new = new_raw.get("courses", [])
    old = load_current()

    old_map = {key(c): c for c in old}
    new_map = {key(c): c for c in new}

    added = [k for k in new_map if k not in old_map]
    removed = [k for k in old_map if k not in new_map]
    common = [k for k in new_map if k in old_map]

    def label(k) -> str:
        return f"{WD[k[0]]} 第 {k[1]}-{k[2]} 节"

    print(f"现有 {len(old)} 条  →  新表 {len(new)} 条")
    print(f"（按「星期 + 节次」配对，因为同一时段只能有一门课）")
    print()

    if added:
        print(f"■ 新增 {len(added)} 条")
        for k in sorted(added):
            print(f"    + {label(k)}　{describe(new_map[k], total)}")
        print()

    if removed:
        print(f"■ 删除 {len(removed)} 条")
        for k in sorted(removed):
            print(f"    - {label(k)}　{describe(old_map[k], total)}")
        print()

    changed = []
    for k in sorted(common):
        a, b = old_map[k], new_map[k]
        diffs = []
        if a["name"] != b["name"]:
            diffs.append(f"课程名：{a['name']} → {b['name']}")
        aw, bw = weeks_text(a, total), weeks_text(b, total)
        if aw != bw:
            diffs.append(f"周次：{aw} → {bw}")
        ap, bp = a.get("place", ""), b.get("place", "")
        if ap != bp:
            diffs.append(f"教室：{ap or '（无）'} → {bp or '（无）'}")
        if diffs:
            changed.append((k, diffs))

    if changed:
        print(f"■ 修改 {len(changed)} 条")
        for k, diffs in changed:
            print(f"    ~ {label(k)}　{new_map[k]['name']}")
            for d in diffs:
                print(f"        {d}")
        print()

    same = len(common) - len(changed)
    if not (added or removed or changed):
        print("完全相同，没有任何变化。")
    else:
        print(f"未变化 {same} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
