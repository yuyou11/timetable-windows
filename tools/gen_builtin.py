"""
生成 app/builtin_data.py。

**为什么用脚本生成而不是手抄**：内置的六套作息模板有 100 多格，
手抄一遍必然出错，而且以后手机版改了模板，电脑版就对不上了。
直接从手机版导出的 example-full.json 转换，两边永远一致。

用法（在工程根目录）：

    python tools/gen_builtin.py                    # 用手机版 docs 里的示例文件
    python tools/gen_builtin.py 我的课表.json       # 用自己导出的文件

## 为什么支持传路径

两个原因：

1. **公开仓库里不该硬编码本机路径。** 默认值那份路径是开发者本机的布局，
   别人 clone 下去跑必然会失败 —— 但至少报错要能看懂，
   所以找不到文件时会提示「把你的导出文件路径传进来」。
2. **可以让用户生成属于自己的内置数据。** 想让「恢复内置课表」
   恢复成自己课表的人，从手机版导出后跑一次即可。

## ⚠️ 内置数据是「首次启动时显示的课表」，会被提交进仓库

所以**不要把真实个人课表当作来源**（除非是私有仓库）——
里面有学校、年级专业、教室号。手机版的 docs/ 示例就是为此专门
换成了通用样例数据（示例大学 / 大学英语 / 教一-101）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 默认来源：手机版的示例文件（**通用样例数据**，不是任何人的真实课表）
#:
#: 手机版和电脑版是**同级目录**（projects/06-android-timetable、
#: projects/07-timetable-desktop），所以从这里往上找一层就行 ——
#: 不写死绝对路径，换台机器也能跑。
#: 想从别处取，就直接把路径当参数传进来。
DEFAULT_SRC = (
    ROOT.parent / "06-android-timetable" / "docs" / "example-full.json"
)
OUT = ROOT / "app" / "builtin_data.py"


def main() -> int:
    if len(sys.argv) > 1:
        src = Path(sys.argv[1])
    else:
        src = DEFAULT_SRC
        if not src.exists():
            print(f"找不到默认源文件：{src}", file=sys.stderr)
            print("", file=sys.stderr)
            print("请把你的导出文件路径传进来，例如：", file=sys.stderr)
            print("    python tools/gen_builtin.py 我的课表.json", file=sys.stderr)
            return 1

    if not src.exists():
        print(f"找不到源文件：{src}", file=sys.stderr)
        return 1

    data = json.loads(src.read_text(encoding="utf-8"))

    term = data["term"]
    courses = data["courses"]
    templates = data.get("templates", {})

    lines: list[str] = []
    lines.append('"""')
    lines.append("内置数据 —— 由 tools/gen_builtin.py 自动生成，**不要手工编辑**。")
    lines.append("")
    lines.append("来源：手机版导出的 example-full.json（程序真实输出，不是手抄）。")
    lines.append("要改内容请改手机版的内置数据后重新导出，再跑一次生成脚本。")
    lines.append('"""')
    lines.append("")
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("import json")
    lines.append("from datetime import date")
    lines.append("")
    lines.append("from .models import Block, Course, DayType, Kind")
    lines.append("")
    lines.append(f'TERM_NAME = {term["name"]!r}')
    lines.append(f'TERM_START = date.fromisoformat({term["startDate"]!r})')
    lines.append(f'TOTAL_WEEKS = {term["totalWeeks"]}')
    lines.append("")
    lines.append("#: 内置课表。每项是一个 dict，字段与 JSON 格式一致")
    lines.append("COURSES_RAW = [")
    for c in courses:
        lines.append(f"    {c!r},")
    lines.append("]")
    lines.append("")
    lines.append("#: 六套作息模板的原始 JSON（保持原样嵌入，避免手工转写出错）")
    lines.append("TEMPLATES_JSON = r'''")
    lines.append(json.dumps(templates, ensure_ascii=False, indent=1))
    lines.append("'''")
    lines.append("")
    lines.append("")
    lines.append("def _weeks_from_text(text: str) -> frozenset[int]:")
    lines.append("    \"\"\"把 '2-4,6-17' 这类写法展开成集合（复用正式解析器，避免两套逻辑）\"\"\"")
    lines.append("    from .format_spec import parse_weeks")
    lines.append("    return parse_weeks(text, TOTAL_WEEKS)")
    lines.append("")
    lines.append("")
    lines.append("def courses() -> list[Course]:")
    lines.append("    \"\"\"构造内置课表。每次调用返回新的对象，避免被就地修改污染\"\"\"")
    lines.append("    out: list[Course] = []")
    lines.append("    for i, raw in enumerate(COURSES_RAW):")
    lines.append("        start_node, end_node = raw['nodes']")
    lines.append("        out.append(Course(")
    lines.append("            name=raw['name'],")
    lines.append("            day_of_week=raw['dayOfWeek'],")
    lines.append("            start_node=start_node,")
    lines.append("            end_node=end_node,")
    lines.append("            weeks=_weeks_from_text(raw['weeks']),")
    lines.append("            place=raw.get('place', ''),")
    lines.append("            enabled=raw.get('enabled', True),")
    lines.append("            id=f'c{i + 1:03d}',")
    lines.append("        ))")
    lines.append("    return out")
    lines.append("")
    lines.append("")
    lines.append("def templates() -> dict[DayType, list[Block]]:")
    lines.append("    \"\"\"构造内置模板。延迟到调用时才解析，避免 import 时就依赖 format_spec\"\"\"")
    lines.append("    from .format_spec import _parse_templates")
    lines.append("    raw = json.loads(TEMPLATES_JSON)")
    lines.append("    merged = _parse_templates(raw, [])")
    lines.append("    merged[DayType.REST] = _REST_DAY_TEMPLATE")
    lines.append("    return merged")
    lines.append("")
    # 「无课休息日」模板是电脑版引擎特有的（REST 日型，手机版还没有），
    # 不在六套模板的 JSON 里 —— 必须由生成器一并产出，否则重新生成
    # builtin_data.py 时 REST 就没有模板可用，无课日会直接 KeyError。
    lines.append("")
    lines.append("# 「无课休息日」模板 —— **电脑版特有**，不是从手机版导出的。")
    lines.append("#")
    lines.append("# 引擎把「全天没课的工作日」判为 REST（假期、停课、课表没排到的日子），")
    lines.append("# 那天按这一套过：没有课程格子、没有晚自修，全是自由块。")
    lines.append("# REST 不在 DAY_TYPE_ORDER 里（不能出现在 JSON 文件中），所以这套模板")
    lines.append("# 只能在代码里构造，走不了 TEMPLATES_JSON。")
    lines.append("_REST_DAY_TEMPLATE = [")
    lines.append("    Block(0, 9 * 60, '睡觉', kind=Kind.SLEEP),")
    lines.append("    Block(9 * 60, 9 * 60 + 30, '起床、洗漱'),")
    lines.append("    Block(9 * 60 + 30, 10 * 60 + 15, '早餐', kind=Kind.MEAL),")
    lines.append("    Block(10 * 60 + 15, 12 * 60, '★ 自由块', note='没课的日子，做点自己想做的事', kind=Kind.FREE),")
    lines.append("    Block(12 * 60, 12 * 60 + 40, '午餐', kind=Kind.MEAL),")
    lines.append("    Block(12 * 60 + 40, 13 * 60 + 40, '午休', note='闭眼躺一会儿，不用睡着', kind=Kind.SLEEP),")
    lines.append("    Block(13 * 60 + 40, 17 * 60 + 30, '★ 自由块', note='整块时间，别切成碎片', kind=Kind.FREE),")
    lines.append("    Block(17 * 60 + 30, 18 * 60 + 30, '晚餐 + 散步', kind=Kind.MEAL),")
    lines.append("    Block(18 * 60 + 30, 22 * 60 + 30, '★ 自由块', note='晚上没有晚自修，放松、收尾都行', kind=Kind.FREE),")
    lines.append("    Block(22 * 60 + 30, 23 * 60 + 30, '洗漱、聊天、睡前刷手机'),")
    lines.append("    Block(23 * 60 + 30, 24 * 60, '睡觉', note='没有闹钟的一天也别熬太晚', kind=Kind.SLEEP),")
    lines.append("]")
    lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成 {OUT}")
    print(f"  课程 {len(courses)} 门，模板 {len(templates)} 种")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
