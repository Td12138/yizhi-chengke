"""HTTP 入口：上传、生成、判分、追问、SSE 事件流。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db
from .events import bus
from .kb_routes import router as kb_router
from .orchestrator import Orchestrator, new_run_id

# 上传落盘分块大小：1 MB 一块，超限时立刻停手，不把整包读进内存
UPLOAD_CHUNK_BYTES = 1024 * 1024
# 各字段上限：只拦明显异常的输入，正常文案远小于这些数
ASK_QUERY_MAX_CHARS = 2000
ANSWER_MAX_CHARS = 5000
REQUIREMENT_MAX_CHARS = 500
CARD_TITLE_MAX_CHARS = 120
CARD_SUMMARY_MAX_CHARS = 500
CARD_FORMULA_MAX_CHARS = 2000
CARD_BODY_MAX_CHARS = 20000
# 服务版本：与 apps/web、apps/desktop 的 package.json 保持一致
APP_VERSION = "0.1.5"
# 托管前端时的 CSP：与 apps/desktop/main.js 的 APP_CSP 同源
# KaTeX 字体走 data:，拍照预览走 blob:，PDF 预览走 frame-src 'self'
CSP_POLICY = "; ".join(
    [
        "default-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "media-src 'self' blob:",
        "connect-src 'self'",
        "frame-src 'self'",
    ]
)

logger = logging.getLogger(__name__)
orchestrator = Orchestrator(bus)
background_tasks: set[asyncio.Task] = set()


def _log_background_result(task: asyncio.Task) -> None:
    """后台任务收尾：日志留一条栈，客户端仍只看 SSE 里的 error 事件。"""
    background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.exception("后台任务失败：%s", task.get_name(), exc_info=exc)


def spawn(coro) -> None:
    """持有引用，避免后台任务被 GC 回收。"""
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(_log_background_result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.init_db()
    # 启动日志打印生效配置（脱敏：只打开关与模式，不打密钥/地址），排障一眼看出走哪条路径
    logger.info(
        "生效配置 v%s：VLM=%s LLM=%s 结构检查=%s 等价判分=%s 事件落库=%s 检索=%s 拍题讲题=%s",
        APP_VERSION,
        config.VLM_PROVIDER,
        config.LLM_PROVIDER,
        config.VERIFY_STRUCTURE_CHECK,
        config.JUDGE_SYMPY_EQUIV,
        config.EVENT_PERSIST,
        config.RETRIEVAL_MODE,
        config.SOLVE_ENTRY,
    )
    yield


app = FastAPI(title="一纸成课", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(kb_router)


def csp_applies(path: str, *, serve_web: bool, enabled: bool) -> bool:
    """CSP 只补在托管的前端文档上：API / SSE 响应不需要，开发模式也不经过这里。"""
    return serve_web and enabled and not path.startswith("/api/")


@app.middleware("http")
async def add_web_csp(request: Request, call_next):
    """Web 模式（SERVE_WEB=1）补与桌面壳等价的 CSP，防止浏览器控制台报缺失。"""
    response = await call_next(request)
    if csp_applies(
        request.url.path, serve_web=config.SERVE_WEB, enabled=config.WEB_CSP
    ):
        response.headers.setdefault("Content-Security-Policy", CSP_POLICY)
    return response


class GenerateRequest(BaseModel):
    card_ids: list[int] = Field(
        default_factory=list, max_length=config.MAX_GENERATE_CARD_IDS
    )
    requirement: str = Field(
        default="出几道基础题", max_length=REQUIREMENT_MAX_CHARS
    )
    count: int = Field(default=config.DEFAULT_QUESTION_COUNT)


class GradeRequest(BaseModel):
    question_id: int
    user_answer: str = Field(max_length=ANSWER_MAX_CHARS)


class ReviewRequest(BaseModel):
    """AI 批改请求：题号 + 学生作答原文。"""

    question_id: int
    user_answer: str = Field(max_length=ANSWER_MAX_CHARS)


class AskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=ASK_QUERY_MAX_CHARS)


class SolveRequest(BaseModel):
    """拍题讲题：只按卡片 id 请求，不接收任意题干文本。"""

    card_id: int


class CardPatch(BaseModel):
    """人工修正卡片：只给要改的字段，缺省字段保持原值。"""

    title: str | None = Field(
        default=None, min_length=1, max_length=CARD_TITLE_MAX_CHARS
    )
    summary: str | None = Field(default=None, max_length=CARD_SUMMARY_MAX_CHARS)
    formula: str | None = Field(default=None, max_length=CARD_FORMULA_MAX_CHARS)
    body: str | None = Field(default=None, max_length=CARD_BODY_MAX_CHARS)


@app.get("/api/health")
async def health() -> dict:
    status = config.provider_status()
    return {
        "ok": True,
        "vlm_provider": config.VLM_PROVIDER,
        "llm_provider": config.LLM_PROVIDER,
        "vlm_ready": status["vlm_ready"],
        "llm_ready": status["llm_ready"],
        "ready": status["vlm_ready"] and status["llm_ready"],
        # 新增字段（v0.1.7，只增不改）：前端据此决定是否显示「拍题讲题」入口
        "solve_entry": config.SOLVE_ENTRY,
    }


@app.post("/api/ingest")
async def ingest(file: UploadFile = File(...)) -> dict:
    """上传入口：图片与文档共用，返回 kind 供前端区分进度文案。"""
    suffix = Path(file.filename or "").suffix.lower()
    kind = config.KIND_BY_SUFFIX.get(suffix)
    if kind is None:
        raise HTTPException(
            400,
            "仅支持图片 "
            f"{sorted(config.ALLOWED_SUFFIXES)} 或文档 {sorted(config.DOC_SUFFIXES)}",
        )

    # 文档比图片大得多（PDF 常常十几 MB），按类型给不同上限
    limit = config.MAX_DOC_BYTES if kind == "document" else config.MAX_UPLOAD_BYTES
    config.ensure_dirs()
    upload_path = config.UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    total = 0
    try:
        with upload_path.open("wb") as handle:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise HTTPException(413, f"文件超过 {limit // (1024 * 1024)} MB 上限")
                handle.write(chunk)
    except BaseException:
        # 超限 / 连接中断：删掉半成品，别让 uploads 目录留下残缺文件
        upload_path.unlink(missing_ok=True)
        raise

    run_id = new_run_id()
    bus.create(run_id)
    # 原始文件名要传下去：mock 靠它选样例集，落盘的 uuid 名字认不出主题
    spawn(orchestrator.ingest(run_id, upload_path, file.filename))
    return {"run_id": run_id, "kind": kind}


@app.get("/api/events/{run_id}")
async def events(run_id: str) -> StreamingResponse:
    return StreamingResponse(
        bus.stream(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str) -> dict:
    """已落库事件的回放（v0.1.5）：run 被回收或重启后，前端重试仍可取回历史。"""
    persisted = db.list_events(run_id)
    if not persisted:
        raise HTTPException(404, f"运行 {run_id} 没有已落库的事件")
    return {"run_id": run_id, "events": persisted}


@app.get("/api/cards")
async def cards() -> dict:
    return {"cards": db.list_cards()}


@app.patch("/api/cards/{card_id}")
async def patch_card(card_id: int, req: CardPatch) -> dict:
    """人工修正识别/萃取错误，返回更新后的完整卡片。"""
    card = db.update_card(card_id, req.model_dump(exclude_none=True))
    if card is None:
        raise HTTPException(404, f"卡片 {card_id} 不存在")
    return card


@app.get("/api/cards/{card_id}/revisions")
async def card_revisions(card_id: int) -> dict:
    """人工修正历史（v0.1.5）：改前/改后值与时间，新→旧。"""
    if db.get_card(card_id) is None:
        raise HTTPException(404, f"卡片 {card_id} 不存在")
    return {"revisions": db.list_card_revisions(card_id)}


@app.get("/api/sources")
async def sources() -> dict:
    """原图归档列表：按上传时间倒序，image_url 可直接取原图。"""
    return {
        "sources": [
            {**source, "image_url": f"/api/sources/{source['id']}/image"}
            for source in db.list_sources()
        ]
    }


@app.get("/api/sources/{source_id}")
async def source_detail(source_id: int) -> dict:
    """来源详情：识别原文块 + 该来源的卡片，用于原图与结果并排核对。

    不返回 image_path：服务器绝对路径对前端没用，且会泄露本机目录结构。
    """
    source = db.get_source(source_id)
    if source is None:
        raise HTTPException(404, f"来源 {source_id} 不存在")
    return {
        "id": source["id"],
        "display_name": source.get("display_name") or Path(source["image_path"]).name,
        "created_at": source["created_at"],
        "blocks": source["blocks"],
        "cards": db.get_cards_by_source(source_id),
    }


@app.get("/api/sources/{source_id}/image")
async def source_image(source_id: int) -> FileResponse:
    """归档文件下载：图片按图片 MIME，PDF / DOCX / 文本按各自 MIME。"""
    source = db.get_source(source_id)
    if source is None:
        raise HTTPException(404, f"来源 {source_id} 不存在")
    image_path = Path(source["image_path"])
    if not image_path.is_file():
        raise HTTPException(404, "原文件已不存在")
    mime = config.SOURCE_MIME_BY_SUFFIX.get(
        image_path.suffix.lower(), config.FALLBACK_MIME
    )
    return FileResponse(image_path, media_type=mime)


@app.get("/api/questions")
async def questions() -> dict:
    return {"questions": db.list_questions()}


@app.post("/api/generate")
async def generate(req: GenerateRequest) -> dict:
    count = max(config.QUESTION_MIN, min(config.QUESTION_MAX, req.count))
    run_id = new_run_id()
    bus.create(run_id)
    spawn(orchestrator.generate(run_id, req.card_ids, req.requirement, count))
    return {"run_id": run_id}


@app.post("/api/grade")
async def grade(req: GradeRequest) -> dict:
    run_id = new_run_id()
    bus.create(run_id)
    spawn(orchestrator.grade(run_id, req.question_id, req.user_answer))
    return {"run_id": run_id}


@app.post("/api/review")
async def review(req: ReviewRequest) -> dict:
    """AI 批改：立刻返回 run_id，评分与流式评语走 /api/events/{run_id}。"""
    if db.get_question(req.question_id) is None:
        raise HTTPException(404, f"题目 {req.question_id} 不存在")
    run_id = new_run_id()
    bus.create(run_id)
    spawn(orchestrator.review(run_id, req.question_id, req.user_answer))
    return {"run_id": run_id}


@app.post("/api/ask")
async def ask(req: AskRequest) -> dict:
    run_id = new_run_id()
    bus.create(run_id)
    spawn(orchestrator.ask(run_id, req.query))
    return {"run_id": run_id}


@app.post("/api/solve")
async def solve(req: SolveRequest) -> dict:
    """拍题讲题（v0.1.7，独立入口）：按卡片直接返回讲解脚本与绘图参数。

    与判分 / 批改互不影响：不写 attempt、不动题目表，只读卡片。
    """
    if not config.SOLVE_ENTRY:
        raise HTTPException(404, "拍题讲题入口未启用（SOLVE_ENTRY=0）")
    card = db.get_card(req.card_id)
    if card is None:
        raise HTTPException(404, f"卡片 {req.card_id} 不存在")
    result = await orchestrator.solve(card)
    return {
        "title": result.get("title") or "",
        "script": result.get("script") or "",
        "params": result.get("params"),
    }


def _mount_web() -> None:
    """可选静态托管：SERVE_WEB=1 时把前端 dist 挂到 `/`。

    必须在所有 /api/* 路由注册之后调用：Starlette 按注册顺序匹配，
    后挂的 `/` 不会遮住先前注册的接口。缺省（SERVE_WEB=false）不挂载，
    Web 开发模式（vite 5173 + 后端 8710）行为与之前完全一致。
    """
    if not config.SERVE_WEB:
        return
    dist = config.WEB_DIST
    if not (dist / "index.html").is_file():
        print(f"[serve-web] 警告：未找到前端产物 {dist}，本次仅提供 API。")
        return
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")
    print(f"[serve-web] 已挂载前端 {dist} → http://{config.HOST}:{config.PORT}/")


_mount_web()
