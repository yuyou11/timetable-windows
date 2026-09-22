"""
前端文件的加载清单 —— 拆分 `app.js` 之后，漏写一个 `<script>` 就是
**那一整块不工作，而且没有任何报错**。

## 为什么现在才需要这条测试

原先前端只有 `app.js` 一个文件，不存在「忘加标签」这回事。按页面拆成
`core.js` + 六个 `page-*.js` + `app.js` 之后，新增或改名一个 js 却忘了动
`index.html`，症状是那个页面整个不渲染，控制台只有一句
`renderToday is not defined` —— 正是 README 坑 5 那类静默失效。

## 加载顺序的两条硬规则

  · `core.js` 必须**最先** —— `app.js` 顶层就调了 `jslog(...)`
  · `app.js` 必须**最后** —— 它的 `boot()` 可能在自己那一行就立刻执行
    （`window.pywebview` 已就绪的那条路径），那时六个页面的渲染函数
    必须已经定义好。

中间六个 `page-*.js` 彼此独立，顺序随意（所以下面只断言相对位置，不锁死）。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "app" / "web"
INDEX = WEB / "index.html"


class TestIndexScriptTags(unittest.TestCase):
    def setUp(self):
        self.html = INDEX.read_text(encoding="utf-8")
        # 只看外链脚本；内联脚本不算（目前也没有）
        self.loaded = re.findall(r'<script\s+src="([^"]+)"', self.html)

    def test_every_referenced_script_exists(self):
        """index.html 里写了、但磁盘上没有 —— 页面会白搭一块，不报错。"""
        missing = [s for s in self.loaded if not (WEB / s).exists()]
        self.assertEqual([], missing, f"index.html 引用了不存在的脚本：{missing}")

    def test_no_orphan_scripts(self):
        """
        app/web 顶层的 js 都要被某处引用，不能有「写了但没人加载」的孤儿。

        `ball.js` 是例外 —— 它由 `ball.html` 加载（悬浮窗是另一个窗口、
        另一份页面），所以这里按 `ball.html` 的引用算。
        """
        ball_html = (WEB / "ball.html").read_text(encoding="utf-8")
        referenced = set(self.loaded) | set(re.findall(r'<script\s+src="([^"]+)"', ball_html))
        orphans = sorted(p.name for p in WEB.glob("*.js") if p.name not in referenced)
        self.assertEqual([], orphans, f"这些 js 没有任何页面加载它们：{orphans}")

    def test_core_loads_first_and_app_loads_last(self):
        """理由见模块文档 —— 顺序反了是运行期 ReferenceError，不是语法错。"""
        self.assertTrue(self.loaded, "index.html 里一个 <script> 都没有")
        self.assertEqual("core.js", self.loaded[0],
                         f"core.js 必须最先加载，现在第一个是 {self.loaded[0]}")
        self.assertEqual("app.js", self.loaded[-1],
                         f"app.js 必须最后加载（boot() 可能立刻执行），"
                         f"现在最后一个是 {self.loaded[-1]}")

    def test_all_page_files_are_wired(self):
        """每个 page-*.js 都要出现在加载清单里。"""
        pages = sorted(p.name for p in WEB.glob("page-*.js"))
        self.assertTrue(pages, "一个 page-*.js 都没找到 —— 目录结构是不是变了？")
        missing = [p for p in pages if p not in self.loaded]
        self.assertEqual([], missing, f"这些页面脚本没被 index.html 加载：{missing}")


class TestCallSitesUseSingleQuotes(unittest.TestCase):
    def test_call_sites_are_still_scannable(self):
        """
        `call('方法名')` 必须保持**单引号字符串字面量**。

        `tests/test_api_contract.py` 的正则是 `call\\(\\s*'([A-Za-z_]\\w*)'`，
        只认单引号 —— 改成双引号或变量就**静默脱离扫描**，那条契约测试会
        变成假绿。这条测试盯的是「扫描还有效」这件事本身。
        """
        found = 0
        for p in WEB.glob("*.js"):
            text = p.read_text(encoding="utf-8")
            found += len(re.findall(r"call\(\s*'([A-Za-z_]\w*)'", text))
            # 双引号的写法一旦出现，就是有人改坏了
            bad = re.findall(r'call\(\s*"([A-Za-z_]\w*)"', text)
            # ⚠️ 失败消息里别写 `bad[0]` —— assert 的消息 f-string 是**立刻求值**的，
            # 断言通过（bad 为空）时也会先算它，直接 IndexError 把测试搞崩。
            # 我第一版就栽在这儿：一条专门抓「静默失效」的测试，自己先静默崩了。
            self.assertFalse(bad,
                             f"{p.name} 里有双引号写法的 call(\"...\")：{bad}。"
                             f"test_api_contract 的正则只认单引号，扫不到它们。")
        self.assertGreater(found, 20, f"只扫到 {found} 处 call('...')，"
                                      f"正则或文件位置可能不对")


if __name__ == "__main__":
    unittest.main()
