; ============================================================================
;  时间规划表 —— Windows 安装程序（Inno Setup 6）
;
;  编译方式（别直接双击这个文件，用驱动脚本）：
;
;      python installer/build_installer.py
;
;  它会先检查打包产物在不在，再找到 ISCC.exe 编译本文件。
;  产物落在 dist\installer\ 下。
;
;  ---------------------------------------------------------------------------
;  三条贯穿始终的规矩
;
;  1. **只装给当前用户，不要管理员权限。**（PrivilegesRequired=lowest）
;     装到 %LOCALAPPDATA%\Programs，全程不弹 UAC。
;     发给同学时对方也不用输密码 —— 为一个小工具要管理员，很多人会直接放弃。
;
;  2. **绝对不碰用户的课表数据。**
;     数据在 %APPDATA%\Timetable\data.json，**不在安装目录里**，
;     所以安装和卸载都不会动它。卸载后重装，课表和作息设置自动回来。
;     这一点很重要：这份数据是用户手工录了一学期的东西，比程序本身值钱。
;
;  3. **覆盖安装前要把正在运行的程序关掉。**
;     exe 被占用时文件替换会失败（或者换到一半失败），
;     所以打开 CloseApplications，让安装程序自己提示用户关闭。
;     本程序没有 AppMutex，Inno 靠"文件被占用"来检测，对本项目够用。
;  ============================================================================

#define AppName "时间规划表"
#define AppVersion "1.0"
#define AppExeName "时间规划表.exe"

; AppId 是**这个程序的唯一身份**，升级时要保持不变。
; 换了它，新版本会被当成另一个程序装一份新的，而不是覆盖升级。
#define AppId "{{5500c962-86bd-4939-92b7-6326d859af91}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=余欣哲
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
; 不显示"选择程序组"那一页 —— 只有一项，问了也是白问
DisableProgramGroupPage=yes
; 只给当前用户装，不要管理员
PrivilegesRequired=lowest
; 产物落在 dist\installer\（相对本文件所在目录）
OutputDir=..\dist\installer
OutputBaseFilename={#AppName}-安装程序-{#AppVersion}
; 安装程序自己也用这个时钟图标 —— 不设的话它会是个通用安装包图标
SetupIconFile=..\app\assets\icon.ico
; 控制面板"应用和功能"里显示的图标
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; PyInstaller 打的是 64 位
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 程序正开着时提示用户关掉，而不是装作没这回事
CloseApplications=yes
RestartApplications=no
AllowNoIcons=yes
; exe 文件属性里显示的信息
VersionInfoVersion=1.0.0.0
VersionInfoDescription={#AppName} 安装程序
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

[Languages]
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"

[CustomMessages]
; 下面的 %1 是"数据目录"的路径（见 [Code] 里怎么传）
MyDataKeptLocation=你的课表和作息数据在这个文件夹里：%n%n%1%n%n卸载程序**不会**删除它 —— 以后重新安装，设置会自动回来。
MyWebView2Missing=程序需要「Microsoft Edge WebView2 运行时」才能显示界面，%n但系统里没有检测到它。%n%nWindows 11 和较新的 Windows 10 已经自带。%n如果装好后双击没反应，请先安装 WebView2 运行时（微软官网有"常青版"免费下载），再运行本程序。%n%n现在仍然可以继续安装。

[Messages]
; 官方简体中文翻译停在 Inno 6.1.0+ 那一版，6.3 之后新增的消息它没有。
; 编译时会有几十条 "Will use the English message from Default.isl" 警告。
;
; ⚠️ **那些警告不用管，这里解释一下为什么，免得以后有人当成疏漏去"修"。**
;
; 缺的全是同一类消息 —— 归到三件事上：
;     下载文件（Downloading*、ErrorDownloading、StatusDownloadFiles……）
;     解压归档（ExtractingLabel、ErrorExtracting、ArchiveIsCorrupted……）
;     签名与哈希校验（Verification*、ErrorFileHash*、SourceVerificationFailed）
;
; 而**这个安装程序一件都不做**：文件全在本地（[Files] 里直接指 dist 目录）、
; 没有归档、不联网、也没签名。也就是说这些消息永远不会显示出来。
; 为了永远看不到的字符串去补二十多条翻译，只是徒增需要维护的东西。
;
; 唯一真正会碰到的是 VerificationSignatureInvalid（签名无效时才弹），
; 留着英文会显得"没做完"，所以单独补上。
;
; 语法是 `语言名.消息名=` —— 只覆盖中文，不用去改下载来的
; ChineseSimplified.isl（那个文件重新下载就会覆盖，改动留不住）。
chinese.VerificationSignatureInvalid=安装包的验证签名无效，文件可能已被改动。%n%n为了安全，安装无法继续。

[Tasks]
; 默认不勾 —— 桌面图标应该是用户自己要才有的（不想要的人会觉得被塞了东西）
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 整个文件夹版一起装。
; ⚠️ recursesubdirs 是必需的 —— _internal 目录里是 Python 解释器和依赖库，
; 少了它程序根本起不来（exe 只有 5.9 MB，剩下 35 MB 全在 _internal 里）。
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; 装完可以顺手打开。skipifsilent：静默安装时不弹出来打扰批量部署
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; ============================================================================
;  ⚠️ 这里**故意没有** [UninstallDelete] 段落
;
;  用户数据在 %APPDATA%\Timetable，压根不在 {app} 里，所以卸载本来就不会碰它。
;  别为了"清干净"去加一段删 %APPDATA%\Timetable 的代码 ——
;  那会把用户录了一学期的课表一起删掉，而卸载一个软件绝不该有这种后果。
; ============================================================================

[Code]
const
  { WebView2 运行时的产品 ID。三个位置都要查，因为装法不同写的地方不同：
      机器级安装 → HKLM\SOFTWARE\WOW6432Node\...（64 位系统上的 32 位视图）
      机器级安装 → HKLM\SOFTWARE\...（原生 64 位）
      用户级安装 → HKCU\Software\...  }
  WebView2Key = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2Installed: Boolean;
var
  Ver: String;
begin
  Result :=
    RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SOFTWARE\WOW6432Node\' + WebView2Key, 'pv', Ver) or
    RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SOFTWARE\' + WebView2Key, 'pv', Ver) or
    RegQueryStringValue(HKEY_CURRENT_USER, 'Software\' + WebView2Key, 'pv', Ver);
end;

function InitializeSetup: Boolean;
begin
  { 缺 WebView2 只是**警告**，不拦着安装。
    理由：这个检测可能因为注册表位置变化而误判，而误判的后果如果是
    "装都装不了"，用户就完全没有退路了。
    警告 + 告诉用户怎么办，比直接拒绝友好得多。

    ⚠️ 这两道防护挡的是不同的东西（理由和 CurUninstallStepChanged 里一样）：
      · SuppressibleMsgBox 受 /SUPPRESSMSGBOXES 管
      · WizardSilent 管 /SILENT 与 /VERYSILENT
    只留一个的话，另一种静默组合下会弹出一个人永远点不到的框，
    安装就卡在那里。 }
  if (not WebView2Installed) and (not WizardSilent) then
    SuppressibleMsgBox(ExpandConstant('{cm:MyWebView2Missing}'), mbInformation, MB_OK, IDOK);

  Result := True;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  { 在"准备安装"那一页加一句：数据会存在哪、卸载会不会删。
    放在这里而不是弹窗，是因为它属于"安装前应该知道的事"，
    而弹窗会打断流程 —— 用户点掉之后往往什么也没记住。 }
  { ⚠️ 下面这个数组字面量**不能把 [ 写在行首**。
    Inno 是逐行找段落标签的（形如 [Section]），一行只要以 [ 开头就被当成
    段落名，于是编译报 "Invalid section tag" —— 而报错位置指着这一行的
    行号，看不出跟"段落"有什么关系，我第一次就在这里卡住了。
    Pascal 的数组语法和 Inno 的段落语法恰好撞车，只能靠排版避开。 }
  Result := MemoDirInfo + NewLine + NewLine + MemoTasksInfo + NewLine + NewLine
    + FmtMessage(CustomMessage('MyDataKeptLocation'), [ExpandConstant('{userappdata}\Timetable')]);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { 卸载完之后再说一次数据还在哪。
    卸载是"删掉程序"，用户很容易误以为课表也一起没了 ——
    结果重装之后发现设置还在，反而变成惊喜；但更常见的是他不敢卸载。
    说清楚，两边都省事。

    ⚠️ 这里用了两道防护，**它们挡的是不同的东西**，不是重复劳动：

      ① `SuppressibleMsgBox` —— 官方文档写明它"在消息框被抑制时返回 Default"。
         它存在的意义本身就是「受 /SUPPRESSMSGBOXES 影响的 MsgBox」，
         反过来说明**裸的 MsgBox 不受那个开关管**。
         所以这里不能用 MsgBox。

      ② `not UninstallSilent` —— /VERYSILENT 时没有人坐在屏幕前。
         注意这个条件 ① 盖不住：`/VERYSILENT` **不带** /SUPPRESSMSGBOXES
         时，① 那边并不认为自己被抑制，照样会弹。

    两个条件都满足才弹。漏掉任何一个，静默卸载都可能停在这里
    等一个永远没人点的"确定"。 }
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
    SuppressibleMsgBox(FmtMessage(CustomMessage('MyDataKeptLocation'), [ExpandConstant('{userappdata}\Timetable')]), mbInformation, MB_OK, IDOK);
end;
