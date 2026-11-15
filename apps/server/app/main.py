"""HTTP 入口：上传、生成、判分、追问、SSE 事件流。"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
