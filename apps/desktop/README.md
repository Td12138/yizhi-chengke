# 桌面版（Electron + 内置 Python）

把「FastAPI 后端 + React 前端」封装成双击即用的 Windows 程序。
**Web 模式仍是主路径**：`apps/desktop` 不参与、不影响 `apps/web` 与 `apps/server` 的原有用法。

## 三种运行方式

| 方式 | 命令 | 适用 |
|---|---|---|
| Web（主路径） | 后端 `python -m uvicorn app.main:app --port 8710`；前端 `cd apps/web && npm run dev` → <http://127.0.0.1:5173> | 开发、录屏、改代码 |
| Electron dev | `cd apps/desktop && npm run dev`（可选 `YIZHI_PYTHON` 指定解释器） | 调试外壳 |
| 打包产物 | 双击 `一纸成课-portable.exe`，或解压 `yizhi-chengke-0.1.5-win64.zip` 后运行 `一纸成课.exe` | 交给评委 / 答辩现场 |

### Electron dev

```powershell
cd apps/desktop
npm install
# 前端产物需先构建（dev 模式加载的是 dist，不是 vite dev server）
npm run build:web
# 指定后端解释器（缺省用 PATH 里的 python）
$env:YIZHI_PYTHON = "<你的 venv>\Scripts\python.exe"   # 例：仓库默认 ML 环境
npm run dev
```

dev 模式直接跑仓库里的 `apps/server`，数据仍写仓库 `data/`，方便演示前用
`python apps/server/scripts/reset_demo.py` 重置。

## 构建

```powershell
cd apps/desktop
npm install                       # Electron 走 npmmirror；postinstall 自动补二进制（npm 11 默认不跑依赖脚本）
npm run build:icon                # 生成 build/icon.ico（Pillow）
npm run build:web                 # 构建 apps/web/dist
npm run build:runtime             # 下载 embeddable Python 3.14.2 + 装依赖 → runtime/python
npm run package                   # portable + win-unpacked 一起出
npm run package:zip               # 压 zip + 生成「一纸成课-portable.exe」中文副本
```

单独出包：

```powershell
npm run package:dir               # 只出 win-unpacked（最快，验证用）
npm run package:nsis              # 只出 NSIS 安装包（可选）
```

产物文件名用英文（`YizhiChengke-0.1.5-portable.exe`），避免中文路径在部分解压工具/CI 中出问题；`package:zip` 会额外复制一份中文名 `一纸成课-portable.exe` 方便直接发人。窗口与安装后的显示名仍是「一纸成课」。

构建直接用 `node_modules/electron/dist`（`build.electronDist`），不再联网下载 Electron；若本机杀软/网盘同步盘干扰打包，可用
`npx electron-builder --win --x64 --config.directories.output=<另一块盘的空目录>` 换输出盘，再把产物拷回 `release/`。

图标由 `build/make_icon.py` 用 Pillow 生成：深色底 `#0e1320` + 青色 `#2dd4bf` 折角纸页。

### 内置 Python 运行时

`build/prepare_runtime.ps1` 做三件事：

1. 下载 `python-3.14.2-embed-amd64.zip`（缓存在 `build/.cache/`）
2. 解压到 `runtime/python/`，改 `python314._pth`：放开 `import site` 并追加 `Lib\site-packages`
3. 用宿主 Python 执行 `pip install --target runtime/python/Lib/site-packages -r apps/server/requirements.txt`

```powershell
pwsh -File build/prepare_runtime.ps1 -Python "<宿主 python 路径>"
```

`runtime/` 是构建产物，已 gitignore；打包时由 electron-builder 原样复制到 `resources/runtime`。

## 运行机制

```
双击 exe
  └─ app.whenReady() 第一件事：createLoadingWindow()（不等后端、不探端口）
       └─ loading.html 四步清单：准备运行时 → 启动本地服务 → 加载界面 → 完成
            （每步 待办○/进行中●/完成✓/失败✕ + 耗时；顶部百分比 + 已用秒数，200ms 刷新）
       ├─ 探测 8710 → 8711 → 8712
       │    ├─ 已有同款后端（/api/health ok）→ 直接复用
       │    └─ 被其他程序占用 → 顺延下一个端口，加载页提示「8710 被占用，改用 8711」
       ├─ spawn resources/runtime/python/python.exe -m uvicorn app.main:app --port <实际端口>
       │    （cwd = resources/server，SERVE_WEB=1，WEB_DIST=resources/web）
       ├─ 300ms 轮询 /api/health，40s 超时（portable 首次解压 + 实时防护扫描会慢）
       │    ├─ 就绪 → 在隐藏窗口里 loadURL(http://127.0.0.1:<实际端口>)（后端同时托管前端静态文件）
       │    │    └─ 自检 title/可见文字 → 通过后在加载页显示「完成」400ms → 切到主界面窗口
       │    ├─ 超时 → error.html，带「再等 30 秒」按钮（继续轮询、不重启进程）
       │    └─ 退出/界面空白 → error.html（错误原因 + 「查看日志」按钮）
       └─ 关窗 / 退出 → taskkill /T /F 进程树，连同 python.exe
```

节奏控制（`main.js` 常量）：每步 active 至少 300ms（`STEP_MIN_ACTIVE_MS`）、「完成」至少 400ms
（`DONE_HOLD_MS`）、总耗时不足 1.2s 时放慢到 1.2s（`MIN_STARTUP_PACING_MS`）；视觉更新走
`queueVisual` 队列，不阻塞真实启动流程。

首次运行：`%APPDATA%\yizhi-chengke-desktop\first-run.done` 不存在时，主界面 URL 追加 `?tour=1`；
标记只在界面加载成功（自检通过）之后才写。

前端 `fetch` 全部用相对路径 `/api/...`，所以换端口不需要改前端。

数据目录：打包版写到 `%APPDATA%\yizhi-chengke-desktop\data`
（Electron 用 `APP_DATA_DIR` 传给后端；dev 模式仍是仓库 `data/`）。

日志：`%APPDATA%\yizhi-chengke-desktop\logs\desktop.log`（错误页可一键打开）。

## 摄像头权限

前端 `apps/web/src/components/CameraCapture.tsx` 用 `getUserMedia` 取摄像头；Electron 默认不弹系统授权框，不显式放行会静默失败。

- 放行代码：`main.js:setupPermissions()`
  - `session.defaultSession.setPermissionRequestHandler`：`permission === "media"` 且来源是 `http://127.0.0.1:<port>` 时 `callback(true)`，其余权限一律拒绝
  - `setPermissionCheckHandler`：同一条件下返回 `true`
  - `boot()` 在创建窗口前调用 `setupPermissions()`
- 作用域仅限本机后端 origin（`http://127.0.0.1:`）；`file://` 与外部站点都不放行。
- 界面加载地址是 `http://127.0.0.1:<实际端口>`（安全上下文），不是 `file://`；`file://` 只是启动骨架与错误页。
- `webPreferences` 为 `sandbox: true` + `contextIsolation: true`，未禁用 media 能力；摄像头画面由 Chromium 直接渲染，不需要预加载脚本。
- 无摄像头 / 用户拒绝时，前端自身有降级提示（改用「选择文件」），外壳只负责不崩溃。

本机验证（假摄像头）：`YIZHI_FAKE_MEDIA=1` 启动后，CDP 调 `getUserMedia` 返回 `OK tracks=1 label=fake_device_0`，点「打开摄像头」后 `video.readyState=4`；日志出现 `放行媒体权限：video`。`YIZHI_FAKE_MEDIA_UI=0` 可强制走 request handler 路径。

## 产物与体积（2026-11-16 实测）

| 产物 | 体积 | 说明 |
|---|---|---|
| `release/YizhiChengke-0.1.5-portable.exe` → 交付副本 `release/一纸成课-portable.exe` | **136.9 MB** | 单文件，双击自解压到 `%TEMP%` 运行 |
| `release/yizhi-chengke-0.1.5-win64.zip` | **200.5 MB** | `win-unpacked/` 整目录压缩，答辩现场推荐 |
| `release/win-unpacked/一纸成课.exe` | 解压后 471.3 MB | 免安装目录版（Electron 357.2 MB + 内置 Python 111.6 MB） |

内置 Python 运行时单独 111.6 MB（sympy 约 69 MB；已剔除全部 `tests/` 目录，省 31 MB）。
2026-11-15 的运行时重建补装了此前漏装的 `pymupdf / pypdf / python-docx`（`requirements.txt` 有、runtime 里没有，
文档上传会 ImportError），因此体积比旧版大，属修复而非膨胀。

## 冷启动实测

| 启动方式 | 窗口出现 | 到 `/api/health` 通过 | 到界面可交互 |
|---|---|---|---|
| Web 模式（仓库 venv） | — | ~1.2 s | 取决于 vite |
| `win-unpacked/一纸成课.exe` | **0.18 ~ 0.21 s** | 2.5 ~ 5.1 s | 3.9 ~ 6.4 s |
| `一纸成课-portable.exe`（解压后） | 0.15 ~ 0.25 s | 12.9 s | 14.5 s |
| `一纸成课-portable.exe`（含解压，实测） | **36 ~ 48 s** | — | 48.1 s |

窗口出现与后端无关：`app.whenReady()` 第一件事就开窗，实测 0.18~0.21 s 可见（目标 < 1.5 s）。
后端就绪时间波动大（同一台机器 2.5~5.1 s），主要花在 Defender 首次扫描新解压的 111.6 MB 运行时与
sympy/pymupdf 导入；第二次启动通常更快。

### portable 的解压成本（已知限制）

portable 是 NSIS 自解压：**每次双击都要把 471.3 MB 解到 `%TEMP%\<随机目录>`，退出时删除**。
本机实测解压阶段 36~48 s，期间没有任何窗口，用户容易以为没反应——这是 NSIS 包装器的固有限制，
主进程无法提前出窗。

试过用固定解压目录复用（`"portable": { "unpackDirName": "yizhi-chengke-${version}" }`），实测：
目录确实变成 `%TEMP%\yizhi-chengke-0.1.0` 并保留，但**第二次启动仍要 37.5 s**（时间戳显示目录被整个
重写，electron-builder 每次都会重新解压），达不到「秒开」，因此**已回退该配置**。

> **结论：主推 `yizhi-chengke-0.1.5-win64.zip`（解压一次，之后双击 0.2 s 出窗、~5 s 进界面）。**
> portable 仅用于发给评委自行体验，首次需要耐心等 40 s 左右；不存在可清理的缓存（临时目录退出即删）。

## 已验证（2026-11-15）

| 项 | 结果 |
|---|---|
| 前端 `npm run build` | 通过（889 ms） |
| 后端 `SERVE_WEB=1` 托管 `dist` | `/` 返回 200 且含「一纸成课」；`/api/*` 不受影响 |
| 内置 Python 独立跑后端 | `runtime/python/python.exe -m uvicorn app.main:app --port 8711` → health ok，冷启动 1.23 s |
| Electron dev 开窗 | 日志 `界面自检：title=一纸成课`，DOM 非空 |
| portable 双击 → 界面 | `界面自检` 通过，`/api/health` ok |
| 打包版 PDF/DOCX 上传 | 内置运行时直接跑后端：`POST /api/ingest` 分别返回 `{"kind":"document"}`；DOCX 事件 `docx 直接解析：2 个段落/表格块`，PDF 事件 `文档共 1 页`（pymupdf 解析），无 ImportError |
| 打包版端到端「上传 → 出题 → 判分 → 批改 → 追问」 | 7 个 Agent 全部有名：recognize / extract / compose / verify / explain / retrieve / review，3 张卡片、5 道题 |
| 端口占用顺延 | 8710 被占 → 自动用 8711，界面 1.68 s 就绪 |
| 单实例锁 | 第二个进程立即退出（`second_exited=True`），不会抢端口 |
| 后端起不来 | 显示 `error.html`（含原因 + 「查看日志」按钮），不白屏 |
| 摄像头（假设备，打包版） | `getUserMedia` 返回 `fake_device_0`；点「打开摄像头」后 `video.readyState=4`、`paused=false`、无错误提示；日志「放行媒体权限：video」 |
| 启动页 / 错误页配色 | 与主界面同一套浅色 token（`#f7f9fc` / `#ffffff` / `#0f766e`），无深色闪屏 |
| 关闭窗口 | `taskkill /T /F` 后端，残留 `一纸成课*` / `python*` 进程 0 个，监听端口 0 个 |
| `pytest apps/server/tests -q` | **395 passed**（25 个测试文件，约 70 s） |
| `npm --prefix apps/web test` | **18 passed**（前端纯函数单测，Node ≥ 22.6 自带测试器） |

## v0.1.5 发布验证（2026-11-16）

| 项 | 结果 |
|---|---|
| 打包版端到端冒烟（启动 `win-unpacked/一纸成课.exe`，复用 `e2e_smoke.py` 的 7 条路径断言驱动包内服务） | **7/7 通过**：上传→卡片、出题→质检回环→徽章、答错→讲解、追问→出处、批改→分步、修正→留痕刷新不丢、事件落库回放；截图归档工作区 `temp/yizhi-packaged-shots/` |
| zip 完整性 | 2230 条目；`.pyc`/`__pycache__` 0；`safe_math.py`/`embedding.py`/`retrieval.py`/LICENSE/THIRD_PARTY_NOTICES 均在包 |
| 隐私扫描 | 真实工作区/用户路径 0（上游 mpmath 文档串里的欧拉常数数字含某 5 位子串，属误报，已逐条核实；本文不复述该数字） |
| 新接口在包 | `/api/runs/{id}/events`、`/api/cards/{id}/revisions` 路由存在；`APP_VERSION = "0.1.5"` |
| SHA256 | zip `FAC8D985971D9614252859D45A4443165B5FFAC705DF84E75A07C7A5CEA41799`；portable `3E60BE65D9ED56FDFDC6A0E02953D8CAEB4242D4D91D1E6EE50F15B5006C17C7` |

## 已知限制

- **首次启动慢**：portable 要先自解压 471.3 MB 到临时目录，本机实测 36–48 s（Defender 实时扫描），之后 Electron 骨架立即出现，后端约 6 s 就绪。追求「秒开」请用 zip 目录版。
- **杀软误报**：单文件自解压 + 未签名 exe 容易被 360/火绒/Defender 拦。兜底方案是解压 `yizhi-chengke-0.1.5-win64.zip` 直接运行目录版；不要为此关杀软。未做代码签名（时间与证书限制）。
- **端口占用**：8710/8711/8712 全被占时启动失败并提示端口；需要先关掉占用程序。
- **不做自动更新、不做签名**。
- **打包版不读仓库 `.env`**：如需切真实模型或回退 v0.1.5 新行为，在启动前设置同名环境变量（`VLM_PROVIDER`、`VERIFY_STRUCTURE_CHECK=0`、`JUDGE_SYMPY_EQUIV=0`、`EVENT_PERSIST=0`、`RETRIEVAL_MODE=legacy` 等），或后续版本再补设置界面。
- **模型 key 不入库**：仓库与产物中都不含任何密钥。
- 只出 x64；Windows 10/11。内置 Python 自带 VC 运行库，无需另装 Python。
- 本机构建产物未做代码签名，SmartScreen 首次运行会提示「更多信息 → 仍要运行」。
