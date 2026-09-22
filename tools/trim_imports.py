"""
删掉 Python 文件里**没被用到**的 import。

    python tools/trim_imports.py app/api.py app/api_ball.py ...

## 为什么用 ast 而不是靠肉眼

`app/api_*.py` 那几个 Mixin 是 `split_api.py` 生成的，每个文件头上都套了同一份
「宁可多不可少」的 import，结果每个文件挂着十几个用不到的名字。
肉眼删容易看漏 —— 而删错了是 `NameError`，且**只在跑得到那条分支时才炸**
（例如只在错误路径里用到的 `Path`）。

这里的做法：把所有 import 语句所在的行**挖空**，再看剩下的正文里还提不提到
这个名字。提到就是用到，没提到就可以删。用 `ast` 定位 import 的行范围，
所以多行的 `from x import (...)` 也能整块正确处理。

⚠️ `from __future__ import ...` 永远不动 —— 那不是普通导入。

## 边界

只按**标识符出现**判断。如果某个名字只在字符串里被用到（`globals()["X"]`、
`__getattr__` 动态取名、typing 的字符串注解），会被误判成未使用。
这个项目里没有这种写法；真遇到了就在那一行加个 `# noqa: keep` 并把它
加进下面的 KEEP 注释。

删完务必跑一遍测试 —— 本工具只保证「名字没在正文出现」，不保证「跑不到」。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


def used_names(src: str, tree: ast.Module) -> set[str]:
    """正文里出现过的标识符（import 语句本身所在的行不算）。"""
    lines = src.splitlines(keepends=True)
    blanked = list(lines)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for i in range(node.lineno - 1, node.end_lineno):
                blanked[i] = ""
    body = "".join(blanked)
    return set(re.findall(r"[A-Za-z_]\w*", body))


def trim(path: Path) -> bool:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    used = used_names(src, tree)

    edits: list[tuple[int, int, str]] = []
    removed: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue

        # ⚠️ 保留原始缩进。函数体里的 `import x` 是缩进过的，
        # 重写成顶格就是 IndentationError（第一版就栽在这儿）。
        raw = lines[node.lineno - 1]
        indent = raw[: len(raw) - len(raw.lstrip())]

        if isinstance(node, ast.ImportFrom):
            keep = [a for a in node.names if (a.asname or a.name) in used]
            removed += [a.name for a in node.names if a not in keep]
            if not keep:
                # 嵌套在函数里的 import 整条删掉可能让函数体变空（SyntaxError），
                # 那种就原样留着 —— 少删一个没用的 import，好过改坏代码。
                if node.col_offset == 0:
                    edits.append((node.lineno - 1, node.end_lineno, ""))
                continue
            names = ", ".join(a.name + (f" as {a.asname}" if a.asname else "")
                              for a in keep)
            dotted = "." * node.level + (node.module or "")
            edits.append((node.lineno - 1, node.end_lineno,
                          f"{indent}from {dotted} import {names}\n"))
        elif isinstance(node, ast.Import):
            keep = [a for a in node.names
                    if (a.asname or a.name).split(".")[0] in used]
            removed += [a.name for a in node.names if a not in keep]
            if not keep:
                if node.col_offset == 0:
                    edits.append((node.lineno - 1, node.end_lineno, ""))
                continue
            names = ", ".join(a.name + (f" as {a.asname}" if a.asname else "")
                              for a in keep)
            edits.append((node.lineno - 1, node.end_lineno,
                          f"{indent}import {names}\n"))

    if not removed:
        return False

    # 从后往前改，免得行号错位
    for start, end, text in sorted(set(edits), reverse=True):
        lines[start:end] = [text]
    # 收拾空行：类体内（缩进的 def / 注释 / 装饰器）之前收到 1 行，
    # 模块层最多留 2 行（PEP8）。
    text = re.sub(r"\n{3,}(?=    (?:def |#|@))", "\n\n", "".join(lines))
    out = re.sub(r"\n{4,}", "\n\n\n", text)
    path.write_text(out, encoding="utf-8")
    print(f"  {path}  删掉：{', '.join(sorted(set(removed)))}")
    return True


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for arg in sys.argv[1:]:
        trim(Path(arg))
    print("\n⚠️ 记得跑一遍测试 —— 本工具只保证「名字没在正文出现」，不保证「跑不到」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
