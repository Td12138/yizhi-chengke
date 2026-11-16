# 变更日志

格式参考 Keep a Changelog；版本与 `apps/web`、`apps/desktop` 的 `package.json` 及后端 `APP_VERSION` 一致。

## [0.1.5] - 2026-11-16

### Added
- 三级验算语义：`symbolic` / `structural` / `unchecked`，无 `verify` 的题做结构检查（题干/答案/题型），UI 按级别显示徽章。开关 `VERIFY_STRUCTURE_CHECK`（缺省 1，设 0 回退「直接放行」旧行为）。
- sympy 等价判分：反写方程、因式分解↔展开、分数↔小数互化；方程答案走代入/端点验证。开关 `JUDGE_SYMPY_EQUIV`（缺省 1，设 0 回退纯字符串规则）。
- 人工修正留痕：`card_revision` 表 + `card.revised_at`，`GET /api/cards/{id}/revisions`，编辑器内改前→改后历史，徽章刷新不丢。
- 批改方法归因 `student_method`（仅真实 provider；mock 能力边界不变）：识别学生实际解法并判断成立性。
- Agent 事件落库与回放：`GET /api/runs/{id}/events`，前端重试在 run 回收/重启后走回放。开关 `EVENT_PERSIST`（缺省 1）、`EVENT_PERSIST_RUNS`（缺省 200，0 = 不清理）。
- 可选混合检索：`RETRIEVAL_MODE=hybrid` 时 2-gram + 向量 RRF 融合（OpenAI 兼容嵌入端点 `EMBED_BASE_URL`/`EMBED_MODEL`/`EMBED_API_KEY`），向量缓存、余弦出处下限 0.35、任何故障回退 legacy。缺省 `legacy` 行为不变。
- 评测脚本 `scripts/eval_verify.py` / `scripts/eval_retrieve.py` 与首轮结果 `scripts/eval-results/`：拦截率 100% / 误驳 0%；检索 top-3 legacy 80% → hybrid 95%（BGE-M3 实测）。
- Playwright 端到端冒烟 `scripts/e2e_smoke.py`：7 条主路径，自管临时数据；端口被占即退出，不驱动外部服务。
- 启动日志打印生效配置（脱敏）。

### Fixed
- 无 `verify` 的题不再标「已验算」（表述缺陷）。
- 判分：`x=2` 与 `y=2` 不再因右端值相同判等；`x/2=2` 与 `2` 不再因右端值字面相等判对（须代入验证）。
- 判分路径幂指数上限 20，防高次幂 simplify 阻塞。
- 混合检索不再「硬凑出处」（向量路余弦下限）；向量缓存故障不再穿透追问主流程。
- 结构检查不再误伤 3 字短题干（阈值 4→2）。
- `student_method.valid` 字符串布尔归一（`"false"` 不再当 True）。
- 批改面板：方法「不成立」时不再显示「你的解法即为推荐解法」。
- `agent_event` 加 `run_id` 索引；向量缓存查询按 900 分块（SQLite 变量上限）。

### Changed
- 测试口径：后端 25 文件 395 用例、前端 18 用例。
- 技术文档 v1.2（30 页），README / 桌面 README 同步。
- 界面：纸墨讲义风 + 流水线工作台（v0.1.4 后两次 style 提交，随本版发布）。

## [0.1.4] - 2026-11-15

### Added
- 讲解参数两层归一守卫；判分上标等价（`x²`≡`x^2`）；配置健壮性（坏值回默认、.env BOM/行内注释）。
- Web 模式 CSP（`WEB_CSP` 开关）；`/api/health` ready 前端警示；前端纯函数单测；首屏 allSettled 错误横幅。
- 重试重放 run（断流不重复 POST）。

### Fixed
- PDF 导出 U+2010 连字符；希腊字母文本层判定；DOCX 上限文案动态化。

## [0.1.3] - 2026-11-15

### Fixed
- safe_math 拒绝幂塔绕过（`((2**1000)**1000)**1000`）；加密/0 字节 PDF 校验；批改流式中断半句拼接；白名单放行 `pi`；桌面 CSP font-src。
- PyMuPDF AGPL 说明（THIRD_PARTY_NOTICES.md）。

## [0.1.2] - 2026-11-15

### Added
- safe_math 白名单（字符/标识符/受限 parse_expr/数值 subs），替代裸 sympify。
- PDF/DOCX 输入双路径；mock 按卡片主题分流命题。

## [0.1.0] - 2026-11-15

### Added
- 首版：七 Agent 编排、命题↔质检回环、SSE 看板、mock 全链路、卡片编辑、追问出处、函数图像、知识谱系、Electron 桌面封装。