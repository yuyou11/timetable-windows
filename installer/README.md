# 打包安装程序

把 `dist\时间规划表\` 那个文件夹版做成一个能发给同学的安装程序。

```bash
python build.py                    # 先出文件夹版（约 41 MB）
python installer\build_installer.py   # 再出安装程序（约 17 MB）
```

产物：`dist\installer\时间规划表-安装程序-2.0.exe`

## 这个目录里有什么

| 文件 | 是什么 |
|---|---|
| `timetable.iss` | **安装脚本**，Inno Setup 的配置。改安装行为就改这里 |
| `build_installer.py` | **驱动脚本**，先做几项检查再调编译器。用它，别直接双击 .iss |
| `ChineseSimplified.isl` | Inno Setup 的**简体中文语言文件**，见下面「语言文件」一节 |

## 为什么要有驱动脚本

和 `build.py` 一个道理：有几件必须做对的事，固化成检查比"记得"可靠。

1. **先确认打包产物在** —— 否则 Inno 会报一堆"找不到文件"，真正的原因
   （还没跑 `build.py`）反而看不出来。
2. **拒绝把个人课表打进安装包** —— 见下。
3. **找到 `ISCC.exe`** —— Inno 装在三个可能的位置之一，而且**不在 PATH 里**。
4. **给 `.iss` 补 UTF-8 BOM** —— 见下。

### ⚠️ 检查个人数据这一条最要紧

`.gitignore` 里特意写了 `课表*.json` 不进版本库，理由是
「里面是个人课表（学校、年级专业、课程名、教室号）」。

安装包**是要发给别人的**。一旦夹带了，等于把自己的课表随程序一起散出去，
而对方装完只看得到自己的界面，**根本不会发现包里还藏着一份别人的课表**。

所以 `build_installer.py` 会扫一遍产物，发现 `data.json` / `*课表*.json`
就拒绝打包。

### ⚠️ 为什么非要补 BOM

Inno Setup 6 读 `.iss` / `.isl` 时的规则是：**有 BOM 按 UTF-8 读，
没有 BOM 按系统代码页（中文 Windows 上是 GBK）读。**

我们的 `.iss` 是 UTF-8 无 BOM 的。不补 BOM 的话，里面的中文会被当成 GBK
解码，变成乱码显示在安装界面上 —— 而且**编译不会报错**，只有人眼看得见。
这就是这个项目里反复出现的那类问题：**不报错的错**。

`ChineseSimplified.isl` 从官方翻译仓库下载时就带 BOM，不用管。

## 语言文件从哪来的

Inno Setup 官方自带 20 多种语言，但**简体中文不在里面**（它属于
"非官方翻译"）。所以这个文件是单独下载的：

- 上游维护仓库：<https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation>
- 国内镜像（本次用的）：<https://gitee.com/kira96/Inno-Setup-Chinese-Simplified-Translation>

换机器重新拉一份：

```bash
curl -L -o installer/ChineseSimplified.isl \
  https://gitee.com/kira96/Inno-Setup-Chinese-Simplified-Translation/raw/main/ChineseSimplified.isl
```

> 注意这个翻译目前停在 Inno 6.1.0+ 那一版，编译时会有几十条
> `Will use the English message from Default.isl` 警告。**那些不用管** ——
> 缺的全是「下载文件 / 解压归档 / 签名校验」那一类消息，而本安装程序
> 一件事都不做（文件全在本地、不联网、没签名），那些字符串永远不会显示。
> 详细说明写在 `timetable.iss` 的 `[Messages]` 段落里。

## 装 Inno Setup

```bash
winget install JRSoftware.InnoSetup
```

装完 `ISCC.exe` 在这些位置之一（`build_installer.py` 会自己找）：

- `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` ← winget 装的是这个
- `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`
- `C:\Program Files\Inno Setup 6\ISCC.exe`

## 安装程序的行为

- **装给当前用户**，不需要管理员权限，全程不弹 UAC
- 默认装到 `%LOCALAPPDATA%\Programs\时间规划表`
- **每次安装都可以自己选安装位置**（`DisableDirPage=no`，点「浏览…」换盘/换目录）
  - 选到没权限的地方（`C:\Program Files` 之类）会**当场拦下并提示换位置**，
    而不是装到一半才失败。见 `timetable.iss` 里的 `DirIsWritable`
- 开始菜单快捷方式 + 可选的桌面图标
- 「设置 → 应用」里能卸载
- **卸载不会删课表数据**（数据在 `%APPDATA%\Timetable`，压根不在安装目录里）
- 装之前会检查 WebView2 运行时，缺了只**警告**不拦（误判时用户还有退路）

## 改版本号要改哪两处

1. `timetable.iss` 顶部的 `#define AppVersion`
   （下面的 `VersionInfoVersion` 是 exe 属性里显示的四段式版本，跟着一起改）
2. `app\__init__.py` 的 `__version__` —— 这是**唯一一处**写应用版本号的地方，
   `app\api.py` 的 `bootstrap()` 直接引它，不用另改

> ⚠️ `AppId`（那一串 GUID）**升级时绝对不要改**。
> 改了之后新版本会被当成另一个程序装一份新的，而不是覆盖升级 ——
> 用户机器上会同时存在两份。

## 验证

```bash
python -c "..."   # 见对话记录里的 verify_installer.py
```

走的是真实路径：静默安装 → 查文件/快捷方式/注册表 → 试运行 →
静默卸载 → 查残留。顺带盯住两件事：

- **注册表里的 DisplayName 是不是正确的中文** —— 这能验证 `.iss` 的编码
  真的对了（读成 GBK 的话这里就是乱码）
- **用户的真实课表数据全程没被动过**（前后比 SHA-256）

静默卸载**带超时**：卸载程序要是挂住了（比如弹了个没人点的对话框），
验证会当场发现，而不是跟着一起挂。
