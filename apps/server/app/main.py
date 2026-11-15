"""HTTP 入口：上传、生成、判分、追问、SSE 事件流。"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import config, db
from .events import bus
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


class GenerateRequest(BaseModel):
    card_ids: list[int] = Field(default_factory=list)
    requirement: str = "出几道基础题"
    count: int = Field(default=config.DEFAULT_QUESTION_COUNT)


class GradeRequest(BaseModel):
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
    return {
        "ok": True,
        "vlm_provider": config.VLM_PROVIDER,
        "llm_provider": config.LLM_PROVIDER,
    }


@app.post("/api/ingest")
async def ingest(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in config.ALLOWED_SUFFIXES:
        raise HTTPException(400, f"仅支持 {sorted(config.ALLOWED_SUFFIXES)}")

    data = await file.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(400, "图片超过大小上限")

    config.ensure_dirs()
    image_path = config.UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    image_path.write_bytes(data)

    run_id = new_run_id()
    bus.create(run_id)
    spawn(orchestrator.ingest(run_id, image_path))
    return {"run_id": run_id}


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


@app.get("/api/sources/{source_id}")
async def source_detail(source_id: int) -> dict:
    """来源详情：识别原文块 + 该来源的卡片，用于原图与结果并排核对。"""
    source = db.get_source(source_id)
    if source is None:
        raise HTTPException(404, f"来源 {source_id} 不存在")
    return {
        "id": source["id"],
        "image_path": source["image_path"],
        "created_at": source["created_at"],
        "blocks": source["blocks"],
        "cards": db.get_cards_by_source(source_id),
    }


@app.get("/api/sources/{source_id}/image")
async def source_image(source_id: int) -> FileResponse:
    source = db.get_source(source_id)
    if source is None:
        raise HTTPException(404, f"来源 {source_id} 不存在")
    image_path = Path(source["image_path"])
    if not image_path.is_file():
        raise HTTPException(404, "原图文件已不存在")
    mime = config.IMAGE_MIME_BY_SUFFIX.get(image_path.suffix.lower(), "image/png")
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


@app.post("/api/ask")
async def ask(req: AskRequest) -> dict:
    run_id = new_run_id()
    bus.create(run_id)
    spawn(orchestrator.ask(run_id, req.query))
    return {"run_id": run_id}
