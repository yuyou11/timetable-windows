"""
浏览器里预览 / 调试网页前端 —— 不起 pywebview，直接用真实 Api。

## 为什么需要这个工具

前端靠 `pywebview.api.<方法>()` 调后端，这个桥只在桌面壳里存在，
浏览器直接打开 web/ 是一片空白。改 UI（比如课表网格）时想看真实数据
渲染出来的样子，就得有一条替代的桥。

## 用法

    python tools/preview_web.py                 # 临时数据目录 + 示例学校数据
    python tools/preview_web.py --data-dir DIR  # 指定数据目录（想看自己的真实数据）
    python tools/preview_web.py --no-seed       # 不写示例数据（空库启动）
    python tools/preview_web.py --port 9000     # 固定端口（默认 8943，被占则随机）

启动后打印一个 http://127.0.0.1:端口/ 地址，用浏览器打开即可，Ctrl+C 停止。
只读浏览不写数据；但在页面上做的修改会真实写进数据目录 —— 想试自己的
数据请先备份，或者就用默认的临时目录。

## 原理

标准库 http.server 起一个本地服务：

    GET  /...            静态文件（app/web/），index.html 注入一行 shim 引用
    GET  /__preview.js   shim：把 window.pywebview.api 指到 HTTP 桥
    POST /api/<方法名>    反射调用真实 Api 的同名方法（JSON 数组 = 参数列表）

Api 方法返回值保证可 JSON 序列化（tests/test_api_serializable.py），
桥上不需要任何特殊处理。前端代码零改动 —— app.js 加载时检测到
window.pywebview 就直接 boot()。

ASCII only on purpose (PowerShell 5.1 reads non-BOM files as GBK).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import tempfile
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "app" / "web"

#: 注进 index.html 的 shim：把 pywebview 桥换成 fetch。必须是同步定义，
#: 这样 app.js 加载时就能看到 window.pywebview，boot() 立即启动。
SHIM_JS = r"""
window.pywebview = {
  api: new Proxy({}, {
    get(_t, prop) {
      if (typeof prop !== 'string') return undefined;
      return (...args) => fetch('/api/' + encodeURIComponent(prop), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(args),
      }).then((r) => r.json());
    },
  }),
};
""".strip()


def seed(store) -> None:
    """种一份示例学校数据：553 节次 + 覆盖各种合并形态的课表。

    - 单个空档（周一第 5 节）、空 2 节、空 3 节、空 6 节
    - 整列只有一两门课（周六补课）、整天没课（周日）
    - 连堂课（同名相邻格合并）、单节小课
    """
    from app.models import Course

    monday = date.today() - timedelta(days=date.today().weekday())
    store.periods = "553"
    store.term_name = "预览 · 示例学校"
    store.term_start = monday
    store.total_weeks = 19
    # 作息模板不种 —— 周网格只认 periods + courses；想看带自定义模板的
    # 整页效果，用 --data-dir 指向一份导入过作息的数据目录。

    w = frozenset(range(1, 20))          # 1-19 周
    half_a = frozenset(range(1, 20, 2))  # 单周
    courses = [
        Course(name="语文", day_of_week=1, start_node=1, end_node=2, weeks=w, place="教一-101", id="p01"),
        Course(name="数学", day_of_week=1, start_node=3, end_node=4, weeks=w, place="教一-203", id="p02"),
        Course(name="英语", day_of_week=1, start_node=6, end_node=7, weeks=w, place="教一-108", id="p03"),
        Course(name="体育", day_of_week=1, start_node=9, end_node=10, weeks=w, place="操场", id="p04"),

        Course(name="语文", day_of_week=2, start_node=1, end_node=2, weeks=w, place="教一-101", id="p05"),
        Course(name="数学", day_of_week=2, start_node=3, end_node=4, weeks=w, place="教一-203", id="p06"),
        Course(name="化学", day_of_week=2, start_node=5, end_node=6, weeks=w, place="实验楼-301", id="p07"),
        Course(name="物理", day_of_week=2, start_node=7, end_node=8, weeks=w, place="教二-108", id="p08"),
        Course(name="晚自习", day_of_week=2, start_node=11, end_node=13, weeks=w, id="p09"),

        Course(name="数学", day_of_week=3, start_node=1, end_node=2, weeks=w, place="教一-203", id="p10"),
        Course(name="音乐", day_of_week=3, start_node=9, end_node=9, weeks=w, place="艺术楼-201", id="p11"),
        Course(name="班会", day_of_week=3, start_node=13, end_node=13, weeks=w, id="p12"),

        Course(name="英语", day_of_week=4, start_node=1, end_node=2, weeks=w, place="教一-108", id="p13"),
        Course(name="历史", day_of_week=4, start_node=3, end_node=4, weeks=w, place="教二-105", id="p14"),
        Course(name="地理", day_of_week=4, start_node=6, end_node=6, weeks=w, place="教二-220", id="p15"),
        Course(name="信息技术", day_of_week=4, start_node=8, end_node=9, weeks=w, place="机房-3", id="p16"),

        Course(name="物理", day_of_week=5, start_node=2, end_node=3, weeks=w, place="教二-108", id="p17"),
        Course(name="化学", day_of_week=5, start_node=5, end_node=6, weeks=w, place="实验楼-301", id="p18"),
        Course(name="美术", day_of_week=5, start_node=10, end_node=10, weeks=w, place="艺术楼-101", id="p19"),

        Course(name="生物", day_of_week=6, start_node=3, end_node=4, weeks=half_a, place="实验楼-305", id="p20"),
    ]
    store.save_courses(courses)
    store.mark_launched()


class PreviewHandler(BaseHTTPRequestHandler):
    #: 由 main() 注入的真实 Api 实例
    api = None

    # ---------------- GET：静态文件 + shim ----------------

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/__preview.js":
            self._send(200, SHIM_JS, "application/javascript; charset=utf-8")
            return
        if path in ("", "/"):
            path = "/index.html"

        target = (WEB_DIR / path.lstrip("/")).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            self._send(403, "forbidden", "text/plain; charset=utf-8")
            return
        if not target.is_file():
            self._send(404, "not found: " + path, "text/plain; charset=utf-8")
            return

        body = target.read_bytes()
        if target.suffix == ".html":
            text = body.decode("utf-8").replace(
                "<head>", '<head><script src="/__preview.js"></script>', 1)
            body = text.encode("utf-8")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send_raw(200, body, ctype)

    # ---------------- POST：JSON-RPC 到真实 Api ----------------

    def do_POST(self):
        path = self.path.split("?")[0]
        if not path.startswith("/api/"):
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        name = path[len("/api/"):]

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "[]"
        try:
            if name.startswith("_"):
                raise AttributeError(f"私有方法不暴露: {name}")
            args = json.loads(raw or "[]")
            result = getattr(self.api, name)(*args)
            payload = json.dumps(result, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001 —— 调试桥：出错也回 JSON，页面别白屏
            payload = json.dumps(
                {"__preview_error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
        self._send(200, payload, "application/json; charset=utf-8")

    # ---------------- 杂项 ----------------

    def _send(self, code: int, text: str, ctype: str) -> None:
        self._send_raw(code, text.encode("utf-8"), ctype)

    def _send_raw(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")   # 调试时改了 JS 刷新就生效
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
        pass  # 每个请求都打一行太吵，调试桥安静点


def main() -> int:
    ap = argparse.ArgumentParser(description="浏览器预览网页前端（不起 pywebview）")
    ap.add_argument("--data-dir", default=None, help="数据目录（默认临时目录 + 示例数据）")
    ap.add_argument("--port", type=int, default=8943)
    ap.add_argument("--no-seed", action="store_true", help="不写示例数据")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))    # 从 tools/ 直接运行时让 `import app` 生效
    from app.api import Api
    from app.store import Store

    data_dir = (Path(args.data_dir).resolve() if args.data_dir
                else Path(tempfile.mkdtemp(prefix="tt_preview_")))
    data_dir.mkdir(parents=True, exist_ok=True)
    fresh = not (data_dir / "data.json").exists()

    store = Store(path=data_dir / "data.json")
    if fresh and not args.no_seed:
        seed(store)
        print("seeded sample data (553 + 示例课表)")

    PreviewHandler.api = Api(store)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), PreviewHandler)

    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"preview: {url}")
    print(f"data dir: {data_dir}")
    print("Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
