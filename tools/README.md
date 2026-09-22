# tools/ 索引

这里 37 个文件平铺着，不好找。**特意不分子目录** —— 因为 `tools/xxx.py` 这种路径
被 README、7 个测试文件的断言文案、6 个 app 模块的注释共 20 处引用着，
搬动一次要同步改 20 处文档，漏一处就变成死链。**能用索引解决的问题，就不要动文件。**

---

## 一、生成器（改了会牵动生成物，看清楚再动）

| 文件 | 干什么 | 被谁引用 |
|---|---|---|
| `gen_builtin.py` | 从手机版真实导出的 `example-full.json` 生成 `app/builtin_data.py` | `app/builtin_data.py:2` |
| `gen_icon.py` | 从 `icon-source.png` 生成 `app/assets/icon.png` + `icon.ico` | `app/tray.py:18`、`build.py:49` |
| `icon-source.png` | 图标素材。换图标换这张，别手改 `app/assets/` | `app/builtin_data.py`、README |

> 生成物（`app/builtin_data.py`、`app/assets/`）**不能手改**，改了就跟手机版/素材对不上。

## 二、守卫脚本 `verify_*.py`（15 个，动代码后跑）

对着**变异**验证测试有效 —— 故意把某行改坏，看测试是不是真的会红。
和 `tests/` 是互补不是重复：`tests/` 断言"现在是对的"，这些验证"坏了能被发现"。

| 文件 | 守什么 |
|---|---|
| `verify_frozen_api.py` | Api 上不能混进非方法的公开属性（走字节码认模块，见 `_module_names`） |
| `verify_serializable_guard.py` | 前端调用的返回值必须能 `json.dumps` |
| `verify_import.py` / `verify_verify_import.py` | 导入导出链路；后者验证前者本身没瞎 |
| `verify_format_parity.py` | 和手机版的格式标准逐行对齐（`app/format_spec.py:787` 引用） |
| `verify_ui_wiring.py` | 前端 `call('xxx')` 和 `Api` 方法对得上 |
| `verify_ball_guard.py` | 悬浮窗按钮自我禁用后的恢复路径 |
| `verify_close_button.py` / `verify_close_e2e.py` | 「关闭」按钮真的关掉、且设置开关同步 |
| `verify_external_close_guard.py` | 外部关闭被拦截降级为隐藏（`tests/test_ball_ui.py:196` 引用其 M3 变异） |
| `verify_hover_guard.py` / `verify_hover_e2e.py` | 贴边收起后悬停滑出 |
| `verify_anim_guards.py` | 动画的令牌 / 精确收口 / 中断续走（`tests/test_ball_ui.py:734` 引用） |
| `verify_grid_guard.py` | 周视图 5×7 网格形状 |
| `verify_daytype_guard.py` | 日型启用策略 |

## 三、排查实验 `exp_*.py`（7 个，一次性，但结论已进文档）

跑完就没人再跑，不过**结论都写进 README 和代码注释了**，留着是当"怎么测出来的"的存档。

| 文件 | 结论去哪了 |
|---|---|
| `exp_corner.py` | README「⭐ 一个藏得很深的坑：`disable_shadow()` 会把圆角一起关掉」整节 |
| `exp_anim_fps.py` | `app/main.py:76` 的 160ms 动画时长就是它量出来的 |
| `exp_dock.py` / `exp_windows.py` | 依赖 `app/main.py:50-57` 的再导出（含 `_bg_color`），**删掉那行它们会 ImportError** |
| `exp_shell.py` / `exp_swp.py` / `exp_resize.py` | 窗口样式 / 双缓冲 / 尺寸下限 |

## 四、探针 `probe_*.py`（6 个，一次性）

外部观察工具 —— 从进程外面看窗口的真实位置、尺寸、可见性。
`app/main.py:485` 提到的 `exp_toolwin.py` **已经不在了**（引用是历史遗留）。

`probe_ball_anim.py`、`probe_ball_hover.py`、`probe_ball_hover_src.py`、
`probe_dock_anim.py`、`probe_import_ui.py`、`probe_suppress_off.py`

> 它们用 `PIL.ImageGrab` 截图比对，所以**装 Pillow 才能跑**（已在 `requirements.txt`）。

## 五、其他

| 文件 | 干什么 |
|---|---|
| `diag_web.py` | 诊断 webview 起不起得来 |
| `inspect_windows.py` | `EnumWindows` 列出所有相关窗口的真实几何 |
| `check_corners.py` | 读四角像素判断圆角有没有生效 |
| `convert_schedule.py` | 课表数据格式转换 |
| `diff_schedule.py` | 两份课表逐行 diff |
| `sample-jiaowu.json` | 教务系统导出的**通用样例**（示例大学，不含真实课表） |
