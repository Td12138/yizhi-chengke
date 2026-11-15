"""HTTP 入口：上传、生成、判分、追问、SSE 事件流。"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db
from .events import bus
from .kb_routes import router as kb_router
from .orchestrator import Orchestrator, new_run_id

orchestrator = Orchestrator(bus)
background_tasks: set[asyncio.Task] = set()


def spawn(coro) -> None:
    """持有引用，避免后台任务被 GC 回收。"""
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.init_db()
    yield


app = FastAPI(title="一纸成课", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(kb_router)


class GenerateRequest(BaseModel):
    card_ids: list[int] = Field(default_factory=list)
    requirement: str = "出几道基础题"
    count: int = Field(default=config.DEFAULT_QUESTION_COUNT)


class GradeRequest(BaseModel):
    question_id: int
    user_answer: str


class ReviewRequest(BaseModel):
    """AI 批改请求：题号 + 学生作答原文。"""

    question_id: int
    user_answer: str


class AskRequest(BaseModel):
    query: str


class CardPatch(BaseModel):
    """人工修正卡片：只给要改的字段，缺省字段保持原值。"""

    title: str | None = None
    summary: str | None = None
    formula: str | None = None
    body: str | None = None


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
    data = await file.read()
    if len(data) > limit:
        raise HTTPException(413, f"文件超过 {limit // (1024 * 1024)} MB 上限")

    config.ensure_dirs()
    upload_path = config.UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    upload_path.write_bytes(data)

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
    """来源详情：识别原文块 + 该来源的卡片，用于原图与结果并排核对。"""
    source = db.get_source(source_id)
    if source is None:
        raise HTTPException(404, f"来源 {source_id} 不存在")
    return {
        "id": source["id"],
        "display_name": source.get("display_name") or Path(source["image_path"]).name,
        "image_path": source["image_path"],
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
