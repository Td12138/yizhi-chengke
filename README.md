# 一纸成课（Paper2Lesson）

> 拍下任何一页纸 —— 板书、试卷、讲义、错题本 —— AI 把它变成可追问、可练习、可讲解的交互式课程。

参赛：全国大学生计算机应用能力与数字素养大赛暨第九届传智杯全国大学生数智创新与 AI 应用大赛 · **AI 创新应用挑战赛**（个人赛 / 技术应用赛）

![主界面](docs/images/main.png)

## 它解决什么

手机相册里躺着几千张板书和试卷，复习时一张都找不到。现有工具（扫描类 App、公式识别服务）止步于「图 → 文字」：转完就结束，不能追问、不能出题、不讲解。

一纸成课补的是识别**之后**那一段：把纸面知识变成结构化卡片，再按需变成题，答错还能讲。

```
拍照 ──▶ 识别 ──▶ 萃取卡片 ──▶ 入库 ──┐
                                      ├──▶ 一句话出题 ──▶ 质检 ──▶ 交互练习卷
                                      └──▶ 追问（带出处）      │
                                                              └──▶ 答错 ──▶ 可视化讲解
```

## 环境要求

| 依赖 | 版本 |
|---|---|
| Python | ≥ 3.10（代码使用了 `str \| None` 等新语法） |
| Node.js | ≥ 18（Vite 5 要求） |

## 快速开始

无需任何 API Key —— 缺省使用 `mock` 提供方，内置一套二次函数教学样例，整条链路可完整演示。

### 1. 后端

```bash
# 在仓库根目录执行
cp .env.example .env          # Windows: copy .env.example .env

cd apps/server
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8710
```

> `.env` 放在**仓库根**（`config.py` 只从根目录加载），不是 `apps/server/` 下。

### 2. 前端

另开一个终端：

```bash
cd apps/web
npm install
npm run dev
```

打开 <http://127.0.0.1:5173>。

### 3. 验证

`http://127.0.0.1:8710/api/health` 应返回：

```json
{"ok": true, "vlm_provider": "mock", "llm_provider": "mock"}
```

界面上依次点：上传任意图片（或用「打开摄像头」拍一页）→ 生成练习卷 → 答错一题 → 看讲解图。质检 Agent 会先驳回一道答案不自洽的题，再让命题 Agent 重生成 —— 这是多 Agent 存在的证据，不是故障。

### 4. 演示前重置

```bash
# 在仓库根目录执行
python apps/server/scripts/reset_demo.py
```

## 配置

全部字段缺省即为可离线运行的最稳路径。切换到真实模型只改两处：`*_PROVIDER` 与 `*_API_KEY`。

| 变量 | 缺省 | 说明 |
|---|---|---|
| `VLM_PROVIDER` | `mock` | `mock` / `qwen` / `glm`，视觉识别提供方 |
| `VLM_BASE_URL` / `VLM_API_KEY` / `VLM_MODEL` | 空 | OpenAI 兼容端点与模型名，留空用内置预设 |
| `LLM_PROVIDER` | `mock` | `mock` / `deepseek` / `qwen` / `glm` |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 空 | 同上 |
| `VERIFY_MAX_RETRY` | `2` | 命题↔质检**重试**轮数（首轮 + 最多 2 次重试） |
| `APP_PORT` | `8710` | 后端端口，改端口需同步改 `apps/web/vite.config.ts` 的 `BACKEND` |

缺字段按默认值补，未知字段忽略，旧配置原样能跑。

## 功能

| 功能 | 说明 |
|---|---|
| 拍照 / 上传 | 摄像头直接拍（`getUserMedia`）或选图；图片最长边压到 1920 再上传 |
| **PDF / DOCX** | 支持文档输入：PDF 文本层可用则直读，乱码（扫描件/子集字体）自动渲染成图走视觉模型；DOCX 按原文顺序解析段落与表格 |
| 知识点卡片 | 识别结果结构化成卡片，可**人工修正**（标题/摘要/公式/正文四个字段） |
| 一句话出题 | 按自然语言需求生成变式题，KaTeX 渲染 |
| 质检回环 | sympy 代入验算，不通过打回重生成；验算过程可在看板看到 |
| 错题讲解 | 按错因生成讲解脚本 + 可拖动参数的可视化图 |
| 追问 | 召回卡片原文作答，标注出处 |
| 函数图像 | 自写表达式解析器（无 eval、无绘图库），随手画任意函数 |
| 知识谱系 | 知识点按标签成图，节点大小=出题量，颜色深浅=被问次数 |

## 架构

六个 Agent，能力异构，靠事件总线串成一条流水线：

| Agent | 职责 | 依赖 |
|---|---|---|
| 识别 | 图像 → 版面块（公式 / 表格 / 手写） | 视觉模型 |
| 萃取 | 版面块 → 知识点卡片 | 语言模型 |
| 命题 | 一句话需求 → 变式题 | 语言模型 |
| 质检 | sympy 代入验算，驳回不通过的题 | 符号计算 |
| 讲解 | 错因归因 + 选可视化模板与参数 | 语言模型 |
| 检索 | 追问时召回卡片原文并标出处 | 本地检索 |

```mermaid
sequenceDiagram
    participant U as 用户
    participant O as 编排器
    participant A1 as 识别
    participant A2 as 萃取
    participant A3 as 命题
    participant A4 as 质检
    participant A5 as 讲解
    U->>O: 上传图片
    O->>A1: 版面识别
    A1-->>O: blocks
    O->>A2: 萃取知识点
    A2-->>O: cards（入库）
    U->>O: 一句话需求
    O->>A3: 命题
    A3-->>O: questions
    O->>A4: 验算
    A4-->>O: 驳回 → 回到 A3（首轮 + 最多 2 次重试）
    U->>O: 提交答案
    O->>A5: 生成讲解
```

每个 Agent 的状态经 SSE 推到前端看板，用户能看见谁在跑、谁把谁驳回了。

## 目录

| 路径 | 内容 |
|---|---|
| `apps/server/app/` | FastAPI 服务、编排器、六个 Agent、Provider 层 |
| `apps/server/tests/` | 68 项 pytest：判分、验算、检索、编排回环、知识库统计、接口 |
| `apps/server/scripts/` | 演示前重置脚本 |
| `apps/web/src/` | React 前端：看板、练习卷、摄像头、函数图像、知识谱系 |
| `docs/技术文档.md` / `.pdf` | 技术文档正文与提交版 PDF |
| `docs/ai-logs/` | 与 AI 的核心对话快照（提交物） |
| `data/samples/` | 演示用板书样例图（入库）；`data/*.db`、`uploads/` 为运行数据，不入库 |

## 和同类项目的差异

GitHub 上「错题本 / AI 出题」类项目不少，但**对生成内容的正确性验证**几乎是空白。2026-11-15 用 GitHub 检索的结果：

| 检索词 | 仓库数 | 最高星 |
|---|---|---|
| `错题本 ai` | 237 | 853 |
| `handwritten math ocr` | 103 | 40 |
| `photo to quiz ai` | 16 | 3 |
| `quiz generation answer verification` | 6 | 0 |
| `sympy verification questions` | 4 | 0 |

| 维度 | 多数同类 | 本项目 |
|---|---|---|
| 生成题目是否正确 | 不验证，模型说了算 | **sympy 代入验算，不通过打回重生成** |
| 过程是否可见 | 只给结果 | 六个 Agent 实时看板，谁驳回了谁一眼可见 |
| 没有 API Key 能否跑 | 需要 OpenAI / Gemini Key | **mock 模式零依赖跑通全链路** |
| 交付形态 | Web 服务 / Docker | Web + 132 MB 双击即用桌面版 |
| 知识沉淀 | 错题列表 | 知识点卡片 + 被问次数 + 知识谱系 |

## 边界与诚实声明

- `mock` 模式**不读取图片内容**：任何图片都会返回内置的二次函数样例，用于无密钥演示。切到真实模型（`VLM_PROVIDER=qwen|glm`）才真正识别。
- 交付的题目全部经过验算：验算不通过的题被丢弃并由命题 Agent 重生成；若多轮仍不通过，整批拒绝交付而不是带病上线。
- 人工修正只允许改四个字段（标题/摘要/公式/正文），服务端做字段白名单；卡片不存在返回 404。
- 摄像头依赖 `getUserMedia`，仅在**安全上下文**可用（`127.0.0.1` 或 HTTPS）。用局域网 IP 访问时浏览器会拒绝，此时改用「选择图片」。
- 识别不确定的内容标 `[无法识别]`，不猜。

## 提交物位置

| 提交物 | 位置 |
|---|---|
| 代码仓库 | 本仓库（Public，MIT） |
| README 复现指南 | 本文件 |
| AI 对话快照 | `docs/ai-logs/`，索引见 `docs/ai-logs/README.md` |
| 技术文档 | `docs/技术文档.pdf`（正文 `docs/技术文档.md`） |
| 桌面版（可选） | [Releases](https://github.com/Td12138/yizhi-chengke/releases)：`yizhi-chengke-0.1.0-win64.zip` 解压即用，`YizhiChengke-0.1.0-portable.exe` 单文件 |
| 演示视频 | 随作品在赛事平台提交，不入库 |

## 红线

- 仓库不含任何内网地址、域名、账号口令、token 与个人身份信息。
- 每个 commit 对应一个功能模块或一次与 AI 的核心会话，不补历史、不改时间戳。
