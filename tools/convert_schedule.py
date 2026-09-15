"""
把教务系统导出的课表转成《时间规划表》的 v3 JSON。

    python tools/convert_schedule.py                      # 用自带的样例表格
    python tools/convert_schedule.py 我的表格.json         # 用你自己的表格
    python tools/convert_schedule.py 我的表格.json -o 课表.json

## 这个脚本解决什么问题

教务系统导出的是一张"一行一条上课记录"的表：

    课程名 | 上课周次 | 上课星期 | 开始节次 | 结束节次 | 教室名称

而本程序的格式要的是"一条记录 = 一周里的一个固定时段"：

    { "name": ..., "dayOfWeek": 1, "nodes": [1, 2], "weeks": "2-4,6-17", "place": ... }

两者最麻烦的差异是**周次的写法**，见下面 convert_weeks()。

## 输入表格的格式

一个 JSON 文件，形如：

    {
      "term": { "name": "示例大学 2026 级 · 大一上",
                "startDate": "2026-09-07", "totalWeeks": 19 },
      "rows": [
        { "name": "大学英语", "weeks": "2-4周,6-17周", "weekday": "星期一",
          "start": 1, "end": 2, "place": "教一-101" },
        ...
      ]
    }

`weekday` 接受「周一」「星期一」「礼拜一」这类写法；
`weeks` 保留教务的原样写法（`2周` / `2-4周` / `3-17周(单)`），
由下面的 convert_weeks() 负责转换。

`tools/sample-jiaowu.json` 是一份**通用样例**（示例大学），
照着它的形状做一份自己的就能用。

## 为什么表格放在外部文件，而不是内嵌在脚本里

一开始是内嵌的，但那样有两个问题：

  1. **真实课表会跟着脚本进版本库** —— 里面有学校、年级专业、教室号。
     这个仓库是公开的，不合适。
  2. 想换一份课表就得改代码，而不是换一个文件。

## 为什么脚本自己做周次转换，而不是手工转好再写死

手工转换二十多条记录，转错了看不出来 —— 而错了的后果是"某几周的课不出现"，
要等到那一周才会发现。让脚本按规则转，规则本身可以核对。
转换完还会用 format_spec.parse() 走一遍**程序自己的校验器**，
确保生成的确实是程序认的文件，而不是"看起来像"。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import format_spec  # noqa: E402

#: 默认输入：仓库自带的通用样例（**不是任何人的真实课表**）
SAMPLE = Path(__file__).resolve().parent / "sample-jiaowu.json"


def load_table(path: Path) -> tuple[str, date, int, list[tuple]]:
    """
    读入表格文件，返回 (学期名, 起始日, 总周数, 记录列表)。

    记录会被转成元组（课程名, 周次, 星期, 起, 止, 教室）——
    这是 convert_weeks() 和 build_courses() 期待的旧形状，
    保留它是为了让转换逻辑一行都不用改。
    """
    data = json.loads(path.read_text(encoding="utf-8"))

    term = data.get("term") or {}
    name = str(term.get("name", "")).strip() or "未命名学期"
    start_raw = str(term.get("startDate", "")).strip()
    if not start_raw:
        raise SystemExit(f"{path} 里缺少 term.startDate")
    start = date.fromisoformat(start_raw)
    weeks = int(term.get("totalWeeks", 19))

    rows = []
    for i, r in enumerate(data.get("rows", [])):
        try:
            rows.append((
                str(r["name"]),
                str(r["weeks"]),
                str(r["weekday"]),
                int(r["start"]),
                int(r["end"]),
                str(r.get("place", "")),
            ))
        except (KeyError, TypeError, ValueError) as e:
            raise SystemExit(f"{path} 第 {i + 1} 条记录不完整：{e}") from None

    if not rows:
        raise SystemExit(f"{path} 里没有任何记录（rows 是空的）")

    return name, start, weeks, rows


def convert_weeks(raw: str) -> str:
    """
    把教务的周次写法转成 v2 的写法。

        "2周"           -> "2"
        "2-4周"         -> "2-4"
        "2周,5-12周"    -> "2,5-12"
        "3-17周(单)"    -> "3-17/2"     单周 = 从起点每隔一周
        "2-16周(双)"    -> "2-16/2"     双周同理，起点 2 本身就是偶数

    ## (单)/(双) 都写成 /2，因为起点已经决定了奇偶

    这一点值得说明：教务写"3-17周(单)"，包含的是 3、5、7…17；
    而 v2 的 "3-17/2" 是"从 3 开始每隔 2 周"，恰好就是 3、5、7…17。
    所以不需要区分单双，**起点写对了就行**。

    如果哪天教务导出「星期几的单双周」这种更绕的写法，这里会露馅 ——
    所以下面 parse 之后会核对周次数量，对不上就报错（见 main）。
    """
    parts: list[str] = []
    for seg in raw.split(","):
        seg = seg.strip()
        if not seg:
            continue

        biweekly = False
        for mark in ("(单)", "(双)", "单周", "双周"):
            if mark in seg:
                seg = seg.replace(mark, "")
                biweekly = True
                break

        seg = seg.replace("周", "").strip()
        if not seg:
            raise ValueError(f"周次片段解析不出数字：{raw!r}")

        # 校验片段形状：要么是 "3"，要么是 "2-4"
        if not re.fullmatch(r"\d+(-\d+)?", seg):
            raise ValueError(f"周次片段形状不认识：{seg!r}（原始：{raw!r}）")

        parts.append(seg + "/2" if biweekly else seg)

    return ",".join(parts)


WEEKDAY = {
    "星期一": 1, "星期二": 2, "星期三": 3, "星期四": 4,
    "星期五": 5, "星期六": 6, "星期日": 7,
}


def build_courses(rows: list[tuple]) -> list[str]:
    """把表格记录转成 course 对象文本块（手写序列化，理由见 format_spec）"""
    blocks: list[str] = []
    for name, weeks_raw, weekday, start, end, place in rows:
        dow = WEEKDAY.get(weekday)
        if dow is None:
            raise ValueError(f"不认识的星期写法：{weekday!r}（{name}）")

        weeks = convert_weeks(weeks_raw)
        esc = format_spec._escape

        item = [
            f'      "name": "{esc(name)}",',
            f'      "dayOfWeek": {dow},',
            f'      "nodes": [{start}, {end}],',
            f'      "weeks": "{weeks}"',
        ]
        if place:
            item[-1] += ","
            item.append(f'      "place": "{esc(place)}"')

        blocks.append("    {\n" + "\n".join(item) + "\n    }")

    return blocks


def main() -> int:
    # 位置参数就是输入表格；不给就用自带的样例
    args = [a for a in sys.argv[1:]]
    out_path = None
    if "-o" in args:
        i = args.index("-o")
        out_path = Path(args[i + 1])
        del args[i:i + 2]
    src = Path(args[0]) if args else SAMPLE

    if not src.exists():
        raise SystemExit(f"找不到表格文件：{src}")

    term_name, term_start, total_weeks, rows = load_table(src)

    # 先自查一遍转换结果：每条记录的周次展开后，周数应该和原始写法对得上。
    # 这是防"转换规则写错了但看不出来"的唯一办法。
    for name, weeks_raw, _wd, _s, _e, _p in rows:
        converted = convert_weeks(weeks_raw)
        got = format_spec.parse_weeks(converted, total_weeks)
        if not got:
            raise SystemExit(f"周次转换后是空的：{name} {weeks_raw!r} -> {converted!r}")

    body = ",\n".join(build_courses(rows))
    text = (
        "{\n"
        f'  "format": "{format_spec.FORMAT_ID}",\n'
        f'  "version": {format_spec.VERSION},\n'
        '  "term": {\n'
        f'    "name": "{format_spec._escape(term_name)}",\n'
        f'    "startDate": "{term_start.isoformat()}",\n'
        f'    "totalWeeks": {total_weeks}\n'
        "  },\n"
        '  "courses": [\n'
        f"{body}\n"
        "  ]\n"
        "}\n"
    )

    # ---- 用程序自己的解析器校验 ----
    #
    # 这一步是重点：不是"我检查了一眼觉得没问题"，而是**让程序去读它**。
    # 生成的文本要是过不了这一关，导入时一样会失败。
    parsed = format_spec.parse(text, fallback_total_weeks=total_weeks)

    if parsed.term is None:
        raise SystemExit("生成的文本没有 term 段")
    if parsed.courses is None:
        raise SystemExit("生成的文本没有 courses 段")
    if len(parsed.courses) != len(rows):
        raise SystemExit(
            f"课程条数对不上：原始 {len(rows)} 条，解析出 {len(parsed.courses)} 条"
        )

    print(f"输入表格    {src.name}", file=sys.stderr)
    print(f"源记录      {len(rows)} 条", file=sys.stderr)
    print(f"解析通过    {len(parsed.courses)} 门", file=sys.stderr)
    print(f"学期        {parsed.term.name}", file=sys.stderr)
    print(f"起始日      {parsed.term.start_date}（周{parsed.term.start_date.isoweekday()}）",
          file=sys.stderr)
    print(f"总周数      {parsed.term.total_weeks}", file=sys.stderr)

    if parsed.warnings:
        print("\n⚠️ 冲突警告：", file=sys.stderr)
        for w in parsed.warnings:
            print("   " + w, file=sys.stderr)
    else:
        print("冲突检查    没有时段重叠", file=sys.stderr)

    # 每门课展开后的周次也报一下，方便和教务的表肉眼核对
    print("\n周次核对（展开后）：", file=sys.stderr)
    for c in parsed.courses:
        ws = sorted(c.weeks)
        shown = format_spec.WEEKDAY_CN[c.day_of_week]
        print(f"  {shown} {c.start_node}-{c.end_node}  {c.name[:14]:<16}"
              f"{format_spec.format_weeks(c.weeks):<16}{ws}", file=sys.stderr)

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"\n已写出：{out_path}", file=sys.stderr)
    else:
        sys.stdout.write(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
