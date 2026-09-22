"""
一次性工具：把 `app/web/app.js` 按页面拆成多个文件。

和 `split_api.py` 同一个思路：**按行号原样切片**，不重新生成、不转写 ——
这个文件里全是中文注释和踩坑记录，手工复制抄错一个字就是静默失效。

## 硬约束（拆错了测试会红，或者更糟：静默不工作）

  1. `KIND_COLOR` / `KIND_LABEL` 必须留在 `app.js` —— `tests/test_ball_ui.py`
     按**文件名 "app.js"** 读 `const KIND_COLOR = { ... };`，还用正则要求
     它以 `const KIND_COLOR = {` 开头、`};` 结尾、含 8 行 `KEY: '#hex',`。
  2. `call('方法名')` 的**单引号字面量**不能变。`tests/test_api_contract.py` 的
     正则只认单引号，改了就静默脱离扫描（假绿）。
  3. 被 Python 侧 `evaluate_js` 调用的函数必须继续挂在 `window` 上 ——
     所以保持**顶层函数声明**（`function foo()` / `async function foo()`），
     别改成 `const foo = () => {}`（那不进 window，`main.py` 会静默跳过）。
  4. 文件必须平铺在 `app/web/` **顶层**：`test_api_contract` 用
     `WEB.glob("*.js")`，**不递归子目录**。
  5. `ball.js` / `ball.html` / `toast.html` / `style.css` 一律不动。

跑完必须：
    python -m unittest discover -s tests
    python tests/smoke.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "app" / "web" / "app.js"

#: (目标文件, 文件头说明, [(起始行, 结束行)])  —— 行号 1-based，闭区间。
#:
#: 说明写 None 表示「这一段自带文件头，不要另加」（app.js 就是）。
PLAN: list[tuple[str, str | None, list[tuple[int, int]]]] = [
    (
        "core.js",
        "共享的基础工具 —— 选择器、日志、调后端、弹窗、建 DOM 节点。\n"
        "\n"
        "纯工具，不碰业务；六个页面和 app.js 都会用到它们。\n"
        "从 app.js 拆出来（tools/split_app_js.py），内容一字未改。",
        [(18, 32), (45, 143), (171, 215), (660, 672)],
    ),
    (
        "page-today.js",
        "「今天」页 —— 此刻在做什么、还剩多久、明天预告、今日时间线。\n"
        "\n"
        "从 app.js 拆出来（tools/split_app_js.py），内容一字未改。",
        [(236, 305)],
    ),
    (
        "page-week.js",
        "「课表」页 —— 一周的课程网格，可以翻周。\n"
        "\n"
        "从 app.js 拆出来（tools/split_app_js.py），内容一字未改。",
        [(307, 362)],
    ),
    (
        "page-courses.js",
        "「课程编辑」页 —— 图形化增删改课程。\n"
        "\n"
        "从 app.js 拆出来（tools/split_app_js.py），内容一字未改。",
        [(364, 498)],
    ),
    (
        "page-templates.js",
        "「作息编辑」页 —— 改六套作息模板、一键改起床时间。\n"
        "\n"
        "从 app.js 拆出来（tools/split_app_js.py），内容一字未改。\n"
        "（`toMinutes` 搬去了 core.js，它是通用工具。）",
        [(500, 659), (673, 713)],
    ),
    (
        "page-data.js",
        "「导入导出」页 —— 和手机版交换数据、AI 提示词。\n"
        "\n"
        "从 app.js 拆出来（tools/split_app_js.py），内容一字未改。",
        [(715, 797)],
    ),
    (
        "page-settings.js",
        "「设置」页 —— 学期信息、课前提醒、悬浮窗开关、日型勾选。\n"
        "\n"
        "从 app.js 拆出来（tools/split_app_js.py），内容一字未改。",
        [(799, 904)],
    ),
    (
        # 留在 app.js 的：文件头、KIND_COLOR / KIND_LABEL（被测试按文件名钉住）、
        # STATE、页面切换、事件绑定、启动、pywebview 就绪双路径、报错转发。
        "app.js",
        None,
        [(1, 17), (34, 43), (145, 169), (217, 234), (906, 1140)],
    ),
]


def main() -> int:
    lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)

    # 先把所有切片取到内存里 —— 后面要覆盖 app.js，不能边读边写
    out: dict[str, str] = {}
    for name, doc, ranges in PLAN:
        body = "".join("".join(lines[a - 1: b]) for a, b in ranges)
        if doc is None:
            out[name] = body
        else:
            out[name] = f"/*\n{doc}\n   ========================================================================== */\n\n'use strict';\n\n{body}"

    # 覆盖率自检：漏行会在测试里表现为「某个函数没了」，太难查，这里先挡一道
    covered = sorted(i for _n, _d, rs in PLAN for a, b in rs for i in range(a, b + 1))
    holes = [i for i in range(1, len(lines) + 1) if i not in set(covered)]
    nonblank = [i for i in holes if lines[i - 1].strip()]
    if nonblank:
        raise SystemExit(f"!! 这些非空行没被任何一组覆盖：{nonblank[:20]}")

    for name, text in out.items():
        (ROOT / "app" / "web" / name).write_text(text, encoding="utf-8")
        print(f"  写出 app/web/{name:18} {len(text.splitlines()):4} 行")

    total = sum(len(t.splitlines()) for t in out.values())
    print(f"\n  合计 {total} 行（原 {len(lines)} 行；差额是各文件新加的文件头）")
    print("  ⚠️ 记得改 index.html 的 <script> 标签，然后跑全量测试。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
